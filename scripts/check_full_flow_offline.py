"""Historical stage-output replay helper; retained for reference. No API calls.

Only Agent1Analysis, Agent2TestDesign and Agent3AutomationPlan are replayed.
Current P1 also requests grounding-review responses, which this helper does not
provide. Do not use it as evidence of current end-to-end compatibility or model
performance. Current local checks are documented in PROJECT_HANDOFF.md.
Product tests may execute in the copied baseline; official assets stay intact.
"""
from pathlib import Path
import argparse
import subprocess
import sys
from eval_harness.full_flow import FlowSpec, make_preview, run_flow, clean_environment
from eval_harness.v2_recorded import read, write, child


def worker_main(config_file, recorded):
    from types import SimpleNamespace
    from eval_harness.flow_worker import execute_trial
    counter = [0]
    names = {"Agent1Analysis": "agent1_change_analysis.json",
             "Agent2TestDesign": "agent2_test_design.json",
             "Agent3AutomationPlan": "agent3_automation_plan.json"}
    def parse(**kwargs):
        counter[0] += 1
        schema = kwargs["text_format"]
        path = child(recorded, names[schema.__name__])
        if not path.is_file() and schema.__name__ == "Agent3AutomationPlan":
            matches = list(recorded.glob("agent3_candidates/*/agent3_automation_plan.json"))
            if len(matches) != 1:
                raise ValueError("Offline replay requires exactly one matching plan")
            path = matches[0]
        return SimpleNamespace(id=f"offline-answer-{counter[0]}", status="completed",
                               output_parsed=schema.model_validate(read(path)), usage=None)
    config_file = config_file.resolve()
    execute_trial(config_file.parent, read(config_file),
                  client=SimpleNamespace(responses=SimpleNamespace(parse=parse)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path)
    parser.add_argument("--recorded-run", required=True)
    parser.add_argument("--request", default="examples/change_request.success-fan-speed.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-config", type=Path)
    args = parser.parse_args()
    if args.worker_config:
        worker_main(args.worker_config, Path(args.recorded_run))
        return
    target = args.target.resolve()
    recorded = child(target / "runs", args.recorded_run)
    config_dir = args.output.resolve() / "offline-input"
    config_dir.mkdir(parents=True, exist_ok=True)
    write(config_dir / "oracle.json", {"case_id": "OFFLINE-CONTRACT", "status": "DRAFT",
          "expectations": [{"role": "E2E", "condition": "저장 응답으로 현재 실행 연결을 검증. 의미 정답은 미평가."}]})
    spec = FlowSpec(case_id="OFFLINE-CONTRACT", request=args.request, oracle="oracle.json",
                    generation_model="offline-recorded", judge_model="offline-no-judge",
                    judge_calls_total=0)
    def worker(work, config):
        config_file = work / "worker_config.json"
        write(config_file, config)
        with (work / "worker.log").open("w", encoding="utf-8") as log:
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker-config", str(config_file),
                 "--recorded-run", str(recorded)],
                env=clean_environment(), stdout=log, stderr=log, timeout=config["trial_timeout_seconds"])
        if proc.returncode != 0:
            return {"status": "ERROR", "reason": "OFFLINE_WORKER_ERROR", "stages": {}}
        return read(work / "worker_result.json")
    def no_judge(*args):
        raise AssertionError("Offline integration must not call a judge")
    info = make_preview(target, spec, config_dir)
    root = run_flow(target, spec, config_dir, args.output, info["approval_sha256"],
                    worker=worker, judge=no_judge)
    result = read(root / "result.json")
    result["integration_test"] = "RECORDED_MODEL_RESPONSES_WITH_CURRENT_P1"
    write(root / "result.json", result)
    print(root)
    print(result["status"], result["trials"][0]["roles"])


if __name__ == "__main__":
    main()
