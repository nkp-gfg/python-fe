import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import * as echarts from "echarts";
import type { ECharts } from "echarts";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AnalyticsPage from "@/app/analytics/page";
import type { DailyTrend, NetworkAnalyticsResponse } from "@/lib/types";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push() {}, replace() {}, prefetch() {} }),
  usePathname: () => "/analytics",
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

/**
 * Counts the network analytics API returns for three days.
 * `flights` is the stored departure count (backend `$sum: 1`), not a display scale.
 */
const STORED_TRENDS: DailyTrend[] = [
  { date: "2026-11-18", flights: 12, passengers: 480, changes: 3 },
  { date: "2026-11-19", flights: 7, passengers: 220, changes: 1 },
  { date: "2026-11-20", flights: 150, passengers: 900, changes: 4 },
];

function analyticsPayload(): NetworkAnalyticsResponse {
  return {
    dateRange: { from: "2026-10-27", to: "2026-11-21" },
    filters: { origin: null, cabin: null, flightNumber: null },
    overview: {
      totalFlights: 169,
      uniqueRoutes: 4,
      uniqueStations: 2,
      daysTracked: 3,
      totalPassengers: 1600,
      totalSouls: 1600,
      avgLoadFactor: 70,
      avgPassengersPerFlight: 9,
      maxPassengersOnFlight: 400,
      economyPassengers: 1400,
      businessPassengers: 200,
    },
    routePerformance: [],
    bookingClassDistribution: [],
    nationalityDistribution: [],
    loyaltyDistribution: [],
    mealDistribution: [],
    wheelchairDistribution: [],
    changeDistribution: [],
    totalChanges: 8,
    dailyTrends: STORED_TRENDS,
    partySizeDistribution: [],
    pointOfSaleDistribution: [],
    operationalRates: {
      totalPassengers: 1600,
      checkedIn: 1000,
      boarded: 800,
      revenue: 1500,
      standby: 10,
      checkInRate: 62.5,
      boardingRate: 50,
    },
    upgradeAnalytics: { cabinChanges: 2, upgradesConfirmed: 1 },
    bookingLeadTime: {
      sameDay: 1,
      within7d: 2,
      within30d: 3,
      within90d: 4,
      over90d: 5,
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function trendChart(): ECharts {
  const hosts = document.querySelectorAll<HTMLElement>('[role="img"]');
  const charts = [...hosts]
    .map((host) => echarts.getInstanceByDom(host))
    .filter((chart): chart is ECharts => chart != null);
  const trend = charts.find((chart) => {
    const option = chart.getOption() as { series?: { name?: string }[] };
    return (option.series ?? []).some((series) => series.name === "Flights");
  });
  if (!trend) {
    throw new Error("daily trend chart is not mounted");
  }
  return trend;
}

function seriesByName(chart: ECharts, name: string) {
  const series = chart.getModel().getSeriesByName(name)[0];
  if (!series) {
    throw new Error(`series ${name} is missing`);
  }
  return series;
}

type GraphicNode = {
  childCount?: () => number;
  childAt?: (index: number) => GraphicNode;
  getTextContent?: () => { style?: { text?: unknown } } | null;
};

function drawnText(el: GraphicNode | null | undefined, depth = 0): string | null {
  if (!el || depth > 6) return null;
  const own = el.getTextContent?.()?.style?.text;
  if (own != null && own !== "") return String(own);
  const count = el.childCount?.() ?? 0;
  for (let i = 0; i < count; i++) {
    const found = drawnText(el.childAt?.(i), depth + 1);
    if (found) return found;
  }
  return null;
}

function pointLabel(series: ReturnType<typeof seriesByName>, dataIndex: number): string {
  const text = drawnText(series.getData().getItemGraphicEl(dataIndex) as GraphicNode | undefined);
  if (!text) {
    throw new Error(`point label for data index ${dataIndex} was not drawn`);
  }
  return text;
}

/**
 * Read the number a person sees. Accepts plain integers and thousands
 * separators. A "k" suffix is the page's abbreviation of a value >= 1000,
 * so "1.5k" is 1500, not 150.
 */
function displayedCount(text: string): number {
  const trimmed = text.trim();
  const kilo = trimmed.match(/^(\d+(?:\.\d+)?)k$/i);
  if (kilo) return Number(kilo[1]) * 1000;
  const plain = trimmed.match(/^(\d{1,3}(?:,\d{3})*|\d+)(?:\.0+)?$/);
  if (!plain) {
    throw new Error(`could not read a flight count from ${JSON.stringify(text)}`);
  }
  return Number(plain[1].replace(/,/g, ""));
}

function flightsTooltipCount(chart: ECharts, dataIndex: number): { count: number; text: string } {
  const flights = seriesByName(chart, "Flights");
  chart.dispatchAction({
    type: "showTip",
    seriesIndex: flights.seriesIndex,
    dataIndex,
  });
  const tip = [...chart.getDom().querySelectorAll("div")].find((node) =>
    [...node.querySelectorAll("span")].some((span) => span.textContent === "Flights"),
  );
  if (!tip) {
    throw new Error(`tooltip for data index ${dataIndex} did not render`);
  }
  const spans = [...tip.querySelectorAll("span")];
  const nameIndex = spans.findIndex((span) => span.textContent === "Flights");
  const valueText = spans[nameIndex + 1]?.textContent ?? "";
  return { count: displayedCount(valueText), text: tip.textContent ?? "" };
}

describe("network analytics daily trend", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/analytics/network")) return jsonResponse(analyticsPayload());
        if (url.includes("/flights")) return jsonResponse([]);
        return jsonResponse({ detail: `unexpected ${url}` }, 404);
      }),
    );
  });

  afterEach(() => {
    for (const host of document.querySelectorAll<HTMLElement>('[role="img"]')) {
      echarts.getInstanceByDom(host)?.dispose();
    }
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("plots and labels each day's stored flight count", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <AnalyticsPage />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(trendChart()).toBeTruthy();
    });

    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/analytics/network"))).toBe(true);

    const chart = trendChart();
    const flights = seriesByName(chart, "Flights");
    const passengers = seriesByName(chart, "Passengers");
    const changes = seriesByName(chart, "Changes");

    expect(flights.name).toBe("Flights");

    for (let i = 0; i < STORED_TRENDS.length; i++) {
      const stored = STORED_TRENDS[i];
      expect(flights.getRawValue(i)).toBe(stored.flights);
      expect(displayedCount(pointLabel(flights, i))).toBe(stored.flights);
      expect(passengers.getRawValue(i)).toBe(stored.passengers);
      expect(changes.getRawValue(i)).toBe(stored.changes);

      const tooltip = flightsTooltipCount(chart, i);
      expect(tooltip.count).toBe(stored.flights);
    }
  });
});
