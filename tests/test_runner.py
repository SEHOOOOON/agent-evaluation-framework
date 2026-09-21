from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eval_harness.models import CheckStatus, GateDecision, StageStatus
from eval_harness.runner import EvaluationRunner


def file_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_known_bad_run_detects_expected_findings_and_does_not_modify_source(
    known_bad_root: Path, runs_root: Path
) -> None:
    before = file_hashes(known_bad_root)
    manifest = EvaluationRunner(known_bad_root, runs_root).run()
    after = file_hashes(known_bad_root)

    assert before == after
    assert manifest.status == "COMPLETED"
    assert manifest.release_decision == GateDecision.HOLD
    finding_ids = {
        finding.finding_id for stage in manifest.stages for finding in stage.findings
    }
    assert {
        "FIND-A1-001",
        "FIND-A2-001",
        "FIND-A2-002",
        "FIND-A3-001",
        "FIND-A4-001",
    }.issubset(finding_ids)


def test_stage_modes_and_checkpoint_results(known_bad_root: Path, runs_root: Path) -> None:
    manifest = EvaluationRunner(known_bad_root, runs_root).run()
    stages = {stage.stage_id: stage for stage in manifest.stages}

    assert stages["agent_1"].agent_mode == "생성형 산출물 Fixture 검사"
    assert stages["agent_1"].checkpoint_status == "HOLD"
    assert stages["agent_2"].status == StageStatus.FAIL
    assert stages["agent_3"].checkpoint_status == "HOLD"
    assert stages["agent_4"].status == StageStatus.PASS
    assert stages["agent_4"].checkpoint_status == "PASS"
    assert stages["pipeline_gate"].checkpoint_status == "HOLD"


def test_a4_classification_is_not_claimed_without_gold_label(
    known_bad_root: Path, runs_root: Path
) -> None:
    manifest = EvaluationRunner(known_bad_root, runs_root).run()
    a4 = next(stage for stage in manifest.stages if stage.stage_id == "agent_4")
    classification = next(check for check in a4.checks if check.criterion_id == "A4-CLASS-001")

    assert classification.status == CheckStatus.NOT_APPLICABLE
    assert classification.actual["evaluation"] == "N/A"


def test_json_is_ssot_and_static_report_is_exported(
    known_bad_root: Path, runs_root: Path
) -> None:
    runner = EvaluationRunner(known_bad_root, runs_root)
    manifest = runner.run()
    run_root = runs_root / manifest.run_id

    stored = json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))
    report = (run_root / "report.html").read_text(encoding="utf-8")
    events = (run_root / "events.jsonl").read_text(encoding="utf-8").splitlines()

    assert stored["run_id"] == manifest.run_id
    assert stored["release_decision"] == "HOLD"
    assert manifest.run_id in report
    assert "SUT EXECUTED" in report
    assert "SOURCE UNCHANGED" in report
    assert "Agent 1 · 요구사항 정제" in report
    assert any('"event": "run_completed"' in line for line in events)


def test_runner_rejects_unsupported_mode_and_trials(
    known_bad_root: Path, runs_root: Path
) -> None:
    runner = EvaluationRunner(known_bad_root, runs_root)
    for kwargs in ({"execution_mode": "live"}, {"trials": 2}, {"suite": "holdout"}):
        try:
            runner.create_manifest(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"지원하지 않는 설정을 허용했습니다: {kwargs}")


def test_known_good_run_passes_without_modifying_source(
    known_good_root: Path, runs_root: Path
) -> None:
    before = file_hashes(known_good_root)
    manifest = EvaluationRunner(known_good_root, runs_root).run(suite="known-good")
    after = file_hashes(known_good_root)

    assert before == after
    assert manifest.status == "COMPLETED"
    assert manifest.release_decision == GateDecision.PASS
    assert manifest.summary.checks_failed == 0
    assert manifest.source_unchanged is True

