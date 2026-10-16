"""Option B: declared product mismatches vs verified assertion records.

Synthetic records mirror the 2026-10-15 representative run (unchanged product:
COOL 20 + decrease shows 19). They are not that run's files.
"""
from __future__ import annotations

import json

import pytest

from eval_harness.execution_observations import COMPLETE, OBSERVED, VERSION, compare
from eval_harness.full_flow import FlowSpec
from eval_harness.product_expectations import ExpectedMismatch, classify

DECLARED = [ExpectedMismatch(field="setTemp", expected=20, actual=19,
                             reason="제품 미변경: 냉방 20°C에서 내림 시 19°C로 내려감")]


def temp(result_id, expected, actual, strategy="UI_TEMPERATURE"):
    return dict(assertion=dict(result_id=result_id, strategy=strategy, expected_number=expected),
                actual=actual, matched=actual == expected)


def fields(result_id, expected, actual):
    return dict(assertion=dict(result_id=result_id, strategy="INTERNAL_DEVICE_FIELDS_EQUALS",
                               expected_fields=[dict(field_name="setTemp", expected_value=expected)]),
                actual={"setTemp": actual}, matched=actual == expected)


def cool_block(actual):
    """Panel, card, internal checks after decrease from 20."""
    return [temp("ER-004", 20, actual), fields("ER-005", 20, actual), temp("ER-006", 20, actual, "INTERNAL_SET_TEMP")]


def test_case1_correct_tc_fails_as_declared():
    observed = {"TC-COOL": [temp("ER-001", 20, 20), *cool_block(19)], "TC-HEAT": [temp("ER-001", 16, 16)]}
    result = classify(DECLARED, observed, "PASS")
    assert result["status"] == "AS_EXPECTED" and result["unexpected"] == []
    assert result["declared"][0]["where"] == ["TC-COOL/ER-004", "TC-COOL/ER-005", "TC-COOL/ER-006"]


def test_case2_missing_cool_check_is_not_as_expected():
    result = classify(DECLARED, {"TC-HEAT": [temp("ER-001", 16, 16)]}, "PASS")
    assert result["status"] == "NOT_AS_EXPECTED"
    assert result["declared"][0]["status"] == "NOT_OBSERVED"


def test_case3_unexpected_pass_is_not_as_expected():
    result = classify(DECLARED, {"TC-COOL": cool_block(20)}, "PASS")
    assert result["status"] == "NOT_AS_EXPECTED" and result["declared"][0]["status"] == "NOT_OBSERVED"


def test_undeclared_mismatch_is_reported():
    observed = {"TC-COOL": [*cool_block(19), temp("ER-009", 30, 31)]}
    result = classify(DECLARED, observed, "PASS")
    assert result["status"] == "NOT_AS_EXPECTED"
    assert result["unexpected"] == [dict(tc_id="TC-COOL", result_id="ER-009", field="setTemp", expected=30, actual=31)]


@pytest.mark.parametrize("verdict", ["REVIEW", "FAIL"])
def test_unverified_records_are_not_interpreted(verdict):
    assert classify(DECLARED, {"TC-COOL": cool_block(19)}, verdict)["status"] == "UNVERIFIED"


def test_nothing_declared_infers_nothing():
    assert classify([], {"TC-COOL": cool_block(19)}, "PASS") == dict(version="product-expectation-1.0",
                                                                     status="NOT_DECLARED")


def test_compare_hands_over_records_only_when_they_verify():
    assertion = dict(result_id="ER-1", strategy="UI_TEMPERATURE", expected_number=20, after_action_id="ACT-1")
    case = dict(tc_id="TC-X", expected_results=[dict(result_id="ER-1")])
    plan = dict(tc_id="TC-X", target_device_id=1, actions=[dict(action_id="ACT-1", phase="TEST")],
                assertions=[assertion])
    identity = dict(version=VERSION, run_id="RUN-local", tc_id="TC-X")
    event = dict(**identity, target_device_id=1, assertion=dict(assertion), actual=19, matched=False)
    log = OBSERVED + json.dumps(event) + "\n" + COMPLETE + json.dumps(identity)
    sink = []
    assert compare(case, plan, log, run_id="RUN-local", outcome="PRODUCT_MISMATCH_CANDIDATE", sink=sink)["verdict"] == "PASS"
    assert sink == [event]
    sink = []
    # Recorded outcome contradicts the record: nothing is handed over.
    assert compare(case, plan, log, run_id="RUN-local", outcome="PASS", sink=sink)["verdict"] == "FAIL"
    assert sink == []


def test_spec_field_is_optional_and_strict():
    base = dict(case_id="X", request="r.json", oracle="o.json", generation_model="m")
    assert FlowSpec(**base).expected_product_mismatches == []
    spec = FlowSpec.model_validate_json(json.dumps(dict(base, expected_product_mismatches=[
        dict(field="setTemp", expected=20, actual=19, reason="제품 미변경")])))
    assert spec.expected_product_mismatches == DECLARED[:0] + [ExpectedMismatch(
        field="setTemp", expected=20, actual=19, reason="제품 미변경")]
    with pytest.raises(ValueError):
        FlowSpec.model_validate_json(json.dumps(dict(base, expected_product_mismatches=[
            dict(field="setTemp", expected=20, actual=19)])))   # reason is required
