import json

import pytest
from pydantic import ValidationError

from eval_harness.scenario_oracle import Expectation, evaluate_expectations, select
from eval_harness.v2_recorded import ROOT, load_cases


def specification(**updates):
    data = dict(rule="A3.SCENARIO.VALUE", artifact="plan.json", path=["assertions", "*"],
                operator="contains", expected={"expected_text": "중풍", "after_action_id": "ACT-003"},
                reason="값과 시점을 같은 행에서 확인", basis="요청 원문")
    data.update(updates)
    return Expectation(**data)


@pytest.mark.parametrize("rows,status", [
    ([{"expected_text":"중풍", "after_action_id":"ACT-003", "other":1}], "PASS"),
    ([{"expected_text":"강풍", "after_action_id":"ACT-003"}], "FAIL"),
    ([{"expected_text":"중풍", "after_action_id":"ACT-002"}], "FAIL"),
    ([{"expected_text":"중풍", "after_action_id":"ACT-002"},
      {"expected_text":"강풍", "after_action_id":"ACT-003"}], "FAIL"),
    ([], "FAIL"),
])
def test_correlated_value_and_timing(tmp_path, rows, status):
    (tmp_path/"plan.json").write_text(json.dumps({"assertions":rows}), encoding="utf-8")
    assert evaluate_expectations(tmp_path, [specification()])[0]["status"] == status


@pytest.mark.parametrize("content", ['{', '{}', '{"assertions":null}', '{"assertions":{}}'])
def test_bad_artifact_is_error_not_agent_fail(tmp_path, content):
    (tmp_path/"plan.json").write_text(content, encoding="utf-8")
    assert evaluate_expectations(tmp_path, [specification()])[0]["status"] == "ERROR"


@pytest.mark.parametrize("actual,expected,status", [(True,1,"FAIL"),("18",18,"FAIL"),(18,18,"PASS"),([],[],"PASS")])
def test_exact_types(tmp_path, actual, expected, status):
    (tmp_path/"plan.json").write_text(json.dumps({"value":actual}), encoding="utf-8")
    spec=specification(path=["value"], operator="equals", expected=expected)
    assert evaluate_expectations(tmp_path,[spec])[0]["status"] == status


@pytest.mark.parametrize("updates", [
    {"artifact":"../secret.json"}, {"artifact":"C:/secret.json"}, {"artifact":"plan.txt"},
    {"operator":"eval"}, {"operator":"count","expected":True},
    {"operator":"count","expected":-1}, {"path":[]}, {"rule":"FREEFORM"},
])
def test_reject_unsafe_or_ambiguous_oracle(updates):
    with pytest.raises(ValidationError):
        specification(**updates)


def test_zero_new_tc_is_valid_reuse_only(tmp_path):
    (tmp_path/"plan.json").write_text('{"test_cases":[]}', encoding="utf-8")
    spec=specification(path=["test_cases","*"],operator="count",expected=0)
    assert evaluate_expectations(tmp_path,[spec])[0]["status"] == "PASS"


def test_missing_one_artifact_does_not_cancel_other_rules(tmp_path):
    (tmp_path/"plan.json").write_text('{"value":18}', encoding="utf-8")
    good=specification(path=["value"],operator="equals",expected=18)
    bad=specification(artifact="missing.json")
    assert [c["status"] for c in evaluate_expectations(tmp_path,[bad,good])] == ["ERROR","PASS"]


def test_scenario_dataset_remains_draft_and_has_all_roles():
    cases=load_cases(ROOT/"benchmarks/v2/scenario_v1.jsonl")
    assert len(cases)==3
    assert all(c.oracle_status=="DRAFT" for c in cases)
    roles={e.rule.split('.')[0] for c in cases for e in c.expectations}
    assert roles == {"A1","A2","A3","A4","E2E"}
    assert sum(len(c.expectations) for c in cases)==17


def test_question_loss_visible_in_role_report():
    from eval_harness.agent_rubric import summarize_roles
    result=summarize_roles([dict(rule="E2E.SCENARIO.QUESTION_HANDOFF",status="FAIL",reason="missing")])
    assert next(i for i in result["groups"]["E2E"] if i["criterion_id"]=="E03")["status"]=="FAIL"


def test_integration_records_draft_mismatch_not_pass(tmp_path):
    from test_v2_recorded import sample
    from eval_harness.v2_recorded import evaluate
    case=sample(tmp_path)
    case.expectations=[specification(artifact="final_report.json",path=["recommendation"],
                                   operator="equals",expected="HUMAN_REVIEW")]
    result=evaluate(tmp_path,case)
    assert result["status"]=="FAIL"
    assert result["oracle_status"]=="DRAFT"
    assert result["quality_score"] is None
