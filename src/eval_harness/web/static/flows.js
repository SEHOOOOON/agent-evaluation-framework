(() => {
  "use strict";
  const el=id=>document.getElementById(id);
  const esc=value=>String(value??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;");
  const states={COMPLETED:"수행 완료",PARTIAL:"일부 수행",BLOCKED:"진행 차단",NOT_EXECUTED:"미실행",ERROR:"오류",LIMIT_REACHED:"호출 한도 중단",STARTED:"진행 중",RUNNING:"진행 중",STOPPED:"중단",NOT_EVALUATED:"미평가",PASS:"검사 통과",FAIL:"위반 발견",REVIEW:"검토 필요",JUDGE_BUDGET_EXHAUSTED:"평가 호출 미배정 또는 한도 소진",SAFETY_STOP:"안전 중단으로 미평가"};
  states.WAITING_FOR_USER="사용자 확인 대기";
  const label=value=>states[value]||value||"미기록";
  const stopReasons={JUDGE_PREFLIGHT_FAILED:"평가 자료의 크기·안전성 확인 중 문제가 있어 AI 평가를 시작하지 않았습니다. 프로젝트1의 시험 실패를 뜻하지 않습니다.",EVIDENCE_CHANGED_DURING_REVIEW:"평가 준비 또는 평가 중 원본 기록이 바뀌어 중단했습니다.",JUDGE_ERROR:"AI 평가 중 오류가 발생했습니다. 프로젝트1의 시험 결과와 구분해 확인하세요."};
  let ticket=0;
  stopReasons.RUNTIME_INPUT_PREFLIGHT_FAILED="기본·승인 TC 등 실행 자료를 읽지 못해 시작 전에 중단했습니다. 이 회차의 AI 호출은 없으며 에이전트 점수는 미평가입니다.";
  async function fetchJson(url){const r=await fetch(url,{cache:"no-store"});if(!r.ok)throw Error("전체 실행 기록을 읽을 수 없습니다.");return r.json();}
  function alert(text=""){el("flowAlert").textContent=text;el("flowAlert").hidden=!text;}
  function scores(card) {
    const s=card.summary, number=s.average_5==null?(card.versions?.mixed?"버전 혼합으로 산출 안 함":"미평가"):s.average_5+" / 5";
    const methods={PYTHON:"파이썬",AI:"AI",HYBRID:"파이썬+AI",NOT_EVALUATED:"미평가"};
    return `<div class="evidence-rows"><h3>평가 ${esc(s.evaluated)} / ${esc(s.total)}항목 · 미평가 ${esc(s.unevaluated)}항목</h3>
      <p>평가된 항목 평균 ${esc(number)} · 해당 없음 ${esc(s.not_applicable)} · ${esc(card.profile)}. 사람 검토 전이며 프로젝트 완성도·정확도가 아닙니다.</p>
      ${card.versions&&(card.versions.mixed||card.versions.missing_version_roles.length)?`<p>${esc(card.versions.note)}</p>`:""}
      ${s.violations.length?`<p class="status fail">위반 확인: ${esc(s.violations.join(", "))} — 평균과 별도로 검토</p>`:""}
      <p>${Object.entries(card.groups).map(([name,g])=>esc(name)+": "+esc(g.evaluated)+"/"+esc(g.total)+" 채점 · "+(g.average_5==null?"미평가":esc(g.average_5)+"/5")).join(" · ")}</p>
      <div class="matrix-wrap"><table class="matrix score-matrix"><thead><tr><th>항목</th><th>점수</th><th>판정</th><th>이유</th></tr></thead>
      <tbody>${card.criteria.map(c=>`<tr><td>${esc(c.criterion_id)} · ${esc(c.title)}<br><small>담당: ${esc(methods[c.evaluation_method]||"미기록")}</small></td><td>${c.score==null?(c.verdict==="NOT_APPLICABLE"?"해당 없음":"미평가"):esc(c.score)+" / 5"}</td><td>${esc(label(c.verdict))}</td><td>${esc(c.reason)}</td></tr>`).join("")}</tbody></table></div></div>`;
  }
  async function load(){
    const current=++ticket; alert(); el("flowBody").innerHTML="";
    try {
      const d=await fetchJson("/api/full-flows/"+encodeURIComponent(el("flowSelect").value));
      if(current!==ticket)return;
      const mode=d.mode==="TEST_DOUBLE" ? "무과금 연결 검증 · 실제 모델 성능 아님" : "실제 모델 파일럿 · 사람 교정 전";
      el("flowBody").innerHTML=`<div class="self-intro"><div><h2>${esc(mode)}</h2><p>예정 ${esc(d.planned_trials)}회 · ${esc(d.status==="COMPLETED"?"기록 수집 종료":label(d.status))} · 품질 판정은 검토 필요</p></div><span class="muted small">외부 API 시도 ${esc(d.api_request_attempts??"집계 중")}회</span></div>
      ${d.stop_reason?`<p class="status review">중단 이유: ${esc(stopReasons[d.stop_reason]||label(d.stop_reason))}</p>`:""}
      <p>${d.score_comparison.comparable?`평균 ${esc(d.score_comparison.mean)}점 · 최저 ${esc(d.score_comparison.minimum)}점 · `:""}${esc(d.score_comparison.note)}</p>
      <div class="matrix-wrap" role="region" aria-label="전체 실행 역할별 현황" tabindex="0"><table class="matrix"><thead><tr><th>회차</th><th>Agent 1</th><th>Agent 2</th><th>Agent 3</th><th>Agent 4</th><th>프로젝트1 실행 상태</th></tr></thead><tbody>${d.trials.map(t=>`<tr><td>${esc(t.trial)}회차</td>${["A1","A2","A3","A4"].map(r=>`<td>${esc(label(t.roles[r]))}</td>`).join("")}<td>${esc(label(t.status))}</td></tr>`).join("")}</tbody></table></div>
      ${d.trials.map(t=>`<details class="scope-details"><summary>${esc(t.trial)}회차 · 검사 근거와 평가 상태</summary><div class="evidence-rows">${t.new_plan_not_required?"<p>기존 TC 재사용: 신규 계획 생성은 해당 없음. 기존 테스트 실행은 별도로 확인합니다.</p>":""}
      ${t.checks.map(c=>`<div class="evidence-row"><code>${esc(c.rule)}</code><span>${esc(label(c.status))}</span><p>${esc(c.reason)}</p></div>`).join("")}
      ${t.reason?`<p>${esc(t.reason)}</p>`:""}
      ${(t.questions||[]).map(q=>`<p>확인 질문: ${esc(q)}</p>`).join("")}
      ${Object.entries(t.judges).map(([r,j])=>`<p>${esc(r)} 평가: ${esc(label(j.status))} · ${esc(label(j.reason))}</p>`).join("")}</div>${scores(t.scorecard)}</details>`).join("")}`;
    } catch(error){if(current===ticket)alert(error.message);}
  }
  async function list(){
    el("flowRefresh").disabled=true; el("flowSelect").disabled=true; el("flowBody").innerHTML=""; alert();
    try {const rows=await fetchJson("/api/full-flows");
      el("flowSelect").innerHTML=rows.map(r=>`<option value="${esc(r.id)}">${esc(r.id)}</option>`).join("");
      if(!rows.length){el("flowSelect").innerHTML="<option>아직 기록 없음</option>";el("flowBody").innerHTML='<div class="empty-state"><h2>전체 실행 기록이 없습니다</h2><p>실제 호출 전 Preview와 예산 승인이 필요합니다.</p></div>';return;}
      el("flowSelect").disabled=false;await load();
    }catch(error){alert(error.message);}finally{el("flowRefresh").disabled=false;}
  }
  el("flowSelect").addEventListener("change",load);el("flowRefresh").addEventListener("click",list);list();
})();
