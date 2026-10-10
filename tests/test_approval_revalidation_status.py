"""Local follow-up status is separate from immutable execution grades."""
import json
from copy import deepcopy
import pytest
from eval_harness.full_flow import approval_revalidation_status, python_scores
from eval_harness.v2_recorded import read, write, digest
from eval_harness.web.flows import flow_summary
from test_execution_observations import saved_sample, with_validation


def retry_case(tmp_path, outcome="PASS"):
    run, folder = saved_sample(tmp_path)
    execution, wrapper, _ = with_validation(run, folder)
    execution['candidate_results'][0]['target_sha256'] = 'a'*64
    write(run/'validation_execution.json', execution)
    wrapper['validation_execution_sha256'] = digest(run/'validation_execution.json')
    write(run/'validation_manifest.json', wrapper)
    log = run/'asset_revalidation/TC-X/attempt-1/evidence/stdout.txt'
    log.parent.mkdir(parents=True)
    log.write_text('synthetic retry evidence',encoding='utf-8')
    record = dict(run_id=run.name,tc_id='TC-X',attempt_id='attempt-1',outcome=outcome,
                  target_sha256='a'*64,candidate_sha256=digest(folder/'candidates/test_tc.py'),
                  evidence_complete=True,evidence_files=[log.relative_to(run).as_posix()],
                  evidence_sha256={log.relative_to(run).as_posix():digest(log)})
    write(run/'asset_revalidation/TC-X/latest.json',record)
    return run,folder,record


@pytest.mark.parametrize('outcome',['PASS','PRODUCT_MISMATCH_CANDIDATE','AUTOMATION_ERROR','ENVIRONMENT_ERROR','TIMEOUT','INCOMPLETE'])
def test_later_retry_is_reported_without_rewriting_original_score(tmp_path,outcome):
    run,folder,record=retry_case(tmp_path)
    original=python_scores(run,[])
    before={p.relative_to(run).as_posix():digest(p) for p in run.rglob('*') if p.is_file()}
    record['outcome']=outcome
    write(run/'asset_revalidation/TC-X/latest.json',record)
    result=python_scores(run,[])
    assert result['judgments']==original['judgments']
    assert next(r for r in result['judgments'] if r['criterion_id']=='A3-03')['score']==5
    status=result['approval_revalidation'];item=status['items'][0]
    assert item['status']=='VERIFIED' and item['outcome']==outcome
    assert item['registration']==('P1_CHECK_REQUIRED' if outcome=='PASS' else 'BLOCKED_BY_RETRY')
    assert status['scope']=='COLLECTION_SNAPSHOT_NOT_LIVE' and not status['scores_changed']
    for name,h in before.items():
        if name!='asset_revalidation/TC-X/latest.json':assert digest(run/name)==h


@pytest.mark.parametrize('fault',['run','tc','code','product','validation_hash','manifest_hash','missing_log','changed_log','path','incomplete_evidence','unknown_outcome','malformed','duplicate','unrelated_tc','candidate_file','attempt'])
def test_missing_or_conflicting_retry_is_not_a_verified_pass(tmp_path,fault):
    run,folder,record=retry_case(tmp_path)
    if fault in {'run','tc'}:record[fault+'_id']='OTHER'
    if fault=='code':record['candidate_sha256']='b'*64
    if fault=='product':record['target_sha256']='b'*64
    if fault=='unknown_outcome':record['outcome']='MADE_UP_SUCCESS'
    if fault=='incomplete_evidence':record['evidence_complete']=False
    if fault=='attempt':record['attempt_id']='../elsewhere'
    if fault=='path':record['evidence_files']=['../outside.txt']
    if fault in {'missing_log','changed_log'}:
        log=run/record['evidence_files'][0]
        if fault=='missing_log':log.unlink()
        else:log.write_text('changed',encoding='utf-8')
    if fault in {'validation_hash','manifest_hash','duplicate','unrelated_tc'}:
        wrapper=read(run/'validation_manifest.json')
        if fault=='validation_hash':wrapper['validation_execution_sha256']='b'*64
        if fault=='manifest_hash':wrapper['source_agent3_artifacts'][0]['agent3_manifest_sha256']='b'*64
        if fault=='duplicate':wrapper['source_agent3_artifacts']*=2
        if fault=='unrelated_tc':wrapper['source_agent3_artifacts'][0]['tc_id']='TC-Y'
        write(run/'validation_manifest.json',wrapper)
    if fault=='candidate_file':(folder/'candidates/test_tc.py').write_text('changed',encoding='utf-8')
    path=run/'asset_revalidation/TC-X/latest.json';write(path,record)
    if fault=='malformed':path.write_text('{broken',encoding='utf-8')
    result=approval_revalidation_status(run)
    assert result['status']=='REVIEW_REQUIRED' and result['registration']=='UNCONFIRMED'
    assert result['items'][0]['outcome'] is None


def test_no_retry_and_interrupted_retry_do_not_mean_registration_approved(tmp_path):
    run,folder=saved_sample(tmp_path)
    assert approval_revalidation_status(run)['status']=='NO_RETRY_RECORD'
    write(run/'asset_revalidation/TC-X/latest.json',dict(outcome='INCOMPLETE'))
    assert approval_revalidation_status(run)['status']=='REVIEW_REQUIRED'


def test_invalid_retry_directory_does_not_mean_no_retry(tmp_path):
    run,_=saved_sample(tmp_path)
    (run/'asset_revalidation').write_text('not a directory',encoding='utf-8')
    assert approval_revalidation_status(run)['status']=='REVIEW_REQUIRED'


def test_current_product_change_cannot_hide_behind_old_product_hash(tmp_path):
    run,_,record=retry_case(tmp_path/'p1/runs')
    target=tmp_path/'p1/product_baseline/virtual-controller.html'
    target.parent.mkdir();target.write_text('new product',encoding='utf-8')
    assert approval_revalidation_status(run)['status']=='REVIEW_REQUIRED'
    record['target_sha256']=digest(target)
    write(run/'asset_revalidation/TC-X/latest.json',record)
    result=approval_revalidation_status(run)
    assert result['status']=='COLLECTED'
    assert result['items'][0]['product_scope']=='CURRENT_WORKSPACE'
    assert result['registration']=='P1_CHECK_REQUIRED'


def test_dashboard_keeps_historical_scores_and_marks_absent_followup_uncollected(tmp_path):
    name='FLOW-20261010-120000-abcdef';path=tmp_path/name/'result.json'
    run,_,_=retry_case(tmp_path/'fixture','TIMEOUT')
    evaluation=python_scores(run,[])
    value=dict(mode='TEST_DOUBLE',trials=[dict(trial=1,status='COMPLETED',python_evaluation=evaluation)])
    write(path,value)
    result=flow_summary(tmp_path,name)['trials'][0]
    assert result['approval_revalidation']['items'][0]['registration']=='BLOCKED_BY_RETRY'
    assert 'source_sha256' not in json.dumps(result['approval_revalidation'])
    score=deepcopy(result['scorecard'])
    value['trials'][0]['python_evaluation'].pop('approval_revalidation')
    write(path,value)
    result=flow_summary(tmp_path,name)['trials'][0]
    assert result['approval_revalidation']['status']=='NOT_COLLECTED'
    assert result['scorecard']==score
