"""20 scenarios x 5 isolated local trials; no model calls or official writes.

P1 reference builders are test inputs, NOT generated AI outputs. P2 scores only
existing program-checkable criteria. Historical model judgments are not rerun.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
from types import SimpleNamespace

P2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P2 / 'src'))
from eval_harness.local_stability import VERSION, write_report
from eval_harness.execution_observations import compare
from eval_harness.v2_recorded import digest, read, write
from eval_harness.full_flow import python_scores, local_checks, collect_packets, FlowSpec


def hashes(folder):
    return {p.relative_to(folder).as_posix(): digest(p) for p in folder.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts}


def protected(p1):
    result = {}
    for prefix, folder in [('p1_src', p1 / 'src'), ('assets', p1 / 'approved_assets'),
                           ('product', p1 / 'product_baseline'), ('p2_src', P2 / 'src')]:
        result.update({prefix + '/' + k: v for k, v in hashes(folder).items()})
    for name, path in [('srs', p1 / 'docs/01_PRODUCT_SRS.md'),
                       ('builder', p1 / 'tests/pipeline_test_support.py'),
                       ('runner', Path(__file__))]:
        result[name] = digest(path)
    return result


def prepare_cases(s):
    p = s.pipeline
    cases = []
    normal = [
        ('전원 정지→운전', 'status', 'STOP', 'OPERATION', None),
        ('전원 운전→정지', 'status', 'OPERATION', 'STOP', None),
        ('모드 냉방→난방', 'mode', 'COOL', 'HEAT', None),
        ('모드 난방→제습', 'mode', 'HEAT', 'DRY', None),
        ('풍량 자동→강풍', 'fanSpeed', 'AUTO', 'HIGH', None),
        ('풍량 강풍→약풍', 'fanSpeed', 'HIGH', 'LOW', None),
        ('난방 온도 27→21°C', 'setTemp', 27, 21, {'mode': 'HEAT'}),
        ('자동 모드 온도 24→18°C', 'setTemp', 24, 18, {'mode': 'AUTO'}),
        ('잠금 해제→설정', 'locked', False, True, None),
        ('잠금 설정→해제', 'locked', True, False, None),
    ]
    for title, field, initial, target, prep in normal:
        tc, _ = s.mapped_tc_fixture(field, initial, target, prepare=prep, prove_ui=True)
        cases.append(dict(title=title, kind='direct', group='실제 브라우저', origin='사람이 준비한 공통 TC',
                          tc=tc.model_dump(mode='json'), expected=dict(outcome='PASS', restore='RESTORED', evidence_complete=True)))
    tc, _ = s.mapped_tc_fixture('setTemp', 16, 16, blocked=True, blocked_by_lock=False, prove_ui=True)
    action = next(o for o in tc.execution_spec.operations if o.phase.value == 'TEST' and o.action_type.value == 'SET_TEMPERATURE')
    tc.steps[0] = '16°C에서 온도 내림 버튼을 한 번 선택한다.'
    action.action_type = type(action.action_type)('CLICK')
    action.target, action.value, action.source_text = 'temperature.decrease', None, tc.steps[0]
    cases.append(dict(title='하한 16°C에서 감소 차단 시험', kind='direct', group='제품 불일치 검출',
                      origin='사람이 준비한 경계 TC · 기존 SRS 하한 16°C', tc=tc.model_dump(mode='json'),
                      expected=dict(outcome='PRODUCT_MISMATCH_CANDIDATE', restore='RESTORED', evidence_complete=True)))
    tc, _ = s.mapped_tc_fixture('mode', 'COOL', 'AUTO', prove_ui=True)
    action = next(o for o in tc.execution_spec.operations if o.phase.value == 'TEST' and o.action_type.value == 'SET_MODE')
    action.target = 'unsupported.automatic_switch'
    action.source_text = tc.steps[0] = '기존 AUTO 모드와 별개인 자동 전환을 켠다.'
    cases.append(dict(title='없는 자동 전환 기능의 실행 제외', kind='direct', group='실행 불가',
                      origin='사람이 준비한 미지원 TC · 모델 분석은 미실행', tc=tc.model_dump(mode='json'),
                      expected=dict(outcome='NOT_AUTOMATABLE', browser_executed=False)))
    cases += [dict(title='저장된 냉방→제습 전체 후속 흐름', kind='saved', group='실제 브라우저·보고',
                   origin='실제 AI 저장 TC·검토 재사용', expected=dict(outcome='PASS', restore='RESTORED', recommendation='PASS', roles_collected=['A1', 'A2', 'A3', 'A4'])),
              dict(title='온도 목표 미정 → 질문 후 대기', kind='waiting', group='정보 부족 보류',
                   origin='실제 AI 저장 분석 재검사', expected=dict(outcome='WAITING_FOR_USER', questions_preserved=True, next_stage_blocked=True)),
              dict(title='등록된 강풍 TC 재사용', kind='reuse', group='기존 TC 재사용',
                   origin='등록된 AI TC의 유지보수 버전', expected=dict(outcome='PASS', restore='RESTORED', evidence_complete=True))]
    for kind, title, outcome in [('ADD', '시험용 자산에 새 TC 추가', 'APPROVED'),
                                  ('REPLACE', '선택한 기존 TC 대체', 'REPLACED'),
                                  ('DECLINE', '등록하지 않음', 'DECLINED'),
                                  ('HOLD', '사람 판단 보류', 'HELD'),
                                  ('DUPLICATE', '같은 TC 중복 승인', 'DUPLICATE_UNCHANGED')]:
        cases.append(dict(title=title, kind=kind, group='자산 선택 사본', origin='실제 저장 Run의 독립 사본 · 승인 함수 호출',
                          expected=dict(outcome=outcome, asset_contract_ok=True, srs_unchanged=True)))
    for n, case in enumerate(cases, 1):
        case['id'] = f'S{n:02}'
    return cases


def install_offline(s):
    for key in tuple(os.environ):
        if key.startswith(('OPENAI_', 'NOTION_', 'SLACK_')):
            os.environ.pop(key, None)
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    import openai
    def denied(*a, **kw):
        raise RuntimeError('OFFLINE: API calls are forbidden')
    openai.OpenAI = denied
    import qa_pipeline_grounding as g
    g.OpenAI = denied
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def allow(address):
        if isinstance(address, tuple) and address[0] not in ('localhost', '127.0.0.1', '::1'):
            raise RuntimeError('OFFLINE: external socket blocked')
    def guarded(sock, address):
        allow(address)
        return connect(sock, address)
    def guarded_ex(sock, address):
        allow(address)
        return connect_ex(sock, address)
    socket.socket.connect, socket.socket.connect_ex = guarded, guarded_ex
    from playwright.sync_api import Browser
    original = Browser.new_context
    def context(browser, *a, **kw):
        result = original(browser, *a, **kw)
        result.route('**/*', lambda route: route.continue_() if route.request.url.startswith(('file:', 'data:', 'about:')) else route.abort())
        return result
    Browser.new_context = context
    original_subprocess = s.pipeline_execution._run_trial_subprocess
    def trial(command, *, cwd, env, timeout_seconds):
        # Same restriction in child pytest browser contexts. No operating-system sandbox claim.
        (Path(cwd) / 'conftest.py').write_text(
            "import pytest\n@pytest.fixture(autouse=True)\ndef offline_browser(monkeypatch):\n"
            " from playwright.sync_api import Browser\n old=Browser.new_context\n"
            " def new(self,*a,**kw):\n  c=old(self,*a,**kw)\n  c.route('**/*',lambda r:r.continue_() if r.request.url.startswith(('file:','data:','about:')) else r.abort())\n  return c\n"
            " monkeypatch.setattr(Browser,'new_context',new)\n", encoding='utf-8')
        return original_subprocess(command, cwd=cwd, env=env, timeout_seconds=timeout_seconds)
    s.pipeline_execution._run_trial_subprocess = trial


def restore(log):
    values = [line.split(': ', 1)[1] for line in log.splitlines() if line.startswith('RESTORE_STATUS: ')]
    return values[-1] if values else 'UNKNOWN'


def execute(case, directory, p1, s, sources):
    import qa_pipeline_agent3 as a
    p, ui = s.pipeline, s.pipeline_ui
    product = p1 / 'product_baseline/virtual-controller.html'
    grades = []
    facts = dict(browser_executed=False)
    if case['kind'] == 'direct':
        tc = p.ProductTestCaseCandidate.model_validate(case['tc'])
        before = tc.model_dump_json()
        write(directory / 'tc.json', tc.model_dump(mode='json'))
        eligible = a.evaluate_agent3_eligibility(tc)
        write(directory / 'eligibility.json', eligible.model_dump(mode='json'))
        if eligible.status.value != 'ELIGIBLE':
            return dict(outcome='NOT_AUTOMATABLE', browser_executed=False), grades
        observation = a.inspect_target_ui(product, required_selectors=set(eligible.required_selectors),
                                         required_harness_keys=set(eligible.required_harness_keys), discover_generic=False)
        plan = a.ControllerMapAgent3().plan(tc, observation, {}).plan
        cp = a.evaluate_checkpoint3_plan(tc, plan, observation, legacy_wording_checks=False,
                                        allow_state_change_terminal_observation=True)
        write(directory / 'plan.json', plan.model_dump(mode='json'))
        write(directory / 'checkpoint.json', cp.model_dump(mode='json'))
        if cp.status.value != 'PASS':
            return dict(outcome='STRUCTURAL_BLOCK', browser_executed=False), grades
        code = a.compile_automation_candidate(directory.name, tc, plan, explicit_expectations_only=True, typed_values=True)
        compiled = a.evaluate_compiled_candidate(tc, code)
        write(directory / 'static-checks.json', [item.model_dump(mode='json') for item in compiled])
        if any(item.status.value == 'FAIL' for item in compiled):
            return dict(outcome='COMPILE_BLOCK', browser_executed=False), grades
        candidate = directory / 'candidate.py'
        candidate.write_text(code, encoding='utf-8')
        trial = p.run_candidate_trial(candidate, product, directory / 'evidence', timeout_seconds=90)
        write(directory / 'trial.json', trial.model_dump(mode='json'))
        log = (directory / 'evidence' / trial.stdout_file).read_text(encoding='utf-8')
        grades = [compare(tc.model_dump(mode='json'), plan.model_dump(mode='json'), log,
                          run_id=directory.name, outcome=trial.outcome.value)]
        assert tc.model_dump_json() == before
        facts = dict(outcome=trial.outcome.value, browser_executed=True, restore=restore(log), evidence_complete=trial.evidence_complete)
    elif case['kind'] == 'reuse':
        catalog, _ = p.load_approved_regression_catalog(p1 / 'approved_assets')
        spec = next(tc for tc in catalog if tc.tc_id == 'TC-V2-001')
        trial = p.run_existing_regression(spec, p1 / 'approved_assets' / spec.automation_file,
                                          product, directory / 'evidence', timeout_seconds=90)
        write(directory / 'trial.json', trial.model_dump(mode='json'))
        log = next((directory / 'evidence').rglob('*stdout*')).read_text(encoding='utf-8')
        source = p1 / 'approved_assets/revisions/20261005/TC-V2-001'
        tc, plan = read(source / 'test_case.json')['test_case'], read(source / 'plan.json')
        write(directory / 'tc.json', tc); write(directory / 'plan.json', plan)
        identity = next(json.loads(line.split(': ', 1)[1]) for line in log.splitlines() if line.startswith('QA_ASSERTIONS_COMPLETE: '))
        outcome = 'PASS' if trial.status.value == 'PASSED' else trial.source_outcome
        grades = [compare(tc, plan, log, run_id=identity['run_id'], outcome=outcome)]
        facts = dict(outcome=outcome, browser_executed=True, restore=restore(log), evidence_complete=trial.evidence_complete)
    else:
        source = sources['waiting' if case['kind'] == 'waiting' else 'normal']
        run = directory / 'runs' / source.name
        if case['kind'] == 'saved':
            run.mkdir(parents=True)
            for path in source.iterdir():
                if path.is_dir() and path.name in {'candidates', 'evidence'}:
                    shutil.copytree(path, run / path.name)
                elif path.is_file() and (path.name.startswith(('agent1_', 'agent2_', 'agent3_', 'checkpoint1', 'checkpoint2', 'checkpoint3'))
                                        or path.name in {'request.json', 'run_manifest.json', 'approved_regression_catalog.json', 'srs_snapshot.md'}):
                    shutil.copy2(path, run / path.name)
            args = SimpleNamespace(run_id=run.name, runs_root=str(run.parent), target_html=str(product),
                                   baseline_tests=str(p1 / 'product_baseline/tests/test_controller.py'),
                                   approved_assets_root=str(p1 / 'approved_assets'), timeout=90)
            rc = p.run_validation_execution(args)
            if rc != 0:
                return dict(outcome='ERROR', browser_executed=False), []
            assert p.run_agent4(SimpleNamespace(run_id=run.name, runs_root=str(run.parent))) == 0
            result = read(run / 'validation_execution.json')
            row = (result.get('candidate_results') or [result['candidate_result']])[0]
            report = read(run / 'final_report.json')
            log = (run / row['stdout_file']).read_text(encoding='utf-8')
            facts = dict(outcome='PASS' if row['status'] == 'PASSED' else row['source_outcome'],
                         browser_executed=not row['reused'], restore=restore(log), recommendation=report['recommendation'])
            checks = local_checks(run, True, True)
            scores = python_scores(run, checks)
            grades = [g for g in scores['judgments'] if g['score'] is not None]
            spec = FlowSpec(case_id=case['id'], request='request.json', oracle='oracle.json', generation_model='offline', judge_model='offline')
            packets = collect_packets(run, p1, spec, dict(request=read(run / 'request.json'),
                                      oracle={'status': 'DRAFT'}, target_commit='LOCAL_FROZEN_WORKTREE'), checks)
            write(directory / 'p2-transfer.json', {role: {'artifact_files': list(packet.sources['artifact'])} for role, packet in packets.items()})
            for role, packet in packets.items():
                write(directory / ('p2-' + role + '-NOT-SENT.json'), packet.model_dump(mode='json'))
            facts['roles_collected'] = sorted(packets)
        elif case['kind'] == 'waiting':
            shutil.copytree(source, run)
            analysis = read(run / 'agent1_change_analysis.json')
            notice = ui._waiting_user_notice(run, run.name)
            blocked = False
            try:
                p._load_verified_agent1_run(run, run.name)
            except ValueError as exc:
                # State and its verified display contract, not exception wording,
                # establish that this is an expected pause rather than an error.
                manifest = read(run / 'run_manifest.json')
                if not notice or manifest.get('handoff_status') != 'PAUSE':
                    raise
                blocked = True
                write(directory / 'blocked.json', {'reason': str(exc), 'handoff_status': manifest['handoff_status']})
            notes = p._final_review_notes_for_validation(run)
            facts = dict(outcome=analysis['decision'], browser_executed=False,
                         questions_preserved=bool(notice) and bool(analysis['user_questions']) and
                         all(q in '\n'.join(notes) and q in notice for q in analysis['user_questions']),
                         next_stage_blocked=blocked and not (run / 'agent2_test_design.json').exists())
            write(directory / 'waiting-check.json', dict(notes=notes, facts=facts))
        else:
            shutil.copytree(source, run)
            assets, srs = directory / 'assets', directory / 'srs.md'
            shutil.copytree(p1 / 'approved_assets', assets)
            shutil.copy2(p1 / 'docs/01_PRODUCT_SRS.md', srs)
            before, srs_hash = hashes(assets), digest(srs)
            tc = ui._candidate_test_case(run, 'TC-CAND-001')
            comparison = ui.candidate_asset_comparison(tc, assets)
            write(directory / 'comparison.json', comparison)
            kind = case['kind']
            mode = 'REPLACE' if kind == 'REPLACE' else 'ADD'
            decision = kind if kind in {'HOLD', 'DECLINE'} else 'APPROVE'
            def decide():
                return ui.decide_candidate_asset(run.parent, assets, product, run.name, 'TC-CAND-001',
                    srs_path=srs, decision=decision, reviewer='로컬 반복 검증', note='시험용 사본이며 공식 승인 아님',
                    registration_mode=mode, replace_tc_ids=['TC-MODE-003'] if mode == 'REPLACE' else [],
                    comparison_sha256=comparison['fingerprint'])
            record = decide()
            write(directory / 'decision.json', record)
            after = hashes(assets)
            if kind in {'HOLD', 'DECLINE'}:
                ok = before == after
                outcome = record['decision']
            else:
                catalog, snap = p.load_approved_regression_catalog(assets)
                ids = {item.tc_id for item in p._catalog_from_snapshot(snap)}
                ok = record['official_tc_id'] in ids and all(digest(assets / name) == value for name, value in before.items() if name != 'registry.json')
                ok = ok and (('TC-MODE-003' not in ids) == (kind == 'REPLACE'))
                outcome = 'REPLACED' if kind == 'REPLACE' else record['decision']
                if kind == 'DUPLICATE':
                    again = decide()
                    ok = ok and again == record and hashes(assets) == after
                    outcome = 'DUPLICATE_UNCHANGED'
                    write(directory / 'decision-again.json', again)
            facts = dict(outcome=outcome, browser_executed=False, asset_contract_ok=ok, srs_unchanged=digest(srs) == srs_hash)
    return facts, grades


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--p1', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=5, choices=range(1, 11))
    parser.add_argument('--cases', nargs='+', help='사전 점검용 시나리오 ID 선택. 생략하면 전체 20개.')
    args = parser.parse_args()
    p1, output = args.p1.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'records').mkdir()
    sys.path[:0] = [str(p1 / 'src'), str(p1 / 'tests')]
    import pipeline_test_support as s
    install_offline(s)
    sources = dict(normal=p1 / 'runs/cont1007/runs/RUN-20261006-124032-11E19F',
                   waiting=p1 / 'runs/remaining-representative-20261005/live/runs/RUN-20261005-025933-EB9B4F')
    originals = {k: hashes(v) for k, v in sources.items()}
    frozen = protected(p1)
    write(output / 'frozen.json', dict(code_and_assets=frozen, saved_sources=originals))
    cases = prepare_cases(s)
    if args.cases:
        if len(args.cases) != len(set(args.cases)) or set(args.cases) - {c['id'] for c in cases}:
            raise ValueError('중복 또는 알 수 없는 시나리오')
        cases = [case for case in cases if case['id'] in args.cases]
    plan = dict(version=VERSION, mode='OFFLINE_LOCAL', date_kst=datetime.now().isoformat(),
                repetitions=args.repetitions, cases=cases, api_calls=0, semantic_review='NOT_PERFORMED')
    write(output / 'plan.json', plan)
    pin = digest(output / 'plan.json')
    for iteration in range(1, args.repetitions + 1):
        for case in cases:
            trial_id = f'T{(iteration - 1) * len(cases) + cases.index(case) + 1:03}'
            directory = output / trial_id
            directory.mkdir()
            start = time.monotonic()
            error, facts, grades = None, {'outcome': 'ERROR', 'browser_executed': False}, []
            with (directory / 'runner.log').open('w', encoding='utf-8') as stream, redirect_stdout(stream):
                try:
                    facts, grades = execute(case, directory, p1, s, sources)
                except Exception as exc:
                    import traceback
                    traceback.print_exc(file=stream)
                    error = type(exc).__name__ + ': ' + str(exc)
            unchanged = protected(p1) == frozen and originals == {k: hashes(v) for k, v in sources.items()}
            write(directory / 'facts.json', facts)
            write(directory / 'p2-grades.json', grades)
            evidence = [dict(file=path.relative_to(output).as_posix(), sha256=digest(path))
                        for path in directory.rglob('*') if path.is_file() and '__pycache__' not in path.parts]
            row = dict(trial_id=trial_id, case_id=case['id'], iteration=iteration, plan_sha256=pin,
                       actual=facts, grades=grades, error=error, protected_unchanged=unchanged,
                       duration_seconds=round(time.monotonic() - start, 3), evidence=evidence)
            write(output / 'records' / (trial_id + '.json'), row)
            print(trial_id, case['id'], facts['outcome'], 'ERROR' if error else '', flush=True)
            if not unchanged or error:
                write_report(output)
                raise RuntimeError('Local error or protected source change; remaining trials NOT_RUN. Inspect before rerunning.')
    result = write_report(output)
    print(json.dumps({k: result[k] for k in ('planned', 'recorded', 'counts', 'browser_trials', 'restored', 'p2')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
