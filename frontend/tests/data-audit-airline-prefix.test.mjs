/**
 * Data-audit flight form versus the digit-only comparison routes.
 *
 * These tests fail while the form submits an airline prefix (the placeholder
 * example is GF2152) and pass once that prefix is removed before the request,
 * the example is digit-only, and a value that is not a flight number is not
 * submitted.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:net";
import path from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const nodeFetch = globalThis.fetch;
const nodeAbortSignal = globalThis.AbortSignal;
const nodeHeaders = globalThis.Headers;
const nodeRequest = globalThis.Request;
const nodeResponse = globalThis.Response;

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://127.0.0.1/",
  pretendToBeVisual: true,
});

const { window } = dom;

function installGlobal(name, value) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value,
  });
}

installGlobal("window", window);
installGlobal("document", window.document);
installGlobal("navigator", window.navigator);
installGlobal("HTMLElement", window.HTMLElement);
installGlobal("HTMLInputElement", window.HTMLInputElement);
installGlobal("HTMLFormElement", window.HTMLFormElement);
installGlobal("HTMLButtonElement", window.HTMLButtonElement);
installGlobal("Node", window.Node);
installGlobal("Element", window.Element);
installGlobal("DocumentFragment", window.DocumentFragment);
installGlobal("SVGElement", window.SVGElement);
installGlobal("MutationObserver", window.MutationObserver);
installGlobal("Event", window.Event);
installGlobal("MouseEvent", window.MouseEvent);
installGlobal("InputEvent", window.InputEvent);
installGlobal("getComputedStyle", window.getComputedStyle.bind(window));
installGlobal("requestAnimationFrame", (callback) => setTimeout(() => callback(Date.now()), 0));
installGlobal("cancelAnimationFrame", (id) => clearTimeout(id));
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
window.requestIdleCallback = (callback) => setTimeout(() => callback({
  didTimeout: false,
  timeRemaining: () => 50,
}), 0);
window.cancelIdleCallback = (id) => clearTimeout(id);
installGlobal("self", window);

window.matchMedia = (query) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() {
    return false;
  },
});
globalThis.fetch = nodeFetch;
globalThis.AbortSignal = nodeAbortSignal;
globalThis.Headers = nodeHeaders;
globalThis.Request = nodeRequest;
globalThis.Response = nodeResponse;

const auditCalls = [];
const realFetch = nodeFetch.bind(globalThis);
globalThis.fetch = async (input, init) => {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (url.includes("/data-audit/")) auditCalls.push(url);
  return realFetch(input, init);
};

let apiBase = "";
let serverProcess;
let serverLog = "";
let renderPage;
let resetAuditQueries;
let fireEvent;
let waitFor;
let cleanup;
let act;

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      const port = typeof address === "object" && address ? address.port : 0;
      probe.close(() => resolve(port));
    });
  });
}

async function waitForServer() {
  const deadline = Date.now() + 30_000;
  let lastError = "";
  while (Date.now() < deadline) {
    if (serverProcess.exitCode !== null) {
      throw new Error(`data-audit test server exited ${serverProcess.exitCode}\n${serverLog}`);
    }
    try {
      const response = await realFetch(`${apiBase}/__test__/observations`);
      if (response.ok) return;
      lastError = `status ${response.status}`;
    } catch (error) {
      lastError = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`data-audit test server did not start: ${lastError}\n${serverLog}`);
}

async function observations() {
  const response = await realFetch(`${apiBase}/__test__/observations`);
  assert.equal(response.status, 200);
  return response.json();
}

async function resetObservations() {
  auditCalls.length = 0;
  const response = await realFetch(`${apiBase}/__test__/reset`, { method: "POST" });
  assert.equal(response.status, 200);
}

function forms() {
  return [...document.querySelectorAll("form")];
}

async function mount() {
  resetAuditQueries();
  cleanup();
  await act(async () => {
    renderPage(renderPage.__element());
  });
}

async function typeInto(form, id, value) {
  const input = form.querySelector(`#${id}`);
  assert.ok(input, `missing #${id}`);
  fireEvent.change(input, { target: { value } });
}

async function submitForm(value, extra = {}) {
  await mount();
  const form = forms()[0];
  assert.ok(form, "data audit form was not rendered");
  await typeInto(form, "fn", value);
  if (extra.origin !== undefined) await typeInto(form, "origin", extra.origin);
  if (extra.date !== undefined) await typeInto(form, "date", extra.date);
  const button = form.querySelector("button[type='submit']");
  assert.ok(button, "missing compare button");
  fireEvent.click(button);
}

function bodyText() {
  return document.body.textContent ?? "";
}

before(async () => {
  const port = await freePort();
  apiBase = `http://127.0.0.1:${port}`;
  process.env.NEXT_PUBLIC_API_URL = apiBase;

  const python = process.env.DATA_AUDIT_PYTHON ?? "python3";
  serverProcess = spawn(
    python,
    [path.join(repoRoot, "backend/tests/data_audit_prefix_harness.py"), String(port)],
    {
      cwd: repoRoot,
      env: { ...process.env, PYTHONPATH: repoRoot },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  serverProcess.stdout.on("data", (chunk) => {
    serverLog += chunk.toString();
  });
  serverProcess.stderr.on("data", (chunk) => {
    serverLog += chunk.toString();
  });
  await waitForServer();

  const react = await import("react");
  const rtl = await import("@testing-library/react");
  const harness = await import("./DataAuditHarness.tsx");

  act = react.act;
  fireEvent = rtl.fireEvent;
  waitFor = rtl.waitFor;
  cleanup = rtl.cleanup;
  resetAuditQueries = harness.resetAuditQueries;
  renderPage = (node) => rtl.render(node);
  renderPage.__element = () => react.createElement(harness.DataAuditHarness);
});

after(async () => {
  if (cleanup) cleanup();
  if (serverProcess && serverProcess.exitCode === null) {
    serverProcess.kill("SIGTERM");
    await Promise.race([
      once(serverProcess, "exit"),
      new Promise((resolve) => setTimeout(resolve, 2_000)),
    ]);
    if (serverProcess.exitCode === null) serverProcess.kill("SIGKILL");
  }
});

test("flight number example is one to five digits", async () => {
  await mount();
  const inputs = [...document.querySelectorAll("#fn")];
  assert.ok(inputs.length >= 1, "flight number field was not rendered");
  for (const input of inputs) {
    assert.match(input.getAttribute("placeholder") ?? "", /^\d{1,5}$/);
  }
});

test("submitting an airline prefix loads both comparisons for the numeric flight", async () => {
  await resetObservations();
  await submitForm("  gf2152  ", { origin: "bah", date: "2026-03-19" });

  await waitFor(() => {
    const text = bodyText();
    assert.equal(text.includes("Invalid flight number"), false, text);
    assert.equal(text.includes("Unable to load this section"), false, text);
    assert.ok(text.includes("G14"), text);
    assert.ok(text.includes("09:41"), text);
    assert.ok(text.includes("137"), text);
    assert.ok(text.includes("96"), text);
  }, { timeout: 5_000 });

  const seen = await observations();
  const paths = seen.requests.map((request) => request.path).sort();
  assert.deepEqual(paths, [
    "/data-audit/2152/compare",
    "/data-audit/2152/passengers",
  ]);
  assert.deepEqual(
    auditCalls.map((url) => new URL(url).pathname).sort(),
    ["/data-audit/2152/compare", "/data-audit/2152/passengers"],
  );
  for (const request of seen.requests) {
    assert.equal(request.status, 200);
    const query = new URLSearchParams(request.query);
    assert.equal(query.get("origin"), "BAH");
    assert.equal(query.get("date"), "2026-03-19");
    assert.equal(query.get("seq"), null);
  }
  assert.ok(seen.lookups.length >= 4, JSON.stringify(seen.lookups));
  for (const lookup of seen.lookups) {
    assert.equal(lookup.flightNumber, "2152");
    assert.equal(lookup.origin, "BAH");
    assert.equal(lookup.date, "2026-03-19");
    assert.equal(lookup.seq, null);
  }
  const stores = new Set(seen.lookups.map((lookup) => lookup.store));
  assert.ok(stores.has("postgres"));
  assert.ok(stores.has("flight_status"));
  assert.ok(stores.has("passenger_list"));

  const passengerButton = [...document.querySelectorAll("button")].find((button) =>
    button.textContent?.includes("Passenger information"),
  );
  assert.ok(passengerButton, "passenger section was not rendered");
  fireEvent.click(passengerButton);
  await waitFor(() => {
    const text = bodyText();
    assert.equal(text.includes("Invalid flight number"), false, text);
    assert.equal(text.includes("Unable to load this section"), false, text);
    assert.ok(text.includes("Field-level comparison for passenger totals"), text);
    assert.ok(text.includes("137"), text);
    assert.ok(text.includes("96"), text);
  });
});

test("a two-letter prefix other than the placeholder carrier is stripped once", async () => {
  await resetObservations();
  await submitForm("BA2152");

  await waitFor(() => {
    assert.ok(bodyText().includes("G14"), bodyText());
  });

  const seen = await observations();
  assert.deepEqual(
    seen.requests.map((request) => request.path).sort(),
    ["/data-audit/2152/compare", "/data-audit/2152/passengers"],
  );
  for (const request of seen.requests) assert.equal(request.status, 200);
  for (const lookup of seen.lookups) assert.equal(lookup.flightNumber, "2152");
});

test("a leading airline code is removed before a zero-padded flight number is looked up", async () => {
  await resetObservations();
  await submitForm("GF02152");

  await waitFor(() => {
    const text = bodyText();
    assert.equal(text.includes("Invalid flight number"), false, text);
    assert.ok(text.includes("Flight 02152 not found"), text);
  });

  const seen = await observations();
  assert.deepEqual(
    seen.requests.map((request) => `${request.status} ${request.path}`).sort(),
    [
      "404 /data-audit/02152/compare",
      "404 /data-audit/02152/passengers",
    ],
  );
  assert.ok(seen.lookups.length >= 4);
  for (const lookup of seen.lookups) assert.equal(lookup.flightNumber, "02152");
});

test("a value that is not a flight number is not submitted", async () => {
  const rejected = ["ABCDE", "GF", "G2152", "2152GF", "21GF52", "GF123456", "GFGF2152", "GF 2152", "12A", "21526"];
  for (const value of rejected) {
    await resetObservations();
    await submitForm(value);
    await new Promise((resolve) => setTimeout(resolve, 400));
    const seen = await observations();
    assert.deepEqual(seen.requests, [], `${value} reached the data-audit routes: ${JSON.stringify(seen.requests)}`);
    assert.deepEqual(seen.lookups, [], `${value} touched a store: ${JSON.stringify(seen.lookups)}`);
    assert.deepEqual(auditCalls, [], `${value} called the data-audit client: ${auditCalls.join(", ")}`);
    assert.equal(bodyText().includes("Invalid flight number"), false, bodyText());
    assert.equal(bodyText().includes("Unable to load this section"), false, bodyText());
    assert.ok(bodyText().includes("No comparison loaded"), bodyText());
  }
});

test("a digit-only flight number still loads the same comparison", async () => {
  await resetObservations();
  await submitForm("2152", { origin: "BAH", date: "2026-03-19" });

  await waitFor(() => {
    const text = bodyText();
    assert.ok(text.includes("G14"), text);
    assert.ok(text.includes("137"), text);
    assert.ok(text.includes("96"), text);
    assert.equal(text.includes("Invalid flight number"), false, text);
  });

  const seen = await observations();
  assert.deepEqual(
    seen.requests.map((request) => request.path).sort(),
    ["/data-audit/2152/compare", "/data-audit/2152/passengers"],
  );
  for (const request of seen.requests) assert.equal(request.status, 200);
  for (const lookup of seen.lookups) assert.equal(lookup.flightNumber, "2152");
});
