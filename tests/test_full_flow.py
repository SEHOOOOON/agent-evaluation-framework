from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from eval_harness.full_flow import FlowSpec, make_preview, run_flow, clean_environment
from eval_harness.flow_worker import MeteredResponses, generation_client
from eval_harness.flow_worker import bind_model_clients
from eval_harness.v2_recorded import write, read, digest


@pytest.fixture
def flow_setup(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    for name, text in {
        "src/qa_pipeline_orchestrator.py": "# integration test target",
        "docs/01_PRODUCT_SRS.md": "설정 온도는 18~30도",
        "product_baseline/virtual-controller.html": "<html>local test</html>",
        "product_baseline/pytest.ini": "[pytest]",
        "product_baseline/tests/conftest.py": "# harness",
        "product_baseline/tests/test_controller.py": "# baseline",
        "approved_assets/registry.json": "{}",
        "request.json": '{"description":"18~30도 범위를 확인한다."}',
    }.items():
        p = target / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    def git(*args):
        subprocess.run(["git", "-C", str(target), *args], check=True, capture_output=True)
    git("init")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "test fixture")
    config = tmp_path / "config"
    config.mkdir()
    write(config / "oracle.json", dict(case_id="TEST", status="DRAFT",
          expectations=[{"role": "A1", "condition": "18~30도 보존"}]))
    spec = FlowSpec(case_id="TEST", request="request.json", oracle="oracle.json",
                    generation_model="test-model", judge_model="test-judge")
    return target, config, spec, tmp_path / "output"


def fake_judge(packet, model, approval, output):
    out = output / "JUDGE-test"
    write(out / "result.json", dict(mode="TEST_DOUBLE", status="COMPLETED",
          assessment="REVIEW", sdk_parse_attempts=1, rubric_count=len(packet.rubric),
          judgments=[], api_request_attempts=0))
    return out


def worker_for(branch="normal", received=None):
    def worker(work, config):
        if received is not None:
            received.append((work, config))
        run = work / "p1/runs" / config["run_id"]
        write(run / "request.json", read(work / "p1/request.json"))
        (run / "srs_snapshot.md").write_text("18~30도", encoding="utf-8")
        a1 = dict(information_gaps=[], user_questions=[])
        write(run / "agent1_change_analysis.json", a1)
        stages = {"agent1": 0}
        calls = ["A1"] if branch == "blocked" else ["A1", "A2"] if branch == "reuse" else ["A1", "A2", "A3"]
        write(work / "call_ledger.json", {"attempts": [
            {"role": role, "status": "RECEIVED",
             "response_id": "duplicate" if branch == "duplicate" else f"{config['run_id']}-{i}"}
            for i, role in enumerate(calls)
        ]})
        if branch == "blocked":
            return dict(status="BLOCKED", reason="CP1_BLOCK", stages={"agent1": 2})
        if branch == "transport":
            return dict(status="ERROR", reason="GENERATION_RESPONSE_ERROR", stages=stages)
        write(run / "agent2_test_design.json", {"test_cases": []})
        write(run / "agent3_selection.json", {"status": "NOT_REQUIRED" if branch == "reuse" else "SELECTED"})
        write(run / "agent3_run_summary.json", {"status": "PASS"})
        stages.update(agent2=0, execute=0, agent4=0)
        if branch != "reuse":
            stages["agent3"] = 0
            for n in range(2 if branch == "multi" else 1):
                write(run / f"agent3_candidates/TC-{n}/agent3_automation_plan.json", {"tc_id": f"TC-{n}"})
                write(run / f"agent3_candidates/TC-{n}/ui_observation.json", {"selectors": ["#apply"]})
        status = "ASSERTION_FAILED" if branch == "product_fail" else "PASSED"
        bundle = {"candidate_results": [], "regression_results": [{"tc_id": "TC1", "status": status}]}
        write(run / "validation_execution.json", bundle)
        write(run / "agent4_analysis.json", {"validation_execution_sha256": digest(run / "validation_execution.json")})
        write(run / "final_report.json", dict(total_results=1, status_counts={status: 1},
              analysis_sha256=digest(run / "agent4_analysis.json"), 최종_확인_사항=[], 제외된_정보_부족=[]))
        if branch == "asset_change":
            (work / "p1/docs/01_PRODUCT_SRS.md").write_text("changed", encoding="utf-8")
        return dict(status="COMPLETED", stages=stages)
    return worker


def execute(setup, worker, spec=None, judge=fake_judge):
    target, config, default, output = setup
    spec = spec or default
    preview = make_preview(target, spec, config)
    root = run_flow(target, spec, config, output, preview["approval_sha256"], worker=worker, judge=judge)
    return root, read(root / "result.json")


def test_preview_no_transmission_and_bound_models(flow_setup):
    target, config, spec, output = flow_setup
    info = make_preview(target, spec, config)
    assert info["plan"]["maximum_api_attempts"] == 14
    assert info["plan"]["external_publish"] is False
    assert not output.exists()
    changed = spec.model_copy(update={"judge_model": "other"})
    assert make_preview(target, changed, config)["approval_sha256"] != info["approval_sha256"]
    with pytest.raises(ValueError, match="approval"):
        run_flow(target, changed, config, output, info["approval_sha256"],
                 worker=worker_for(), judge=fake_judge)


def test_five_fresh_full_trials_and_each_role_judge(flow_setup):
    recorded = []
    spec = flow_setup[2].model_copy(update={"trials": 5, "generation_calls_total": 15, "judge_calls_total": 20})
    root, result = execute(flow_setup, worker_for(received=recorded), spec)
    assert result["status"] == "COMPLETED"
    assert result["mode"] == "TEST_DOUBLE" and result["api_request_attempts"] == 0
    assert result["generation_attempts"] == 15 and result["judge_attempts"] == 20
    assert result["role_reached"] == dict.fromkeys(["A1", "A2", "A3", "A4"], 5)
    assert len({str(work) for work, _ in recorded}) == 5
    assert len({t["run_id"] for t in result["trials"]}) == 5
    assert result["quality_assessment"] == "REVIEW_REQUIRED"
    for role, count in (("A1", 6), ("A2", 10), ("A3", 6), ("A4", 6)):
        assert result["trials"][0]["judges"][role]["rubric_count"] == count
    assert "independent_oracle" in read(root / "trial-1/A2/packet.json")["sources"]["context"]
    # Independent oracle is never copied into the generator workspace.
    assert not (recorded[0][0] / "p1/oracle.json").exists()


@pytest.mark.parametrize("branch", ["blocked", "reuse", "multi", "product_fail"])
def test_required_branches(flow_setup, branch):
    root, result = execute(flow_setup, worker_for(branch))
    row = result["trials"][0]
    assert result["status"] == "COMPLETED"
    if branch == "blocked":
        assert row["roles"]["A1"] == "BLOCKED"
        assert row["roles"]["A2"] == row["roles"]["A4"] == "NOT_EXECUTED"
        assert set(row["judges"]) == {"A1"}
    elif branch == "reuse":
        assert row["new_plan_not_required"] is True
        assert row["roles"]["A3"] == row["roles"]["A4"] == "COMPLETED"
        assert row["generation_attempts"] == 2
    elif branch == "multi":
        assert len(read(root / "trial-1/A3/packet.json")["sources"]["artifact"]) == 2
    else:
        assert row["assessment"] == "REVIEW"
        assert next(c for c in row["checks"] if c["rule"] == "A4.COUNTS")["status"] == "PASS"


@pytest.mark.parametrize("branch", ["transport", "asset_change", "duplicate"])
def test_safety_stop_keeps_unexecuted_trials(flow_setup, branch):
    spec = flow_setup[2].model_copy(update={"trials": 5, "generation_calls_total": 30})
    _, result = execute(flow_setup, worker_for(branch), spec)
    assert result["status"] == "STOPPED"
    assert len(result["trials"]) == 5
    assert all(r["status"] == "NOT_EXECUTED" for r in result["trials"][1:])


def test_generation_and_judge_budgets(flow_setup):
    spec = flow_setup[2].model_copy(update={"trials": 5, "generation_calls_total": 3, "judge_calls_total": 1})
    _, result = execute(flow_setup, worker_for(), spec)
    assert result["generation_attempts"] == 3
    assert result["judge_attempts"] == 1
    assert result["trials"][0]["judges"]["A4"]["status"] == "NOT_EVALUATED"
    assert result["trials"][1]["status"] == "NOT_EXECUTED"


def test_judge_error_not_agent_failure(flow_setup):
    def broken(*args):
        raise RuntimeError("test error")
    _, result = execute(flow_setup, worker_for(), judge=broken)
    assert result["trials"][0]["judges"]["A1"]["status"] == "ERROR"
    assert result["trials"][0]["assessment"] == "REVIEW"
    assert result["judge_attempts"] == 0


def test_changed_inputs_stop_next_trial(flow_setup):
    target = flow_setup[0]
    base = worker_for()
    def worker(work, config):
        outcome = base(work, config)
        (target / "docs/01_PRODUCT_SRS.md").write_text("changed", encoding="utf-8")
        return outcome
    spec = flow_setup[2].model_copy(update={"trials": 5})
    _, result = execute(flow_setup, worker, spec)
    assert result["stop_reason"] == "ASSET_PROTECTION_FAILED"
    assert result["trials"][1]["status"] == "NOT_EXECUTED"


def test_external_reporting_environment_removed(monkeypatch):
    monkeypatch.setenv("NOTION_API_KEY", "test-not-a-secret")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    env = clean_environment()
    assert not any(k.startswith(("NOTION_", "SLACK_", "OPENAI_")) for k in env)


class Payload(BaseModel):
    text: str


def test_three_call_pilot_rejects_fourth_generation(tmp_path):
    calls = []
    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=f"three-limit-{len(calls)}", status="completed",
                               output_parsed=Payload(text="original"), usage=None)
    ledger = {"attempts": [], "fatal": None}
    meter = MeteredResponses(SimpleNamespace(responses=SimpleNamespace(parse=parse)),
                             "A2", tmp_path, ledger, 3, set())
    kwargs = dict(model="test", input=[], store=False, text_format=Payload)
    for _ in range(3):
        meter.parse(**kwargs)
    with pytest.raises(RuntimeError, match="budget"):
        meter.parse(**kwargs)
    assert len(calls) == len(ledger["attempts"]) == 3
    assert ledger["fatal"] == "GENERATION_BUDGET_EXHAUSTED"


def test_three_call_pilot_configuration():
    root = Path(__file__).resolve().parents[1]
    spec = FlowSpec.model_validate_json(
        (root / "benchmarks/v2/full_flow.three-call-pilot.json").read_text(encoding="utf-8"))
    assert spec.trials == 1
    assert spec.generation_calls_total + spec.judge_calls_total == 3
    assert spec.generation_calls_per_trial == 3


def test_meter_budget_ids_errors_and_exact_input(tmp_path):
    calls = []
    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="response-1", status="completed", output_parsed=Payload(text="original"), usage=None)
    ledger = {"attempts": [], "fatal": None}
    meter = MeteredResponses(SimpleNamespace(responses=SimpleNamespace(parse=parse)), "A2",
                             tmp_path, ledger, 1, set())
    kwargs = dict(model="test", input=[{"role": "user", "content": "18도"}], store=False, text_format=Payload)
    assert meter.parse(**kwargs).output_parsed.text == "original"
    with pytest.raises(RuntimeError):
        meter.parse(**kwargs)
    assert len(calls) == 1 and len(ledger["attempts"]) == 1
    assert read(tmp_path / "calls/001/request.json")["payload"]["input"] == kwargs["input"]
    assert ledger["fatal"] == "GENERATION_BUDGET_EXHAUSTED"


def test_missing_reviewer_contract_stops_before_client_use(tmp_path):
    calls = []
    class Agent:
        def __init__(self, *, model=None, client=None): calls.append(client)
    execution = SimpleNamespace(OpenAIAgent1=Agent, OpenAIAgent2=Agent, OpenAIAgent3=Agent)
    with pytest.raises(ValueError, match="Unsupported P1"):
        bind_model_clients(execution, object(), tmp_path, {}, {})
    assert calls == []
    assert execution.OpenAIAgent1 is Agent


def test_actual_p1_generation_and_review_share_one_budget(tmp_path, monkeypatch):
    import os
    import importlib
    target = os.environ.get("QA_P1_TEST_ROOT")
    if not target:
        pytest.skip("Set QA_P1_TEST_ROOT to the fixed P1 working tree for cross-repository validation")
    monkeypatch.syspath_prepend(str(Path(target) / "src"))
    execution = importlib.import_module("qa_pipeline_execution")
    for name in ("OpenAIAgent1", "OpenAIAgent2", "OpenAIAgent3", "OpenAIGroundingReviewer"):
        monkeypatch.setattr(execution, name, getattr(execution, name))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = []
    def parse(**kwargs):
        calls.append(kwargs)
        schema = kwargs["text_format"]
        value = {"items": []} if schema.__name__ == "GroundingReview" else {"text": "offline"}
        return SimpleNamespace(id=f"offline-{len(calls)}", status="completed",
                               output_parsed=schema.model_validate(value), usage=None)
    ledger = {"attempts": [], "fatal": None}
    config = dict(seen_response_ids=[], generation_limit=6, generation_max_output_tokens=12000)
    bind_model_clients(execution, SimpleNamespace(responses=SimpleNamespace(parse=parse)), tmp_path, ledger, config)
    for stage in (1, 2, 3):
        agent = getattr(execution, f"OpenAIAgent{stage}")(model="offline")
        agent.client.responses.parse(model="offline", store=False, input=[], text_format=Payload)
        execution.OpenAIGroundingReviewer(model="offline").review(
            {"stage": f"AGENT{stage}", "items": [], "source_documents": []})
    assert [(r["role"], r["purpose"]) for r in ledger["attempts"]] == [
        (role, purpose) for role in ("A1", "A2", "A3") for purpose in ("generation", "grounding")]
    with pytest.raises(RuntimeError, match="budget"):
        execution.OpenAIGroundingReviewer(model="offline").review({"stage": "AGENT1"})
    assert len(calls) == len(ledger["attempts"]) == 6
    assert ledger["fatal"] == "GENERATION_BUDGET_EXHAUSTED"


def test_meter_duplicate_and_timeout_no_retry(tmp_path):
    for duplicate in (True, False):
        folder = tmp_path / str(duplicate)
        folder.mkdir()
        def parse(**kwargs):
            if not duplicate:
                raise TimeoutError()
            return SimpleNamespace(id="old", status="completed", output_parsed=Payload(text="x"))
        ledger = {"attempts": [], "fatal": None}
        meter = MeteredResponses(SimpleNamespace(responses=SimpleNamespace(parse=parse)), "A1",
                                 folder, ledger, 3, {"old"})
        with pytest.raises(RuntimeError):
            meter.parse(model="test", input=[], store=False, text_format=Payload)
        with pytest.raises(RuntimeError):
            meter.parse(model="test", input=[], store=False, text_format=Payload)
        assert len(ledger["attempts"]) == 1 and ledger["attempts"][0]["status"] == "ERROR"


@pytest.mark.parametrize("http_status", [200, 429, 500])
def test_generation_real_sdk_one_http_attempt(tmp_path, http_status):
    import httpx
    requests = []
    def handler(request):
        requests.append(request)
        if http_status != 200:
            return httpx.Response(http_status, json={"error": {"message": "synthetic"}})
        return httpx.Response(200, json={
            "id": "response-sdk", "object": "response", "created_at": 1,
            "status": "completed", "model": "test",
            "output": [{"id": "msg-test", "type": "message", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": '{"text":"18도"}', "annotations": []}]}],
        })
    ledger = {"attempts": [], "fatal": None}
    with generation_client("local-test-only", tmp_path, ledger,
                           transport=httpx.MockTransport(handler)) as client:
        meter = MeteredResponses(client, "A1", tmp_path, ledger, 2, set())
        kwargs = dict(model="test", input=[{"role": "user", "content": "18도"}], store=False, text_format=Payload)
        if http_status == 200:
            assert meter.parse(**kwargs).output_parsed.text == "18도"
        else:
            with pytest.raises(RuntimeError):
                meter.parse(**kwargs)
    assert len(requests) == len(ledger["http_requests"]) == 1
    assert json.loads(requests[0].content)["max_output_tokens"] == 12000


def test_flow_api_hides_workspace_and_separates_test_mode(flow_setup, known_bad_root):
    from fastapi.testclient import TestClient
    from eval_harness.web.app import create_app
    root, result = execute(flow_setup, worker_for("reuse"))
    with TestClient(create_app(known_bad_root, root.parent)) as client:
        assert client.get("/api/full-flows").json() == [{"id": root.name}]
        response = client.get("/api/full-flows/" + root.name)
        assert response.status_code == 200
        assert response.json()["mode"] == "TEST_DOUBLE"
        assert "workspace" not in response.text
        assert response.json()["trials"][0]["roles"]["A3"] == "COMPLETED"
        assert client.get("/api/full-flows/invalid").status_code == 422
        assert client.post("/api/full-flows/" + root.name).status_code == 405


def test_evidence_archive_preserves_original_bytes(flow_setup):
    import zipfile
    root, result = execute(flow_setup, worker_for())
    row = result["trials"][0]
    archive = root / row["evidence_archive"]
    assert digest(archive) == row["evidence_archive_sha256"]
    with zipfile.ZipFile(archive) as z:
        name = "p1/runs/" + row["run_id"] + "/final_report.json"
        assert z.read(name) == (Path(row["workspace"]) / name).read_bytes()


def test_compacted_evidence_is_lossless():
    from eval_harness.full_flow import compact_evidence, request_evidence
    repeated = {"text": "동일 원문" * 200}
    source = {"context": repeated, "trace": [repeated, {"other": repeated["text"]}]}
    compact = compact_evidence(source)
    def expand(value):
        if isinstance(value, dict) and set(value) == {"evidence_reference"}:
            target = compact
            for key in value["evidence_reference"]:
                target = target[int(key)] if isinstance(target, list) else target[key]
            return expand(target)
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value
    assert expand(compact) == source
    raw = json.dumps(repeated)
    projected = request_evidence({"payload": {"input": [{"role": "user", "content": raw}]}})
    assert projected["payload"]["input"][0]["content"]["parsed_json"] == repeated


def test_added_official_asset_blocks_further_trials(flow_setup):
    base = worker_for()
    def worker(work, config):
        outcome = base(work, config)
        write(work / "p1/approved_assets/unauthorized.json", {"new": True})
        return outcome
    spec = flow_setup[2].model_copy(update={"trials": 5})
    _, result = execute(flow_setup, worker, spec)
    assert result["stop_reason"] == "ASSET_PROTECTION_FAILED"
    assert result["trials"][1]["status"] == "NOT_EXECUTED"


def test_partial_candidate_execution_not_reported_as_skipped(tmp_path):
    from eval_harness.full_flow import stage_states
    write(tmp_path / "agent3_selection.json", {"status": "SELECTED"})
    write(tmp_path / "agent3_run_summary.json", {"status": "PARTIAL"})
    states, reuse = stage_states(tmp_path, {"stages": {"agent3": 2, "execute": 0}})
    assert states["A3"] == "PARTIAL" and not reuse


def test_interrupted_repair_keeps_draft_for_evaluation(flow_setup):
    base = worker_for()
    def worker(work, config):
        outcome = base(work, config)
        run = work / "p1/runs" / config["run_id"]
        (run / "agent2_test_design.json").rename(run / "agent2_test_design_attempt_1.json")
        outcome.update(status="ERROR", reason="GENERATION_BUDGET_EXHAUSTED")
        outcome["stages"] = {"agent1": 0, "agent2": 1}
        return outcome
    root, result = execute(flow_setup, worker)
    packet = read(root / "trial-1/A2/packet.json")
    assert packet["sources"]["trace"]["output_state"] == "DRAFT_ONLY_NO_FINAL_HANDOFF"
    assert "agent2_test_design_attempt_1.json" in packet["sources"]["artifact"]
    assert "agent2_test_design.json" not in packet["sources"]["artifact"]
    assert result["trials"][0]["judges"]["A2"]["status"] == "NOT_EVALUATED"
    from eval_harness.web.flows import flow_summary
    view = flow_summary(root.parent, root.name)
    assert view["trials"][0]["status"] == "LIMIT_REACHED"
    assert view["trials"][0]["roles"]["A2"] == "LIMIT_REACHED"
    assert read(root / "result.json")["trials"][0]["status"] == "ERROR"


@pytest.mark.parametrize("status", ["incomplete", "failed", None])
def test_incomplete_server_response_stops_further_calls(tmp_path, status):
    def parse(**kwargs):
        return SimpleNamespace(id="resp", status=status, output_parsed=Payload(text="x"))
    ledger = {"attempts": [], "fatal": None}
    meter = MeteredResponses(SimpleNamespace(responses=SimpleNamespace(parse=parse)),
                             "A1", tmp_path, ledger, 3, set())
    for _ in range(2):
        with pytest.raises(RuntimeError):
            meter.parse(model="test", input=[], store=False, text_format=Payload)
    assert len(ledger["attempts"]) == 1
    assert ledger["attempts"][0]["server_completion"] == status
