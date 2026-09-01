const TASK_ID = "stockinette-swatch-v1";
const queryParameters = new URLSearchParams(window.location.search);
const prolificRecruitment = {
  source: "prolific",
  prolific_pid: queryParameters.get("PROLIFIC_PID") || "",
  study_id: queryParameters.get("STUDY_ID") || "",
  prolific_session_id: queryParameters.get("SESSION_ID") || "",
};
const hasProlificParticipant = Boolean(prolificRecruitment.prolific_pid);
const sourceStorageScope = prolificRecruitment.prolific_session_id || prolificRecruitment.prolific_pid || "direct";
const SOURCE_STORAGE_KEY = `knitscript-studio-source:${TASK_ID}:from-scratch-v1:${sourceStorageScope}`;
const STARTER_SOURCE = "";

const editor = document.querySelector("#sourceEditor");
const lineNumbers = document.querySelector("#lineNumbers");
const cursorPosition = document.querySelector("#cursorPosition");
const saveState = document.querySelector("#saveState");
const runButton = document.querySelector("#runButton");
const submitButton = document.querySelector("#submitButton");
const resetButton = document.querySelector("#resetButton");
const compilerState = document.querySelector("#compilerState");
const studyState = document.querySelector("#studyState");
const emptyState = document.querySelector("#emptyState");
const resultContent = document.querySelector("#resultContent");
const runSummary = document.querySelector("#runSummary");
const testOutput = document.querySelector("#testOutput");
const consoleOutput = document.querySelector("#consoleOutput");
const knitoutOutput = document.querySelector("#knitoutOutput");
const copyButton = document.querySelector("#copyButton");
const toast = document.querySelector("#toast");
const completionDialog = document.querySelector("#completionDialog");
const completionMessage = document.querySelector("#completionMessage");
const closeCompletionButton = document.querySelector("#closeCompletionButton");
const prolificCompletionLink = document.querySelector("#prolificCompletionLink");
const tabs = [...document.querySelectorAll(".tab")];
const guideTabs = [...document.querySelectorAll(".guide-tab")];
const guideViews = [...document.querySelectorAll(".guide-view")];
const documentationLink = document.querySelector("#documentationLink");

let activeTab = "tests";
let saveTimer;
let toastTimer;
let previousSource = "";
let telemetrySessionId = null;
let telemetrySeq = 0;
let telemetryFlush = Promise.resolve();
let telemetryInFlightBatch = null;
let sessionReady;
const pendingEvents = [];
const telemetryStartedAt = performance.now();
const participantId = prolificRecruitment.prolific_pid || getPersistentId("knitscript-participant-id");
const clientInstanceId = crypto.randomUUID();

function getPersistentId(key) {
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const value = crypto.randomUUID();
  localStorage.setItem(key, value);
  return value;
}

function recordEvent(type, payload = {}) {
  telemetrySeq += 1;
  pendingEvents.push({
    client_event_id: `${clientInstanceId}:${telemetrySeq}`,
    seq: telemetrySeq,
    type,
    client_timestamp: new Date().toISOString(),
    elapsed_ms: Math.round(performance.now() - telemetryStartedAt),
    payload,
  });
  if (pendingEvents.length >= 50) void flushEvents();
}

async function initializeTelemetrySession() {
  try {
    const response = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        participant_id: participantId,
        task_id: TASK_ID,
        initial_source: editor.value,
        recruitment: hasProlificParticipant ? prolificRecruitment : { source: "direct" },
        client: {
          client_instance_id: clientInstanceId,
          locale: navigator.language,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          viewport: { width: window.innerWidth, height: window.innerHeight },
        },
      }),
    });
    if (!response.ok) return;
    const body = await response.json();
    telemetrySessionId = body.session.session_id;
    recordEvent("session.started", { source_length: editor.value.length });
  } catch (_error) {
    // Compilation remains usable if telemetry storage is temporarily unavailable.
  }
}

async function flushEvents() {
  telemetryFlush = telemetryFlush.then(async () => {
    await sessionReady;
    if (!telemetrySessionId) return;
    while (pendingEvents.length > 0) {
      const events = pendingEvents.splice(0, 100);
      const batchId = crypto.randomUUID();
      telemetryInFlightBatch = { batch_id: batchId, events };
      try {
        const response = await fetch("/api/events", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: telemetrySessionId, batch_id: batchId, events }),
          keepalive: true,
        });
        if (!response.ok) throw new Error("Telemetry upload failed");
      } catch (_error) {
        pendingEvents.unshift(...events);
        return;
      } finally {
        telemetryInFlightBatch = null;
      }
    }
  });
  return telemetryFlush;
}

function describeEdit(before, after, inputType = "") {
  let start = 0;
  const shorterLength = Math.min(before.length, after.length);
  while (start < shorterLength && before[start] === after[start]) start += 1;

  let beforeEnd = before.length;
  let afterEnd = after.length;
  while (beforeEnd > start && afterEnd > start && before[beforeEnd - 1] === after[afterEnd - 1]) {
    beforeEnd -= 1;
    afterEnd -= 1;
  }

  const insertedText = after.slice(start, afterEnd);
  const deletedText = before.slice(start, beforeEnd);
  let operation = "replace";
  if (!deletedText) operation = "insert";
  if (!insertedText) operation = "delete";

  return {
    operation,
    origin: inputType || "unknown",
    offset_encoding: "utf-16",
    range_start: start,
    range_end: beforeEnd,
    inserted_text: insertedText,
    deleted_text: deletedText,
    source_length_after: after.length,
  };
}

function eventTypeForInput(inputType = "") {
  if (inputType === "insertFromPaste") return "editor.paste";
  if (inputType === "historyUndo") return "editor.undo";
  if (inputType === "historyRedo") return "editor.redo";
  return "editor.edit";
}

function setInitialSource() {
  editor.value = localStorage.getItem(SOURCE_STORAGE_KEY) || STARTER_SOURCE;
  updateEditorChrome();
}

function updateLineNumbers() {
  const lines = editor.value.split("\n").length;
  lineNumbers.textContent = Array.from({ length: lines }, (_, index) => index + 1).join("\n");
}

function updateCursorPosition() {
  const beforeCursor = editor.value.slice(0, editor.selectionStart);
  const line = beforeCursor.split("\n").length;
  const lastBreak = beforeCursor.lastIndexOf("\n");
  const column = editor.selectionStart - lastBreak;
  cursorPosition.textContent = `Ln ${line}, Col ${column}`;
}

function updateEditorChrome() {
  updateLineNumbers();
  updateCursorPosition();
}

function persistSource() {
  saveState.textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    localStorage.setItem(SOURCE_STORAGE_KEY, editor.value);
    saveState.textContent = "Saved locally";
  }, 350);
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 1800);
}

function clearToast() {
  clearTimeout(toastTimer);
  toast.classList.remove("visible");
  toast.textContent = "";
}

function selectTab(name, logInteraction = false) {
  activeTab = name;
  tabs.forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  testOutput.hidden = name !== "tests";
  consoleOutput.hidden = name !== "console";
  knitoutOutput.hidden = name !== "knitout";
  copyButton.hidden = name !== "knitout" || !knitoutOutput.textContent;
  if (logInteraction) recordEvent(`output.${name}_viewed`);
}

function selectGuideTab(name, logInteraction = false) {
  guideTabs.forEach((tab) => {
    const selected = tab.dataset.guideTab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  guideViews.forEach((view) => {
    view.hidden = view.id !== `${name}Guide`;
  });
  if (logInteraction) recordEvent(`guide.${name}_viewed`);
}

function renderCheck(check) {
  if (!check || !Array.isArray(check.tests)) {
    testOutput.innerHTML = "";
    return;
  }
  testOutput.innerHTML = check.tests
    .map(
      (test) => `
        <article class="test-case ${test.passed ? "passed" : "failed"}">
          <span class="test-marker" aria-hidden="true">${test.passed ? "✓" : "×"}</span>
          <div>
            <h3>${escapeHtml(test.label)}</h3>
            <p>${escapeHtml(test.message)}</p>
          </div>
        </article>`,
    )
    .join("");
}

function showResult(result) {
  emptyState.hidden = true;
  resultContent.hidden = false;
  const check = result.check;
  const checkPill = check
    ? `<span class="summary-pill ${check.passed ? "success" : "error"}">${check.passed_count}/${check.total_count} tests passed</span>`
    : "";

  if (result.ok) {
    const metrics = result.metrics || {};
    runSummary.innerHTML = [
      `<span class="summary-pill success">✓ Compiled</span>`,
      checkPill,
      `<span class="summary-pill">${result.duration_ms ?? 0} ms</span>`,
      `<span class="summary-pill">${metrics.loops ?? 0} loops</span>`,
      `<span class="summary-pill">${metrics.stitches ?? 0} stitches</span>`,
      `<span class="summary-pill">${metrics.courses ?? 0} courses</span>`,
    ].join("");
    const messages = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    consoleOutput.textContent = messages || "Compilation completed without messages.";
    consoleOutput.classList.remove("error");
    knitoutOutput.textContent = result.knitout || "";
  } else {
    const error = result.error || {};
    runSummary.innerHTML = [
      `<span class="summary-pill error">× Compile failed</span>`,
      checkPill,
      result.duration_ms == null ? "" : `<span class="summary-pill">${result.duration_ms} ms</span>`,
      error.type ? `<span class="summary-pill">${escapeHtml(error.type)}</span>` : "",
    ].join("");
    consoleOutput.textContent = [error.message, result.stdout, result.stderr, result.details]
      .filter(Boolean)
      .join("\n\n");
    consoleOutput.classList.add("error");
    knitoutOutput.textContent = "";
  }
  renderCheck(check);
  selectTab(check ? "tests" : "console");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showCompletion(result) {
  const completionUrl = result.completion_url;
  prolificCompletionLink.hidden = !completionUrl;
  if (completionUrl) {
    prolificCompletionLink.href = completionUrl;
    completionMessage.textContent = "Your solution and programming trace are saved. Return to Prolific to finish the study.";
    closeCompletionButton.textContent = "Stay here";
  } else {
    prolificCompletionLink.removeAttribute("href");
    completionMessage.textContent = "Your solution and programming trace are saved. This preview has no Prolific completion URL configured yet.";
    closeCompletionButton.textContent = "Close";
  }
  completionDialog.showModal();
}

async function executeSource(mode, trigger = "button") {
  if (runButton.disabled || submitButton.disabled) return;
  clearToast();
  const isSubmission = mode === "submit";
  const activeButton = isSubmission ? submitButton : runButton;
  runButton.disabled = true;
  submitButton.disabled = true;
  activeButton.querySelector("span").textContent = isSubmission ? "Submitting…" : "Running…";
  compilerState.innerHTML = `<i></i> ${isSubmission ? "Checking submission" : "Running tests"}`;
  try {
    await sessionReady;
    recordEvent(`${mode}.requested`, { trigger, source_length: editor.value.length });
    await flushEvents();
    const response = await fetch(isSubmission ? "/api/submit" : "/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: editor.value, session_id: telemetrySessionId }),
    });
    const result = await response.json();
    showResult(result);
    if (result.trace_saved === false) showToast("Compiled, but the trace could not be saved");
    const eventOutcome = isSubmission && result.submission?.passed ? "accepted" : result.ok ? "completed" : "failed";
    recordEvent(`${mode}.${eventOutcome}`, {
      execution_id: result.execution_id || null,
      code_state_id: result.code_state_id || null,
      submission_id: result.submission?.submission_id || null,
      duration_ms: result.duration_ms ?? null,
      error_type: result.error?.type || null,
      metrics: result.metrics || null,
      check: result.check || null,
    });
    if (isSubmission && result.submission?.passed) showCompletion(result);
    if (isSubmission && result.submission && !result.submission.passed) showToast("Not accepted yet — review the failed tests");
    void flushEvents();
  } catch (error) {
    showResult({ ok: false, error: { type: "ConnectionError", message: "Could not reach the compiler backend." } });
    recordEvent(`${mode}.connection_error`);
  } finally {
    runButton.disabled = false;
    submitButton.disabled = false;
    activeButton.querySelector("span").textContent = isSubmission ? "Submit" : "Run";
    compilerState.innerHTML = "<i></i> Compiler ready";
  }
}

editor.addEventListener("input", (event) => {
  const currentSource = editor.value;
  recordEvent(eventTypeForInput(event.inputType), describeEdit(previousSource, currentSource, event.inputType));
  previousSource = currentSource;
  updateEditorChrome();
  persistSource();
});
editor.addEventListener("click", updateCursorPosition);
editor.addEventListener("keyup", updateCursorPosition);
editor.addEventListener("scroll", () => {
  lineNumbers.scrollTop = editor.scrollTop;
});
editor.addEventListener("keydown", (event) => {
  if (event.key === "Tab") {
    event.preventDefault();
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    editor.setRangeText("  ", start, end, "end");
    editor.dispatchEvent(new Event("input"));
  }
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    executeSource("run", "shortcut");
  }
});

runButton.addEventListener("click", () => executeSource("run", "button"));
submitButton.addEventListener("click", () => executeSource("submit", "button"));
resetButton.addEventListener("click", () => {
  const previousLength = editor.value.length;
  editor.value = STARTER_SOURCE;
  previousSource = STARTER_SOURCE;
  localStorage.setItem(SOURCE_STORAGE_KEY, STARTER_SOURCE);
  updateEditorChrome();
  recordEvent("file.reset", {
    previous_length: previousLength,
    source_length_after: STARTER_SOURCE.length,
    source_after: STARTER_SOURCE,
  });
  showToast("Editor cleared");
  editor.focus();
});
copyButton.addEventListener("click", async () => {
  await navigator.clipboard.writeText(knitoutOutput.textContent);
  recordEvent("output.knitout_copied", { character_count: knitoutOutput.textContent.length });
  showToast("Knitout copied");
});
tabs.forEach((tab) => tab.addEventListener("click", () => selectTab(tab.dataset.tab, true)));
guideTabs.forEach((tab) => tab.addEventListener("click", () => selectGuideTab(tab.dataset.guideTab, true)));
documentationLink.addEventListener("click", () => recordEvent("guide.documentation_opened"));
closeCompletionButton.addEventListener("click", () => completionDialog.close());

document.addEventListener("visibilitychange", () => {
  recordEvent(document.hidden ? "page.hidden" : "page.visible");
  if (document.hidden) void flushEvents();
});

window.addEventListener("pagehide", () => {
  if (!telemetrySessionId) return;
  recordEvent("session.ended", { source_length: editor.value.length });
  const events = pendingEvents.splice(0);
  const eventBatches = telemetryInFlightBatch ? [telemetryInFlightBatch] : [];
  for (let index = 0; index < events.length; index += 100) {
    eventBatches.push({ batch_id: crypto.randomUUID(), events: events.slice(index, index + 100) });
  }
  const payload = JSON.stringify({
    session_id: telemetrySessionId,
    event_batches: eventBatches,
    final_source: editor.value,
  });
  const sent = navigator.sendBeacon("/api/sessions/end", new Blob([payload], { type: "application/json" }));
  if (!sent) {
    void fetch("/api/sessions/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true,
    });
  }
});

fetch("/api/health")
  .then((response) => {
    if (!response.ok) throw new Error("offline");
  })
  .catch(() => {
    compilerState.classList.add("offline");
    compilerState.innerHTML = "<i></i> Compiler offline";
  });

studyState.textContent = hasProlificParticipant ? "Prolific session" : "Preview mode";
setInitialSource();
previousSource = editor.value;
sessionReady = initializeTelemetrySession();
sessionReady.then(() => flushEvents());
setInterval(() => void flushEvents(), 2000);
