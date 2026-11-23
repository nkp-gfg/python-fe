// @ts-nocheck
/**
 * Carrier label on the operations surfaces.
 *
 * The flight list and dashboard already return the airline stored with the
 * flight. These tests render the real screens against that payload and require
 * the loaded header to print that airline. A fixed "GF" prefix fails them.
 *
 * CARRIER_FIXTURE is a JSON file produced by the API test from an unauthenticated
 * ingestion of a non-GF carrier. It contains the list, dashboard, and tree
 * responses that the product returned.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { mock, test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const nodeRequire = createRequire(import.meta.url);
const nodeModule = nodeRequire("module");
const resolveFilename = nodeModule._resolveFilename;
function resolveAtAlias(request) {
  const base = path.join(frontendRoot, request.slice(2));
  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    `${base}.js`,
    `${base}.mjs`,
    path.join(base, "index.ts"),
    path.join(base, "index.tsx"),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? base;
}

const linkShim = path.join(frontendRoot, "tests/shims/next-link.cjs");

nodeModule._resolveFilename = function resolveAlias(request, parent, isMain, options) {
  if (request === "next/link") {
    request = linkShim;
  } else if (typeof request === "string" && request.startsWith("@/")) {
    request = resolveAtAlias(request);
  }
  return resolveFilename.call(this, request, parent, isMain, options);
};

const fixturePath = process.env.CARRIER_FIXTURE;
if (!fixturePath) {
  throw new Error("CARRIER_FIXTURE must point at the API payload JSON");
}

const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
const AIRLINE = fixture.airline;
const FLIGHT = fixture.flightNumber;
const ORIGIN = fixture.origin;
const DATE = fixture.date;
const LABEL = `${AIRLINE}${FLIGHT}`;

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://127.0.0.1:3000/",
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
installGlobal("SVGElement", window.SVGElement);
installGlobal("Element", window.Element);
installGlobal("Node", window.Node);
installGlobal("DocumentFragment", window.DocumentFragment);
installGlobal("MutationObserver", window.MutationObserver);
installGlobal("getComputedStyle", window.getComputedStyle.bind(window));
installGlobal("IS_REACT_ACT_ENVIRONMENT", true);
installGlobal("self", window);

window.matchMedia = () => ({
  matches: false,
  media: "",
  onchange: null,
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() {
    return false;
  },
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = ResizeObserverStub;
globalThis.ResizeObserver = ResizeObserverStub;

const searchBySurface = {
  board: `origin=${ORIGIN}&date=${DATE}`,
  compare: `flights=${FLIGHT}:${ORIGIN}:${DATE}`,
  analytics: `flight=${FLIGHT}&origin=${ORIGIN}&destination=${fixture.destination || ""}&date=${DATE}`,
};
let activeSurface = "board";

mock.module("next/navigation", {
  namedExports: {
    useRouter() {
      return {
        replace() {},
        push() {},
        prefetch() {},
        back() {},
      };
    },
    usePathname() {
      if (activeSurface === "compare") return "/compare";
      if (activeSurface === "analytics") return "/analytics";
      return `/flights/${FLIGHT}`;
    },
    useParams() {
      return { flightNumber: FLIGHT };
    },
    useSearchParams() {
      return new URLSearchParams(searchBySurface[activeSurface] || "");
    },
  },
});

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(payload) {
  globalThis.fetch = async (input) => {
    const url = String(input);
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    if (path.startsWith("/flights?") || path === "/flights") {
      return jsonResponse(payload.flights);
    }
    if (path.includes("/dashboard")) {
      return jsonResponse(payload.dashboard);
    }
    if (path.includes("/tree")) {
      return jsonResponse(payload.tree);
    }
    if (path.startsWith("/analytics/")) {
      return jsonResponse({ detail: "analytics not required for the carrier label" }, 500);
    }
    return jsonResponse({ detail: `unmocked ${path}` }, 404);
  };
}

async function waitUntil(check) {
  const started = Date.now();
  let lastError = new Error("condition was not met");
  while (Date.now() - started < 3000) {
    try {
      check();
      return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw lastError;
}

function headerText(container, selector) {
  const node = container.querySelector(selector);
  assert.ok(node, `missing ${selector}`);
  return (node.textContent || "").replace(/\s+/g, "");
}

let render;
let cleanup;
let QueryClient;
let QueryClientProvider;
let ToastProvider;
let ThemeProvider;
let activeClient;

test.before(async () => {
  const testing = await import("@testing-library/react");
  render = testing.render;
  cleanup = testing.cleanup;
  // The screens are loaded as CJS and resolve this package to its source
  // entry. Requiring that same entry keeps one QueryClient context.
  const query = nodeRequire("@tanstack/react-query");
  QueryClient = query.QueryClient;
  QueryClientProvider = query.QueryClientProvider;
  ToastProvider = nodeRequire(path.join(frontendRoot, "components/ui/toast.tsx")).ToastProvider;
  ThemeProvider = nodeRequire("next-themes").ThemeProvider;
});

test.afterEach(() => {
  cleanup?.();
  activeClient?.clear();
  activeClient = undefined;
});

function withClient(ui) {
  activeClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, refetchOnWindowFocus: false },
    },
  });
  return render(
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      <QueryClientProvider client={activeClient}>
        <ToastProvider>{ui}</ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

test("flight board header prints the stored carrier, not a fixed GF prefix", async () => {
  activeSurface = "board";
  installFetch(fixture);
  const { FlightWorkbench } = await import("../components/dashboard/flight-workbench.tsx");
  const view = withClient(
    <FlightWorkbench
      initialSelection={{ flightNumber: FLIGHT, origin: ORIGIN, date: DATE }}
    />,
  );

  await waitUntil(() => {
    const heading = headerText(view.container, "h1");
    assert.equal(heading.startsWith(LABEL), true, `board header rendered ${heading}`);
  });

  const sidebar = view.container.textContent || "";
  assert.ok(sidebar.includes(LABEL), "sidebar should keep the stored carrier");
  const heading = headerText(view.container, "h1");
  assert.equal(heading.startsWith("GF"), false);
});

test("flight board prefers dashboard flight status airline, then the list row", async () => {
  activeSurface = "board";
  const { FlightWorkbench } = await import("../components/dashboard/flight-workbench.tsx");

  const dashboardFirst = structuredClone(fixture);
  dashboardFirst.flights[0].airline = "QR";
  dashboardFirst.dashboard.flightStatus.airline = AIRLINE;
  installFetch(dashboardFirst);
  const preferred = withClient(
    <FlightWorkbench
      initialSelection={{ flightNumber: FLIGHT, origin: ORIGIN, date: DATE }}
    />,
  );
  await waitUntil(() => {
    const heading = headerText(preferred.container, "h1");
    assert.equal(heading.startsWith(LABEL), true, `dashboard airline lost, header rendered ${heading}`);
  });
  cleanup();

  const listFallback = structuredClone(fixture);
  delete listFallback.dashboard.flightStatus.airline;
  listFallback.flights[0].airline = AIRLINE;
  installFetch(listFallback);
  const fallback = withClient(
    <FlightWorkbench
      initialSelection={{ flightNumber: FLIGHT, origin: ORIGIN, date: DATE }}
    />,
  );
  await waitUntil(() => {
    const heading = headerText(fallback.container, "h1");
    assert.equal(heading.startsWith(LABEL), true, `list-row airline lost, header rendered ${heading}`);
  });
});

test("compare headers print the stored carrier on the column and the tree panel", async () => {
  activeSurface = "compare";
  installFetch(fixture);
  const compare = await import("../app/compare/page.tsx");
  const ComparePage = compare.default;
  const view = withClient(<ComparePage />);

  await waitUntil(() => {
    const text = (view.container.textContent || "").replace(/\s+/g, "");
    const occurrences = text.split(LABEL).length - 1;
    const wrong = text.split(`GF${FLIGHT}`).length - 1;
    assert.ok(
      occurrences >= 3 && wrong === 0,
      `stored=${occurrences} fixedGF=${wrong} snippet=${text.slice(text.indexOf(FLIGHT) - 20, text.indexOf(FLIGHT) + 80)}`,
    );
  });
});

test("analytics active-flight chip prints the stored carrier", async () => {
  activeSurface = "analytics";
  installFetch(fixture);
  const analytics = await import("../app/analytics/page.tsx");
  const AnalyticsPage = analytics.default;
  const view = withClient(<AnalyticsPage />);

  await waitUntil(() => {
    const flightLabel = [...view.container.querySelectorAll("span")].find(
      (node) => node.textContent === "Flight",
    );
    assert.ok(flightLabel, "active flight filter missing");
    const chip = flightLabel.parentElement?.querySelector("span");
    const value = [...(flightLabel.parentElement?.querySelectorAll("span") ?? [])].find(
      (node) => node !== flightLabel,
    );
    assert.ok(chip && value, "active flight value missing");
    assert.equal((value.textContent || "").replace(/\s+/g, ""), LABEL);
  });
});
