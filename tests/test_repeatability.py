import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from eval_harness.repeatability import RepeatPlan, SimulationTransport, run_simulated


def plan(**updates):
    data = dict(case_id="SELF-A2-TEMP", agent_id="A2", target_version="synthetic-only",
                prompt_version="synthetic-only", model_config_id="no-model",
                input_payload={"requirement":"Verify the requested 18 degree setting"},
                manual_review_topics=["조작 상세도·문장 의미는 별도 검토"],
                criteria=[dict(criterion_id="A2-VALUE", objective="요청 온도 보존", basis="시험용 입력의 18도",
                    path=["test_cases","*","test_data","requested_temperature_c"],operator="equals",expected=18)])
    data.update(updates)
    return RepeatPlan(**data)


def responses(values=(18,18,18,18,18)):
    return [dict(response_id=f"fake-{i}",output={"title":f"표현 {i}","test_cases":[
        {"test_data":{"requested_temperature_c":v}}]}) for i,v in enumerate(values)]


def run(tmp_path, items=None, spec=None):
    transport=SimulationTransport(responses() if items is None else items)
    root=run_simulated(spec or plan(),transport,tmp_path)
    return json.loads((root/"result.json").read_text(encoding="utf-8")),transport,root


def test_five_independent_simulated_calls_with_wording_variance(tmp_path):
    result,transport,root=run(tmp_path)
    assert transport.calls==result["simulated_calls"]==5
    assert result["api_requests"]==0
    assert result["pass_count"]==5
    assert result["contract_consistency_rate"]==1
    assert result["raw_output_consistency_rate"]==.2
    assert result["assessment"]=="REVIEW"
    assert not result["performance_claim_eligible"]
    events=[json.loads(x) for x in (root/"attempts.jsonl").read_text().splitlines()]
    assert len(events)==10
    assert len({x["trial_id"] for x in events})==5
    assert len({x["input_sha256"] for x in events if x["event"]=="STARTED"})==1


def test_one_wrong_value_is_visible_without_average_hiding(tmp_path):
    result,_,_=run(tmp_path,responses([18,18,16,18,18]))
    assert result["compliance_rate"]==.8
    assert result["contract_consistency_rate"]==.8
    assert result["assessment"]=="FAIL"
    assert result["criteria"][0]["affected_trials"]==["T003"]


def test_five_identical_wrong_answers_are_consistent_but_fail(tmp_path):
    result,_,_=run(tmp_path,responses([16]*5))
    assert result["contract_consistency_rate"]==1
    assert result["compliance_rate"]==0
    assert result["fail_count"]==5


def test_failure_consumes_attempt_without_hidden_retry(tmp_path):
    items=responses()
    items[1]=TimeoutError("secret must not be persisted")
    result,transport,root=run(tmp_path,items)
    assert transport.calls==5
    assert result["error_count"]==1
    assert result["valid_outputs"]==4
    assert result["compliance_rate"]==.8
    assert result["comparable_trials"]==4
    assert not result["all_planned_trials_comparable"]
    assert result["assessment"]=="INCOMPLETE"
    assert "secret must" not in (root/"result.json").read_text(encoding="utf-8")


def test_identical_output_is_allowed_but_duplicate_response_id_is_not(tmp_path):
    items=responses()
    for item in items:
        item["output"]=deepcopy(items[0]["output"])
    result,_,_=run(tmp_path,items)
    assert result["pass_count"]==5
    items[1]["response_id"]=items[0]["response_id"]
    result,_,_=run(tmp_path,items)
    assert result["error_count"]==1
    assert result["pass_count"]==4


def test_call_limit_leaves_explicit_not_run_records(tmp_path):
    result,transport,_=run(tmp_path,spec=plan(call_limit=2))
    assert transport.calls==2
    assert result["not_run_count"]==3
    assert result["criteria"][0]["unassessed"]==3
    assert result["compliance_rate"]==.4


@pytest.mark.parametrize("output,status", [({},"FAIL"),({"test_cases":None},"ERROR"),([],"ERROR")])
def test_missing_and_malformed_outputs_never_pass(tmp_path,output,status):
    items=responses()
    items[2]["output"]=output
    result,_,_=run(tmp_path,items)
    assert result["trials"][2]["status"]==status
    assert result["pass_count"]==4


@pytest.mark.parametrize("field,value", [("planned_trials",0),("planned_trials",True),("call_limit",0),("criteria",[]),("manual_review_topics",[])])
def test_invalid_plans_rejected(field,value):
    with pytest.raises(ValidationError):
        plan(**{field:value})


def test_duplicate_criteria_rejected():
    spec=plan().model_dump()
    spec["criteria"]*=2
    with pytest.raises(ValidationError):
        RepeatPlan(**spec)


def test_foreign_transport_cannot_turn_self_test_into_live(tmp_path):
    with pytest.raises(ValueError):
        run_simulated(plan(),object(),tmp_path)


def test_report_escapes_user_content(tmp_path):
    spec=plan(manual_review_topics=["<script>alert(1)</script>"])
    _,_,root=run(tmp_path,spec=spec)
    assert "<script>" not in (root/"report.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("operator,expected,output,status", [
    ("count",0,{"rows":[]},"PASS"),
    ("count",1,{"rows":[]},"FAIL"),
    ("contains",{"value":18,"after":"apply"},{"rows":[{"value":18,"after":"select"},{"value":16,"after":"apply"}]},"FAIL"),
    ("not_contains",{"color":"red"},{"rows":[{"color":"red"}]},"FAIL"),
    ("not_contains",{"color":"red"},{"rows":[]},"PASS"),
])
def test_contract_operators(operator,expected,output,status):
    from eval_harness.repeatability import Criterion,grade
    criterion=Criterion(criterion_id="check",objective="test",basis="test",path=["rows","*"],operator=operator,expected=expected)
    assert grade(output,[criterion])[0][0]["status"]==status


def test_unordered_is_explicit_and_action_order_is_preserved():
    from eval_harness.repeatability import Criterion,grade
    criterion=Criterion(criterion_id="check",objective="test",basis="test",path=["rows","*"],operator="count",expected=2)
    first={"rows":["select","apply"]}
    second={"rows":["apply","select"]}
    assert grade(first,[criterion])[1] != grade(second,[criterion])[1]
    criterion.unordered=True
    assert grade(first,[criterion])[1] == grade(second,[criterion])[1]
