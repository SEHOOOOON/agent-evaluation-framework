from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from eval_harness.web.app import create_app


def wait_until_complete(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        manifest = response.json()
        if manifest["status"] in {"COMPLETED", "ERROR"}:
            return manifest
        time.sleep(0.02)
    raise AssertionError("background evaluation timed out")


def test_dashboard_and_safe_config(known_bad_root: Path, runs_root: Path) -> None:
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        page = client.get("/")
        config = client.get("/api/config").json()
        assert page.status_code == 200
        assert "QA Agent Evaluation Control Center" in page.text
        assert 'value="project1-audit"' in page.text
        assert 'id="sutExecuted"' in page.text
        assert 'role="tablist"' in page.text
        assert '.truth-strip' in client.get("/static/dashboard.css").text
        assert 'aria-current="step"' in client.get("/static/dashboard.js").text
        assert config["source_name"] == "project1"
        assert str(known_bad_root) not in str(config)
        assert config["local_only"] is True
        assert config["sut_executed"] is False
        assert config["stability_evaluated"] is False


def test_api_run_lifecycle_stage_artifact_and_report(
    known_bad_root: Path, runs_root: Path
) -> None:
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        response = client.post(
            "/api/runs",
            json={
                "suite": "project1-audit",
                "execution_mode": "fixture",
                "target_version": "test-version",
                "trials": 1,
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        manifest = wait_until_complete(client, run_id)

        assert manifest["release_decision"] == "HOLD"
        assert manifest["sut_executed"] is False
        assert manifest["stability_evaluated"] is False
        assert manifest["source_unchanged"] is True
        stage = client.get(f"/api/runs/{run_id}/stages/agent_1")
        artifact = client.get(f"/api/runs/{run_id}/artifacts/A1-NORMALIZED")
        report = client.get(f"/api/runs/{run_id}/report")
        assert stage.status_code == 200
        assert artifact.status_code == 200
        assert report.status_code == 200
        assert stage.json()["checkpoint_status"] == "HOLD"
        assert artifact.json()["yaml_confirmed_fact_ids"][-1] == "FACT-003"


def test_api_rejects_unsupported_or_path_like_inputs(
    known_bad_root: Path, runs_root: Path
) -> None:
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        assert client.post("/api/runs", json={"execution_mode": "live"}).status_code == 400
        assert client.post("/api/runs", json={"trials": 2}).status_code == 422
        assert client.post("/api/runs", json={"unexpected": True}).status_code == 422
        assert client.get("/api/runs/not-a-run").status_code == 404
        assert client.get("/api/runs/EVAL-invalid/artifacts/%2E%2E%2Fmanifest").status_code in {404, 422}


def test_latest_run_endpoint(known_bad_root: Path, runs_root: Path) -> None:
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        assert client.get("/api/runs/latest").json() == {"run": None}


def test_known_good_api_run_returns_pass(
    known_bad_root: Path, runs_root: Path
) -> None:
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        benchmarks = client.get("/api/benchmarks").json()
        assert [item["id"] for item in benchmarks] == ["project1-audit", "known-good"]

        response = client.post(
            "/api/runs",
            json={"suite": "known-good", "execution_mode": "fixture", "trials": 1},
        )
        assert response.status_code == 202
        manifest = wait_until_complete(client, response.json()["run_id"])
        assert manifest["release_decision"] == "PASS"
        assert manifest["summary"]["checks_failed"] == 0

