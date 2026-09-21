import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from eval_harness.agent_rubric import CRITERIA as PARENTS
from eval_harness.detailed_rubric import CRITERIA, VERSION, apply_role_profile, coverage, role_items
from eval_harness.openai_judge import JudgePacket, evaluate, preview, validate_judgment

ROOT = Path(__file__).resolve().parents[1]


def packet(agent="A2"):
    return JudgePacket(case_id="test", trial_id="T1", agent_id=agent, target_version="synthetic",
        sources={"context": {"request": "온도 적용 후 장비 카드 표시만 검증. 알림 변경은 요청하지 않았다."},
                 "artifact": {"text": "장비 선택 → 온도 입력 → 적용. 적용 직후 카드 표시를 확인한다."}},
        rubric=[dict(criterion_id="CUSTOM", objective="예시", pass_condition="예시")], code_checks=[])


def response(p, overrides=None):
    missing = {r["criterion_id"] for r in coverage(p)["criteria"] if r["missing_sources"]}
    return {"judgments": [dict(criterion_id=item.criterion_id,
        verdict=(overrides or {}).get(item.criterion_id, "REVIEW" if item.criterion_id in missing else "PASS"),
        reason="합성 판정: 실제 모델의 의미 평가 결과가 아님.",
        evidence=[dict(source_id="context", path=["request"], quote="알림 변경은 요청하지 않았다.")])
        for item in p.rubric], "limitations": ["평가기 계약 검사 전용"]}


def test_catalog_covers_existing_23_parents_and_user_tc_seven():
    assert len(CRITERIA) == 26
    assert len({r["criterion_id"] for r in CRITERIA}) == len(CRITERIA)
    assert {r["parent_id"] for r in CRITERIA} == {r[1] for r in PARENTS}
    assert {r["criterion_id"] for r in CRITERIA if r["criterion_id"].startswith("TC-R")} == {f"TC-R0{i}" for i in range(1, 8)}
    for row in CRITERIA:
        assert all(row[key].strip() for key in ("applicability", "pass_condition", "fail_condition",
            "review_condition", "positive_example", "negative_example"))
        assert row["positive_example"] != row["negative_example"]
        assert len(row["evidence_requirements"]) == len(set(row["evidence_requirements"]))
        assert row["evidence_requirements"]


@pytest.mark.parametrize("agent,count", [("A1", 6), ("A2", 10), ("A3", 6), ("A4", 6)])
def test_role_profile_only_selects_own_and_common_criteria(agent, count):
    original = packet(agent)
    old = original.model_dump()
    p = apply_role_profile(original)
    assert original.model_dump() == old
    assert len(p.rubric) == count
    assert p.rubric_profile == VERSION
    assert not any(r.criterion_id.startswith("E0") for r in p.rubric)
    assert coverage(p)["scope"] == "ROLE_ONLY"


def test_custom_subset_not_presented_as_whole_role():
    assert coverage(packet())["scope"] == "CUSTOM_SUBSET"


@pytest.mark.parametrize("mutation", ["missing", "weaken", "agent", "requirements", "parent", "reorder"])
def test_profile_cannot_be_silently_weakened(mutation):
    p = apply_role_profile(packet())
    if mutation == "missing": p.rubric.pop()
    if mutation == "weaken": p.rubric[0].pass_condition = "Always pass"
    if mutation == "agent": p.agent_id = "A1"
    if mutation == "requirements": p.rubric[0].evidence_requirements = []
    if mutation == "parent": p.rubric[0].parent_id = "A4-01"
    if mutation == "reorder": p.rubric.reverse()
    with pytest.raises(ValueError): preview(p, "configured-model")


@pytest.mark.parametrize("value", [None, "", "  ", [], {}, False, 0])
def test_empty_evidence_is_not_coverage(value):
    p = apply_role_profile(packet())
    p.sources["trace"] = value
    rows = coverage(p)["criteria"]
    assert rows[0]["missing_sources"] == ["trace"]
    assert rows[0]["status"] == "MISSING_EVIDENCE"


@pytest.mark.parametrize("verdict", ["PASS", "NOT_APPLICABLE"])
def test_missing_trace_cannot_be_passed_or_dismissed(verdict):
    p = apply_role_profile(packet())
    with pytest.raises(ValueError): validate_judgment(p, response(p, {"C01": verdict}))


@pytest.mark.parametrize("verdict", ["REVIEW", "FAIL"])
def test_missing_trace_allows_review_or_evidenced_violation(verdict):
    p = apply_role_profile(packet())
    assert validate_judgment(p, response(p, {"C01": verdict})).judgments[0].verdict == verdict


def test_presence_is_not_semantic_or_truth_verification():
    p = apply_role_profile(packet())
    p.sources["trace"] = {"note": "SYNTHETIC delivery record"}
    assert all(r["status"] == "READY_FOR_REVIEW" for r in coverage(p)["criteria"])
    assert "합격이 아님" in coverage(p)["note"]


def test_profile_changes_approval_and_keeps_code_checks_hidden():
    p = packet()
    p.code_checks = []
    old = preview(p, "configured-model")["approval_sha256"]
    current = preview(apply_role_profile(p), "configured-model")
    assert current["approval_sha256"] != old
    sent = json.loads(current["request"]["input"][1]["content"])
    assert sent["rubric_profile"] == VERSION
    assert "code_checks" not in sent
    assert len(sent["rubric"]) == 10


def test_cli_role_preview_is_keyless_and_network_free(capsys, monkeypatch):
    from eval_harness.cli import main
    import eval_harness.openai_judge as module
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(module, "new_client", lambda: pytest.fail("No external request permitted"))
    assert main(["judge-openai", "--packet", str(ROOT / "benchmarks/v2/role_packet.example.json"),
                 "--model", "configured-model", "--role-rubric"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["maximum_calls"] == 1
    rows = result["rubric_coverage"]["criteria"]
    assert len(rows) == 10
    assert sum(bool(r["missing_sources"]) for r in rows) == 2


def test_development_gap_pair_requires_only_requested_missing_information():
    dataset = yaml.safe_load((ROOT / "benchmarks/development/agent_behavior_cases.yaml").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in dataset["cases"]}
    missing = cases["CASE-A1-GAP-001"]
    normal = cases["CASE-A1-NO-UNREQUESTED-GAP-001"]
    assert missing["expected_core"]["required_gaps"] == ["alarm_color"]
    assert "문구·갱신 주기 변경은 요청하지 않음" in missing["input_summary"]
    assert normal["expected_core"]["required_gaps"] == []
    assert normal["expected_core"]["recommendation"] == "Proceed"


def test_role_item_results_do_not_mutate_the_catalog():
    before = deepcopy(CRITERIA)
    result = role_items("A2")
    result[0]["evidence_requirements"].clear()
    assert before == CRITERIA


def test_invalid_role_rejected():
    with pytest.raises(ValueError): role_items("E2E")


@pytest.mark.parametrize("invalid_pass", [False, True])
def test_full_role_result_preserves_coverage_and_missing_evidence_contract(tmp_path, invalid_pass):
    p = apply_role_profile(packet())
    calls = []
    data = response(p, {"C01": "PASS"} if invalid_pass else None)
    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="resp-synthetic", status="completed", output_parsed=data)
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    out = evaluate(p, "configured-model", preview(p, "configured-model")["approval_sha256"],
                   tmp_path, client_factory=lambda: client)
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert result["api_request_attempts"] == 0
    assert result["rubric_coverage"]["scope"] == "ROLE_ONLY"
    assert result["status"] == ("ERROR" if invalid_pass else "COMPLETED")
    assert result["assessment"] == ("INCOMPLETE" if invalid_pass else "REVIEW")
    assert result["performance_claim_eligible"] is False


def test_documentation_lists_every_criterion_and_role_counts():
    document = (ROOT / "docs/12_DETAILED_RUBRIC.md").read_text(encoding="utf-8")
    assert all(f"| {row['criterion_id']} |" in document for row in CRITERIA)
    assert "A1 6개, A2 10개, A3 6개, A4 6개" in document
