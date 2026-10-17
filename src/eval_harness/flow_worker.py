"""One bounded P1 trial in a fresh process; never approves or publishes assets."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib
import inspect
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from .openai_judge import encode, scan_payload
from .v2_recorded import digest, read, write

# Same as the OpenAI SDK default that P1 uses when run on its own. A 120 s cap
# cut off a full-size A2 design response (FLOW-20261015-104803-e84cbf).
GENERATION_TIMEOUT_SECONDS = 600


class MeteredResponses:
    def __init__(self, client, role, work, ledger, limit, seen, max_output_tokens=24000, purpose="generation"):
        self.client, self.role, self.work = client, role, work
        self.ledger, self.limit, self.seen = ledger, limit, seen
        self.max_output_tokens = max_output_tokens
        self.purpose = purpose

    def persist(self):
        write(self.work / "call_ledger.json", self.ledger)

    def parse(self, **kwargs):
        kwargs = dict(kwargs, max_output_tokens=self.max_output_tokens)
        if self.ledger.get("fatal"):
            raise RuntimeError("Further generation calls blocked")
        if len(self.ledger["attempts"]) >= self.limit:
            self.ledger["fatal"] = "GENERATION_BUDGET_EXHAUSTED"
            self.persist()
            raise RuntimeError("Generation budget exhausted before transmission")
        schema = kwargs["text_format"].model_json_schema()
        payload = {key: value for key, value in kwargs.items() if key != "text_format"}
        scan_payload(payload)
        if payload.get("store") is not False or payload.get("tools"):
            raise ValueError("Unsupported generation transmission")
        if len(encode(payload).encode("utf-8")) > 250_000:
            raise ValueError("Generation payload too large")
        seq = len(self.ledger["attempts"]) + 1
        folder = self.work / "calls" / f"{seq:03d}"
        write(folder / "request.json", {"role": self.role, "purpose": self.purpose, "attempt": seq, "payload": payload})
        write(folder / "schema.json", schema)
        item = {"attempt": seq, "role": self.role, "purpose": self.purpose, "status": "STARTED",
                "request_sha256": digest(folder / "request.json"),
                "sdk_retries": 0, "server_completion": "UNKNOWN",
                "started_at": datetime.now(timezone.utc).isoformat()}
        self.ledger["attempts"].append(item)
        self.persist()
        try:
            response = self.client.responses.parse(**kwargs)
            # Billed even when the response is later rejected (incomplete, no
            # identity, no structured output), so keep usage before any check.
            usage = getattr(response, "usage", None)
            item["usage"] = usage.model_dump() if hasattr(usage, "model_dump") else None
            item["server_completion"] = getattr(response, "status", "UNKNOWN")
            if item["server_completion"] != "completed":
                raise ValueError("Generation response did not complete")
            rid = getattr(response, "id", None)
            item["response_id"] = rid
            item["request_id"] = getattr(response, "_request_id", None)
            if not rid or rid in self.seen:
                self.ledger["fatal"] = "MISSING_OR_DUPLICATE_RESPONSE_ID"
                raise ValueError("Response identity could not be verified")
            self.seen.add(rid)
            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                raise ValueError("Structured output missing")
            write(folder / "response.json", parsed.model_dump(mode="json", by_alias=True))
            item.update(status="RECEIVED", server_completion="completed",
                        response_sha256=digest(folder / "response.json"))
            return response
        except Exception as exc:
            item.update(status="ERROR", error_type=type(exc).__name__)
            self.ledger["fatal"] = self.ledger.get("fatal") or "GENERATION_RESPONSE_ERROR"
            # Do not propagate SDK bodies, credentials, or server error messages into P1.
            raise RuntimeError("Generation response failed; inspect call ledger") from None
        finally:
            item["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.persist()


def generation_client(key, work, ledger, *, transport=None):
    import httpx
    from openai import OpenAI
    def on_request(request):
        if str(request.url) != "https://api.openai.com/v1/responses":
            raise ValueError("Unexpected generation destination")
        if not ledger["attempts"]:
            raise ValueError("HTTP transmission without attempt reservation")
        ledger.setdefault("http_requests", []).append({
            "attempt": ledger["attempts"][-1]["attempt"],
            "role": ledger["attempts"][-1]["role"], "status": "SEND_ATTEMPT",
            "time": datetime.now(timezone.utc).isoformat()})
        write(work / "call_ledger.json", ledger)
    return OpenAI(api_key=key, base_url="https://api.openai.com/v1", max_retries=0,
                  timeout=GENERATION_TIMEOUT_SECONDS,
                  http_client=httpx.Client(transport=transport, trust_env=False,
                                           follow_redirects=False, event_hooks={"request": [on_request]}))


def bind_model_clients(execution, client, work, ledger, config):
    """Inject all supported P1 clients before the first metered request."""
    names = ("OpenAIAgent1", "OpenAIAgent2", "OpenAIAgent3", "OpenAIGroundingReviewer")
    originals = {name: getattr(execution, name, None) for name in names}
    if any(cls is None or not {"model", "client"} <= set(inspect.signature(cls).parameters)
           for cls in originals.values()):
        raise ValueError("Unsupported P1 model client contract; no requests sent")
    seen = set(config["seen_response_ids"])

    def metered(role, purpose):
        return SimpleNamespace(responses=MeteredResponses(
            client, role, work, ledger, config["generation_limit"], seen,
            config["generation_max_output_tokens"], purpose=purpose))

    for role in ("A1", "A2", "A3"):
        original = originals["OpenAIAgent" + role[-1]]
        def factory(*, model=None, _class=original, _client=metered(role, "generation")):
            return _class(model=model, client=_client)
        setattr(execution, "OpenAIAgent" + role[-1], factory)

    class Reviewer:
        def __init__(self, *, model=None):
            self.model = model

        def review(self, payload):
            stages = {"AGENT1": "A1", "AGENT2": "A2", "AGENT3": "A3"}
            role = stages.get(payload.get("stage"))
            if role is None:
                raise ValueError("Unsupported grounding stage; no requests sent")
            return originals["OpenAIGroundingReviewer"](
                model=self.model, client=metered(role, "grounding")).review(payload)

    execution.OpenAIGroundingReviewer = Reviewer


def execute_trial(work: Path, config: dict, *, client=None):
    copy = work / "p1"
    if not copy.is_dir() or not (copy / "src/qa_pipeline_orchestrator.py").is_file():
        raise ValueError("Isolated target copy is missing")
    ledger = {"attempts": [], "fatal": None, "automatic_retries": 0}
    write(work / "call_ledger.json", ledger)
    # Product child processes must not inherit API or external reporting credentials.
    key = os.environ.pop("OPENAI_API_KEY", None)
    for name in list(os.environ):
        if name.startswith(("NOTION_", "SLACK_", "OPENAI_")):
            os.environ.pop(name, None)
    sys.path.insert(0, str(copy / "src"))
    os.chdir(copy)
    # Reuse P1's actual input reader, not another copy of its validation rules.
    # An incomplete runtime must fail before A1 spends any generation budget.
    try:
        execution = importlib.import_module("qa_pipeline_execution")
        pipeline = importlib.import_module("qa_pipeline_orchestrator")
        execution.load_approved_regression_catalog(copy / "approved_assets")
    except Exception as exc:
        ledger["fatal"] = "RUNTIME_INPUT_PREFLIGHT_FAILED"
        outcome = {"status": "ERROR", "reason": ledger["fatal"],
                   "error_type": type(exc).__name__, "stages": {}, "generation_by_role": {}}
        # Keep exception bodies/absolute paths out of shared result records.
        write(work / "runtime_input_preflight.json", {
            "status": "ERROR", "error_type": type(exc).__name__})
        write(work / "worker_result.json", outcome)
        write(work / "call_ledger.json", ledger)
        return outcome
    write(work / "runtime_input_preflight.json", {"status": "READY"})
    if client is None:
        if not key:
            raise ValueError("API key unavailable")
        client = generation_client(key, work, ledger)
    bind_model_clients(execution, client, work, ledger, config)
    stages = {}
    candidate_entries = []
    def wrap_stage(name, original):
        def invoke(args):
            if name == "agent3":
                candidate_entries.append({"tc_id": args.tc_id, "status": "STARTED"})
                if len(candidate_entries) > config["max_candidates"] or ledger["fatal"]:
                    candidate_entries[-1]["status"] = "BUDGET_BLOCKED"
                    ledger["fatal"] = ledger["fatal"] or "CANDIDATE_LIMIT"
                    write(work / "call_ledger.json", ledger)
                    stages[name] = 1
                    raise RuntimeError("Candidate execution budget blocked")
            code = original(args)
            stages[name] = max(stages.get(name, 0), code)
            if name == "agent3":
                candidate_entries[-1].update(status="FINISHED", exit_code=code)
            write(work / "stage_progress.json", dict(stages=stages, candidates=candidate_entries))
            return code
        return invoke
    for name in ("agent1", "agent2", "agent3"):
        setattr(pipeline, "run_" + name, wrap_stage(name, getattr(pipeline, "run_" + name)))
    run_id = config["run_id"]
    runs = copy / "runs"
    base = dict(run_id=run_id, runs_root=str(runs), model=config["generation_model"],
                approved_assets_root=str(copy / "approved_assets"),
                target_html=str(copy / "product_baseline/virtual-controller.html"),
                timeout=config["product_timeout_seconds"])
    outcome = {"status": "STARTED", "stages": stages, "candidates": candidate_entries}
    try:
        code = pipeline.run_pipeline(argparse.Namespace(
            **base, request=str(copy / config["request"]), srs=str(copy / "docs/01_PRODUCT_SRS.md"),
            tc_id="AUTO"))
        if code != 0 or ledger["fatal"]:
            outcome.update(status="BLOCKED" if code == 2 and not ledger["fatal"] else "ERROR",
                           reason=ledger["fatal"] or "PIPELINE_STOP")
        else:
            code = execution.run_validation_execution(argparse.Namespace(**base, baseline_tests=None))
            stages["execute"] = code
            if (runs / run_id / "validation_execution.json").is_file():
                a4code = pipeline.run_agent4(argparse.Namespace(
                    run_id=run_id, runs_root=str(runs), send=False))
                stages["agent4"] = a4code
                outcome.update(status="COMPLETED" if a4code == 0 else "BLOCKED" if a4code == 2 else "ERROR",
                               reason=None if a4code == 0 else "AGENT4_STOP")
            else:
                outcome.update(status="ERROR", reason="VALIDATION_OUTPUT_MISSING")
    except Exception as exc:
        outcome.update(status="ERROR", reason=type(exc).__name__)
    finally:
        outcome["generation_by_role"] = dict(Counter(x["role"] for x in ledger["attempts"]))
        write(work / "worker_result.json", outcome)
        write(work / "call_ledger.json", ledger)
    return outcome


def main():
    path = Path(sys.argv[1]).resolve()
    execute_trial(path.parent, read(path))


if __name__ == "__main__":
    main()
