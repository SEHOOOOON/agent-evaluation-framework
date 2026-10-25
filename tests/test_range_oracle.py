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
    """T-4 (H-6, 2026-10-23): HEAT 16 applied and seen in three locations (any start)."""
    ops = [op(1, "PRECONDITION", "device.select"), op(2, "PRECONDITION", "mode.heat"),
           op(3, "PRECONDITION", "temperature.set", 24), op(4, "PRECONDITION", "command.apply"),
           op(5, "TEST", "temperature.set", 16), op(6, "TEST", "command.apply"),
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
            "최종_확인_사항": ["난방 17°C처럼 냉방 범위 밖 온도에서 냉방으로 전환할 때 설정 온도 처리 방식을 확인해 주세요."],
            "중단_확인_사항": []}


def by_id(result):
    return {i["id"]: i for i in result["items"]}


def strict_key():
    """Same request without the out_of_scope exclusion: T-5 stays REQUIRED."""
    return load_key(KEY_PATH).model_copy(update={"switch_behavior_excluded": False})


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
    result = grade(strict_key(), live_like_design())
    items = by_id(result)
    assert result["verdict"] == "FAIL"
    assert items["T-2:BLOCK:COOL:31"]["status"] == "MISSING"
    assert items["T-1:APPLY:COOL:20"]["status"] == "MISSING"   # setup-only apply is not a test
    assert items["T-5:QUESTION:COOL"]["status"] == "MISSING"
    assert "HEAT 17 → COOL" in items["T-5:QUESTION:COOL"]["detail"]
    # What the design did cover is credited, not flattened into one failure.
    for met in ("T-2:BLOCK:COOL:19", "T-1:APPLY:COOL:30", "T-4:APPLY:HEAT:16"):
        assert items[met]["status"] == "MET", met
    assert items["T-3:BLOCK:HEAT:31"]["status"] == "MISSING"   # recommended: not counted
    assert (result["required_met"], result["required_total"]) == (3, 6)


def test_question_in_confirmation_items_satisfies_t5():
    design = live_like_design()
    design["최종_확인_사항"] = ["난방 17°C 상태에서 냉방으로 전환할 때 설정 온도 처리 방식을 확인해 주세요."]
    assert by_id(grade(strict_key(), design))["T-5:QUESTION:COOL"]["status"] == "MET"


def test_defined_switch_behavior_needs_no_question():
    key = load_key(KEY_PATH).model_copy(update={"switch_behavior_defined": True})
    assert not any(i["rule"] == "T-5" for i in grade(key, live_like_design())["items"])


def test_correct_design_passes():
    result = grade(strict_key(), correct_design())
    assert result["verdict"] == "PASS", [i for i in result["items"] if i["status"] != "MET"]
    assert result["required_met"] == result["required_total"] == 6
    assert result["issues"] == []


def test_t5_comes_from_the_request_even_without_a_switch_in_the_tc():
    """Real 10-15 TC set COOL right after device selection (no out-of-range switch)."""
    design = correct_design()
    design["최종_확인_사항"] = []
    result = grade(strict_key(), design)
    item = by_id(result)["T-5:QUESTION:COOL"]
    assert item["status"] == "MISSING" and "TC에 해당 전환 없음" in item["detail"] and "HEAT 16~30" in item["detail"]
    assert result["verdict"] == "FAIL"


def test_a1_question_counts_for_t5():
    design = correct_design()
    design["최종_확인_사항"] = []
    analysis = {"user_questions": [{"question": "냉방 하한보다 낮은 온도에서 냉방 모드로 바꾸면 어떻게 처리하나요?"}],
                "information_gaps": []}
    assert by_id(grade(strict_key(), design, analysis))["T-5:QUESTION:COOL"]["status"] == "MET"


def test_widened_range_needs_no_t5_question():
    key = load_key(KEY_PATH).model_copy(update={"changed": [RangeKey.model_fields["changed"].annotation.__args__[0](mode="COOL", lower=16, upper=30)],
                                                "kept": [RangeKey.model_fields["changed"].annotation.__args__[0](mode="HEAT", lower=18, upper=28)]})
    assert not any(i["rule"] == "T-5" for i in grade(key, correct_design())["items"])


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


def test_second_key_with_both_bounds_changed_uses_its_own_values():
    """Same rules, different mode and numbers: nothing is tied to the COOL 20 case."""
    key = load_key(ROOT / "benchmarks/v2/range_keys/heat-range-narrowing.json")
    rows = {r["id"] for r in expectations(key) if r["level"] == "REQUIRED"}
    assert rows == {"T-1:APPLY:HEAT:18", "T-1:APPLY:HEAT:28", "T-2:BLOCK:HEAT:17", "T-2:BLOCK:HEAT:29"}
    heat = [op(1, "PRECONDITION", "mode.heat"),
            op(2, "TEST", "temperature.set", 18), op(3, "TEST", "command.apply"),
            op(4, "TEST", "temperature.decrease"), op(5, "TEST", "command.apply"),
            op(6, "TEST", "temperature.set", 28), op(7, "TEST", "command.apply"),
            op(8, "TEST", "temperature.increase"), op(9, "TEST", "command.apply")]
    verifications = (checks("ACT-003", 18, er=1) + checks("ACT-005", 18, er=4)
                     + checks("ACT-007", 28, er=7) + checks("ACT-009", 28, er=10))
    design = {"test_cases": [tc("TC-HEAT", heat, verifications)]}
    # The key leaves out-of-range switching into HEAT undefined (COOL 16~30 is wider): ask (T-5).
    assert by_id(grade(key, design))["T-5:QUESTION:HEAT"]["status"] == "MISSING"
    design["최종_확인_사항"] = ["난방 18~28°C 밖 온도에서 난방으로 전환할 때의 처리 방식을 확인해 주세요."]
    assert grade(key, design)["verdict"] == "PASS"
    # The representative COOL design does not satisfy this key's boundary tests.
    assert not any(i["status"] == "MET" for i in grade(key, correct_design())["items"]
                   if i["rule"] in {"T-1", "T-2"})


def _t5(design, analysis=None):
    return by_id(grade(strict_key(), design, analysis))["T-5:QUESTION:COOL"]


@pytest.mark.parametrize("text", [
    "냉방 30°C 적용 후 카드 표시를 확인해 주세요.",
    "COOL 모드 화면 문구가 SRS와 같은지 확인해 주세요.",
])
def test_mode_name_alone_is_not_a_switch_question(text):
    """Counterexample (1.1 counted these as MET): unrelated sentences naming the mode."""
    design = correct_design()
    design["최종_확인_사항"] = [text]
    item = _t5(design)
    assert item["status"] == "PARTIAL" and "모드 이름만" in item["detail"]
    assert grade(strict_key(), design)["verdict"] == "FAIL"


def test_words_in_different_entries_are_not_combined():
    design = correct_design()
    design["최종_확인_사항"] = ["냉방 30°C 표시를 확인해 주세요.", "난방 모드 전환 버튼 위치를 확인해 주세요."]
    assert _t5(design)["status"] == "PARTIAL"


def test_kept_mode_alone_does_not_count():
    design = correct_design()
    design["최종_확인_사항"] = ["난방 17°C에서 풍량 표시를 확인해 주세요."]
    assert _t5(design)["status"] == "MISSING"


@pytest.mark.parametrize("text", [
    "난방 17°C 상태에서 냉방을 선택하면 설정 온도가 어떻게 되나요?",   # changed + kept mode
    "냉방 범위를 벗어난 기존 설정 온도는 어떻게 처리하나요?",           # out-of-range phrase
    "What happens to setTemp when switching to COOL from 17?",          # English switch
])
def test_switch_question_forms_are_met(text):
    design = correct_design()
    design["최종_확인_사항"] = [text]
    assert _t5(design)["status"] == "MET"


def test_a1_field_names_are_not_read_as_words():
    design = correct_design()
    design["최종_확인_사항"] = []
    analysis = {"user_questions": [{"switch_outside": "냉방 30°C 표시 확인"}], "information_gaps": []}
    assert _t5(design, analysis)["status"] == "PARTIAL"


def test_request_excluded_switch_makes_t5_optional():
    """H-2 (2026-10-17): the real request lists the COOL entry policy as out of scope."""
    key = load_key(KEY_PATH)
    assert key.status == "HUMAN_CONFIRMED" and key.switch_behavior_excluded
    design = correct_design()
    design["최종_확인_사항"] = []
    result = grade(key, design)
    item = by_id(result)["T-5:QUESTION:COOL"]
    assert item["level"] == "OPTIONAL" and item["status"] == "MISSING" and "H-2" in item["detail"]
    assert result["verdict"] == "PASS" and result["required_met"] == result["required_total"] == 5
    # Asking anyway is recorded as met and never lowers the result.
    assert by_id(grade(key, correct_design()))["T-5:QUESTION:COOL"]["status"] == "MET"


def test_english_field_names_count_for_t5():
    """A2 attempt files may use English field names; the question is the same."""
    design = correct_design()
    question = design.pop("최종_확인_사항")
    assert _t5(design)["status"] == "MISSING"
    design["final_review_notes"] = question
    assert _t5(design)["status"] == "MET"

def _blocked_then_increase_design():
    """2026-10-25 review: 18 set in preparation, decrease to 17 is refused (kept 18), then increase -> 19."""
    def op(i, phase, target, value=None):
        return {"action_id": f"ACT-{i:03}", "phase": phase, "target": target, "value": value}
    def check(rid, after, location, value):
        return {"result_id": rid, "after_action_id": after, "target": f"{location}.setTemp", "expected_number": value}
    blocked = {"tc_id": "TC-CAND-001", "execution_spec": {
        "operations": [op(1, "PRECONDITION", "mode.cool"), op(2, "PRECONDITION", "temperature.set", 18),
                       op(3, "TEST", "temperature.decrease"), op(4, "TEST", "command.apply"),
                       op(5, "TEST", "temperature.increase"), op(6, "TEST", "command.apply")],
        "verifications": [check("ER-001", "ACT-004", "internal", 18), check("ER-002", "ACT-006", "internal", 19)]}}
    applied = {"tc_id": "TC-CAND-002", "execution_spec": {
        "operations": [op(1, "PRECONDITION", "mode.cool"), op(2, "TEST", "temperature.set", 18), op(3, "TEST", "command.apply")],
        "verifications": [check("ER-003", "ACT-003", loc, 18) for loc in ("internal", "panel", "card")]}}
    return {"test_cases": [blocked, applied]}


def test_walk_design_starts_after_a_blocked_attempt_from_the_kept_value():
    """range-oracle-1.4."""
    from eval_harness.range_oracle import walk_design
    probes = walk_design(_blocked_then_increase_design())["probes"]
    assert [(p["tc_id"], p["start"], p["attempted"]) for p in probes] == [
        ("TC-CAND-001", 18, 17), ("TC-CAND-001", 18, 19), ("TC-CAND-002", None, 18)]



def test_wrong_expectation_in_another_tc_is_kept_when_one_tc_meets_the_row():
    """range-oracle-1.4 (2026-10-25): the row is MET, the other TC's wrong value stays visible."""
    design = correct_design()
    wrong = [op(1, "PRECONDITION", "device.select"), op(2, "PRECONDITION", "mode.cool"),
             op(3, "TEST", "temperature.set", 20), op(4, "TEST", "command.apply"),
             op(5, "RESTORE", "controller.restore")]
    design["test_cases"].append(tc("TC-WRONG", wrong, checks("ACT-004", 21, er=50)))
    result = grade(load_key(KEY_PATH), design)
    apply20 = next(i for i in result["items"] if i["mode"] == "COOL" and i["attempted"] == 20
                   and i["kind"] == "APPLY")
    assert apply20["status"] == "MET"
    assert any(i["rule"] == "WRONG_OTHER_TC" and i["tc_id"] == "TC-WRONG" for i in result["issues"])
    assert result["verdict"] == "REVIEW"
