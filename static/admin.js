"use strict";

const loginView = document.querySelector("#loginView");
const dashboardView = document.querySelector("#dashboardView");
const loginForm = document.querySelector("#loginForm");
const loginMessage = document.querySelector("#loginMessage");
const adminToken = document.querySelector("#adminToken");
const sessionList = document.querySelector("#sessionList");
const sessionSearch = document.querySelector("#sessionSearch");
const statusFilter = document.querySelector("#statusFilter");
const resultCount = document.querySelector("#resultCount");
const emptyDetail = document.querySelector("#emptyDetail");
const detailView = document.querySelector("#detailView");
const detailTitle = document.querySelector("#detailTitle");
const detailSubtitle = document.querySelector("#detailSubtitle");
const detailStatus = document.querySelector("#detailStatus");
const tabContent = document.querySelector("#tabContent");
const inspectorTitle = document.querySelector("#inspectorTitle");
const inspectorMeta = document.querySelector("#inspectorMeta");
const inspectorContent = document.querySelector("#inspectorContent");
const syncState = document.querySelector("#syncState");
const toast = document.querySelector("#toast");

const state = {
  sessions: [],
  selectedSessionId: null,
  detail: null,
  events: null,
  files: null,
  fileTotal: 0,
  filesHaveMore: false,
  tab: "overview",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 1800);
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function formatDate(value, compact = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, compact
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { dateStyle: "medium", timeStyle: "medium" }).format(date);
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function sessionState(session) {
  if (session.passed) return "passed";
  return session.status || "unknown";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = { ok: false, error: { message: `Request failed (${response.status}).` } };
  }
  if (!response.ok) {
    const error = new Error(payload.error?.message || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function showLogin(message = "") {
  dashboardView.hidden = true;
  loginView.hidden = false;
  loginMessage.textContent = message;
  window.setTimeout(() => adminToken.focus(), 0);
}

function showDashboard() {
  loginView.hidden = true;
  dashboardView.hidden = false;
  loginMessage.textContent = "";
  adminToken.value = "";
}

function setInspector(title, content, meta = "") {
  inspectorTitle.textContent = title;
  inspectorMeta.textContent = meta;
  inspectorContent.textContent = typeof content === "string" ? content : JSON.stringify(content, null, 2);
}

async function loadFile(path) {
  if (!state.selectedSessionId) return;
  setInspector(path, "Loading…");
  try {
    const file = await api(`/api/admin/sessions/${encodeURIComponent(state.selectedSessionId)}/file?path=${encodeURIComponent(path)}`);
    setInspector(file.path, file.content, `${formatBytes(file.size)}${file.truncated ? " · preview truncated" : ""}`);
  } catch (error) {
    setInspector(path, error.message);
  }
}

function renderMetrics(summary) {
  document.querySelector("#sessionMetric").textContent = formatNumber(summary.session_count);
  document.querySelector("#activeMetric").textContent = formatNumber(summary.active_count);
  document.querySelector("#passedMetric").textContent = formatNumber(summary.passed_count);
  document.querySelector("#eventMetric").textContent = formatNumber(summary.event_count);
  document.querySelector("#compileMetric").textContent = formatNumber(summary.execution_count);
}

function filteredSessions() {
  const query = sessionSearch.value.trim().toLowerCase();
  const status = statusFilter.value;
  return state.sessions.filter((session) => {
    const matchesStatus = status === "all" || sessionState(session) === status;
    const haystack = [
      session.session_id,
      session.participant_id,
      session.prolific_pid,
      session.study_id,
      session.task_id,
    ].filter(Boolean).join(" ").toLowerCase();
    return matchesStatus && (!query || haystack.includes(query));
  });
}

function renderSessions() {
  const sessions = filteredSessions();
  sessionList.replaceChildren();
  resultCount.textContent = `${sessions.length} record${sessions.length === 1 ? "" : "s"}`;
  if (!sessions.length) {
    sessionList.append(element("p", "list-message", "No sessions match these filters."));
    return;
  }
  for (const session of sessions) {
    const button = element("button", "session-card");
    button.type = "button";
    if (session.session_id === state.selectedSessionId) button.classList.add("active");

    const top = element("div", "session-card-top");
    top.append(
      element("strong", "", session.prolific_pid || session.participant_id || "Anonymous participant"),
      element("time", "", formatDate(session.started_at, true)),
    );
    const id = element("div", "session-id", session.session_id);
    const stats = element("div", "session-stats");
    stats.append(
      element("span", "", `${formatNumber(session.event_count)} events`),
      element("span", "", `${formatNumber(session.execution_count)} runs`),
      element("span", "", `${formatNumber(session.submission_count)} submits`),
    );
    const status = element("span", `mini-state ${sessionState(session)}`, sessionState(session));
    stats.append(status);
    button.append(top, id, stats);
    button.addEventListener("click", () => selectSession(session.session_id));
    sessionList.append(button);
  }
}

function keyValue(label, value) {
  const item = element("div", "key-value");
  item.append(element("span", "", label), element("strong", "", value ?? "—"));
  return item;
}

function sectionHeader(title, copy) {
  const section = element("div", "section-copy");
  section.append(element("h3", "", title), element("p", "", copy));
  return section;
}

function recordRow(index, title, subtitle, meta, metaClass = "") {
  const row = element("button", "record-row");
  row.type = "button";
  row.append(element("span", "record-index", index));
  const main = element("span", "record-main");
  main.append(element("strong", "", title), element("span", "", subtitle));
  row.append(main, element("span", `record-meta ${metaClass}`, meta));
  return row;
}

function renderOverview() {
  const { manifest, event_type_counts: eventTypes, trace_integrity: integrity } = state.detail;
  tabContent.append(sectionHeader("Session summary", "Identity, recruitment context, collection state, and stored data volume."));
  const grid = element("div", "key-grid");
  const recruitment = manifest.recruitment || {};
  [
    ["Participant", manifest.participant_id],
    ["Prolific PID", recruitment.prolific_pid],
    ["Study ID", recruitment.study_id],
    ["Prolific session", recruitment.prolific_session_id],
    ["Task", manifest.task_id],
    ["Started", formatDate(manifest.started_at)],
    ["Ended", formatDate(manifest.ended_at)],
    ["Replay steps", formatNumber(manifest.observation_count)],
    ["Trace integrity", integrity?.recovery_needed ? "Checkpoint recovered" : "Raw event stream complete"],
    ["Recovered checkpoints", formatNumber(integrity?.recovered_checkpoint_count)],
  ].forEach(([label, value]) => grid.append(keyValue(label, value)));
  tabContent.append(grid, sectionHeader("Captured event types", "Counts are calculated from the immutable raw event batches."));
  const cloud = element("div", "type-cloud");
  for (const [type, count] of Object.entries(eventTypes)) {
    cloud.append(element("span", "type-pill", `${type} · ${count}`));
  }
  if (!cloud.childElementCount) cloud.append(element("span", "list-message", "No events recorded."));
  tabContent.append(cloud);
  setInspector("Session manifest", manifest, `schema v${manifest.schema_version || 1}`);
}

async function renderTimeline() {
  tabContent.append(sectionHeader("Event timeline", "Ordered browser interactions, edits, guide views, compile actions, and submission actions."));
  if (!state.events) {
    tabContent.append(element("p", "list-message", "Loading raw events…"));
    try {
      const payload = await api(`/api/admin/sessions/${encodeURIComponent(state.selectedSessionId)}/events?limit=10000`);
      state.events = payload.events;
      if (state.tab === "timeline") renderCurrentTab();
    } catch (error) {
      tabContent.replaceChildren(element("p", "list-message", error.message));
    }
    return;
  }
  if (!state.events.length) {
    tabContent.append(element("p", "list-message", "No events recorded."));
    return;
  }
  state.events.forEach((event) => {
    const row = recordRow(`#${event.seq}`, event.type || "unknown", formatDate(event.client_timestamp), `${event.elapsed_ms ?? "—"} ms`);
    row.addEventListener("click", () => setInspector(`Event #${event.seq}`, event, event.type || "unknown"));
    tabContent.append(row);
  });
  setInspector("Raw event stream", state.events, `${state.events.length} events`);
}

function renderSteps() {
  const integrity = state.detail.trace_integrity || {};
  const steps = state.detail.recovered_trajectory || state.detail.observations;
  const title = integrity.recovery_needed ? "Recovered trajectory" : "Replayable observations";
  const copy = integrity.recovery_needed
    ? `Raw logging has gaps. ${formatNumber(integrity.recovered_checkpoint_count)} exact compile-time source checkpoints fill the missing interval; missing keystrokes are not inferred.`
    : "Every source-changing action is a full code snapshot; task, tutorial, and test actions are marker steps.";
  tabContent.append(sectionHeader(title, copy));
  if (!steps.length) {
    tabContent.append(element("p", "list-message", "No replay steps recorded."));
    return;
  }
  steps.forEach((step) => {
    const recovered = step.provenance === "execution_checkpoint";
    const row = recordRow(
      `S${step.trajectoryStep ?? step.step}`,
      step.primaryLabel,
      step.sourcePath || step.file,
      recovered ? "recovered checkpoint" : `event #${step.eventSeq}`,
      recovered ? "recovered" : "",
    );
    row.addEventListener("click", () => loadFile(step.sourcePath || `delta-observations/${step.file}`));
    tabContent.append(row);
  });
  loadFile(steps[steps.length - 1].sourcePath || `delta-observations/${steps[steps.length - 1].file}`);
}

function renderCompiles() {
  const executions = state.detail.executions;
  tabContent.append(sectionHeader("Compiler attempts", "Each attempt retains its exact source, result, diagnostics, stdout, stderr, and generated artifacts."));
  if (!executions.length) {
    tabContent.append(element("p", "list-message", "No compiler attempts recorded."));
    return;
  }
  executions.forEach((execution, position) => {
    const success = execution.status === "succeeded";
    const row = recordRow(
      `R${executions.length - position}`,
      execution.status || "unknown",
      formatDate(execution.requested_at),
      execution.duration_ms == null ? "—" : `${execution.duration_ms} ms`,
      success ? "success" : "error",
    );
    row.addEventListener("click", () => {
      const base = execution.result_path.slice(0, execution.result_path.lastIndexOf("/"));
      tabContent.querySelectorAll(".record-row").forEach((item) => item.classList.remove("active"));
      tabContent.querySelectorAll(".artifact-cloud").forEach((item) => item.remove());
      row.classList.add("active");
      setInspector("Compile result", execution, execution.execution_id);
      const artifacts = element("div", "type-cloud artifact-cloud");
      for (const [name, path] of Object.entries(execution.artifacts || {})) {
        const button = element("button", "type-pill", name);
        button.type = "button";
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          loadFile(`${base}/${path}`);
        });
        artifacts.append(button);
      }
      row.insertAdjacentElement("afterend", artifacts);
    });
    tabContent.append(row);
  });
  setInspector("Compiler attempts", executions, `${executions.length} runs`);
}

function renderSubmissions() {
  const submissions = state.detail.submissions;
  tabContent.append(sectionHeader("Answer submissions", "Checker outcomes and the execution associated with each submission."));
  if (!submissions.length) {
    tabContent.append(element("p", "list-message", "No submissions recorded."));
    return;
  }
  submissions.forEach((submission, position) => {
    const row = recordRow(
      `A${submissions.length - position}`,
      submission.passed ? "Accepted" : "Not accepted",
      formatDate(submission.submitted_at),
      submission.passed ? "passed" : "failed",
      submission.passed ? "success" : "error",
    );
    row.addEventListener("click", () => loadFile(submission.path));
    tabContent.append(row);
  });
  setInspector("Submissions", submissions, `${submissions.length} attempts`);
}

async function loadFiles(offset = 0) {
  const payload = await api(
    `/api/admin/sessions/${encodeURIComponent(state.selectedSessionId)}/files?limit=500&offset=${offset}`,
  );
  state.files = offset === 0 ? payload.files : [...state.files, ...payload.files];
  state.fileTotal = payload.total;
  state.filesHaveMore = payload.has_more;
}

async function renderFiles() {
  tabContent.append(sectionHeader("Stored files", "Raw JSON, JSONL, source, knitout, diagnostics, and derived replay files for this session."));
  if (!state.files) {
    tabContent.append(element("p", "list-message", "Loading raw file index…"));
    try {
      await loadFiles();
      if (state.tab === "files") renderCurrentTab();
    } catch (error) {
      tabContent.replaceChildren(element("p", "list-message", error.message));
    }
    return;
  }
  state.files.forEach((file, position) => {
    const row = recordRow(String(position + 1).padStart(2, "0"), file.path, formatDate(file.updated_at), formatBytes(file.size));
    row.addEventListener("click", () => loadFile(file.path));
    tabContent.append(row);
  });
  if (state.filesHaveMore) {
    const loadMore = element("button", "secondary-button", `Load more (${state.files.length} of ${state.fileTotal})`);
    loadMore.type = "button";
    loadMore.addEventListener("click", async () => {
      loadMore.disabled = true;
      await loadFiles(state.files.length);
      renderCurrentTab();
    });
    const wrapper = element("div", "section-copy");
    wrapper.append(loadMore);
    tabContent.append(wrapper);
  }
  setInspector("Raw file index", state.files, `${state.fileTotal} files`);
}

function renderCurrentTab() {
  if (!state.detail) return;
  tabContent.replaceChildren();
  document.querySelectorAll(".detail-tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  });
  if (state.tab === "overview") renderOverview();
  if (state.tab === "timeline") renderTimeline();
  if (state.tab === "steps") renderSteps();
  if (state.tab === "compiles") renderCompiles();
  if (state.tab === "submissions") renderSubmissions();
  if (state.tab === "files") renderFiles();
}

async function selectSession(sessionId) {
  state.selectedSessionId = sessionId;
  state.detail = null;
  state.events = null;
  state.files = null;
  state.fileTotal = 0;
  state.filesHaveMore = false;
  renderSessions();
  emptyDetail.hidden = true;
  detailView.hidden = false;
  detailTitle.textContent = "Loading session…";
  detailSubtitle.textContent = sessionId;
  tabContent.replaceChildren(element("p", "list-message", "Preparing trace view…"));
  setInspector("Loading", "");
  try {
    state.detail = await api(`/api/admin/sessions/${encodeURIComponent(sessionId)}`);
    const manifest = state.detail.manifest;
    const recruitment = manifest.recruitment || {};
    detailTitle.textContent = recruitment.prolific_pid || manifest.participant_id || "Participant session";
    detailSubtitle.textContent = `${manifest.session_id} · ${formatDate(manifest.started_at)}`;
    detailStatus.textContent = manifest.passed_submission_id ? "passed" : (manifest.status || "unknown");
    detailStatus.className = `status-badge ${manifest.passed_submission_id ? "passed" : (manifest.status || "")}`;
    renderCurrentTab();
  } catch (error) {
    tabContent.replaceChildren(element("p", "list-message", error.message));
    if (error.status === 401) showLogin("Your administrator session expired. Sign in again.");
  }
}

async function loadSessions({ preserveSelection = false } = {}) {
  syncState.textContent = "Refreshing stored traces…";
  try {
    const payload = await api("/api/admin/sessions");
    state.sessions = payload.sessions;
    renderMetrics(payload.summary);
    renderSessions();
    showDashboard();
    syncState.textContent = `Updated ${new Intl.DateTimeFormat(undefined, { timeStyle: "medium" }).format(new Date())}`;
    if (preserveSelection && state.selectedSessionId && state.sessions.some((item) => item.session_id === state.selectedSessionId)) {
      await selectSession(state.selectedSessionId);
    }
  } catch (error) {
    if (error.status === 401) showLogin();
    else if (error.status === 503) showLogin("Set TRACE_ADMIN_TOKEN before using this dashboard.");
    else showLogin(error.message);
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginMessage.textContent = "Checking key…";
  const submitButton = loginForm.querySelector("button");
  submitButton.disabled = true;
  try {
    await api("/api/admin/login", { method: "POST", body: JSON.stringify({ token: adminToken.value }) });
    await loadSessions();
  } catch (error) {
    loginMessage.textContent = error.message;
    adminToken.select();
  } finally {
    submitButton.disabled = false;
  }
});

document.querySelector("#logoutButton").addEventListener("click", async () => {
  try { await api("/api/admin/logout", { method: "POST" }); } catch (_error) {}
  state.sessions = [];
  state.selectedSessionId = null;
  state.detail = null;
  showLogin("You have been logged out.");
});

document.querySelector("#refreshButton").addEventListener("click", () => loadSessions({ preserveSelection: true }));
document.querySelector("#copySessionButton").addEventListener("click", async () => {
  if (!state.selectedSessionId) return;
  await navigator.clipboard.writeText(state.selectedSessionId);
  showToast("Session ID copied");
});
sessionSearch.addEventListener("input", renderSessions);
statusFilter.addEventListener("change", renderSessions);
document.querySelectorAll(".detail-tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    renderCurrentTab();
  });
});

loadSessions();
