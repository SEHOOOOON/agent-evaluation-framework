"""Separate Claude judge (2026-10-24): same packet, rubric, evidence IDs and validation as
the OpenAI judge. Preview first, one explicitly approved call, no fallback. One retry only when the
answer leaves out criteria (1.5, 2026-10-24 user decision, IMP-017).

No server-side refusal fallback on purpose: a fallback would answer with a different
model, and this judge exists to compare one fixed model against people and OpenAI.
A refusal is recorded as an error instead.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from typing import Literal

from pydantic import BaseModel, Field

from .openai_judge import (JUDGE_TIMEOUT_SECONDS, MAX_JUDGE_PAYLOAD_BYTES, SYSTEM, JudgePacket, JudgmentError,
                           StrictModel, cache_ordered, encode, evidence_catalog, judge_payload,
                           resolve_references, scan_payload)

VERSION = "claude-judge-1.5"
# 1.5 (2026-10-24, IMP-017): A1 came back with 1 of 6 criteria and end_turn. The same
# request is sent once more only for CRITERION_SET_MISMATCH; other errors still stop.
MAX_CALLS = 2
RETRY_CODES = {"CRITERION_SET_MISMATCH"}
DEFAULT_CLAUDE_JUDGE_MODEL = "claude-sonnet-5-5"
DESTINATION = "https://api.anthropic.com/v1/messages"
# Room for adaptive thinking plus the structured judgments. Haiku at effort high used all
# 16K on 2026-10-24, so 1.3 streams the response (no HTTP timeout) with a 32K cap.
MAX_TOKENS = 32000
# USD per million tokens (Claude API, checked 2026-10-24). Cache writes 1.25x, reads 0.1x input.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50)}
# Haiku 5.5 prices prompts over 100K tokens at a higher rate.
LONG_PROMPT_PRICES = {"claude-haiku-5-5": (0.50, 2.50)}


class ClaudeReferencedJudgment(StrictModel):
    criterion_id: str
    verdict: Literal["PASS", "FAIL", "REVIEW", "NOT_APPLICABLE"]
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    # Required but nullable (1.1): with an optional score Claude returned FAIL without a
    # score on 2026-10-24. Same accepted values as the OpenAI judge; validation unchanged.
    score: int | None = Field(ge=0, le=5)


class ClaudeReferencedResponse(StrictModel):
    packet_sha256: str
    judgments: list[ClaudeReferencedJudgment]
    limitations: list[str]


def is_claude_model(model) -> bool:
    return isinstance(model, str) and model.startswith("claude-")


def usage_summary(response):
    """Billed token counts. input_tokens excludes cache reads/writes on the Claude API."""
    usage = getattr(response, "usage", None)
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: v for k in keys if isinstance(v := getattr(usage, k, None), int)}


EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def split_model(model):
    """'claude-sonnet-5-5:low' -> ('claude-sonnet-5-5', 'low'). Default effort high (1.4, 2026-10-24):
    at medium Sonnet missed a restore check and changed its verdicts between runs; at high it
    matched the people on the same design."""
    base, _, effort = model.partition(":")
    effort = effort or "high"
    if effort not in EFFORTS:
        raise ValueError(f"Unknown effort: {effort}")
    return base, effort


def estimated_cost_usd(model, usage):
    """Estimate only; not a billed amount."""
    model = split_model(model)[0]
    if model not in PRICES:
        return None
    rate_in, rate_out = PRICES[model]
    prompt = (usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
              + usage.get("cache_read_input_tokens", 0))
    if model in LONG_PROMPT_PRICES and prompt > 100_000:
        rate_in, rate_out = LONG_PROMPT_PRICES[model]
    total = (usage.get("input_tokens", 0) * rate_in + usage.get("output_tokens", 0) * rate_out
             + usage.get("cache_creation_input_tokens", 0) * rate_in * 1.25
             + usage.get("cache_read_input_tokens", 0) * rate_in * 0.1)
    return round(total / 1_000_000, 4)


def preview(packet: JudgePacket, model: str):
    if not is_claude_model(model):
        raise ValueError("Claude judge requires a claude-* model")
    packet, payload, catalog, rubric_coverage, payload_bytes = judge_payload(packet, model)
    # The fixed instructions are the cacheable prefix; the per-packet JSON follows.
    base, effort = split_model(model)
    request = dict(model=base, max_tokens=MAX_TOKENS,
                   system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                   messages=[{"role": "user", "content": cache_ordered(payload)}],
                   output_config={"effort": effort})
    binding = dict(request=request, packet=packet.model_dump(), schema=ClaudeReferencedResponse.model_json_schema(),
                   evidence_catalog=catalog, timeout_seconds=JUDGE_TIMEOUT_SECONDS, max_retries=0,
                   criterion_retries=MAX_CALLS - 1, retry_codes=sorted(RETRY_CODES), version=VERSION)
    return dict(destination=DESTINATION, maximum_calls=MAX_CALLS, criterion_retry_codes=sorted(RETRY_CODES),
                payload_bytes=payload_bytes, payload_limit_bytes=MAX_JUDGE_PAYLOAD_BYTES,
                request_bytes=len(encode(request).encode("utf-8")),
                rubric_coverage=rubric_coverage,
                max_retries=0, timeout_seconds=JUDGE_TIMEOUT_SECONDS, request=request,
                response_schema=ClaudeReferencedResponse.model_json_schema(),
                approval_sha256=sha256(encode(binding).encode("utf-8")).hexdigest(),
                note="전송 전 미리보기. 코드 검사 결과는 AI에 보여주지 않고 최종 결과에서 별도 보존합니다.")


def new_client():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY environment variable is required")
    # Explicit origin; do not pick up a different endpoint from an environment default.
    return anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com", max_retries=0,
                               timeout=JUDGE_TIMEOUT_SECONDS)


def evaluate(packet, model, approval_sha256, output_root, *, client_factory=None):
    from .detailed_rubric import VERSION as RUBRIC_VERSION
    packet = JudgePacket.model_validate(packet.model_dump())
    info = preview(packet, model)
    if approval_sha256 != info["approval_sha256"]:
        raise ValueError("Approval must match the exact preview")
    factory = client_factory or new_client
    mode = "TEST_DOUBLE" if client_factory is not None else "LIVE_JUDGE"
    client = factory()
    out = output_root / ("JUDGE-" + uuid4().hex[:12])
    out.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    save("preview.json", info)
    save("packet.json", packet.model_dump())
    _, catalog, fingerprint = evidence_catalog(packet)
    save("evidence_catalog.json", dict(packet_sha256=fingerprint, entries=catalog))
    started = datetime.now(timezone.utc)
    save("attempt.json", dict(mode=mode, status="STARTED", started_at=started.isoformat(),
                              sdk_parse_attempts=1, automatic_retries=0, server_completion="UNKNOWN"))
    result = dict(mode=mode, status="ERROR", assessment="INCOMPLETE", sdk_parse_attempts=1,
                  api_request_attempts=0 if mode == "TEST_DOUBLE" else 1,
                  response_received=False, structured_response_valid=False,
                  server_completion="UNKNOWN", judgments=[], code_checks=[c.model_dump() for c in packet.code_checks],
                  oracle_status=packet.oracle_status, human_review_required=True, performance_claim_eligible=False,
                  rubric_coverage=info["rubric_coverage"],
                  judge_model=model, judge_provider="anthropic",
                  rubric_version=RUBRIC_VERSION if packet.scoring_profile else None,
                  evaluator_version=VERSION, evaluator_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    def call(attempt):
        with client.messages.stream(**info["request"], output_format=ClaudeReferencedResponse) as stream:
            response = stream.get_final_message()
        result["response_received"] = True
        result["response_id"] = getattr(response, "id", None)
        result["request_id"] = getattr(response, "_request_id", None)
        stop_reason = getattr(response, "stop_reason", None)
        result["stop_reason"] = stop_reason
        # end_turn is the only complete answer; refusal/max_tokens are recorded, not retried.
        result["server_completion"] = "completed" if stop_reason == "end_turn" else (stop_reason or "UNKNOWN")
        usage = usage_summary(response)
        for key, value in usage.items():
            result["usage"][key] = result["usage"].get(key, 0) + value
        result["estimated_cost_usd"] = estimated_cost_usd(model, result["usage"])
        if not result["response_id"] or result["server_completion"] != "completed":
            raise ValueError("Response incomplete or identity missing")
        received = getattr(response, "parsed_output", None)
        received = received.model_dump(mode="json") if isinstance(received, BaseModel) else received
        try:
            encoded = encode(received)
            if len(encoded.encode("utf-8")) > 120_000:
                raise ValueError("Diagnostic too large")
            scan_payload(received)
        except (ValueError, TypeError):
            result["response_diagnostic"] = "OMITTED_UNSAFE_OR_OVERSIZED"
            raise JudgmentError("UNSAFE_OR_OVERSIZED_RESPONSE") from None
        save("received_response.json", received)
        result["response_diagnostic"] = "SAVED_UNVALIDATED"
        result["received_sha256"] = sha256(encoded.encode("utf-8")).hexdigest()
        return received, resolve_references(packet, received)

    result["usage"] = {}
    try:
        for attempt in range(1, MAX_CALLS + 1):
            result["sdk_parse_attempts"] = attempt
            if mode == "LIVE_JUDGE":
                result["api_request_attempts"] = attempt
            try:
                received, parsed = call(attempt)
                break
            except JudgmentError as exc:
                if exc.diagnostic.get("code") not in RETRY_CODES or attempt == MAX_CALLS:
                    raise
                result.setdefault("retried_after", []).append(exc.diagnostic)
                # Keep the rejected answer; received_response.json is always the latest one.
                if (out / "received_response.json").is_file():
                    (out / "received_response.json").replace(out / f"received_response_attempt_{attempt}.json")
        result.update(status="COMPLETED", structured_response_valid=True,
                      judgments=[j.model_dump(exclude_none=True) for j in parsed.judgments],
                      limitations=parsed.limitations)
        if packet.scoring_profile:
            from .detailed_rubric import score_summary
            result.update(scoring_profile=packet.scoring_profile, scoring=score_summary(result["judgments"]))
        code_states = {c.status for c in packet.code_checks}
        if "FAIL" in code_states or any(j.verdict == "FAIL" for j in parsed.judgments):
            result["assessment"] = "ISSUES_FOUND"
        elif "ERROR" in code_states:
            result["assessment"] = "INCOMPLETE"
        else:
            result["assessment"] = "REVIEW"
    except Exception as exc:
        # SDK errors may echo credentials or entire requests; preserve type only.
        result["error_type"] = type(exc).__name__
        if isinstance(exc, JudgmentError):
            result["validation_error"] = exc.diagnostic
        elif result["response_received"]:
            result["validation_error"] = {"code": "RESPONSE_SHAPE_OR_COMPLETION", "criterion_index": None}
    finally:
        if client_factory is None:
            try:
                client.close()
            except Exception as exc:
                result["client_close_error"] = type(exc).__name__
    if any(c.status == "FAIL" for c in packet.code_checks):
        result["assessment"] = "ISSUES_FOUND"
    finished = datetime.now(timezone.utc)
    result.update(started_at=started.isoformat(), finished_at=finished.isoformat(),
                  elapsed_seconds=round((finished - started).total_seconds(), 3))
    save("result.json", result)
    save("attempt.json", dict(mode=mode, status="FINISHED", started_at=result["started_at"],
                              finished_at=result["finished_at"], elapsed_seconds=result["elapsed_seconds"],
                              sdk_parse_attempts=result["sdk_parse_attempts"],
                              automatic_retries=result["sdk_parse_attempts"] - 1, server_completion=result["server_completion"],
                              response_received=result["response_received"]))
    return out
