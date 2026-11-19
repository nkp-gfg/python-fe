/**
 * Spec for the comparison page fan-out.
 *
 * Opening /compare?flights=<comma-separated flight:origin:date entries>
 * must not start a dashboard fetch and a passenger-tree fetch for every
 * entry. These tests fail while that happens. They pass when the page:
 *
 * * keeps at most the first MAX_COMPARE_FLIGHTS entries from the query
 *   string and ignores the rest
 * * keeps the flight selector from growing the selection past that cap
 * * does not call the passenger-tree endpoint until the user activates
 *   the "Load passenger trees" button (or a checkbox of that name)
 * * after that confirmation, requests trees only for the capped selection
 *
 * The page is rendered through the same Next.js search-param objects the
 * app router uses, and the requests are the ones fetchDashboard and
 * fetchFlightTree build.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createHrefFromUrl } from "next/dist/client/components/router-reducer/create-href-from-url.js";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime.js";
import ComparePage from "@/app/compare/page";
import { Providers } from "@/components/providers";

const MAX_COMPARE_FLIGHTS = 4;
const ORIGIN = "BAH";
const DATE = "2026-01-15";

const require = createRequire(import.meta.url);
const NEXT_VERSION = JSON.parse(
  readFileSync(require.resolve("next/package.json"), "utf8"),
).version;
const QUERY_VERSION = JSON.parse(
  readFileSync(require.resolve("@tanstack/query-core/package.json"), "utf8"),
).version;

type Recorded = { url: string; kind: "list" | "dashboard" | "tree" | "other"; flightNumber: string | null };

const recorded: Recorded[] = [];
const originalFetch = globalThis.fetch;

function classify(url: string): Recorded {
  const parsed = new URL(url);
  const match = parsed.pathname.match(/^\/flights\/([^/]+)\/(dashboard|tree)$/);
  if (match) {
    return {
      url,
      kind: match[2] as "dashboard" | "tree",
      flightNumber: decodeURIComponent(match[1]),
    };
  }
  if (parsed.pathname === "/flights") {
    return { url, kind: "list", flightNumber: null };
  }
  return { url, kind: "other", flightNumber: null };
}

function calls(kind: Recorded["kind"]): Recorded[] {
  return recorded.filter((entry) => entry.kind === kind);
}

function flightNumbers(kind: Recorded["kind"]): string[] {
  return calls(kind).map((entry) => entry.flightNumber ?? "");
}

function entry(flightNumber: string): string {
  return `${flightNumber}:${ORIGIN}:${DATE}`;
}

function flightList(count: number) {
  return Array.from({ length: count }, (_, index) => {
    const flightNumber = String(2000 + index);
    return {
      airline: "GF",
      flightNumber,
      origin: ORIGIN,
      destination: "LHR",
      departureDate: DATE,
      status: "OPEN",
      operationalSummary: { soulsOnBoard: index + 1 },
    };
  });
}

/**
 * Same canonical-url steps as next@16.2.0 app-router initial state:
 * location.href -> createHrefFromUrl -> new URL(canonicalUrl).searchParams,
 * which useSearchParams then wraps.
 */
function searchParamsFor(flights: string[]) {
  const location = new URL("http://127.0.0.1:3000/compare");
  if (flights.length > 0) {
    location.searchParams.set("flights", flights.join(","));
  }
  const canonicalUrl = createHrefFromUrl(location);
  return new URL(canonicalUrl, "http://n").searchParams;
}

function renderCompare(flights: string[]) {
  return render(
    <SearchParamsContext.Provider value={searchParamsFor(flights)}>
      <Providers>
        <ComparePage />
      </Providers>
    </SearchParamsContext.Provider>,
  );
}

async function flushQueries() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
}

function confirmControl(): HTMLElement | null {
  return (
    screen.queryByRole("button", { name: /load passenger trees/i }) ??
    screen.queryByRole("checkbox", { name: /load passenger trees/i })
  );
}

async function confirmTrees() {
  const control = confirmControl();
  expect(control, "passenger trees must wait for an explicit confirmation control").not.toBeNull();
  await act(async () => {
    fireEvent.click(control as HTMLElement);
  });
  await flushQueries();
}

beforeEach(() => {
  recorded.length = 0;
  process.env.NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000";
  // next-themes reads matchMedia while mounting the same Providers the app uses.
  // jsdom does not implement it. The stub only answers the theme query.
  window.matchMedia = (query: string) =>
    ({
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
    }) as MediaQueryList;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const entry = classify(url);
    recorded.push(entry);
    if (entry.kind === "list") {
      const count = Number(process.env.COMPARE_TEST_FLIGHT_COUNT ?? "0");
      return new Response(JSON.stringify(flightList(count)), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Promise<Response>(() => {
      /* Leave dashboard and tree queries in flight so the page stays on its loading state. */
    });
  }) as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  delete process.env.COMPARE_TEST_FLIGHT_COUNT;
});

describe(`compare page fan-out (next ${NEXT_VERSION}, query-core ${QUERY_VERSION})`, () => {
  it("ignores flights query entries past the cap and does not load trees until confirmed", async () => {
    const requested = Array.from({ length: 12 }, (_, index) => entry(String(1000 + index)));
    const allowed = new Set(requested.slice(0, MAX_COMPARE_FLIGHTS).map((value) => value.split(":")[0]));
    const ignored = requested.slice(MAX_COMPARE_FLIGHTS).map((value) => value.split(":")[0]);

    renderCompare(requested);
    await flushQueries();

    const dashboards = flightNumbers("dashboard");
    const trees = flightNumbers("tree");
    const unexpected = [...dashboards, ...trees].filter((flightNumber) => ignored.includes(flightNumber));

    expect({
      dashboardFlights: dashboards,
      treeFlights: trees,
      treeUrls: calls("tree").map((entry) => entry.url),
    }).toEqual({
      dashboardFlights: dashboards.filter((flightNumber) => allowed.has(flightNumber)),
      treeFlights: [],
      treeUrls: [],
    });
    expect(new Set(dashboards).size).toBeLessThanOrEqual(MAX_COMPARE_FLIGHTS);
    expect(unexpected, `ignored query entries were requested: ${unexpected.join(",")}`).toEqual([]);

    await confirmTrees();

    const treesAfter = flightNumbers("tree");
    const dashboardsAfter = flightNumbers("dashboard");
    expect(treesAfter.length).toBeGreaterThan(0);
    expect(new Set(treesAfter).size).toBeLessThanOrEqual(MAX_COMPARE_FLIGHTS);
    expect(treesAfter.every((flightNumber) => allowed.has(flightNumber))).toBe(true);
    expect(dashboardsAfter.every((flightNumber) => allowed.has(flightNumber))).toBe(true);
    expect(
      [...dashboardsAfter, ...treesAfter].filter((flightNumber) => ignored.includes(flightNumber)),
    ).toEqual([]);
  });

  it("does not let the flight selector mount a dashboard and tree for every listed flight", async () => {
    process.env.COMPARE_TEST_FLIGHT_COUNT = "8";
    const allowed = new Set(flightList(MAX_COMPARE_FLIGHTS).map((flight) => flight.flightNumber));

    renderCompare([]);
    await flushQueries();

    const buttons = flightList(8).map((flight) =>
      screen.getByRole("button", { name: new RegExp(`GF${flight.flightNumber}`) }),
    );
    for (const button of buttons) {
      await act(async () => {
        fireEvent.click(button);
      });
    }
    await flushQueries();

    const dashboards = flightNumbers("dashboard");
    const trees = flightNumbers("tree");
    expect({
      dashboardCount: new Set(dashboards).size,
      dashboards,
      trees,
    }).toEqual({
      dashboardCount: Math.min(new Set(dashboards).size, MAX_COMPARE_FLIGHTS),
      dashboards: dashboards.filter((flightNumber) => allowed.has(flightNumber)),
      trees: [],
    });

    await confirmTrees();
    const treesAfter = flightNumbers("tree");
    expect(treesAfter.length).toBeGreaterThan(0);
    expect(new Set(treesAfter).size).toBeLessThanOrEqual(MAX_COMPARE_FLIGHTS);
    expect(treesAfter.every((flightNumber) => allowed.has(flightNumber))).toBe(true);
  });
});
