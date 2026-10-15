"""Rule-based range answer key vs A2 execution_spec.

The 2026-10-15 live run file is not in this repository; the fixtures below are
SYNTHETIC designs that reproduce only the three errors a person found there
(upper+1 block missing, no separate apply ER for the new lower bound, mode switch
from an out-of-range value without a question). They are not the real artifact.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from eval_harness.cli import main
from eval_harness.range_oracle import RangeKey, expectations, grade, load_key

ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = ROOT / "benchmarks/v2/range_keys/representative-temperature.json"


def op(n, phase, target, value=None):
    return dict(action_id=f"ACT-{n:03d}", phase=phase, target=target, value=value,
                action_type="X", source_text="합성")


def checks(after, value, *, er, locations=("panel", "card", "internal")):
    return [dict(result_id=f"ER-{er + i:03d}", target=f"{loc}.setTemp", after_action_id=after,
                 expected_number=value, strategy="X", observation_layer="X")
            for i, loc in enumerate(locations)]


def tc(tc_id, operations, verifications):
    return dict(tc_id=tc_id, execution_spec=dict(operations=operations, verifications=verifications))


def heat_tc():
    """T-4: HEAT 17 prepared, decrease to 16, three locations checked."""
    ops = [op(1, "PRECONDITION", "device.select"), op(2, "PRECONDITION", "mode.heat"),
           op(3, "PRECONDITION", "temperature.set", 17), op(4, "PRECONDITION", "command.apply"),
           op(5, "TEST", "temperature.decrease"), op(6, "TEST", "command.apply"),
           op(7, "RESTORE", "controller.restore")]
    return tc("TC-HEAT", ops, checks("ACT-006", 16, er=1))


def live_like_design():
    """Synthetic reproduction of the three human-found errors of 2026-10-15."""
    cool = [op(1, "PRECONDITION", "device.select"), op(2, "PRECONDITION", "mode.heat"),
            op(3, "PRECONDITION", "temperature.set", 17), op(4, "PRECONDITION", "command.apply"),
            # HEAT 17 -> COOL: 17 is outside the new COOL range; behavior undefined.
            op(5, "PRECONDITION", "mode.cool"), op(6, "PRECONDITION", "temperature.set", 20),
            op(7, "PRECONDITION", "command.apply"),  # 20 applied only as setup: no apply ER.
            op(8, "TEST", "temperature.decrease"), op(9, "TEST", "command.apply"),
            op(10, "TEST", "temperature.set", 30), op(11, "TEST", "command.apply"),
            op(12, "RESTORE", "controller.restore")]
    verifications = checks("ACT-009", 20, er=1) + checks("ACT-011", 30, er=4)  # no 31 block
    return {"test_cases": [tc("TC-COOL", cool, verifications), heat_tc()],
            "최종_확인_사항": [], "중단_확인_사항": []}


def correct_design():
    cool = [op(1, "PRECONDITION", "device.select"), op(2, "PRECONDITION", "mode.cool"),
            op(3, "TEST", "temperature.set", 20), op(4, "TEST", "command.apply"),
            op(5, "TEST", "temperature.decrease"), op(6, "TEST", "command.apply"),
            op(7, "TEST", "temperature.set", 30), op(8, "TEST", "command.apply"),
            op(9, "TEST", "temperature.increase"), op(10, "TEST", "command.apply"),
            op(11, "RESTORE", "controller.restore")]
    verifications = (checks("ACT-004", 20, er=1) + checks("ACT-006", 20, er=4)
                     + checks("ACT-008", 30, er=7) + checks("ACT-010", 30, er=10))
    return {"test_cases": [tc("TC-COOL", cool, verifications), heat_tc()],
            "최종_확인_사항": [], "중단_확인_사항": []}


def by_id(result):
    return {i["id"]: i for i in result["items"]}


def test_key_expands_to_required_and_recommended_tests():
    rows = {r["id"]: r for r in expectations(load_key(KEY_PATH))}
    assert {k for k, r in rows.items() if r["level"] == "REQUIRED"} == {
        "T-1:APPLY:COOL:20", "T-1:APPLY:COOL:30", "T-2:BLOCK:COOL:19", "T-2:BLOCK:COOL:31",
        "T-4:APPLY:HEAT:16"}
    assert {k for k, r in rows.items() if r["level"] == "RECOMMENDED"} == {
        "T-3:BLOCK:HEAT:15", "T-3:BLOCK:HEAT:31"}
    assert rows["T-2:BLOCK:COOL:31"]["expected"] == 30


def test_synthetic_live_errors_are_all_found():
    """The reviewer AI and P2 judge gave 5/5 to a design with these errors."""
    result = grade(load_key(KEY_PATH), live_like_design())
    items = by_id(result)
    assert result["verdict"] == "FAIL"
    assert items["T-2:BLOCK:COOL:31"]["status"] == "MISSING"
    assert items["T-1:APPLY:COOL:20"]["status"] == "MISSING"   # setup-only apply is not a test
    assert items["T-5:QUESTION:COOL:TC-COOL"]["status"] == "MISSING"
    assert "HEAT 17 → COOL" in items["T-5:QUESTION:COOL:TC-COOL"]["detail"]
    # What the design did cover is credited, not flattened into one failure.
    for met in ("T-2:BLOCK:COOL:19", "T-1:APPLY:COOL:30", "T-4:APPLY:HEAT:16"):
        assert items[met]["status"] == "MET", met
    assert items["T-3:BLOCK:HEAT:15"]["status"] == "MISSING"   # recommended: not counted
    assert (result["required_met"], result["required_total"]) == (3, 6)


def test_question_in_confirmation_items_satisfies_t5():
    design = live_like_design()
    design["최종_확인_사항"] = ["난방 17°C 상태에서 냉방으로 전환할 때 설정 온도 처리 방식을 확인해 주세요."]
    assert by_id(grade(load_key(KEY_PATH), design))["T-5:QUESTION:COOL:TC-COOL"]["status"] == "MET"


def test_defined_switch_behavior_needs_no_question():
    key = load_key(KEY_PATH).model_copy(update={"switch_behavior_defined": True})
    assert not any(i["rule"] == "T-5" for i in grade(key, live_like_design())["items"])


def test_correct_design_passes():
    result = grade(load_key(KEY_PATH), correct_design())
    assert result["verdict"] == "PASS", [i for i in result["items"] if i["status"] != "MET"]
    assert result["required_met"] == result["required_total"] == 5
    assert result["issues"] == []


def test_missing_internal_value_is_partial_not_met():
    design = correct_design()
    spec = design["test_cases"][0]["execution_spec"]
    spec["verifications"] = [v for v in spec["verifications"]
                             if not (v["after_action_id"] == "ACT-004" and v["target"] == "internal.setTemp")]
    item = by_id(grade(load_key(KEY_PATH), design))["T-1:APPLY:COOL:20"]
    assert item["status"] == "PARTIAL" and "internal" in item["detail"] and "C-3" in item["detail"]


def test_block_expecting_the_out_of_range_value_is_wrong():
    design = correct_design()
    for v in design["test_cases"][0]["execution_spec"]["verifications"]:
        if v["after_action_id"] == "ACT-006":
            v["expected_number"] = 19
    result = grade(load_key(KEY_PATH), design)
    assert by_id(result)["T-2:BLOCK:COOL:19"]["status"] == "WRONG_EXPECTATION"
    assert result["verdict"] == "FAIL"


def test_block_judged_by_toast_is_flagged_t8():
    design = correct_design()
    design["test_cases"][0]["execution_spec"]["verifications"].append(
        dict(result_id="ER-099", target="notification.toast", after_action_id="ACT-010"))
    result = grade(load_key(KEY_PATH), design)
    assert [i["rule"] for i in result["issues"]] == ["T-8"] and result["verdict"] == "FAIL"


def test_boundaries_split_across_tcs_is_review_t7():
    design = correct_design()
    cool = design["test_cases"][0]
    second = copy.deepcopy(cool)
    second["tc_id"] = "TC-COOL-UPPER"
    ops = cool["execution_spec"]["operations"]
    cool["execution_spec"]["operations"] = ops[:6] + ops[-1:]
    second["execution_spec"]["operations"] = ops[:2] + ops[6:]
    design["test_cases"].insert(1, second)
    result = grade(load_key(KEY_PATH), design)
    assert result["required_met"] == result["required_total"]
    assert [i["rule"] for i in result["issues"]] == ["T-7"] and result["verdict"] == "REVIEW"


def test_key_rejects_inverted_or_duplicate_ranges():
    base = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    bad = dict(base, changed=[{"mode": "COOL", "lower": 30, "upper": 20}])
    with pytest.raises(ValueError):
        RangeKey.model_validate(bad)
    with pytest.raises(ValueError):
        RangeKey.model_validate(dict(base, kept=[{"mode": "COOL", "lower": 16, "upper": 30}]))


def test_cli_exit_codes(tmp_path, capsys):
    path = tmp_path / "agent2_test_design.json"
    path.write_text(json.dumps(live_like_design(), ensure_ascii=False), encoding="utf-8")
    assert main(["range-oracle", "--key", str(KEY_PATH), "--design", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["verdict"] == "FAIL"
    path.write_text(json.dumps(correct_design(), ensure_ascii=False), encoding="utf-8")
    assert main(["range-oracle", "--key", str(KEY_PATH), "--design", str(path)]) == 0
    capsys.readouterr()
    assert main(["range-oracle", "--key", str(KEY_PATH), "--design", str(tmp_path / "none.json")]) == 4
