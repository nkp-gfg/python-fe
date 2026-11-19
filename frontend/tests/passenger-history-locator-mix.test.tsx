/**
 * History for one traveler on a shared record locator.
 *
 * The manifest row for Sara Ali and the row for Ahmed Ali share locator
 * SHARE1. Selecting Sara must ask the timeline and the detail routes for
 * both her last name and her first name, and the History card must not show
 * a current cabin, seat, check-in, or boarded flag until the payload names
 * Sara.
 *
 * Run from frontend/ after `npm ci` and
 * `npm install --no-save jsdom @testing-library/react @testing-library/dom tsx`:
 *   node --import ./tests/register-dom.mjs ./node_modules/tsx/dist/cli.mjs --test --test-force-exit ./tests/passenger-history-locator-mix.test.tsx
 */
import "./register-dom.mjs";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PassengerTable } from "@/components/dashboard/passenger-table";
import { BottomDetailPanel } from "@/components/dashboard/bottom-detail-panel";
import { PassengerDetailSheet } from "@/components/dashboard/passenger-detail-sheet";
import { PassengerTimeline } from "@/components/dashboard/passenger-timeline";
import type { FilterCabin, FilterLoyalty, FilterNationality, FilterStatus, FilterType } from "@/components/dashboard/passenger-table";
import type { FlightDashboard, PassengerRecord } from "@/lib/types";

const FLIGHT = "072";
const ORIGIN = "BAH";
const DATE = "2026-04-01";
const LOCATOR = "SHARE1";

function traveler(partial: Pick<PassengerRecord, "lastName" | "firstName" | "seat" | "cabin" | "bookingClass" | "isCheckedIn" | "isBoarded" | "bagCount">): PassengerRecord {
  return {
    pnr: LOCATOR,
    passengerId: `${partial.lastName}-${partial.firstName}`,
    lineNumber: 1,
    priorityCode: "",
    desiredBookingClass: "",
    destination: "LHR",
    passengerType: "F",
    isStandby: false,
    corpId: "",
    seniorityDate: "",
    boardingPassIssued: false,
    checkInSequence: 0,
    isRevenue: true,
    isThru: false,
    isChild: false,
    hasInfant: false,
    vcrType: "",
    ticketNumber: "",
    editCodes: [],
    nationality: "BH",
    ...partial,
  };
}

const AHMED_ALI = traveler({
  lastName: "ALI", firstName: "AHMED", seat: "12A", cabin: "J", bookingClass: "C",
  isCheckedIn: true, isBoarded: true, bagCount: 2,
});
const SARA_ALI = traveler({
  lastName: "ALI", firstName: "SARA", seat: "14C", cabin: "Y", bookingClass: "V",
  isCheckedIn: false, isBoarded: false, bagCount: 0,
});
const AHMED_HASSAN = traveler({
  lastName: "HASSAN", firstName: "AHMED", seat: "2F", cabin: "F", bookingClass: "F",
  isCheckedIn: true, isBoarded: false, bagCount: 1,
});

function detailFor(lastName: string, firstName: string) {
  return {
    airline: "GF",
    flightNumber: FLIGHT,
    origin: ORIGIN,
    departureDate: DATE,
    fetchedAt: "2026-04-01T11:00:00Z",
    status: "OK",
    completionStatus: "Complete",
    errors: [],
    totalPassengers: 1,
    passengers: [{
      lineNumber: 1,
      lastName,
      firstName,
      passengerId: firstName,
      pnr: LOCATOR,
      nameAssociationId: "",
      groupCode: "",
      vcrNumber: "",
      couponNumber: "",
      gender: "",
      docsData: {},
      baggageRoutes: [],
      itinerary: [],
      requiredInfo: [],
      freeText: [],
      passengerEdits: [],
      timaticInfo: [],
    }],
  };
}

function timelineFor(lastName: string, firstName: string, state: Record<string, unknown>, events: { changeType: string; category: string; description: string }[]) {
  return {
    flightNumber: FLIGHT,
    pnr: LOCATOR,
    departureDate: DATE,
    origin: ORIGIN,
    passenger: { lastName, firstName },
    originalBooking: { cabin: state.cabin, bookingClass: state.bookingClass },
    currentState: state,
    eventCount: events.length,
    events: events.map((event, index) => ({
      timestamp: `2026-04-01T08:0${index}:00Z`,
      changeType: event.changeType,
      category: event.category,
      description: event.description,
      passenger: { lastName, firstName },
      details: {},
    })),
  };
}

const SARA_TIMELINE = timelineFor("ALI", "SARA", {
  cabin: "Y", bookingClass: "V", seat: "14C", isCheckedIn: false, isBoarded: false, bagCount: 0,
}, [
  { changeType: "PASSENGER_ADDED", category: "booking", description: "Passenger added to manifest" },
  { changeType: "SEAT_CHANGE", category: "seat", description: "Seat changed: unassigned → 14C" },
]);

const AHMED_TIMELINE = timelineFor("ALI", "AHMED", {
  cabin: "J", bookingClass: "C", seat: "12A", isCheckedIn: true, isBoarded: true, bagCount: 2,
}, [
  { changeType: "PASSENGER_ADDED", category: "booking", description: "Passenger added to manifest" },
  { changeType: "CHECKED_IN", category: "checkin", description: "Passenger checked in" },
  { changeType: "BOARDED", category: "boarding", description: "Passenger boarded aircraft" },
]);

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch() {
  const requests: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    requests.push(url);
    const parsed = new URL(url);
    if (parsed.pathname.endsWith("/audit")) {
      return json({
        flightNumber: FLIGHT,
        origin: ORIGIN,
        departureDate: DATE,
        fetchedAt: "",
        alerts: [],
        summary: {},
        totalAlerts: 0,
        passengerAlerts: {},
      });
    }
    if (parsed.pathname.endsWith("/passengers")) {
      return json({
        flightNumber: FLIGHT,
        origin: ORIGIN,
        departureDate: DATE,
        fetchedAt: "",
        passengers: [AHMED_ALI, SARA_ALI, AHMED_HASSAN],
      });
    }
    if (parsed.pathname.endsWith("/detail")) {
      const last = parsed.searchParams.get("last_name");
      const first = parsed.searchParams.get("first_name");
      if (last === "ALI" && first === "SARA") return json(detailFor("ALI", "SARA"));
      return json(detailFor("ALI", "AHMED"));
    }
    if (parsed.pathname.endsWith("/timeline")) {
      const last = parsed.searchParams.get("last_name");
      const first = parsed.searchParams.get("first_name");
      if (last === "ALI" && first === "SARA") return json(SARA_TIMELINE);
      return json(AHMED_TIMELINE);
    }
    return json({});
  }) as typeof fetch;
  return requests;
}

afterEach(() => {
  cleanup();
});

function withClient(children: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function TableHarness({ onSelect }: { onSelect: (...args: string[]) => void }) {
  const [filterCabin, setFilterCabin] = useState<FilterCabin>("all");
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("all");
  const [filterType, setFilterType] = useState<FilterType>("all");
  const [filterLoyalty, setFilterLoyalty] = useState<FilterLoyalty>("all");
  const [filterNationality, setFilterNationality] = useState<FilterNationality>("all");
  return (
    <PassengerTable
      flightNumber={FLIGHT}
      origin={ORIGIN}
      date={DATE}
      onSelectPassenger={onSelect}
      filterCabin={filterCabin}
      setFilterCabin={setFilterCabin}
      filterStatus={filterStatus}
      setFilterStatus={setFilterStatus}
      filterType={filterType}
      setFilterType={setFilterType}
      filterLoyalty={filterLoyalty}
      setFilterLoyalty={setFilterLoyalty}
      filterNationality={filterNationality}
      setFilterNationality={setFilterNationality}
    />
  );
}

test("selecting the second traveler keeps that person's name", async () => {
  installFetch();
  const calls: string[][] = [];
  render(withClient(<TableHarness onSelect={(...args) => calls.push(args)} />));
  const sara = await screen.findByText("ALI, SARA");
  fireEvent.click(sara.closest("tr") as HTMLElement);
  assert.deepEqual(calls, [["SHARE1", "ALI", "SARA"]]);
});

test("the manifest detail list keeps the second traveler's name", async () => {
  installFetch();
  const calls: string[][] = [];
  render(withClient(
    <BottomDetailPanel
      view="records"
      flightNumber={FLIGHT}
      origin={ORIGIN}
      date={DATE}
      dashboard={{} as FlightDashboard}
      onClose={() => {}}
      onSelectPassenger={(...args: string[]) => calls.push(args)}
    />,
  ));
  const sara = await screen.findByText("ALI, SARA");
  fireEvent.click(sara.closest("tr") as HTMLElement);
  assert.deepEqual(calls, [["SHARE1", "ALI", "SARA"]]);
});

test("history hides another traveler's current status until the payload matches", async () => {
  const requests = installFetch();
  render(withClient(
    <PassengerTimeline
      flightNumber={FLIGHT}
      pnr={LOCATOR}
      origin={ORIGIN}
      date={DATE}
      lastName="ALI"
      firstName="NOBODY"
    />,
  ));
  await waitFor(() => {
    assert.ok(requests.some((url) => url.includes("/timeline")));
  });
  const timelineUrl = requests.find((url) => url.includes("/timeline")) as string;
  const params = new URL(timelineUrl).searchParams;
  assert.equal(params.get("last_name"), "ALI");
  assert.equal(params.get("first_name"), "NOBODY");
  // The locator-only API answers with Ahmed Ali, who is boarded in 12A.
  // That card must not render for a request that named somebody else.
  assert.equal(screen.queryByText("Current Status"), null);
  assert.equal(screen.queryByText("12A"), null);
});

test("history for Sara shows her seat and names her events", async () => {
  const requests = installFetch();
  render(withClient(
    <PassengerDetailSheet
      open
      onOpenChange={() => {}}
      flightNumber={FLIGHT}
      origin={ORIGIN}
      date={DATE}
      pnr={LOCATOR}
      lastName="ALI"
      firstName="SARA"
    />,
  ));

  await screen.findByRole("heading", { name: /ALI,/ });
  const shown = document.body.textContent || "";
  assert.match(shown, /ALI,\s*SARA/);
  assert.doesNotMatch(shown, /ALI,\s*AHMED/);

  const detailUrl = requests.find((url) => url.includes("/detail")) as string;
  const detailParams = new URL(detailUrl).searchParams;
  assert.equal(detailParams.get("last_name"), "ALI");
  assert.equal(detailParams.get("first_name"), "SARA");

  fireEvent.click(screen.getByRole("tab", { name: /history/i }));

  await waitFor(() => {
    assert.ok(requests.some((url) => url.includes("/timeline")));
  });
  const timelineUrl = requests.find((url) => url.includes("/timeline")) as string;
  const params = new URL(timelineUrl).searchParams;
  assert.equal(params.get("last_name"), "ALI");
  assert.equal(params.get("first_name"), "SARA");
  assert.equal(params.get("origin"), ORIGIN);
  assert.equal(params.get("date"), DATE);

  await waitFor(() => {
    assert.match(document.body.textContent || "", /Current Status/);
  });
  const text = document.body.textContent || "";
  assert.match(text, /14C/);
  assert.match(text, /Not Checked In/);
  assert.doesNotMatch(text, /12A/);
  assert.doesNotMatch(text, /Boarded/);

  const history = screen.getByText("Event History").closest("[data-slot=card]");
  const historyText = history?.textContent || "";
  assert.match(historyText, /SARA/);
  assert.match(historyText, /ALI/);
  assert.match(historyText, /Passenger added to manifest/);
  assert.doesNotMatch(historyText, /Passenger boarded aircraft/);
  assert.doesNotMatch(historyText, /Passenger checked in/);
});
