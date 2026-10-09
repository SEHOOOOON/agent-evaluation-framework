"""Saved baseline metadata is passed through, not promoted to execution proof."""
import json
import pytest
from eval_harness.full_flow import collect_packets, FlowSpec
from eval_harness.v2_recorded import digest


@pytest.mark.parametrize('field_value', [
    '사용자가 유지보수 작업 승인', 'Bearer private-test-token',
    {'api_key': 'not-a-real-credential'}, None,
])
def test_maintenance_approval_metadata_withheld_without_changing_tc(field_value):
    from copy import deepcopy
    from hashlib import sha256
    from eval_harness.full_flow import catalog_evidence
    from eval_harness.openai_judge import scan_payload, encode
    document = {'test_case': {'steps': ['온도 선택', '적용'], 'expected_results': [{'value': 20}],
                             'restoration': {'compare': 'OBSERVED_BASELINE'}},
                'maintenance_revision': {'authorization': field_value, 'model_review': 'NOT_RUN',
                                         'limitation': '로컬 검증만 완료'}}
    raw = json.dumps(document, ensure_ascii=False)
    original = {'approved_assets': [{'test_case_json': raw}], 'baseline_details': {'cases': []}}
    before = deepcopy(original)
    result, omissions = catalog_evidence(original)
    assert original == before
    restored = json.loads(result['approved_assets'][0]['test_case_json'])
    assert restored['test_case'] == document['test_case']
    assert restored['maintenance_revision'] == {'model_review': 'NOT_RUN', 'limitation': '로컬 검증만 완료'}
    assert omissions[0]['source_test_case_json_sha256'] == sha256(raw.encode()).hexdigest()
    assert omissions[0]['withheld_value_sha256'] == sha256(encode(field_value).encode()).hexdigest()
    scan_payload({'content': result, 'local_only_metadata': omissions})


@pytest.mark.parametrize('location', ['tc', 'other_metadata', 'nested_json'])
def test_catalog_projection_keeps_secret_scanner_fail_closed(location):
    from eval_harness.full_flow import catalog_evidence
    from eval_harness.openai_judge import scan_payload
    document = {'test_case': {}, 'maintenance_revision': {'authorization': 'approval note'}}
    if location == 'tc':
        document['test_case']['api_key'] = 'placeholder'
    elif location == 'other_metadata':
        document['maintenance_revision']['password'] = 'placeholder'
    else:
        document['test_case']['notes'] = json.dumps({'authorization': 'placeholder'})
    projected, omitted = catalog_evidence({'approved_assets': [{'test_case_json': json.dumps(document)}]})
    assert omitted
    with pytest.raises(ValueError, match='Sensitive field'):
        scan_payload(projected)


def test_catalog_without_maintenance_metadata_is_byte_preserved():
    from eval_harness.full_flow import catalog_evidence
    original = {'approved_assets': [{'test_case_json': '{ "test_case": { "value": 16 } }'}]}
    assert catalog_evidence(original) == (original, [])


def test_catalog_duplicate_keys_are_not_silently_normalized():
    from eval_harness.full_flow import catalog_evidence
    from eval_harness.openai_judge import scan_payload
    raw = '{"test_case":{"steps":["wrong"]},"test_case":{"steps":["safe"]},"maintenance_revision":{"authorization":"note"}}'
    original = {'approved_assets': [{'test_case_json': raw}]}
    assert catalog_evidence(original) == (original, [])
    with pytest.raises(ValueError):
        scan_payload(original)


def test_collected_catalog_projection_is_marked_and_original_is_unchanged(tmp_path):
    from eval_harness.openai_judge import scan_payload
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'srs_snapshot.md').write_text('설정 범위', encoding='utf-8')
    raw = json.dumps({'test_case': {'steps': ['apply'], 'expected_results': [{'value': 20}]},
                      'maintenance_revision': {'authorization': 'local maintenance approval', 'model_review': 'NOT_RUN'}})
    catalog = {'approved_assets': [{'test_case_json': raw}]}
    path = run / 'approved_regression_catalog.json'
    path.write_text(json.dumps(catalog), encoding='utf-8')
    before = path.read_bytes()
    (run / 'agent2_manifest.json').write_text(json.dumps({'approved_regression_catalog_sha256': digest(path)}))
    (run / 'agent2_test_design.json').write_text(json.dumps({'test_cases': [], 'related_existing_tests': []}))
    (run / 'validation_execution.json').write_text(json.dumps({'regression_results': []}))
    spec = FlowSpec(case_id='LOCAL', request='request.json', oracle='oracle.json', generation_model='offline', judge_model='offline')
    packets = collect_packets(run, tmp_path, spec, {'request': {}, 'oracle': {'status': 'DRAFT'}, 'target_commit': 'local'}, [])
    for role in ('A2', 'A3'):
        source = packets[role].sources
        evidence = source['context']['existing_tc_catalog']
        assert evidence['status'] == 'HASH_VERIFIED' and evidence['sha256'] == digest(path)
        assert evidence['local_only_metadata'][0]['reason'] == 'LOCAL_APPROVAL_METADATA_NOT_JUDGE_INPUT'
        assert source['trace']['evidence_delivery']['json_files']['approved_regression_catalog.json'] == 'VERIFIED_CATALOG_PROJECTION'
        assert json.loads(evidence['content']['approved_assets'][0]['test_case_json'])['test_case'] == json.loads(raw)['test_case']
        scan_payload(source)
    assert path.read_bytes() == before

@pytest.mark.parametrize('has_details', [False, True])
def test_native_baseline_details_preserved_without_historical_backfill(tmp_path, has_details):
    run = tmp_path/'run'
    run.mkdir()
    (run/'srs_snapshot.md').write_text('로컬 수집 시험', encoding='utf-8')
    snapshot = {'approved_assets': []}
    if has_details:
        snapshot['baseline_details'] = {'contract_version':'baseline-details-1.0',
            'cases':[{'tc_id':'TC-LOCK-001', 'test_case_json':json.dumps({
                'steps':['16대 잠금 표시 확인', '현장 명령 차단 로그 확인'],
                'restoration_status':'NOT_IMPLEMENTED_CONTEXT_DISPOSED'}, ensure_ascii=False)}]}
    path = run/'approved_regression_catalog.json'
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
    (run/'agent2_manifest.json').write_text(json.dumps({'approved_regression_catalog_sha256':digest(path)}), encoding='utf-8')
    (run/'agent2_test_design.json').write_text(json.dumps({'test_cases':[], 'related_existing_tests':[{'tc_id':'TC-LOCK-001'}]}), encoding='utf-8')
    (run/'validation_execution.json').write_text(json.dumps({'regression_results':[]}), encoding='utf-8')
    spec=FlowSpec(case_id='LOCAL', request='request.json', oracle='oracle.json', generation_model='offline', judge_model='offline')
    packets=collect_packets(run, tmp_path, spec, {'request':{},'oracle':{'status':'DRAFT'},'target_commit':'local'}, [])
    for role in ('A2','A3'):
        entry=packets[role].sources['context']['existing_tc_catalog']
        assert entry['status']=='HASH_VERIFIED'
        assert entry['content']==snapshot
        assert ('baseline_details' in entry['content']) == has_details
        assert '성공 증거가 아님' in entry['scope']
