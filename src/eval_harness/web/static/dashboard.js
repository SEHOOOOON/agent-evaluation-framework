const stageCatalog = [
  { id: "agent_1", step: "01", label: "A1 요구사항 규칙" },
  { id: "agent_2", step: "02", label: "A2 TC 규칙" },
  { id: "agent_3", step: "03", label: "A3 실행 규칙" },
  { id: "agent_4", step: "04", label: "A4 보고 규칙" },
  { id: "pipeline_gate", step: "05", label: "Evaluator Gate" },
];

const allowedStatuses = new Set([
  "IDLE", "RUNNING", "PASS", "FAIL", "REVIEW", "ERROR", "BLOCKED",
  "ABSTAIN", "NOT_APPLICABLE", "HOLD", "HUMAN_REVIEW", "PENDING", "COMPLETED",
]);
const allowedSeverities = new Set(["critical", "high", "medium", "low", "info"]);
const terminalEvents = new Set(["run_completed", "run_failed"]);
const state = {
  manifest: null,
  selectedStage: "agent_1",
  selectedTab: "checks",
  events: [],
  eventSource: null,
  dialogOpener: null,
  benchmarks: [],
};

const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
const cssStatus = (value) => {
  const normalized = String(value || "IDLE").toUpperCase();
  return (allowedStatuses.has(normalized) ? normalized : "IDLE").toLowerCase();
};
const cssSeverity = (value) => {
  const normalized = String(value || "info").toLowerCase();
  return allowedSeverities.has(normalized) ? normalized : "info";
};
const boolText = (value) => value === true ? "TRUE" : value === false ? "FALSE" : "PENDING";

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) { /* not json */ }
    throw new Error(message);
  }
  return response.json();
}

function setAppAlert(message = "") {
  byId("appAlert").textContent = message;
  byId("appAlert").classList.toggle("hidden", !message);
}

function renderDesign(design, selfTest) {
  // Self-test metadata is kept separate from the recorded V2 review.
  byId("sourceName").textContent += ` · 개발 사례 ${design.case_count}개`;
}

function setStatus(element, value) {
  element.textContent = value;
  element.className = `status ${cssStatus(value)}`;
}

function renderStepper() {
  const stages = new Map((state.manifest?.stages || []).map((stage) => [stage.stage_id, stage]));
  byId("stepper").innerHTML = stageCatalog.map((item) => {
    const status = stages.get(item.id)?.status || "IDLE";
    const active = state.selectedStage === item.id;
    return `<button class="step-button ${active ? "active" : ""}" data-stage="${item.id}"${active ? ' aria-current="step"' : ""}>
      <span class="mini-status ${cssStatus(status)}" aria-label="${escapeHtml(status)}"></span>
      <span class="number">STEP ${item.step}</span><span class="name">${escapeHtml(item.label)}</span>
    </button>`;
  }).join("");
  document.querySelectorAll("[data-stage]").forEach((button) => button.addEventListener("click", () => {
    state.selectedStage = button.dataset.stage;
    render();
  }));
}

function renderSummary() {
  const manifest = state.manifest;
  byId("runId").textContent = manifest?.run_id || "실행 전";
  setStatus(byId("runStatus"), manifest?.status || "IDLE");
  byId("passCount").textContent = manifest?.summary?.checks_passed ?? "—";
  byId("failCount").textContent = manifest?.summary?.checks_failed ?? "—";
  byId("abstainCount").textContent = manifest?.summary?.checks_abstained ?? "—";
  byId("naCount").textContent = manifest?.summary?.checks_not_applicable ?? "—";
  byId("findingCount").textContent = manifest?.summary?.total_findings ?? "—";
  byId("sutExecuted").textContent = boolText(manifest?.sut_executed ?? false);
  byId("stabilityEvaluated").textContent = boolText(manifest?.stability_evaluated ?? false);
  byId("sourceUnchanged").textContent = boolText(manifest?.source_unchanged);
  byId("gateDecision").textContent = manifest?.release_decision || "PENDING";
  byId("gateDecision").style.color = manifest?.release_decision === "PASS" ? "var(--green)" :
    manifest?.release_decision === "HOLD" ? "var(--red)" : "var(--amber)";
  const gateCopy = {
    PASS: "정상 기준본을 오탐 없이 통과했습니다.",
    HOLD: "주입되었거나 현재 Snapshot에 존재하는 중요 위반을 탐지했습니다.",
    HUMAN_REVIEW: "자동 판정 근거가 부족해 평가기 결과 검토가 필요합니다.",
  };
  byId("gateSummary").textContent = gateCopy[manifest?.release_decision]
    || "검증을 실행하면 평가기가 알려진 오류를 탐지하는지 표시합니다.";
  byId("runButton").disabled = manifest?.status === "RUNNING";
  byId("runButton").setAttribute("aria-busy", manifest?.status === "RUNNING" ? "true" : "false");
  const report = byId("reportLink");
  if (manifest?.report_available) {
    report.href = `/api/runs/${encodeURIComponent(manifest.run_id)}/report`;
    report.classList.remove("hidden");
  } else {
    report.classList.add("hidden");
  }
}

function artifactButtons(refs) {
  const unique = [...new Set((refs || []).filter(Boolean))];
  if (!unique.length) return "<p class='muted'>연결된 Artifact가 없습니다.</p>";
  return `<div class="artifact-list">${unique.map((ref) => `<div class="artifact-card">
    <div><p class="eyebrow">ARTIFACT</p><strong class="mono">${escapeHtml(ref)}</strong></div>
    <button data-artifact="${escapeHtml(ref)}">읽기 전용 보기</button></div>`).join("")}</div>`;
}

function renderChecks(stage) {
  if (!stage?.checks?.length) return "<p class='muted'>Rule Check 결과가 없습니다.</p>";
  return `<div class="check-list">${stage.checks.map((check) => `<div class="check-card">
    <header><div><p class="eyebrow">${escapeHtml(check.criterion_id)}</p><h3>${escapeHtml(check.title)}</h3></div>
    <span class="status ${cssStatus(check.status)}">${escapeHtml(check.status)}</span></header>
    <p>${escapeHtml(check.reason)}</p>
    <div class="metadata">severity=${escapeHtml(check.severity)}\nexpected=${escapeHtml(JSON.stringify(check.expected))}\nactual=${escapeHtml(JSON.stringify(check.actual))}</div>
  </div>`).join("")}</div>`;
}

function renderFindings(stage) {
  if (!stage?.findings?.length) return "<p class='muted'>등록된 Finding이 없습니다.</p>";
  return `<div class="finding-list">${stage.findings.map((finding) => `<div class="finding-card ${cssSeverity(finding.severity)}">
    <header><div><p class="eyebrow">${escapeHtml(finding.finding_id)}</p><h3>${escapeHtml(finding.title)}</h3></div>
    <span class="severity">${escapeHtml(finding.severity)}</span></header>
    <p>${escapeHtml(finding.reason)}</p><div class="metadata">권장 조치 · ${escapeHtml(finding.recommended_action)}</div>
  </div>`).join("")}</div>`;
}

function attachArtifactEvents() {
  document.querySelectorAll("[data-artifact]").forEach((button) => button.addEventListener("click", () => viewArtifact(button.dataset.artifact, button)));
}

function renderStage() {
  const catalog = stageCatalog.find((item) => item.id === state.selectedStage) || stageCatalog[0];
  const stage = state.manifest?.stages?.find((item) => item.stage_id === catalog.id);
  byId("stageStep").textContent = `STEP ${catalog.step}`;
  byId("stageName").textContent = stage?.agent_name || catalog.label;
  byId("stageMode").textContent = stage
    ? `${stage.agent_mode} · ${stage.component_type} · AGENT EXECUTED: ${boolText(stage.sut_executed)}`
    : "아직 실행되지 않은 단계입니다.";
  setStatus(byId("stageStatus"), stage?.status || "IDLE");
  byId("checkpointName").textContent = stage?.checkpoint_id || (catalog.id === "pipeline_gate" ? "Final Gate" : `Checkpoint ${Number(catalog.step)}`);
  byId("checkpointStatus").textContent = stage?.checkpoint_status || "PENDING";
  byId("modeNote").textContent = stage?.mode_note || "화면은 JSON 판정을 표시하며 자체적으로 결과를 계산하지 않습니다.";

  const detail = byId("detailContent");
  detail.classList.remove("empty-state");
  if (!stage) {
    detail.classList.add("empty-state");
    detail.innerHTML = "<p>이 단계의 실행 결과가 아직 없습니다.</p>";
    return;
  }
  if (state.selectedTab === "checks") detail.innerHTML = renderChecks(stage);
  if (state.selectedTab === "findings") detail.innerHTML = renderFindings(stage);
  if (state.selectedTab === "inputs") detail.innerHTML = artifactButtons([...(stage.input_refs || []), stage.raw_output_ref, stage.normalized_output_ref]);
  if (state.selectedTab === "evidence") {
    const refs = [
      ...(stage.checks || []).flatMap((check) => check.evidence_refs || []),
      ...(stage.findings || []).flatMap((finding) => finding.evidence_refs || []),
    ];
    detail.innerHTML = artifactButtons(refs);
  }
  attachArtifactEvents();
}

function renderTimeline() {
  const timeline = byId("timeline");
  if (!state.events.length) { timeline.innerHTML = "<li class='muted'>아직 기록이 없습니다.</li>"; return; }
  timeline.innerHTML = state.events.slice().reverse().map((event) => `<li><time>${escapeHtml(event.at || "")}</time> · ${escapeHtml(event.event)}${event.stage_id ? ` · ${escapeHtml(event.stage_id)}` : ""}${event.decision ? ` · ${escapeHtml(event.decision)}` : ""}</li>`).join("");
}

function renderTabs() {
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const active = button.dataset.tab === state.selectedTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
  byId("detailContent").setAttribute("aria-labelledby", `tab-${state.selectedTab}`);
}

function render() { renderSummary(); renderStepper(); renderStage(); renderTimeline(); renderTabs(); }

async function refreshRun(runId) {
  state.manifest = await requestJson(`/api/runs/${encodeURIComponent(runId)}`);
  render();
}

function followEvents(runId) {
  state.eventSource?.close();
  state.events = [];
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  state.eventSource = source;
  source.onmessage = async (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch (_) { return; }
    state.events.push(event);
    await refreshRun(runId);
    if (terminalEvents.has(event.event)) source.close();
  };
  source.onerror = async () => {
    source.close();
    try { await refreshRun(runId); } catch (_) { setAppAlert("실행 상태를 새로 고치지 못했습니다."); }
  };
}

async function startRun() {
  setAppAlert();
  byId("runButton").disabled = true;
  try {
    const payload = {
      suite: byId("suite").value,
      execution_mode: byId("mode").value,
      target_version: byId("targetVersion").value,
      trials: Number(byId("trials").value),
    };
    const result = await requestJson("/api/runs", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    state.selectedStage = "agent_1";
    await refreshRun(result.run_id);
    followEvents(result.run_id);
  } catch (error) {
    setAppAlert(`평가기 검증을 시작하지 못했습니다. ${error.message}`);
    byId("runButton").disabled = false;
  }
}

async function viewArtifact(artifactId, opener) {
  if (!state.manifest) return;
  const response = await fetch(`/api/runs/${encodeURIComponent(state.manifest.run_id)}/artifacts/${encodeURIComponent(artifactId)}`);
  if (!response.ok) { setAppAlert("Artifact를 불러오지 못했습니다."); return; }
  const text = await response.text();
  let formatted = text;
  if ((response.headers.get("content-type") || "").includes("json")) {
    try { formatted = JSON.stringify(JSON.parse(text), null, 2); } catch (_) { /* raw */ }
  }
  state.dialogOpener = opener || document.activeElement;
  byId("artifactTitle").textContent = artifactId;
  byId("artifactContent").textContent = formatted;
  byId("artifactDialog").showModal();
}

function activateTab(button) {
  state.selectedTab = button.dataset.tab;
  render();
  button.focus();
}

function renderBenchmarkDescription() {
  const benchmark = state.benchmarks.find((item) => item.id === byId("suite").value);
  byId("benchmarkDescription").textContent = benchmark?.description || "선택한 검증 Profile 설명이 없습니다.";
}

async function initialize() {
  const [config, benchmarks, design, selfTest] = await Promise.all([
    requestJson("/api/config"),
    requestJson("/api/benchmarks"),
    requestJson("/api/evaluation-design"),
    requestJson("/api/evaluator-self-test"),
  ]);
  state.benchmarks = benchmarks;
  byId("suite").innerHTML = benchmarks.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
  byId("sourceName").textContent = `source: ${config.source_name}`;
  renderDesign(design, selfTest);
  const latest = await requestJson("/api/runs/latest");
  if (latest.run) {
    state.manifest = latest.run;
    state.selectedStage = latest.run.stages?.[0]?.stage_id || "agent_1";
    followEvents(latest.run.run_id);
  }
  renderBenchmarkDescription();
  render();
}

byId("suite").addEventListener("change", () => {
  renderBenchmarkDescription();
  byId("targetVersion").value = byId("suite").value === "known-good" ? "known-good-v1" : "project1-current";
});
byId("runButton").addEventListener("click", startRun);
byId("closeDialog").addEventListener("click", () => byId("artifactDialog").close());
byId("artifactDialog").addEventListener("close", () => state.dialogOpener?.focus());
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => activateTab(button));
  button.addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll("[data-tab]")];
    const current = tabs.indexOf(button);
    let next = null;
    if (event.key === "ArrowRight") next = tabs[(current + 1) % tabs.length];
    if (event.key === "ArrowLeft") next = tabs[(current - 1 + tabs.length) % tabs.length];
    if (event.key === "Home") next = tabs[0];
    if (event.key === "End") next = tabs[tabs.length - 1];
    if (next) { event.preventDefault(); activateTab(next); }
  });
});
initialize().catch((error) => {
  byId("sourceName").textContent = "source: 초기화 실패";
  byId("runButton").disabled = true;
  setAppAlert(`대시보드를 초기화하지 못했습니다. ${error.message}`);
});
