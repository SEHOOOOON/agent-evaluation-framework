"""Synthetic scoring contracts, not evidence of model scoring accuracy."""
from copy import deepcopy
import json

import pytest

from eval_harness.detailed_rubric import (
    CRITERIA, SCORE_VERSION, VERSION, apply_role_profile, full_scorecard, role_items, scoring_anchors,
)
from eval_harness.openai_judge import JudgePacket, preview, validate_judgment, resolve_references, evidence_catalog


def packet():
    return JudgePacket(case_id="score", trial_id="1", agent_id="A2", target_version="synthetic",
                       sources={"context": {"request": "장비 선택 후 적용"},
                                "artifact": {"steps": "장비 선택 후 적용"}},
                       rubric=[next(x for x in role_items("A2") if x["criterion_id"] == "TC-R02")],
                       code_checks=[], scoring_profile=SCORE_VERSION)


def response(score=5, verdict="PASS"):
    return {"judgments": [{"criterion_id": "TC-R02", "score": score, "verdict": verdict,
                          "reason": "합성 근거", "evidence": [
                              {"source_id": "context", "path": ["request"], "quote": "장비 선택 후 적용"}]}],
            "limitations": ["합성 검사"]}


@pytest.mark.parametrize("score", range(6))
def test_all_six_scores_with_evidence(score):
    p = packet()
    assert validate_judgment(p, response(score, "PASS" if score >= 4 else "FAIL")).judgments[0].score == score


@pytest.mark.parametrize("score,verdict", [(6,"PASS"),(-1,"FAIL"),(True,"PASS"),(4.5,"PASS"),
                                        (None,"PASS"),(5,"FAIL"),(4,"FAIL"),(3,"PASS"),(0,"REVIEW"),(5,"NOT_APPLICABLE")])
def test_invalid_or_unfounded_scores_rejected(score, verdict):
    with pytest.raises(ValueError):
        validate_judgment(packet(), response(score, verdict))


@pytest.mark.parametrize("verdict", ["REVIEW", "NOT_APPLICABLE"])
def test_unrated_not_zero(verdict):
    assert validate_judgment(packet(), response(None, verdict)).judgments[0].score is None


def test_reference_resolution_keeps_score_and_requires_exact_evidence():
    p = packet()
    _, catalog, fingerprint = evidence_catalog(p)
    ref = next(iter(catalog))
    raw = {"packet_sha256": fingerprint, "judgments": [
        {"criterion_id": "TC-R02", "score": 4, "verdict": "PASS", "reason": "합성 경미한 설명 부족",
         "evidence_ids": [ref]}], "limitations": []}
    assert resolve_references(p, raw).judgments[0].score == 4
    raw["judgments"][0]["evidence_ids"] = ["NO_SUCH_ID"]
    with pytest.raises(ValueError):
        resolve_references(p, raw)


def test_scoring_changes_preview_and_covers_26_anchors():
    p = packet()
    old = p.model_copy(update={"scoring_profile": None})
    assert preview(p,"test")["approval_sha256"] != preview(old,"test")["approval_sha256"]
    sent = json.loads(preview(p,"test")["request"]["input"][1]["content"])
    assert sent["scoring_profile"] == SCORE_VERSION
    assert set(sent["scoring_scale"]) == set("012345")
    assert "scoring_rubric" not in sent  # Do not repeat conditions already in rubric.
    assert len(CRITERIA) == 26
    for row in CRITERIA:
        anchors = scoring_anchors(row["criterion_id"])
        assert set(anchors) == set("012345")
        assert row["pass_condition"] in anchors["5"]
        assert row["fail_condition"] in anchors["0"]


def results():
    return {owner: {"status": "COMPLETED", "scoring_profile": SCORE_VERSION,
                    "rubric_version": VERSION, "evaluator_version": "synthetic-judge-v1", "judge_model": "synthetic-model",
                    "judgments": [{"criterion_id": r["criterion_id"], "score": 5, "verdict": "PASS",
                                   "reason": "합성 근거", "evidence": []}
                                  for r in role_items(owner)]}
            for owner in ("A1", "A2", "A3", "A4", "E2E")}


def test_26_unique_items_common_minimum_and_violation_not_hidden():
    data = results()
    data["A1"]["judgments"][0].update(score=3, verdict="FAIL")
    data["A2"]["judgments"][-1].update(score=None, verdict="NOT_APPLICABLE")
    before = deepcopy(data)
    result = full_scorecard(data)
    assert len(result["criteria"]) == 26
    assert result["criteria"][0]["score"] == 3
    assert result["summary"] == {"score_100": 98.4, "average_5":4.92, "evaluated":25, "not_applicable":1,
                                "unevaluated":0, "total":26, "violations":["C01"]}
    assert data == before


def test_missing_role_and_legacy_not_invented_as_scores():
    data = results()
    data.pop("E2E")
    data["A3"].pop("scoring_profile")
    result = full_scorecard(data)
    assert result["summary"]["unevaluated"] == 10  # common 2, A3 4, E2E 4
    assert full_scorecard({})["summary"]["score_100"] is None
    assert full_scorecard({})["summary"]["unevaluated"] == 26


def test_e2e_requires_separate_sources_not_role_results_alone():
    p = packet().model_copy(update={"agent_id":"E2E"})
    p = apply_role_profile(p)
    assert len(p.rubric) == 3
    from eval_harness.detailed_rubric import coverage
    assert coverage(p)["scope"] == "E2E_ONLY"
    assert all(row["missing_sources"] for row in coverage(p)["criteria"])


def test_scores_resolve_through_fake_model_and_keep_code_failure(tmp_path):
    from types import SimpleNamespace
    from eval_harness.openai_judge import evaluate, CodeCheck
    p = packet()
    p.code_checks = [CodeCheck(rule="synthetic", status="FAIL", reason="합성 코드 검사 위반")]
    _, catalog, fingerprint = evidence_catalog(p)
    response = {"packet_sha256": fingerprint, "judgments": [
        {"criterion_id": "TC-R02", "verdict": "PASS", "score": 5,
         "reason": "합성 기준 충족", "evidence_ids": [next(iter(catalog))]}], "limitations": ["합성"]}
    client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs:
        SimpleNamespace(id="synthetic", status="completed", output_parsed=response, usage=None)))
    root = evaluate(p, "test", preview(p, "test")["approval_sha256"], tmp_path, client_factory=lambda: client)
    saved = json.loads((root / "result.json").read_text(encoding="utf-8"))
    assert saved["status"] == "COMPLETED"
    assert saved["scoring"]["score_100"] == 100
    assert saved["mode"] == "TEST_DOUBLE"
    assert saved["assessment"] == "ISSUES_FOUND"
    assert saved["judge_model"] == "test" and saved["rubric_version"] == VERSION


def test_same_coverage_only_comparison():
    from eval_harness.detailed_rubric import repeat_scores
    first = full_scorecard(results())
    changed = results()
    changed["A2"]["judgments"][-1].update(score=0, verdict="FAIL")
    second = full_scorecard(changed)
    compared = repeat_scores([first, second])
    assert compared["comparable"]
    assert compared["minimum"] == second["summary"]["score_100"]
    changed["A2"]["judgments"][-1].update(score=None, verdict="REVIEW")
    assert not repeat_scores([first, full_scorecard(changed)])["comparable"]


def test_dashboard_projects_scores_and_preserves_legacy(tmp_path):
    from eval_harness.web.flows import flow_summary
    name = "FLOW-20260930-123000-abcdef"
    root = tmp_path / name
    root.mkdir()
    data = results()
    data["A2"]["judgments"][2]["reason"] = "C:/Users/private/secret.json"
    data["A2"]["judge_model"] = "C:/Users/private/model-secret.json"
    (root / "result.json").write_text(json.dumps({
        "mode":"TEST_DOUBLE", "trials":[{"trial":1,"status":"COMPLETED","judges":data}],
        "status":"COMPLETED"}), encoding="utf-8")
    summary = flow_summary(tmp_path, name)
    assert summary["trials"][0]["scorecard"]["summary"]["evaluated"] == 26
    assert "secret.json" not in json.dumps(summary)
    assert "judge_models" not in summary["trials"][0]["scorecard"]["versions"]
    assert len(summary["trials"][0]["scorecard"]["criteria"]) == 26
    assert summary["score_comparison"]["mean"] is None  # One trial is not repeatability.


@pytest.mark.parametrize("different_model", [False, True])
def test_dashboard_compares_exact_private_versions_before_hiding_metadata(tmp_path, different_model):
    from eval_harness.web.flows import flow_summary
    name = "FLOW-20261002-123000-abcdef"
    root = tmp_path / name
    root.mkdir()
    first, second = results(), results()
    if different_model:
        for result in second.values():
            result["judge_model"] = "private-different-model"
    saved = json.dumps({"mode": "TEST_DOUBLE", "status": "COMPLETED", "trials": [
        {"trial": 1, "status": "COMPLETED", "judges": first},
        {"trial": 2, "status": "COMPLETED", "judges": second}]})
    (root / "result.json").write_text(saved, encoding="utf-8")
    summary = flow_summary(tmp_path, name)
    assert all(not trial["scorecard"]["versions"]["mixed"] for trial in summary["trials"])
    assert summary["score_comparison"]["comparable"] is (not different_model)
    assert summary["score_comparison"]["mean"] == (None if different_model else 100)
    assert "private-different-model" not in json.dumps(summary)
    assert all("judge_models" not in trial["scorecard"]["versions"] for trial in summary["trials"])
    assert (root / "result.json").read_text(encoding="utf-8") == saved


def test_invalid_saved_scores_not_treated_as_valid():
    data = results()
    data["A2"]["judgments"][-1].update(score=5, verdict="NOT_APPLICABLE")
    assert full_scorecard(data)["summary"]["unevaluated"] == 1


def test_weakened_rubric_cannot_request_scores():
    data = packet().model_dump()
    data["rubric"][0]["pass_condition"] = "Always pass"
    with pytest.raises(ValueError):
        JudgePacket.model_validate(data)


@pytest.mark.parametrize("field,changed", [("scoring_profile", "score-0-5-v1"),
    ("rubric_version", "role-rubric-2.2"), ("evaluator_version", "synthetic-judge-v2"),
    ("judge_model", "different-model")])
def test_mixed_versions_keep_raw_scores_but_never_produce_combined_averages(field, changed):
    from eval_harness.detailed_rubric import repeat_scores
    data = results()
    data["A3"][field] = changed
    data["A2"]["judgments"][-1].update(score=0, verdict="FAIL")
    before = deepcopy(data)
    card = full_scorecard(data)
    assert card["versions"]["mixed"] and not card["versions"]["comparison_ready"]
    assert card["summary"]["average_5"] is card["summary"]["score_100"] is None
    assert card["summary"]["evaluated"] == 26 and card["summary"]["violations"] == ["TC-R07"]
    assert all(group["average_5"] is None for group in card["groups"].values())
    assert next(row for row in card["criteria"] if row["criterion_id"] == "A3-01")["score"] == 5
    assert not repeat_scores([card, card])["comparable"]
    assert data == before


@pytest.mark.parametrize("field", ["rubric_version", "evaluator_version", "judge_model"])
def test_missing_historical_versions_are_not_guessed_for_repeat_comparison(field):
    from eval_harness.detailed_rubric import repeat_scores
    data = results()
    data["A3"].pop(field)
    before = deepcopy(data)
    card = full_scorecard(data)
    assert not card["versions"]["mixed"]
    assert card["versions"]["missing_version_roles"] == ["A3"]
    assert card["summary"]["average_5"] == 5  # Descriptive old scores remain readable.
    assert not repeat_scores([card, card])["comparable"]
    assert data == before


def test_custom_subset_uses_recorded_canonical_version_not_custom_as_a_version():
    from eval_harness.detailed_rubric import repeat_scores
    data = results()
    data["A4"]["rubric_coverage"] = {"profile": "custom"}
    card = full_scorecard(data)
    assert card["versions"]["rubric_versions"] == [VERSION]
    assert card["versions"]["comparison_ready"]
    assert repeat_scores([card, card])["comparable"]
    data["A4"].pop("rubric_version")
    old = full_scorecard(data)
    assert old["versions"]["missing_version_roles"] == ["A4"]
    assert not repeat_scores([old, old])["comparable"]


def test_known_historical_version_is_read_from_coverage_not_relabelled_as_current():
    from eval_harness.detailed_rubric import repeat_scores
    data = results()
    for result in data.values():
        result.pop("rubric_version")
        result["rubric_coverage"] = {"profile": "role-rubric-2.2"}
    before = deepcopy(data)
    card = full_scorecard(data)
    assert card["versions"]["rubric_versions"] == ["role-rubric-2.2"]
    assert repeat_scores([card, card])["comparable"]
    assert data == before


def test_partial_python_scores_use_recorded_rubric_without_requiring_a_judge_model():
    data = results()
    python_result = {"status": "COMPLETED", "mode": "PYTHON", "scoring_profile": SCORE_VERSION,
                     "rubric_version": VERSION, "judgments": [
        {"criterion_id": "A4-01", "score": 5, "verdict": "PASS", "reason": "합성 사실", "evidence": []}]}
    card = full_scorecard(data, python_result)
    assert card["versions"]["comparison_ready"]
    assert next(row for row in card["criteria"] if row["criterion_id"] == "A4-01")["evaluation_method"] == "PYTHON"
