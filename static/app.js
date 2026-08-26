const EXAMPLE_SOURCE = `// A small stockinette swatch
pattern_width = 10;
pattern_height = 6;
c = 1;

with Carrier as c:{
  in Leftward direction:{
    tuck Front_Needles[1:pattern_width:2];
  }
  in reverse direction:{
    tuck Front_Needles[0:pattern_width:2];
  }

  // Secure the cast-on before releasing the yarn hook.
  in reverse direction:{ knit Loops; }
  in reverse direction:{ knit Loops; }
  releasehook;

  for row in range(pattern_height):{
    in reverse direction:{ knit Loops; }
  }
}`;

const editor = document.querySelector("#sourceEditor");
const lineNumbers = document.querySelector("#lineNumbers");
const cursorPosition = document.querySelector("#cursorPosition");
const saveState = document.querySelector("#saveState");
const runButton = document.querySelector("#runButton");
const resetButton = document.querySelector("#resetButton");
const compilerState = document.querySelector("#compilerState");
const emptyState = document.querySelector("#emptyState");
const resultContent = document.querySelector("#resultContent");
const runSummary = document.querySelector("#runSummary");
const consoleOutput = document.querySelector("#consoleOutput");
const knitoutOutput = document.querySelector("#knitoutOutput");
const copyButton = document.querySelector("#copyButton");
const toast = document.querySelector("#toast");
const tabs = [...document.querySelectorAll(".tab")];

let activeTab = "console";
let saveTimer;
let toastTimer;
let previousSource = "";
let telemetrySessionId = null;
let telemetrySeq = 0;
let telemetryFlush = null;
let telemetryInFlightBatch = null;
let sessionReady;
const pendingEvents = [];
const telemetryStartedAt = performance.now();
const participantId = getPersistentId("knitscript-participant-id");
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
        task_id: "playground",
        initial_source: editor.value,
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
  await sessionReady;
  if (!telemetrySessionId || pendingEvents.length === 0) return;
  if (telemetryFlush) {
    await telemetryFlush;
    return;
  }

  const events = pendingEvents.splice(0, 100);
  const batchId = crypto.randomUUID();
  telemetryInFlightBatch = { batch_id: batchId, events };
  telemetryFlush = (async () => {
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
    }
  })();

  try {
    await telemetryFlush;
  } finally {
    telemetryFlush = null;
    telemetryInFlightBatch = null;
  }
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
  editor.value = localStorage.getItem("knitscript-studio-source") || EXAMPLE_SOURCE;
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
    localStorage.setItem("knitscript-studio-source", editor.value);
    saveState.textContent = "Saved locally";
  }, 350);
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 1800);
}

function selectTab(name, logInteraction = false) {
  activeTab = name;
  tabs.forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  consoleOutput.hidden = name !== "console";
  knitoutOutput.hidden = name !== "knitout";
  copyButton.hidden = name !== "knitout" || !knitoutOutput.textContent;
  if (logInteraction) recordEvent(`output.${name}_viewed`);
}

function showResult(result) {
  emptyState.hidden = true;
  resultContent.hidden = false;

  if (result.ok) {
    const metrics = result.metrics || {};
    runSummary.innerHTML = [
      `<span class="summary-pill success">✓ Compiled</span>`,
      `<span class="summary-pill">${result.duration_ms ?? 0} ms</span>`,
      `<span class="summary-pill">${metrics.loops ?? 0} loops</span>`,
      `<span class="summary-pill">${metrics.stitches ?? 0} stitches</span>`,
      `<span class="summary-pill">${metrics.courses ?? 0} courses</span>`,
    ].join("");
    const messages = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    consoleOutput.textContent = messages || "Compilation completed without messages.";
    consoleOutput.classList.remove("error");
    knitoutOutput.textContent = result.knitout || "";
    selectTab("knitout");
  } else {
    const error = result.error || {};
    runSummary.innerHTML = [
      `<span class="summary-pill error">× Compile failed</span>`,
      result.duration_ms == null ? "" : `<span class="summary-pill">${result.duration_ms} ms</span>`,
      error.type ? `<span class="summary-pill">${escapeHtml(error.type)}</span>` : "",
    ].join("");
    consoleOutput.textContent = [error.message, result.stdout, result.stderr, result.details]
      .filter(Boolean)
      .join("\n\n");
    consoleOutput.classList.add("error");
    knitoutOutput.textContent = "";
    selectTab("console");
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function runSource(trigger = "button") {
  if (runButton.disabled) return;
  runButton.disabled = true;
  runButton.querySelector("span").textContent = "Running…";
  compilerState.innerHTML = "<i></i> Compiling";
  try {
    await sessionReady;
    recordEvent("compile.requested", { trigger, source_length: editor.value.length });
    await flushEvents();
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: editor.value, session_id: telemetrySessionId }),
    });
    const result = await response.json();
    showResult(result);
    if (result.trace_saved === false) showToast("Compiled, but the trace could not be saved");
    recordEvent(result.ok ? "compile.completed" : "compile.failed", {
      execution_id: result.execution_id || null,
      code_state_id: result.code_state_id || null,
      duration_ms: result.duration_ms ?? null,
      error_type: result.error?.type || null,
      metrics: result.metrics || null,
    });
    void flushEvents();
  } catch (error) {
    showResult({ ok: false, error: { type: "ConnectionError", message: "Could not reach the compiler backend." } });
    recordEvent("compile.connection_error");
  } finally {
    runButton.disabled = false;
    runButton.querySelector("span").textContent = "Run";
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
    runSource("shortcut");
  }
});

runButton.addEventListener("click", () => runSource("button"));
resetButton.addEventListener("click", () => {
  const previousLength = editor.value.length;
  editor.value = EXAMPLE_SOURCE;
  previousSource = EXAMPLE_SOURCE;
  localStorage.setItem("knitscript-studio-source", EXAMPLE_SOURCE);
  updateEditorChrome();
  recordEvent("file.reset", { previous_length: previousLength, source_length_after: EXAMPLE_SOURCE.length });
  showToast("Example restored");
  editor.focus();
});
copyButton.addEventListener("click", async () => {
  await navigator.clipboard.writeText(knitoutOutput.textContent);
  recordEvent("output.knitout_copied", { character_count: knitoutOutput.textContent.length });
  showToast("Knitout copied");
});
tabs.forEach((tab) => tab.addEventListener("click", () => selectTab(tab.dataset.tab, true)));

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
  navigator.sendBeacon("/api/sessions/end", new Blob([payload], { type: "application/json" }));
});

fetch("/api/health")
  .then((response) => {
    if (!response.ok) throw new Error("offline");
  })
  .catch(() => {
    compilerState.classList.add("offline");
    compilerState.innerHTML = "<i></i> Compiler offline";
  });

setInitialSource();
previousSource = editor.value;
sessionReady = initializeTelemetrySession();
sessionReady.then(() => flushEvents());
setInterval(() => void flushEvents(), 2000);
