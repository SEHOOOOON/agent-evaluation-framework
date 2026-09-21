from copy import deepcopy

import pytest

from eval_harness.behavior import RecordedTrial, evaluate_recorded_trials


CATALOG={"catalog_version":"self", "rules":[
    {"rule_id":"REQUIRED", "severity":"critical"}, {"rule_id":"OTHER", "severity":"high"}]}
BENCH={"dataset_version":"self", "cases":[dict(case_id="CASE",target="agent_1",
    required_rule_ids=["REQUIRED","OTHER"],expected_core={"decision":"PROCEED"},trials=5)]}


def trials():
    return [RecordedTrial(trial_id=f"T{i}",case_id="CASE",agent_id="agent_1",core_outcome={"decision":"PROCEED"},
        rule_results=[dict(rule_id="REQUIRED",severity="critical",status="PASS"),
                      dict(rule_id="OTHER",severity="high",status="PASS")]) for i in range(5)]


def evaluate(items):
    return evaluate_recorded_trials(items,evaluation_mode="self-test",agent_executed=False,catalog=CATALOG,benchmark=BENCH)


@pytest.mark.parametrize("mutation", ["duplicate_trial","duplicate_rule","wrong_agent","downgrade","unknown_critical","empty"])
def test_invalid_identity_or_authority_rejected(mutation):
    items=trials()
    if mutation=="duplicate_trial": items[1].trial_id=items[0].trial_id
    if mutation=="duplicate_rule": items[0].rule_results.append(items[0].rule_results[0])
    if mutation=="wrong_agent": items[0].agent_id="agent_2"
    if mutation=="downgrade":
        from eval_harness.models import Severity
        items[0].rule_results[0].severity=Severity.LOW
    if mutation=="unknown_critical": items[0].critical_violations=["UNKNOWN"]
    if mutation=="empty": items=[]
    if mutation=="downgrade":
        items=[RecordedTrial.model_validate(i.model_dump()) for i in items]
    with pytest.raises(ValueError): evaluate(items)


@pytest.mark.parametrize("mutation", ["missing_rule","error","abstain","not_applicable","wrong_gold","missing_gold","four_trials"])
def test_incomplete_evaluation_never_passes(mutation):
    items=trials()
    if mutation=="missing_rule": items[0].rule_results.pop()
    if mutation in {"error","abstain","not_applicable"}:
        from eval_harness.models import CheckStatus
        items[0].rule_results[0].status=CheckStatus(mutation.upper())
    if mutation=="wrong_gold": items[0].core_outcome={"decision":"BLOCKED"}
    if mutation=="missing_gold": items[0].core_outcome={}
    if mutation=="four_trials": items.pop()
    result=evaluate(items)
    assert result.decision.value!="PASS"
    assert result.evaluation_issues
    assert not result.performance_claim_eligible
    if mutation in {"missing_rule","error","abstain","not_applicable"}:
        assert result.rule_compliance_rate==90.0


def test_unverified_live_flag_does_not_assert_proven_execution():
    result=evaluate_recorded_trials(trials(),evaluation_mode="recorded",agent_executed=True,catalog=CATALOG,benchmark=BENCH)
    assert not result.performance_claim_eligible
    assert "성능 지표가 아닙니다" in result.truth_note
