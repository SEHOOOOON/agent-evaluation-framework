"""repeat-a2: Agent 2 only, on a stored trial's Agent 1 result (test doubles only)."""
from __future__ import annotations

from pathlib import Path

import pytest

from eval_harness import a2_repeat
from eval_harness.v2_recorded import read, write

from test_full_flow import execute, flow_setup, worker_for  # noqa: F401  (fixture)


def tree(path: Path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def stored(flow_setup):
    base = worker_for()
    def worker(work, config):
        outcome = base(work, config)
        run = work / "p1/runs" / config["run_id"]
        write(run / "run_manifest.json", {"run_id": config["run_id"]})
        write(run / "checkpoint1.json", {"status": "PASS"})
        return outcome
    root, _ = execute(flow_setup, worker)
    return root, flow_setup[0]


def test_a1_files_keep_only_agent1_outputs(tmp_path):
    for name in ("agent1_change_analysis.json", "checkpoint1.json", "checkpoint1_attempt_1.json", "request.json",
                 "srs_snapshot.md", "run_manifest.json", "agent2_test_design.json", "checkpoint2.json",
                 "agent4_analysis.json", "final_report.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert sorted(a2_repeat._a1_files(tmp_path)) == [
        "agent1_change_analysis.json", "checkpoint1.json", "checkpoint1_attempt_1.json",
        "request.json", "run_manifest.json", "srs_snapshot.md"]
    (tmp_path / "run_manifest.json").unlink()
    with pytest.raises(ValueError, match="Agent 1"):
        a2_repeat._a1_files(tmp_path)


def test_preview_bounds_calls_without_judge_or_writes(stored):
    flow, target = stored
    before = tree(flow)
    info = a2_repeat.make_preview(flow, 1, target, 3)
    plan = info["plan"]
    assert plan["maximum_api_attempts"] == 12 and plan["judge_calls"] == 0
    assert "agent2_test_design.json" not in plan["agent1_files"]
    assert tree(flow) == before
    assert a2_repeat.make_preview(flow, 1, target, 2)["approval_sha256"] != info["approval_sha256"]
    with pytest.raises(ValueError, match="between"):
        a2_repeat.make_preview(flow, 1, target, 6)


def test_execute_reuses_agent1_and_copies_only_agent2_outputs(stored, tmp_path):
    flow, target = stored
    info = a2_repeat.make_preview(flow, 1, target, 2)
    with pytest.raises(ValueError, match="approval"):
        a2_repeat.execute(flow, 1, target, 2, "0" * 64, tmp_path / "out")
    seen = []
    def worker(work, config):
        run = work / "p1/runs" / config["run_id"]
        seen.append(sorted(p.name for p in run.iterdir()))
        assert config["mode"] == "AGENT2_REPEAT" and config["generation_limit"] == 4
        write(work / "call_ledger.json", {"attempts": [{"role": "A2", "usage": {"input_tokens": 1}}] * 2})
        write(run / "agent2_test_design.json", {"test_cases": []})
        write(run / "checkpoint2.json", {"status": "PASS"})
        return {"status": "COMPLETED", "stages": {"agent2": 0}}
    out = a2_repeat.execute(flow, 1, target, 2, info["approval_sha256"], tmp_path / "out", worker=worker)
    result = read(out / "result.json")
    assert result["status"] == "COMPLETED" and result["generation_attempts"] == 4
    assert all("agent2_test_design.json" not in names for names in seen)  # only A1 files were placed
    assert all(row["workspace_removed"] and row["checkpoint2_status"] == "PASS" for row in result["rows"])
    assert (out / "rep-2" / "agent2_test_design.json").is_file()


def test_over_budget_repetition_stops_the_batch(stored, tmp_path):
    flow, target = stored
    info = a2_repeat.make_preview(flow, 1, target, 2)
    def worker(work, config):
        write(work / "call_ledger.json", {"attempts": [{"role": "A2"}] * 5})
        return {"status": "COMPLETED", "stages": {"agent2": 0}}
    out = a2_repeat.execute(flow, 1, target, 2, info["approval_sha256"], tmp_path / "out", worker=worker)
    result = read(out / "result.json")
    assert result["status"] == "STOPPED" and len(result["rows"]) == 1
