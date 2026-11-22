/**
 * Comparison columns must survive a status-only dashboard payload.
 *
 * The dashboard returns HTTP 200 with `analysis: {}` when flight status exists
 * and the passenger list does not. ComparisonColumn reads
 * `analysis.economy.passengers` as soon as that response arrives, and
 * app/compare/error.tsx replaces the whole route when the column throws.
 *
 * These tests fail while that throw escapes the column. After the fix, a column
 * with no economy or business breakdown shows the same "Failed to load" state
 * used for a request error, and the other selected flight stays on the page.
 * The route error screen ("Comparison error") must not replace the page.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ComparePage from "./page";
import CompareError from "./error";

let search = "";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search),
  usePathname: () => "/compare",
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {} }),
}));

const HEALTHY_SOULS = 42;

const flightList = [
  {
    airline: "GF",
    flightNumber: "2006",
    origin: "LHR",
    destination: "BAH",
    departureDate: "2026-03-19",
    status: "OPEN",
    operationalSummary: { soulsOnBoard: 0 },
  },
  {
    airline: "GF",
    flightNumber: "2057",
    origin: "BAH",
    destination: "DXB",
    departureDate: "2026-03-20",
    status: "OPEN",
    operationalSummary: { soulsOnBoard: 0 },
  },
];

function healthyDashboard() {
  return {
    flightStatus: { status: "OPEN", aircraft: { type: "789" }, gate: "B4" },
    route: { origin: "LHR", destination: "BAH", departureDate: "2026-03-19" },
    passengerSummary: {
      totalPassengers: 1,
      adultCount: 1,
      childCount: 0,
      infantCount: 0,
      totalSouls: 1,
      cabinSummary: [],
    },
    analysis: {
      economy: {
        total: 1,
        passengers: { total: 1, male: 1, female: 0, children: 0, infants: 0 },
        staff: { total: 0, male: 0, female: 0 },
      },
      business: {
        total: 0,
        passengers: { total: 0, male: 0, female: 0, children: 0, infants: 0 },
        staff: { total: 0, male: 0, female: 0 },
      },
      revenue: 1,
      totalMale: 1,
      totalFemale: 0,
      totalChildren: 0,
    },
    overview: {
      soulsOnBoard: HEALTHY_SOULS,
      economySouls: 1,
      businessSouls: 0,
      manifestRecords: 1,
      totalSouls: 1,
      trackedChanges: 0,
    },
    stateSummary: {
      boarded: { totalPassengers: 0 },
      checkedIn: { totalPassengers: 1 },
      booked: { totalPassengers: 0 },
      others: { nonRevenue: 0 },
    },
    emptyManifest: false,
  };
}

/** Shape returned by get_flight_dashboard when flight status exists and passenger_list does not. */
function statusOnlyDashboard() {
  return {
    flightStatus: {
      status: "OPEN",
      aircraft: { type: "32N" },
      gate: "A1",
      origin: "BAH",
      departureDate: "2026-03-20",
    },
    route: { origin: "BAH", destination: "DXB", departureDate: "2026-03-20" },
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
    stateSummary: {
      booked: { totalPassengers: 0 },
      checkedIn: { totalPassengers: 0 },
      boarded: { totalPassengers: 0 },
      others: { nonRevenue: 0, jumpSeat: 0 },
    },
    tree: null,
    flightPhase: { phase: "SCHEDULED", label: "Scheduled" },
  };
}

function emptyManifestDashboard() {
  return {
    ...statusOnlyDashboard(),
    analysis: null,
    emptyManifest: true,
  };
}

class RouteErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return <CompareError error={this.state.error} reset={() => {}} />;
    }
    return this.props.children;
  }
}

function renderCompare() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <RouteErrorBoundary>
      <QueryClientProvider client={client}>
        <ComparePage />
      </QueryClientProvider>
    </RouteErrorBoundary>,
  );
}

async function expectBothFlightsStayOnThePage() {
  await waitFor(() => {
    expect(screen.queryByRole("heading", { name: "Comparison error" })).toBeNull();
    expect(screen.getByText("Failed to load")).toBeTruthy();
    expect(screen.getByText(String(HEALTHY_SOULS))).toBeTruthy();
    expect(screen.getByText("GF2006")).toBeTruthy();
  });
}

beforeEach(() => {
  search = "";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      if (url.includes("/flights/2057/dashboard")) {
        const variant = (globalThis as { __statusVariant?: string }).__statusVariant;
        if (variant === "empty-flag") return json(emptyManifestDashboard());
        if (variant === "missing-business") {
          const body = healthyDashboard();
          delete (body.analysis as { business?: unknown }).business;
          return json(body);
        }
        if (variant === "missing-passengers") {
          const body = healthyDashboard();
          body.analysis.economy = { total: 1, staff: { total: 0, male: 0, female: 0 } } as never;
          return json(body);
        }
        if (variant === "missing-staff") {
          const body = healthyDashboard();
          delete (body.analysis.economy as { staff?: unknown }).staff;
          return json(body);
        }
        return json(statusOnlyDashboard());
      }
      if (url.includes("/flights/2006/dashboard")) return json(healthyDashboard());
      if (url.includes("/tree")) {
        return json({
          title: "Aircraft Humans Breakdown Tree",
          badge: "Sabre Live",
          width: 940,
          height: 600,
          nodes: [],
          edges: [],
          statusCards: [],
        });
      }
      if (url.includes("/flights")) return json(flightList);
      return json({ detail: "not found" }, 404);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete (globalThis as { __statusVariant?: string }).__statusVariant;
});

describe("status-only comparison column", () => {
  it("keeps the other flight when the query string includes a status-only flight", async () => {
    search = "flights=2057:BAH:2026-03-20,2006:LHR:2026-03-19";
    renderCompare();
    await expectBothFlightsStayOnThePage();
  });

  it("keeps the other flight when a status-only flight is chosen from the list", async () => {
    renderCompare();
    fireEvent.click(await screen.findByRole("button", { name: /GF2006/ }));
    fireEvent.click(screen.getByRole("button", { name: /GF2057/ }));
    await expectBothFlightsStayOnThePage();
  });

  it("treats an explicit empty manifest as a failed column", async () => {
    (globalThis as { __statusVariant?: string }).__statusVariant = "empty-flag";
    search = "flights=2057:BAH:2026-03-20,2006:LHR:2026-03-19";
    renderCompare();
    await expectBothFlightsStayOnThePage();
  });

  it("treats a missing business breakdown as a failed column", async () => {
    (globalThis as { __statusVariant?: string }).__statusVariant = "missing-business";
    search = "flights=2057:BAH:2026-03-20,2006:LHR:2026-03-19";
    renderCompare();
    await expectBothFlightsStayOnThePage();
  });

  it("treats economy without a passenger breakdown as a failed column", async () => {
    (globalThis as { __statusVariant?: string }).__statusVariant = "missing-passengers";
    search = "flights=2057:BAH:2026-03-20,2006:LHR:2026-03-19";
    renderCompare();
    await expectBothFlightsStayOnThePage();
  });

  it("treats economy without a staff breakdown as a failed column", async () => {
    (globalThis as { __statusVariant?: string }).__statusVariant = "missing-staff";
    search = "flights=2057:BAH:2026-03-20,2006:LHR:2026-03-19";
    renderCompare();
    await expectBothFlightsStayOnThePage();
  });
});
