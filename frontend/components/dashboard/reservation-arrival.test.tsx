/**
 * The Reservations tab must print each Trip Search air leg's stored arrival
 * next to its departure, with a label that says which clock is the arrival.
 *
 * The payload is the JSON produced by the reservations API from the GF2057
 * Mumbai sample (see backend/tests/reservation_arrival_payload.py). This test
 * fails until the segment row renders that arrival time.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReservationView } from "./reservation-view";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

interface AirSegment {
  marketingAirline: string;
  flightNumber: string;
  departureAirport: string;
  arrivalAirport: string;
  departureDate: string;
  arrivalDate: string;
}

interface ReservationPayload {
  reservations: { pnr: string; segments: AirSegment[] }[];
}

function loadReservations(): ReservationPayload {
  const stdout = execFileSync(
    "python3",
    ["-m", "backend.tests.reservation_arrival_payload"],
    {
      cwd: REPO_ROOT,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH: REPO_ROOT },
    },
  );
  return JSON.parse(stdout) as ReservationPayload;
}

function dateAndClockShown(text: string, iso: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (!match) {
    throw new Error(`timestamp is not a date and clock: ${iso}`);
  }
  const [, year, month, day, hour, minute] = match;
  const clock = `${hour}:${minute}`;
  const isoDate = `${year}-${month}-${day}`;
  const pretty = `${day} ${MONTHS[Number(month) - 1]} ${year}`.toLowerCase();
  const lower = text.toLowerCase();
  const hasDate =
    text.includes(isoDate) ||
    lower.includes(pretty) ||
    text.includes(`${day}/${month}/${year}`);
  return hasDate && text.includes(clock);
}

function hasArrivalLabel(text: string): boolean {
  // Adjacent spans concatenate in textContent, so the label may be glued
  // to the previous clock ("00Arrival"). Require the word itself.
  return /arriv(?:al|es|e)\b/i.test(text);
}

function segmentRow(root: HTMLElement, segment: AirSegment): HTMLElement {
  const flight = `${segment.marketingAirline}${segment.flightNumber}`;
  const matches = Array.from(root.querySelectorAll("div")).filter((element) => {
    const text = element.textContent ?? "";
    return (
      text.includes(flight) &&
      text.includes(segment.departureAirport) &&
      text.includes(segment.arrivalAirport) &&
      dateAndClockShown(text, segment.departureDate)
    );
  });
  matches.sort((a, b) => (a.textContent?.length ?? 0) - (b.textContent?.length ?? 0));
  const row = matches[0];
  if (!row) {
    throw new Error(
      `no segment row for ${flight} ${segment.departureAirport}-${segment.arrivalAirport} departing ${segment.departureDate}`,
    );
  }
  return row;
}

describe("reservation segment arrival times", () => {
  it("prints a labeled arrival date and clock on every booked air leg", async () => {
    const payload = loadReservations();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/flights/2057/reservations")) {
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <ReservationView flightNumber="2057" origin="BOM" date="2026-03-18" />
      </QueryClientProvider>,
    );

    await screen.findByText(`${payload.reservations.length} of ${payload.reservations.length} reservations`);
    expect(fetchMock).toHaveBeenCalled();
    const requested = String(fetchMock.mock.calls[0][0]);
    expect(requested).toContain("/flights/2057/reservations");
    expect(requested).toContain("origin=BOM");
    expect(requested).toContain("date=2026-03-18");

    for (const reservation of payload.reservations) {
      fireEvent.click(screen.getByRole("button", { name: new RegExp(reservation.pnr) }));
      const card = screen.getByRole("button", { name: new RegExp(reservation.pnr) }).closest("[data-slot='card']");
      if (!card) {
        throw new Error(`reservation ${reservation.pnr} is not on a card`);
      }
      const scope = within(card as HTMLElement);
      expect(scope.getByText("Segments")).toBeTruthy();

      for (const segment of reservation.segments) {
        const row = segmentRow(card as HTMLElement, segment);
        const text = row.textContent ?? "";
        expect(
          dateAndClockShown(text, segment.arrivalDate),
          `${reservation.pnr} ${segment.marketingAirline}${segment.flightNumber} hides arrival ${segment.arrivalDate}. Row text: ${text}`,
        ).toBe(true);
        expect(
          hasArrivalLabel(text),
          `${reservation.pnr} ${segment.marketingAirline}${segment.flightNumber} shows ${segment.arrivalDate} without an arrival label. Row text: ${text}`,
        ).toBe(true);
        expect(dateAndClockShown(text, segment.departureDate)).toBe(true);
      }
    }
  });
});
