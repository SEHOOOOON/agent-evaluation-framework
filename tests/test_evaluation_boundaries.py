"""Offline counterexamples for score ownership, evidence and legacy boundaries."""
from collections import Counter
from copy import deepcopy

import pytest

from eval_harness.detailed_rubric import (CRITERIA, SCORE_VERSION, evaluation_method,
    full_scorecard, repeat_scores, score_summary)
from eval_harness.full_flow import python_scores, execution_support, collect_packets, FlowSpec, local_checks
from eval_harness.v2_recorded import write, digest, handoff_checks, read, report_counts_match
from eval_harness.openai_judge import preview, validate_judgment
from test_scoring import packet, response, results


def saved_run(tmp_path):
    run = tmp_path / "RUN-test"
    run.mkdir()
    log = run / "stdout.txt"
    log.write_text("observed STOP; restored OPERATION", encoding="utf-8")
    row = dict(test_id="TC-any", status="PASSED", source="NEW_AUTOMATION_CANDIDATE",
               requirement_ids=["REQ-ANY-001"],
               stdout_file="stdout.txt", evidence_files=["stdout.txt"],
               evidence_sha256={"stdout.txt": digest(log)})
    execution = dict(run_id=run.name, candidate_results=[row], regression_results=[], status="COMPLETED")
    write(run / "validation_execution.json", execution)
    analysis = dict(run_id=run.name, validation_execution_sha256=digest(run / "validation_execution.json"),
                    total_results=1, status_counts={"PASSED":1}, 검토_항목=[],
                    product_result_count=1, environment_result_count=0, pipeline_fixture_result_count=0)
    write(run / "agent4_analysis.json", analysis)
    report = dict(run_id=run.name, analysis_sha256=digest(run / "agent4_analysis.json"),
                  total_results=1, status_counts={"PASSED": 1}, 검토_항목=[],
                  product_result_count=1, environment_result_count=0, pipeline_fixture_result_count=0)
    write(run / "final_report.json", report)
    return run, execution, analysis, report


def grade(run):
    return {r["criterion_id"]: r for r in python_scores(run, [])["judgments"]}


def test_methods_cover_exactly_26_without_new_rules():
    assert Counter(evaluation_method(c["criterion_id"]) for c in CRITERIA) == dict(PYTHON=4, AI=12, HYBRID=10)


def test_counts_and_linkage_are_local_approval_not_invented(tmp_path):
    run, *_ = saved_run(tmp_path)
    checked = grade(run)
    assert checked["A4-01"]["score"] == checked["A4-03"]["score"] == 5
    assert checked["E04"]["score"] is None
    data = results()
    data["A4"]["judgments"] = [j for j in data["A4"]["judgments"] if j["criterion_id"] not in {"A4-01", "A4-03"}]
    card = full_scorecard(data, python_scores(run, []))
    assert next(c for c in card["criteria"] if c["criterion_id"] == "A4-01")["evaluation_method"] == "PYTHON"
    assert next(c for c in card["criteria"] if c["criterion_id"] == "E04")["score"] is None


@pytest.mark.parametrize("mutation", ["count", "analysis_count", "boolean_count", "duplicate", "wrong_run", "changed_log", "missing_file", "wrong_finding"])
def test_real_counterexamples_not_passed(tmp_path, mutation):
    run, execution, analysis, report = saved_run(tmp_path)
    expected = "A4-03"
    if mutation == "count":
        report["total_results"] = 2
        expected = "A4-01"
    elif mutation == "analysis_count":
        analysis["status_counts"] = {"ASSERTION_FAILED":1}
        expected = "A4-01"
    elif mutation == "boolean_count":
        report["total_results"] = True
        expected = "A4-01"
    elif mutation == "duplicate":
        execution["candidate_results"] *= 2
        report.update(total_results=2, status_counts={"PASSED":2})
        expected = "A4-01"
    elif mutation == "wrong_run":
        report["run_id"] = "OTHER"
    elif mutation == "changed_log":
        (run / "stdout.txt").write_text("altered", encoding="utf-8")
    elif mutation == "missing_file":
        execution["candidate_results"][0]["evidence_files"] = ["absent.txt"]
    elif mutation == "wrong_finding":
        report["검토_항목"] = [{"test_id":"OTHER", "status":"PASSED", "evidence_files":[]}]
    write(run / "validation_execution.json", execution)
    analysis["validation_execution_sha256"] = digest(run / "validation_execution.json")
    write(run / "agent4_analysis.json", analysis)
    report["analysis_sha256"] = digest(run / "agent4_analysis.json")
    write(run / "final_report.json", report)
    assert grade(run)[expected]["verdict"] == "FAIL"


def test_missing_manifest_is_review_not_five_or_zero(tmp_path):
    run, execution, analysis, report = saved_run(tmp_path)
    execution["candidate_results"][0].pop("evidence_files")
    write(run / "validation_execution.json", execution)
    analysis["validation_execution_sha256"] = digest(run / "validation_execution.json")
    write(run / "agent4_analysis.json", analysis)
    report["analysis_sha256"] = digest(run / "agent4_analysis.json")
    write(run / "final_report.json", report)
    assert grade(run)["A4-03"]["verdict"] == "REVIEW"


def test_asset_violation_remains_fail_without_approval_evidence(tmp_path):
    run, *_ = saved_run(tmp_path)
    result = python_scores(run, [{"rule":"E04.ASSET_PROTECTION", "status":"FAIL"}])
    assert next(r for r in result["judgments"] if r["criterion_id"] == "E04")["verdict"] == "FAIL"


@pytest.mark.parametrize("output", ["agent4_analysis.json", "final_report.json"])
@pytest.mark.parametrize("field", ["product_result_count", "environment_result_count", "pipeline_fixture_result_count"])
@pytest.mark.parametrize("value", [2, True, -1])
def test_each_partition_is_independently_graded(tmp_path, output, field, value):
    run, *_ = saved_run(tmp_path)
    payload = read(run / output)
    payload[field] = value
    write(run / output, payload)
    assert grade(run)["A4-01"]["verdict"] == "FAIL"
    assert next(c for c in local_checks(run, True, True) if c["rule"] == "A4.COUNTS")["status"] == "FAIL"


def test_missing_counts_do_not_disable_independent_linkage(tmp_path):
    run, *_ = saved_run(tmp_path)
    payload = read(run / "final_report.json")
    payload.pop("product_result_count")
    write(run / "final_report.json", payload)
    checked = grade(run)
    assert checked["A4-01"]["verdict"] == "REVIEW"
    assert checked["A4-03"]["score"] == 5


def test_partition_uses_source_before_fixture_id_and_preserves_skipped_product():
    rows = [dict(test_id='TC-NEW', source='NEW_AUTOMATION_CANDIDATE', status='PASSED'),
            dict(test_id='TC-OLD', source='EXISTING_REGRESSION', status='SKIPPED'),
            dict(test_id='TC-PIPE-FIXTURE', source='EXISTING_REGRESSION', status='ASSERTION_FAILED'),
            dict(test_id='TC-PIPE-ENV', source='ENVIRONMENT_PRECHECK', status='PASSED')]
    payload = dict(total_results=4, status_counts={'PASSED':2,'SKIPPED':1,'ASSERTION_FAILED':1},
                   product_result_count=2, environment_result_count=1, pipeline_fixture_result_count=1)
    assert report_counts_match(rows, payload)
    payload.update(product_result_count=3, environment_result_count=0)
    assert not report_counts_match(rows, payload)


@pytest.mark.parametrize("mutation", ["source", "requirement_ids", "missing_source", "missing_requirements"])
def test_finding_source_and_requirements_are_not_inferred(tmp_path, mutation):
    run, execution, analysis, report = saved_run(tmp_path)
    row = execution["candidate_results"][0]
    finding = dict(test_id=row["test_id"], status=row["status"], source=row["source"],
                   requirement_ids=row["requirement_ids"], evidence_files=row["evidence_files"])
    if mutation == "source": finding["source"] = "EXISTING_REGRESSION"
    elif mutation == "requirement_ids": finding["requirement_ids"] = ["REQ-OTHER-001"]
    else: finding.pop("source" if mutation == "missing_source" else "requirement_ids")
    for payload in (analysis, report): payload["검토_항목"] = [finding]
    write(run / "agent4_analysis.json", analysis)
    report["analysis_sha256"] = digest(run / "agent4_analysis.json")
    write(run / "final_report.json", report)
    assert grade(run)["A4-03"]["verdict"] == ("REVIEW" if mutation.startswith("missing") else "FAIL")


def handoff_files():
    gap = "요청한 알림 색상이 미정"
    question = "목표 알림 색상은 무엇인가요?"
    files = {"agent1_change_analysis.json": dict(information_gaps=[gap], excluded_information_gaps=[gap],
                                                user_questions=[question])}
    for name in ("agent2_test_design.json", "validation_execution.json", "agent4_analysis.json", "final_report.json"):
        files[name] = {"제외된_정보_부족": [gap], "최종_확인_사항": ["사용자 확인 요청: " + question, "CP1: 추가 확인", "CP2: 시험 절차 확인"]}
    return files


@pytest.mark.parametrize("wrapped", [True, False])
def test_exact_questions_accept_p1_wrapper_and_legitimate_additional_notes(wrapped):
    files = handoff_files()
    if not wrapped:
        for name, payload in files.items():
            if name != "agent1_change_analysis.json": payload["최종_확인_사항"][0] = files["agent1_change_analysis.json"]["user_questions"][0]
    assert all(c["status"] == "PASS" for c in handoff_checks(files))


@pytest.mark.parametrize("name", ["validation_execution.json", "agent4_analysis.json", "final_report.json"])
@pytest.mark.parametrize("mutation", ["missing", "changed_punctuation", "extra_prefixed_question"])
def test_question_loss_and_change_at_every_stage_are_detected(name, mutation):
    files = handoff_files()
    notes = files[name]["최종_확인_사항"]
    if mutation == "missing": notes.pop(0)
    elif mutation == "changed_punctuation": notes[0] += "."
    else: notes.append("사용자 확인 요청: 요청하지 않은 주기는?")
    assert next(c for c in handoff_checks(files) if c["rule"] == "E03.QUESTIONS")["status"] == "FAIL"


@pytest.mark.parametrize("name", ["agent2_test_design.json", "validation_execution.json", "agent4_analysis.json", "final_report.json"])
@pytest.mark.parametrize("mutation", ["lost", "replaced", "extra", "absent"])
def test_gap_comparison_covers_intermediate_stages(name, mutation):
    files = handoff_files()
    if mutation == "absent": files[name].pop("제외된_정보_부족")
    else: files[name]["제외된_정보_부족"] = {"lost": [], "replaced": ["다른 값 미정"], "extra": ["요청한 알림 색상이 미정", "새 부족 항목"]}[mutation]
    expected = "NOT_EVALUATED" if mutation == "absent" else "FAIL"
    assert next(c for c in handoff_checks(files) if c["rule"] == "E02.GAPS")["status"] == expected


def test_gap_aliases_are_supported_but_conflicts_not_ignored():
    files = handoff_files()
    a2 = files["agent2_test_design.json"]
    a2["excluded_information_gaps"] = a2.pop("제외된_정보_부족")
    assert all(c["status"] == "PASS" for c in handoff_checks(files))
    a2["제외된_정보_부족"] = []
    assert next(c for c in handoff_checks(files) if c["rule"] == "E02.GAPS")["status"] == "NOT_EVALUATED"


def test_reused_code_and_logs_verified_by_hash_not_case_name(tmp_path):
    run, execution, *_ = saved_run(tmp_path)
    code = tmp_path / "p1/approved_assets/new-name.py"
    code.parent.mkdir(parents=True)
    code.write_text("assert actual == expected", encoding="utf-8")
    execution["candidate_results"][0].update(test_file="old-name.py", test_sha256=digest(code))
    result = execution_support(run, tmp_path, execution)
    assert result["files"]["stdout.txt"]["content"] == (run / "stdout.txt").read_text(encoding="utf-8")
    assert result["files"]["pinned/approved_assets/new-name.py"]["content"] == code.read_text(encoding="utf-8")
    assert result["omissions"] == []
    execution["candidate_results"][0]["test_sha256"] = "0" * 64
    assert execution_support(run, tmp_path, execution)["omissions"][0]["reason"] == "PINNED_CODE_HASH_NOT_FOUND"


def test_large_log_not_silently_cut_or_assumed_complete(tmp_path):
    run, execution, *_ = saved_run(tmp_path)
    (run / "stdout.txt").write_text("x" * 25000, encoding="utf-8")
    execution["candidate_results"][0]["evidence_sha256"]["stdout.txt"] = digest(run / "stdout.txt")
    result = execution_support(run, tmp_path, execution)
    assert result["files"] == {}
    assert result["omissions"][0]["reason"] == "SIZE_LIMIT"


@pytest.mark.parametrize("byte_count", [23999, 24000, 24001, 35713])
def test_recovered_baseline_code_keeps_existing_size_boundary(tmp_path, byte_count):
    """Recovering a historical hash does not bypass cost limits or imply a score."""
    run = tmp_path / "run"
    run.mkdir()
    code = tmp_path / "p1/product_baseline/tests/test_controller.py"
    code.parent.mkdir(parents=True)
    code.write_bytes(b"#" + b"x" * (byte_count - 1))
    execution = {"environment_precheck": {"test_file": "test_controller.py", "test_sha256": digest(code)}}
    before = code.read_bytes()
    result = execution_support(run, tmp_path, execution)
    label = "pinned/product_baseline/tests/test_controller.py"
    if byte_count <= 24000:
        assert result["files"][label]["content"].encode("utf-8") == before
        assert result["omissions"] == []
    else:
        assert result["files"] == {}
        assert result["omissions"] == [{"file": label, "reason": "SIZE_LIMIT", "sha256": digest(code)}]
    assert code.read_bytes() == before


def test_html_or_unrelated_file_not_collected_as_log_or_code(tmp_path):
    run, execution, *_ = saved_run(tmp_path)
    path = run / "page.html"
    path.write_text("<html>private product</html>", encoding="utf-8")
    row = execution["candidate_results"][0]
    row.update(stdout_file="page.html", test_file="page.html", test_sha256=digest(path))
    row["evidence_sha256"]["page.html"] = digest(path)
    result = execution_support(run, tmp_path, execution)
    assert result["files"] == {}
    assert {r["reason"] for r in result["omissions"]} == {"UNSUPPORTED_LOG_TYPE", "UNSUPPORTED_CODE_TYPE"}


@pytest.mark.parametrize("reverse", [False, True])
def test_support_checks_each_hash_before_deduplication(tmp_path, reverse):
    run, execution, *_ = saved_run(tmp_path)
    row = execution["candidate_results"][0]
    other = deepcopy(row)
    other["test_id"] = "OTHER"
    other["evidence_sha256"]["stdout.txt"] = "0" * 64
    rows = [row, other]
    result = execution_support(run, tmp_path, {"candidate_results": rows[::-1] if reverse else rows})
    assert "stdout.txt" not in result["files"]
    assert any(o.get("reason") == "MISSING_OR_UNVERIFIED" for o in result["omissions"])


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("alias", ["./a.py", "././a.py"])
def test_aliases_of_same_file_cannot_hide_conflicting_hash(tmp_path, reverse, alias):
    (tmp_path / "folder").mkdir()
    code = tmp_path / "a.py"
    code.write_text("assert True", encoding="utf-8")
    rows = [dict(test_file="a.py", test_sha256=digest(code)),
            dict(test_file=alias, test_sha256="0" * 64)]
    result = execution_support(tmp_path, tmp_path, {"candidate_results": rows[::-1] if reverse else rows})
    assert result["files"] == {}
    assert any(o["reason"] == "MISSING_OR_UNVERIFIED" for o in result["omissions"])


@pytest.mark.parametrize("reverse", [False, True])
def test_invalid_file_does_not_consume_budget_before_validation_finishes(tmp_path, reverse):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_bytes(b"#" * 23000)
    b.write_bytes(b"#" * 20000)
    rows = [dict(test_file="a.py", test_sha256=digest(a)),
            dict(test_file="b.py", test_sha256=digest(b)),
            dict(test_file="a.py", test_sha256="0" * 64)]
    result = execution_support(tmp_path, tmp_path, {"candidate_results": rows[::-1] if reverse else rows})
    assert list(result["files"]) == ["b.py"]
    assert all(o["reason"] != "SIZE_LIMIT" for o in result["omissions"])


def test_valid_aliases_and_budget_selection_are_order_independent(tmp_path):
    rows = []
    for name in ("b.py", "a.py"):
        code = tmp_path / name
        code.write_bytes(b"#" * 23000)
        rows.extend([dict(test_file=name, test_sha256=digest(code)),
                     dict(test_file="./" + name, test_sha256=digest(code))])
    first = execution_support(tmp_path, tmp_path, {"candidate_results": rows})
    assert first == execution_support(tmp_path, tmp_path, {"candidate_results": rows[::-1]})
    assert list(first["files"]) == ["a.py"]
    assert len(first["omissions"]) == 1


@pytest.mark.parametrize("mode", ["trial_only", "with_validation", "without_manifest", "root_layout",
                                  "changed_log", "changed_trial", "manifest_conflict", "unsafe_log"])
def test_trial_logs_collected_with_origin_hash_and_stage_preserved(tmp_path, mode):
    run = tmp_path / "run"
    folder = run if mode == "root_layout" else run / "agent3_candidates/TC-ANY"
    log = folder / "evidence/TC-ANY/trial-stdout.txt"
    log.parent.mkdir(parents=True)
    log.write_text("observed wrong mode; original state restored", encoding="utf-8")
    label = log.relative_to(run).as_posix()
    trial = dict(outcome="PRODUCT_MISMATCH_CANDIDATE", exit_code=1,
                 stdout_file=log.name, evidence_sha256={log.name: digest(log)})
    if mode == "unsafe_log":
        trial["stdout_file"] = "../../../private.txt"
    write(folder / "agent3_trial.json", trial)
    manifest = dict(tc_id="TC-ANY", trial_file="agent3_trial.json",
                    trial_sha256=digest(folder / "agent3_trial.json"),
                    trial_evidence_sha256=trial["evidence_sha256"])
    if mode == "changed_trial":
        manifest["trial_sha256"] = "0" * 64
    if mode == "manifest_conflict":
        manifest["trial_evidence_sha256"] = {log.name: "0" * 64}
    if mode != "without_manifest":
        write(folder / "agent3_manifest.json", manifest)
    if mode == "changed_log":
        log.write_text("changed evidence", encoding="utf-8")
    write(folder / "agent3_automation_plan.json", {"tc_id": "TC-ANY"})
    if mode == "with_validation":
        write(run / "validation_execution.json", {"candidate_results": [dict(stdout_file=label,
              evidence_sha256={label: digest(log)})]})
    (run / "srs_snapshot.md").write_text("local test", encoding="utf-8")
    spec = FlowSpec(case_id="LOCAL", request="request.json", oracle="oracle.json",
                    generation_model="offline", judge_model="offline")
    def collect():
        return collect_packets(run, tmp_path, spec,
            {"request": {}, "oracle": {"status": "DRAFT"}, "target_commit": "local"}, [])["A3"]
    if mode == "unsafe_log":
        with pytest.raises(ValueError):
            collect()
        return
    packet = collect()
    support = packet.sources["execution"]["verified_support"]
    assert packet.sources["execution"][(folder / "agent3_trial.json").relative_to(run).as_posix()]["outcome"] == trial["outcome"]
    assert ("validation_execution.json" in packet.sources["execution"]) == (mode == "with_validation")
    if mode in {"changed_log", "changed_trial", "manifest_conflict"}:
        assert label not in support["files"]
        assert support["omissions"]
    else:
        assert list(support["files"]) == [label]
        assert support["files"][label]["content"] == log.read_text(encoding="utf-8")
        assert support["omissions"] == []


@pytest.mark.parametrize("kind", ["valid", "changed", "oversized", "unreferenced", "manifest_only", "no_execution", "conflict"])
@pytest.mark.parametrize("layout", ["root", "nested"])
def test_candidate_code_uses_one_verified_collection_path(tmp_path, kind, layout):
    run, execution, *_ = saved_run(tmp_path)
    folder = run if layout == "root" else run / "agent3_candidates/TC-X"
    code = folder / "candidates/test_any.py"
    code.parent.mkdir(parents=True)
    code.write_text("def test_any():\n    assert True\n" if kind != "oversized" else "#" + "x" * 24000,
                    encoding="utf-8")
    expected = digest(code)
    write(folder / "agent3_automation_plan.json", {"tc_id": "TC-X"})
    if kind in {"manifest_only", "no_execution", "conflict"}:
        write(folder / "agent3_manifest.json", {"candidate_file": code.name, "candidate_sha256": expected})
    if kind not in {"manifest_only", "no_execution", "unreferenced"}:
        execution["candidate_results"][0].update(test_file=code.relative_to(run).as_posix(),
            test_sha256="0" * 64 if kind in {"changed", "conflict"} else expected)
    write(run / "validation_execution.json", execution)
    if kind == "no_execution":
        (run / "validation_execution.json").unlink()
    (run / "srs_snapshot.md").write_text("local test", encoding="utf-8")
    spec = FlowSpec(case_id="LOCAL", request="request.json", oracle="oracle.json",
                    generation_model="offline", judge_model="offline")
    packet = collect_packets(run, tmp_path, spec,
        {"request": {}, "oracle": {"status": "DRAFT"}, "target_commit": "local"}, [])["A3"]
    compiled = packet.sources["trace"]["code_artifacts"]
    label = code.relative_to(run).as_posix()
    if kind in {"valid", "manifest_only", "no_execution", "oversized"}:
        assert compiled[label] == dict(sha256=expected, bytes=code.stat().st_size, delivery="LOCAL_ONLY_CODE")
    else:
        assert label not in compiled
    assert code.read_text(encoding="utf-8") not in preview(packet, "offline")["request"]["input"][1]["content"]
    assert "A3-03" not in {r.criterion_id for r in packet.rubric}
    assert digest(code) == expected


@pytest.mark.parametrize("sizes, expected_count", [([23999], 1), ([24000], 1), ([24001], 0), ([21000, 21000], 1)])
def test_candidate_collection_retains_file_and_total_limits(tmp_path, sizes, expected_count):
    run, execution, *_ = saved_run(tmp_path)
    execution["candidate_results"] = []
    for i, size in enumerate(sizes):
        folder = run / f"agent3_candidates/TC-{i}"
        code = folder / "candidates/test_case.py"
        code.parent.mkdir(parents=True)
        code.write_bytes(b"#" + b"x" * (size - 1))
        write(folder / "agent3_automation_plan.json", {"tc_id": f"TC-{i}"})
        write(folder / "agent3_manifest.json", {"candidate_file": code.name, "candidate_sha256": digest(code)})
        execution["candidate_results"].append({"test_file": code.relative_to(run).as_posix(), "test_sha256": digest(code)})
    write(run / "validation_execution.json", execution)
    (run / "srs_snapshot.md").write_text("local", encoding="utf-8")
    spec = FlowSpec(case_id="LOCAL", request="request.json", oracle="oracle.json",
                    generation_model="offline", judge_model="offline")
    packet = collect_packets(run, tmp_path, spec,
        {"request": {}, "oracle": {"status": "DRAFT"}, "target_commit": "local"}, [])["A3"]
    assert len(packet.sources["trace"]["code_artifacts"]) == len(sizes)
    # Local code inspection keeps its old size limits; AI code metadata does not
    # use the log-text budget and is not reported as accidentally missing code.
    local = execution_support(run, tmp_path, execution)
    assert len(local["files"]) == expected_count
    if expected_count < len(sizes):
        assert any(o["reason"] == "SIZE_LIMIT" for o in local["omissions"])
    preview(packet, "offline")


@pytest.mark.parametrize("name", ["../outside.py", "C:/outside.py", "folder\\outside.py"])
def test_manifest_code_reference_cannot_escape_candidate_directory(tmp_path, name):
    run, *_ = saved_run(tmp_path)
    write(run / "agent3_candidates/TC-X/agent3_manifest.json", {"candidate_file": name, "candidate_sha256": "0" * 64})
    spec = FlowSpec(case_id="LOCAL", request="request.json", oracle="oracle.json",
                    generation_model="offline", judge_model="offline")
    with pytest.raises(ValueError):
        collect_packets(run, tmp_path, spec, {}, [])


def test_collected_sensitive_text_is_blocked_before_api(tmp_path):
    run, execution, analysis, report = saved_run(tmp_path)
    log = run / "stdout.txt"
    log.write_text("OPENAI_API_KEY=" + "sk-" + "x" * 40, encoding="utf-8")
    execution["candidate_results"][0]["evidence_sha256"]["stdout.txt"] = digest(log)
    write(run / "validation_execution.json", execution)
    (run / "srs_snapshot.md").write_text("시험 기준", encoding="utf-8")
    spec = FlowSpec(case_id="LOCAL", request="request.json", oracle="oracle.json", generation_model="offline", judge_model="offline")
    packets = collect_packets(run, tmp_path, spec, {"request":{}, "oracle":{"status":"DRAFT"}, "target_commit":"test"}, [])
    with pytest.raises(ValueError):
        preview(packets["A4"], "offline")


@pytest.mark.parametrize("unsafe", ["../outside.txt", "folder/../stdout.txt", "C:/secret.txt", "file:stream"])
def test_evidence_paths_cannot_escape_run(tmp_path, unsafe):
    run, execution, *_ = saved_run(tmp_path)
    execution["candidate_results"][0]["stdout_file"] = unsafe
    with pytest.raises(ValueError):
        execution_support(run, tmp_path, execution)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("alias", ["A.py", "a.PY"])
def test_windows_case_alias_conflict_is_also_quarantined(tmp_path, reverse, alias):
    from pathlib import Path
    if Path("a.py") != Path("A.py"):
        pytest.skip("Case-insensitive Windows path contract")
    code = tmp_path / "a.py"
    code.write_text("assert True", encoding="utf-8")
    rows = [dict(test_file="a.py", test_sha256=digest(code)),
            dict(test_file=alias, test_sha256="0" * 64)]
    assert not execution_support(tmp_path, tmp_path, {"candidate_results": rows[::-1] if reverse else rows})["files"]


@pytest.mark.parametrize("fault", ["missing", "oversized", "sensitive", "conflicting_validation"])
def test_trial_log_protections_survive_collection(tmp_path, fault):
    run = tmp_path / "run"
    folder = run / "agent3_candidates/TC-OTHER"
    log = folder / "evidence/TC-OTHER/trial-stdout.txt"
    log.parent.mkdir(parents=True)
    log.write_text("x" * 24001 if fault == "oversized" else
                   "OPENAI_API_KEY=" + "sk-" + "x" * 40 if fault == "sensitive" else "observed ON", encoding="utf-8")
    expected = digest(log)
    label = log.relative_to(run).as_posix()
    write(folder / "agent3_trial.json", {"stdout_file": log.name, "evidence_sha256": {log.name: expected}})
    write(folder / "agent3_automation_plan.json", {"tc_id": "TC-OTHER"})
    (run / "srs_snapshot.md").write_text("local", encoding="utf-8")
    if fault == "missing":
        log.unlink()
    if fault == "conflicting_validation":
        write(run / "validation_execution.json", {"candidate_results": [dict(stdout_file=label,
              evidence_sha256={label: "0" * 64})]})
    spec = FlowSpec(case_id="LOCAL", request="request.json", oracle="oracle.json",
                    generation_model="offline", judge_model="offline")
    packet = collect_packets(run, tmp_path, spec,
        {"request": {}, "oracle": {"status": "DRAFT"}, "target_commit": "local"}, [])["A3"]
    support = packet.sources["execution"]["verified_support"]
    if fault == "sensitive":
        with pytest.raises(ValueError):
            preview(packet, "offline")
    else:
        assert not support["files"]
        assert any(o["reason"] == ("SIZE_LIMIT" if fault == "oversized" else "MISSING_OR_UNVERIFIED")
                   for o in support["omissions"])


def _expanded_source(source):
    def expand(value, stack=()):
        if isinstance(value, dict) and set(value) == {"evidence_reference"}:
            path = tuple(value["evidence_reference"])
            assert path not in stack
            target = source
            for part in path:
                target = target[int(part)] if isinstance(target, list) else target[part]
            return expand(target, stack + (path,))
        if isinstance(value, dict):
            return {k: expand(v, stack) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v, stack) for v in value]
        return value
    return expand(source)


@pytest.mark.parametrize("layout", ["root", "nested"])
def test_all_stage_context_and_status_records_reach_owner(tmp_path, layout):
    from eval_harness.full_flow import comparison_snapshot
    run = tmp_path / "RUN-TRANSFER"
    folder = run if layout == "root" else run / "agent3_candidates/TC-Z"
    write(run / "request.json", {"description": "요청 원문"})
    (run / "srs_snapshot.md").write_text("SRS 원문", encoding="utf-8")
    write(run / "run_manifest.json", {"run_id": run.name, "handoff_status": "CONTINUE"})
    write(run / "orchestrator_manifest.json", {"run_id": run.name, "status": "PARTIAL"})
    write(run / "agent1_change_analysis.json", {"information_gaps": []})
    write(run / "agent2_test_design.json", {"test_cases": [{"tc_id": "TC-Z"}]})
    write(run / "approved_regression_catalog.json", {"approved_assets": [{"tc_id": "TC-OLD", "test_case_json": "당시 상세 TC"}]})
    write(run / "agent2_manifest.json", {"run_id": run.name,
          "approved_regression_catalog_sha256": digest(run / "approved_regression_catalog.json")})
    write(run / "validation_manifest.json", {"run_id": run.name, "stage": "VALIDATION_EXECUTION"})
    write(folder / "agent3_automation_plan.json", {"tc_id": "TC-Z", "actions": ["원문 행동"]})
    write(folder / "agent3_eligibility.json", {"tc_id": "TC-Z", "status": "ELIGIBLE"})
    write(folder / "agent3_model_input_preview.json", {"tc_id": "TC-Z", "preview": "입력 미리보기이지 실제 호출 아님"})
    write(run / "agent3_selection.json", {"status": "SELECTED", "tc_ids": ["TC-Z"]})
    write(run / "agent3_run_summary.json", {"status": "PARTIAL", "candidates": [{"tc_id": "TC-OTHER", "status": "EXCLUDED"}]})
    for role in (1, 2, 3):
        parent = folder if role == 3 else run
        write(parent / f"agent{role}_grounding_review.json", {"verdict": "FAIL", "reason": f"최종 검토 {role}"})
        write(parent / f"agent{role}_error.json", {"stage": role, "message": "원본 오류"})
    reference_context = {"catalog": [{"tc_id": "TC-BASE", "covered_behaviors": ["기본 TC 범위"]}],
                         "compiler_recovery_facts": {"owner": "executor", "restoration": "before preparation"}}
    write(run / "agent2_grounding_input_attempt_2.json", {"context": {**reference_context, "not_role_input": "excluded"}, "items": ["excluded duplicate body"]})
    spec = FlowSpec(case_id="TRANSFER", request="request.json", oracle="oracle.json", generation_model="offline", judge_model="offline")
    packets = collect_packets(run, tmp_path, spec,
        {"request": read(run / "request.json"), "oracle": {"status": "DRAFT"}, "target_commit": "local"}, [])
    for role in ("A1", "A2", "A3"):
        source = _expanded_source(packets[role].sources)
        trace = source["trace"]["checkpoints_and_attempts"]
        prefix = "agent3_candidates/TC-Z/" if role == "A3" and layout == "nested" else ""
        assert trace[prefix + f"agent{role[-1]}_grounding_review.json"]["verdict"] == "FAIL"
        assert trace[prefix + f"agent{role[-1]}_error.json"]["message"] == "원본 오류"
        assert trace["orchestrator_manifest.json"]["status"] == "PARTIAL"
        if role in ("A2", "A3"):
            assert source["context"]["existing_tc_catalog"]["content"] == read(run / "approved_regression_catalog.json")
            assert source["context"]["existing_tc_catalog"]["status"] == "HASH_VERIFIED"
        if role == "A2":
            record = trace["agent2_grounding_input_attempt_2.json"]
            assert record["retained_reference_context"] == reference_context
            assert "items" not in record and "not_role_input" not in record["retained_reference_context"]
            assert record["raw_artifact_sha256"] == digest(run / "agent2_grounding_input_attempt_2.json")
        assert source["trace"]["evidence_delivery"]["actual_model_requests"] == "UNAVAILABLE_NOT_PROOF_OF_NO_CALL"
        preview(packets[role], "offline")
    a3 = _expanded_source(packets["A3"].sources)
    assert a3["trace"]["checkpoints_and_attempts"]["agent3_run_summary.json"]["candidates"][0]["status"] == "EXCLUDED"
    assert "A3" in comparison_snapshot(run)


@pytest.mark.parametrize("filename", ["agent1_error.json", "agent2_error.json", "agent3_eligibility.json", "agent3_selection.json"])
def test_status_only_stage_is_collected_but_not_a_final_plan(tmp_path, filename):
    run = tmp_path / "RUN-STATUS"
    write(run / filename, {"status": "EXCLUDED", "reason": "지원할 수 없는 동작"})
    (run / "srs_snapshot.md").write_text("기준", encoding="utf-8")
    role = "A" + filename[5]
    spec = FlowSpec(case_id="STATUS", request="request.json", oracle="oracle.json", generation_model="offline", judge_model="offline")
    packets = collect_packets(run, tmp_path, spec, {"request": {}, "oracle": {"status": "DRAFT"}, "target_commit": "local"}, [])
    assert list(packets) == [role]
    assert packets[role].sources["trace"]["output_state"] == "STATUS_ONLY_NO_FINAL_ARTIFACT"
    assert packets[role].sources["artifact"][filename]["status"] == "EXCLUDED"


@pytest.mark.parametrize("fault", ["request", "run", "oversize", "catalog_hash", "catalog_missing", "unknown"])
def test_collection_does_not_silently_discard_or_substitute_input(tmp_path, fault):
    run = tmp_path / "RUN-SAFE"
    write(run / "request.json", {"description": "original"})
    write(run / "agent2_test_design.json", {"test_cases": []})
    (run / "srs_snapshot.md").write_text("local", encoding="utf-8")
    write(run / "approved_regression_catalog.json", {"approved_assets": []})
    sha = digest(run / "approved_regression_catalog.json")
    write(run / "agent2_manifest.json", {"run_id": "OTHER" if fault == "run" else run.name,
          "approved_regression_catalog_sha256": "0" * 64 if fault == "catalog_hash" else sha})
    if fault == "catalog_missing":
        (run / "approved_regression_catalog.json").unlink()
    elif fault == "oversize":
        write(run / "agent2_test_design.json", {"body": "x" * 2_000_000})
    elif fault == "unknown":
        write(run / "new_output.json", {"unknown": "must remain visible as not selected"})
    spec = FlowSpec(case_id="SAFE", request="request.json", oracle="oracle.json", generation_model="offline", judge_model="offline")
    plan = {"request": {"description": "changed" if fault == "request" else "original"},
            "oracle": {"status": "DRAFT"}, "target_commit": "local"}
    if fault != "unknown":
        with pytest.raises(ValueError):
            collect_packets(run, tmp_path, spec, plan, [])
    else:
        packet = collect_packets(run, tmp_path, spec, plan, [])["A2"]
        assert packet.sources["trace"]["evidence_delivery"]["json_files"]["new_output.json"] == "LOCAL_ONLY_NOT_ROLE_INPUT"


@pytest.mark.parametrize("fault", [None, "run", "stage", "hash", "source", "path", "size"])
def test_human_review_document_is_linked_guidance_not_approval(tmp_path, fault):
    from eval_harness.full_flow import human_review_support
    run = tmp_path / "RUN-HUMAN"
    write(run / "final_report.json", {"run_id": run.name})
    write(run / "validation_execution.json", {"run_id": run.name})
    doc = run / "사람_최종_검토.md"
    doc.write_text("x" * 24001 if fault == "size" else "사람이 등록 여부를 결정하세요.", encoding="utf-8")
    manifest = {"run_id": "OTHER" if fault == "run" else run.name,
                "stage": "APPROVAL" if fault == "stage" else "HUMAN_FINAL_REVIEW_DOCUMENT",
                "document_file": "../secret.md" if fault == "path" else doc.name,
                "document_sha256": "0" * 64 if fault == "hash" else digest(doc),
                "final_report_sha256": "0" * 64 if fault == "source" else digest(run / "final_report.json"),
                "validation_execution_sha256": digest(run / "validation_execution.json")}
    write(run / "사람_최종_검토_manifest.json", manifest)
    if fault and fault != "size":
        with pytest.raises(ValueError):
            human_review_support(run)
    else:
        result = human_review_support(run)
        assert result["status"] == ("UNAVAILABLE" if fault == "size" else "LINKED_DOCUMENT_NOT_APPROVAL")
        assert ("content" in result) == (fault is None)


def test_linked_json_is_not_silently_dropped(tmp_path, monkeypatch):
    from pathlib import Path
    run = tmp_path / "RUN-LINK"
    path = run / "agent1_change_analysis.json"
    write(path, {"decision": "CONTINUE"})
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == path or original(p))
    spec = FlowSpec(case_id="LINK", request="request.json", oracle="oracle.json", generation_model="offline", judge_model="offline")
    with pytest.raises(ValueError, match="Linked evidence"):
        collect_packets(run, tmp_path, spec, {}, [])


def test_new_rule_does_not_rewrite_legacy_four_point_fail():
    p = packet().model_copy(update={"scoring_profile":"score-0-5-v1"})
    assert validate_judgment(p, response(4, "FAIL")).judgments[0].score == 4
    old = results()
    for result in old.values():
        result["scoring_profile"] = "score-0-5-v1"
    old["A1"]["judgments"][0].update(score=4, verdict="FAIL")
    before = deepcopy(old)
    assert full_scorecard(old)["criteria"][0]["score"] == 4
    assert old == before
    assert not repeat_scores([full_scorecard(old), full_scorecard(results())])["comparable"]


def test_shared_scoring_instructions_do_not_rewrite_evidence():
    import json
    from eval_harness.openai_judge import evidence_catalog
    p = packet()
    before = deepcopy(p.model_dump())
    payload = json.loads(preview(p, "offline")["request"]["input"][1]["content"])
    assert payload["sources"] == evidence_catalog(p)[0]
    assert payload["rubric"] == [r.model_dump() for r in p.rubric]
    assert p.model_dump() == before
    assert set(payload["scoring_scale"]) == set("012345")
    legacy = p.model_copy(update={"scoring_profile":"score-0-5-v1"})
    assert "scoring_rubric" in json.loads(preview(legacy, "offline")["request"]["input"][1]["content"])


def test_one_rated_item_is_explicitly_partial_and_not_repeatability():
    rows = [{"criterion_id":"one", "score":5, "verdict":"PASS"}] + [
        {"criterion_id":str(i), "score":None, "verdict":"REVIEW"} for i in range(25)]
    summary = score_summary(rows)
    assert summary["average_5"] == 5 and summary["evaluated"] == 1 and summary["unevaluated"] == 25
    assert not repeat_scores([full_scorecard(results())])["comparable"]


def test_fullflow_only_sends_semantic_a4_items(tmp_path):
    run, *_ = saved_run(tmp_path)
    (run / "srs_snapshot.md").write_text("시험 기준", encoding="utf-8")
    spec = FlowSpec(case_id="LOCAL", request="request.json", oracle="oracle.json", generation_model="offline", judge_model="offline")
    packets = collect_packets(run, tmp_path, spec, {"request":{}, "oracle":{"status":"DRAFT"}, "target_commit":"test"}, [])
    assert {r.criterion_id for r in packets["A4"].rubric} == {"C01", "C02", "A4-02", "A4-04"}
    assert packets["A4"].scoring_profile == SCORE_VERSION
    assert preview(packets["A4"], "offline")["request"]["store"] is False
