/**
 * Selecting a departure on Network Analytics must drive the same date the
 * sidebar prints. These tests fail while the page stores that date only for
 * the card and the row highlight, and still requests the open date range.
 *
 * The fetch handler below is the analytics query the API actually runs for the
 * figures this page prints: inclusive departureDate window, then origin,
 * flight number, and (for passengers only) destination. See
 * backend/api/routes/analytics.py _flight_match, _pax_match, _change_match,
 * the passenger/load-factor rollup, the change and upgrade counts, and the
 * daily trend join. Cabin is not applied to those rollups.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { format, subDays } from "date-fns";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AnalyticsPage from "@/app/analytics/page";

const navigation = vi.hoisted(() => ({
  search: new URLSearchParams(),
}));

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next/navigation")>();
  return {
    ...actual,
    useSearchParams: () => navigation.search,
  };
});

const today = new Date();
const older = subDays(today, 10);
const todayIso = format(today, "yyyy-MM-dd");
const olderIso = format(older, "yyyy-MM-dd");

type CabinSummary = { cabin: string; count: number; authorized: number };

type PaxDoc = {
  flightNumber: string;
  origin: string;
  destination: string;
  departureDate: string;
  totalPassengers: number;
  cabinSummary: CabinSummary[];
  fetchedAt: string;
};

type ChangeDoc = {
  flightNumber: string;
  origin: string;
  departureDate: string;
  changeType: string;
};

type FlightDoc = {
  flightNumber: string;
  origin: string;
  departureDate: string;
};

const flights: FlightDoc[] = [
  { flightNumber: "2057", origin: "BAH", departureDate: olderIso },
  { flightNumber: "2057", origin: "BAH", departureDate: todayIso },
  { flightNumber: "9999", origin: "BAH", departureDate: olderIso },
];

const passengers: PaxDoc[] = [
  {
    flightNumber: "2057",
    origin: "BAH",
    destination: "LHR",
    departureDate: olderIso,
    totalPassengers: 40,
    cabinSummary: [{ cabin: "Y", count: 40, authorized: 80 }],
    fetchedAt: "2026-11-01T00:00:00Z",
  },
  {
    flightNumber: "2057",
    origin: "BAH",
    destination: "LHR",
    departureDate: todayIso,
    totalPassengers: 80,
    cabinSummary: [{ cabin: "Y", count: 80, authorized: 100 }],
    fetchedAt: "2026-11-01T00:00:00Z",
  },
  {
    flightNumber: "9999",
    origin: "BAH",
    destination: "DXB",
    departureDate: olderIso,
    totalPassengers: 10,
    cabinSummary: [{ cabin: "Y", count: 10, authorized: 10 }],
    fetchedAt: "2026-11-01T00:00:00Z",
  },
];

const changes: ChangeDoc[] = [
  { flightNumber: "2057", origin: "BAH", departureDate: olderIso, changeType: "CABIN_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: olderIso, changeType: "SEAT_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: olderIso, changeType: "SEAT_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: todayIso, changeType: "CABIN_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: todayIso, changeType: "CABIN_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: todayIso, changeType: "CABIN_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: todayIso, changeType: "CABIN_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: todayIso, changeType: "SEAT_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: todayIso, changeType: "SEAT_CHANGE" },
  { flightNumber: "2057", origin: "BAH", departureDate: todayIso, changeType: "SEAT_CHANGE" },
  { flightNumber: "9999", origin: "BAH", departureDate: olderIso, changeType: "CABIN_CHANGE" },
];

const listItems = flights.map((flight) => ({
  airline: "GF",
  flightNumber: flight.flightNumber,
  origin: flight.origin,
  destination: flight.flightNumber === "9999" ? "DXB" : "LHR",
  departureDate: flight.departureDate,
  status: "SCHEDULED",
  gate: "",
  aircraft: {},
  schedule: {},
  passengerCounts: {},
  jumpSeat: {},
  fetchedAt: "2026-11-01T00:00:00Z",
}));

function round1(value: number): number {
  return Math.round((value + Number.EPSILON) * 10) / 10;
}

function inDateWindow(date: string, params: URLSearchParams): boolean {
  const from = params.get("date_from") ?? "";
  const to = params.get("date_to") ?? "";
  return date >= from && date <= to;
}

function matchesIdentity(
  doc: { origin: string; flightNumber: string },
  params: URLSearchParams,
): boolean {
  const origin = params.get("origin");
  const flightNumber = params.get("flight_number");
  if (origin && doc.origin !== origin) return false;
  if (flightNumber && doc.flightNumber !== flightNumber) return false;
  return true;
}

/** _flight_match / _change_match: date window, origin, flight number. Destination is ignored. */
function matchesFlightOrChange(
  doc: { origin: string; flightNumber: string; departureDate: string },
  params: URLSearchParams,
): boolean {
  return inDateWindow(doc.departureDate, params) && matchesIdentity(doc, params);
}

/** _pax_match: same window plus destination when the request sends one. */
function matchesPassenger(doc: PaxDoc, params: URLSearchParams): boolean {
  if (!matchesFlightOrChange(doc, params)) return false;
  const destination = params.get("destination");
  if (destination && doc.destination !== destination) return false;
  return true;
}

function latestPassengers(params: URLSearchParams): PaxDoc[] {
  const matched = passengers.filter((doc) => matchesPassenger(doc, params));
  const byDeparture = new Map<string, PaxDoc>();
  const ordered = [...matched].sort((a, b) => (a.fetchedAt < b.fetchedAt ? 1 : -1));
  for (const doc of ordered) {
    const key = `${doc.flightNumber}|${doc.origin}|${doc.departureDate}`;
    if (!byDeparture.has(key)) byDeparture.set(key, doc);
  }
  return [...byDeparture.values()];
}

function buildAnalytics(params: URLSearchParams) {
  const matchedPax = latestPassengers(params);
  const matchedFlights = flights.filter((doc) => matchesFlightOrChange(doc, params));
  const matchedChanges = changes.filter((doc) => matchesFlightOrChange(doc, params));

  let economy = 0;
  let business = 0;
  let authorized = 0;
  let booked = 0;
  let totalPassengers = 0;
  for (const doc of matchedPax) {
    totalPassengers += doc.totalPassengers;
    for (const cab of doc.cabinSummary) {
      if (cab.cabin === "Y") {
        economy += cab.count;
        authorized += cab.authorized;
        booked += cab.count;
      } else if (cab.cabin === "J") {
        business += cab.count;
        authorized += cab.authorized;
        booked += cab.count;
      }
    }
  }

  const changeCounts = new Map<string, number>();
  for (const change of matchedChanges) {
    changeCounts.set(change.changeType, (changeCounts.get(change.changeType) ?? 0) + 1);
  }
  const changeDistribution = [...changeCounts.entries()].map(([changeType, count]) => ({
    changeType,
    count,
  }));
  const totalChanges = changeDistribution.reduce((sum, item) => sum + item.count, 0);
  const cabinChanges = changeCounts.get("CABIN_CHANGE") ?? 0;
  const upgradesConfirmed = changeCounts.get("UPGRADE_CONFIRMED") ?? 0;

  const paxByDate = new Map<string, number>();
  for (const doc of matchedPax) {
    paxByDate.set(doc.departureDate, (paxByDate.get(doc.departureDate) ?? 0) + doc.totalPassengers);
  }
  const changesByDate = new Map<string, number>();
  for (const change of matchedChanges) {
    changesByDate.set(change.departureDate, (changesByDate.get(change.departureDate) ?? 0) + 1);
  }
  const flightsByDate = new Map<string, number>();
  for (const flight of matchedFlights) {
    flightsByDate.set(flight.departureDate, (flightsByDate.get(flight.departureDate) ?? 0) + 1);
  }
  const dailyTrends = [...flightsByDate.keys()].sort().map((date) => ({
    date,
    flights: flightsByDate.get(date) ?? 0,
    passengers: paxByDate.get(date) ?? 0,
    changes: changesByDate.get(date) ?? 0,
  }));

  const dates = new Set(matchedFlights.map((flight) => flight.departureDate));

  return {
    dateRange: { from: params.get("date_from"), to: params.get("date_to") },
    filters: {
      origin: params.get("origin"),
      cabin: params.get("cabin"),
      flightNumber: params.get("flight_number"),
    },
    overview: {
      totalFlights: matchedFlights.length,
      uniqueRoutes: new Set(matchedFlights.map((flight) => flight.flightNumber)).size,
      uniqueStations: new Set(matchedFlights.map((flight) => flight.origin)).size,
      daysTracked: dates.size,
      totalPassengers,
      totalSouls: totalPassengers,
      avgPassengersPerFlight: matchedPax.length ? round1(totalPassengers / matchedPax.length) : 0,
      maxPassengersOnFlight: matchedPax.reduce((max, doc) => Math.max(max, doc.totalPassengers), 0),
      economyPassengers: economy,
      businessPassengers: business,
      avgLoadFactor: authorized > 0 ? round1((booked / authorized) * 100) : 0,
    },
    routePerformance: [],
    bookingClassDistribution: [],
    nationalityDistribution: [],
    loyaltyDistribution: [],
    mealDistribution: [],
    wheelchairDistribution: [],
    changeDistribution,
    totalChanges,
    dailyTrends,
    partySizeDistribution: [],
    pointOfSaleDistribution: [],
    operationalRates: {
      totalPassengers: 0,
      checkedIn: 0,
      boarded: 0,
      revenue: 0,
      standby: 0,
      checkInRate: 0,
      boardingRate: 0,
    },
    upgradeAnalytics: { cabinChanges, upgradesConfirmed },
    bookingLeadTime: { sameDay: 0, within7d: 0, within30d: 0, within90d: 0, over90d: 0 },
  };
}

const analyticsCalls: URL[] = [];

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch() {
  analyticsCalls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw);
      if (url.pathname.endsWith("/flights")) return jsonResponse(listItems);
      if (url.pathname.endsWith("/analytics/network")) {
        analyticsCalls.push(url);
        return jsonResponse(buildAnalytics(url.searchParams));
      }
      return new Response("not found", { status: 404 });
    }),
  );
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return render(<AnalyticsPage />, { wrapper: Wrapper });
}

function kpiCard(label: string): HTMLElement {
  const labelNode = screen.getByText(label);
  const card = labelNode.parentElement;
  if (!card) throw new Error(`No KPI card for ${label}`);
  return card;
}

function kpiValue(label: string): string {
  const value = kpiCard(label).querySelector("p");
  if (!value?.textContent) throw new Error(`No KPI value for ${label}`);
  return value.textContent;
}

function selectedCard(): HTMLElement {
  const heading = screen.getByText("Currently Selected");
  const card = heading.parentElement?.parentElement;
  if (!card) throw new Error("Currently Selected card is missing");
  return card;
}

function requestsForFlight(flightNumber: string): URL[] {
  return analyticsCalls.filter((url) => url.searchParams.get("flight_number") === flightNumber);
}

function expectDepartureRequest(url: URL, date: string, cabin: string | null) {
  const params = url.searchParams;
  expect(params.get("date_from"), url.search).toBe(date);
  expect(params.get("date_to"), url.search).toBe(date);
  expect(params.get("origin"), url.search).toBe("BAH");
  expect(params.get("flight_number"), url.search).toBe("2057");
  expect(params.get("destination"), url.search).toBe("LHR");
  expect(params.get("cabin"), url.search).toBe(cabin);
}

async function clickDeparture(date: string) {
  const buttons = screen.getAllByRole("button", { name: /GF2057/ });
  const button = buttons.find((candidate) => candidate.textContent?.includes(date));
  if (!button) throw new Error(`No GF2057 row for ${date}`);
  await userEvent.click(button);
}

describe("analytics selected departure", () => {
  beforeEach(() => {
    navigation.search = new URLSearchParams();
    installFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps passenger, load-factor, upgrade, change, and trend figures on the clicked departure", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("button", { name: new RegExp(`GF2057[\\s\\S]*${olderIso}`) });
    await user.click(screen.getByRole("button", { name: "Economy" }));
    await clickDeparture(olderIso);

    await waitFor(() => {
      expect(selectedCard().textContent ?? "").toContain(olderIso);
      expect(requestsForFlight("2057").length).toBeGreaterThan(0);
    });

    const olderRequest = requestsForFlight("2057").at(-1)!;
    expectDepartureRequest(olderRequest, olderIso, "Y");

    await waitFor(() => {
      expect(kpiValue("Total Passengers")).toBe("40");
      expect(kpiValue("Avg Load Factor")).toBe("50%");
      expect(kpiValue("Upgrades")).toBe("1");
      expect(kpiValue("Total Changes")).toBe("3");
      expect(kpiCard("Days Tracked").textContent ?? "").toContain(`${olderIso} → ${olderIso}`);
    });

    const trend = screen.getByTestId("daily-trend");
    expect(JSON.parse(trend.getAttribute("data-x") ?? "[]")).toEqual([olderIso.slice(5)]);
    expect(JSON.parse(trend.getAttribute("data-series") ?? "[]")).toEqual([
      { name: "Passengers", data: [40] },
      { name: "Flights", data: [10] },
      { name: "Changes", data: [3] },
    ]);

    await clickDeparture(todayIso);

    await waitFor(() => {
      expect(selectedCard().textContent ?? "").toContain(todayIso);
      const latest = requestsForFlight("2057").at(-1)!;
      expectDepartureRequest(latest, todayIso, "Y");
      expect(kpiValue("Total Passengers")).toBe("80");
      expect(kpiValue("Avg Load Factor")).toBe("80%");
      expect(kpiValue("Upgrades")).toBe("4");
      expect(kpiValue("Total Changes")).toBe("7");
    });

    const todayTrend = screen.getByTestId("daily-trend");
    expect(JSON.parse(todayTrend.getAttribute("data-x") ?? "[]")).toEqual([todayIso.slice(5)]);
    expect(JSON.parse(todayTrend.getAttribute("data-series") ?? "[]")).toEqual([
      { name: "Passengers", data: [80] },
      { name: "Flights", data: [10] },
      { name: "Changes", data: [7] },
    ]);

  });

  it("does not widen a selected departure when the open range is moved across another day", async () => {
    navigation.search = new URLSearchParams({
      flight: "2057",
      origin: "BAH",
      destination: "LHR",
      date: todayIso,
    });
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(selectedCard().textContent ?? "").toContain(todayIso);
      expect(kpiValue("Total Passengers")).toBe("80");
      const latest = requestsForFlight("2057").at(-1)!;
      expectDepartureRequest(latest, todayIso, null);
    });

    const rangeLabel = screen.getByText("Date Range");
    const rangeButtons = rangeLabel.parentElement?.querySelectorAll("button");
    if (!rangeButtons || rangeButtons.length < 1) throw new Error("Date range controls are missing");
    await user.click(rangeButtons[0]);

    const day = document.querySelector(`[data-day="${olderIso}"] button`);
    if (!(day instanceof HTMLElement)) throw new Error(`No calendar day for ${olderIso}`);
    const callsBefore = analyticsCalls.length;
    await user.click(day);

    await waitFor(() => {
      const fromText = rangeButtons[0].textContent ?? "";
      const rangeMoved = fromText.includes(format(older, "MMM d"));
      const refetched = analyticsCalls.length > callsBefore;
      expect(rangeMoved || refetched, `range label "${fromText}", calls ${analyticsCalls.length}`).toBe(true);
      const cardVisible = screen.queryByText("Currently Selected");
      const latest = analyticsCalls.at(-1)!;
      if (cardVisible) {
        const cardText = selectedCard().textContent ?? "";
        const shownDate = cardText.includes(todayIso) ? todayIso : cardText.includes(olderIso) ? olderIso : "";
        expect(shownDate, cardText).not.toBe("");
        expect(latest.searchParams.get("date_from"), latest.search).toBe(shownDate);
        expect(latest.searchParams.get("date_to"), latest.search).toBe(shownDate);
        expect(kpiValue("Total Passengers")).toBe(shownDate === todayIso ? "80" : "40");
        expect(kpiValue("Avg Load Factor")).toBe(shownDate === todayIso ? "80%" : "50%");
        expect(kpiValue("Upgrades")).toBe(shownDate === todayIso ? "4" : "1");
        expect(kpiValue("Total Changes")).toBe(shownDate === todayIso ? "7" : "3");
      } else {
        expect(latest.searchParams.get("date_from"), latest.search).toBe(olderIso);
        expect(latest.searchParams.get("date_to"), latest.search).toBe(todayIso);
        const expected = buildAnalytics(latest.searchParams);
        expect(kpiValue("Total Passengers")).toBe(String(expected.overview.totalPassengers));
        expect(kpiValue("Total Changes")).toBe(String(expected.totalChanges));
      }
    });
  });
});
