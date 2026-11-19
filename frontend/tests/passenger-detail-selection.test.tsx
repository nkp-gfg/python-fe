/**
 * The workbench opens live passenger detail for the traveler who was clicked.
 *
 * A shared record locator is not an identity. Clicks in the passenger table,
 * the bottom manifest, the group-member list, and an audit alert must send
 * that traveler's passenger id, and the detail sheet must render that
 * traveler when the Sabre payload lists someone else first.
 *
 * These tests fail while those clicks keep only the record locator and the
 * sheet always reads passengers[0].
 *
 * Run from frontend/:
 *
 *   NODE_OPTIONS='--import ./tests/dom-setup.mjs' ./node_modules/.bin/tsx --tsconfig tests/tsconfig.json --test tests/passenger-detail-selection.test.tsx
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { fetchPassengerDetail } from "@/lib/api";
import { AuditPanel } from "@/components/dashboard/audit-panel";
import { BottomDetailPanel } from "@/components/dashboard/bottom-detail-panel";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { PassengerTable } from "@/components/dashboard/passenger-table";
import { PassengerDetailSheet } from "@/components/dashboard/passenger-detail-sheet";
import { ToastProvider } from "@/components/ui/toast";
import type { FlightDashboard } from "@/lib/types";

const PNR = "ABCPQR";
const SARA_ID = "HALESARA0002";
const IDRIS_ID = "HALEIDRIS001";

const idris = traveler("IDRIS", IDRIS_ID, "14A", "100000001", "0000000000001", "DOCS OK FOR IDRIS HALE");
const sara = traveler("SARA", SARA_ID, "14B", "200000002", "0000000000002", "DOCS FAILED FOR SARA HALE");
const tarek = traveler("TAREK", "HALETAREK003", "14C", "300000003", "0000000000003", "DOCS OK FOR TAREK HALE");

function traveler(
  firstName: string,
  passengerId: string,
  seat: string,
  documentNumber: string,
  vcr: string,
  requirement: string,
) {
  return { firstName, passengerId, seat, documentNumber, vcr, requirement, lastName: "HALE" };
}

function manifestRow(person: ReturnType<typeof traveler>) {
  return {
    passengerId: person.passengerId,
    lastName: person.lastName,
    firstName: person.firstName,
    pnr: PNR,
    seat: person.seat,
    cabin: "Y",
    bookingClass: "Y",
    bagCount: 0,
    isBoarded: false,
    isCheckedIn: false,
    isRevenue: true,
    isChild: false,
    hasInfant: false,
    editCodes: [] as string[],
    nationality: "BH",
  };
}

function livePassenger(person: ReturnType<typeof traveler>) {
  return {
    lineNumber: 1,
    lastName: person.lastName,
    firstName: person.firstName,
    passengerId: person.passengerId,
    pnr: PNR,
    nameAssociationId: "1",
    groupCode: "AD3",
    vcrNumber: person.vcr,
    couponNumber: "",
    gender: person.firstName === "SARA" ? "F" : "M",
    docsData: {
      documentType: "P",
      documentNumber: person.documentNumber,
      nationality: "BH",
      lastName: person.lastName,
      firstName: person.firstName,
    },
    baggageRoutes: [],
    itinerary: [
      {
        segmentId: "1",
        airline: "GF",
        flight: "2057",
        operatingAirline: "GF",
        operatingFlight: "2057",
        marketingAirline: "GF",
        marketingFlight: "2057",
        bookingClass: "Y",
        marketingBookingClass: "Y",
        operatingBookingClass: "Y",
        origin: "LHR",
        destination: "BAH",
        departureDate: "2026-03-19",
        aircraftType: "320",
        cabin: "Y",
        seat: person.seat,
        passengerType: "F",
        priority: "",
        bagCount: 0,
        totalBagWeight: "0",
        checkInNumber: "",
        editCodes: [],
        bagTags: [],
        vcrInfo: [],
        aeDetails: [],
        requiredInfo: [
          { code: "DOCS", detailStatus: "ValidationRequired", freeText: person.requirement },
        ],
      },
    ],
    requiredInfo: [
      { code: "DOCS", detailStatus: "ValidationRequired", freeText: person.requirement },
    ],
    freeText: [],
    passengerEdits: [],
    timaticInfo: [],
  };
}

const detailPayload = {
  airline: "GF",
  flightNumber: "2057",
  origin: "LHR",
  departureDate: "2026-03-19",
  fetchedAt: "2026-03-19T09:00:00Z",
  status: "Success",
  completionStatus: "Complete",
  errors: [],
  totalPassengers: 3,
  passengers: [livePassenger(idris), livePassenger(sara), livePassenger(tarek)],
};

const passengerList = {
  flightNumber: "2057",
  origin: "LHR",
  departureDate: "2026-03-19",
  passengers: [manifestRow(idris), manifestRow(sara), manifestRow(tarek)],
};

const auditPayload = {
  flightNumber: "2057",
  origin: "LHR",
  departureDate: "2026-03-19",
  fetchedAt: "2026-03-19T09:00:00Z",
  totalAlerts: 2,
  summary: { critical: 0, warning: 2, info: 0 },
  passengerAlerts: {},
  alerts: [
    {
      ruleId: "BOARDED_WITHOUT_DOCS",
      severity: "warning",
      message: "Boarded passenger missing DOCS",
      pnr: PNR,
      passengerName: "HALE/IDRIS",
      passengerId: IDRIS_ID,
    },
    {
      ruleId: "BOARDED_WITHOUT_DOCS",
      severity: "warning",
      message: "Boarded passenger missing DOCS",
      pnr: PNR,
      passengerName: "HALE/SARA",
      passengerId: SARA_ID,
    },
  ],
};

const flight = {
  airline: "GF",
  flightNumber: "2057",
  origin: "LHR",
  destination: "BAH",
  departureDate: "2026-03-19",
  status: "CHECK_IN",
  flightPhase: { phase: "CHECK_IN", label: "Check-in" },
};

const dashboard = {
  flightNumber: "2057",
  origin: "LHR",
  overview: { soulsOnBoard: 0, totalSouls: 3, manifestRecords: 3, trackedChanges: 0 },
  groupBookingSummary: {
    totalGroups: 1,
    totalGroupPassengers: 3,
    totalUnnamed: 0,
    totalNamed: 3,
    groups: [
      {
        groupCode: "AD3",
        pnr: PNR,
        cabin: "Y",
        bookingClass: "Y",
        totalMembers: 3,
        namedMembers: 3,
        unnamedMembers: 0,
        checkedIn: 0,
        boarded: 0,
        members: [idris, sara, tarek].map((person, index) => ({
          lastName: person.lastName,
          firstName: person.firstName,
          pnr: PNR,
          passengerId: person.passengerId,
          lineNumber: index + 1,
          isCheckedIn: false,
          isBoarded: false,
          isUnnamed: false,
          seat: person.seat,
        })),
      },
    ],
  },
};

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function installFetch() {
  const calls: string[] = [];
  globalThis.fetch = async (input: RequestInfo | URL) => {
    const url = requestUrl(input);
    calls.push(url);
    let body: unknown = {};
    if (url.includes("/detail")) body = detailPayload;
    else if (url.includes("/dashboard")) body = dashboard;
    else if (url.includes("/tree")) body = {};
    else if (url.includes("/audit")) body = auditPayload;
    else if (url.includes("/passengers")) body = passengerList;
    else if (url.includes("/flights")) body = [flight];
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  return calls;
}

function mount(node: ReactNode) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  });
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <ToastProvider>{node}</ToastProvider>
      </QueryClientProvider>,
    );
  });
  return {
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

async function settle(times = 1) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
}

async function waitFor(check: () => void) {
  let last: unknown;
  for (let i = 0; i < 40; i += 1) {
    try {
      check();
      return;
    } catch (error) {
      last = error;
      await settle();
    }
  }
  throw last;
}

function smallest(selector: string, text: string): Element {
  const found = [...document.querySelectorAll(selector)].filter((element) =>
    (element.textContent || "").includes(text),
  );
  found.sort((a, b) => (a.textContent || "").length - (b.textContent || "").length);
  assert.ok(found.length > 0, `nothing matching ${selector} includes ${text}`);
  return found[0];
}

async function click(element: Element) {
  await act(async () => {
    element.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

function detailRequest(calls: string[]): string {
  const url = calls.find((item) => item.includes("/detail"));
  assert.ok(url, `no detail request in ${calls.join("\n")}`);
  return url;
}

const tableFilters = {
  filterCabin: "all" as const,
  setFilterCabin: () => {},
  filterStatus: "all" as const,
  setFilterStatus: () => {},
  filterType: "all" as const,
  setFilterType: () => {},
  filterLoyalty: "all" as const,
  setFilterLoyalty: () => {},
  filterNationality: "all",
  setFilterNationality: () => {},
};

test("fetchPassengerDetail sends the selected passenger id", async () => {
  const calls = installFetch();
  await (fetchPassengerDetail as (...args: unknown[]) => Promise<unknown>)(
    "2057",
    PNR,
    "LHR",
    "2026-03-19",
    "GF",
    SARA_ID,
  );
  const url = detailRequest(calls);
  assert.match(url, new RegExp(`passenger_id=${SARA_ID}`));
});

test("detail sheet shows the selected traveler when another person is first", async () => {
  const calls = installFetch();
  const view = mount(
    <PassengerDetailSheet
      {...{
        open: true,
        onOpenChange: () => {},
        flightNumber: "2057",
        origin: "LHR",
        date: "2026-03-19",
        pnr: PNR,
        passengerId: SARA_ID,
      }}
    />,
  );
  try {
    await waitFor(() => {
      const url = detailRequest(calls);
      assert.match(url, new RegExp(`passenger_id=${SARA_ID}`));
      const heading = document.querySelector("h3")?.textContent || "";
      assert.match(heading, /SARA/);
      assert.doesNotMatch(heading, /IDRIS/);
      assert.match(document.body.textContent || "", /DOCS FAILED FOR SARA HALE/);
    });
  } finally {
    view.unmount();
  }
});

test("passenger table click identifies the clicked traveler", async () => {
  installFetch();
  const calls: unknown[][] = [];
  const view = mount(
    <PassengerTable
      flightNumber="2057"
      origin="LHR"
      date="2026-03-19"
      onSelectPassenger={(...args) => {
        calls.push(args);
      }}
      {...tableFilters}
    />,
  );
  try {
    await waitFor(() => {
      assert.match(document.body.textContent || "", /HALE, SARA/);
    });
    await click(smallest("tr", "HALE, SARA"));
    assert.match(JSON.stringify(calls), new RegExp(SARA_ID));
  } finally {
    view.unmount();
  }
});

test("bottom manifest click identifies the clicked traveler", async () => {
  installFetch();
  const calls: unknown[][] = [];
  const view = mount(
    <BottomDetailPanel
      view="records"
      flightNumber="2057"
      origin="LHR"
      date="2026-03-19"
      dashboard={{} as FlightDashboard}
      onClose={() => {}}
      onSelectPassenger={(...args) => {
        calls.push(args);
      }}
    />,
  );
  try {
    await waitFor(() => {
      assert.match(document.body.textContent || "", /HALE, SARA/);
    });
    await click(smallest("tr", "HALE, SARA"));
    assert.match(JSON.stringify(calls), new RegExp(SARA_ID));
  } finally {
    view.unmount();
  }
});

test("audit alert click identifies the named traveler", async () => {
  installFetch();
  const calls: unknown[][] = [];
  const view = mount(
    <AuditPanel
      flightNumber="2057"
      origin="LHR"
      date="2026-03-19"
      onSelectPassenger={(...args) => {
        calls.push(args);
      }}
    />,
  );
  try {
    await waitFor(() => {
      assert.match(document.body.textContent || "", /HALE\/SARA/);
    });
    const link = [...document.querySelectorAll("[role=link]")].find((element) =>
      element.parentElement?.textContent?.includes("SARA"),
    );
    assert.ok(link, "SARA alert link was not rendered");
    await click(link);
    assert.match(JSON.stringify(calls), new RegExp(SARA_ID));
  } finally {
    view.unmount();
  }
});

test("group member click opens that traveler's live detail", async () => {
  const calls = installFetch();
  const view = mount(
    <FlightWorkbench
      initialSelection={{ flightNumber: "2057", origin: "LHR", date: "2026-03-19" }}
    />,
  );
  try {
    await waitFor(() => {
      assert.match(document.body.textContent || "", /Groups/);
    });
    await click(smallest("button", "Groups"));
    await waitFor(() => {
      assert.match(document.body.textContent || "", /AD3/);
    });
    await click(smallest("button", "AD3"));
    await waitFor(() => {
      assert.match(document.body.textContent || "", /HALE, SARA/);
    });
    await click(smallest("button", "HALE, SARA"));
    await waitFor(() => {
      const url = detailRequest(calls);
      assert.match(url, new RegExp(`passenger_id=${SARA_ID}`));
      const heading = document.querySelector("h3")?.textContent || "";
      assert.match(heading, /SARA/);
      assert.doesNotMatch(heading, /IDRIS/);
      assert.match(document.body.textContent || "", /DOCS FAILED FOR SARA HALE/);
    });
  } finally {
    view.unmount();
  }
});
