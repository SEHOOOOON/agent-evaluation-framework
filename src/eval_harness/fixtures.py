from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .models import (
    CheckResult,
    CheckStatus,
    Finding,
    Severity,
    StageResult,
    StageStatus,
)
from .storage import ArtifactStore


SOURCE_FILES = {
    "agent1": Path("docs/samples/agent1_output_sample.md"),
    "agent2": Path("docs/samples/agent2_tc_revised.md"),
    "agent3": Path("reports/qa_results.json"),
    "agent4": Path("reports/agent4_summary.json"),
    "checkpoint4": Path("reports/checkpoint4_validation.json"),
}

A1_REQUIRED_FIELDS = {
    "feature_id",
    "source_ids",
    "input_assessment",
    "confirmed_facts",
    "inferred_facts",
    "information_gaps",
    "clarification_questions",
    "control_points",
    "testable_scope",
    "excluded_scope",
    "integration_assessment",
    "agent1_recommendation",
}

A2_REQUIRED_FIELDS = {
    "tc_id",
    "tc_category",
    "title",
    "requirement_ids",
    "source_evidence",
    "qa_criteria",
    "quality_standard_ids",
    "risk_covered",
    "initial_state",
    "preconditions",
    "setup",
    "steps",
    "expected_ui",
    "expected_device_state",
    "negative_assertions",
    "unchanged_state_assertions",
    "excluded_assertions",
    "cleanup",
    "double_assert_policy",
    "automation_level",
    "independent_execution",
    "review_status",
}

A3_REQUIRED_FIELDS = {
    "run_id",
    "tc_id",
    "test_id",
    "description",
    "result",
    "failure_reason",
    "executed_at",
    "duration",
    "evidence_path",
}
A3_ALLOWED_RESULTS = {"passed", "failed", "skipped", "error"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _source_path(root: Path, key: str) -> Path:
    resolved_root = root.resolve()
    target = (resolved_root / SOURCE_FILES[key]).resolve()
    if resolved_root not in target.parents or not target.is_file():
        raise FileNotFoundError(f"필수 fixture 파일이 없습니다: {SOURCE_FILES[key]}")
    return target


def _read_text(root: Path, key: str) -> str:
    return _source_path(root, key).read_text(encoding="utf-8")


def validate_source(root: Path) -> list[str]:
    return [str(path) for path in SOURCE_FILES.values() if not (root / path).is_file()]


def source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for key, relative_path in sorted(SOURCE_FILES.items()):
        target = _source_path(root, key)
        digest.update(key.encode("utf-8"))
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(target.read_bytes())
    return digest.hexdigest()


def _yaml_block(markdown: str) -> dict[str, Any]:
    blocks = re.findall(r"```ya?ml\s*(.*?)```", markdown, flags=re.DOTALL | re.IGNORECASE)
    if len(blocks) != 1:
        raise ValueError(f"YAML Data Contract는 정확히 1개여야 합니다. actual={len(blocks)}")
    parsed = yaml.safe_load(blocks[0])
    if not isinstance(parsed, dict):
        raise ValueError("YAML Data Contract 최상위 값이 객체가 아닙니다.")
    return parsed


def _stage_state(checks: list[CheckResult]) -> tuple[StageStatus, str]:
    if any(check.status == CheckStatus.ERROR for check in checks):
        return StageStatus.ERROR, "ERROR"
    if any(check.status == CheckStatus.FAIL for check in checks):
        return StageStatus.FAIL, "HOLD"
    if any(check.status == CheckStatus.ABSTAIN for check in checks):
        return StageStatus.REVIEW, "REVIEW"
    applicable = [check for check in checks if check.status != CheckStatus.NOT_APPLICABLE]
    if not applicable:
        return StageStatus.REVIEW, "REVIEW"
    return StageStatus.PASS, "PASS"


def evaluate_agent1(source_root: Path, store: ArtifactStore) -> StageResult:
    started = now_iso()
    raw = _read_text(source_root, "agent1")
    raw_ref = store.add_text("A1-RAW", raw, ".md")
    checks: list[CheckResult] = []
    findings: list[Finding] = []
    normalized_ref: str | None = None

    try:
        contract = _yaml_block(raw)
        missing = sorted(A1_REQUIRED_FIELDS - set(contract))
        checks.append(
            CheckResult(
                criterion_id="A1-SCHEMA-001",
                title="Agent 1 Data Contract 필수 필드",
                status=CheckStatus.PASS if not missing else CheckStatus.FAIL,
                severity=Severity.HIGH,
                reason="필수 필드를 모두 확인했습니다." if not missing else "필수 필드가 누락됐습니다.",
                expected=sorted(A1_REQUIRED_FIELDS),
                actual={"missing": missing},
                evidence_refs=[raw_ref],
            )
        )
        section = re.search(r"###.*?\[확정 정보\].*?(?=\n###|\Z)", raw, flags=re.DOTALL)
        markdown_ids = re.findall(
            r"\*\*Fact ID\*\*:\s*(FACT-\d+)", section.group(0) if section else ""
        )
        yaml_ids = [str(item.get("fact_id")) for item in contract.get("confirmed_facts", [])]
        consistent = markdown_ids == yaml_ids
        checks.append(
            CheckResult(
                criterion_id="A1-CONSIST-001",
                title="Markdown 설명과 YAML 확정 사실 1:1 일치",
                status=CheckStatus.PASS if consistent else CheckStatus.FAIL,
                severity=Severity.HIGH,
                reason=(
                    "두 표현 계층의 Fact ID가 일치합니다."
                    if consistent
                    else "Markdown 확정 사실과 YAML confirmed_facts가 일치하지 않습니다."
                ),
                expected={"markdown_fact_ids": markdown_ids},
                actual={"yaml_fact_ids": yaml_ids},
                evidence_refs=[raw_ref],
            )
        )
        if not consistent:
            yaml_only = [fact_id for fact_id in yaml_ids if fact_id not in markdown_ids]
            findings.append(
                Finding(
                    finding_id="FIND-A1-001",
                    criterion_id="A1-CONSIST-001",
                    title="Agent 1 확정 사실 표현 불일치",
                    severity=Severity.HIGH,
                    reason=f"YAML에만 존재하는 Fact ID: {', '.join(yaml_only) or '없음'}",
                    recommended_action="Markdown 설명과 YAML Data Contract의 Fact ID를 동일한 SSOT에서 생성합니다.",
                    evidence_refs=[raw_ref],
                )
            )
        normalized_ref = store.add_json(
            "A1-NORMALIZED",
            {
                "feature_id": contract.get("feature_id"),
                "markdown_confirmed_fact_ids": markdown_ids,
                "yaml_confirmed_fact_ids": yaml_ids,
                "recommendation": contract.get("agent1_recommendation"),
                "missing_required_fields": missing,
            },
        )
    except Exception as exc:
        checks.append(
            CheckResult(
                criterion_id="A1-PARSE-001",
                title="Agent 1 산출물 파싱",
                status=CheckStatus.ERROR,
                severity=Severity.HIGH,
                reason=str(exc),
                evidence_refs=[raw_ref],
            )
        )

    status, checkpoint = _stage_state(checks)
    return StageResult(
        stage_id="agent_1",
        step=1,
        agent_name="Agent 1 · 요구사항 정제",
        agent_mode="생성형 산출물 Fixture 검사",
        component_type="generative_artifact",
        sut_executed=False,
        checkpoint_id="Checkpoint 1 · 문서 기반",
        checkpoint_status=checkpoint,
        status=status,
        mode_note="기존 Markdown/YAML 산출물을 읽기 전용으로 평가합니다. 실제 모델 호출이 아닙니다.",
        input_refs=[raw_ref],
        raw_output_ref=raw_ref,
        normalized_output_ref=normalized_ref,
        checks=checks,
        findings=findings,
        started_at=started,
        completed_at=now_iso(),
    )


def evaluate_agent2(source_root: Path, store: ArtifactStore) -> StageResult:
    started = now_iso()
    raw = _read_text(source_root, "agent2")
    raw_ref = store.add_text("A2-RAW", raw, ".md")
    checks: list[CheckResult] = []
    findings: list[Finding] = []
    normalized_ref: str | None = None

    try:
        contract = _yaml_block(raw)
        test_cases = contract.get("test_cases") or []
        if not isinstance(test_cases, list):
            raise ValueError("test_cases는 배열이어야 합니다.")
        missing_by_tc = {
            str(case.get("tc_id", f"index-{index}")): sorted(A2_REQUIRED_FIELDS - set(case))
            for index, case in enumerate(test_cases)
            if isinstance(case, dict) and A2_REQUIRED_FIELDS - set(case)
        }
        malformed = [index for index, case in enumerate(test_cases) if not isinstance(case, dict)]
        checks.append(
            CheckResult(
                criterion_id="A2-SCHEMA-001",
                title="TC 필수 필드 준수",
                status=CheckStatus.PASS if not missing_by_tc and not malformed else CheckStatus.FAIL,
                severity=Severity.HIGH,
                reason=(
                    "모든 TC가 필수 필드를 포함합니다."
                    if not missing_by_tc and not malformed
                    else "필수 필드가 없거나 객체가 아닌 TC가 있습니다."
                ),
                expected={"required_fields": sorted(A2_REQUIRED_FIELDS)},
                actual={"missing_by_tc": missing_by_tc, "malformed_indexes": malformed},
                evidence_refs=[raw_ref],
            )
        )
        if missing_by_tc:
            findings.append(
                Finding(
                    finding_id="FIND-A2-001",
                    criterion_id="A2-SCHEMA-001",
                    title="승인 TC 필수 필드 누락",
                    severity=Severity.HIGH,
                    reason=f"필수 필드 누락: {missing_by_tc}",
                    recommended_action="모든 승인 TC에 계약 필드를 기록하고 Schema 검사를 승인 전에 수행합니다.",
                    evidence_refs=[raw_ref],
                )
            )

        approved_cases = [case for case in test_cases if isinstance(case, dict)
                          and case.get("review_status") == "Approved"]
        # Presence only: no TC-name or wording oracle, and no inference that every
        # TC must modify other devices. Empty evidence needs applicability review.
        missing_invariants = [case.get("tc_id") for case in approved_cases
            if not isinstance(case.get("unchanged_state_assertions"), list)
            or not case["unchanged_state_assertions"]
            or any(not isinstance(item, str) or not item.strip()
                   for item in case["unchanged_state_assertions"])]
        invariant_status = (CheckStatus.ABSTAIN if missing_invariants else
                            CheckStatus.PASS if approved_cases else CheckStatus.NOT_APPLICABLE)
        checks.append(
            CheckResult(
                criterion_id="A2-INVARIANT-001",
                title="승인 TC의 불변 검증 근거 유무",
                status=invariant_status,
                severity=Severity.CRITICAL,
                reason=(
                    "검증 항목의 존재만 확인합니다. 의미 충분성은 별도 검토 대상입니다."
                    if not missing_invariants
                    else "불변 검증 근거가 비어 있습니다. 필요한 검사 누락인지 정당한 비적용인지 확인해야 합니다."
                ),
                expected={"empty_evidence_is_not_automatic_pass": True},
                actual={"approved_count": len(approved_cases), "needs_applicability_review": missing_invariants},
                evidence_refs=[raw_ref],
            )
        )
        if missing_invariants:
            findings.append(
                Finding(
                    finding_id="FIND-A2-002",
                    criterion_id="A2-INVARIANT-001",
                    title="승인 TC의 불변 검증 적용 여부 확인 필요",
                    severity=Severity.CRITICAL,
                    reason=f"검증 근거가 비어 있는 승인 TC: {missing_invariants}",
                    recommended_action="독립 기준에서 검증 필요 여부를 확인하고, 필요한 근거 또는 비적용 사유를 검토합니다.",
                    evidence_refs=[raw_ref],
                )
            )
        normalized_ref = store.add_json(
            "A2-NORMALIZED",
            {
                "test_case_count": len(test_cases),
                "test_cases": [
                    {
                        "tc_id": case.get("tc_id"),
                        "tc_category": case.get("tc_category"),
                        "requirement_ids": case.get("requirement_ids", []),
                        "review_status": case.get("review_status"),
                        "excluded_assertions": case.get("excluded_assertions", []),
                    }
                    for case in test_cases
                    if isinstance(case, dict)
                ],
                "missing_required_fields": missing_by_tc,
            },
        )
    except Exception as exc:
        checks.append(
            CheckResult(
                criterion_id="A2-PARSE-001",
                title="Agent 2 산출물 파싱",
                status=CheckStatus.ERROR,
                severity=Severity.HIGH,
                reason=str(exc),
                evidence_refs=[raw_ref],
            )
        )

    status, checkpoint = _stage_state(checks)
    return StageResult(
        stage_id="agent_2",
        step=2,
        agent_name="Agent 2 · 테스트케이스 설계",
        agent_mode="생성형 산출물 Fixture 검사",
        component_type="generative_artifact",
        sut_executed=False,
        checkpoint_id="Checkpoint 2 · 문서 기반",
        checkpoint_status=checkpoint,
        status=status,
        mode_note="승인된 TC 문서를 Schema와 위험 불변성 기준으로 평가합니다. 실제 모델 호출이 아닙니다.",
        input_refs=[raw_ref],
        raw_output_ref=raw_ref,
        normalized_output_ref=normalized_ref,
        checks=checks,
        findings=findings,
        started_at=started,
        completed_at=now_iso(),
    )


def evaluate_agent3(source_root: Path, store: ArtifactStore) -> StageResult:
    started = now_iso()
    raw = _read_text(source_root, "agent3")
    try:
        results = json.loads(raw)
    except json.JSONDecodeError as exc:
        results = []
        parse_error = str(exc)
    else:
        parse_error = None
    raw_ref = store.add_json("A3-RAW", results if parse_error is None else {"parse_error": parse_error})
    checks: list[CheckResult] = []
    findings: list[Finding] = []

    rows_are_dicts = isinstance(results, list) and all(isinstance(item, dict) for item in results)
    missing_by_row = {
        str(index): sorted(A3_REQUIRED_FIELDS - set(item))
        for index, item in enumerate(results)
        if isinstance(item, dict) and A3_REQUIRED_FIELDS - set(item)
    }
    invalid_results = [
        {"index": index, "result": item.get("result")}
        for index, item in enumerate(results)
        if isinstance(item, dict) and item.get("result") not in A3_ALLOWED_RESULTS
    ]
    schema_ok = parse_error is None and rows_are_dicts and not missing_by_row and not invalid_results
    checks.append(
        CheckResult(
            criterion_id="A3-SCHEMA-001",
            title="실행 결과 필드·상태 계약",
            status=CheckStatus.PASS if schema_ok else CheckStatus.ERROR if parse_error else CheckStatus.FAIL,
            severity=Severity.HIGH,
            reason="실행 결과 필드와 상태를 확인했습니다." if schema_ok else (parse_error or "결과 Schema가 올바르지 않습니다."),
            expected={"required_fields": sorted(A3_REQUIRED_FIELDS), "allowed_results": sorted(A3_ALLOWED_RESULTS)},
            actual={"missing_by_row": missing_by_row, "invalid_results": invalid_results},
            evidence_refs=[raw_ref],
        )
    )

    run_ids = sorted({str(item.get("run_id") or "") for item in results if isinstance(item, dict)})
    run_ok = len(run_ids) == 1 and bool(run_ids[0])
    checks.append(
        CheckResult(
            criterion_id="A3-RUN-001",
            title="Source Test Run ID 단일성",
            status=CheckStatus.PASS if run_ok else CheckStatus.FAIL,
            severity=Severity.CRITICAL,
            reason="단일 Source Run의 결과만 포함합니다." if run_ok else "서로 다른 Source Run 또는 빈 Run ID가 섞였습니다.",
            expected={"unique_non_empty_run_ids": 1},
            actual={"run_ids": run_ids},
            evidence_refs=[raw_ref],
        )
    )

    test_ids = [str(item.get("test_id") or "") for item in results if isinstance(item, dict)]
    tc_ids = [str(item.get("tc_id") or "") for item in results if isinstance(item, dict)]
    id_ok = (
        bool(test_ids)
        and all(test_ids)
        and all(tc_ids)
        and len(test_ids) == len(set(test_ids))
        and len(tc_ids) == len(set(tc_ids))
    )
    checks.append(
        CheckResult(
            criterion_id="A3-ID-001",
            title="Test ID·TC ID 누락과 중복 통제",
            status=CheckStatus.PASS if id_ok else CheckStatus.FAIL,
            severity=Severity.HIGH,
            reason="실행 식별자가 모두 고유합니다." if id_ok else "실행 식별자가 비어 있거나 중복됐습니다.",
            expected={"non_empty_unique_test_id_and_tc_id": True},
            actual={"test_ids": test_ids, "tc_ids": tc_ids},
            evidence_refs=[raw_ref],
        )
    )

    status_mismatches = []
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        reason = str(item.get("failure_reason") or "").strip()
        result = item.get("result")
        if (result == "passed" and reason) or (result in {"failed", "skipped", "error"} and not reason):
            status_mismatches.append(index)
    checks.append(
        CheckResult(
            criterion_id="A3-STATUS-001",
            title="상태와 실패 사유 조합",
            status=CheckStatus.PASS if not status_mismatches else CheckStatus.FAIL,
            severity=Severity.HIGH,
            reason="상태와 실패 사유 조합이 유효합니다." if not status_mismatches else "상태와 실패 사유 조합이 맞지 않습니다.",
            expected={"passed_reason_empty_non_pass_reason_present": True},
            actual={"mismatch_indexes": status_mismatches},
            evidence_refs=[raw_ref],
        )
    )

    non_pass = [item for item in results if isinstance(item, dict) and item.get("result") != "passed"]
    missing_evidence = [
        str(item.get("tc_id")) for item in non_pass if not str(item.get("evidence_path") or "").strip()
    ]
    evidence_ok = not missing_evidence
    checks.append(
        CheckResult(
            criterion_id="A3-EVIDENCE-001",
            title="비통과 결과의 증거 연결",
            status=CheckStatus.PASS if evidence_ok else CheckStatus.FAIL,
            severity=Severity.HIGH,
            reason="모든 비통과 결과에 증거가 연결됐습니다." if evidence_ok else "비통과 결과의 evidence_path가 비어 있습니다.",
            expected={"non_pass_results_require_evidence": True},
            actual={"missing_evidence_tc_ids": missing_evidence},
            evidence_refs=[raw_ref],
        )
    )
    if not evidence_ok:
        findings.append(
            Finding(
                finding_id="FIND-A3-001",
                criterion_id="A3-EVIDENCE-001",
                title="비통과 실행 결과 증거 누락",
                severity=Severity.HIGH,
                reason=f"증거가 없는 TC: {', '.join(missing_evidence)}",
                recommended_action="비통과 시 Screenshot·Trace·로그 중 최소 하나를 Run ID와 함께 저장합니다.",
                evidence_refs=[raw_ref],
            )
        )

    normalized_ref = store.add_json(
        "A3-NORMALIZED",
        {
            "total": len(results),
            "passed": sum(1 for item in results if isinstance(item, dict) and item.get("result") == "passed"),
            "failed": sum(1 for item in results if isinstance(item, dict) and item.get("result") == "failed"),
            "skipped": sum(1 for item in results if isinstance(item, dict) and item.get("result") == "skipped"),
            "error": sum(1 for item in results if isinstance(item, dict) and item.get("result") == "error"),
            "source_run_ids": run_ids,
            "non_pass_without_evidence": missing_evidence,
            "rows": [{"tc_id": item.get("tc_id"), "test_id": item.get("test_id"), "result": item.get("result")} for item in results if isinstance(item, dict)],
        },
    )
    status, checkpoint = _stage_state(checks)
    return StageResult(
        stage_id="agent_3",
        step=3,
        agent_name="Agent 3 · Playwright 실행",
        agent_mode="Pytest 결과 Fixture 검사",
        component_type="deterministic_executor",
        sut_executed=False,
        checkpoint_id="Checkpoint 3 · 결과 증거 검사",
        checkpoint_status=checkpoint,
        status=status,
        mode_note="기존 qa_results.json을 검사합니다. 브라우저 테스트를 다시 실행하지 않습니다.",
        input_refs=[raw_ref],
        raw_output_ref=raw_ref,
        normalized_output_ref=normalized_ref,
        checks=checks,
        findings=findings,
        started_at=started,
        completed_at=now_iso(),
    )


def evaluate_agent4(source_root: Path, store: ArtifactStore) -> StageResult:
    started = now_iso()
    summary = json.loads(_read_text(source_root, "agent4"))
    checkpoint = json.loads(_read_text(source_root, "checkpoint4"))
    source_results = json.loads(_read_text(source_root, "agent3"))
    summary_ref = store.add_json("A4-SUMMARY", summary)
    checkpoint_ref = store.add_json("A4-CHECKPOINT", checkpoint)
    checks: list[CheckResult] = []
    findings: list[Finding] = []

    summary_required = {"pytest_summary", "results", "execution"}
    checkpoint_required = {"status", "errors"}
    summary_missing = sorted(summary_required - set(summary)) if isinstance(summary, dict) else sorted(summary_required)
    checkpoint_missing = sorted(checkpoint_required - set(checkpoint)) if isinstance(checkpoint, dict) else sorted(checkpoint_required)
    schema_ok = (
        isinstance(summary, dict)
        and isinstance(checkpoint, dict)
        and isinstance(summary.get("results"), list)
        and isinstance(checkpoint.get("errors"), list)
        and not summary_missing
        and not checkpoint_missing
    )
    checks.append(
        CheckResult(
            criterion_id="A4-SCHEMA-001",
            title="Summary·Checkpoint 4 필드 계약",
            status=CheckStatus.PASS if schema_ok else CheckStatus.FAIL,
            severity=Severity.HIGH,
            reason="Agent 4와 Checkpoint 4 계약을 확인했습니다." if schema_ok else "Agent 4 또는 Checkpoint 4 필드가 누락됐습니다.",
            expected={"summary": sorted(summary_required), "checkpoint": sorted(checkpoint_required)},
            actual={"summary_missing": summary_missing, "checkpoint_missing": checkpoint_missing},
            evidence_refs=[summary_ref, checkpoint_ref],
        )
    )

    pytest_summary = summary.get("pytest_summary") or {}
    results = summary.get("results") or []
    states = ("passed", "failed", "skipped", "error")
    source_valid = (bool(source_results)
                    and all(isinstance(item, dict) and item.get("result") in states for item in source_results))
    source_counts = Counter(item["result"] for item in source_results) if source_valid else Counter()
    total = pytest_summary.get("total")
    counts_valid = all(type(pytest_summary.get(key)) is int and pytest_summary[key] >= 0
                       for key in ("total", *states))
    component_total = sum(pytest_summary[key] for key in states) if counts_valid else None
    counts_ok = (source_valid and counts_valid and total == len(results) == len(source_results) == component_total
                 and all(pytest_summary[key] == source_counts[key] for key in states))
    checks.append(
        CheckResult(
            criterion_id="A4-COUNT-001",
            title="실행 수치와 결과 행 정합성",
            status=CheckStatus.PASS if counts_ok else CheckStatus.FAIL,
            severity=Severity.CRITICAL,
            reason="요약 수치와 결과 행 수가 일치합니다." if counts_ok else "요약 수치와 결과 행 수가 다릅니다.",
            expected={"total_equals_result_rows_and_components": True},
            actual={"total": total, "result_rows": len(results), "component_total": component_total,
                    "source_counts": dict(source_counts)},
            evidence_refs=[summary_ref],
        )
    )

    source_rows = Counter((str(item.get("tc_id")), str(item.get("result"))) for item in source_results if isinstance(item, dict))
    report_rows = Counter((str(item.get("tc_id")), str(item.get("result"))) for item in results if isinstance(item, dict))
    rows_ok = source_rows == report_rows
    checks.append(
        CheckResult(
            criterion_id="A4-ROW-001",
            title="Agent 3 원본과 Agent 4 결과 행 불변",
            status=CheckStatus.PASS if rows_ok else CheckStatus.FAIL,
            severity=Severity.CRITICAL,
            reason="원본 실행 결과 행이 Agent 4에서 유지됐습니다." if rows_ok else "Agent 3와 Agent 4의 TC·상태 행이 다릅니다.",
            expected=dict(source_rows),
            actual=dict(report_rows),
            evidence_refs=[summary_ref],
        )
    )

    a3_run_ids = {str(item.get("run_id")) for item in source_results if isinstance(item, dict) and item.get("run_id")}
    candidate_run_ids = set()
    if summary.get("source_execution_run_id"):
        candidate_run_ids.add(str(summary["source_execution_run_id"]))
    candidate_run_ids.update(str(item.get("run_id")) for item in results if isinstance(item, dict) and item.get("run_id"))
    if checkpoint.get("run_id"):
        candidate_run_ids.add(str(checkpoint["run_id"]))
    source_run_ok = len(a3_run_ids) == 1 and bool(candidate_run_ids) and candidate_run_ids == a3_run_ids
    checks.append(
        CheckResult(
            criterion_id="PIPE-RUN-001",
            title="Agent 3→4 Source Run 추적성",
            status=CheckStatus.PASS if source_run_ok else CheckStatus.FAIL,
            severity=Severity.CRITICAL,
            reason="Agent 3와 Agent 4가 동일 Source Run을 사용합니다." if source_run_ok else "Source Run ID가 없거나 단계 사이에 일치하지 않습니다.",
            expected={"agent3_run_ids": sorted(a3_run_ids)},
            actual={"agent4_candidate_run_ids": sorted(candidate_run_ids)},
            evidence_refs=[summary_ref, checkpoint_ref],
        )
    )

    cp_status = checkpoint.get("status")
    cp_errors = checkpoint.get("errors") or []
    reporting_allowed = checkpoint.get("external_reporting_allowed")
    cp4_ok = (
        (cp_status == "VALIDATED" and not cp_errors and reporting_allowed is not False)
        or (cp_status == "BLOCKED" and bool(cp_errors) and reporting_allowed is not True)
    )
    checks.append(
        CheckResult(
            criterion_id="A4-CP4-001",
            title="Checkpoint 4 상태와 보고 허용 정합성",
            status=CheckStatus.PASS if cp4_ok else CheckStatus.FAIL,
            severity=Severity.CRITICAL,
            reason="Checkpoint 4 상태·오류·보고 허용이 일치합니다." if cp4_ok else "Checkpoint 4 상태와 오류 또는 보고 허용 값이 충돌합니다.",
            expected={"VALIDATED": "errors=0 and reporting_allowed!=false", "BLOCKED": "errors>0 and reporting_allowed!=true"},
            actual={"status": cp_status, "errors": cp_errors, "external_reporting_allowed": reporting_allowed},
            evidence_refs=[checkpoint_ref],
        )
    )

    checks.append(
        CheckResult(
            criterion_id="A4-CLASS-001",
            title="5가지 원인 분류 정확성",
            status=CheckStatus.NOT_APPLICABLE,
            severity=Severity.MEDIUM,
            reason="현재 fixture는 Agent 3의 사전 분류 문구와 Agent 4 결과를 함께 포함하여 독립 Gold Label이 없습니다.",
            expected={"independent_gold_labels": True},
            actual={"independent_gold_labels": False, "evaluation": "N/A"},
            evidence_refs=[summary_ref],
        )
    )
    findings.append(
        Finding(
            finding_id="FIND-A4-001",
            criterion_id="A4-CLASS-001",
            title="Agent 4 분류 정확도 독립 평가 불가",
            severity=Severity.MEDIUM,
            reason="Agent 4가 중립 증거가 아니라 이전 단계의 분류 의미가 포함된 failure_reason을 사용합니다.",
            recommended_action="중립 오류 신호와 별도의 Human Gold Label fixture를 만들어 분류 정확도를 평가합니다.",
            evidence_refs=[summary_ref],
        )
    )

    release = (summary.get("execution") or {}).get("release_decision")
    non_pass = sum(source_counts[key] for key in states if key != "passed")
    gate_ok = source_valid and release == ("HOLD" if non_pass else "PASS")
    checks.append(
        CheckResult(
            criterion_id="A4-GATE-001",
            title="제품 릴리즈 판정 규칙",
            status=CheckStatus.PASS if gate_ok else CheckStatus.FAIL,
            severity=Severity.CRITICAL,
            reason="비통과 실행 결과와 제품 릴리즈 판정이 일치합니다." if gate_ok else "제품 릴리즈 판정이 실행 결과와 맞지 않습니다.",
            expected={"release_decision": "HOLD" if non_pass else "PASS"},
            actual={"release_decision": release},
            evidence_refs=[summary_ref, checkpoint_ref],
        )
    )

    normalized_ref = store.add_json(
        "A4-NORMALIZED",
        {
            "source_checkpoint_status": cp_status,
            "pytest_summary": pytest_summary,
            "qa_classification_summary": summary.get("qa_classification_summary"),
            "product_release_decision": release,
            "source_test_run_ids": sorted(a3_run_ids),
            "agent4_source_run_ids": sorted(candidate_run_ids),
            "classification_evaluation": "N/A",
        },
    )
    status, evaluation_checkpoint = _stage_state(checks)
    return StageResult(
        stage_id="agent_4",
        step=4,
        agent_name="Agent 4 · 결과 분석·보고",
        agent_mode="결정론적 규칙 엔진 Fixture 검사",
        component_type="deterministic_analyzer",
        sut_executed=False,
        checkpoint_id="Checkpoint 4 · 교차검증",
        checkpoint_status=evaluation_checkpoint,
        status=status,
        mode_note="수치·Source Run·Gate는 독립 검사하고, 분류 정확도는 Gold Label 부재로 N/A 처리합니다.",
        input_refs=[summary_ref, checkpoint_ref],
        raw_output_ref=summary_ref,
        normalized_output_ref=normalized_ref,
        checks=checks,
        findings=findings,
        started_at=started,
        completed_at=now_iso(),
    )


FIXTURE_EVALUATORS = (
    evaluate_agent1,
    evaluate_agent2,
    evaluate_agent3,
    evaluate_agent4,
)