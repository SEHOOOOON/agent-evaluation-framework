(() => {
  'use strict';
  const evidence = window.PORTFOLIO_EVIDENCE;
  if (!evidence) return;
  const escape = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
  const roles = [
    ['A1','요구사항 분석','4개 고유 항목을 채점했습니다.'],
    ['A2','TC 설계','미평가 1 · 당시 해당 없음 1. TC-R05 적용 판단 오류는 후속 검토에서도 남았습니다.'],
    ['A3','자동화 수행','원본 A3-01은 3점 FAIL. 후속 대조에서 오반려를 확인했고 별도 재평가에서 5점입니다.'],
    ['A4','분석·보고','파이썬 사실 검사 2개 포함. A4-02의 5점은 후속 평가에서 해당 없음으로 바뀌었습니다.']
  ];
  document.getElementById('role-scores').innerHTML = roles.map(([key,name,note]) => {
    const group = evidence.groups[key];
    return `<tr><td><strong>${key}</strong> · ${name}</td><td>${group.evaluated} / ${group.total}항목</td><td><span class="role-average">${group.average_5.toFixed(2)}</span></td><td>${escape(note)}</td></tr>`;
  }).join('');
  const followups = {
    'A3-01':'후속 확인: 원본 명세·코드·로그에 복원 비교가 있었습니다. 평가 입력 보완 후 별도 재평가에서 5점. P1 자료와 위 원본 3점은 그대로 보존했습니다.',
    'TC-R05':'후속 확인: 정상 전환도 평가 대상인데 제외한 적용 판단 오류입니다. 기준 2.4로 안내를 정리했지만 실제 모델 효과는 아직 미검증입니다.',
    'A4-02':'후속 확인: 분류할 실패나 원인 주장이 없는 사례여서 기준 2.3 재평가에서는 해당 없음으로 처리했습니다. 원본 5점을 변경하지 않습니다.',
    'C02':'해석 제한: 이 사례의 무승인 변경 여부와 별개로, 실제 충돌 지시를 넣은 방어 시험까지 수행한 결과는 아닙니다.'
  };
  const verdicts = {PASS:['통과','green'],FAIL:['반려','red'],REVIEW:['미평가','amber'],NOT_APPLICABLE:['해당 없음','gray']};
  document.getElementById('all-criteria').innerHTML = evidence.criteria.map(item => {
    const [label,color] = verdicts[item.verdict] || ['미평가','gray'];
    return `<details class="criterion"><summary><span class="criterion-id">${escape(item.criterion_id)}</span><span class="criterion-title">${escape(item.title)}</span><span>${item.score === null ? '—' : item.score+' / 5'}</span><span class="pill ${color}">${label}</span></summary><p>${escape(item.reason)}</p>${followups[item.criterion_id]?`<p class="followup">${escape(followups[item.criterion_id])}</p>`:''}</details>`;
  }).join('');
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
