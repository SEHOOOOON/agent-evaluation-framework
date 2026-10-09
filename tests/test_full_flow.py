from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
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
        "docs/basic_tc_details.json": "{}",
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


@pytest.fixture
def guidance_run(tmp_path):
    run = tmp_path / "p1/runs/RUN-20261005-000000-ABCDEF"
    write(run / "checkpoint4.json", {"checkpoint": "CP4", "status": "PASS"})
    write(run / "final_report.json", {"run_id": run.name, "recommendation": "PASS",
          "checkpoint4_sha256": digest(run / "checkpoint4.json")})
    (run / "srs_snapshot.md").write_text("원문 기준", encoding="utf-8")
    write(run / "slack_payload.json", {"text": run.name, "blocks": [
        {"text": {"text": "결과를 검토하고 등록 여부를 결정하세요. 자동 시험은 공식 승인이 아닙니다."}}]})
    write(run / "notion_payload.json", {"records": [{"run_id": run.name, "result": "PASSED"}]})
    write(run / "external_reporting.json", {
        "run_id": run.name, "mode": "DRY_RUN",
        "final_report_sha256": digest(run / "final_report.json"),
        "checkpoint4_sha256": digest(run / "checkpoint4.json"),
        **{channel: {"status": "PREVIEW", "payload_file": channel + "_payload.json",
                     "payload_sha256": digest(run / (channel + "_payload.json"))}
           for channel in ("slack", "notion")}})
    return run


def guidance_packet(run):
    from eval_harness.full_flow import collect_packets
    spec = FlowSpec(case_id="GUIDANCE", request="request.json", oracle="oracle.json",
                    generation_model="offline", judge_model="offline")
    return collect_packets(run, run.parents[2], spec,
        {"request": {}, "oracle": {"status": "DRAFT"}, "target_commit": "test"}, [])["A4"]


@pytest.mark.parametrize("role", ["A3", "A4"])
@pytest.mark.parametrize("fault", [None, "wrong_run", "private_path"])
def test_execution_error_reaches_role_without_weakening_input_guards(guidance_run, role, fault):
    from eval_harness.full_flow import collect_packets, local_checks, python_scores
    from eval_harness.openai_judge import preview
    run = guidance_run
    write(run / "agent3_automation_plan.json", {"tc_id": "TC-CAND-001"})
    record = {"run_id": "OTHER" if fault == "wrong_run" else run.name,
              "stage": "VALIDATION_EXECUTION", "error_type": "ValueError",
              "message": "C:/private/evidence.txt" if fault == "private_path" else "검토 입력 해시가 다릅니다."}
    write(run / "validation_error.json", record)
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    spec = FlowSpec(case_id="ERROR-TRANSFER", request="request.json", oracle="oracle.json",
                    generation_model="offline", judge_model="offline")
    def collect():
        return collect_packets(run, run.parents[2], spec,
            {"request": {}, "oracle": {"status": "DRAFT"}, "target_commit": "test"}, [])
    if fault == "wrong_run":
        with pytest.raises(ValueError, match="another Run"):
            collect()
    else:
        packet = collect()[role]
        assert packet.sources["trace"]["checkpoints_and_attempts"]["validation_error.json"] == record
        assert packet.sources["trace"]["evidence_delivery"]["json_files"]["validation_error.json"] == "ROLE_INPUT"
        if fault:
            with pytest.raises(ValueError):
                preview(packet, "offline")
        else:
            assert preview(packet, "offline")["maximum_calls"] == 1
        # An error explanation is not proof of execution or an automatic zero.
        scores = python_scores(run, local_checks(run, True, True))
        assert all(row["score"] is None for row in scores["judgments"])
    assert before == {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}


@pytest.mark.parametrize("mode,status,recommendation", [
    ("DRY_RUN", "PREVIEW", "PASS"), ("LIVE", "SENT", "HOLD"), ("LIVE", "FAILED", "HUMAN_REVIEW")])
def test_guidance_preserves_content_and_recorded_delivery(guidance_run, mode, status, recommendation):
    run = guidance_run
    report = read(run / "final_report.json")
    report["recommendation"] = recommendation
    write(run / "final_report.json", report)
    record = read(run / "external_reporting.json")
    record.update(mode=mode, final_report_sha256=digest(run / "final_report.json"))
    record["slack"]["status"] = status
    write(run / "external_reporting.json", record)
    packet = guidance_packet(run)
    support = packet.sources["artifact"]["user_guidance"]
    assert support["mode"] == mode
    assert support["channels"]["slack"]["status_as_recorded"] == status
    assert support["channels"]["slack"]["content"] == read(run / "slack_payload.json")
    assert support["channels"]["notion"]["content"] == read(run / "notion_payload.json")
    assert packet.sources["artifact"]["final_report.json"] == report
    assert "수신을 독립 확인한 것이 아닙니다" in packet.sources["trace"]["evaluation_boundary"]
    from eval_harness.openai_judge import preview
    assert preview(packet, "offline")["maximum_calls"] == 1


@pytest.mark.parametrize("fault", ["record_run", "report_run", "checkpoint_kind", "report_cp_link", "report_hash",
    "checkpoint_hash", "payload_hash", "missing_payload", "path_escape", "wrong_file", "slack_run", "notion_run"])
def test_guidance_rejects_unlinked_or_tampered_evidence(guidance_run, fault):
    from eval_harness.full_flow import user_guidance_support
    run = guidance_run
    record = read(run / "external_reporting.json")
    if fault == "record_run":
        record["run_id"] = "OLD-RUN"
    elif fault in ("report_hash", "checkpoint_hash"):
        record["final_report_sha256" if fault == "report_hash" else "checkpoint4_sha256"] = "0" * 64
    elif fault in ("report_run", "checkpoint_kind"):
        name, field = ("final_report.json", "final_report_sha256") if fault == "report_run" else ("checkpoint4.json", "checkpoint4_sha256")
        write(run / name, {"run_id": "OLD-RUN", "checkpoint": "CP3"})
        record[field] = digest(run / name)
    elif fault == "report_cp_link":
        report = read(run / "final_report.json")
        report["checkpoint4_sha256"] = "0" * 64
        write(run / "final_report.json", report)
        record["final_report_sha256"] = digest(run / "final_report.json")
    elif fault == "payload_hash":
        record["slack"]["payload_sha256"] = "0" * 64
    elif fault == "missing_payload":
        (run / "slack_payload.json").unlink()
    elif fault in ("path_escape", "wrong_file"):
        record["slack"]["payload_file"] = "../secret.json" if fault == "path_escape" else "final_report.json"
    else:
        channel = "slack" if fault == "slack_run" else "notion"
        write(run / (channel + "_payload.json"), {"text": "OLD-RUN"} if channel == "slack" else
              {"records": [{"run_id": "OLD-RUN"}]})
        record[channel]["payload_sha256"] = digest(run / (channel + "_payload.json"))
    write(run / "external_reporting.json", record)
    with pytest.raises(ValueError):
        user_guidance_support(run)


def test_guidance_missing_record_or_uncreated_channel_is_not_agent_fail(guidance_run):
    run = guidance_run
    record = read(run / "external_reporting.json")
    record["notion"] = {"status": "SKIPPED"}
    write(run / "external_reporting.json", record)
    packet = guidance_packet(run)
    assert packet.sources["artifact"]["user_guidance"]["channels"]["notion"] == {
        "status_as_recorded": "SKIPPED", "payload_available": False, "reason": "NO_PAYLOAD_REFERENCE"}
    (run / "external_reporting.json").unlink()
    packet = guidance_packet(run)
    assert packet.sources["artifact"]["user_guidance"]["status"] == "UNAVAILABLE"
    assert not packet.code_checks


@pytest.mark.parametrize("channel", ["slack", "notion"])
@pytest.mark.parametrize("record_exists", [True, False])
def test_delivery_inventory_only_claims_linked_payloads(guidance_run, channel, record_exists):
    run = guidance_run
    record = read(run / "external_reporting.json")
    record[channel] = {"status": "SKIPPED"}
    if record_exists:
        write(run / "external_reporting.json", record)
    else:
        (run / "external_reporting.json").unlink()
    packet = guidance_packet(run)
    files = packet.sources["trace"]["evidence_delivery"]["json_files"]
    assert files[channel + "_payload.json"] == "LOCAL_ONLY_NOT_ROLE_INPUT"
    if record_exists:
        other = "slack" if channel == "notion" else "notion"
        assert files[other + "_payload.json"] == "ROLE_INPUT"


@pytest.mark.parametrize("fault", [None, "legacy", "srs_hash", "request_hash", "srs_file",
                                   "request_file", "missing_request", "linked_srs"])
def test_collector_checks_saved_input_links_without_requiring_legacy_hashes(guidance_run, monkeypatch, fault):
    run = guidance_run
    write(run / "request.json", {})
    manifest = {"run_id": run.name, "request_file": "request.json",
                "srs_snapshot_file": "srs_snapshot.md",
                "request_sha256": digest(run / "request.json"),
                "srs_sha256": digest(run / "srs_snapshot.md")}
    if fault in ("srs_hash", "request_hash"):
        manifest["srs_sha256" if fault == "srs_hash" else "request_sha256"] = "0" * 64
    elif fault in ("srs_file", "request_file"):
        manifest["srs_snapshot_file" if fault == "srs_file" else "request_file"] = "other.md"
    elif fault == "missing_request":
        (run / "request.json").unlink()
    elif fault == "linked_srs":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda p: p == run / "srs_snapshot.md" or original(p))
    if fault != "legacy":
        write(run / "run_manifest.json", manifest)
    if fault not in (None, "legacy"):
        with pytest.raises(ValueError):
            guidance_packet(run)
    else:
        packet = guidance_packet(run)
        links = packet.sources["context"]["saved_input_links"]
        assert set(links) == {"request.json", "srs_snapshot.md"}
        assert {item["status"] for item in links.values()} == {
            "UNVERIFIED_NO_SAVED_HASH" if fault == "legacy" else "HASH_VERIFIED"}
        assert packet.sources["context"]["request"] == {}
        assert packet.sources["context"]["srs"] == "원문 기준"


@pytest.mark.parametrize("fault", ["secret", "oversize", "html", "absolute_path"])
def test_guidance_keeps_transmission_safety_limits(guidance_run, fault):
    from eval_harness.openai_judge import preview, MAX_JUDGE_PAYLOAD_BYTES
    run = guidance_run
    contents = {"secret": "OPENAI_API_KEY=" + "sk-" + "x" * 40,
                "oversize": "x" * (MAX_JUDGE_PAYLOAD_BYTES + 1),
                "html": "<!DOCTYPE html><html><body>private</body></html>",
                "absolute_path": "C:\\private\\secret.txt"}
    write(run / "slack_payload.json", {"text": run.name, "blocks": [contents[fault]]})
    record = read(run / "external_reporting.json")
    record["slack"]["payload_sha256"] = digest(run / "slack_payload.json")
    write(run / "external_reporting.json", record)
    with pytest.raises(ValueError):
        preview(guidance_packet(run), "offline")


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
        write(run / "agent2_test_design.json", {"test_cases": [], "제외된_정보_부족": []})
        write(run / "agent3_selection.json", {"status": "NOT_REQUIRED" if branch == "reuse" else "SELECTED"})
        write(run / "agent3_run_summary.json", {"status": "PASS"})
        stages.update(agent2=0, execute=0, agent4=0)
        if branch != "reuse":
            stages["agent3"] = 0
            for n in range(2 if branch == "multi" else 1):
                write(run / f"agent3_candidates/TC-{n}/agent3_automation_plan.json", {"tc_id": f"TC-{n}"})
                write(run / f"agent3_candidates/TC-{n}/ui_observation.json", {"selectors": ["#apply"]})
        status = "ASSERTION_FAILED" if branch == "product_fail" else "PASSED"
        bundle = {"candidate_results": [], "regression_results": [{"tc_id": "TC1", "status": status, "source": "EXISTING_REGRESSION"}],
                  "제외된_정보_부족": [], "최종_확인_사항": []}
        write(run / "validation_execution.json", bundle)
        write(run / "agent4_analysis.json", dict(validation_execution_sha256=digest(run / "validation_execution.json"),
              total_results=1, status_counts={status:1}, product_result_count=1, environment_result_count=0,
              pipeline_fixture_result_count=0, 최종_확인_사항=[], 제외된_정보_부족=[]))
        write(run / "final_report.json", dict(total_results=1, status_counts={status: 1},
              analysis_sha256=digest(run / "agent4_analysis.json"), product_result_count=1, environment_result_count=0,
              pipeline_fixture_result_count=0, 최종_확인_사항=[], 제외된_정보_부족=[]))
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


def test_preview_pins_basic_tc_details(flow_setup):
    target, config, spec, _ = flow_setup
    hashes = make_preview(target, spec, config)["plan"]["runtime_hashes"]
    assert hashes["docs/basic_tc_details.json"] == digest(target / "docs/basic_tc_details.json")


def test_missing_tracked_basic_details_blocks_before_execution(flow_setup):
    target, config, spec, output = flow_setup
    subprocess.run(["git", "-C", str(target), "rm", "--cached", "docs/basic_tc_details.json"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(target), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-m", "missing input fixture"],
                   check=True, capture_output=True)
    with pytest.raises(ValueError, match="Required runtime inputs"):
        make_preview(target, spec, config)
    assert not output.exists()


def test_basic_details_changed_after_preview_rejects_old_approval(flow_setup):
    target, config, spec, output = flow_setup
    approval = make_preview(target, spec, config)["approval_sha256"]
    write(target / "docs/basic_tc_details.json", {"changed": True})
    def forbidden(*args):
        pytest.fail("Changed inputs must not reach a worker or judge")
    with pytest.raises(ValueError, match="tracked changes"):
        run_flow(target, spec, config, output, approval, worker=forbidden, judge=forbidden)
    assert not output.exists()


@pytest.mark.parametrize("already_started", [False, True])
def test_input_preflight_failure_is_not_an_agent_asset_grade(flow_setup, already_started):
    def worker(work, config):
        write(work / "p1/docs/basic_tc_details.json", {"broken": True})
        write(work / "call_ledger.json", {"attempts": [
            {"role": "A1", "status": "RECEIVED", "response_id": "local-fake"}
        ] if already_started else []})
        return {"status": "ERROR", "reason": "RUNTIME_INPUT_PREFLIGHT_FAILED",
                "stages": {"agent1": 0} if already_started else {}}
    root, result = execute(flow_setup, worker)
    row = result["trials"][0]
    if already_started:
        # A label alone must not hide changes made after generation started.
        assert "E04" in row["scorecard"]["summary"]["violations"]
    else:
        assert row["scorecard"]["summary"]["evaluated"] == 0
        assert all(state == "NOT_EXECUTED" for state in row["roles"].values())
        assert row["runtime_input_integrity"] == {"source_unchanged": True, "copy_unchanged": False}
        assert result["generation_attempts"] == result["judge_attempts"] == 0
        from eval_harness.web.flows import flow_summary
        view = flow_summary(root.parent, root.name)
        assert view["stop_reason"] == "RUNTIME_INPUT_PREFLIGHT_FAILED"
        assert view["trials"][0]["scorecard"]["summary"]["evaluated"] == 0
        script = (Path(__file__).resolve().parents[1] / "src/eval_harness/web/static/flows.js").read_text(encoding="utf-8")
        assert "stopReasons.RUNTIME_INPUT_PREFLIGHT_FAILED=" in script


@pytest.mark.parametrize("fault", [None, "missing", "invalid_json", "invalid_catalog"])
def test_worker_reads_catalog_before_constructing_client(tmp_path, monkeypatch, fault):
    from eval_harness import flow_worker
    copy = tmp_path / "p1"
    write(copy / "docs/basic_tc_details.json", {})
    entry = copy / "src/qa_pipeline_orchestrator.py"
    entry.parent.mkdir()
    entry.write_text("# local fixture", encoding="utf-8")
    if fault == "missing":
        (copy / "docs/basic_tc_details.json").unlink()
    elif fault == "invalid_json":
        (copy / "docs/basic_tc_details.json").write_text("{", encoding="utf-8")
    order = []
    def load_catalog(root):
        assert root == copy / "approved_assets"
        order.append("catalog")
        read(copy / "docs/basic_tc_details.json")
        if fault == "invalid_catalog":
            raise ValueError("private diagnostic must not enter worker result")
        return (), {}
    def new_client(*args):
        order.append("client")
        assert order == ["catalog", "client"]
        return object()
    def pipeline_stop(args):
        order.append("pipeline")
        return 2
    pipeline = SimpleNamespace(run_pipeline=pipeline_stop,
        **{"run_" + role: lambda args: 0 for role in ("agent1", "agent2", "agent3")})
    modules = {"qa_pipeline_execution": SimpleNamespace(load_approved_regression_catalog=load_catalog),
               "qa_pipeline_orchestrator": pipeline}
    monkeypatch.setattr(flow_worker.importlib, "import_module", modules.__getitem__)
    monkeypatch.setattr(flow_worker, "generation_client", new_client)
    monkeypatch.setattr(flow_worker, "bind_model_clients", lambda *args: None)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture")
    config = dict(run_id="RUN-local", generation_model="offline", request="request.json",
                  product_timeout_seconds=30)
    result = flow_worker.execute_trial(tmp_path, config)
    assert read(tmp_path / "call_ledger.json")["attempts"] == []
    preflight = read(tmp_path / "runtime_input_preflight.json")
    if fault:
        assert order == ["catalog"]
        assert preflight["status"] == "ERROR"
        assert result["reason"] == "RUNTIME_INPUT_PREFLIGHT_FAILED"
        assert result["stages"] == {}
        assert "private diagnostic" not in json.dumps(result)
    else:
        assert order == ["catalog", "client", "pipeline"]
        assert preflight == {"status": "READY"}
        assert result["reason"] == "PIPELINE_STOP"


@pytest.mark.parametrize("fault", [None, "missing", "source_hash", "approved_hash"])
def test_actual_p1_copy_preflight_without_api(tmp_path, fault):
    target_name = os.environ.get("QA_P1_TEST_ROOT")
    if not target_name:
        pytest.skip("Set QA_P1_TEST_ROOT for the actual isolated P1 copy regression")
    target = Path(target_name)
    config_root = Path(__file__).resolve().parents[1] / "benchmarks/v2"
    spec = FlowSpec.model_validate(read(config_root / "full_flow.example.json")).model_copy(
        update={"judge_calls_total": 0})
    preview = make_preview(target, spec, config_root)
    expected_counts = {
        "approved": len(read(target / "approved_assets/registry.json")["assets"]),
        "baseline": len(read(target / "docs/basic_tc_details.json")["cases"]),
    }
    probes = []
    def worker(work, config):
        copy = work / "p1"
        if fault == "missing":
            (copy / "docs/basic_tc_details.json").unlink(missing_ok=True)
        elif fault == "source_hash":
            details = read(copy / "docs/basic_tc_details.json")
            details["source_sha256"] = "0" * 64
            write(copy / "docs/basic_tc_details.json", details)
        elif fault == "approved_hash":
            registry = read(copy / "approved_assets/registry.json")
            asset = registry["assets"][0]
            (copy / "approved_assets" / asset["test_case_file"]).write_text("{}", encoding="utf-8")
        write(work / "probe_config.json", config)
        # Fresh process: no original-worktree modules can mask a missing copy input.
        code = """
import socket, sys
from pathlib import Path
from eval_harness.v2_recorded import read, write
from eval_harness import flow_worker
work = Path(sys.argv[1])
sys.path.insert(0, str(work / 'p1/src'))
def forbidden(*args, **kwargs):
    raise AssertionError('No network or paid client in this local probe')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
flow_worker.generation_client = forbidden
import qa_pipeline_execution as execution
import qa_pipeline_orchestrator as pipeline
assert Path(execution.__file__).resolve().is_relative_to(work / 'p1')
original = execution.load_approved_regression_catalog
def catalog(root):
    approved, snapshot = original(root)
    write(work / 'catalog_probe.json', {'approved': len(approved),
        'baseline': len(snapshot['baseline_details']['cases'])})
    return approved, snapshot
execution.load_approved_regression_catalog = catalog
def stop_before_model(args):
    write(work / 'pipeline_reached.json', {'reached': True})
    return 2
pipeline.run_pipeline = stop_before_model
flow_worker.execute_trial(work, read(work / 'probe_config.json'), client=object())
"""
        completed = subprocess.run([sys.executable, "-B", "-c", code, str(work)],
            cwd=work, env=clean_environment(), capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert completed.returncode == 0, completed.stderr
        result = read(work / "worker_result.json")
        assert read(work / "call_ledger.json")["attempts"] == []
        if fault:
            assert result["reason"] == "RUNTIME_INPUT_PREFLIGHT_FAILED"
            assert result["stages"] == {}
            assert not (work / "pipeline_reached.json").exists()
        else:
            assert (work / "pipeline_reached.json").exists()
            assert read(work / "catalog_probe.json") == expected_counts
        probes.append(result)
        return result
    def forbidden_judge(*args):
        pytest.fail("Preflight-only test must not invoke a judge")
    root = run_flow(target, spec, config_root, tmp_path / "output", preview["approval_sha256"],
                    worker=worker, judge=forbidden_judge)
    assert len(probes) == 1  # run_flow preserves worker exceptions; don't silently pass.
    result = read(root / "result.json")
    assert result["generation_attempts"] == result["judge_attempts"] == 0
    assert result["trials"][0]["scorecard"]["summary"]["evaluated"] == 0
    assert make_preview(target, spec, config_root) == preview  # source assets unchanged


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
    for role, count in (("A1", 6), ("A2", 10), ("A3", 5), ("A4", 4)):
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


def test_preflight_stop_is_visible_without_changing_p1_result(flow_setup, monkeypatch):
    import eval_harness.full_flow as flow
    from eval_harness.web.flows import flow_summary
    collect = flow.collect_packets
    def oversized(*args):
        packets = collect(*args)
        from eval_harness.openai_judge import MAX_JUDGE_PAYLOAD_BYTES
        packets["A3"].sources["trace"]["probe"] = "x" * MAX_JUDGE_PAYLOAD_BYTES
        return packets
    monkeypatch.setattr(flow, "collect_packets", oversized)
    root, result = execute(flow_setup, worker_for())
    view = flow_summary(root.parent, root.name)
    assert view["status"] == "STOPPED"
    assert view["stop_reason"] == "JUDGE_PREFLIGHT_FAILED"
    assert view["trials"][0]["status"] == "COMPLETED"
    assert result["judge_attempts"] == 0
    script = (Path(flow.__file__).parent / "web/static/flows.js").read_text(encoding="utf-8")
    assert "esc(stopReasons[d.stop_reason]||label(d.stop_reason))" in script
    assert "프로젝트1 실행 상태" in script


@pytest.mark.parametrize("fault", ["oversized", "sensitive"])
def test_all_selected_packets_preflight_before_any_judge(flow_setup, monkeypatch, fault):
    import eval_harness.full_flow as flow
    collect = flow.collect_packets
    called = []
    def bad_later_packet(*args):
        packets = collect(*args)
        from eval_harness.openai_judge import MAX_JUDGE_PAYLOAD_BYTES
        packets["A3"].sources["trace"]["probe"] = "x" * MAX_JUDGE_PAYLOAD_BYTES if fault == "oversized" else "C:/private/path"
        return packets
    def judge(packet, *args):
        called.append(packet.agent_id)
        return fake_judge(packet, *args)
    monkeypatch.setattr(flow, "collect_packets", bad_later_packet)
    _, result = execute(flow_setup, worker_for(), judge=judge)
    assert called == []
    assert result["judge_slots_used"] == result["judge_attempts"] == 0
    assert result["stop_reason"] == "JUDGE_PREFLIGHT_FAILED"
    assert result["trials"][0]["judges"]["A3"]["status"] == "ERROR"
    assert result["trials"][0]["assessment"] == "REVIEW"


@pytest.mark.parametrize("budget", [0, 1])
def test_unselected_packets_do_not_block_limited_judge_budget(flow_setup, monkeypatch, budget):
    import eval_harness.full_flow as flow
    collect = flow.collect_packets
    called = []
    def oversized_unselected(*args):
        packets = collect(*args)
        from eval_harness.openai_judge import MAX_JUDGE_PAYLOAD_BYTES
        packets["A3"].sources["trace"]["probe"] = "x" * MAX_JUDGE_PAYLOAD_BYTES
        return packets
    def judge(packet, *args):
        called.append(packet.agent_id)
        return fake_judge(packet, *args)
    monkeypatch.setattr(flow, "collect_packets", oversized_unselected)
    spec = flow_setup[2].model_copy(update={"judge_calls_total": budget})
    _, result = execute(flow_setup, worker_for(), spec=spec, judge=judge)
    assert called == (["A1"] if budget else [])
    assert result["stop_reason"] is None
    assert result["trials"][0]["judges"]["A3"]["reason"] == "JUDGE_BUDGET_EXHAUSTED"


def test_all_previews_exist_before_first_judge(flow_setup):
    def judge(packet, model, approval, output):
        assert all((output.parent / role / "preview.json").is_file() for role in ("A1", "A2", "A3", "A4"))
        return fake_judge(packet, model, approval, output)
    _, result = execute(flow_setup, worker_for(), judge=judge)
    assert result["status"] == "COMPLETED"
    assert result["judge_attempts"] == 4


def test_evidence_changed_during_preparation_stops_before_judge(flow_setup, monkeypatch):
    import eval_harness.full_flow as flow
    collect = flow.collect_packets
    def changed_after_collection(run, *args):
        packets = collect(run, *args)
        (run / "srs_snapshot.md").write_text("changed", encoding="utf-8")
        return packets
    called = []
    def judge(*args):
        called.append(True)
        return fake_judge(*args)
    monkeypatch.setattr(flow, "collect_packets", changed_after_collection)
    _, result = execute(flow_setup, worker_for(), judge=judge)
    assert called == []
    assert result["stop_reason"] == "EVIDENCE_CHANGED_DURING_REVIEW"


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


@pytest.mark.parametrize("unsafe", [False, True])
def test_meter_scans_json_content_without_rewriting_or_spending_on_rejection(tmp_path, unsafe):
    calls = []
    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="offline-json-scan", status="completed",
                               output_parsed=Payload(text="original"), usage=None)
    ledger = {"attempts": [], "fatal": None}
    meter = MeteredResponses(SimpleNamespace(responses=SimpleNamespace(parse=parse)), "A2",
                             tmp_path, ledger, 1, set())
    content = json.dumps({"instructions": "Execution/review responsibilities for new runs:\nKeep original values.",
                          "detail": r"C:\new\file" if unsafe else "STOP"})
    kwargs = dict(model="test", input=[{"role": "user", "content": content}], store=False, text_format=Payload)
    if unsafe:
        with pytest.raises(ValueError):
            meter.parse(**kwargs)
        assert calls == ledger["attempts"] == []
        assert not (tmp_path / "calls").exists()
    else:
        assert meter.parse(**kwargs).output_parsed.text == "original"
        assert calls[0]["input"] == kwargs["input"]
        assert len(ledger["attempts"]) == 1
        assert read(tmp_path / "calls/001/request.json")["payload"]["input"] == kwargs["input"]


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
        value = {"items": []} if "items" in schema.model_fields else {"text": "offline"}
        return SimpleNamespace(id=f"offline-{len(calls)}", status="completed",
                               output_parsed=schema.model_validate(value), usage=None)
    ledger = {"attempts": [], "fatal": None}
    config = dict(seen_response_ids=[], generation_limit=6, generation_max_output_tokens=12000)
    bind_model_clients(execution, SimpleNamespace(responses=SimpleNamespace(parse=parse)), tmp_path, ledger, config)
    def review_input(stage):
        return {"stage": stage, "items": [], "source_documents": [
            {"source_id": "REQ-001", "text": "로컬 호출 예산 검증용 원문"}]}
    for stage in (1, 2, 3):
        agent = getattr(execution, f"OpenAIAgent{stage}")(model="offline")
        agent.client.responses.parse(model="offline", store=False, input=[], text_format=Payload)
        execution.OpenAIGroundingReviewer(model="offline").review(
            review_input(f"AGENT{stage}"))
    assert [(r["role"], r["purpose"]) for r in ledger["attempts"]] == [
        (role, purpose) for role in ("A1", "A2", "A3") for purpose in ("generation", "grounding")]
    with pytest.raises(RuntimeError, match="budget"):
        execution.OpenAIGroundingReviewer(model="offline").review(review_input("AGENT1"))
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


@pytest.mark.parametrize("repeat", [1, 2])
def test_embedded_request_text_factors_exact_core_strings_losslessly(repeat):
    from eval_harness.full_flow import compact_evidence
    text = "원래 TC의 조작과 기대결과를 그대로 보존한다. " * 30
    content = "[생성 입력]\n" + (text + "\n다음 자료\n") * repeat
    source = {"context": {"tc": text}, "artifact": {"different": text + "변경"},
              "trace": {"request": content, "unique_instruction": "별도 지침을 유지한다."}}
    before = deepcopy(source)
    compact = compact_evidence(source)
    factored = compact["trace"]["request"]
    assert isinstance(factored, dict) and "evidence_text_parts" in factored
    rebuilt = ""
    for part in factored["evidence_text_parts"]:
        if isinstance(part, str):
            rebuilt += part
        else:
            target = compact
            for key in part["evidence_reference"]:
                target = target[int(key)] if isinstance(target, list) else target[key]
            assert isinstance(target, str)
            rebuilt += target
    from hashlib import sha256
    assert rebuilt == content
    assert factored["raw_utf8_sha256"] == sha256(content.encode()).hexdigest()
    assert compact["context"] == source["context"] and compact["artifact"] == source["artifact"]
    assert compact["trace"]["unique_instruction"] == source["trace"]["unique_instruction"]
    assert source == before
    assert len(json.dumps(compact, ensure_ascii=False)) < len(json.dumps(source, ensure_ascii=False))


@pytest.mark.parametrize("unsafe", ["sk-", "Bearer ", "C:/", "<script>"])
def test_embedded_text_never_hides_sensitive_boundary(unsafe):
    from eval_harness.full_flow import compact_evidence
    text = "A" * 600
    source = {"context": {"value": text}, "artifact": {}, "trace": {"request": unsafe + text}}
    with pytest.raises(ValueError, match="Secret, local path, or HTML"):
        compact_evidence(source)


def test_small_or_nonidentical_embedded_text_is_not_factored():
    from eval_harness.full_flow import compact_evidence
    text = "한" * 160  # UTF-8 below the existing 512-byte threshold.
    source = {"context": {"small": text, "large": "A" * 600}, "artifact": {},
              "trace": {"short": "입력 " + text, "changed": "입력 " + "A" * 599 + "B"}}
    assert compact_evidence(source) == source


@pytest.mark.parametrize("field,value,wrong", [
    ("status", "STOP", "OPERATION"), ("mode", "HEAT", "FAN"),
    ("fanSpeed", "HIGH", "LOW"), ("setTemp", 21, 27),
    ("locked", True, False),
])
@pytest.mark.parametrize("variant", ["normal", "missing_confirmation", "wrong_expected"])
def test_core_evidence_is_direct_without_hiding_or_repairing_errors(field, value, wrong, variant):
    """Check what reaches the judge, not a live model's grading accuracy."""
    from eval_harness.full_flow import compact_evidence
    confirmation = [dict(result_id="ER-X", basis="OBSERVED_BASELINE",
                         source_text="복원 뒤 같은 관찰 위치의 원래값과 비교한다. " * 24)]
    tc = dict(expected={field: value}, restore_confirmations=confirmation,
              description="전달 원문을 그대로 보존하는 합성 시험 설명. " * 32)
    plan = deepcopy(tc)
    if variant == "missing_confirmation":
        plan["restore_confirmations"] = []
    elif variant == "wrong_expected":
        plan["expected"][field] = wrong
    source = dict(context={"upstream": tc}, artifact={"plan": plan},
                  trace={"saved_attempt": deepcopy(plan)})
    before = deepcopy(source)
    compact = compact_evidence(source)
    assert source == before
    assert compact["context"] == source["context"]
    assert compact["artifact"] == source["artifact"]
    expected_reference = ["context", "upstream"] if variant == "normal" else ["artifact", "plan"]
    assert compact["trace"]["saved_attempt"] == {"evidence_reference": expected_reference}
    # Never repair a flawed plan by copying the correct upstream contract.
    assert compact["artifact"]["plan"]["restore_confirmations"] == ([] if variant == "missing_confirmation" else confirmation)
    assert compact["artifact"]["plan"]["expected"][field] == (wrong if variant == "wrong_expected" else value)


@pytest.mark.parametrize("purpose", ["grounding", "generation", "unknown", None])
def test_only_grounding_request_body_is_omitted(purpose):
    from eval_harness.full_flow import request_evidence
    call = dict(role="A2", attempt=2, payload={"input": [{"role": "user", "content": "unchanged body"}]})
    if purpose is not None:
        call["purpose"] = purpose
    original = deepcopy(call)
    result = request_evidence(call, "a" * 64)
    assert call == original
    assert result["role"] == "A2" and result["attempt"] == 2
    if purpose == "grounding":
        assert "payload" not in result
        assert result["raw_request_sha256"] == "a" * 64
        assert result["payload_omitted"] == "GROUNDING_BODY_NOT_ROLE_GENERATION"
    else:
        assert result == call


def test_grounding_body_cannot_be_omitted_without_original_hash():
    from eval_harness.full_flow import request_evidence
    with pytest.raises(ValueError, match="original file hash"):
        request_evidence(dict(purpose="grounding", payload={"input": []}))


def test_large_grounding_body_preserves_call_and_rejection_records(flow_setup):
    import zipfile
    from eval_harness.openai_judge import JudgePacket, preview
    base = worker_for("reuse")
    def worker(work, config):
        outcome = base(work, config)
        run = work / "p1/runs" / config["run_id"]
        write(work / "calls/001/request.json", dict(role="A2", purpose="generation", attempt=2,
              payload={"input": [{"role": "user", "content": "Required generation input"}]}))
        write(work / "calls/002/request.json", dict(role="A2", purpose="grounding", attempt=3,
              payload={"input": [{"role": "user", "content": "long review body " * 9000}]}))
        ledger = read(work / "call_ledger.json")
        ledger["attempts"].append(dict(role="A2", purpose="grounding", attempt=3, status="RECEIVED",
              request_sha256=digest(work / "calls/002/request.json"), response_id="review-response"))
        write(work / "call_ledger.json", ledger)
        write(run / "agent2_grounding_review_attempt_1.json", {"verdict": "UNSUPPORTED", "reason": "Missing requested check"})
        write(run / "agent2_grounding_input_attempt_1.json", {"input": "long saved review input " * 9000})
        write(run / "agent2_grounding_input_attempt_notes.json", {"input": "Unrecognized artifact is not silently omitted"})
        write(run / "checkpoint2_attempt_1.json", {"status": "FAIL"})
        return outcome
    root, state = execute(flow_setup, worker)
    packet = JudgePacket.model_validate(read(root / "trial-1/A2/packet.json"))
    trace = packet.sources["trace"]
    assert trace["actual_model_requests"][0]["payload"]["input"][0]["content"] == "Required generation input"
    omitted = trace["actual_model_requests"][1]
    assert "payload" not in omitted
    assert trace["model_call_records"][-1]["status"] == "RECEIVED"
    assert trace["model_call_records"][-1]["request_sha256"] == omitted["raw_request_sha256"]
    assert trace["checkpoints_and_attempts"]["agent2_grounding_review_attempt_1.json"]["verdict"] == "UNSUPPORTED"
    assert trace["checkpoints_and_attempts"]["checkpoint2_attempt_1.json"]["status"] == "FAIL"
    assert trace["checkpoints_and_attempts"]["agent2_grounding_input_attempt_notes.json"]["input"] == "Unrecognized artifact is not silently omitted"
    archived_input = trace["checkpoints_and_attempts"]["agent2_grounding_input_attempt_1.json"]
    assert "input" not in archived_input and archived_input["payload_omitted"]
    assert preview(packet, "test-judge")
    trial = state["trials"][0]
    work = Path(trial["workspace"])
    raw = work / "calls/002/request.json"
    raw_input = work / "p1/runs" / trial["run_id"] / "agent2_grounding_input_attempt_1.json"
    assert archived_input["raw_artifact_sha256"] == digest(raw_input)
    assert raw.stat().st_size > 120_000
    with zipfile.ZipFile(root / trial["evidence_archive"]) as z:
        assert z.read("calls/002/request.json") == raw.read_bytes()
        assert z.read(f"p1/runs/{trial['run_id']}/agent2_grounding_input_attempt_1.json") == raw_input.read_bytes()


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


@pytest.fixture
def waiting_run(tmp_path):
    run = tmp_path / "RUN-WAITING"
    write(run / "agent1_change_analysis.json", {"decision": "WAITING_FOR_USER", "user_questions": ["새 하한은 몇 도인가요?"]})
    write(run / "checkpoint1.json", {"status": "PASS", "handoff_status": "PAUSE"})
    write(run / "run_manifest.json", {
        "run_id": run.name, "stage": "AGENT_1_CP1", "status": "PASS", "handoff_status": "PAUSE",
        "checkpoint1_sha256": digest(run / "checkpoint1.json"),
        "agent1_analysis_sha256": digest(run / "agent1_change_analysis.json")})
    write(run / "orchestrator_manifest.json", {
        "run_id": run.name, "status": "STOPPED", "stopped_at": "agent1", "stage_exit_codes": {"agent1": 2},
        "agent1_manifest_sha256": digest(run / "run_manifest.json")})
    return run


@pytest.mark.parametrize("fault", [None, "worker_error", "next_stage", "run", "stage", "checkpoint",
                                  "handoff", "analysis", "questions", "missing", "hash", "shape"])
def test_waiting_notice_requires_verified_pause(waiting_run, fault):
    from eval_harness.full_flow import waiting_user_notice, stage_states
    run = waiting_run
    worker = {"status": "BLOCKED", "stages": {"agent1": 2}}
    if fault == "worker_error": worker["status"] = "ERROR"
    if fault == "next_stage": worker["stages"]["agent2"] = 2
    changes = {
        "run": ("orchestrator_manifest.json", "run_id", "OTHER"),
        "stage": ("orchestrator_manifest.json", "stopped_at", "agent2"),
        "checkpoint": ("checkpoint1.json", "status", "FAIL"),
        "handoff": ("run_manifest.json", "handoff_status", "CONTINUE"),
        "analysis": ("agent1_change_analysis.json", "decision", "CONTINUE"),
        "questions": ("agent1_change_analysis.json", "user_questions", []),
        "hash": ("orchestrator_manifest.json", "agent1_manifest_sha256", "0" * 64)}
    if fault in changes:
        filename, key, value = changes[fault]
        write(run / filename, {**read(run / filename), key: value})
        if fault in {"checkpoint", "handoff", "analysis", "questions"}:
            # Consistent hashes must not make a semantically different stop a pause.
            write(run / "run_manifest.json", {**read(run / "run_manifest.json"),
                  "checkpoint1_sha256": digest(run / "checkpoint1.json"),
                  "agent1_analysis_sha256": digest(run / "agent1_change_analysis.json")})
            write(run / "orchestrator_manifest.json", {**read(run / "orchestrator_manifest.json"),
                  "agent1_manifest_sha256": digest(run / "run_manifest.json")})
    if fault == "missing": (run / "checkpoint1.json").unlink()
    if fault == "shape": write(run / "checkpoint1.json", [])
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    notice = waiting_user_notice(run, worker)
    assert (notice is not None) == (fault is None)
    if notice:
        assert notice["questions"] == ["새 하한은 몇 도인가요?"]
    assert stage_states(run, worker)[0]["A1"] == "BLOCKED"  # raw stage outcome remains unchanged
    assert before == {p.name: p.read_bytes() for p in run.iterdir()}


@pytest.mark.parametrize("status", ["BLOCKED", "ERROR", "COMPLETED"])
def test_waiting_dashboard_does_not_change_saved_outcome(waiting_run, tmp_path, status):
    from eval_harness.full_flow import waiting_user_notice, stage_states
    from eval_harness.web.flows import flow_summary
    worker = {"status": "BLOCKED", "stages": {"agent1": 2}}
    row = {"trial": 1, "status": status, "roles": stage_states(waiting_run, worker)[0],
           "waiting_for_user": waiting_user_notice(waiting_run, worker)}
    name = "FLOW-20261005-000000-abcdef"
    path = tmp_path / name / "result.json"
    write(path, {"mode": "TEST_DOUBLE", "trials": [row]})
    before = path.read_bytes()
    view = flow_summary(tmp_path, name)["trials"][0]
    assert view["raw_status"] == status
    assert view["status"] == ("WAITING_FOR_USER" if status == "BLOCKED" else status)
    assert bool(view["questions"]) == (status == "BLOCKED")
    assert before == path.read_bytes()


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
