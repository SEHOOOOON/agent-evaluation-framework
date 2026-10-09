import copy
import json
import pytest
from eval_harness.full_flow import execution_support
from eval_harness.v2_recorded import digest

@pytest.mark.parametrize('variant', ['valid','mismatch','wrong_id','wrong_hash','missing','failed','typed'])
def test_native_recovery_is_hash_verified_and_compared(tmp_path, variant):
    state=dict(devices=[{'id':1,'locked':False}], selected=[],pending={},gateway=True,multi=False,cards=[{'id':1}],panel=[])
    record=dict(contract_version='native-recovery-1.0',test_id='TC-LOCK-001',status='RESTORED',original=state,restored=copy.deepcopy(state))
    if variant=='mismatch': record['restored']['cards'][0]['id']=2
    if variant=='typed': record['restored']['devices'][0]['locked']=0
    if variant=='wrong_id': record['test_id']='TC-OTHER-001'
    if variant=='missing': del record['original']['panel']
    if variant=='failed': record['status']='FAILED'
    file=tmp_path/'native-restoration.json'
    file.write_text(json.dumps(record),encoding='utf-8')
    row=dict(test_id='TC-LOCK-001',evidence_files=[file.name],evidence_sha256={file.name: '0'*64 if variant=='wrong_hash' else digest(file)})
    result=execution_support(tmp_path,tmp_path,{'regression_results':[row]},include_code=False)
    if variant in ('wrong_id','wrong_hash','missing'):
        assert not result.get('native_recovery')
        assert any(x.get('reason')=='NATIVE_RECOVERY_UNVERIFIED' for x in result['omissions'] if isinstance(x,dict))
    else:
        assert result['native_recovery'][0]['verification']==('MATCH' if variant=='valid' else 'NOT_CONFIRMED')
