"""Offline repetition report, separate from the 26-item semantic scorecard.

Consumes a frozen plan and hash-linked trial records. Never executes P1 or calls
AI. A correctly detected product mismatch can satisfy the local test contract.
"""
from collections import Counter
from html import escape
import json
from pathlib import Path
from urllib.parse import quote

from .execution_observations import equal
from .detailed_rubric import CRITERIA, PYTHON_CRITERIA, SCORE_VERSION, score_is_valid
from .v2_recorded import child, digest, read

VERSION = 'local-stability-1.0'
LABELS = {'PASS': '시험 통과', 'PRODUCT_MISMATCH_CANDIDATE': '제품 불일치 검출',
          'NOT_AUTOMATABLE': '실행 제외', 'WAITING_FOR_USER': '질문 후 대기',
          'APPROVED': '등록', 'REPLACED': '대체 등록', 'DECLINED': '미등록',
          'HELD': '보류', 'DUPLICATE_UNCHANGED': '중복 변경 없음',
          'ERROR': '실행 오류', 'NOT_RUN': '미실행'}


def summarize(root: Path):
    root = root.resolve()
    plan = read(root / 'plan.json')
    if plan.get('mode') != 'OFFLINE_LOCAL' or plan.get('version') != VERSION:
        raise ValueError('지원하는 로컬 반복 계획이 아닙니다.')
    repetitions = plan['repetitions']
    cases = plan['cases']
    if (type(repetitions) is not int or not 1 <= repetitions <= 100 or not cases
            or len({c['id'] for c in cases}) != len(cases)):
        raise ValueError('반복 횟수 또는 시나리오 ID 오류')
    if any(not c.get('expected') or not c.get('title') or not c.get('origin') for c in cases):
        raise ValueError('사전 기대결과·입력 출처가 필요합니다.')
    records = [read(path) for path in sorted((root / 'records').glob('*.json'))]
    planned = {(c['id'], n) for c in cases for n in range(1, repetitions + 1)}
    seen, trial_ids = {}, set()
    for record in records:
        if type(record.get('iteration')) is not int:
            raise ValueError('회차는 정수여야 합니다.')
        key = (record['case_id'], record['iteration'])
        if key not in planned or key in seen or record['trial_id'] in trial_ids:
            raise ValueError('예정에 없는 회차 또는 중복 결과입니다.')
        if record.get('plan_sha256') != digest(root / 'plan.json'):
            raise ValueError('다른 계획의 결과를 합산할 수 없습니다.')
        grades = record.get('grades', [])
        if len({g['criterion_id'] for g in grades}) != len(grades) or any(
                g['criterion_id'] not in PYTHON_CRITERIA or
                not score_is_valid(g.get('score'), g.get('verdict'), SCORE_VERSION) for g in grades):
            raise ValueError('기존 프로그램 평가 기준·점수 형식만 집계합니다.')
        seen[key] = record
        trial_ids.add(record['trial_id'])
    rows = []
    for case in cases:
        for n in range(1, repetitions + 1):
            record = seen.get((case['id'], n))
            row = dict(case_id=case['id'], title=case['title'], origin=case['origin'],
                       group=case['group'], iteration=n, expected=case['expected'],
                       actual={}, status='NOT_RUN', duration_seconds=None, grades=[], evidence=[])
            if record is not None:
                row.update(actual=record['actual'], duration_seconds=record['duration_seconds'],
                           grades=record.get('grades', []), evidence=record.get('evidence', []),
                           error=record.get('error'), trial_id=record['trial_id'])
                verified = bool(row['evidence'])
                for reference in row['evidence']:
                    try:
                        path = child(root, reference['file'])
                        original = root / reference['file']
                        linked = any(p.is_symlink() for p in [original, *original.parents] if p.is_relative_to(root))
                        verified = verified and path.is_file() and not linked and digest(path) == reference['sha256']
                    except (OSError, ValueError, KeyError, TypeError):
                        verified = False
                # Summary rows are not evidence themselves. Tie their facts and
                # grades to the persisted, hash-checked per-trial artifacts.
                for name, value in [('facts.json', row['actual']), ('p2-grades.json', row['grades'])]:
                    expected_file = record['trial_id'] + '/' + name
                    if sum(ref.get('file') == expected_file for ref in row['evidence']) != 1:
                        verified = False
                    else:
                        try:
                            verified = verified and equal(read(child(root, expected_file)), value)
                        except (OSError, ValueError, TypeError):
                            verified = False
                if record.get('error') or not verified or not record.get('protected_unchanged'):
                    row['status'] = 'ERROR'
                else:
                    row['status'] = 'MATCH' if all(
                        k in row['actual'] and equal(row['actual'][k], v) for k, v in case['expected'].items()) else 'MISMATCH'
            rows.append(row)
    groups = []
    for case in cases:
        subset = [r for r in rows if r['case_id'] == case['id']]
        counts = Counter(r['status'] for r in subset)
        # Only compared contract fields; log paths, timings and run IDs aren't behavior.
        signatures = {json.dumps({k: r['actual'].get(k) for k in case['expected']}, sort_keys=True)
                      for r in subset if r['status'] in {'MATCH', 'MISMATCH'}}
        comparable = counts['MATCH'] + counts['MISMATCH']
        groups.append(dict(case_id=case['id'], title=case['title'], group=case['group'], origin=case['origin'],
                           expected=case['expected'], planned=repetitions, counts=dict(counts),
                           consistent=(len(signatures) == 1) if comparable == repetitions else None,
                           outcomes=dict(Counter(r['status'] if r['status'] in {'ERROR', 'NOT_RUN'}
                                                 else r['actual'].get('outcome', 'NOT_RUN') for r in subset))))
    counts = Counter(r['status'] for r in rows)
    # Keep original claims for diagnosis, not as confirmed execution/restore evidence.
    confirmed = [r for r in rows if r['status'] in {'MATCH', 'MISMATCH'}]
    grades = [g for r in confirmed for g in r['grades']]
    criteria = sorted({g['criterion_id'] for g in grades if g.get('score') is not None})
    coverage = []
    for item in CRITERIA:
        matches = [g for g in grades if g['criterion_id'] == item['criterion_id']]
        scored = [g['score'] for g in matches if g['score'] is not None]
        coverage.append(dict(criterion_id=item['criterion_id'], title=item['objective'], owner=item['owner'],
                             checked=len(matches), verdicts=dict(Counter(g['verdict'] for g in matches)),
                             average_5=round(sum(scored) / len(scored), 2) if scored else None))
    return dict(version=VERSION, mode='OFFLINE_LOCAL', api_calls=0, ai_generation_rate=None,
                ai_quality_score=None, original_scores_changed=False, plan_sha256=digest(root / 'plan.json'),
                planned=len(rows), recorded=len(records), repetitions=repetitions, counts=dict(counts),
                expected_behavior_rate=round(counts['MATCH'] / len(rows) * 100, 1),
                browser_trials=sum(r['actual'].get('browser_executed') is True for r in confirmed),
                restored=sum(r['actual'].get('restore') == 'RESTORED' for r in confirmed),
                p2=dict(criterion_ids=criteria, rubric_total=26, ai_evaluated=0,
                        comparisons=len(grades), verdicts=dict(Counter(g['verdict'] for g in grades))),
                criterion_coverage=coverage, groups=groups, rows=rows,
                limitation='준비한 TC·저장 AI 결과의 로컬 반복입니다. 새 AI 생성·의미 판단·공식 승인·현장 장비 안정성은 미평가입니다.')


def render(summary):
    e = lambda x: escape(str(x))
    grade_labels = {'A3-03': '실행 대조', 'A4-01': '보고 집계', 'A4-03': '근거 연결', 'E04': '승인 보호'}
    verdict_labels = {'PASS': '통과', 'FAIL': '위반', 'REVIEW': '확인 부족', 'NOT_APPLICABLE': '해당 없음'}
    def status_text(row):
        return {'MATCH': '예상대로 처리', 'MISMATCH': '예상과 다름', 'ERROR': '검사 오류', 'NOT_RUN': '미실행'}[row['status']]
    group_rows = ''.join('<tr><td>' + e(g['title']) + '</td><td>' + e(g['group']) + '</td><td>'
        + e(g['counts'].get('MATCH', 0)) + '/' + e(g['planned']) + '</td><td>'
        + e(' · '.join(LABELS.get(k, k) + ' ' + str(v) + '회' for k, v in g['outcomes'].items()))
        + '</td><td>' + e('같음' if g['consistent'] is True else '다름' if g['consistent'] is False else '확인 부족')
        + '</td></tr>' for g in summary['groups'])
    trial_rows = ''
    for r in summary['rows']:
        unverified = r['status'] == 'ERROR'
        links = ' · '.join('<a href="' + quote(ref['file'], safe='/') + '">' + e(Path(ref['file']).name) + '</a>'
                           for ref in r['evidence'])
        attention = r['status'] != 'MATCH' or any(g['verdict'] == 'FAIL' for g in r['grades'])
        trial_rows += ('<tr data-attention="' + str(attention).lower() + '" data-state="' + r['status'] + '"><td>' + e(r.get('trial_id', '—'))
                      + '</td><td>' + e(r['title']) + '</td><td>' + e(r['iteration']) + '</td><td>'
                      + e(LABELS.get(r['actual'].get('outcome'), r['actual'].get('outcome', '미실행'))
                          + (' (기록값 · 미확인)' if unverified else ''))
                      + '</td><td class="' + r['status'] + '">' + status_text(r)
                      + '</td><td>' + e('근거 확인 불가' if unverified else {'RESTORED': '복원 확인', 'UNKNOWN': '확인 부족', 'FAILED': '복원 실패'}.get(r['actual'].get('restore'), r['actual'].get('restore', '해당 없음'))) + '</td><td>'
                      + e('근거 확인 불가' if unverified else ' / '.join(grade_labels.get(g['criterion_id'], g['criterion_id']) + ' ' + verdict_labels.get(g['verdict'], g['verdict']) for g in r['grades']) or '이번 기준 미평가')
                      + '</td><td><details><summary>근거</summary><p>' + e(r['origin']) + '</p><p>'
                      + e(r.get('error') or '') + '</p>' + links + '</details></td></tr>')
    issues = [r for r in summary['rows'] if r['status'] != 'MATCH' or any(g['verdict'] == 'FAIL' for g in r['grades'])]
    issue_text = '<br>'.join(e(r['title']) + ' · ' + str(r['iteration']) + '회차: ' +
                            (status_text(r) if r['status'] != 'MATCH' else 'P2 대조에서 위반 확인') for r in issues) or '이번 고정 조건에서 예상 밖 결과는 발견되지 않았습니다.'
    rubric_rows = ''.join('<tr><td>' + e(c['owner']) + '</td><td>' + e(c['criterion_id']) + '</td><td>' + e(c['title'])
                         + '</td><td>' + e(str(c['checked']) + '회' if c['checked'] else '이번 미평가')
                         + '</td><td>' + e(str(c['average_5']) + ' / 5' if c['average_5'] is not None else '—') + '</td></tr>'
                         for c in summary['criterion_coverage'])
    return '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P1·P2 로컬 반복 검증</title><style>
*{box-sizing:border-box}body{margin:0;background:#0c121b;color:#e8f0f9;font:15px/1.65 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:40px 28px}h1{font-size:29px;margin:8px 0}h2{font-size:20px;margin:34px 0 12px}p{color:#b5c4d6}.eyebrow,a{color:#72bcff}a{overflow-wrap:anywhere}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:26px 0}.stat{background:#152030;border:1px solid #2c3a4d;border-radius:10px;padding:20px}.stat strong{font-size:29px;display:block}.stat span,small{color:#aebed0}.note{border-left:3px solid #e4b962;background:#201e18;padding:14px 18px}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;background:#111b28}th,td{padding:12px 14px;border-bottom:1px solid #29374a;text-align:left;vertical-align:top}th{color:#a7bdd5;font-size:13px;background:#182436;white-space:nowrap}tr:hover{background:#172638}.MATCH{color:#73dbb1}.MISMATCH,.ERROR{color:#ffab9c}.NOT_RUN{color:#e4cc8a}summary{cursor:pointer;color:#72bcff}button{padding:8px 14px;background:#1c3047;color:#dcf0ff;border:1px solid #426081;border-radius:6px;margin:10px 6px 16px 0;cursor:pointer}.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}.columns section{border-top:1px solid #344559}footer{margin-top:35px;color:#94a6ba}@media(max-width:700px){main{padding:22px 16px}.stats,.columns{grid-template-columns:1fr 1fr}.stat{padding:12px}.stat strong{font-size:24px}.columns{grid-template-columns:1fr}td,th{padding:10px}h1{font-size:25px}}
</style><main><div class="eyebrow">프로젝트 1 실행 · 프로젝트 2 확인 / 로컬 검증</div><h1>같은 조건에서 다시 돌려도 예상대로 처리할까?</h1>
<p>''' + e(len(summary['groups'])) + '''개 시나리오를 각 ''' + e(summary['repetitions']) + '''회 시험합니다. 제품이 FAIL이어도 정확히 검출했다면 정상 처리입니다.<br>API 호출과 새 AI 생성은 없으며, 공식 자산 대신 시험용 사본을 사용했습니다.</p>
<div class="stats"><div class="stat"><span>예상대로 처리</span><strong>''' + e(summary['counts'].get('MATCH', 0)) + '/' + e(summary['planned']) + '''</strong><small>미실행·오류도 분모에 포함</small></div><div class="stat"><span>실제 브라우저 시험</span><strong>''' + e(summary['browser_trials']) + '''회</strong><small>나머지는 보류·승인 등 경로 확인</small></div><div class="stat"><span>시험 후 원상 복원</span><strong>''' + e(summary['restored']) + '''회</strong><small>실행기가 남긴 복원 증거 기준</small></div><div class="stat"><span>이번 P2 자동 평가 범위</span><strong>''' + e(len(summary['p2']['criterion_ids'])) + '''/26 항목</strong><small>항목 합집합 · 모든 회차의 전체 채점 아님</small></div></div>
<div class="note">이 수치는 AI 정확도나 프로젝트 완성도 점수가 아닙니다. 준비한 입력의 실행·복원·승인 경로 안정성을 확인합니다. 새 AI 판단과 생성의 반복 신뢰성은 아직 별도 확인이 필요합니다.</div>
<div class="columns"><section><h2>프로젝트 1 · 실행 수준</h2><p>다섯 관제점, 실패 검출, 보류, 재사용, 자산 선택을 확인합니다. 자산 선택은 함수 호출 시험이며 실제 사람이 승인 화면을 누른 시험은 아닙니다.</p></section><section><h2>프로젝트 2 · 평가 수준</h2><p>회차 누락·증거 해시와 사전 기대결과를 확인합니다. 기존 A3-03은 계획과 관찰, A4-01·A4-03은 보고 집계와 연결을 대조합니다. 의미 판단과 나머지 항목은 임의로 점수를 채우지 않습니다.</p></section></div>
<h2>이번에 확인할 문제</h2><p>''' + issue_text + '''</p><h2>시나리오별 결과</h2><div class="scroll"><table><thead><tr><th>시나리오</th><th>시험 종류</th><th>예상 처리 / 예정</th><th>실제 결과</th><th>반복 결과</th></tr></thead><tbody>''' + group_rows + '''</tbody></table></div>
<h2>26개 기준 중 이번에 확인한 부분</h2><details><summary>전체 기준과 이번 자동 대조 점수 보기</summary><p>각 항목에서 확인한 회차만의 평균입니다. 전체 Agent 품질 점수나 과거 AI 평가의 갱신이 아닙니다.</p><div class="scroll"><table><thead><tr><th>역할</th><th>기준</th><th>평가 내용</th><th>확인 범위</th><th>확인된 회차 평균</th></tr></thead><tbody>''' + rubric_rows + '''</tbody></table></div></details>
<h2>회차별 전체 기록</h2><button onclick="filter(false)">전체 보기</button><button onclick="filter(true)">예상 밖·오류만</button><div class="scroll"><table id="trials"><thead><tr><th>회차 ID</th><th>시나리오</th><th>반복</th><th>제품·절차 결과</th><th>예상과 비교</th><th>복원</th><th>P2 자동 대조</th><th>상세</th></tr></thead><tbody>''' + trial_rows + '''</tbody></table></div><footer>원본 실패와 과거 AI 점수는 보존합니다. <a href="summary.json">전체 결과 JSON</a> · <a href="plan.json">시작 전 고정 계획</a> · <a href="frozen.json">코드·자산 해시</a></footer></main><script>function filter(only){document.querySelectorAll('#trials tbody tr').forEach(r=>r.hidden=only&&r.dataset.attention!=='true')}</script></html>'''


def write_report(root):
    result = summarize(root)
    # New batch directories are enforced by the runner; refresh only derived views.
    (root / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'report.html').write_text(render(result), encoding='utf-8')
    return result
