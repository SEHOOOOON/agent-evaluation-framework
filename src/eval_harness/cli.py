from __future__ import annotations

import argparse
import json
import subprocess
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .behavior import run_evaluator_self_test
from .runner import (
    DEFAULT_KNOWN_GOOD_ROOT,
    DEFAULT_PROJECT1_ROOT,
    DEFAULT_RUNS_ROOT,
    EvaluationRunner,
)
from .web.app import create_app


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m eval_harness")
    commands = root.add_subparsers(dest="command", required=True)

    flow = commands.add_parser("full-flow", help="전체 A1~4 실행·수집·평가. 기본은 읽기 전용 Preview.")
    flow.add_argument("--target", type=Path, required=True)
    flow.add_argument("--spec", type=Path, required=True)
    flow.add_argument("--output", type=Path, default=DEFAULT_RUNS_ROOT)
    flow.add_argument("--execute", action="store_true")
    flow.add_argument("--approval-sha256")

    run = commands.add_parser("run", help="저장 산출물 기반 Evaluator 검증을 실행합니다.")
    run.add_argument("--project1-root", type=Path, default=DEFAULT_PROJECT1_ROOT)
    run.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    run.add_argument(
        "--suite",
        default="project1-audit",
        choices=["project1-audit", "known-good"],
    )
    run.add_argument("--mode", default="fixture", choices=["fixture"])
    run.add_argument("--target-version", default="project1-current")
    run.add_argument("--trials", type=int, default=1, choices=[1])

    commands.add_parser(
        "behavior-self-test",
        help="합성 Recorded Trial로 행동 품질 지표 집계기를 검증합니다.",
    )

    recorded = commands.add_parser("run-v2", help="V2 저장 Run을 읽기 전용으로 독립 평가합니다. 새 모델 호출 없음.")
    recorded.add_argument("--target", type=Path, required=True)
    recorded.add_argument("--dataset", type=Path)
    recorded.add_argument("--output", type=Path)

    repeat = commands.add_parser("repeat-self-test", help="실제 API 없이 반복 평가기의 호출 관리·판정·보고를 검증합니다.")
    repeat.add_argument("--trials", type=int, default=5)
    repeat.add_argument("--scenario", choices=["normal", "value-drift", "same-wrong", "missing", "transport-error", "duplicate"], default="normal")
    repeat.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2]/"evaluation-runs")

    judge = commands.add_parser("judge-openai", help="별도 OpenAI 의미 평가. 기본은 전송 미리보기이며 호출하지 않습니다.")
    judge.add_argument("--packet", type=Path, required=True)
    from .openai_judge import DEFAULT_JUDGE_MODEL
    judge.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    judge.add_argument("--role-rubric", action="store_true", help="담당 Agent의 전체 세부 기준을 적용합니다. 근거 누락은 Preview에 표시합니다.")
    judge.add_argument("--score-rubric", action="store_true", help="기존 26항목의 0~5점 기준을 적용합니다. 과거 결과를 환산하지 않습니다.")
    judge.add_argument("--execute", action="store_true")
    judge.add_argument("--approval-sha256")
    judge.add_argument("--output",type=Path,default=Path(__file__).resolve().parents[2]/"evaluation-runs")

    oracle = commands.add_parser("range-oracle", help="범위 변경 요청의 규칙 기반 정답지로 A2 TC 설계를 대조합니다. API 호출 없음.")
    oracle.add_argument("--key", type=Path, required=True)
    oracle.add_argument("--design", type=Path, required=True, help="agent2_test_design.json")

    dashboard = commands.add_parser("dashboard", help="로컬 평가 대시보드를 실행합니다.")
    dashboard.add_argument("--project1-root", type=Path, default=DEFAULT_PROJECT1_ROOT)
    dashboard.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    # 8765 is the Project 1 UI default; keep the two local servers apart.
    dashboard.add_argument("--port", type=int, default=8766)
    dashboard.add_argument("--no-browser", action="store_true")
    return root


def evaluation_exit_code(status: str, decision: str | None) -> int:
    if status != "COMPLETED":
        return 4
    return {"PASS": 0, "HOLD": 2, "HUMAN_REVIEW": 3}.get(decision or "", 4)


def source_mismatch(cwd: Path | None = None) -> str | None:
    """Refuse a stale installed copy when run from a checkout that has newer source."""
    loaded = Path(__file__).resolve().parent
    source = (Path(cwd or Path.cwd()) / "src" / "eval_harness").resolve()
    if not (source / "__init__.py").is_file() or source == loaded:
        return None
    return (f"저장소 소스({source}) 대신 다른 위치의 eval_harness({loaded})가 실행되었습니다. "
            "설치본이 수정 전 코드일 수 있습니다. 저장소 루트에서 PYTHONPATH=src로 실행하거나 "
            "`python -m pip install -e .`로 소스 연결 설치를 하세요.")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    mismatch = source_mismatch()
    if mismatch:
        print(json.dumps({"status": "ERROR", "reason": "STALE_INSTALLED_COPY", "detail": mismatch},
                         ensure_ascii=False))
        return 4
    if args.command == "range-oracle":
        from .range_oracle import grade, load_key
        try:
            if args.design.stat().st_size > 2_000_000:
                raise ValueError("Design too large")
            result = grade(load_key(args.key), json.loads(args.design.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(json.dumps({"status": "ERROR", "reason": type(exc).__name__}, ensure_ascii=False))
            return 4
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return {"PASS": 0, "FAIL": 2, "REVIEW": 3}[result["verdict"]]
    if args.command == "full-flow":
        from .full_flow import FlowSpec, make_preview, run_flow
        try:
            if args.spec.stat().st_size > 100_000:
                raise ValueError("Flow specification too large")
            spec = FlowSpec.model_validate_json(args.spec.read_text(encoding="utf-8"))
            info = make_preview(args.target, spec, args.spec.resolve().parent)
            if not args.execute:
                print(json.dumps(info, ensure_ascii=False, indent=2))
                return 0
            if not args.approval_sha256:
                raise ValueError("Exact preview approval required")
            out = run_flow(args.target, spec, args.spec.resolve().parent, args.output, args.approval_sha256)
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
            print(json.dumps({"output": str(out), "status": result["status"],
                              "quality_assessment": result.get("quality_assessment", "REVIEW_REQUIRED")}, ensure_ascii=False))
            return 3 if result["status"] == "COMPLETED" else 4
        except (OSError, ValueError, ImportError, subprocess.SubprocessError) as exc:
            print("Full flow blocked:", type(exc).__name__)
            return 4
    if args.command == "judge-openai":
        from .openai_judge import JudgePacket,preview,evaluate
        try:
            if args.packet.stat().st_size>200_000: raise ValueError("Packet too large")
            packet=JudgePacket.model_validate_json(args.packet.read_text(encoding="utf-8"))
            if args.role_rubric:
                from .detailed_rubric import apply_role_profile
                packet=apply_role_profile(packet)
            if args.score_rubric:
                from .detailed_rubric import SCORE_VERSION
                packet=JudgePacket.model_validate({**packet.model_dump(), "scoring_profile": SCORE_VERSION})
            if not args.execute:
                print(json.dumps(preview(packet,args.model),ensure_ascii=False,indent=2))
                return 0
            if not args.approval_sha256: raise ValueError("Explicit preview approval is required")
            out=evaluate(packet,args.model,args.approval_sha256,args.output)
            result=json.loads((out/"result.json").read_text(encoding="utf-8"))
            print(json.dumps({"output":str(out),"status":result["status"],"assessment":result["assessment"]},ensure_ascii=False))
            return 4 if result["status"]=="ERROR" else 2 if result["assessment"]=="ISSUES_FOUND" else 3
        except (OSError,ValueError,ImportError) as exc:
            print("Judge blocked:",type(exc).__name__)
            return 4
    if args.command == "repeat-self-test":
        from .repeatability import run_self_test
        try:
            out=run_self_test(args.output,args.scenario,args.trials)
        except (OSError,ValueError) as exc:
            print("Repeat self-test blocked:",type(exc).__name__)
            return 4
        result=json.loads((out/"result.json").read_text(encoding="utf-8"))
        print(json.dumps({"output":str(out),"mode":result["mode"],"api_requests":0,
                          "assessment":result["assessment"],"pass_count":result["pass_count"],
                          "fail_count":result["fail_count"],"error_count":result["error_count"]},ensure_ascii=False))
        # The command executes an evaluator self-test, not a target quality gate.
        return 0
    if args.command == "run-v2":
        from .v2_recorded import main as recorded_main
        arguments = ["--target", str(args.target)]
        if args.dataset:
            arguments += ["--dataset", str(args.dataset)]
        if args.output:
            arguments += ["--output", str(args.output)]
        return recorded_main(arguments)
    if args.command == "behavior-self-test":
        summary = run_evaluator_self_test()
        print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2))
        # HOLD는 합성 오류를 의도대로 탐지한 Self-test 결과이므로 명령 자체는 성공입니다.
        return 0

    if args.command == "run":
        source_root = (
            DEFAULT_KNOWN_GOOD_ROOT
            if args.suite == "known-good" and args.project1_root == DEFAULT_PROJECT1_ROOT
            else args.project1_root
        )
        runner = EvaluationRunner(source_root, args.runs_root)
        manifest = runner.run(
            suite=args.suite,
            execution_mode=args.mode,
            target_version=args.target_version,
            trials=args.trials,
        )
        result = {
            "run_id": manifest.run_id,
            "status": manifest.status,
            "evaluation_release_decision": (
                manifest.release_decision.value if manifest.release_decision else None
            ),
            "summary": manifest.summary.model_dump(mode="json"),
            "manifest": str(
                (runner.runs_root / manifest.run_id / "manifest.json").resolve()
            ),
            "report": str((runner.runs_root / manifest.run_id / "report.html").resolve()),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return evaluation_exit_code(
            manifest.status,
            manifest.release_decision.value if manifest.release_decision else None,
        )

    if not 1024 <= args.port <= 65535:
        raise SystemExit("port는 1024~65535 범위여야 합니다.")
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app = create_app(args.project1_root, args.runs_root)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0
