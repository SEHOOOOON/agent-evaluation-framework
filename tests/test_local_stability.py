import json
from pathlib import Path
import pytest
from eval_harness.local_stability import VERSION, summarize, render
from eval_harness.v2_recorded import digest, write


def batch(tmp_path, outcome='PRODUCT_MISMATCH_CANDIDATE'):
    (tmp_path / 'records').mkdir()
    write(tmp_path / 'plan.json', dict(version=VERSION, mode='OFFLINE_LOCAL', repetitions=2,
          cases=[dict(id='S1', title='하한 검출', group='제품 시험', origin='준비한 TC', expected={'outcome': outcome})]))
    evidence = tmp_path / 'evidence.json'
    write(evidence, {'observation': 15})
    record = dict(trial_id='T001', case_id='S1', iteration=1, plan_sha256=digest(tmp_path / 'plan.json'),
                  actual={'outcome': outcome, 'browser_executed': True, 'restore': 'RESTORED'},
                  duration_seconds=1.0, protected_unchanged=True, error=None,
                  grades=[{'criterion_id': 'A3-03', 'verdict': 'PASS', 'score': 5}],
                  evidence=[dict(file='evidence.json', sha256=digest(evidence))])
    bind_facts(tmp_path, record)
    write(tmp_path / 'records/T001.json', record)
    return record


def bind_facts(root, record):
    for name, value in [('facts.json', record['actual']), ('p2-grades.json', record['grades'])]:
        path = root / record['trial_id'] / name
        write(path, value)
        file = path.relative_to(root).as_posix()
        record['evidence'] = [r for r in record['evidence'] if r['file'] != file]
        record['evidence'].append(dict(file=file, sha256=digest(path)))


def test_product_failure_detected_is_successful_local_handling_not_model_success(tmp_path):
    batch(tmp_path)
    result = summarize(tmp_path)
    assert result['counts'] == {'MATCH': 1, 'NOT_RUN': 1}
    assert result['expected_behavior_rate'] == 50.0
    assert result['ai_generation_rate'] is result['ai_quality_score'] is None
    assert result['p2']['criterion_ids'] == ['A3-03']
    assert result['p2']['ai_evaluated'] == 0
    assert result['groups'][0]['consistent'] is None
    assert result['browser_trials'] == result['restored'] == 1


@pytest.mark.parametrize('damage', ['changed_evidence', 'missing_evidence', 'protected_change', 'execution_error'])
def test_bad_or_missing_evidence_never_disappears_from_denominator(tmp_path, damage):
    record = batch(tmp_path)
    if damage == 'changed_evidence':
        write(tmp_path / 'evidence.json', {'observation': 16})
    elif damage == 'missing_evidence':
        record['evidence'] = []
    elif damage == 'protected_change':
        record['protected_unchanged'] = False
    else:
        record['error'] = 'PermissionError'
    write(tmp_path / 'records/T001.json', record)
    result = summarize(tmp_path)
    assert result['counts'] == {'ERROR': 1, 'NOT_RUN': 1}
    assert result['expected_behavior_rate'] == 0
    assert result['p2']['criterion_ids'] == []
    assert result['browser_trials'] == result['restored'] == 0
    assert result['groups'][0]['outcomes'] == {'ERROR': 1, 'NOT_RUN': 1}
    assert result['rows'][0]['actual']['restore'] == 'RESTORED'  # retain the unverified claim
    html = render(result)
    assert '기록값 · 미확인' in html and '근거 확인 불가' in html
    assert '복원 확인</td>' not in html and '실행 대조 통과' not in html


@pytest.mark.parametrize('damage', ['duplicate', 'other_plan', 'unknown_case', 'extra_iteration'])
def test_mixed_or_duplicate_trials_are_not_counted(tmp_path, damage):
    record = batch(tmp_path)
    if damage == 'other_plan': record['plan_sha256'] = '0' * 64
    if damage == 'unknown_case': record['case_id'] = 'OTHER'
    if damage == 'extra_iteration': record['iteration'] = 3
    write(tmp_path / 'records' / ('duplicate.json' if damage == 'duplicate' else 'T001.json'), record)
    with pytest.raises(ValueError): summarize(tmp_path)


def test_repeat_consistency_is_separate_from_correctness_and_html_escapes(tmp_path):
    record = batch(tmp_path, 'PASS')
    for n in (1, 2):
        record.update(trial_id=f'T{n:03}', iteration=n,
                      actual={'outcome': '<script>alert(1)</script>',
                              'browser_executed': True, 'restore': 'RESTORED'})
        record['evidence'] = []
        bind_facts(tmp_path, record)
        write(tmp_path / 'records' / f'T{n:03}.json', record)
    result = summarize(tmp_path)
    assert result['counts'] == {'MISMATCH': 2}
    assert result['groups'][0]['consistent'] is True
    assert result['browser_trials'] == result['restored'] == 2
    html = render(result)
    assert '&lt;script&gt;' in html and '<script>alert' not in html
    assert '1개 시나리오를 각 2회' in html


def test_boolean_not_numeric_and_wrong_restore_not_success(tmp_path):
    record = batch(tmp_path)
    plan = json.loads((tmp_path / 'plan.json').read_text(encoding='utf-8'))
    plan['cases'][0]['expected'].update(restore='RESTORED', browser_executed=True)
    write(tmp_path / 'plan.json', plan)
    record.update(plan_sha256=digest(tmp_path / 'plan.json'))
    record['actual'].update(restore='FAILED', browser_executed=1)
    bind_facts(tmp_path, record)
    write(tmp_path / 'records/T001.json', record)
    result = summarize(tmp_path)
    assert result['counts'] == {'MISMATCH': 1, 'NOT_RUN': 1}
    assert result['browser_trials'] == result['restored'] == 0


@pytest.mark.parametrize('grade', [
    {'criterion_id': 'A1-01', 'verdict': 'PASS', 'score': 5},
    {'criterion_id': 'A3-03', 'verdict': 'PASS', 'score': 0},
    {'criterion_id': 'UNKNOWN', 'verdict': 'PASS', 'score': 5},
])
def test_no_ai_or_invented_scores_are_added_to_local_results(tmp_path, grade):
    record = batch(tmp_path)
    record['grades'] = [grade]
    write(tmp_path / 'records/T001.json', record)
    with pytest.raises(ValueError): summarize(tmp_path)


def test_all_26_criteria_visible_without_filling_missing_scores(tmp_path):
    batch(tmp_path)
    result = summarize(tmp_path)
    assert len(result['criterion_coverage']) == 26
    assert sum(c['average_5'] is None for c in result['criterion_coverage']) == 25
    assert '전체 기준과 이번 자동 대조 점수 보기' in render(result)


def test_summary_claim_cannot_override_saved_observation(tmp_path):
    record = batch(tmp_path)
    record['actual']['outcome'] = 'PASS'
    write(tmp_path / 'records/T001.json', record)
    assert summarize(tmp_path)['counts'] == {'ERROR': 1, 'NOT_RUN': 1}


def test_judge_violation_remains_visible_even_when_product_outcome_matches(tmp_path):
    record = batch(tmp_path)
    record['grades'][0].update(verdict='FAIL', score=0)
    bind_facts(tmp_path, record)
    write(tmp_path / 'records/T001.json', record)
    result = summarize(tmp_path)
    assert result['counts']['MATCH'] == 1
    assert result['p2']['verdicts'] == {'FAIL': 1}
    assert 'P2 대조에서 위반 확인' in render(result)
