from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..behavior import evaluation_design, run_evaluator_self_test
from ..models import RunRequest
from ..runner import (
    DEFAULT_KNOWN_GOOD_ROOT,
    DEFAULT_PROJECT1_ROOT,
    DEFAULT_RUNS_ROOT,
    BackgroundEvaluationService,
    EvaluationRunner,
)
from ..storage import ArtifactStore
from .recorded import list_reports, read_report
from .flows import flow_list, flow_summary


WEB_ROOT = Path(__file__).resolve().parent
TERMINAL_EVENTS = {"run_completed", "run_failed"}


def create_app(
    project1_root: Path = DEFAULT_PROJECT1_ROOT,
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> FastAPI:
    runner = EvaluationRunner(project1_root, runs_root)
    known_good_runner = EvaluationRunner(DEFAULT_KNOWN_GOOD_ROOT, runs_root)
    services = {
        "project1-audit": BackgroundEvaluationService(runner),
        "known-good": BackgroundEvaluationService(known_good_runner),
    }
    app = FastAPI(
        title="Project1 Agent Quality Evaluation",
        version="0.3.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.state.runner = runner
    app.state.services = services
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return (WEB_ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "project1-agent-quality-evaluation",
            "version": "0.3.0",
        }

    @app.get("/api/evaluation-design")
    def get_evaluation_design() -> dict[str, object]:
        return evaluation_design()

    @app.get("/api/recorded-reviews")
    def recorded_reviews() -> dict[str, object]:
        return list_reports(runner.runs_root)

    @app.get("/api/full-flows")
    def full_flows():
        return flow_list(runner.runs_root)

    @app.get("/api/full-flows/{flow_id}")
    def full_flow(flow_id: str):
        try:
            return flow_summary(runner.runs_root, flow_id)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise HTTPException(status_code=422, detail="전체 실행 기록을 읽을 수 없습니다.") from None

    @app.get("/api/recorded-reviews/{report_id}")
    def recorded_review(report_id: str) -> dict[str, object]:
        try:
            return read_report(runner.runs_root, report_id)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise HTTPException(status_code=422, detail="저장 평가를 읽을 수 없습니다. 파일 형식·연결·집계를 확인하세요.") from None

    @app.get("/api/evaluator-self-test")
    def evaluator_self_test() -> dict[str, object]:
        return run_evaluator_self_test().model_dump(mode="json")

    @app.get("/api/config")
    def config() -> dict[str, object]:
        return {
            "source_name": runner.source_root.name,
            "source_profiles": ["project1-audit", "known-good"],
            "execution_modes": ["fixture"],
            "trial_options": [1],
            "sut_executed": False,
            "stability_evaluated": False,
            "agent_behavior_evaluated": False,
            "local_only": True,
            "ssot": "evaluation-runs/<run_id>/manifest.json",
            "current_runner_role": "evaluator_self_test_and_artifact_audit",
        }

    @app.get("/api/benchmarks")
    def benchmarks() -> list[dict[str, object]]:
        return [
            {
                "id": "project1-audit",
                "name": "Evaluator Self-test · 현재 Snapshot",
                "mode": "fixture",
                "trial_count": 1,
                "description": (
                    "Project1 저장 산출물에서 규칙 위반을 탐지하는지 확인합니다. "
                    "실제 Agent 반복 성능 측정은 아닙니다."
                ),
            },
            {
                "id": "known-good",
                "name": "Evaluator Positive Control · 정상 기준본",
                "mode": "fixture",
                "trial_count": 1,
                "description": (
                    "정상 기준본을 오탐 없이 PASS하는지 확인하는 평가기 Positive Control입니다."
                ),
            },
        ]

    @app.post("/api/runs", status_code=202)
    def start_run(request: RunRequest) -> dict[str, str]:
        try:
            service = services.get(request.suite)
            if service is None:
                raise ValueError("지원하지 않는 Benchmark입니다.")
            manifest = service.start(
                suite=request.suite,
                execution_mode=request.execution_mode,
                target_version=request.target_version,
                trials=request.trials,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run_id": manifest.run_id, "status": manifest.status}

    @app.get("/api/runs/latest")
    def latest_run() -> dict[str, object]:
        manifest = runner.latest()
        return {"run": manifest.model_dump(mode="json") if manifest else None}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        try:
            return runner.load(run_id).model_dump(mode="json")
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.") from None

    @app.get("/api/runs/{run_id}/stages/{stage_id}")
    def get_stage(run_id: str, stage_id: str) -> dict[str, object]:
        try:
            manifest = runner.load(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.") from None
        stage = next((item for item in manifest.stages if item.stage_id == stage_id), None)
        if not stage:
            raise HTTPException(status_code=404, detail="Stage 결과가 아직 없습니다.")
        return stage.model_dump(mode="json")

    @app.get("/api/runs/{run_id}/artifacts/{artifact_id}")
    def get_artifact(run_id: str, artifact_id: str) -> FileResponse:
        try:
            run_root = runner._run_root(run_id)
            target, media_type = ArtifactStore.resolve_artifact(run_root, artifact_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Artifact를 찾을 수 없습니다.") from None
        return FileResponse(
            target,
            media_type=media_type,
            filename=target.name,
            content_disposition_type="inline",
        )

    @app.get("/api/runs/{run_id}/report")
    def get_report(run_id: str) -> FileResponse:
        try:
            target = runner._run_root(run_id) / "report.html"
            if not target.is_file():
                raise FileNotFoundError
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="리포트가 아직 없습니다.") from None
        return FileResponse(
            target,
            media_type="text/html",
            filename=f"{run_id}-report.html",
            content_disposition_type="inline",
        )

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request) -> StreamingResponse:
        try:
            run_root = runner._run_root(run_id)
            if not (run_root / "manifest.json").is_file():
                raise FileNotFoundError
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Run을 찾을 수 없습니다.") from None

        async def stream():
            delivered = 0
            terminal_delivered = False
            while not terminal_delivered:
                if await request.is_disconnected():
                    break
                events_path = run_root / "events.jsonl"
                lines = (
                    events_path.read_text(encoding="utf-8").splitlines()
                    if events_path.is_file()
                    else []
                )
                while delivered < len(lines):
                    line = lines[delivered]
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        break
                    delivered += 1
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    if data.get("event") in TERMINAL_EVENTS:
                        terminal_delivered = True
                        break
                if not terminal_delivered:
                    await asyncio.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
