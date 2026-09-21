from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from eval_harness.web.app import create_app


def test_behavior_design_and_self_test_api(
    known_bad_root: Path, runs_root: Path
) -> None:
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        design = client.get("/api/evaluation-design")
        self_test = client.get("/api/evaluator-self-test")

        assert design.status_code == 200
        assert self_test.status_code == 200
        assert design.json()["rule_count"] >= 30
        assert design.json()["case_count"] == 9
        assert design.json()["live_agent_evaluation_ready"] is False
        assert self_test.json()["agent_executed"] is False
        assert self_test.json()["decision"] == "HOLD"
        assert "Agent 성능 지표가 아닙니다" in self_test.json()["truth_note"]
