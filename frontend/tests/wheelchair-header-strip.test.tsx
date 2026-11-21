import { readFileSync } from "node:fs";
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { Providers } from "@/components/providers";
import type { FlightDashboard, SpecialRequestsSummary } from "@/lib/types";

interface HeaderCase {
  name: string;
  flightNumber: string;
  origin: string;
  date: string;
  dashboard: FlightDashboard;
  flights: unknown[];
  tree: unknown;
}

// Button titles are the rail labels. The header prints tabLabels, which differs for groups.
const TABS = [
  { button: "Live Ops", header: "Live Ops" },
  { button: "Briefing", header: "Briefing" },
  { button: "Commercial", header: "Commercial" },
  { button: "Readiness", header: "Readiness" },
  { button: "Exceptions", header: "Exceptions" },
  { button: "Risk", header: "Risk" },
  { button: "Revenue", header: "Revenue" },
  { button: "Passengers", header: "Passengers" },
  { button: "Groups", header: "Group Bookings" },
  { button: "Standby", header: "Standby" },
  { button: "Changes", header: "Changes" },
  { button: "History", header: "History" },
  { button: "Journey", header: "Journey" },
  { button: "Reservations", header: "Reservations" },
  { button: "Activity", header: "Activity" },
  { button: "Audit", header: "Audit" },
] as const;

function loadCases(): HeaderCase[] {
  const path = process.env.WHEELCHAIR_CASES;
  if (!path) {
    throw new Error("WHEELCHAIR_CASES must point at the dashboard payloads built by the backend test");
  }
  return JSON.parse(readFileSync(path, "utf8")) as HeaderCase[];
}

beforeAll(() => {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => {
  cleanup();
  delete (globalThis as { fetch?: typeof fetch }).fetch;
});

function installFetch(testCase: HeaderCase) {
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input);
    const body = url.includes("/dashboard")
      ? testCase.dashboard
      : url.includes("/tree")
        ? testCase.tree
        : /\/flights\/[^/?]+\/passengers/.test(url)
          ? { passengers: [], totalPassengers: 0 }
          : /\/flights\/[^/?]+\/reservations/.test(url)
            ? { reservations: [] }
            : /\/flights(\?|$)/.test(url)
              ? testCase.flights
              : {};
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  globalThis.fetch = fetchMock;
}

function tabHeader(container: HTMLElement, label: string): HTMLElement {
  const title = Array.from(container.querySelectorAll("div")).find(
    (el) => el.classList.contains("text-sm") && el.classList.contains("font-medium") && el.textContent?.trim() === label,
  );
  const strip = title?.parentElement;
  if (!strip) {
    throw new Error(`tab header for ${label} was not rendered`);
  }
  return strip;
}

/**
 * The meal and frequent-flyer chips are rendered as "{code}: {count}" inside the
 * same strip. Wheelchair entries from specialRequestsSummary.wheelchairs have to
 * use that same visible text, plus a total next to a wheelchair label.
 */
function assertStripShowsWheelchairs(strip: HTMLElement, summary: SpecialRequestsSummary) {
  // Read each chip's own text. The strip's combined textContent glues sibling
  // chips together, so "SLV: 1" followed by "1 Wheelchairs" becomes "11 Wheelchairs".
  const texts = Array.from(strip.querySelectorAll("*")).map((el) =>
    (el.textContent ?? "").replace(/\s+/g, " ").trim(),
  );
  const total = summary.totalWheelchairs;
  const totalLabel = new RegExp(`^${total} (?:Wheelchairs?|WCHR)$`, "i");
  expect(
    texts.some((text) => totalLabel.test(text)),
    `wheelchair total missing from tab header chips: ${texts.join(" | ")}`,
  ).toBe(true);
  for (const [code, count] of Object.entries(summary.wheelchairs)) {
    expect(texts, `wheelchair code ${code} missing from tab header`).toContain(`${code}: ${count}`);
  }
}

function assertStripShowsMealsAndTiers(strip: HTMLElement, summary: SpecialRequestsSummary) {
  const text = strip.textContent ?? "";
  if (summary.totalSpecialMeals > 0) {
    expect(text).toContain(`${summary.totalSpecialMeals}`);
    expect(text).toMatch(/Meals/);
    for (const [code, count] of Object.entries(summary.specialMeals)) {
      expect(text).toContain(`${code}: ${count}`);
    }
  }
  if (summary.frequentFlyers > 0) {
    expect(text).toContain(`${summary.frequentFlyers}`);
    for (const [tier, count] of Object.entries(summary.ffTiers)) {
      expect(text).toContain(`${tier}: ${count}`);
    }
  }
}

describe("flight board special-request strip", () => {
  const cases = loadCases();

  it.each(cases.map((testCase) => [testCase.name, testCase] as const))(
    "%s",
    async (_name, testCase) => {
      const summary = testCase.dashboard.specialRequestsSummary;
      expect(summary).toBeTruthy();
      expect(summary!.totalWheelchairs).toBeGreaterThan(0);
      expect(Object.keys(summary!.wheelchairs).length).toBeGreaterThan(0);

      installFetch(testCase);
      const view = render(
        <Providers>
          <FlightWorkbench
            initialSelection={{
              flightNumber: testCase.flightNumber,
              origin: testCase.origin,
              date: testCase.date,
            }}
          />
        </Providers>,
      );

      await waitFor(() => {
        expect(tabHeader(view.container, "Live Ops").textContent ?? "").toMatch(/Live Ops/);
      });

      for (const tab of TABS) {
        if (tab.button !== "Live Ops") {
          const nav = view.container.querySelector(`button[title="${tab.button}"]`);
          expect(nav, `missing tab button ${tab.button}`).toBeTruthy();
          fireEvent.click(nav!);
        }
        await waitFor(() => {
          const strip = tabHeader(view.container, tab.header);
          assertStripShowsWheelchairs(strip, summary!);
          assertStripShowsMealsAndTiers(strip, summary!);
        });
        // The chips have to live in the tab header, not in a panel opened by the tab.
        const strip = tabHeader(view.container, tab.header);
        expect(within(strip).getByText(tab.header)).toBeTruthy();
      }
    },
  );
});
