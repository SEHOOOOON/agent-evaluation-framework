import copy
import json

import pytest
from fastapi.testclient import TestClient

from eval_harness.web.app import create_app
from eval_harness.web.recorded import list_reports, read_report, result_path, MAX_BYTES

RID = "V2-EVAL-20260920-120000-abcdef"


def sample():
    return {
        "mode": "RECORDED_REVIEW", "target_executed": False,
        "stability_measured": False, "source_unchanged": True,
        "case_count": 1, "counts": {"REVIEW": 1},
        "results": [{
            "case_id": "C1", "run_id": "RUN-example", "title": "검토 사례",
            "status": "REVIEW", "oracle_status": "DRAFT",
            "checks": [{"rule": "R1", "status": "PASS", "reason": "값 일치"}],
            "agent_evaluation": {"groups": {"A1": [{
                "criterion_id": "A1-01", "objective": "요구사항 보존",
                "status": "PARTIAL", "evidence_rules": ["R1"],
            }]}},
        }],
    }


def save(root, data, rid=RID):
    folder = root / rid
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "result.json"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return target


def test_recorded_projection_is_read_only(tmp_path):
    path = save(tmp_path, sample())
    before = path.read_bytes()
    report = read_report(tmp_path, RID)
    assert path.read_bytes() == before
    assert report["counts"] == {"REVIEW": 1}
    assert report["quality_score"] is None
    assert report["source_unchanged_at_review"] is True
    assert report["target_executed"] is report["stability_measured"] is False
    assert report["cases"][0]["groups"]["A1"]["status"] == "PARTIAL"
    assert report["cases"][0]["groups"]["A4"]["status"] == "NOT_EVALUATED"


@pytest.mark.parametrize('missing', ['user_questions', '제외된_정보_부족'])
def test_actual_producer_unmeasured_result_is_readable(tmp_path, known_bad_root, missing):
    from test_v2_recorded import sample as source_sample, edit, refresh_links
    from eval_harness.v2_recorded import evaluate
    from eval_harness.agent_rubric import summarize_roles
    source = tmp_path / 'source'
    case = source_sample(source)
    name = 'agent1_change_analysis.json' if missing == 'user_questions' else 'agent2_test_design.json'
    edit(source, name, lambda value: value.pop(missing))
    refresh_links(source)
    row = evaluate(source, case)
    assert any(c['status'] == 'NOT_EVALUATED' for c in row['checks'])
    row['agent_evaluation'] = summarize_roles(row['checks'])
    data = sample()
    data['results'] = [row]
    data['counts'] = {row['status']: 1}
    report_root = tmp_path / 'reports'
    path = save(report_root, data)
    original = path.read_bytes()
    with TestClient(create_app(known_bad_root, report_root)) as client:
        assert client.get('/api/recorded-reviews').status_code == 200
        response = client.get(f'/api/recorded-reviews/{RID}')
    assert response.status_code == 200
    assert any(c['status'] == 'NOT_EVALUATED' for c in response.json()['cases'][0]['checks'])
    assert path.read_bytes() == original


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(target_executed=True),
    lambda d: d.update(stability_measured=True),
    lambda d: d.update(mode="LIVE"),
    lambda d: d.update(case_count=5),
    lambda d: d.update(counts={"PASS": 1}),
    lambda d: d.update(results=[]),
    lambda d: d["results"].append(copy.deepcopy(d["results"][0])),
    lambda d: d["results"][0].update(status="PASS"),
    lambda d: d["results"][0]["checks"].append(copy.deepcopy(d["results"][0]["checks"][0])),
    lambda d: d["results"][0]["agent_evaluation"]["groups"]["A1"][0].update(status="PASS"),
    lambda d: d["results"][0]["agent_evaluation"]["groups"]["A1"][0].update(evidence_rules=["MISSING"]),
])
def test_invalid_review_not_accepted(tmp_path, mutation):
    data = sample()
    mutation(data)
    save(tmp_path, data)
    with pytest.raises((ValueError, TypeError)):
        read_report(tmp_path, RID)


@pytest.mark.parametrize("states,expected", [
    (["CHECKED"], "CHECKED"), (["CHECKED", "NOT_EVALUATED"], "PARTIAL"),
    (["CHECKED", "ERROR"], "ERROR"), (["FAIL", "ERROR"], "FAIL"),
    (["NOT_EVALUATED"], "NOT_EVALUATED"),
])
def test_role_display_rollup(tmp_path, states, expected):
    data = sample()
    template = data["results"][0]["agent_evaluation"]["groups"]["A1"][0]
    data["results"][0]["agent_evaluation"]["groups"]["A1"] = [
        dict(template, criterion_id=f"A1-{n}", status=state) for n, state in enumerate(states)
    ]
    save(tmp_path, data)
    assert read_report(tmp_path, RID)["cases"][0]["groups"]["A1"]["status"] == expected


def test_empty_list_and_strict_report_paths(tmp_path):
    assert list_reports(tmp_path)["reports"] == []
    save(tmp_path, sample(), "JUDGE-ignored")
    assert list_reports(tmp_path)["reports"] == []
    for bad in ("../result.json", "V2-EVAL-invalid", RID + "/../other"):
        with pytest.raises(ValueError):
            result_path(tmp_path, bad)
    target = save(tmp_path, sample())
    target.write_bytes(b" " * (MAX_BYTES + 1))
    assert list_reports(tmp_path)["reports"] == []


def test_report_index_newest_first_bounded(tmp_path):
    for n in range(101):
        save(tmp_path, sample(), f"V2-EVAL-20260920-{n:06d}-abcdef")
    result = list_reports(tmp_path)
    assert result["truncated"] is True
    assert len(result["reports"]) == 100
    assert result["reports"][0]["id"] == "V2-EVAL-20260920-000100-abcdef"


def test_safe_api_and_corrupt_report(known_bad_root, runs_root):
    data = sample()
    data["private_field"] = "must not leak"
    data["results"][0]["checks"][0]["reason"] = "sk-" + "x" * 24 + " C:/Users/private/secret.json"
    target = save(runs_root, data)
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        assert client.get("/api/recorded-reviews").json()["reports"] == [{"id": RID}]
        response = client.get(f"/api/recorded-reviews/{RID}")
        assert response.status_code == 200
        assert "private_field" not in response.text
        assert "secret.json" not in response.text
        assert "sk-" not in response.text
        target.write_text("{broken", encoding="utf-8")
        assert client.get(f"/api/recorded-reviews/{RID}").status_code == 422
        assert client.get("/api/recorded-reviews/invalid").status_code == 422
        assert client.post(f"/api/recorded-reviews/{RID}").status_code == 405


def test_new_dashboard_assets_and_safe_text_rendering(known_bad_root, runs_root):
    with TestClient(create_app(known_bad_root, runs_root)) as client:
        html = client.get("/").text
        js = client.get("/static/recorded.js").text
        assert 'id="recordedPanel"' in html and 'id="selfPanel"' in html
        assert "저장 결과 검토" in html and "평가기 자체 검증" in html
        assert "textContent" in js and "&lt;" in js and "&quot;" in js
        assert client.get("/api/evaluation-design").json()["primary_evaluation_mode"] == "full_pipeline_repeat"
