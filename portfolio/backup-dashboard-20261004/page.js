(() => {
  'use strict';
  const evidence = window.PORTFOLIO_EVIDENCE;
  const rubric = window.PORTFOLIO_RUBRIC;
  if (!evidence || !rubric) return;
  const escape = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
  const roles = [
    ['A1','Agent 1','요구사항 분석','변경·유지 조건, 부족 정보, 영향 범위와 인용 근거를 평가합니다.'],
    ['A2','Agent 2','TC 설계','미평가 1 · 당시 해당 없음 1. TC-R05 적용 판단 오류는 후속 검토에서도 남았습니다.'],
    ['A3','Agent 3','자동화 수행','원본 A3-01은 3점 반려. 후속 대조에서 오반려를 확인했고 별도 재평가에서 5점입니다.'],
    ['A4','Agent 4','규칙 기반 결과 보고','파이썬 사실 검사 2개 포함. A4-02의 5점은 후속 평가에서 해당 없음으로 바뀌었습니다.'],
    ['COMMON','공통 기준','입력·권한','네 역할에 공통으로 적용합니다. 역할별 점수에 중복 합산하지 않습니다.'],
    ['E2E','전체 연결','인계·완료·승인','단일 역할 평가만으로 전체 연결 점수를 만들지 않습니다. 현재 4개 모두 미평가입니다.']
  ];
  for (const [id,value] of Object.entries({'count-scored':evidence.summary.evaluated,'count-review':evidence.summary.unevaluated,'count-na':evidence.summary.not_applicable,'count-fail':evidence.summary.violations.length})) {
    document.getElementById(id).textContent = value;
  }
  const followups = {
    'A3-01':'후속 확인: 원본 명세·코드·로그에 복원 비교가 있었습니다. 평가 입력 보완 후 별도 재평가에서 5점. P1 자료와 위 원본 3점은 그대로 보존했습니다.',
    'TC-R05':'후속 확인: 정상 전환도 평가 대상인데 제외한 적용 판단 오류입니다. 기준 2.4로 안내를 정리했지만 실제 모델 효과는 아직 미검증입니다.',
    'A4-02':'후속 확인: 분류할 실패나 원인 주장이 없는 사례여서 기준 2.3 재평가에서는 해당 없음으로 처리했습니다. 원본 5점을 변경하지 않습니다.',
    'C02':'해석 제한: 이 사례의 무승인 변경 여부와 별개로, 실제 충돌 지시를 넣은 방어 시험까지 수행한 결과는 아닙니다.'
  };
  const verdicts = {PASS:['통과','green'],FAIL:['반려','red'],REVIEW:['미평가','amber'],NOT_APPLICABLE:['해당 없음','gray']};
  const methods = {PYTHON:'파이썬',AI:'AI',HYBRID:'파이썬 + AI',NOT_EVALUATED:'미평가'};
  function criterionHTML(item, definition) {
    const [label,color] = verdicts[item.verdict] || ['미평가','gray'];
    const fields = [['평가 목적',definition.objective],['적용 조건',definition.applicability],['통과 기준',definition.pass_condition],['위반 기준',definition.fail_condition],['판단 보류',definition.review_condition],['정상 예시',definition.positive_example],['위반 예시',definition.negative_example]];
    return `<details class="criterion" id="criterion-${escape(item.criterion_id)}"><summary><span class="criterion-id">${escape(item.criterion_id)}</span><span class="criterion-title">${escape(item.title)}</span><span class="criterion-score">${item.score === null ? '—' : item.score+' / 5'}</span><span class="pill ${color}">${label}</span></summary><div class="criterion-body"><div class="criteria-definition"><div class="definition-head"><h3>현재 세부 기준 · 2.4 (원본 점수 재채점 아님)</h3><span>평가 담당: ${escape(methods[definition.method])}</span></div><dl>${fields.map(([name,value])=>`<dt>${name}</dt><dd>${escape(value)}</dd>`).join('')}</dl></div><div class="original-reason"><h3>원본 평가 근거 · 당시 기준 2.2</h3><p>${escape(item.reason)}</p><p>원본 기록의 평가 방법: ${escape(methods[item.evaluation_method] || item.evaluation_method)}</p>${followups[item.criterion_id]?`<p class="followup">${escape(followups[item.criterion_id])}</p>`:''}</div></div></details>`;
  }
  document.getElementById('all-criteria').innerHTML = roles.map(([key,name,description,note]) => {
    const group = evidence.groups[key];
    const definitions = rubric.criteria.filter(row=>row.owner === key);
    const items = definitions.map(definition=> {
      const item = evidence.criteria.find(row=>row.criterion_id === definition.criterion_id);
      return item ? criterionHTML(item,definition) : '';
    }).join('');
    const state = key === 'A3' ? ['오반려 후속 확인','amber'] : key === 'A2' || key === 'A4' ? ['적용 판단 재확인','amber'] : group.unevaluated ? ['미평가 포함','gray'] : ['원본 기준 통과','green'];
    return `<details class="role-group" id="group-${key}"><summary><span class="role-name">${escape(name)}<small>${escape(description)}</small></span><span class="role-count">채점 ${group.evaluated} / ${group.total}개</span><span class="role-score">${group.average_5 === null ? '—' : group.average_5.toFixed(2)} <small>${group.average_5 === null ? '미평가' : '/ 5'}</small></span><span class="role-state"><span class="pill ${state[1]}">${state[0]}</span></span></summary><div class="role-body"><p class="role-note">${escape(note)}</p>${items}</div></details>`;
  }).join('');
  document.getElementById('expand-groups').addEventListener('click',()=>document.querySelectorAll('.role-group').forEach(group=>{group.open = true;}));
  document.getElementById('collapse-all').addEventListener('click',()=>document.querySelectorAll('#all-criteria details').forEach(detail=>{detail.open = false;}));
  const branches = ['hold','no-consent','approve','hold-approve','duplicate','stale-srs','rollback'];
  document.getElementById('asset-matrix').innerHTML = evidence.matrix.map(item => `<tr><th scope="row">${escape(item.title)}</th>${branches.map(branch => {
    const result = item.branches.find(x => x.branch === branch);
    return `<td>${result?.status === 'PASS' ? '일치' : result ? escape(result.status) : '미확인'}</td>`;
  }).join('')}</tr>`).join('');
  document.getElementById('source-hashes').innerHTML = evidence.sources.map(source => `<div class="hash-block"><strong>${escape(source.artifact)}</strong><code>SHA-256 ${escape(source.sha256)}</code></div>`).join('');
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  function activate(tab, focus=false) {
    tabs.forEach(button => {
      const selected = button === tab;
      button.setAttribute('aria-selected',String(selected));
      button.tabIndex = selected ? 0 : -1;
      document.getElementById(button.getAttribute('aria-controls')).hidden = !selected;
    });
    if (focus) tab.focus();
  }
  tabs.forEach((tab,index) => {
    tab.addEventListener('click',() => activate(tab));
    tab.addEventListener('keydown',event => {
      let next;
      if (event.key === 'ArrowRight') next = (index+1)%tabs.length;
      if (event.key === 'ArrowLeft') next = (index+tabs.length-1)%tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length-1;
      if (next !== undefined) {event.preventDefault();activate(tabs[next],true);}
    });
  });
})();
