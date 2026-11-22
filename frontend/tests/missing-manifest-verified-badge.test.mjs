/**
 * Renders the real FlightWorkbench header.
 *
 * The Verified badge is shown only when dataIntegrity.valid is true AND
 * dataIntegrity.checks is greater than zero. A payload with valid true and
 * checks zero — the shape GET /flights/{flight}/dashboard returns when a
 * flight-status document exists and the passenger list does not — must not
 * render that badge or the "all checks passed" tooltip.
 *
 * next/navigation is stubbed because the header does not read the router.
 * useRouter and usePathname are only used when the operator picks a flight
 * or goes home. The badge decision is the component's own render.
 */
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const frontendRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");

function loadDep(name) {
  try {
    return require(name);
  } catch {
    console.error(
      `Missing ${name}. From frontend/ run: npm install && npm install --no-save --no-package-lock esbuild jsdom`,
    );
    process.exit(1);
  }
}

const esbuild = loadDep("esbuild");
const { JSDOM } = loadDep("jsdom");

const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
  url: "http://127.0.0.1:3000/flights/4401?origin=BAH&date=2026-06-01",
  pretendToBeVisual: true,
});
const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
globalThis.HTMLElement = window.HTMLElement;
globalThis.Node = window.Node;
globalThis.Element = window.Element;
globalThis.SVGElement = window.SVGElement;
globalThis.DocumentFragment = window.DocumentFragment;
globalThis.MutationObserver = window.MutationObserver;
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverPolyfill;
window.ResizeObserver = ResizeObserverPolyfill;
window.matchMedia = (query) => ({
  matches: false,
  media: query,
  addEventListener() {},
  removeEventListener() {},
  addListener() {},
  removeListener() {},
  dispatchEvent() {
    return false;
  },
});

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

globalThis.fetch = async (url) => {
  const target = String(url);
  if (target.includes("/dashboard")) {
    return jsonResponse(globalThis.__dashboard);
  }
  if (target.includes("/tree")) {
    return jsonResponse({
      title: "Aircraft Humans Breakdown Tree",
      badge: "Sabre Live",
      width: 940,
      height: 600,
      nodes: [],
      edges: [],
      statusCards: [],
    });
  }
  if (target.includes("/timeline")) {
    return jsonResponse({
      flightNumber: "4401",
      origin: "BAH",
      departureDate: "2026-06-01",
      events: [],
      eventCount: 0,
      stats: {
        totalChanges: 0,
        totalEvents: 0,
        totalCheckins: 0,
        totalBoardings: 0,
        totalUpgrades: 0,
        totalSeatChanges: 0,
        checkedIn: 0,
        boarded: 0,
        upgrades: 0,
        seatChanges: 0,
        statusChanges: 0,
        timeRange: {},
      },
    });
  }
  if (target.includes("/boarding-progress")) {
    return jsonResponse({
      flightNumber: "4401",
      origin: "BAH",
      departureDate: "2026-06-01",
      totalPassengers: 0,
      checkinProgress: { current: 0, total: 0, percentage: 0, data: [] },
      boardingProgress: { current: 0, total: 0, percentage: 0, data: [] },
      milestones: [],
    });
  }
  if (target.includes("/flights")) {
    return jsonResponse([]);
  }
  return jsonResponse({});
};

const entry = `
import { createRoot } from "react-dom/client";
import { act } from "react";
import { Providers } from "./components/providers.tsx";
import { FlightWorkbench } from "./components/dashboard/flight-workbench.tsx";

export async function renderBoard() {
  const el = document.getElementById("root");
  const root = createRoot(el);
  await act(async () => {
    root.render(
      <Providers>
        <FlightWorkbench initialSelection={{ flightNumber: "4401", origin: "BAH", date: "2026-06-01" }} />
      </Providers>
    );
  });
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    const text = document.body.textContent || "";
    if (text.includes("GF4401") && text.includes("Souls on Manifest")) break;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  const titles = [...document.querySelectorAll("[title]")].map((node) => node.getAttribute("title") || "");
  const text = document.body.textContent || "";
  await act(async () => {
    root.unmount();
  });
  return { titles, text };
}
`;

const built = await esbuild.build({
  absWorkingDir: frontendRoot,
  stdin: {
    contents: entry,
    resolveDir: frontendRoot,
    sourcefile: "missing-manifest-verified-badge.entry.tsx",
    loader: "tsx",
  },
  bundle: true,
  format: "esm",
  platform: "browser",
  write: false,
  jsx: "automatic",
  alias: { "@": frontendRoot },
  define: {
    "process.env.NODE_ENV": '"development"',
    "process.env.NEXT_PUBLIC_API_URL": '"http://127.0.0.1:8000"',
  },
  banner: {
    js: [
      "const window = globalThis.window;",
      "const document = window.document;",
      "const navigator = window.navigator;",
      "const HTMLElement = window.HTMLElement;",
      "const Node = window.Node;",
      "const Element = window.Element;",
      "const SVGElement = window.SVGElement;",
      "const DocumentFragment = window.DocumentFragment;",
      "const MutationObserver = window.MutationObserver;",
      "const getComputedStyle = window.getComputedStyle.bind(window);",
      "const requestAnimationFrame = globalThis.requestAnimationFrame;",
      "const cancelAnimationFrame = globalThis.cancelAnimationFrame;",
      "const ResizeObserver = globalThis.ResizeObserver;",
      "const self = window;",
    ].join(" "),
  },
  plugins: [
    {
      name: "stub-next-navigation",
      setup(build) {
        build.onResolve({ filter: /^next\/navigation$/ }, () => ({
          path: "next-navigation",
          namespace: "stub-next-navigation",
        }));
        build.onLoad({ filter: /.*/, namespace: "stub-next-navigation" }, () => ({
          contents: `
            export function useRouter() {
              return { push() {}, replace() {}, back() {}, prefetch() {} };
            }
            export function usePathname() { return "/flights/4401"; }
            export function useSearchParams() { return new URLSearchParams("origin=BAH&date=2026-06-01"); }
            export function useParams() { return { flightNumber: "4401" }; }
          `,
          loader: "js",
        }));
      },
    },
  ],
});

const bundlePath = path.join(os.tmpdir(), "missing-manifest-verified-badge.bundle.mjs");
fs.writeFileSync(bundlePath, built.outputFiles[0].text);
const { renderBoard } = await import(pathToFileURL(bundlePath).href);

function board(integrity) {
  return {
    flightStatus: {
      status: "OPENCI",
      gate: "A1",
      flightNumber: "4401",
      origin: "BAH",
      departureDate: "2026-06-01",
      aircraft: { type: "320", registration: "A9C-TE" },
      schedule: {},
    },
    flightSequenceNumber: null,
    route: { origin: "BAH", destination: "LHR", departureDate: "2026-06-01" },
    passengerSummary: {},
    analysis: {},
    changeSummary: {},
    overview: {
      soulsOnBoard: 0,
      manifestRecords: 0,
      totalSouls: 0,
      economySouls: 0,
      businessSouls: 0,
      trackedChanges: 0,
    },
    dataIntegrity: integrity,
    fetchedAt: "",
    flightPhase: { phase: "SCHEDULED", label: "Scheduled" },
    tree: null,
    schedule: null,
    stateSummary: {
      booked: {},
      checkedIn: {},
      boarded: {},
      others: {},
    },
    groupBookingSummary: null,
    specialRequestsSummary: null,
    codeshareInfo: [],
    freeTextRemarks: [],
    departureGate: "",
    insights: null,
  };
}

function passedTitles(titles) {
  return titles.filter((title) => /^All \d+ validation checks passed$/.test(title));
}

function assertBoardLoaded(rendered, label) {
  if (!rendered.text.includes("GF4401") || !rendered.text.includes("Souls on Manifest")) {
    throw new Error(`${label}: flight header did not render\n${rendered.text.slice(0, 500)}`);
  }
}

const failures = [];

function check(name, fn) {
  return fn().then(
    () => console.log(`PASS ${name}`),
    (error) => {
      failures.push(`${name}: ${error.message}`);
      console.error(`FAIL ${name}: ${error.message}`);
    },
  );
}

const hardStop = setTimeout(() => {
  console.error("FAIL timed out rendering the flight board");
  process.exit(1);
}, 20000);

await check("zero checks are not Verified", async () => {
  globalThis.__dashboard = board({ valid: true, checks: 0, warnings: [] });
  const rendered = await renderBoard();
  assertBoardLoaded(rendered, "zero checks");
  const passed = passedTitles(rendered.titles);
  if (passed.length !== 0) {
    throw new Error(
      `header rendered a passed-checks badge (${passed.join(", ")}) beside manifest totals of zero`,
    );
  }
  if (rendered.text.includes("Verified")) {
    throw new Error("header text includes Verified with checks equal to zero");
  }
});

await check("completed checks still show Verified", async () => {
  globalThis.__dashboard = board({ valid: true, checks: 4, warnings: [] });
  const rendered = await renderBoard();
  assertBoardLoaded(rendered, "four checks");
  if (!rendered.titles.includes("All 4 validation checks passed")) {
    throw new Error(`missing passed-checks tooltip; titles=${JSON.stringify(rendered.titles)}`);
  }
  if (!rendered.text.includes("Verified")) {
    throw new Error("Verified label missing when checks ran and passed");
  }
});

await check("unchecked manifest shows the warning instead of Verified", async () => {
  globalThis.__dashboard = board({
    valid: false,
    checks: 0,
    warnings: ["Passenger manifest was not checked"],
  });
  const rendered = await renderBoard();
  assertBoardLoaded(rendered, "unchecked manifest");
  const passed = passedTitles(rendered.titles);
  if (passed.length !== 0) {
    throw new Error(`unchecked manifest still shows ${passed.join(", ")}`);
  }
  if (rendered.text.includes("Verified")) {
    throw new Error("Verified label shown when valid is false");
  }
  const blob = `${rendered.titles.join(" ")} ${rendered.text}`.toLowerCase();
  if (!blob.includes("manifest was not checked")) {
    throw new Error("warning that the manifest was not checked is not visible");
  }
});

clearTimeout(hardStop);
if (failures.length) {
  process.exit(1);
}
process.exit(0);
