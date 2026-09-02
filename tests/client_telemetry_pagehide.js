const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function createElement() {
  return {
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    disabled: false,
    selectionStart: 0,
    scrollTop: 0,
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    removeAttribute() {},
    setAttribute() {},
    querySelector() { return createElement(); },
    focus() {},
    close() {},
    showModal() {},
  };
}

const windowListeners = new Map();
const fetchCalls = [];
const beaconCalls = [];
let rejectEventUploads = false;
let uuidCounter = 0;
const context = {
  Blob,
  URLSearchParams,
  clearTimeout() {},
  console,
  crypto: { randomUUID: () => `uuid-${++uuidCounter}` },
  document: {
    hidden: false,
    querySelector: () => createElement(),
    querySelectorAll: () => [],
    addEventListener() {},
  },
  fetch: async (url, options = {}) => {
    fetchCalls.push({ url, options });
    if (url === "/api/events" && (options.keepalive || rejectEventUploads)) {
      throw new TypeError("simulated interrupted upload");
    }
    return { ok: true, json: async () => ({}), text: async () => "" };
  },
  history: { replaceState() {} },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: {
    language: "en-US",
    clipboard: { writeText: async () => {} },
    sendBeacon: (url, body) => {
      beaconCalls.push({ url, body });
      return false;
    },
  },
  performance: { now: () => 1000 },
  setInterval() {},
  setTimeout() {},
  window: {
    innerWidth: 1280,
    innerHeight: 720,
    location: { search: "", pathname: "/", assign() {} },
    addEventListener(type, callback) { windowListeners.set(type, callback); },
  },
};
context.globalThis = context;

const appSource = fs.readFileSync("static/app.js", "utf8");
const hooks = `
  globalThis.__telemetryTest = {
    begin(sessionId) { studyStarted = true; telemetrySessionId = sessionId; },
    recordEvent,
    flushEvents,
    pendingEvents,
  };
`;
vm.runInNewContext(`${appSource}\n${hooks}`, context, { filename: "static/app.js" });

async function run() {
  context.__telemetryTest.begin("session-pagehide-test");
  context.__telemetryTest.recordEvent("editor.edit", { inserted_text: "x" });
  await context.__telemetryTest.flushEvents();

  assert.equal(
    context.__telemetryTest.pendingEvents.length,
    0,
    "normal telemetry uploads must not use the browser keepalive quota",
  );
  assert.equal(fetchCalls.find((call) => call.url === "/api/events").options.keepalive, undefined);

  rejectEventUploads = true;
  for (let index = 0; index < 120; index += 1) {
    context.__telemetryTest.recordEvent("editor.edit", { inserted_text: "x".repeat(128), index });
  }

  windowListeners.get("pagehide")({ persisted: false });

  assert.equal(
    context.__telemetryTest.pendingEvents.length,
    121,
    "pagehide must retain every unacknowledged event, including session.ended",
  );
  assert.ok(
    beaconCalls.some((call) => call.url === "/api/events"),
    "pagehide must attempt bounded event uploads instead of one monolithic end-session body",
  );
  assert.ok(
    beaconCalls.every((call) => call.body.size < 64 * 1024),
    "every keepalive payload must stay below the browser 64 KiB limit",
  );
  assert.equal(
    fetchCalls.some((call) => call.url === "/api/sessions/end" && call.options.keepalive),
    false,
    "pagehide must not fall back to an oversized keepalive end-session request",
  );
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
