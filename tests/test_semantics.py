from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eval_harness.fixtures import _stage_state, now_iso, evaluate_agent2, evaluate_agent4, _yaml_block
from eval_harness.models import (
    CheckResult,
    CheckStatus,
    GateDecision,
    Severity,
    StageResult,
    StageStatus,
)
from eval_harness.runner import EvaluationRunner, gate_stage
from eval_harness.storage import ArtifactStore
from eval_harness.web.app import create_app


def make_stage(checks: list[CheckResult]) -> StageResult:
    return StageResult(
        stage_id="synthetic",
        step=1,
        agent_name="Synthetic",
        agent_mode="deterministic-test",
        component_type="test_component",
        sut_executed=False,
        checkpoint_id="CP",
        checkpoint_status="HOLD",
        status=StageStatus.FAIL,
        mode_note="gate semantics test",
        checks=checks,
        started_at=now_iso(),
        completed_at=now_iso(),
    )


def test_not_applicable_is_excluded_but_abstain_requires_review() -> None:
    pass_check = CheckResult(
        criterion_id="PASS-1", title="pass", status=CheckStatus.PASS,
        severity=Severity.LOW, reason="ok",
    )
    na_check = CheckResult(
        criterion_id="NA-1", title="na", status=CheckStatus.NOT_APPLICABLE,
        severity=Severity.LOW, reason="not applicable",
    )
    abstain_check = CheckResult(
        criterion_id="ABS-1", title="abstain", status=CheckStatus.ABSTAIN,
        severity=Severity.MEDIUM, reason="human judgment required",
    )

    assert _stage_state([pass_check, na_check]) == (StageStatus.PASS, "PASS")
    assert _stage_state([na_check]) == (StageStatus.REVIEW, "REVIEW")
    assert _stage_state([pass_check, abstain_check]) == (StageStatus.REVIEW, "REVIEW")


def test_gate_blocks_high_failure_even_without_finding(tmp_path: Path) -> None:
    failed = CheckResult(
        criterion_id="ORPHAN-FAIL", title="orphan fail", status=CheckStatus.FAIL,
        severity=Severity.HIGH, reason="Finding 없이도 차단되어야 합니다.",
    )
    stage = make_stage([failed])
    gate, decision = gate_stage([stage], ArtifactStore(tmp_path / "run"), True)

    assert decision == GateDecision.HOLD
    assert gate.status == StageStatus.FAIL
    assert "synthetic:ORPHAN-FAIL" in gate.checks[0].actual["blocking_checks"]


def test_manifest_records_fixture_truth_and_source_hash(
    known_bad_root: Path, runs_root: Path
) -> None:
    manifest = EvaluationRunner(known_bad_root, runs_root).run()

    assert manifest.sut_executed is False
    assert manifest.stability_evaluated is False
    assert manifest.source_hash_before == manifest.source_hash_after
    assert manifest.source_unchanged is True
    assert all(stage.sut_executed is False for stage in manifest.stages)
    assert all(stage.component_type for stage in manifest.stages)


def test_runner_rejects_source_and_run_root_overlap(known_bad_root: Path) -> None:
    with pytest.raises(ValueError):
        EvaluationRunner(known_bad_root, known_bad_root / "evaluation-runs")
    with pytest.raises(ValueError):
        EvaluationRunner(known_bad_root, known_bad_root.parent)


def test_agent3_detects_mixed_run_and_duplicate_ids(
    known_bad_root: Path, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    shutil.copytree(known_bad_root, source)
    result_path = source / "reports" / "qa_results.json"
    rows = json.loads(result_path.read_text(encoding="utf-8"))
    rows[1]["run_id"] = "RUN-MIXED"
    rows[1]["tc_id"] = rows[0]["tc_id"]
    result_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = EvaluationRunner(source, tmp_path / "runs").run()
    stage = next(item for item in manifest.stages if item.stage_id == "agent_3")
    statuses = {check.criterion_id: check.status for check in stage.checks}

    assert statuses["A3-RUN-001"] == CheckStatus.FAIL
    assert statuses["A3-ID-001"] == CheckStatus.FAIL
    assert manifest.release_decision == GateDecision.HOLD


def test_agent4_detects_checkpoint4_conflict(
    known_bad_root: Path, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    shutil.copytree(known_bad_root, source)
    checkpoint_path = source / "reports" / "checkpoint4_validation.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["status"] = "BLOCKED"
    checkpoint["errors"] = []
    checkpoint["external_reporting_allowed"] = True
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = EvaluationRunner(source, tmp_path / "runs").run()
    stage = next(item for item in manifest.stages if item.stage_id == "agent_4")
    cp4 = next(check for check in stage.checks if check.criterion_id == "A4-CP4-001")

    assert cp4.status == CheckStatus.FAIL
    assert manifest.release_decision == GateDecision.HOLD


def test_invalid_host_is_rejected(known_bad_root: Path, runs_root: Path) -> None:
    app = create_app(known_bad_root, runs_root)
    with TestClient(app, base_url="http://evil.example") as client:
        assert client.get("/api/health").status_code == 400


def test_sse_replays_terminal_event(known_bad_root: Path, runs_root: Path) -> None:
    runner = EvaluationRunner(known_bad_root, runs_root)
    manifest = runner.run()
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        response = client.get(f"/api/runs/{manifest.run_id}/events")

    assert response.status_code == 200
    assert "run_completed" in response.text
    assert manifest.run_id in response.text


@pytest.mark.parametrize("tc_id", ["TC-INT-002", "ANY-CHANGED-ID"])
@pytest.mark.parametrize("excluded", [[], ["검증 제외"], ["unrelated wording"]])
def test_silent_invariant_omission_never_passes(known_good_root, tmp_path, monkeypatch, tc_id, excluded):
    import yaml
    import eval_harness.fixtures as module
    data = _yaml_block(module._read_text(known_good_root, "agent2"))
    data["test_cases"][0].update(tc_id=tc_id, unchanged_state_assertions=[], excluded_assertions=excluded)
    monkeypatch.setattr(module, "_read_text", lambda *args: "```yaml\n" + yaml.safe_dump(data) + "```")
    stage = evaluate_agent2(known_good_root, ArtifactStore(tmp_path / "run"))
    assert stage.status == StageStatus.REVIEW
    check = next(c for c in stage.checks if c.criterion_id == "A2-INVARIANT-001")
    assert check.status == CheckStatus.ABSTAIN
    assert check.actual["needs_applicability_review"] == [tc_id]


@pytest.mark.parametrize("first,second", [("passed", "failed"), ("passed", "skipped"), ("passed", "error")])
def test_report_counts_and_gate_use_original_rows(tmp_path, monkeypatch, first, second):
    import eval_harness.fixtures as module
    rows = [dict(tc_id="X", result=first, run_id="R"), dict(tc_id="Y", result=second, run_id="R")]
    values = {
        "agent3": rows,
        "agent4": dict(results=rows, source_execution_run_id="R", execution={"release_decision": "PASS"},
                       pytest_summary=dict(total=2, passed=2, failed=0, skipped=0, error=0)),
        "checkpoint4": dict(run_id="R", status="VALIDATED", errors=[], external_reporting_allowed=True),
    }
    monkeypatch.setattr(module, "_read_text", lambda root, key: json.dumps(values[key]))
    stage = evaluate_agent4(tmp_path, ArtifactStore(tmp_path / "run"))
    checks = {c.criterion_id: c.status for c in stage.checks}
    assert checks["A4-COUNT-001"] == CheckStatus.FAIL
    assert checks["A4-GATE-001"] == CheckStatus.FAIL
    assert checks["A4-ROW-001"] == CheckStatus.PASS
    assert stage.status == StageStatus.FAIL
