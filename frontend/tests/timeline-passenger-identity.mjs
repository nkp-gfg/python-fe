/**
 * Renders the Activity tab and the activity feed from the live timeline
 * routes. Each stored passenger change must show last name, first name,
 * and record locator in the row text.
 *
 * The route payload is produced by backend/tests/test_timeline_passenger_identity.py,
 * which diffs two manifests and calls get_flight_timeline / get_activity_feed.
 * fetch is stubbed only as the HTTP transport; it returns those route bodies.
 *
 * Run from frontend/ after `npm install`, with jsdom resolvable
 * (for example `npm install --prefix /tmp/jsdom-pkg jsdom@25`) and the
 * API dependencies importable by the Python fixture:
 *
 *   NODE_PATH=/tmp/jsdom-pkg/node_modules PYTHONPATH=.. \
 *     npx --yes tsx tests/timeline-passenger-identity.mjs
 */

import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(frontendDir, "..");
const require = createRequire(path.join(frontendDir, "package.json"));

const { JSDOM } = require("jsdom");
const dom = new JSDOM("<!DOCTYPE html><html><body><div id='root'></div></body></html>", {
  url: "http://127.0.0.1:3000",
  pretendToBeVisual: true,
});
const { window } = dom;

function copyWindowKey(key) {
  if (key in window) {
    globalThis[key] = window[key];
  }
}

globalThis.window = window;
globalThis.document = window.document;
Object.defineProperty(globalThis, "navigator", {
  value: window.navigator,
  configurable: true,
});
globalThis.HTMLElement = window.HTMLElement;
globalThis.Element = window.Element;
globalThis.Node = window.Node;
globalThis.DocumentFragment = window.DocumentFragment;
globalThis.SVGElement = window.SVGElement;
globalThis.MutationObserver = window.MutationObserver;
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
copyWindowKey("DOMParser");
copyWindowKey("NodeFilter");
copyWindowKey("requestAnimationFrame");
copyWindowKey("cancelAnimationFrame");
if (!globalThis.requestAnimationFrame) {
  globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
}
if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
if (!window.IntersectionObserver) {
  window.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  };
}
globalThis.ResizeObserver = window.ResizeObserver;
globalThis.IntersectionObserver = window.IntersectionObserver;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function loadPayload() {
  const pythonPath = [process.env.PYTHONPATH, repoRoot].filter(Boolean).join(":");
  const result = spawnSync(
    "python3",
    ["-m", "backend.tests.test_timeline_passenger_identity", "--emit"],
    {
      cwd: repoRoot,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH: pythonPath },
    },
  );
  if (result.status !== 0) {
    throw new Error(
      `timeline route fixture failed (${result.status}): ${result.stderr || result.stdout}`,
    );
  }
  return JSON.parse(result.stdout);
}

function installFetch(payload) {
  globalThis.fetch = async (url) => {
    const target = String(url);
    let body;
    if (target.includes("/flights/") && target.includes("/timeline")) {
      body = payload.timeline;
    } else if (target.includes("/activity/feed")) {
      body = payload.activity;
    } else {
      return new Response(JSON.stringify({ detail: `unexpected ${target}` }), { status: 404 });
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
}

function uniquePassengers(expectedPassengers) {
  const byPnr = new Map();
  for (const person of expectedPassengers) {
    byPnr.set(person.pnr, person);
  }
  return [...byPnr.values()];
}

function missingIdentity(text, person) {
  return ["lastName", "firstName", "pnr"].filter((field) => !text.includes(person[field]));
}

const payload = loadPayload();
const people = uniquePassengers(payload.expectedPassengers);
if (people.length < 12) {
  throw new Error(`expected passenger changes were not produced, got ${people.length}`);
}

installFetch(payload);

const { renderActivitySurfaces } = await import("./render-timeline-harness.tsx");
const rendered = await renderActivitySurfaces();
const timeline = { label: "flight activity timeline", text: rendered.timeline };
const feed = { label: "activity feed", text: rendered.feed };

const failures = [];
for (const surface of [timeline, feed]) {
  if (surface.text.includes("Failed to load")) {
    failures.push(`${surface.label} failed to render: ${surface.text}`);
    continue;
  }
  for (const person of people) {
    const missing = missingIdentity(surface.text, person);
    if (missing.length > 0) {
      failures.push(
        `${surface.label} does not show ${person.firstName} ${person.lastName} (${person.pnr}); missing ${missing.join(", ")}`,
      );
    }
  }
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  console.error("--- flight activity timeline text ---");
  console.error(timeline.text);
  console.error("--- activity feed text ---");
  console.error(feed.text);
  process.exit(1);
}

console.log(
  `PASS: ${people.length} passengers are visible on the activity timeline and the activity feed`,
);
