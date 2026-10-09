"""Offline contract tests; synthetic mutations do not establish model quality."""
from copy import deepcopy
import json

import pytest

from eval_harness.execution_observations import compare, grade_run, matches, VERSION, OBSERVED, COMPLETE
from eval_harness.v2_recorded import write, digest
from eval_harness.detailed_rubric import apply_role_profile, full_scorecard
from test_scoring import packet, results


def sample(value="HEAT", actual=None):
    assertion = dict(result_id="ER-1", strategy="INTERNAL_VALUE_EQUALS", expected_value=value,
                     observation_layer="INTERNAL", selector="window.__vccs.devices[0].mode", after_action_id="ACT-1")
    case = dict(tc_id="TC-X", expected_results=[dict(result_id="ER-1")])
    plan = dict(tc_id="TC-X", target_device_id=1, actions=[dict(action_id="ACT-1", phase="TEST")], assertions=[assertion])
    identity = dict(version=VERSION, run_id="RUN-local", tc_id="TC-X")
    event = dict(**identity, target_device_id=1, assertion=deepcopy(assertion),
                 actual=value if actual is None else actual, matched=actual is None or actual == value)
    return case, plan, event, identity


@pytest.mark.parametrize("actual,expected", [
    (True, 1), (1, True), (False, 0), (True, True), (None, False),
    (21, 21.0), ("21", 21), ("HEAT", "heat"), ("MED", "MED"),
    ({"locked": 1}, {"locked": True}), ({"setTemp": 21}, {"setTemp": 21.0}),
    ({"values": [True, 21]}, {"values": [1, 21.0]}), ([False, 21], [False, 21.0])])
def test_p1_emitted_comparator_agrees_with_independent_p2_grader(actual, expected):
    import ast
    import os
    from pathlib import Path
    from eval_harness.execution_observations import equal
    root = os.environ.get("QA_P1_TEST_ROOT")
    if not root:
        pytest.skip("Set QA_P1_TEST_ROOT for cross-project offline comparison")
    tree = ast.parse((Path(root) / "src/qa_pipeline_agent3.py").read_text(encoding="utf-8-sig"))
    helper = next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name)
                     and t.id == "_TYPED_VALUE_COMPARISON_HELPER" for t in node.targets))
    scope = {}
    exec(helper, scope)
    assert scope["_qa_values_equal"](actual, expected) == equal(actual, expected)


def log_of(event, end):
    return OBSERVED + json.dumps(event) + "\n" + COMPLETE + json.dumps(end)


@pytest.mark.parametrize("value", ["OPERATION", "HEAT", "AUTO", "LOW", 21, 30, True, False])
@pytest.mark.parametrize("product_match", [True, False])
def test_product_failure_is_not_execution_fidelity_failure(value, product_match):
    case, plan, event, end = sample(value, None if product_match else "DIFFERENT")
    before = deepcopy((case, plan, event))
    result = compare(case, plan, log_of(event, end), run_id="RUN-local",
                     outcome="PASS" if product_match else "PRODUCT_MISMATCH_CANDIDATE")
    assert result["score"] == 5
    assert (case, plan, event) == before


@pytest.mark.parametrize("mutation", ["target", "expected", "missing", "duplicate", "extra", "matched", "run", "tc", "anchor", "selector", "outcome", "end"])
def test_complete_but_inconsistent_records_fail(mutation):
    case, plan, event, end = sample()
    outcome = "PASS"
    if mutation == "target": event["target_device_id"] = 2
    if mutation == "expected": event["assertion"]["expected_value"] = "COOL"
    if mutation == "missing": case["expected_results"].append(dict(result_id="ER-2"))
    if mutation == "extra": event["assertion"]["result_id"] = "OTHER"
    if mutation == "matched": event["matched"] = False
    if mutation == "run": event["run_id"] = "OTHER"
    if mutation == "tc": plan["tc_id"] = "OTHER"
    if mutation == "anchor": event["assertion"]["after_action_id"] = None
    if mutation == "selector": event["assertion"]["selector"] = "wrong.target"
    if mutation == "outcome": outcome = "PRODUCT_MISMATCH_CANDIDATE"
    if mutation == "end": end["tc_id"] = "OTHER"
    log = log_of(event, end)
    if mutation == "duplicate": log += "\n" + OBSERVED + json.dumps(event)
    assert compare(case, plan, log, run_id="RUN-local", outcome=outcome)["score"] == 0


@pytest.mark.parametrize("kind", ["old", "partial", "invalid", "unknown", "interrupted"])
def test_missing_or_unsupported_evidence_is_not_invented_as_a_score(kind):
    case, plan, event, end = sample()
    outcome = "PASS"
    if kind == "unknown":
        plan["assertions"][0]["strategy"] = event["assertion"]["strategy"] = "TOAST_BLOCKING"
    if kind == "interrupted": outcome = "EXECUTION_ERROR"
    log = log_of(event, end)
    if kind == "old": log = "old assertion success"
    if kind == "partial": log = log.splitlines()[0]
    if kind == "invalid": log = OBSERVED + "{bad json}\n" + log.splitlines()[1]
    result = compare(case, plan, log, run_id="RUN-local", outcome=outcome)
    assert result["score"] is None and result["verdict"] == "REVIEW"


@pytest.mark.parametrize("strategy,fields,actual,ok", [
    ("UI_TEMPERATURE", dict(expected_number=21), 21.0, True),
    ("INTERNAL_SET_TEMP", dict(expected_number=1), True, False),
    ("UI_TEXT_CONTAINS", dict(expected_text="난방"), "난방 모드", True),
    ("UI_VALUE_EQUALS", dict(expected_value=21), "21", True),
    ("UI_CHECKED_EQUALS", dict(expected_value=True), 1, False),
    ("UI_ENABLED_EQUALS", dict(expected_value=False), False, True),
    ("CONTROLLER_UI_FIELDS_EQUALS", dict(expected_fields=[dict(field_name="mode", expected_value="HEAT")]), {"mode":"HEAT"}, True),
    ("INTERNAL_DEVICE_FIELDS_EQUALS", dict(expected_fields=[dict(field_name="locked", expected_value=False)]), {"locked":0}, False),
    ("TOAST_VISIBLE", {}, True, True),
    ("CONTROLS_DISABLED", {}, [False, False], True),
    ("DISABLED_TEMPERATURE_TEXT", {}, "--- °C", True),
])
def test_comparison_uses_values_not_scenario_words(strategy, fields, actual, ok):
    assert matches(dict(strategy=strategy, **fields), actual) is ok


def saved_sample(root):
    run = root / "RUN-local"
    case, plan, event, end = sample()
    folder = run / "agent3_candidates/TC-X"
    write(run / "agent2_test_design.json", dict(test_cases=[case]))
    write(run / "agent2_manifest.json", dict(run_id=run.name))
    write(folder / "agent3_automation_plan.json", plan)
    log = folder / "evidence/TC-X/stdout.txt"
    log.parent.mkdir(parents=True)
    log.write_text(log_of(event, end), encoding="utf-8")
    code = folder / "candidates/test_tc.py"
    code.parent.mkdir()
    code.write_text("# synthetic: never executed", encoding="utf-8")
    trial = dict(outcome="PASS", stdout_file=log.name, evidence_sha256={log.name:digest(log)})
    write(folder / "agent3_trial.json", trial)
    write(folder / "agent3_manifest.json", dict(run_id=run.name, tc_id=case["tc_id"],
        source_agent2_design_sha256=digest(run / "agent2_test_design.json"),
        source_agent2_manifest_sha256=digest(run / "agent2_manifest.json"),
        automation_plan_sha256=digest(folder / "agent3_automation_plan.json"),
        candidate_file=code.name, candidate_sha256=digest(code),
        trial_file="agent3_trial.json", trial_sha256=digest(folder / "agent3_trial.json"),
        trial_evidence_sha256=trial["evidence_sha256"]))
    return run, folder


@pytest.mark.parametrize("changed", [None, "log", "plan", "trial", "code", "design"])
def test_grader_checks_chain_before_assigning_fidelity_score(tmp_path, changed):
    run, folder = saved_sample(tmp_path)
    paths = dict(log=folder / "evidence/TC-X/stdout.txt", plan=folder / "agent3_automation_plan.json",
                 trial=folder / "agent3_trial.json", code=folder / "candidates/test_tc.py", design=run / "agent2_test_design.json")
    if changed:
        with paths[changed].open("a", encoding="utf-8") as stream:
            stream.write(" ")
    result = grade_run(run)
    assert result["score"] == (5 if changed is None else None)


def test_ai_profile_excludes_program_grades_and_historical_score_survives():
    p = apply_role_profile(packet().model_copy(update={"agent_id":"A3"}))
    assert "A3-03" not in {r.criterion_id for r in p.rubric}
    old = results()
    old["A3"]["rubric_version"] = "role-rubric-2.4"
    local = dict(status="COMPLETED", scoring_profile="score-0-5-v2", rubric_version="role-rubric-2.4", judgments=[])
    card = full_scorecard(old, local)
    row = next(r for r in card["criteria"] if r["criterion_id"] == "A3-03")
    assert row["score"] == 5 and row["evaluation_method"] == "AI"


@pytest.mark.parametrize("layout", ["root", "nested"])
@pytest.mark.parametrize("fault", [None, "run", "stage", "tc", "duplicate", "hash", "path", "wrong_manifest", "log", "plan"])
def test_recorded_candidate_location_uses_verified_reference(tmp_path, layout, fault):
    import shutil
    from eval_harness.v2_recorded import read
    run, folder = saved_sample(tmp_path)
    if layout == "root":
        for path in folder.iterdir():
            destination = run / path.name
            if path.is_dir():
                shutil.copytree(path, destination)
            else:
                shutil.copyfile(path, destination)
        folder = run  # Keep the old valid copy: invalid explicit links must not fall back.
    manifest = folder / "agent3_manifest.json"
    link = dict(tc_id="TC-X", agent3_manifest_file=manifest.relative_to(run).as_posix(),
                agent3_manifest_sha256=digest(manifest))
    wrapper = dict(run_id=run.name, stage="VALIDATION_EXECUTION", source_agent3_artifacts=[link])
    if fault == "run": wrapper["run_id"] = "OTHER"
    if fault == "stage": wrapper["stage"] = "OTHER"
    if fault == "tc": link["tc_id"] = "OTHER"
    if fault == "duplicate": wrapper["source_agent3_artifacts"].append(dict(link))
    if fault == "hash": link["agent3_manifest_sha256"] = "0" * 64
    if fault == "path": link["agent3_manifest_file"] = "../outside/agent3_manifest.json"
    if fault == "wrong_manifest":
        alternate = folder / "other.json"
        shutil.copyfile(manifest, alternate)
        link["agent3_manifest_file"] = alternate.relative_to(run).as_posix()
    if fault in {"log", "plan"}:
        path = folder / ("evidence/TC-X/stdout.txt" if fault == "log" else "agent3_automation_plan.json")
        path.write_bytes(path.read_bytes() + b" ")
    write(run / "validation_manifest.json", wrapper)
    result = grade_run(run)
    assert result["score"] == (5 if fault is None else None)
    assert result["verdict"] == ("PASS" if fault is None else "REVIEW")


@pytest.mark.parametrize("reverse", [False, True])
def test_missing_candidate_does_not_erase_another_confirmed_violation(tmp_path, reverse):
    from eval_harness.v2_recorded import read
    run, folder = saved_sample(tmp_path)
    design_path = run / "agent2_test_design.json"
    design = read(design_path)
    design["test_cases"].append(dict(tc_id="TC-MISSING"))
    if reverse:
        design["test_cases"].reverse()
    write(design_path, design)
    log = folder / "evidence/TC-X/stdout.txt"
    case, plan, event, end = sample()
    event["actual"] = "DIFFERENT"  # Still falsely recorded as matched/PASS.
    log.write_text(log_of(event, end), encoding="utf-8")
    trial_path = folder / "agent3_trial.json"
    trial = read(trial_path)
    trial["evidence_sha256"][log.name] = digest(log)
    write(trial_path, trial)
    manifest_path = folder / "agent3_manifest.json"
    manifest = read(manifest_path)
    manifest.update(source_agent2_design_sha256=digest(design_path),
                    trial_sha256=digest(trial_path), trial_evidence_sha256=trial["evidence_sha256"])
    write(manifest_path, manifest)
    assert grade_run(run)["verdict"] == "FAIL"


def with_validation(run, folder, *, actual=None, outcome="PASS"):
    """Independent final execution, retaining the earlier passing candidate log."""
    case, plan, event, end = sample(actual=actual)
    log = run / "validation_evidence/TC-X/stdout.txt"
    log.parent.mkdir(parents=True)
    log.write_text(log_of(event, end), encoding="utf-8")
    code = folder / "candidates/test_tc.py"
    row = dict(test_id="TC-X", source="NEW_AUTOMATION_CANDIDATE",
               status="PASSED" if outcome == "PASS" else "ASSERTION_FAILED",
               source_outcome=outcome, test_file=code.relative_to(run).as_posix(),
               test_sha256=digest(code), stdout_file=log.relative_to(run).as_posix(),
               evidence_sha256={log.relative_to(run).as_posix(): digest(log)})
    execution = dict(run_id=run.name, stage="VALIDATION_EXECUTION", candidate_results=[row])
    write(run / "validation_execution.json", execution)
    manifest = folder / "agent3_manifest.json"
    link = dict(tc_id="TC-X", agent3_manifest_file=manifest.relative_to(run).as_posix(),
                agent3_manifest_sha256=digest(manifest), validation_candidate_sha256=digest(code))
    wrapper = dict(run_id=run.name, stage="VALIDATION_EXECUTION", source_agent3_artifacts=[link],
                   validation_execution_sha256=digest(run / "validation_execution.json"))
    write(run / "validation_manifest.json", wrapper)
    return execution, wrapper, log


@pytest.mark.parametrize("actual,outcome,expected", [
    (None, "PASS", "PASS"),
    ("COOL", "PRODUCT_MISMATCH_CANDIDATE", "PASS"),
    ("COOL", "PASS", "FAIL"),
    (None, "PRODUCT_MISMATCH_CANDIDATE", "FAIL"),
])
def test_final_observations_not_hidden_by_earlier_candidate_pass(tmp_path, actual, outcome, expected):
    run, folder = saved_sample(tmp_path)
    with_validation(run, folder, actual=actual, outcome=outcome)
    grade = grade_run(run)
    assert grade["verdict"] == expected
    assert any(e["file"] == "validation_evidence/TC-X/stdout.txt" for e in grade["evidence"])


@pytest.mark.parametrize("fault", ["missing_log", "changed_log", "missing_execution", "hash", "run", "stage",
                                   "tc", "duplicate", "code_hash", "status", "outside"])
def test_invalid_final_evidence_does_not_fall_back_to_old_pass(tmp_path, fault):
    run, folder = saved_sample(tmp_path)
    execution, wrapper, log = with_validation(run, folder)
    row = execution["candidate_results"][0]
    if fault == "missing_log": log.unlink()
    if fault == "changed_log": log.write_text("changed", encoding="utf-8")
    if fault == "run": execution["run_id"] = "OTHER"
    if fault == "stage": execution["stage"] = "OTHER"
    if fault == "tc": row["test_id"] = "OTHER"
    if fault == "duplicate": execution["candidate_results"].append(dict(row))
    if fault == "code_hash": row["test_sha256"] = "0" * 64
    if fault == "status": row["status"] = "SKIPPED"
    if fault == "outside": row["stdout_file"] = "../outside.txt"
    write(run / "validation_execution.json", execution)
    wrapper["validation_execution_sha256"] = digest(run / "validation_execution.json")
    if fault == "hash": wrapper["validation_execution_sha256"] = "0" * 64
    if fault == "missing_execution": (run / "validation_execution.json").unlink()
    write(run / "validation_manifest.json", wrapper)
    assert grade_run(run)["verdict"] == "REVIEW"
