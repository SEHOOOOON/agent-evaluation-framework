"""Static portfolio checks, not new agent evaluation or model accuracy tests."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'portfolio'


def evidence():
    text = (PAGE / 'evidence.js').read_text(encoding='utf-8')
    return json.loads(text.split('window.PORTFOLIO_EVIDENCE = ', 1)[1].strip().removesuffix(';'))


def assert_preserved_score_and_prior_rubric(backup):
    assert (backup / 'evidence.js').read_bytes() == (PAGE / 'evidence.js').read_bytes()
    def rubric(folder):
        return json.loads((folder / 'rubric.js').read_text(encoding='utf-8').split('window.PORTFOLIO_RUBRIC = ', 1)[1].strip().removesuffix(';'))
    previous, current = rubric(backup), rubric(PAGE)
    assert previous['version'] == 'role-rubric-2.4'
    assert current['version'] == 'role-rubric-2.5'
    assert [r for r in previous['criteria'] if r['criterion_id'] != 'A3-03'] == [r for r in current['criteria'] if r['criterion_id'] != 'A3-03']
    assert next(r for r in previous['criteria'] if r['criterion_id'] == 'A3-03')['method'] == 'HYBRID'


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links, self.resources = [], [], []
        self.feed((PAGE / 'index.html').read_text(encoding='utf-8'))

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
    for link in page.links:
        if link.startswith('#'):
            assert link[1:] in page.ids
        elif not link.startswith('https://'):
            assert (PAGE / link).is_file()
    assert all(resource and (PAGE / resource).is_file() for resource in page.resources)


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
    files = [PAGE / name for name in ('index.html', 'style.css', 'page.js', 'evidence.js', 'rubric.js', 'plain-language.js')]
    text = '\n'.join(p.read_text(encoding='utf-8') for p in files)
    assert not re.search(r'sk-[A-Za-z0-9_-]{16,}|(?<![A-Za-z])[A-Za-z]:[\\/]|file://|OPENAI_API_KEY=', text)
    scripts = '\n'.join(p.read_text(encoding='utf-8') for p in files if p.suffix == '.js')
    assert not re.search(r'fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon', scripts)


def test_scope_and_keyboard_controls_are_explicit():
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    for phrase in ('내 PC에서 보는 검토용 페이지', '일부 과정은 가짜 응답으로 대신', 'AI가 전체 과정을 70번 실행한 것은 아닙니다',
                   '여러 번 잘하는지는 아직 확인하지 않았습니다', '당시 기준 2.2', '처음 점수 유지'):
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
    assert '위 점수를 다시 매긴 것은 아닙니다' in js
    assert '당시 평가자가 남긴 설명 보기 · 기준 2.2' in js
    assert evidence()['rubric'] == 'role-rubric-2.2'


def test_dashboard_collapses_groups_and_preserves_story_backup():
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
    assert 'class="role-group"' in js and 'class="criterion"' in js
    assert 'group.open = true' in js and 'detail.open = false' in js
    assert ' open' not in re.search(r'<details class="role-group"[^>]*>', js).group()
    assert '항목 목록 모두 보기' in html and '모두 접기' in html
    backup = PAGE / 'backup-story-20261004'
    assert all((backup / name).is_file() for name in ('index.html', 'style.css', 'page.js', 'evidence.js'))
    assert 'AI가 만든 테스트' in (backup / 'index.html').read_text(encoding='utf-8')
    assert (backup / 'evidence.js').read_bytes() == (PAGE / 'evidence.js').read_bytes()


def test_summary_scores_have_no_bars_and_keep_source_based_counts():
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
    js = (PAGE / 'page.js').read_text(encoding='utf-8')
    assert 'role="tab"' not in html
    assert 'id="score-chart"' in html
    assert '5점 만점' in html
    assert 'bar-track' not in js and 'bar-fill' not in js
    css = (PAGE / 'style.css').read_text(encoding='utf-8')
    assert '.bar-track' not in css and '.bar-fill' not in css
    assert 'Number.isFinite(score)' in js  # Missing scores must not become zero.
    assert "group.evaluated+'/'+group.total+'개 반영'" in js
    assert "row.browser === 'PASS'" in js and 'row.restored === true' in js
    assert 'evidence.matrix.flatMap(row=>row.branches)' in js
    assert '이 점수만으로 전체가 잘된다고 볼 수 없습니다.' in html
    assert html.index('id="score-chart"') < html.index('id="criteria-details"')
    backup = PAGE / 'backup-dashboard-20261004'
    assert_preserved_score_and_prior_rubric(backup)
    assert 'role="tab"' in (backup / 'index.html').read_text(encoding='utf-8')


def test_role_context_and_prechange_snapshot_are_preserved():
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
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
    backup = PAGE / 'backup-charts-20261004'
    assert all((backup / name).is_file() for name in
               ('index.html', 'style.css', 'page.js', 'evidence.js', 'rubric.js'))
    assert_preserved_score_and_prior_rubric(backup)


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
    assert js.index('class="followup"') < js.index('class="criterion-extra original-reason"')


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
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
    assert "roles.map(([key,name,description])" in js
    assert 'roles.filter(' not in js
    assert "['COMMON','공통 기준'" in js and "['E2E','연결 기준'" in js
    assert 'COMMON: {question:' in js and 'E2E: {question:' in js
    assert 'class="role-row"' in js
    for phrase in ('점수 없음', '점수 없는 항목', '채점'):
        assert phrase not in js + html
    assert "item.verdict === 'REVIEW' ? ''" in js
    assert "group.average_5 === null ? '—'" in js
    assert '0점이 아닙니다' in html and '아직 평가 전' in html
    assert 'group.evaluated' in js  # Partial averages retain their denominator.


def test_improvement_and_sources_keep_scope_in_short_sections():
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
    for phrase in ('발견한 문제', '바꾼 부분', '확인한 결과', '새 응답 1건',
                   '전체 과정은 다른 문제로 중단', '평가 AI가 혼자 발견한 것은 아닙니다',
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
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
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
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
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
                   '각각 AI 평가 1회', '검사 하나 누락만의 영향', '아래의 26개 항목 점수와는 별도'):
        assert phrase in section
    assert not re.search(r'[\u4e00-\u9fff]|sk-[A-Za-z0-9_-]{16,}|[A-Za-z]:[\\/]', json.dumps(data, ensure_ascii=False))


def test_decision_examples_precede_counts_without_claiming_a_new_evaluation():
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
    assert html.index('id="a3-comparison"') < html.index('id="qa-improvement"') < html.index('id="latest-repeat"')
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
    html = (PAGE / 'index.html').read_text(encoding='utf-8')
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
