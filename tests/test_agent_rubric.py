import pytest
from eval_harness.agent_rubric import CRITERIA, summarize_roles, render_roles


def check(rule, status='PASS'):
    return {'rule':rule,'status':status,'reason':'synthetic observation','critical':True}


def item(result, cid):
    return next(i for group in result['groups'].values() for i in group if i['criterion_id']==cid)


def test_roles_unique_and_unmeasured_not_passed():
    result=summarize_roles([])
    assert set(result['groups'])=={'COMMON','A1','A2','A3','A4','E2E'}
    assert len({row[1] for row in CRITERIA})==len(CRITERIA)
    assert all(i['status']=='NOT_EVALUATED' and i['score'] is None for g in result['groups'].values() for i in g)


def test_structural_success_does_not_prove_tc_quality():
    result=summarize_roles([check('A2.TC-001.DETAIL'),check('A2.TC-001.TIMING')])
    assert item(result,'A2-03')['status']=='PARTIAL'
    assert item(result,'A1-01')['status']=='NOT_EVALUATED'


@pytest.mark.parametrize('rule,cid',[
    ('A1.SOURCE_QUOTES','A1-04'),('A2.TC-001.TRACE','A2-04'),
    ('EXEC.TC-001.COMPILE','A3-03'),('A4.FINDING_LINK','A4-03'),
])
def test_role_specific_mutation_is_visible_without_blame(rule,cid):
    result=summarize_roles([check(rule,'FAIL'),check('A4.COUNTS')])
    assert item(result,cid)['status']=='FAIL'
    assert result['failure_triage'][0]['origin']=='UNDETERMINED'
    assert result['quality_score'] is None


def test_missing_checks_cannot_be_complete():
    result=summarize_roles([check('A4.COUNTS')])
    assert item(result,'A4-01')['status']=='PARTIAL'


def test_error_is_not_a_target_failure():
    result=summarize_roles([check('A4.FINDING_LINK','ERROR')])
    assert item(result,'A4-03')['status']=='ERROR'
    assert result['failure_triage'][0]['origin']=='UNDETERMINED'


def test_all_count_checks_confirm_only_count_scope():
    result=summarize_roles([check(r) for r in ['EXEC.UNIQUE_ROWS','A4.COUNTS','A4.ORACLE_COUNTS']])
    assert item(result,'A4-01')['status']=='CHECKED'
    assert item(result,'A4-02')['status']=='NOT_EVALUATED'
    assert 'Agent 4' in render_roles(result)


def test_fail_not_hidden_by_error_or_pass():
    result=summarize_roles([check('EXEC.TC-1.EVIDENCE','FAIL'),check('EXEC.TC-2.EXIT','ERROR'),check('EXEC.TC-3.EXIT')])
    assert item(result,'A3-04')['status']=='FAIL'


@pytest.mark.parametrize('states,expected', [
    (['NOT_EVALUATED', 'NOT_EVALUATED'], 'NOT_EVALUATED'),
    (['PASS', 'NOT_EVALUATED'], 'PARTIAL'),
    (['FAIL', 'NOT_EVALUATED'], 'FAIL'),
    (['ERROR', 'NOT_EVALUATED'], 'ERROR'),
    (['PASS', 'PASS'], 'CHECKED'),
])
def test_unmeasured_checks_never_become_checked(states, expected):
    result = summarize_roles([check(rule, status) for rule, status in
                              zip(['E2E.GAPS', 'E2E.GAPS_RETAINED'], states)])
    assert item(result, 'E02')['status'] == expected
    assert item(result, 'E02')['score'] is None
