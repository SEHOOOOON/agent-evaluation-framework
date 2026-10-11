import json
from pathlib import Path

from eval_harness.cli import parser
from eval_harness.full_flow import FlowSpec
from eval_harness.openai_judge import DEFAULT_JUDGE_MODEL, JudgePacket, preview

def example_packet():
    return JudgePacket(case_id='DEFAULT',trial_id='T1',agent_id='A1',target_version='synthetic',
        sources={'request': 'Confirm power state'},
        rubric=[{'criterion_id':'CHECK','objective':'근거','pass_condition':'일치'}],code_checks=[])

def test_cli_default_and_explicit_override():
    base=['judge-openai','--packet','example.json']
    assert parser().parse_args(base).model == DEFAULT_JUDGE_MODEL
    assert parser().parse_args(base+['--model','gpt-5.6-terra']).model == 'gpt-5.6-terra'
    assert parser().parse_args(base).execute is False

def test_flow_default_does_not_change_generation_or_explicit_judge():
    values=dict(case_id='X',request='r.json',oracle='o.json',generation_model='explicit-generator')
    assert FlowSpec(**values).judge_model == DEFAULT_JUDGE_MODEL
    legacy=FlowSpec(**values,judge_model='gpt-5.6-terra')
    assert legacy.judge_model == 'gpt-5.6-terra'
    assert legacy.generation_model == 'explicit-generator'

def test_sol_settings_are_bound_to_preview_without_changing_legacy_request():
    packet=example_packet()
    sol=preview(packet,DEFAULT_JUDGE_MODEL)
    old=preview(packet,'gpt-5.6-terra')
    assert sol['request']['reasoning']=={'effort':'medium'}
    assert sol['request']['service_tier']=='default'
    assert 'reasoning' not in old['request'] and 'service_tier' not in old['request']
    assert sol['approval_sha256'] != old['approval_sha256']
    assert sol['request']['input'] == old['request']['input']
    assert sol['response_schema'] == old['response_schema']
    assert sol['max_retries']==0 and sol['maximum_calls']==1
    assert sol['request']['store'] is False

def test_pinned_historical_specs_are_not_migrated():
    root=Path(__file__).resolve().parents[1]
    for name in ('full_flow.example.json','full_flow.imp001-recheck.json','full_flow.imp002-recheck.json','full_flow.three-call-pilot.json'):
        path=root/'benchmarks/v2'/name
        data=json.loads(path.read_text(encoding='utf-8'))
        assert data['judge_model']=='gpt-5.6-terra'
