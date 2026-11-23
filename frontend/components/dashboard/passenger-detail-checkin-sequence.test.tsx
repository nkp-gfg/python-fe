import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerDetailSheet } from "./passenger-detail-sheet";
import type { PassengerDetailResponse } from "@/lib/types";

/**
 * The live passenger detail sheet must print Sabre's per-segment check-in
 * sequence. These tests fail while the itinerary row omits checkInNumber.
 *
 * The payload is the passenger detail route's JSON. Parsing goes through
 * SabreClient._parse_xml and _extract_body, then convert_passenger_data,
 * which is what GET /flights/{flight}/passengers/{pnr}/detail returns.
 */

interface LiveDetails {
  bothTravelers: {
    response: PassengerDetailResponse;
    sabreRequest: { last_name?: string; pnr?: string; flight_number?: string };
  };
  richardsOnly: {
    response: PassengerDetailResponse;
    sabreRequest: { last_name?: string; pnr?: string };
  };
}

function loadLiveDetails(): LiveDetails {
  const script = path.resolve(
    import.meta.dirname,
    "../../../backend/tests/checkin_sequence_detail.py",
  );
  const destination = path.join(tmpdir(), "falconeye-checkin-sequence-detail.json");
  const result = spawnSync("python3", [script, destination], { encoding: "utf-8" });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || "detail route helper failed");
  }
  const payload = JSON.parse(readFileSync(destination, "utf-8")) as LiveDetails;
  return payload;
}

let responseBody: PassengerDetailResponse;

function installFetch() {
  const fetchImpl = async (input: RequestInfo | URL) => {
    const url = String(input);
    if (!url.includes("/passengers/") || !url.includes("/detail")) {
      throw new Error(`unexpected request ${url}`);
    }
    return new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  vi.stubGlobal("fetch", fetchImpl);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderSheet(pnr: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PassengerDetailSheet
        open
        onOpenChange={() => {}}
        flightNumber="2071"
        origin="LIN"
        date="2016-03-16"
        pnr={pnr}
      />
    </QueryClientProvider>,
  );
}

function segmentRow(seat: string) {
  const seatNode = screen.getByText(seat);
  const row = seatNode.closest("div.rounded-md.border");
  expect(row, `itinerary row for seat ${seat}`).not.toBeNull();
  return row as HTMLElement;
}

function rowText(row: HTMLElement): string {
  return row.textContent ?? "";
}

function sequencePattern(sequence: string) {
  return new RegExp(`Check-in sequence:?\\s*#?\\s*${sequence}(?!\\d)`);
}

function expectCheckInSequence(seat: string, departureDate: string, sequence: string) {
  const row = segmentRow(seat);
  expect(rowText(row)).toContain(`Departs: ${departureDate}`);
  expect(rowText(row)).toMatch(sequencePattern(sequence));
}

function expectNoCheckInSequence(seat: string, departureDate: string) {
  const row = segmentRow(seat);
  expect(rowText(row)).toContain(`Departs: ${departureDate}`);
  expect(rowText(row)).not.toMatch(/Check-in sequence/i);
}

test("live itinerary prints each Sabre check-in sequence beside the departure date and seat", async () => {
  const details = loadLiveDetails();
  const jose = details.bothTravelers.response.passengers[0];
  const richardsInSameResponse = details.bothTravelers.response.passengers[1];

  expect(details.bothTravelers.sabreRequest.last_name).toBe("JOSE");
  expect(details.bothTravelers.sabreRequest.pnr).toBe("GFLARH");
  expect(details.bothTravelers.sabreRequest.flight_number).toBe("2071");
  expect(jose.lineNumber).toBe(9);
  expect(jose.itinerary.map((segment) => segment.checkInNumber)).toEqual(["1", "15", ""]);
  expect(jose.itinerary[0]).toMatchObject({
    airline: "AZ",
    flight: "2071",
    origin: "LIN",
    destination: "FCO",
    departureDate: "2016-03-16",
    seat: "12A",
  });
  expect(richardsInSameResponse.itinerary[0].checkInNumber).toBe("2");
  expect(details.richardsOnly.sabreRequest.last_name).toBe("RICHARDS");
  expect(details.richardsOnly.response.passengers[0].itinerary[0].checkInNumber).toBe("2");
  expect(details.richardsOnly.response.passengers[0].lineNumber).toBe(2);

  responseBody = details.bothTravelers.response;
  installFetch();
  renderSheet("GFLARH");

  await waitFor(() => {
    expect(screen.getByText("JOSE, P")).toBeTruthy();
  });

  const itinerary = segmentRow("12A").closest("[data-slot='card']") as HTMLElement;
  expect(within(itinerary).getByText("AZ2071")).toBeTruthy();
  expect(rowText(itinerary)).toContain("LIN");
  expect(rowText(itinerary)).toContain("FCO");
  expect(rowText(itinerary)).toContain("Class: Y");

  expectNoCheckInSequence("1A", "2016-03-18");
  expectCheckInSequence("12A", "2016-03-16", "1");
  expectCheckInSequence("14C", "2016-03-17", "15");
  expect(rowText(segmentRow("12A"))).not.toMatch(sequencePattern("9"));

  cleanup();
  responseBody = details.richardsOnly.response;
  installFetch();
  renderSheet("GFLARH");

  await waitFor(() => {
    expect(screen.getByText("RICHARDS, V")).toBeTruthy();
  });
  expectCheckInSequence("12B", "2016-03-16", "2");
});
