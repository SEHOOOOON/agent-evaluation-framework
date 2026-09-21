(() => {
  "use strict";
  const el = id => document.getElementById(id);
  const html = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const labels = {CHECKED:"자동 확인", PARTIAL:"부분 확인", NOT_EVALUATED:"미평가", FAIL:"위반 발견", ERROR:"평가 오류", REVIEW:"검토 필요", PASS:"검사 통과"};
  const roleInfo = {
    A1:["Agent 1 · 요구사항 분석", "요청한 조건·수치·범위와 분석 근거를 확인합니다."],
    A2:["Agent 2 · TC 설계", "재사용 판단과 조작·기대결과·복원을 확인합니다."],
    A3:["Agent 3 · 계획·코드·실제 시험", "TC에서 실제 관찰까지의 연결과 복원 증거를 확인합니다."],
    A4:["Agent 4 · 결과 분석·보고", "규칙 기반 집계·분류와 보고 근거를 확인합니다."],
    E2E:["전체 연결 · 인계·범위·승인", "미정 조건·실패·질문이 끝까지 전달됐는지 확인합니다."],
    COMMON:["공통 · 입력·출력과 권한", "자료 무결성과 역할·승인 경계를 확인합니다."],
  };
  const matrixRoles = ["A1","A2","A3","A4","E2E"];
  const view = {report:null, caseId:null, role:"A1", request:0};
  const badge = status => `<span class="status ${Object.hasOwn(labels,status) ? status.toLowerCase() : "pending"}">${html(labels[status] || "미평가")}</span>`;
  async function get(url) {
    const response = await fetch(url, {cache:"no-store"});
    if (!response.ok) throw new Error("저장 평가를 읽을 수 없습니다. 파일 형식·연결·집계를 확인해 주세요.");
    return response.json();
  }
  function alert(message="") { el("reviewAlert").hidden=!message; el("reviewAlert").textContent=message; }
  function selectedCase() { return view.report?.cases.find(row=>row.id===view.caseId); }
  function renderMatrix() {
    el("reviewMatrix").innerHTML=view.report.cases.map(row=>`<tr class="${row.id===view.caseId ? "selected" : ""}">
      <td><button class="case-name" data-review-case="${html(row.id)}">${html(row.title)}<span>${html(row.id)} · ${row.oracle_status === "DRAFT" ? "정답 초안" : "정답 상태: "+html(row.oracle_status)}</span></button></td>
      ${matrixRoles.map(role=>`<td><button class="status ${row.groups[role].status.toLowerCase()}" data-review-case="${html(row.id)}" data-review-role="${role}" aria-pressed="${row.id===view.caseId && role===view.role}" aria-label="${html(row.title)} · ${html(roleInfo[role][0])} · ${html(labels[row.groups[role].status])}">${html(labels[row.groups[role].status])}</button></td>`).join("")}
      <td>${badge(row.status)}</td></tr>`).join("");
    el("reviewMatrix").querySelectorAll("[data-review-case]").forEach(button=>button.addEventListener("click",()=>{
      view.caseId=button.dataset.reviewCase;
      if(button.dataset.reviewRole) view.role=button.dataset.reviewRole;
      const caseId=view.caseId, role=button.dataset.reviewRole;
      renderMatrix(); renderDetail();
      [...el("reviewMatrix").querySelectorAll("[data-review-case]")].find(node=>node.dataset.reviewCase===caseId && node.dataset.reviewRole===role)?.focus({preventScroll:true});
    }));
  }
  function renderDetail() {
    const row=selectedCase();
    if(!row) return;
    el("reviewCaseId").textContent=`${row.id} · 저장 사례 상세`;
    el("inspectionTitle").textContent=row.title;
    el("reviewRunId").textContent=`원본 실행 ${row.run_id || "미기록"}`;
    el("reviewCaseStatus").className=`status ${row.status.toLowerCase()}`;
    el("reviewCaseStatus").textContent=labels[row.status];
    const issues=row.checks.filter(check=>["FAIL","ERROR"].includes(check.status));
    el("reviewIssues").innerHTML=issues.length ? issues.map(check=>`<div class="issue"><span class="issue-mark" aria-hidden="true">!</span><div><h3>${html(labels[check.status])} · ${html(check.rule)}</h3><p>${html(check.reason || "저장된 검사 이유가 없습니다.")} · 원인 책임은 별도 확인이 필요합니다.</p></div></div>`).join("") : '<p class="no-issue">자동 검사에서 발견된 위반은 없습니다. 미평가·의미 검토 항목은 아래에서 확인하세요.</p>';
    el("reviewRoles").innerHTML=Object.keys(roleInfo).map(role=>`<button data-role="${role}" aria-pressed="${view.role===role}">${role.startsWith("A") ? "Agent "+role.slice(1) : role==="E2E" ? "전체 연결" : "공통"}</button>`).join("");
    el("reviewRoles").querySelectorAll("button").forEach(button=>button.addEventListener("click",()=>{
      view.role=button.dataset.role; renderMatrix(); renderDetail();
      el("reviewRoles").querySelector(`[data-role="${view.role}"]`).focus({preventScroll:true});
    }));
    el("reviewRoleTitle").textContent=roleInfo[view.role][0];
    el("reviewRoleDescription").textContent=roleInfo[view.role][1];
    const group=row.groups[view.role];
    el("reviewRoleCount").textContent=group.items.length ? `${group.items.length}개 기준 · ${labels[group.status]}` : "평가 기록 없음";
    el("reviewCriteria").innerHTML=group.items.length ? group.items.map(item=>{
      const evidence=row.checks.filter(check=>item.evidence_rules.includes(check.rule));
      return `<details class="criterion"><summary><span class="criterion-code">${html(item.id)}</span><span class="criterion-name">${html(item.title)}</span>${badge(item.status)}</summary>
        <div class="evidence-rows">${evidence.length ? evidence.map(check=>`<div class="evidence-row"><code>${html(check.rule)}</code>${badge(check.status)}<p>${html(check.reason)}</p></div>`).join("") : '<p class="muted small">연결된 자동 검사 근거가 없습니다. 판정을 채우지 않고 미평가 범위로 남깁니다.</p>'}</div></details>`;
    }).join("") : '<p class="no-issue">이 저장 평가에는 해당 역할의 기준 기록이 없습니다. 통과로 처리하지 않습니다.</p>';
  }
  async function loadReport(id) {
    const ticket=++view.request;
    view.report=null; alert(); el("reviewContent").hidden=true; el("reviewEmpty").hidden=true;
    el("recordedPanel").setAttribute("aria-busy","true");
    try {
      const report=await get(`/api/recorded-reviews/${encodeURIComponent(id)}`);
      if(ticket!==view.request) return;
      view.report=report;
      const first=report.cases.find(row=>row.status==="FAIL" || row.status==="ERROR") || report.cases[0];
      view.caseId=first.id;
      view.role=matrixRoles.find(role=>["FAIL","ERROR"].includes(first.groups[role].status)) || "A1";
      el("reviewCaseCount").textContent=report.cases.length;
      el("reviewFailCount").textContent=report.counts.FAIL || 0;
      el("reviewPendingCount").textContent=report.counts.REVIEW || 0;
      el("reviewErrorCount").textContent=report.counts.ERROR || 0;
      el("reviewSourceState").textContent=report.source_unchanged_at_review ? "저장 평가 당시 원본 해시 유지 확인" : "저장 평가 당시 원본 보존 확인 없음";
      renderMatrix(); renderDetail(); el("reviewContent").hidden=false;
    } catch(error) { if(ticket===view.request) alert(error.message); }
    finally { if(ticket===view.request) el("recordedPanel").setAttribute("aria-busy","false"); }
  }
  async function loadList() {
    const ticket=++view.request, previous=el("reviewSelect").value;
    el("reviewRefresh").disabled=true; el("reviewSelect").disabled=true;
    el("reviewContent").hidden=true; el("reviewEmpty").hidden=true; alert();
    try {
      const result=await get("/api/recorded-reviews");
      if(ticket!==view.request) return;
      el("reviewSelect").innerHTML=result.reports.map(row=>`<option value="${html(row.id)}">${html(row.id.replace("V2-EVAL-",""))}</option>`).join("");
      if(!result.reports.length) {
        el("reviewSelect").innerHTML='<option>저장 기록 없음</option>'; el("reviewEmpty").hidden=false; view.report=null; return;
      }
      if(result.reports.some(row=>row.id===previous)) el("reviewSelect").value=previous;
      el("reviewSelect").disabled=false;
      await loadReport(el("reviewSelect").value);
      if(result.truncated) alert("최근 기록 100개만 표시합니다.");
    } catch(error) { if(ticket===view.request) alert(error.message); }
    finally { el("reviewRefresh").disabled=false; }
  }
  const modes=[el("mode-recorded"),el("mode-flow"),el("mode-self")];
  function activateMode(button) {
    modes.forEach(node=>{const active=node===button; node.setAttribute("aria-selected",String(active)); node.tabIndex=active ? 0 : -1; el(node.getAttribute("aria-controls")).hidden=!active;});
  }
  modes.forEach((button,index)=>{
    button.addEventListener("click",()=>activateMode(button));
    button.addEventListener("keydown",event=>{
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault(); const next=modes[event.key==="Home" ? 0 : event.key==="End" ? modes.length-1 : (index+(event.key==="ArrowRight" ? 1 : -1)+modes.length)%modes.length]; activateMode(next); next.focus();
    });
  });
  el("reviewSelect").addEventListener("change",()=>loadReport(el("reviewSelect").value));
  el("reviewRefresh").addEventListener("click",loadList);
  loadList();
})();
