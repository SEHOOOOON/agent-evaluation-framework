"""Repeat only Agent 2 on a stored full-flow trial's Agent 1 result.

Stability of the test design is measured where it varies most (A2), without
paying for A1, A3, A4 or the judge again. The stored FLOW folder is read only:
its archive hash is checked, only the Agent 1 files of the stored Run are
copied into a fresh copy of the *current* pinned P1, and P1's own loader
re-verifies them before Agent 2 runs. Each repetition is graded by code
against a range answer key (no judge call). The default is a preview; execution
needs the approval hash printed by that preview and writes only to a new folder.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import shutil
import tempfile

from .full_flow import (child, p2_pin, remove_workspace, runtime_unchanged, run_worker,
                        selected_files, target_pin)
from .openai_judge import encode
from .range_oracle import RangeKey, grade as grade_range
from .rejudge import _extract, _trial
from .v2_recorded import digest, read, write

VERSION = "a2-repeat-1.0"
# Agent 2 writes once, may rewrite once, and each is reviewed once.
CALLS_PER_REPETITION = 4
MAX_TIMES = 5
# Files Agent 1 leaves behind. Everything Agent 2 or later produced is dropped.
A1_FILE = re.compile(r"^(agent1_.*\.json|checkpoint1(_attempt_\d+)?\.json|request\.json|"
                     r"srs_snapshot\.md|run_manifest\.json)$")


def _a1_files(run: Path):
    names = sorted(p.name for p in run.iterdir() if p.is_file() and A1_FILE.match(p.name))
    if "run_manifest.json" not in names or "agent1_change_analysis.json" not in names:
        raise ValueError("Stored Run has no verified Agent 1 result")
    return {name: digest(run / name) for name in names}


def make_preview(flow: Path, trial: int, target: Path, times: int, *, range_key: Path | None = None,
                 model: str | None = None):
    if not 1 <= times <= MAX_TIMES:
        raise ValueError(f"Repetitions must be between 1 and {MAX_TIMES}")
    record, stored_plan, archive = _trial(flow, trial)
    target = target.resolve()
    pin = target_pin(target)
    request = stored_plan["spec"]["request"]
    runtime = selected_files(target, request, pin)
    if not runtime_unchanged(target, runtime):
        raise ValueError("Approved asset inventory differs from the pinned files")
    with tempfile.TemporaryDirectory(prefix="qaf-a2rep-preview-") as temp:
        _extract(archive, Path(temp))
        run = Path(temp) / "p1" / "runs" / record["run_id"]
        a1 = _a1_files(run)
        stored_request = read(run / "request.json")
    if read(child(target, request)) != stored_request:
        raise ValueError("Stored Agent 1 request differs from the current pinned request")
    key = None
    if range_key is not None:
        key = RangeKey.model_validate_json(Path(range_key).read_text(encoding="utf-8")).model_dump()
        if key["request_id"] != stored_request.get("request_id"):
            raise ValueError("Range key was written for a different request")
    plan = dict(version=VERSION, source_flow=flow.resolve().name, trial=trial, source_run_id=record["run_id"],
                evidence_archive_sha256=record["evidence_archive_sha256"], agent1_files=a1,
                target_commit=pin["commit"], runtime_hashes=runtime, evaluator_hashes=p2_pin(),
                generation_model=model or stored_plan["spec"]["generation_model"], times=times,
                calls_per_repetition=CALLS_PER_REPETITION,
                maximum_api_attempts=times * CALLS_PER_REPETITION, judge_calls=0,
                **({"range_key": key} if key is not None else {}),
                note="A1 결과는 저장 회차 그대로, A2만 현재 P1 코드로 반복합니다. 채점은 정답지 코드 대조만 하며 AI 채점은 호출하지 않습니다.")
    return {"plan": plan, "approval_sha256": sha256(encode(plan).encode()).hexdigest()}


def _summary(plan, rows):
    graded = [r["range_oracle"] for r in rows if r.get("range_oracle", {}).get("status") == "EVALUATED"]
    required = {}
    for grade in graded:
        for item in grade["items"]:
            if item.get("level") == "REQUIRED":
                required.setdefault(item["id"], 0)
                required[item["id"]] += item.get("status") == "MET"
    return dict(repetitions=len(rows), designed=sum(r.get("design_sha256") is not None for r in rows),
                graded=len(graded), all_required_met=sum(g["verdict"] == "PASS" for g in graded),
                required_met_counts=required,
                note="필수 항목별로 몇 번 충족했는지 셉니다. 한 묶음의 반복이며 통계적 보장이 아닙니다.")


def execute(flow: Path, trial: int, target: Path, times: int, approval_sha256: str, output_root: Path, *,
            range_key: Path | None = None, model: str | None = None, worker=None):
    info = make_preview(flow, trial, target, times, range_key=range_key, model=model)
    if info["approval_sha256"] != approval_sha256:
        raise ValueError("Execution requires approval bound to this exact preview")
    plan = info["plan"]
    target = target.resolve()
    record, _, archive = _trial(flow, trial)
    worker = worker or run_worker
    root = Path(output_root).resolve() / ("A2REP-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-")
                                          + record["run_id"][-6:])
    root.mkdir(parents=True, exist_ok=False)
    write(root / "preview.json", info)
    rows, used = [], 0
    for index in range(times):
        row = dict(repetition=index + 1, status="NOT_EXECUTED")
        rows.append(row)
        work = Path(tempfile.mkdtemp(prefix="qaf-a2rep-"))
        try:
            copy = work / "p1"
            for name, expected in plan["runtime_hashes"].items():
                source, dest = child(target, name), child(copy, name)
                if digest(source) != expected:
                    raise ValueError("Source changed before copy")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
            stored = work / "stored"
            _extract(archive, stored)
            run = copy / "runs" / record["run_id"]
            run.mkdir(parents=True)
            for name, expected in plan["agent1_files"].items():
                shutil.copyfile(stored / "p1" / "runs" / record["run_id"] / name, run / name)
                if digest(run / name) != expected:
                    raise ValueError("Agent 1 file changed after preview")
            shutil.rmtree(stored)
            config = dict(mode="AGENT2_REPEAT", run_id=record["run_id"], generation_model=plan["generation_model"],
                          generation_limit=CALLS_PER_REPETITION, generation_max_output_tokens=24000,
                          seen_response_ids=[], trial_timeout_seconds=1800)
            row["status"] = "STARTED"
            outcome = worker(work, config)
            ledger = read(work / "call_ledger.json") if (work / "call_ledger.json").is_file() else None
            if ledger is None or not isinstance(ledger.get("attempts"), list):
                raise ValueError("Missing generation accounting; further repetitions blocked")
            used += len(ledger["attempts"])
            row.update(status=outcome.get("status"), agent2_exit=outcome.get("stages", {}).get("agent2"),
                       generation_attempts=len(ledger["attempts"]),
                       usage=[a.get("usage") for a in ledger["attempts"]])
            out = root / f"rep-{index + 1}"
            write(out / "call_ledger.json", ledger)
            for name in ("agent2_test_design.json", "checkpoint2.json", "agent2_manifest.json"):
                if (run / name).is_file():
                    out.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(run / name, out / name)
            design = run / "agent2_test_design.json"
            row["design_sha256"] = digest(design) if design.is_file() else None
            if (run / "checkpoint2.json").is_file():
                row["checkpoint2_status"] = read(run / "checkpoint2.json").get("status")
            if "range_key" in plan:
                row["range_oracle"] = (dict(status="EVALUATED", **grade_range(
                    RangeKey.model_validate(plan["range_key"]), read(design), read(run / "agent1_change_analysis.json")))
                    if design.is_file() else {"status": "NOT_EVALUATED", "reason": "A2_OUTPUT_MISSING"})
            if len(ledger["attempts"]) > CALLS_PER_REPETITION:
                raise ValueError("Generation budget exceeded")
        except Exception as exc:
            row.update(status="ERROR", reason=type(exc).__name__)
            write(root / "result.json", dict(version=VERSION, status="STOPPED", rows=rows, generation_attempts=used))
            break
        finally:
            row["workspace_removed"] = remove_workspace(work)
        write(root / "result.json", dict(version=VERSION, status="RUNNING", rows=rows, generation_attempts=used))
    result = dict(version=VERSION, status="COMPLETED" if all(r["status"] != "ERROR" for r in rows) else "STOPPED",
                  rows=rows, generation_attempts=used, summary=_summary(plan, rows))
    write(root / "result.json", result)
    return root
