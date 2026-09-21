from copy import deepcopy
import json
from pathlib import Path

import pytest

from eval_harness.v2_recorded import Case, child, digest, evaluate, load_cases, normalized_counts, write


def sample(root, *, failed=False):
    root.mkdir(parents=True, exist_ok=True)
    write(root/'request.json', {'request': 'Apply MED; show medium.'})
    (root/'srs_snapshot.md').write_text('REQ-FAN-001 supports fan.', encoding='utf-8')
    write(root/'agent1_change_analysis.json', {'confirmed_conditions': [
        {'condition_id': 'COND-001', 'source_text': 'Apply MED; show medium.'}], 'information_gaps': []})
    write(root/'agent2_test_design.json', {'test_cases': [
        {'tc_id': 'TC-CAND-001', 'preconditions': ['ready'], 'steps': ['apply'], 'source_condition_ids': ['COND-001'],
         'expected_results': [{'statement':'medium', 'source_condition_ids':['COND-001'], 'verify_after_step':'apply'}]}]})
    (root/'trial.txt').write_text('Actual neutral observation',encoding='utf-8')
    (root/'test_candidate.py').write_text('def test_candidate():\n    assert True\n',encoding='utf-8')
    status='ASSERTION_FAILED' if failed else 'PASSED'
    row={'test_id':'TC-CAND-001','status':status,'exit_code':1 if failed else 0,
         'source':'NEW_AUTOMATION_CANDIDATE','evidence_files':['trial.txt'], 'evidence_sha256':{'trial.txt':digest(root/'trial.txt')},
         'test_file':'test_candidate.py','test_sha256':digest(root/'test_candidate.py')}
    write(root/'validation_execution.json', {'candidate_result':row,'candidate_results':[row], 'regression_results':[]})
    report={'total_results':1,'status_counts':{status:1},'recommendation':'HUMAN_REVIEW' if failed else 'PASS',
            '검토_항목':[{'category':'PRODUCT_MISMATCH_CANDIDATE', 'test_id':row['test_id'], 'status':status, 'evidence_files':row['evidence_files']}] if failed else [],'제외된_정보_부족':[]}
    write(root/'agent4_analysis.json',dict(report, validation_execution_sha256=digest(root/'validation_execution.json')))
    write(root/'final_report.json',dict(report, analysis_sha256=digest(root/'agent4_analysis.json')))
    write(root/'run_manifest.json',{'request_sha256':digest(root/'request.json'),'srs_sha256':digest(root/'srs_snapshot.md'),'agent1_analysis_sha256':digest(root/'agent1_change_analysis.json')})
    write(root/'agent2_manifest.json',{'source_run_manifest_sha256':digest(root/'run_manifest.json'),'agent2_design_sha256':digest(root/'agent2_test_design.json')})
    return Case(case_id='V2-001',run_id='RUN-20260918-110541-4102E0',title='Synthetic evaluator control',
                expected_counts={status:1}, expected_recommendation=report['recommendation'],expected_gap_count=0,
                expected_finding_categories=['PRODUCT_MISMATCH_CANDIDATE'] if failed else [],
                require_step_links=True,human_review_topics=['Meaning needs human review'])


def edit(root,name,modify):
    path=root/name
    value=json.loads(path.read_text(encoding='utf-8'))
    modify(value)
    write(path,value)


def refresh_links(root):
    # Recompute hashes so semantic defects cannot be detected only by stale hashes.
    edit(root,'run_manifest.json',lambda x:x.update(agent1_analysis_sha256=digest(root/'agent1_change_analysis.json')))
    edit(root,'agent2_manifest.json',lambda x:x.update(source_run_manifest_sha256=digest(root/'run_manifest.json'),agent2_design_sha256=digest(root/'agent2_test_design.json')))
    edit(root,'agent4_analysis.json',lambda x:x.update(validation_execution_sha256=digest(root/'validation_execution.json')))
    edit(root,'final_report.json',lambda x:x.update(analysis_sha256=digest(root/'agent4_analysis.json')))


@pytest.mark.parametrize('mutation,rule',[
    ('empty_expected','A2.TC-CAND-001.DETAIL'),
    ('empty_precondition','A2.TC-CAND-001.DETAIL'),
    ('missing_reference','A2.TC-CAND-001.TRACE'),
    ('missing_design','EXEC.DESIGN_LINK'),
    ('wrong_finding','A4.FINDING_LINK'),
    ('wrong_status','A4.FINDING_LINK'),
    ('wrong_evidence','A4.FINDING_LINK'),
    ('changed_gap','E2E.GAPS_RETAINED'),
])
def test_semantic_mutations_with_valid_hashes(tmp_path,mutation,rule):
    case=sample(tmp_path,failed=mutation.startswith('wrong_'))
    if mutation=='empty_expected':
        edit(tmp_path,'agent2_test_design.json',lambda x:x['test_cases'][0]['expected_results'][0].update(statement=' '))
    elif mutation=='empty_precondition':
        edit(tmp_path,'agent2_test_design.json',lambda x:x['test_cases'][0].update(preconditions=[' ']))
    elif mutation=='missing_reference':
        edit(tmp_path,'agent2_test_design.json',lambda x:x['test_cases'][0]['expected_results'][0].update(source_condition_ids=[]))
    elif mutation=='missing_design':
        edit(tmp_path,'agent2_test_design.json',lambda x:x.update(test_cases=[]))
    elif mutation.startswith('wrong_'):
        field,value={'wrong_finding':('test_id','NONEXISTENT'),'wrong_status':('status','PASSED'),'wrong_evidence':('evidence_files',[])}[mutation]
        for name in ['agent4_analysis.json','final_report.json']:
            edit(tmp_path,name,lambda x:x['검토_항목'][0].update({field:value}))
    else:
        case=case.model_copy(update={'expected_gap_count':1})
        edit(tmp_path,'agent1_change_analysis.json',lambda x:x.update(information_gaps=['temperature unknown']))
        for name in ['validation_execution.json','agent4_analysis.json','final_report.json']:
            edit(tmp_path,name,lambda x:x.update({'제외된_정보_부족':['alarm unknown']}))
    refresh_links(tmp_path)
    result=evaluate(tmp_path,case)
    assert result['status']=='FAIL'
    assert any(c['rule']==rule and c['status']=='FAIL' for c in result['checks'])
    assert all(c['status']=='PASS' for c in result['checks'] if 'CHAIN' in c['rule'])


def test_bad_nested_object_returns_error(tmp_path):
    case=sample(tmp_path)
    edit(tmp_path,'agent2_test_design.json',lambda x:x['test_cases'][0].update(expected_results=['invalid']))
    refresh_links(tmp_path)
    assert evaluate(tmp_path,case)['status']=='ERROR'


def test_reuse_only_does_not_require_new_design(tmp_path):
    case=sample(tmp_path)
    edit(tmp_path,'agent2_test_design.json',lambda x:x.update(test_cases=[]))
    execution=json.loads((tmp_path/'validation_execution.json').read_text(encoding='utf-8'))
    row=execution['candidate_results'][0]
    row['source']='EXISTING_REGRESSION'
    execution.update(candidate_result=None,candidate_results=[],regression_results=[row])
    write(tmp_path/'validation_execution.json',execution)
    refresh_links(tmp_path)
    assert evaluate(tmp_path,case)['status']=='REVIEW'


@pytest.mark.parametrize('bad_manifest',[[], None])
def test_bad_manifest_does_not_abort_other_cases(tmp_path,monkeypatch,bad_manifest):
    from eval_harness import v2_recorded as module
    target=tmp_path/'target'
    first=sample(target/'runs/RUN-20260918-110541-4102E0')
    second=sample(target/'runs/RUN-20260918-110542-4102E1').model_copy(update={'case_id':'V2-002','run_id':'RUN-20260918-110542-4102E1'})
    broken=target/'runs'/first.run_id/'run_manifest.json'
    if bad_manifest is None:
        broken.unlink()
    else:
        write(broken,bad_manifest)
    dataset=tmp_path/'cases.jsonl'
    dataset.write_text(first.model_dump_json()+'\n'+second.model_dump_json(),encoding='utf-8')
    monkeypatch.setattr(module,'target_pin',lambda _: {'commit':'synthetic'})
    out=module.run_recorded(target,dataset,tmp_path/'output')
    assert module.read(out/'result.json')['counts']=={'ERROR':1,'REVIEW':1}


@pytest.mark.parametrize('failed',[False,True])
def test_product_failure_is_not_agent_failure_and_alias_not_double_counted(tmp_path,failed):
    case=sample(tmp_path,failed=failed)
    result=evaluate(tmp_path,case)
    assert result['status']=='REVIEW'
    assert not [c for c in result['checks'] if c['status']!='PASS']
    assert sum(result['product_counts'].values())==1
    assert result['quality_score'] is None


@pytest.mark.parametrize('mutation,rule',[
    ('false_count','A4.COUNTS'),('fake_quote','A1.SOURCE_QUOTES'),('bad_link','A2.TC-CAND-001.TRACE'),
    ('bad_timing','A2.TC-CAND-001.TIMING'),('duplicate_tc','A2.UNIQUE_TC'),
    ('alter_evidence','EXEC.TC-CAND-001.EVIDENCE'),('missing_evidence','EXEC.TC-CAND-001.EVIDENCE'),
    ('fake_pass','EXEC.TC-CAND-001.EXIT'),('alter_code','EXEC.TC-CAND-001.CODE_HASH'),
])
def test_mutants_are_detected_without_target_checkpoint(tmp_path,mutation,rule):
    case=sample(tmp_path)
    if mutation=='false_count':
        edit(tmp_path,'final_report.json',lambda x:x.update(total_results=99))
    elif mutation=='fake_quote':
        edit(tmp_path,'agent1_change_analysis.json',lambda x:x['confirmed_conditions'][0].update(source_text='Unstated red alert'))
    elif mutation=='bad_link':
        edit(tmp_path,'agent2_test_design.json',lambda x:x['test_cases'][0].update(source_condition_ids=['UNKNOWN']))
    elif mutation=='bad_timing':
        edit(tmp_path,'agent2_test_design.json',lambda x:x['test_cases'][0]['expected_results'][0].update(verify_after_step='not a step'))
    elif mutation=='duplicate_tc':
        edit(tmp_path,'agent2_test_design.json',lambda x:x['test_cases'].append(deepcopy(x['test_cases'][0])))
    elif mutation=='alter_evidence':
        (tmp_path/'trial.txt').write_text('changed',encoding='utf-8')
    elif mutation=='missing_evidence':
        (tmp_path/'trial.txt').unlink()
    elif mutation=='fake_pass':
        edit(tmp_path,'validation_execution.json',lambda x:x['candidate_results'][0].update(exit_code=1))
    elif mutation=='alter_code':
        (tmp_path/'test_candidate.py').write_text('pass',encoding='utf-8')
    result=evaluate(tmp_path,case)
    assert result['status']=='FAIL'
    assert any(c['rule']==rule and c['status']=='FAIL' for c in result['checks'])


@pytest.mark.parametrize('path',['../secret','/tmp/secret','C:/secret',r'C:\secret','https://host/x','a/../../x'])
def test_artifact_paths_cannot_escape(tmp_path,path):
    with pytest.raises(ValueError): child(tmp_path,path)


def test_missing_primary_artifact_is_error_not_agent_failure(tmp_path):
    case=sample(tmp_path)
    (tmp_path/'request.json').unlink()
    result=evaluate(tmp_path,case)
    assert result['status']=='ERROR' and result['quality_score'] is None
    assert str(tmp_path) not in json.dumps(result)


def test_critical_failure_not_hidden_by_later_error(tmp_path):
    case=sample(tmp_path)
    edit(tmp_path,'agent1_change_analysis.json',lambda x:x['confirmed_conditions'][0].update(source_text='invented'))
    edit(tmp_path,'validation_execution.json',lambda x:x['candidate_results'][0].update(status='UNKNOWN'))
    result=evaluate(tmp_path,case)
    assert result['status']=='FAIL' and any(c['status']=='ERROR' for c in result['checks'])


@pytest.mark.parametrize('counts',[{'FAKE':1},{'PASSED':True},{'PASSED':-1}])
def test_bad_count_contract_rejected(counts):
    with pytest.raises(ValueError): normalized_counts(counts)


def test_dataset_duplicate_run_does_not_count_as_repeat_trial(tmp_path):
    case=sample(tmp_path/'source')
    dataset=tmp_path/'cases.jsonl'
    second=case.model_copy(update={'case_id':'V2-002'})
    dataset.write_text(case.model_dump_json()+'\n'+second.model_dump_json(),encoding='utf-8')
    with pytest.raises(ValueError): load_cases(dataset)


def test_cli_v2_mode_is_explicit():
    from eval_harness.cli import parser
    args=parser().parse_args(['run-v2','--target','example'])
    assert args.command=='run-v2' and args.target==Path('example')


@pytest.mark.parametrize('persistent',[False,True])
def test_json_read_retries_only_bounded_access_contention(tmp_path,monkeypatch,persistent):
    from eval_harness import storage
    path=tmp_path/'record.json'
    path.write_text('{"valid":true}',encoding='utf-8')
    real=Path.read_text
    attempts=[]
    def flaky(self,*args,**kwargs):
        if self==path:
            attempts.append(1)
            if persistent or len(attempts)<3:
                raise PermissionError('busy')
        return real(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',flaky)
    monkeypatch.setattr(storage.time,'sleep',lambda _:None)
    if persistent:
        with pytest.raises(PermissionError): storage.read_json(path)
        assert len(attempts)==10
    else:
        assert storage.read_json(path)=={'valid':True}
        assert len(attempts)==3


def test_bad_json_is_not_silently_retried(tmp_path):
    from eval_harness.storage import read_json
    path=tmp_path/'bad.json'
    path.write_text('{broken',encoding='utf-8')
    with pytest.raises(json.JSONDecodeError): read_json(path)


def test_invalid_candidate_code_is_agent_failure_not_evaluator_error(tmp_path):
    case=sample(tmp_path)
    (tmp_path/'test_candidate.py').write_text('def broken(',encoding='utf-8')
    edit(tmp_path,'validation_execution.json',lambda x:x['candidate_results'][0].update(test_sha256=digest(tmp_path/'test_candidate.py')))
    result=evaluate(tmp_path,case)
    assert result['status']=='FAIL'
    assert any(c['rule'].endswith('.COMPILE') and c['status']=='FAIL' for c in result['checks'])


def test_source_quotes_are_compared_as_text_not_json_escapes(tmp_path):
    case=sample(tmp_path)
    quote='Show "medium"\nafter apply.'
    write(tmp_path/'request.json',{'text':quote})
    edit(tmp_path,'agent1_change_analysis.json',lambda x:x['confirmed_conditions'][0].update(source_text=quote))
    result=evaluate(tmp_path,case)
    assert next(c for c in result['checks'] if c['rule']=='A1.SOURCE_QUOTES')['status']=='PASS'


def test_output_cannot_be_inside_target(tmp_path):
    from eval_harness.v2_recorded import run_recorded
    with pytest.raises(ValueError,match='outside'):
        run_recorded(tmp_path,tmp_path/'not-read.jsonl',tmp_path/'output')


def test_recorded_integration_preserves_source_and_escapes_html(tmp_path,monkeypatch):
    from eval_harness import v2_recorded as module
    target=tmp_path/'target'
    case=sample(target/'runs/RUN-20260918-110541-4102E0')
    case=case.model_copy(update={'title':'<script>alert(1)</script>'})
    data=tmp_path/'cases.jsonl'
    data.write_text(case.model_dump_json()+'\n',encoding='utf-8')
    monkeypatch.setattr(module,'target_pin',lambda _: {'commit':'synthetic-test-only'})
    before=module.inventory(target)
    out=module.run_recorded(target,data,tmp_path/'outputs')
    result=module.read(out/'result.json')
    assert result['counts']=={'REVIEW':1} and result['source_unchanged']
    assert result['target_executed'] is False and result['new_model_calls']==0
    assert module.inventory(target)==before
    assert '<script>' not in (out/'report.html').read_text(encoding='utf-8')


def test_source_change_invalidates_evaluation(tmp_path,monkeypatch):
    from eval_harness import v2_recorded as module
    target=tmp_path/'target'
    case=sample(target/'runs/RUN-20260918-110541-4102E0')
    data=tmp_path/'cases.jsonl'
    data.write_text(case.model_dump_json()+'\n',encoding='utf-8')
    monkeypatch.setattr(module,'target_pin',lambda _: {'commit':'synthetic-test-only'})
    original=module.evaluate
    def changed(run,case):
        result=original(run,case)
        (run/'trial.txt').write_text('unexpected source change',encoding='utf-8')
        return result
    monkeypatch.setattr(module,'evaluate',changed)
    with pytest.raises(RuntimeError,match='Target changed'):
        module.run_recorded(target,data,tmp_path/'outputs')
    assert not list((tmp_path/'outputs').rglob('result.json'))
