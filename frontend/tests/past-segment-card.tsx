/**
 * The reservation card must label an already-flown leg and keep the confirmed
 * color for the leg that is still current. Rendered through ReservationView,
 * which is what the reservations screen mounts.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReservationView } from "../components/dashboard/reservation-view";

const RESERVATION = {
  airline: "GF",
  flightNumber: "2057",
  departureAirport: "BOM",
  departureDate: "2026-03-18",
  fetchedAt: "2026-03-18T00:00:00Z",
  totalResults: 1,
  reservations: [
    {
      pnr: "ATAMUS",
      numberInParty: 1,
      numberOfInfants: 0,
      createdAt: "2026-02-01T00:00:00Z",
      updatedAt: "2026-03-01T00:00:00Z",
      passengers: [
        {
          lastName: "TEST",
          firstName: "PAX",
          nameId: "01.01",
          nameType: "ADT",
          gender: "",
          dateOfBirth: "",
          nationality: "",
          seatNumber: "",
          frequentFlyerNumber: "",
          frequentFlyerAirline: "",
        },
      ],
      segments: [
        {
          departureAirport: "BAH",
          arrivalAirport: "BLR",
          departureDate: "2026-02-19T14:10:00",
          arrivalDate: "2026-02-19T21:00:00",
          marketingAirline: "GF",
          flightNumber: "0280",
          bookingClass: "W",
          status: "HK",
          isPast: true,
        },
        {
          departureAirport: "BOM",
          arrivalAirport: "DMM",
          departureDate: "2026-03-18T17:40:00",
          arrivalDate: "2026-03-18T20:10:00",
          marketingAirline: "GF",
          flightNumber: "2057",
          bookingClass: "W",
          status: "HK",
          isPast: false,
        },
      ],
      tickets: [],
    },
  ],
};

function segmentRow(flightCode: string): HTMLElement {
  const other = flightCode === "GF0280" ? "GF2057" : "GF0280";
  const matches = [...document.querySelectorAll("div")].filter((element) => {
    const text = element.textContent ?? "";
    return text.includes(flightCode) && !text.includes(other);
  });
  matches.sort(
    (left, right) => (left.textContent?.length ?? 0) - (right.textContent?.length ?? 0),
  );
  const row = matches[0];
  if (!row) {
    throw new Error(`segment row for ${flightCode} was not rendered`);
  }
  return row;
}

function confirmedBadges(row: HTMLElement): Element[] {
  return [...row.querySelectorAll("[data-slot=badge]")].filter((badge) =>
    String(badge.className).includes("text-emerald-600"),
  );
}

export async function run(): Promise<void> {
  const calls: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return new Response(JSON.stringify(RESERVATION), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const host = document.getElementById("root");
  if (!host) {
    throw new Error("test host missing");
  }
  const root: Root = createRoot(host);
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ReservationView flightNumber="2057" origin="BOM" date="2026-03-18" />
      </QueryClientProvider>,
    );
  });

  for (let attempt = 0; attempt < 20 && !document.body.textContent?.includes("ATAMUS"); attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 25));
    });
  }
  const opener = [...document.querySelectorAll("button")].find((button) =>
    button.textContent?.includes("ATAMUS"),
  );
  if (!opener) {
    throw new Error(`reservation card did not render; fetch calls: ${calls.join(", ")}`);
  }
  await act(async () => {
    opener.click();
  });

  const flown = segmentRow("GF0280");
  const current = segmentRow("GF2057");
  const flownText = flown.textContent ?? "";
  const currentText = current.textContent ?? "";

  if (!/already[-\s]flown/i.test(flownText)) {
    throw new Error(
      `flown GF0280 leg is shown without an already-flown label: ${flownText}`,
    );
  }
  if (confirmedBadges(flown).length > 0) {
    throw new Error("flown GF0280 leg is still colored as a confirmed segment");
  }
  if (/already[-\s]flown/i.test(currentText)) {
    throw new Error("current GF2057 leg is labeled already-flown");
  }
  if (confirmedBadges(current).length === 0) {
    throw new Error("current HK leg GF2057 lost its confirmed styling");
  }
  root.unmount();
}
