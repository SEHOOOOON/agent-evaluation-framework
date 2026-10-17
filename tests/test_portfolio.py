"""Static portfolio checks, not new agent evaluation or model accuracy tests."""
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'portfolio'


def evidence():
    text = (PAGE / 'evidence.js').read_text(encoding='utf-8')
    return json.loads(text.split('window.PORTFOLIO_EVIDENCE = ', 1)[1].strip().removesuffix(';'))


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links, self.resources = [], [], []
        self.feed((PAGE / 'report.html').read_text(encoding='utf-8'))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a':
            self.links.append(attrs.get('href', ''))
        if tag in ('link', 'script', 'img'):
            self.resources.append(attrs.get('src') or attrs.get('href'))


def test_links_ids_and_local_resources():
    page = PageParser()
    assert len(page.ids) == len(set(page.ids))
    rubric = json.loads((PAGE / 'rubric.js').read_text(encoding='utf-8').split('window.PORTFOLIO_RUBRIC = ', 1)[1].strip().removesuffix(';'))
    dynamic_ids = {'criterion-' + row['criterion_id'] for row in rubric['criteria']}
    unchecked = []
    for link in page.links:
        if link.startswith('#'):
            assert link[1:] in set(page.ids) | dynamic_ids
        elif not link.startswith('https://'):
            path, _, anchor = link.partition('#')
            target = Path(os.path.normpath(PAGE / path))  # no symlink/junction resolution
            # Sibling portfolio repositories are only checked when checked out.
            if ROOT not in target.parents and not (ROOT.parent / target.relative_to(ROOT.parent).parts[0]).is_dir():
                unchecked.append(link)
                continue
            assert target.is_file(), link
            if anchor:
                assert f'id="{anchor}"' in target.read_text(encoding='utf-8'), link
    assert all(resource and (PAGE / resource).is_file() for resource in page.resources)
    # Report sibling links as unchecked, never as a pass or an environment failure.
    if unchecked:
        pytest.skip(f'형제 저장소 없음: 링크 {len(unchecked)}개 미확인 {sorted(set(unchecked))}')


def test_all_26_original_items_and_missing_scores_preserved():
    data = evidence()
    criteria = data['criteria']
    assert len(criteria) == len({c['criterion_id'] for c in criteria}) == 26
    assert sum(c['score'] is not None for c in criteria) == data['summary']['evaluated'] == 19
    assert sum(c['verdict'] == 'REVIEW' for c in criteria) == 6
    assert sum(c['verdict'] == 'NOT_APPLICABLE' for c in criteria) == 1
    failed = [c for c in criteria if c['verdict'] == 'FAIL']
    assert [(c['criterion_id'], c['score']) for c in failed] == [('A3-01', 3)]
    assert data['rubric'] == 'role-rubric-2.2'


def test_matrix_is_10_scenarios_7_paths_not_70_browser_runs():
    rows = evidence()['matrix']
    assert len(rows) == 10
    expected = {'hold', 'no-consent', 'approve', 'hold-approve', 'duplicate', 'stale-srs', 'rollback'}
    for row in rows:
        assert row['browser'] == 'PASS' and row['restored'] is True
        assert {b['branch'] for b in row['branches']} == expected
        assert len(row['branches']) == 7
        assert all(b['status'] == 'PASS' for b in row['branches'])
    page_js = (PAGE / 'page.js').read_text(encoding='utf-8')
    assert all("'" + branch + "'" in page_js for branch in expected)


def test_snapshot_matches_available_originals():
    data = evidence()
    originals = {
        'scorecard': ROOT / 'evaluation-runs/judge-ready-20261002/live/full_scorecard.raw.json',
        'matrix': ROOT.parent / 'qa-agent-pipeline-v2/runs/stab1003/m70/summary.json',
    }
    for source in data['sources']:
        path = originals[source['id']]
        assert re.fullmatch('[0-9a-f]{64}', source['sha256'])
        if not path.exists():
            continue  # Original private runs are intentionally not distributed.
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
        raw = json.loads(path.read_text(encoding='utf-8'))
        if source['id'] == 'scorecard':
            for row, original in zip(data['criteria'], raw['criteria'], strict=True):
                assert all(value == original.get(key) for key, value in row.items())
        else:
            assert raw['api_calls'] == raw['official_approvals'] == 0
            assert raw['transactions_passed'] == 70 and raw['browser_runs_requested'] == 10


def test_no_credentials_absolute_paths_or_outbound_execution():
    files = [PAGE / name for name in ('index.html', 'report.html', 'story.css', 'story.js', 'report-view.css', 'report-view.js', 'style.css', 'page.js', 'evidence.js', 'rubric.js', 'plain-language.js')]
    text = '\n'.join(p.read_text(encoding='utf-8') for p in files)
    assert not re.search(r'sk-[A-Za-z0-9_-]{16,}|(?<![A-Za-z])[A-Za-z]:[\\/]|file://|OPENAI_API_KEY=', text)
    scripts = '\n'.join(p.read_text(encoding='utf-8') for p in files if p.suffix == '.js')
    assert not re.search(r'fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon', scripts)


def test_scope_and_keyboard_controls_are_explicit():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    for phrase in ('저장된 실행·평가 기록', '일부 과정은 미리 준비한 응답(모델 대역)으로 대신', 'AI가 전체 과정을 70번 실행한 것은 아닙니다',
                   '여러 번 잘하는지는 아직 확인하지 않았습니다', '당시 기준 2.2', '원본 점수와 이유는 변경하지 않았습니다'):
        assert phrase in html
    assert 'type="button" class="criteria-link"' in js
    assert 'aria-label=' in js
    assert "querySelector('summary').focus()" in js
    assert '<details id="criteria-details"' in html
    assert '<details id="assets-details"' in html


def test_current_26_definitions_match_runtime_without_regrading_old_scores():
    from eval_harness.detailed_rubric import CRITERIA, VERSION, SCORE_VERSION, evaluation_method
    raw = (PAGE / 'rubric.js').read_text(encoding='utf-8')
    data = json.loads(raw.split('window.PORTFOLIO_RUBRIC = ', 1)[1].strip().removesuffix(';'))
    assert data['version'] == VERSION == 'role-rubric-2.5'
    assert data['score_version'] == SCORE_VERSION
    assert data['criteria'] == [dict(**row, method=evaluation_method(row['criterion_id'])) for row in CRITERIA]
    assert {c['criterion_id'] for c in data['criteria']} == {c['criterion_id'] for c in evidence()['criteria']}
    assert {owner: sum(c['owner'] == owner for c in data['criteria'])
            for owner in ('A1', 'A2', 'A3', 'A4', 'COMMON', 'E2E')} == {
                'A1': 4, 'A2': 8, 'A3': 4, 'A4': 4, 'COMMON': 2, 'E2E': 4}
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    for field in ('applicability', 'pass_condition', 'fail_condition', 'review_condition',
                  'positive_example', 'negative_example'):
        assert "'" + field + "'" in js
    assert '${escape(definition[key])}' in js
    assert '과거 점수를 다시 매긴 것은 아닙니다' in js
    assert '당시 평가 결과 · 2026.10.02 · 기준 2.2' in js
    assert evidence()['rubric'] == 'role-rubric-2.2'


def test_dashboard_collapses_groups():
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    assert 'class="role-group"' in js and 'class="criterion"' in js
    assert 'group.open = true' in js and 'detail.open = false' in js
    assert ' open' not in re.search(r'<details class="role-group"[^>]*>', js).group()
    assert '항목 목록 모두 보기' in html and '모두 접기' in html
    # The 2026-10-04 page backups were removed by the user on 2026-10-15.
    assert 'backup-' not in html


def test_summary_scores_have_no_bars_and_keep_source_based_counts():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    assert html.count('role="tab"') == 3
    assert 'id="score-chart"' in html
    assert '5점 만점' in html
    assert 'bar-track' not in js and 'bar-fill' not in js
    css = (PAGE / 'style.css').read_text(encoding='utf-8')
    assert '.bar-track' not in css and '.bar-fill' not in css
    assert 'Number.isFinite(score)' in js  # Missing scores must not become zero.
    assert "group.evaluated+'/'+group.total+'개 반영'" in js
    assert "row.browser === 'PASS'" in js and 'row.restored === true' in js
    assert 'evidence.matrix.flatMap(row=>row.branches)' in js
    assert '당시 기준 2.2로 평가한 항목의 평균' in html
    assert '이후 잘못된 평가가 확인된 항목' in html
    assert '전체 품질 점수는 아닙니다' in html
    assert html.index('id="criteria-details"') < html.index('id="historical-results"') < html.index('id="score-chart"')


def test_role_context_is_explained():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    assert '이 프로젝트에 맞춘 26개 항목' in html
    assert '공인된 평가 점수는 아닙니다' in html
    for question in ('사용자의 요청을 제대로 이해했나요?',
                     '시험 설명만 보고 그대로 따라 할 수 있나요?',
                     '정해진 순서대로 시험하고 확인했나요?',
                     '실제로 한 시험을 사실대로 보고했나요?'):
        assert question in js
    assert '왜 확인하나요?' in js and 'context.checks.map' in js
    assert '세부 기준 보기' in js


def test_detail_layout_keeps_every_field_without_truncating_conditions():
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    for field in ('objective', 'applicability', 'pass_condition', 'fail_condition',
                  'review_condition', 'positive_example', 'negative_example'):
        assert f'data-field="{field}"' in js
        assert "'" + field + "'" in js
    assert '${escape(definition[key])}' in js
    assert 'data-source-field="${key}"' in js
    assert 'class="decision-grid"' in js
    for name in ('criterion-extra examples', 'criterion-extra original-reason'):
        assert f'<details class="{name}">' in js  # Default closed.
    assert '${escape(item.reason)}' in js
    assert js.index('class="criterion-extra original-reason"') < js.index('class="followup"')


def test_role_shortcut_opens_items_but_keeps_extra_evidence_collapsed():
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    assert "group.querySelectorAll('.criterion').forEach(detail=>{detail.open = group === selected;});" in js
    assert "selected.querySelectorAll('.criterion-extra').forEach(detail=>{detail.open = false;});" in js
    assert "selected.querySelector('summary').focus()" in js



def test_plain_explanations_cover_all_items_without_changing_scoring_data():
    raw = (PAGE / 'plain-language.js').read_text(encoding='utf-8')
    data = json.loads(raw.split('window.PORTFOLIO_PLAIN = ', 1)[1].strip().removesuffix(';'))
    rows = data['criteria']
    assert data['version'] == 'role-rubric-2.5'
    assert len(rows) == len({row['criterion_id'] for row in rows}) == 26
    assert {row['criterion_id'] for row in rows} == {row['criterion_id'] for row in evidence()['criteria']}
    for row in rows:
        for key in ('title', 'applicability', 'pass_condition', 'fail_condition',
                    'positive_example', 'negative_example'):
            assert isinstance(row[key], str) and row[key].strip()
        text = ' '.join(value for key, value in row.items() if key != 'criterion_id')
        assert not re.search(r'execution_spec|assertion|결정론|산출물|비적용|Checkpoint|REVIEW|TC|SRS', text)
    assert '판단에 필요한 자료' in data['review_condition']
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    assert '<details class="criterion-extra technical-source">' in js
    assert '${escape(item.reason)}' in js and '${escape(item.title)}' in js


def test_six_area_summary_and_clean_pending_display():
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    assert "roles.map(([key,name,description])" in js
    assert 'roles.filter(' not in js
    assert "['COMMON','공통 기준'" in js and "['E2E','연결 기준'" in js
    assert 'COMMON: {question:' in js and 'E2E: {question:' in js
    assert 'class="role-row"' in js
    for phrase in ('점수 없음', '점수 없는 항목', '채점'):
        assert phrase not in js + html
    assert 'class="criterion-score"' not in js
    assert 'class="role-score"' not in js
    assert 'class="historical-score"' in js
    assert '0점이 아닙니다' in html and '미평가' in html
    assert 'group.evaluated' in js  # Partial averages retain their denominator.


def test_improvement_and_sources_keep_scope_in_short_sections():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    for phrase in ('발견한 문제', '바꾼 부분', '확인한 결과', '새 응답 1건',
                   '전체 과정은 다른 문제로 중단', '중단 기록을 읽고 코드를 검토해 원인을 확인',
                   '과거 점수를 다시 계산한 것은 아님'):
        assert phrase in html
    assert '<details class="source-files">' in html
    assert html.index('<details class="source-files">') < html.index('id="source-hashes"')


def test_new_local_asset_replay_is_separate_and_matches_evidence():
    data = json.loads((PAGE / 'local-verification.json').read_text(encoding='utf-8'))
    assert data['api_calls'] == data['official_approvals'] == 0
    assert data['original_scores_changed'] is False
    assert len(data['cases']) == 2 and sum(len(row['branches']) for row in data['cases']) == 7
    assert len(data['limitations']) == 4
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    assert '<details id="latest-local-checks"' in html
    assert '기존 70건 및 위 AI 점수와 별도' in html
    original = ROOT.parent / 'qa-agent-pipeline-v2/runs/c1004/summary.json'
    if original.exists():
        assert hashlib.sha256(original.read_bytes()).hexdigest() == data['source_sha256']
        raw = json.loads(original.read_text(encoding='utf-8'))
        assert raw['status'] == 'PASS' and raw['protected_unchanged'] is True
        for item in data['cases']:
            row = next(row for row in raw['results'] if row['case'] == item['id'])
            assert row['revalidation']['outcome'] == item['current_product_revalidation']
            assert row['registered_reuse']['status'] == item['registered_code_reuse']
            assert row['registered_reuse']['restoration_marker_present'] == item['restored']
            assert all(branch['status'] == 'PASS' for branch in row['branches'])
            assert [branch['branch'] for branch in row['branches']] == item['branches']


def test_a3_comparison_snapshot_matches_original_scores_and_separates_sources():
    data = json.loads((PAGE / 'a3-comparison.json').read_text(encoding='utf-8'))
    assert not data['original_26_scores_changed']
    assert not data['program_score_included_in_ai_average']
    assert not data['performance_claim_eligible']
    assert data['new_api_calls_for_page_update'] == 0
    assert data['oracle_status'] == 'DRAFT'
    assert len(data['cases']) == 3
    assert [c['average_5'] for c in data['cases']] == [5.0, 5.0, 3.4]
    assert [c['program_score'] for c in data['cases']] == [5, 5, 0]
    assert data['cases'][2]['origin'] == 'SYNTHETIC_NOT_EXECUTED'
    assert data['cases'][2]['product_outcome'] == 'NOT_EXECUTED'
    for case in data['cases']:
        assert case['ai_calls'] == 1
        assert set(case['scores']) == set(data['ai_criteria'])
        assert sum(case['scores'].values()) / len(data['ai_criteria']) == case['average_5']
        assert re.fullmatch('[0-9a-f]{64}', case['source_sha256'])
        source = ROOT / case['source']
        if source.exists():
            assert hashlib.sha256(source.read_bytes()).hexdigest() == case['source_sha256']
            raw = json.loads(source.read_text(encoding='utf-8'))
            assert raw['status'] == 'COMPLETED'
            assert raw['judge_model'] == data['model'] and raw['rubric_version'] == data['rubric']
            assert {j['criterion_id']: j['score'] for j in raw['judgments']} == case['scores']
        source = ROOT / case['program_source']
        if source.exists():
            raw = json.loads(source.read_text(encoding='utf-8'))
            assert raw['criterion_id'] == 'A3-03' and raw['score'] == case['program_score']


def test_a3_comparison_html_preserves_scope_and_all_snapshot_values():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    section = html.split('<section id="a3-comparison"', 1)[1].split('</section>', 1)[0]
    data = json.loads((PAGE / 'a3-comparison.json').read_text(encoding='utf-8'))
    assert '<details id="comparison-details" class="info">' in section  # closed by default
    for case in data['cases']:
        row = re.search(r'<tr data-case="' + case['id'] + r'">(.*?)</tr>', section, re.S).group(1)
        assert case['label'] in row
        assert f'class="comparison-score">{case["average_5"]:.1f}<' in row
    for criterion in [*data['ai_criteria'], data['program_criterion']]:
        row = re.search(r'<tr[^>]*data-criterion="' + criterion + r'">(.*?)</tr>', section, re.S).group(1)
        values = [int(n) for n in re.findall(r'<td[^>]*>(\d+)', row)]
        assert values == [c['program_score'] if criterion == 'A3-03' else c['scores'][criterion] for c in data['cases']]
    for phrase in ('가상 비교 사본', '실제 제품 실행 아님', 'AI 평균에서 제외',
                   '각각 AI 평가 1회', '검사 한 개 누락만으로', '상단 전원 시나리오의 평가 점수와는 별도'):
        assert phrase in section
    assert not re.search(r'[\u4e00-\u9fff]|sk-[A-Za-z0-9_-]{16,}|[A-Za-z]:[\\/]', json.dumps(data, ensure_ascii=False))


def test_decision_examples_precede_counts_without_claiming_a_new_evaluation():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    assert html.index('id="a3-comparison"') < html.index('id="latest-repeat"') < html.index('id="qa-improvement"')
    assert html.index('id="latest-repeat"') < html.index('id="score-chart"')
    assert '세 사례의 평가 결과와 원본 해시' in html
    assert '5fdc882' in html and '934526a' in html
    assert '이 버전에서 다시 실행한 것은 아닙니다' in html


def test_latest_local_comparison_keeps_product_outcome_separate_from_grades():
    data = json.loads((PAGE / 'latest-local-comparison.json').read_text(encoding='utf-8'))
    assert data['api_calls'] == data['p1_executions'] == 0
    assert not data['original_26_scores_changed'] and not data['repeated_stability_claim']
    assert [c['product_status'] for c in data['cases']] == ['PASSED', 'ASSERTION_FAILED']
    assert [c['recommendation'] for c in data['cases']] == ['PASS', 'HUMAN_REVIEW']
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    section = html.split('<details id="latest-evaluation"', 1)[1].split('</details>', 1)[0]
    assert ' open' not in section.split('>', 1)[0]
    for case in data['cases']:
        assert case['scores'] == {'A3-03': 5, 'A4-01': 5, 'A4-03': 5}
        assert case['evaluated_count'] == 3 and case['remaining_unscored'] == 23
        for criterion, value in case['scores'].items():
            row = re.search(r'<tr data-local-criterion="' + criterion + '">(.*?)</tr>', section, re.S).group(1)
            assert [int(x) for x in re.findall(r'<td>(\d+)</td>', row)] == [value, value]
        for kind in ('scorecard', 'report'):
            source = ROOT / case[kind + '_source']
            assert re.fullmatch('[a-f0-9]{64}', case[kind + '_sha256'])
            if source.exists():
                assert hashlib.sha256(source.read_bytes()).hexdigest() == case[kind + '_sha256']
        source = ROOT / case['scorecard_source']
        if source.exists():
            card = json.loads(source.read_text(encoding='utf-8'))
            assert {r['criterion_id']: r['score'] for r in card['criteria'] if r['score'] is not None} == case['scores']
            assert sum(r['score'] is None for r in card['criteria']) == 23
    for phrase in ('23개는 미평가', '새로운 AI 평가는 하지 않음', '반복 안정성이나 개선율로 비교하지 않습니다'):
        assert phrase in section
    assert not re.search(r'sk-[A-Za-z0-9_-]{16,}|[A-Za-z]:[\\/]', json.dumps(data, ensure_ascii=False))

def test_latest_closeout_separates_pending_and_preserves_source_states():
    from collections import Counter
    folder = PAGE / "evidence/closeout-20261009"
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    extract = json.loads((folder / "criteria.json").read_text(encoding="utf-8"))
    assert len(extract["criteria"]) == 26
    assert Counter(x["status"] for x in extract["criteria"]) == summary["criteria_states"]
    assert summary["criteria_states"] == {"SCORED": 21, "NOT_APPLICABLE": 1, "UNASSESSED": 3, "NEEDS_REVIEW": 1}
    assert summary["overall_quality_score"] is None
    assert summary["human_review"] == "PENDING_NOT_CALIBRATED"
    assert summary["p1_new_calls"] == 0 and summary["api_attempts"] == 4
    source = ROOT / "evaluation-runs/p2-closeout-20261009/scorecard.json"
    if source.exists():
        assert hashlib.sha256(source.read_bytes()).hexdigest() == extract["source_sha256"]
        original = json.loads(source.read_text(encoding="utf-8"))
        for got, old in zip(extract["criteria"], original["criteria"], strict=True):
            for key in ("criterion_id", "owner", "title", "status", "score_recorded"):
                assert got[key] == old[key]
            assert got["role_assessments"] == [
                {k: v for k, v in row.items() if k in ("role", "verdict", "score", "reason")}
                for row in old["ai_observations"]
            ]
            expected = {k: v for k, v in old["python"].items() if k in ("score", "verdict", "reason")} if isinstance(old["python"], dict) else None
            assert got["program_assessment"] == expected
    html = (PAGE / "report.html").read_text(encoding="utf-8")
    assert html.index('id="latest-power-evaluation"') < html.index('id="score-chart"')
    assert "전체 평균은 계산하지 않았으며" in html


def test_connection_proposals_are_not_final_scores_or_approval():
    data = json.loads((PAGE / "evidence/closeout-20261009/connection-review.json").read_text(encoding="utf-8"))
    assert data["kind"] == "ASSISTANT_REVIEW_PROPOSAL_NOT_USER_APPROVED_OR_API_JUDGE"
    assert data["human_review_status"] == "PENDING"
    assert data["overall_score"] is None
    assert [x["criterion_id"] for x in data["items"]] == ["E01", "E02", "E03", "E04"]
    assert data["items"][-1]["score_proposal"] is None


def test_current_criteria_and_latest_results_are_separate_from_historical_scores():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    assert html.index('id="latest-power-evaluation"') < html.index('id="a3-comparison"') < html.index('id="evaluation-method"')
    assert '<details id="historical-results" class="disclosure-section">' in html
    assert html.index('id="criteria-details"') < html.index('id="historical-results"')
    assert 'class="criterion-score"' not in js and 'class="role-score"' not in js
    assert 'class="historical-score"' in js and '${escape(item.reason)}' in js
    assert '편집 출발 버전' in html and 'b820571' in html and 'bdb6a15' in html
    assert 'full-flow-0.15' in html and '사람의 최종 검토 전' in html
    assert "window.addEventListener('hashchange', revealAnchor)" in js
    assert "parent.tagName === 'DETAILS'" in js
    plain = (PAGE / 'plain-language.js').read_text(encoding='utf-8')
    assert '자산를' not in plain
    assert '변경 영향을 빠짐없이 분석했나요?' in plain


def test_evaluation_diagram_explains_roles_without_claiming_new_results():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    section = html.split('<section id="evaluation-method"', 1)[1].split('</section>', 1)[0]
    assert section.count('class="flow-stage ') == 3
    for text in ('P1에서 남긴 기록', '프로그램', 'AI', '근거 부족은 평가 보류', '사람이 검토', '26개 세부 기준'):
        assert text in section
    assert 'aria-labelledby="flow-caption"' in section
    assert '<figcaption id="flow-caption">' in section
    css = (PAGE / 'style.css').read_text(encoding='utf-8')
    assert '.shell{width:min(1100px,calc(100% - 48px))' in css
    assert '@media(max-width:800px){.evaluation-flow{grid-template-columns:1fr' in css
    assert 'bdb6a15' in html  # Provenance remains available in collapsed sources.


def test_latest_role_scores_use_unique_owners_and_keep_pending_separate():
    from html import escape
    data = json.loads((PAGE / 'evidence/closeout-20261009/criteria.json').read_text(encoding='utf-8'))
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    assert html.count('class="latest-role"') == 6
    assert html.count('class="latest-item"') == 26
    assert len(set(re.findall(r'id="latest-criterion-([^"]+)"', html))) == 26
    for owner, total, scored in [('A1',4,4),('A2',8,8),('A3',4,4),('A4',4,3),('COMMON',2,2),('E2E',4,0)]:
        group = html.split('id="latest-role-' + owner + '"',1)[1].split('class="latest-role"',1)[0]
        assert f'data-evaluated="{scored}" data-total="{total}"' in group
        items = [x for x in data['criteria'] if x['owner'] == owner]
        values = []
        for item in items:
            records = item['role_assessments'] + ([item['program_assessment']] if item['program_assessment'] else [])
            numbers = [r['score'] for r in records if isinstance(r.get('score'), (int,float))]
            if item['status'] == 'SCORED':
                assert len(set(numbers)) == 1
                values.append(numbers[0])
            assert 'id="latest-criterion-' + item['criterion_id'] + '"' in group
        expected = f'{sum(values)/len(values):.1f} / 5' if values else '미확정'
        assert '<strong class="latest-average">' + expected + '</strong>' in group
    assert 'P1 전체의 품질 점수나 반복 성공률은 아닙니다' in html
    assert '미평가·근거 검토 상태는 0점이 아닙니다' in html


def test_latest_reasons_and_statuses_match_saved_evidence_without_rescoring():
    from html import escape
    data = json.loads((PAGE / 'evidence/closeout-20261009/criteria.json').read_text(encoding='utf-8'))
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    for item in data['criteria']:
        block = html.split('id="latest-criterion-' + item['criterion_id'] + '"',1)[1].split('</details>',1)[0]
        assert 'data-status="' + item['status'] + '"' in block
        records = item['role_assessments'] + ([item['program_assessment']] if item['program_assessment'] else [])
        for record in records:
            assert escape(record['reason']) in block
        summary = block.split('</summary>',1)[0]
        if item['status'] != 'SCORED':
            assert ' / 5' not in summary
        assert 'href="#criterion-' + item['criterion_id'] + '"' in block
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    definition = js.split('function criterionHTML',1)[1].split("document.getElementById('historical-criteria')",1)[0]
    assert 'original-reason' not in definition
    assert 'latest-criterion-${escape(item.criterion_id)}' in definition
    assert "document.getElementById('historical-criteria')" in js


def test_compact_dashboard_tabs_preserve_evidence_navigation():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    panels = ['evaluation', 'validation', 'reference']
    assert html.count('role="tabpanel"') == 3
    for name in panels:
        assert f'aria-controls="panel-{name}"' in html
        assert f'aria-labelledby="tab-{name}"' in html
        opening = re.search(r'<section id="panel-' + name + r'"[^>]*>', html).group(0)
        assert (' hidden' in opening) == (name != 'evaluation')
    assert 'selectDashboardTab' in js and 'showPanelFor(target)' in js
    assert 'showPanelFor(selected)' in js and 'showPanelFor(detail)' in js
    for key in ['ArrowRight', 'ArrowLeft', 'Home', 'End']:
        assert key in js
    assert 'id="comparison-disclosure"' in html
    assert 'id="method-disclosure"' in html
    assert 'id="improvement-disclosure"' in html
    assert 'class="compact-limit"' in html and '전체 품질 점수는 아닙니다' in html


def test_portfolio_explains_evaluation_goal_without_claiming_repeated_ai_success():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    section = html.split('id="qa-purpose"', 1)[-1] if 'id="qa-purpose"' in html else html
    for phrase in ('개인 프로젝트', '여러 시나리오에서 기준을 지키는지',
                   '같은 조건에서도 결과가 일관되는지', '새 AI 실행의 반복 일관성 검증은 아직 남아'):
        assert phrase in section
    assert '회사의 품질 지표 운영이나 공인 평가 도구를 만든 성과는 아닙니다' not in html


def test_purpose_and_readable_summary_precede_detailed_score_matrix():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    assert html.index('id="evaluation-intent"') < html.index('class="case-summary"')
    assert html.index('class="case-summary"') < html.index('id="scenario-role-scores"')
    assert '<details id="scenario-role-scores" class="info compact-fold">' in html
    intro = html.split('id="evaluation-intent"', 1)[1].split('</div>', 1)[0]
    assert '제품의 문제를 찾은 것과, 에이전트가 검사를 빠뜨린 것은 다릅니다' in intro
    assert '같은 판단 원칙을 지키는지 확인하는 것이 목표' in intro
    before_detail = html.split('id="scenario-role-scores"', 1)[0]
    assert '사람의 최종 검토 전' in before_detail
    assert '같은 조건의 새 AI 반복 실행은 아직 검증하지 않았습니다' in before_detail
    assert set(re.findall(r'data-summary="([^"]+)"', before_detail)) == {'power','normal','product','omission','pause','reuse'}


def test_readable_summary_preserves_detailed_role_scores_and_limits():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    expected_roles = {'normal':'실행 역할 · AI 3항목','product':'실행 역할 · AI 3항목',
                      'omission':'실행 역할 · AI 3항목','pause':'분석 역할 · AI 4항목',
                      'reuse':'설계 역할 · AI 8항목'}
    for case in ('power','normal','product','omission','pause','reuse'):
        summary = html.split('data-summary="'+case+'"', 1)[1].split('</tr>', 1)[0]
        detail = html.split('data-scenario="'+case+'"', 1)[1].split('</tr>', 1)[0]
        scores = re.findall(r'<b[^>]*>([0-9.]+)</b>', detail)
        headline = re.search(r'<b[^>]*>(?:각 )?([0-9.]+)</b>', summary).group(1)
        assert headline in scores
        if case == 'power':
            assert scores == ['5.0']*4
            assert '26개 중 21개 평가 · 1개 해당 없음 · 연결 4개 미확정' in summary
        else:
            assert expected_roles[case] in summary
        if case == 'omission':
            assert '가상 사본, 실제 실행 아님' in summary
            assert '검사 한 개 누락만으로 0점이 된 사례는 아닙니다' in summary
        if case == 'reuse':
            assert '실행 역할은 판단 보류' in summary
            assert '당시 TC의 문제와 에이전트 책임은 별도 검토' in summary
        if case == 'pause':
            assert '이후 설계·실행·보고는 진행하지 않았습니다' in summary
        links = re.findall(r'href="([^"]+)"', summary)
        assert links and all(link in detail for link in links)


def test_scenario_overview_keeps_role_means_and_scope_separate():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    section = html.split('<section id="scenario-overview"', 1)[1].split('</section>', 1)[0]
    assert html.index('id="scenario-overview"') < html.index('id="latest-power-evaluation"')
    assert set(re.findall(r'data-scenario="([^"]+)"', section)) == {'power','normal','product','omission','pause','reuse'}
    comparison = json.loads((PAGE / 'a3-comparison.json').read_text(encoding='utf-8'))
    for case in comparison['cases']:
        values = [v for k,v in case['scores'].items() if k.startswith('A3-')]
        average = sum(values) / len(values)
        expected = f'{average:.1f}' if average.is_integer() else f'{average:.2f}'
        row = section.split('data-scenario="'+case['id']+'"',1)[1].split('</tr>',1)[0]
        assert '>'+expected+'</b>' in row and 'AI 3항목' in row
        assert row.count('평가 범위 밖') == 3
    assert '가상 사본, 실제 실행 아님' in section
    assert '연결 4개 미확정' in section and '1개 해당 없음' in section
    assert '전체 평균이나 개선율은 계산하지 않았습니다' in section
    assert '5.0 / 5.0 / 3.4' in section  # Prior common+role mean is not silently replaced.
    assert '<details id="power-result-details" class="info compact-fold">' in html
    assert '반복 위반 횟수·수정 후 감소율' in html


def test_pause_reuse_extract_preserves_saved_judgments_and_source_hashes():
    from html import escape
    data = json.loads((PAGE / 'scenario-assessments.json').read_text(encoding='utf-8'))
    assert data['new_api_calls'] == data['p1_new_executions'] == 0
    assert data['human_review'] == 'PENDING' and not data['same_condition_repetition']
    assert data['kind'] == 'SAVED_ASSESSMENT_EXTRACT_NOT_NEW_EVALUATION'
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    rows = {r['label']: r for r in data['rows']}
    assert set(rows) == {'pause-A1','reuse-A2','reuse-A3'}
    for row in rows.values():
        assert row['rubric'] == 'role-rubric-2.5'
        assert row['evaluator'] == 'openai-judge-1.12'
        assert re.fullmatch('[0-9a-f]{64}',row['source_sha256'])
        source = ROOT / row['source']
        if source.exists():
            assert hashlib.sha256(source.read_bytes()).hexdigest() == row['source_sha256']
            original = json.loads(source.read_text(encoding='utf-8'))
            assert row['judgments'] == [{k:j.get(k) for k in ('criterion_id','verdict','score','reason')} for j in original['judgments']]
        for j in row['judgments']:
            assert escape(j['reason']) in html
    a1 = [j['score'] for j in rows['pause-A1']['judgments'] if j['criterion_id'].startswith('A1-')]
    a2 = [j['score'] for j in rows['reuse-A2']['judgments'] if j['criterion_id'].startswith(('A2-','TC-'))]
    assert len(a1)==4 and sum(a1)/len(a1)==5
    assert len(a2)==8 and sum(a2)/len(a2)==4.75
    assert '>4.75</b>' in html
    a3 = {j['criterion_id']: j for j in rows['reuse-A3']['judgments']}
    assert a3['A3-04']['verdict']=='REVIEW' and a3['A3-04']['score'] is None
    assert a3['A3-01']['verdict']==a3['A3-02']['verdict']=='NOT_APPLICABLE'
    assert '보류로 미실행' in html and '복원 근거 부족' in html


def test_current_evaluator_documentation_matches_code_without_rewriting_history():
    from eval_harness.openai_judge import VERSION
    doc = (ROOT / 'docs/12_DETAILED_RUBRIC.md').read_text(encoding='utf-8')
    match = re.search(r'현재 점수 계약은 [^\n]+평가자 버전은 (openai-judge-[\d.]+)', doc)
    assert match is not None and match.group(1).rstrip('.') == VERSION
    comparison = json.loads((PAGE / 'a3-comparison.json').read_text(encoding='utf-8'))
    assert comparison['evaluator'] == 'openai-judge-1.10'  # Recorded historical evaluator, not the current one.
    assert comparison['original_26_scores_changed'] is False


def test_zero_review_and_non_applicable_are_distinct_and_old_zero_is_preserved():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    assert '충족하지 못했다는 근거가 있음' in html
    assert '판단할 근거가 부족하면 <b>판단 보류</b>' in html
    assert '적용되지 않는다는 근거가 있으면 <b>해당 없음</b>' in html
    assert '둘 다 0점으로 계산하지 않습니다' in html
    assert '미실행 자체가 곧 0점은 아닙니다' in html
    data = json.loads((PAGE / 'a3-comparison.json').read_text(encoding='utf-8'))
    case = next(x for x in data['cases'] if x['id'] == 'omission')
    assert case['origin'] == 'SYNTHETIC_NOT_EXECUTED' and case['product_outcome'] == 'NOT_EXECUTED'
    assert case['hypothetical_claim'] == 'PASS'
    assert case['scores']['A3-04'] == 0 and case['average_5'] == 3.4


def plain_copy():
    return json.loads((PAGE / 'plain-language.js').read_text(encoding='utf-8').split('window.PORTFOLIO_PLAIN = ', 1)[1].strip().removesuffix(';'))


def test_compact_result_summary_uses_original_scores_without_a_selector():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    reader = js.split('function buildResultOverview()', 1)[1].split('buildResultOverview();', 1)[0]
    assert 'id="result-overview"' in html and 'id="result-rows"' in html
    assert 'case-reader' not in html and 'buildCaseReader' not in js
    assert '.case-summary [data-summary]' in reader
    for index in (0, 2):
        assert f'source.cells[{index}].cloneNode(true)' in reader
    assert 'source.cells[1].childNodes' in reader
    assert "source.dataset.summary === 'power'" in reader
    assert 'judgment.append(coverage.cloneNode(true))' in reader
    assert 'querySelectorAll(\'a\')' in reader
    assert reader.index('body.replaceChildren(fragment)') < reader.index('details.open = false')
    assert not any(value in reader for value in ('5.0', '2.33', '4.75', 'fetch(', 'innerHTML'))


def test_compact_result_summary_keeps_limits_and_an_open_no_script_source():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    reader = js.split('function buildResultOverview()', 1)[1].split('buildResultOverview();', 1)[0]
    first = html.split('id="evaluation-evidence"', 1)[0]
    assert '사람의 최종 검토 전' in first
    assert '사례별 평가 범위가 달라 전체 평균은 계산하지 않았습니다' in first
    assert '같은 조건의 새 AI 반복 실행은 아직 검증하지 않았습니다' in first
    assert '<details id="evaluation-evidence" class="info evaluation-evidence" open>' in html
    assert "setAttribute('aria-label'" in reader
    assert reader.index('overview.hidden = false') < reader.index('details.open = false')
    assert "for (let p = target.parentElement; p; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true" in js


def test_restore_copy_evaluates_tc_instructions_not_trial_execution():
    item = next(r for r in plain_copy()['criteria'] if r['criterion_id'] == 'TC-R07')
    assert '시험 설명에 복원 절차' in item['title']
    assert '읽기 전용' in item['applicability'] and '이유' in item['applicability']
    assert '준비 전 상태를 기록하는 방법' in item['pass_condition']
    assert '성공을 확인하는 절차를 시험 설명에 적습니다' in item['pass_condition']
    assert '시험 설명에 복원 방법만 적고' in item['fail_condition']
    assert '내부 저장값을 화면 표시로 잘못 적는' in item['fail_condition']
    assert '시험 설명에 적습니다' in item['positive_example']
    assert '어떻게 확인할지 적지 않습니다' in item['negative_example']


def test_execution_copy_distinguishes_observed_mismatch_from_agent_error():
    item = next(r for r in plain_copy()['criteria'] if r['criterion_id'] == 'A3-03')
    assert '계획대로 검사하고 판정' in item['title']
    for phrase in ('같은 실행', '검사 번호', '기대값', '확인 순서와 시점', '판정이 관찰값과 맞습니다'):
        assert phrase in item['pass_condition']
    assert '제품 불일치로 정확히 기록한 경우도' in item['pass_condition']
    assert '24도가 관찰되어 제품 불일치' in item['positive_example']
    assert '24도가 관찰되어도 통과' in item['negative_example']


def test_review_copy_does_not_turn_confirmed_violations_into_missing_evidence():
    copy = plain_copy()['review_condition']
    assert '사실을 확인할 수 없으면 판단을 보류' in copy
    assert '잘못 수행한 사실이 확인되면 해당 기준에 따라 평가' in copy
    assert '금지된 동작이라고 단정하지 않으며' in copy
    assert '설명만으로 실행을 확인했다고 보지 않습니다' in copy


def test_historical_display_labels_do_not_imply_current_evaluation_completion():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    assert '점수가 있는 항목 <strong id="count-scored">' in html
    assert '당시 판단 대기 <strong id="count-review">' in html
    assert '당시 해당 없음 <strong id="count-na">' in html
    assert "NOT_APPLICABLE:['당시 해당 없음','gray']" in js
    assert " : '당시 점수 미확정'" in js
    assert " : '아직 평가 전'" not in js
    assert evidence()['summary']['evaluated'] == 19


def test_improvement_copy_distinguishes_existing_requirements_and_rechecked_records():
    html = (PAGE / 'report.html').read_text(encoding='utf-8')
    assert '이미 반영된 요구사항을 다시 제안' in html
    assert '100회에 포함된 5건의 실행 기록을 묶음 종료 후 다시 대조' in html
    assert '추가로 5회 실행한 것은 아니며' in html
    records = json.loads((PAGE / 'evidence/local-100-20261009/latest-observations.json').read_text(encoding='utf-8'))
    summary = json.loads((PAGE / 'evidence/local-100-20261009/summary.json').read_text(encoding='utf-8'))
    assert summary['recorded'] == 100 and summary['api_calls'] == 0
    assert {r['trial_id'] for r in records} == {'T013','T033','T053','T073','T093'}
    assert {r['trial_id'] for r in records} <= {r['trial_id'] for r in summary['rows']}
