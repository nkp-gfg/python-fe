/**
 * Renders PassengerDetailSheet and reads the Class column on the baggage card.
 *
 * The fetch stand-in returns the JSON shape the live detail route must
 * produce: bookingClass, marketingBookingClass, and operatingBookingClass on
 * every baggage route. No other transform runs between that JSON and the
 * card (frontend/lib/api.ts get() returns response.json()).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerDetailSheet } from "@/components/dashboard/passenger-detail-sheet";

type BagRoute = {
  segmentId: string;
  airline: string;
  flight: string;
  operatingAirline: string;
  operatingFlight: string;
  origin: string;
  destination: string;
  departureDate: string;
  departureTime: string;
  arrivalDate: string;
  arrivalTime: string;
  bookingClass: string;
  marketingBookingClass: string;
  operatingBookingClass: string;
  segmentStatus: string;
  lateCheckin: boolean;
  homePrintedBagTag: string;
  bagEmbargo: boolean;
};

function route(partial: Pick<
  BagRoute,
  "segmentId" | "airline" | "flight" | "bookingClass" | "marketingBookingClass" | "operatingBookingClass" | "operatingAirline" | "operatingFlight" | "origin" | "destination"
>): BagRoute {
  return {
    departureDate: "2019-05-29",
    departureTime: "06:45PM",
    arrivalDate: "2019-05-29",
    arrivalTime: "08:20PM",
    segmentStatus: "HK",
    lateCheckin: false,
    homePrintedBagTag: "",
    bagEmbargo: false,
    ...partial,
  };
}

const detailBody = {
  airline: "EY",
  flightNumber: "6418",
  origin: "SYD",
  departureDate: "2019-05-29",
  fetchedAt: "2019-05-29T06:31:55Z",
  status: "Success",
  completionStatus: "Complete",
  errors: [],
  totalPassengers: 1,
  passengers: [
    {
      lineNumber: 1,
      lastName: "HORN",
      firstName: "YVETTE",
      passengerId: "F335D0A70001",
      pnr: "QKZOBC",
      nameAssociationId: "1",
      groupCode: "",
      vcrNumber: "",
      couponNumber: "",
      gender: "",
      docsData: {},
      baggageRoutes: [
        route({
          segmentId: "1",
          airline: "EY",
          flight: "6418",
          operatingAirline: "VA",
          operatingFlight: "880",
          origin: "SYD",
          destination: "MEL",
          bookingClass: "L",
          marketingBookingClass: "L",
          operatingBookingClass: "N",
        }),
        route({
          segmentId: "2",
          airline: "EY",
          flight: "7000",
          operatingAirline: "VA",
          operatingFlight: "100",
          origin: "MEL",
          destination: "SYD",
          bookingClass: "L",
          marketingBookingClass: "M",
          operatingBookingClass: "N",
        }),
        route({
          segmentId: "3",
          airline: "GF",
          flight: "010",
          operatingAirline: "",
          operatingFlight: "",
          origin: "BAH",
          destination: "DXB",
          bookingClass: "Y",
          marketingBookingClass: "",
          operatingBookingClass: "",
        }),
        route({
          segmentId: "4",
          airline: "GF",
          flight: "011",
          operatingAirline: "WY",
          operatingFlight: "222",
          origin: "BAH",
          destination: "MCT",
          bookingClass: "J",
          marketingBookingClass: "",
          operatingBookingClass: "",
        }),
        route({
          segmentId: "5",
          airline: "gf",
          flight: "012",
          operatingAirline: "GF",
          operatingFlight: "012",
          origin: "BAH",
          destination: "DOH",
          bookingClass: "Y",
          marketingBookingClass: "Y",
          operatingBookingClass: "C",
        }),
      ],
      itinerary: [
        {
          segmentId: "1",
          airline: "EY",
          flight: "6418",
          operatingAirline: "VA",
          operatingFlight: "880",
          marketingAirline: "EY",
          marketingFlight: "6418",
          bookingClass: "L",
          marketingBookingClass: "L",
          operatingBookingClass: "N",
          origin: "SYD",
          destination: "MEL",
          departureDate: "2019-05-29",
          aircraftType: "C2C",
          cabin: "Y",
          seat: "",
          passengerType: "F",
          priority: "",
          bagCount: 0,
          totalBagWeight: "",
          checkInNumber: "",
          editCodes: [],
          bagTags: [],
          vcrInfo: [],
          aeDetails: [],
          requiredInfo: [],
        },
      ],
      requiredInfo: [],
      freeText: [],
      passengerEdits: [],
      timaticInfo: [],
    },
  ],
};

function installFetch() {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (!url.includes("/flights/6418/passengers/QKZOBC/detail")) {
      return new Response(JSON.stringify({ detail: "unexpected url" }), { status: 500 });
    }
    return new Response(JSON.stringify(detailBody), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitFor(predicate: () => boolean, label: string) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) return;
    await settle();
  }
  throw new Error(`${label}. body=${document.body.textContent?.slice(0, 500)}`);
}

function classByFlight(): Record<string, string> {
  const panel = [...document.querySelectorAll('[role="tabpanel"]')].find(
    (node) => node.textContent?.includes("Baggage Routing"),
  );
  if (!panel) {
    throw new Error("baggage tabpanel is not mounted");
  }
  const table = panel.querySelector("table");
  if (!table) {
    throw new Error("baggage card table is missing");
  }
  const headers = [...table.querySelectorAll("thead th")].map((cell) =>
    (cell.textContent || "").replace(/\s+/g, " ").trim(),
  );
  const classIndex = headers.findIndex((header) => header.startsWith("Class"));
  if (classIndex < 0) {
    throw new Error(`Class column missing from ${headers.join("|")}`);
  }
  const printed: Record<string, string> = {};
  for (const row of table.querySelectorAll("tbody tr")) {
    const cells = [...row.querySelectorAll("td")].map((cell) =>
      (cell.textContent || "").replace(/\s+/g, " ").trim(),
    );
    printed[cells[0]] = cells[classIndex];
  }
  return printed;
}

export async function runBaggageCardOperatingClassTest() {
  installFetch();
  const host = document.createElement("div");
  document.body.appendChild(host);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  let root: Root | undefined;
  await act(async () => {
    root = createRoot(host);
    root.render(
      <QueryClientProvider client={queryClient}>
        <PassengerDetailSheet
          open
          onOpenChange={() => {}}
          flightNumber="6418"
          origin="SYD"
          date="2019-05-29"
          pnr="QKZOBC"
        />
      </QueryClientProvider>,
    );
  });

  await waitFor(
    () => (document.body.textContent || "").includes("HORN"),
    "passenger detail did not render",
  );

  const baggageTab = [...document.querySelectorAll('[role="tab"]')].find((node) =>
    (node.textContent || "").includes("Baggage"),
  );
  if (!baggageTab) {
    throw new Error("Baggage tab was not rendered");
  }
  await act(async () => {
    baggageTab.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, button: 0, ctrlKey: false }),
    );
  });
  await waitFor(
    () => (document.body.textContent || "").includes("Baggage Routing"),
    "baggage card did not render",
  );

  const printed = classByFlight();
  const expected: Record<string, string> = {
    EY6418: "N",
    EY7000: "N",
    GF010: "Y",
    GF011: "J",
    gf012: "Y",
  };
  const mismatches = Object.entries(expected)
    .filter(([flight, classCode]) => printed[flight] !== classCode)
    .map(([flight, classCode]) => `${flight}: expected ${classCode}, printed ${printed[flight] ?? "(missing row)"}`);
  if (mismatches.length > 0) {
    throw new Error(`baggage card class mismatch: ${mismatches.join("; ")}`);
  }

  await act(async () => {
    root?.unmount();
  });
}
