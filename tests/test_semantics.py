from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eval_harness.fixtures import _stage_state, now_iso
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