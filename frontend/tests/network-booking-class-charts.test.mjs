/**
 * The Network Analytics booking-class charts must show every class the
 * analytics payload contains, or say that the chart is a top-N list and how
 * many classes were left out.
 *
 * Run from frontend/:
 *   CHART_TEST_DEPS=/tmp/chart-test-deps node tests/network-booking-class-charts.test.mjs
 *
 * esbuild and jsdom are loaded from $CHART_TEST_DEPS/node_modules when they
 * are not installed in frontend/node_modules.
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const frontendRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const depRoot = process.env.CHART_TEST_DEPS
  ? path.join(process.env.CHART_TEST_DEPS, "node_modules")
  : path.join(frontendRoot, "node_modules");
const require = createRequire(path.join(depRoot, "package.json"));
const esbuild = require("esbuild");
const { JSDOM } = require("jsdom");

const ECONOMY = [
  ["Y", 130], ["B", 120], ["M", 110], ["H", 100], ["K", 90], ["Q", 80],
  ["L", 70], ["V", 60], ["S", 50], ["N", 40], ["O", 30], ["G", 20], ["T", 10],
].map(([bookingClass, count]) => ({ cabin: "Y", bookingClass, count }));

const BUSINESS = [
  ["J", 60], ["C", 50], ["D", 40], ["Z", 30], ["I", 20], ["R", 11],
].map(([bookingClass, count]) => ({ cabin: "J", bookingClass, count }));

const requests = [];

function analyticsBody(cabin) {
  const rows = cabin === "J"
    ? BUSINESS
    : cabin === "Y"
      ? ECONOMY
      : [...ECONOMY, ...BUSINESS].sort((a, b) => b.count - a.count);
  return {
    dateRange: { from: "2026-11-01", to: "2026-11-23" },
    filters: { origin: null, cabin: cabin || null, flightNumber: null },
    overview: {
      totalFlights: 1,
      uniqueRoutes: 1,
      uniqueStations: 1,
      daysTracked: 1,
      totalPassengers: 100,
      totalSouls: 100,
      avgPassengersPerFlight: 100,
      maxPassengersOnFlight: 100,
      economyPassengers: 80,
      businessPassengers: 20,
      avgLoadFactor: 70,
    },
    routePerformance: [],
    bookingClassDistribution: rows,
    nationalityDistribution: [],
    loyaltyDistribution: [],
    mealDistribution: [],
    wheelchairDistribution: [],
    changeDistribution: [],
    totalChanges: 0,
    dailyTrends: [],
    partySizeDistribution: [],
    pointOfSaleDistribution: [],
    operationalRates: {
      totalPassengers: 100,
      checkedIn: 40,
      boarded: 10,
      revenue: 90,
      standby: 0,
      checkInRate: 40,
      boardingRate: 10,
    },
    upgradeAnalytics: { cabinChanges: 0, upgradesConfirmed: 0 },
    bookingLeadTime: { sameDay: 1, within7d: 1, within30d: 1, within90d: 1, over90d: 1 },
  };
}

function installDom() {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://127.0.0.1:3000/analytics",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  window.matchMedia = () => ({
    matches: false,
    media: "",
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent() { return false; },
  });
  const ResizeObserverStub = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  window.ResizeObserver = ResizeObserverStub;
  globalThis.ResizeObserver = ResizeObserverStub;
  window.requestAnimationFrame = (callback) => window.setTimeout(callback, 0);
  window.cancelAnimationFrame = (id) => window.clearTimeout(id);
  const globals = {
    window,
    document: window.document,
    HTMLElement: window.HTMLElement,
    SVGElement: window.SVGElement,
    Element: window.Element,
    Node: window.Node,
    navigator: window.navigator,
    MutationObserver: window.MutationObserver,
    getComputedStyle: window.getComputedStyle.bind(window),
    requestAnimationFrame: window.requestAnimationFrame,
    cancelAnimationFrame: window.cancelAnimationFrame,
    IS_REACT_ACT_ENVIRONMENT: true,
  };
  for (const [key, value] of Object.entries(globals)) {
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  globalThis.fetch = async (url) => {
    const href = String(url);
    requests.push(href);
    const parsed = new URL(href);
    let body;
    if (parsed.pathname.endsWith("/flights")) body = [];
    else if (parsed.pathname.endsWith("/analytics/network")) {
      body = analyticsBody(parsed.searchParams.get("cabin"));
    } else body = {};
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  };
  return dom;
}

async function bundlePage() {
  const outfile = path.join("/tmp", "network-analytics-page-bundle.mjs");
  const stub = (name) => path.join(frontendRoot, "tests", "stubs", name);
  await esbuild.build({
    absWorkingDir: frontendRoot,
    entryPoints: ["tests/analytics-page-entry.tsx"],
    bundle: true,
    format: "esm",
    platform: "browser",
    target: "es2022",
    outfile,
    jsx: "automatic",
    define: { "process.env.NODE_ENV": "\"development\"" },
    banner: {
      js: "globalThis.process = globalThis.process || { env: { NODE_ENV: 'development' } };",
    },
    plugins: [{
      name: "analytics-test-aliases",
      setup(build) {
        build.onResolve({ filter: /^@\// }, async (args) => {
          const target = path.join(frontendRoot, args.path.slice(2));
          const resolved = await build.resolve(target, {
            resolveDir: frontendRoot,
            kind: "import-statement",
          });
          return { path: resolved.path };
        });
        build.onResolve({ filter: /^next\/link$/ }, () => ({ path: stub("next-link.js") }));
        build.onResolve({ filter: /^next\/navigation$/ }, () => ({ path: stub("next-navigation.js") }));
        build.onResolve({ filter: /^next\/dynamic$/ }, () => ({ path: stub("next-dynamic.js") }));
        build.onResolve({ filter: /^echarts$/ }, () => ({ path: stub("echarts.js") }));
        build.onLoad({ filter: /\.css$/ }, () => ({ contents: "export default {}", loader: "js" }));
        build.onLoad({ filter: /components\/ui\/echarts\.tsx$/ }, async (args) => {
          const source = await fs.promises.readFile(args.path, "utf8");
          return {
            contents: source.replace(
              "import(\"echarts\")",
              `import(${JSON.stringify(stub("echarts.js"))})`,
            ),
            loader: "tsx",
            resolveDir: path.dirname(args.path),
          };
        });
      },
    }],
  });
  return outfile;
}

function sectionByHeading(document, pattern) {
  const heading = [...document.querySelectorAll("h3")].find((node) => pattern.test(node.textContent || ""));
  assert.ok(heading, `missing chart heading ${pattern}`);
  return heading.closest("div.rounded-xl");
}

function chartSeries(section) {
  const host = section.querySelector("[role='img']");
  const option = host && host.__chartOption;
  if (!option) return { labels: [], values: [] };
  const labels = option.xAxis?.data || option.yAxis?.data || [];
  const values = option.series?.[0]?.data || [];
  return { labels, values };
}

function classIsPlotted(section, bookingClass, count) {
  const { labels, values } = chartSeries(section);
  const index = labels.indexOf(bookingClass);
  if (index >= 0 && values[index] === count) return true;
  const text = (section.textContent || "").replace(/\s+/g, " ");
  const token = new RegExp(`\\b${bookingClass}\\b`);
  return token.test(text) && text.includes(String(count));
}

function omissionIsDisclosed(section, omittedCount, totalCount) {
  const text = (section.textContent || "").replace(/\s+/g, " ");
  const topN = /top\s*-?\s*\d+/i.test(text);
  const tellsOmitted = new RegExp(`\\b${omittedCount}\\b`).test(text)
    && /omit|hidden|not shown|excluded|left out|truncated|dropped|missing/i.test(text);
  const tellsRatio = new RegExp(`\\b\\d+\\s+of\\s+${totalCount}\\b`, "i").test(text) && topN;
  return (topN && tellsOmitted) || tellsRatio;
}

function assertFareMix(section, rows) {
  const missing = rows.filter((row) => !classIsPlotted(section, row.bookingClass, row.count));
  const disclosed = omissionIsDisclosed(section, missing.length, rows.length);
  assert.ok(
    missing.length === 0 || disclosed,
    `${section.querySelector("h3")?.textContent} hides ${missing.map((row) => row.bookingClass).join(", ")} `
    + `with no top-N omission notice. Plotted: ${chartSeries(section).labels.join(", ") || "(none)"}`,
  );
}

let mountedRoot = null;

async function renderPage(page) {
  const root = page.createRoot(document.getElementById("root"));
  mountedRoot = root;
  await page.act(async () => {
    root.render(page.createElement(page.App));
  });
  const started = Date.now();
  while (Date.now() - started < 5000) {
    if ([...document.querySelectorAll("h3")].some((node) => /Booking Classes/.test(node.textContent || ""))) {
      await page.act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      return root;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error(`analytics charts did not render: ${document.body.textContent}`);
}

const dom = installDom();
const page = await import(pathToFileURL(await bundlePage()).href);

test("network booking-class charts do not silently drop classes", async () => {
  await renderPage(page);
  const failures = [];
  const check = (label, fn) => {
    try {
      fn();
    } catch (error) {
      failures.push(`${label}: ${error.message}`);
    }
  };
  check("all cabins", () => {
    assertFareMix(sectionByHeading(document, /economy booking classes/i), ECONOMY);
    assertFareMix(sectionByHeading(document, /business booking classes/i), BUSINESS);
  });

  const button = [...document.querySelectorAll("button")].find((node) => node.textContent.trim() === "Business");
  assert.ok(button, "Business cabin button is missing");
  await page.act(async () => {
    button.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  });
  const started = Date.now();
  while (Date.now() - started < 5000) {
    if (requests.some((href) => new URL(href).searchParams.get("cabin") === "J")) break;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert.ok(
    requests.some((href) => new URL(href).searchParams.get("cabin") === "J"),
    `Business filter did not request cabin J: ${requests.join("\n")}`,
  );
  await page.act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 150));
  });
  check("business cabin filter", () => {
    assertFareMix(sectionByHeading(document, /business booking classes/i), BUSINESS);
  });
  assert.deepEqual(failures, []);
});

test.after(() => {
  if (mountedRoot) mountedRoot.unmount();
});
