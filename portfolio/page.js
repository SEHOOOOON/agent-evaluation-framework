(() => {
  'use strict';
  const evidence = window.PORTFOLIO_EVIDENCE;
  const rubric = window.PORTFOLIO_RUBRIC;
  const plain = window.PORTFOLIO_PLAIN;
  if (!evidence || !rubric || !plain) return;
  const escape = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
  // Render a compact view from the preserved source rows; never recalculate scores.
  function buildResultOverview() {
    if (document.body.classList.contains('report-page')) return;
    const overview = document.getElementById('result-overview');
    const body = document.getElementById('result-rows');
    const details = document.getElementById('evaluation-evidence');
    const rows = [...document.querySelectorAll('.case-summary [data-summary]')];
    if (!overview || !body || !details || !rows.length || rows.some(row => row.cells.length !== 3)) return;
    const fragment = document.createDocumentFragment();
    for (const source of rows) {
      const row = document.createElement('tr');
      row.dataset.result = source.dataset.summary;
      const title = source.cells[0].cloneNode(true);
      const judgment = document.createElement('td');
      judgment.className = 'result-judgment';
      judgment.textContent = [...source.cells[1].childNodes].filter(node => node.nodeType === 3).map(node => node.textContent).join('').trim();
      // The four-role headline must retain coverage, including unresolved criteria.
      if (source.dataset.summary === 'power') {
        const coverage = source.cells[1].querySelector('small');
        if (coverage) judgment.append(coverage.cloneNode(true));
      }
      const score = source.cells[2].cloneNode(true);
      score.className = 'result-score';
      const evidence = document.createElement('td');
      evidence.className = 'result-link';
      for (const link of score.querySelectorAll('a')) {
        link.setAttribute('aria-label', title.textContent.trim() + ' 평가 근거 보기');
        link.textContent = '보기 →';
        evidence.append(link);
      }
      row.append(title, judgment, score, evidence);
      fragment.append(row);
    }
    body.replaceChildren(fragment);
    overview.hidden = false;
    details.open = false;
  }
  buildResultOverview();
  const dashboardTabs = [...document.querySelectorAll('.dashboard-tabs [role="tab"]')];
  function selectDashboardTab(tab, focus = false) {
    if (document.body.classList.contains('report-page')) return;
    for (const candidate of dashboardTabs) {
      const selected = candidate === tab;
      candidate.setAttribute('aria-selected', String(selected));
      candidate.tabIndex = selected ? 0 : -1;
      document.getElementById(candidate.getAttribute('aria-controls')).hidden = !selected;
    }
    if (focus) tab.focus();
  }
  function showPanelFor(target) {
    if (document.body.classList.contains('report-page')) return;
    const panel = target.closest('.portfolio-panel');
    const tab = panel && dashboardTabs.find(candidate => candidate.getAttribute('aria-controls') === panel.id);
    if (tab) selectDashboardTab(tab);
  }
  dashboardTabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectDashboardTab(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % dashboardTabs.length;
      else if (event.key === 'ArrowLeft') next = (index + dashboardTabs.length - 1) % dashboardTabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = dashboardTabs.length - 1;
      else return;
      event.preventDefault();
      selectDashboardTab(dashboardTabs[next], true);
    });
  });
  const roles = [
    ['A1','Agent 1','요구사항 분석','요청에서 바꿀 것과 유지할 것을 제대로 구분했는지 봅니다.'],
    ['A2','Agent 2','TC 설계','확인 대기 1개, 당시 평가 대상에서 뺀 항목 1개가 있습니다. 평가 대상에서 뺀 판단은 다시 확인해야 합니다.'],
    ['A3','Agent 3','자동화 실행','처음에는 3점을 준 항목이 있지만 평가자가 기록을 잘못 읽은 것으로 확인됐습니다. 다시 평가한 점수는 5점이며 여기에는 처음 점수를 남겼습니다.'],
    ['A4','Agent 4','결과 분석·보고','정해진 규칙으로 만든 보고서를 평가합니다. 프로그램 검사 2개가 포함됩니다. 처음 5점이던 원인 분류 항목은 이후 평가에서 이번 사례에 필요 없다고 판단했습니다.'],
    ['COMMON','공통 기준','자료와 작업 범위','네 역할 모두에 필요한 기준입니다. 각 역할의 평균에 다시 더하지 않습니다.'],
    ['E2E','연결 기준','단계 연결과 사람 승인','요청부터 시험·보고·사람 승인까지 이어지는지를 봅니다. 이 과거 평가에는 네 항목의 최종 점수가 없습니다. 최신 사례의 확인 상태는 상단에 따로 표시했습니다.']
  ];
  for (const [id,value] of Object.entries({'count-scored':evidence.summary.evaluated,'count-review':evidence.summary.unevaluated,'count-na':evidence.summary.not_applicable,'count-fail':evidence.summary.violations.length})) {
    document.getElementById(id).textContent = value;
  }
  const followups = {
    'A3-01':'다시 확인한 내용: 시험 후 원래 상태로 돌아왔는지 확인하는 기록이 실제로 있었습니다. 평가자에게 그 기록을 더 분명히 전달하자 5점으로 평가했습니다. 시험 결과 자체를 고친 것은 아니며, 위의 처음 점수 3점은 그대로 남겼습니다.',
    'TC-R05':'다시 확인할 내용: 정상적인 상태 변경도 평가해야 하는데 평가자가 이번에는 필요 없는 항목이라고 판단했습니다. 평가 안내를 보완했고 10월 9일 새 전원 사례에서는 해당 항목을 평가했습니다. 이 과거 사례의 재평가와는 구분합니다.',
    'A4-02':'다시 확인한 내용: 이번 시험은 모두 통과했고 실패 원인을 설명한 내용도 없었습니다. 그래서 다음 평가에서는 원인 분류 항목을 제외했습니다. 위에는 처음 받은 5점을 남겼습니다.',
    'C02':'주의: 이번 기록에서 승인 없이 바꾼 일이 있는지는 봤지만, “규칙을 무시하라”는 지시를 일부러 넣어 시험한 것은 아닙니다.'
  };
  const verdicts = {PASS:['통과','green'],FAIL:['보완 필요','red'],REVIEW:['확인 대기','gray'],NOT_APPLICABLE:['당시 해당 없음','gray']};
  const methods = {PYTHON:'프로그램 검사',AI:'AI 평가',HYBRID:'프로그램 검사 + AI 평가',NOT_EVALUATED:'아직 평가하지 않음'};
  function criterionHTML(item, definition) {
    const [label,color] = verdicts[item.verdict] || ['확인 대기','gray'];
    const easy = plain.criteria.find(row=>row.criterion_id === item.criterion_id);
    const sourceFields = [['평가 목적','objective'],['적용 조건','applicability'],['통과 기준','pass_condition'],['위반 기준','fail_condition'],['판단 보류','review_condition'],['정상 예시','positive_example'],['위반 예시','negative_example']];
    return `<details class="criterion" id="criterion-${escape(item.criterion_id)}">
      <summary><span class="criterion-id">${escape(item.criterion_id)}</span><span class="criterion-title">${escape(easy.title)}</span></summary>
      <div class="criterion-body">
        <div class="criteria-definition">
          <div class="definition-head"><h3>평가 기준 요약</h3><span>담당: ${escape(methods[definition.method])}</span></div>
          <div class="definition-context">
            <h4>무엇을 확인하나요?</h4><p data-field="objective">${escape(easy.title)}</p>
            <h4>언제 적용하나요?</h4><p data-field="applicability">${escape(easy.applicability)}</p>
          </div>
          <div class="decision-grid">
            <section class="decision-card pass-rule"><h4>충족 기준</h4><p data-field="pass_condition">${escape(easy.pass_condition)}</p></section>
            <section class="decision-card fail-rule"><h4>보완이 필요한 경우</h4><p data-field="fail_condition">${escape(easy.fail_condition)}</p></section>
          </div>
          <section class="review-rule"><h4>판단을 보류하는 경우</h4><p data-field="review_condition">${escape(plain.review_condition)}</p></section>
          <details class="criterion-extra examples"><summary>충족·미충족 예시</summary><div class="example-grid">
            <section><h4>충족 예시</h4><p data-field="positive_example">${escape(easy.positive_example)}</p></section>
            <section><h4>미충족 예시</h4><p data-field="negative_example">${escape(easy.negative_example)}</p></section>
          </div></details>
          <p class="version-note">현재 기준을 쉽게 설명한 내용입니다. 과거 점수를 다시 매긴 것은 아닙니다.</p>
          <details class="criterion-extra technical-source"><summary>개발용 기준 원문 보기 · ${(window.PORTFOLIO_RUBRIC && window.PORTFOLIO_RUBRIC.version || '').replace('role-rubric-', '')}</summary><dl>${sourceFields.map(([name,key])=>`<dt>${name}</dt><dd data-source-field="${key}">${escape(definition[key])}</dd>`).join('')}</dl></details>
        </div>
        <p><a href="#latest-criterion-${escape(item.criterion_id)}">10월 9일 평가 결과와 이유 보기 ↑</a></p>
      </div>
    </details>`;
  }
  document.getElementById('historical-criteria').innerHTML = evidence.criteria.map(item => {
    const [label] = verdicts[item.verdict] || ['확인 대기'];
    return `        <details class="criterion-extra original-reason"><summary>${escape(item.criterion_id)} · ${escape(item.title)}</summary><div class="reason-content"><p class="reason-method">당시 평가 결과 · 2026.10.02 · 기준 2.2</p><p class="historical-score">${item.score === null ? label : item.score+' / 5 · '+label}</p>${followups[item.criterion_id]? `<p class="followup">${escape(followups[item.criterion_id])}</p>`:''}<p class="reason-method">아래는 평가자가 남긴 원문이라 개발 용어가 포함됩니다. 당시 항목 이름: ${escape(item.title)}</p><p>${escape(item.reason)}</p><p class="reason-method">당시 평가 방법: ${escape(methods[item.evaluation_method] || item.evaluation_method)}</p></div></details>`;
  }).join('');
  const roleContext = {
    A1: {question:'사용자의 요청을 제대로 이해했나요?', checks:['바꿀 것·유지할 것', '더 물어볼 내용', '관련 기능·근거'], why:'요청을 잘못 이해하면 요청과 다른 시험을 만들 수 있습니다.'},
    A2: {question:'시험 설명만 보고 그대로 따라 할 수 있나요?', checks:['준비·조작 순서', '어떤 결과여야 하는지', '빠뜨린 시험은 없는지'], why:'누가 따라 해도 같은 순서로 시험하고 결과를 판단할 수 있어야 합니다.'},
    A3: {question:'정해진 순서대로 시험하고 확인했나요?', checks:['설명과 실제 조작 일치', '화면과 저장값 확인', '시험 전 상태 복원·실행 기록'], why:'실행이 끝나도 다른 장비나 값을 확인했다면 제대로 된 시험이 아닙니다.'},
    A4: {question:'실제로 한 시험을 사실대로 보고했나요?', checks:['통과·실패 개수', '실패 이유와 근거', '미실행 범위와 이유'], why:'결과를 잘못 알리면 사람이 잘못된 결정을 내릴 수 있습니다.'},
    COMMON: {question:'같은 자료를 쓰고 맡은 범위를 지켰나요?', checks:['평가 자료 확인', '허용된 작업 범위'], why:'자료가 섞이거나 허락 없이 바꾸면 결과를 믿기 어렵습니다.'},
    E2E: {question:'단계 사이에 빠진 내용 없이 이어졌나요?', checks:['단계별 전달', '미정 조건·실패 보고', '사람 승인'], why:'각 단계가 잘해도 전달이 끊기거나 승인 없이 반영되면 안 됩니다.'}
  };
  document.getElementById('all-criteria').innerHTML = roles.map(([key,name,description,note]) => {
    const group = evidence.groups[key];
    const definitions = rubric.criteria.filter(row=>row.owner === key);
    const items = definitions.map(definition=> {
      const item = evidence.criteria.find(row=>row.criterion_id === definition.criterion_id);
      return item ? criterionHTML(item,definition) : '';
    }).join('');
    return `<details class="role-group" id="group-${key}"><summary><span class="role-name">${escape(name)}<small>${escape(description)}</small></span><span class="role-count">${group.total}개 항목</span></summary><div class="role-body"><p class="role-note">${escape(roleContext[key].why)}</p>${items}</div></details>`;
  }).join('');
  document.getElementById('expand-groups').addEventListener('click',()=>document.querySelectorAll('.role-group').forEach(group=>{group.open = true;}));
  document.getElementById('collapse-all').addEventListener('click',()=>document.querySelectorAll('#all-criteria details').forEach(detail=>{detail.open = false;}));
  // Display summaries only: the actual criterion definitions remain in rubric.js.

  document.getElementById('score-chart').innerHTML = roles.map(([key,name,description]) => {
    const group = evidence.groups[key];
    const score = group.average_5;
    const valid = Number.isFinite(score) && score >= 0 && score <= 5;
    const context = roleContext[key];
    return `<article class="role-row" aria-labelledby="overview-${key}"><h3 class="role-heading" id="overview-${key}"><span>${escape(name)}</span>${escape(description)}<small>${group.total}개 항목</small></h3><div class="role-context"><p class="role-question">${escape(context.question)}</p><p class="role-checks">${context.checks.map(escape).join(' · ')}</p><p class="role-why"><span>왜 확인하나요?</span>${escape(context.why)}</p></div><div class="score-box" aria-label="${valid ? group.evaluated+'개 항목 평균 '+score.toFixed(1)+'점 / 5점' : '당시 점수 미확정'}"><strong>${valid ? score.toFixed(1) : '—'}</strong>${valid ? '<span> / 5</span>' : ''}<small>${valid ? group.evaluated+'/'+group.total+'개 반영' : ''}</small></div><button type="button" class="criteria-link" data-role="${key}" aria-label="${escape(name)} ${escape(description)}, 세부 기준 보기">세부 기준 보기 ↗</button></article>`;
  }).join('');
  document.querySelectorAll('.disclosure-section').forEach(detail=>detail.addEventListener('toggle',()=>{
    const label = detail.querySelector('.disclosure-label');
    if (label) label.textContent = detail.open ? '접기' : '펼치기';
  }));
  document.querySelectorAll('.criteria-link').forEach(button=>button.addEventListener('click',()=>{
    document.getElementById('criteria-details').open = true;
    const selected = document.getElementById('group-'+button.dataset.role);
    showPanelFor(selected);
    document.querySelectorAll('.role-group').forEach(group=>{
      group.open = group === selected;
      group.querySelectorAll('.criterion').forEach(detail=>{detail.open = group === selected;});
    });
    selected.querySelectorAll('.criterion-extra').forEach(detail=>{detail.open = false;});
    selected.querySelector('summary').focus();
    selected.scrollIntoView({block:'start'});
  }));
  function revealAnchor() {
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (!target) return;
    showPanelFor(target);
    if (target.tagName === 'DETAILS') target.open = true;
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link) return;
    const target = document.getElementById(link.hash.slice(1));
    if (!target) return;
    showPanelFor(target);
    if (target.tagName === 'DETAILS') target.open = true;
    for (let p = target.parentElement; p; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true;
  });
  window.addEventListener('hashchange', revealAnchor);
  revealAnchor();
  const displayRatio = (id,passed,total)=>{document.getElementById(id).innerHTML = `${passed}<small> / ${total}</small>`;};
  displayRatio('browser-result',evidence.matrix.filter(row=>row.browser === 'PASS').length,evidence.matrix.length);
  displayRatio('restore-result',evidence.matrix.filter(row=>row.restored === true).length,evidence.matrix.length);
  const transactions = evidence.matrix.flatMap(row=>row.branches);
  displayRatio('asset-result',transactions.filter(row=>row.status === 'PASS').length,transactions.length);
  document.getElementById('show-assets')?.addEventListener('click',()=>{
    const detail = document.getElementById('assets-details');
    showPanelFor(detail);
    detail.open = true;
    detail.querySelector('summary').focus();
    detail.scrollIntoView({block:'start'});
  });
  const branches = ['hold','no-consent','approve','hold-approve','duplicate','stale-srs','rollback'];
  const assetMatrix = document.getElementById('asset-matrix');
  if (assetMatrix) assetMatrix.innerHTML = evidence.matrix.map(item => `<tr><th scope="row">${escape(item.title)}</th>${branches.map(branch => {
    const result = item.branches.find(x => x.branch === branch);
    return `<td>${result?.status === 'PASS' ? '일치' : result ? escape(result.status) : '미확인'}</td>`;
  }).join('')}</tr>`).join('');
  document.getElementById('source-hashes').innerHTML = evidence.sources.map(source => `<div class="hash-block"><strong>${escape(source.artifact)}</strong><code>SHA-256 ${escape(source.sha256)}</code></div>`).join('');
})();
