import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PassengerDetailSheet } from "@/components/dashboard/passenger-detail-sheet";

/**
 * The live passenger detail baggage tab must warn on the route Sabre marked,
 * even when that route's booking segment status is still HK.
 *
 * The payload is the converter contract from
 * backend/tests/test_baggage_route_warnings.py:
 * lateCheckin boolean, bagEmbargo integer code, shortCheckinReason text.
 */

const SHORT_REASON = "Unauthorized baggage route";

function route(overrides: Record<string, unknown>) {
  return {
    segmentId: "1",
    airline: "GF",
    flight: "12",
    operatingAirline: "",
    operatingFlight: "",
    origin: "BAH",
    destination: "CDG",
    departureDate: "2026-03-18",
    departureTime: "",
    arrivalDate: "",
    arrivalTime: "",
    bookingClass: "Y",
    segmentStatus: "HK",
    lateCheckin: false,
    homePrintedBagTag: "",
    bagEmbargo: 0,
    shortCheckinReason: "",
    ...overrides,
  };
}

const payload = {
  airline: "GF",
  flightNumber: "2057",
  origin: "BAH",
  departureDate: "2026-03-18",
  fetchedAt: "2026-03-18T00:00:00Z",
  status: "Success",
  completionStatus: "Complete",
  errors: [],
  totalPassengers: 1,
  passengers: [
    {
      lineNumber: 1,
      lastName: "ALNAHAR",
      firstName: "NOOR",
      passengerId: "PAX0001",
      pnr: "ABC123",
      nameAssociationId: "1",
      groupCode: "",
      vcrNumber: "",
      couponNumber: "",
      gender: "",
      docsData: {},
      itinerary: [],
      requiredInfo: [],
      freeText: [],
      passengerEdits: [],
      timaticInfo: [],
      baggageRoutes: [
        route({
          segmentId: "1",
          flight: "2057",
          origin: "BAH",
          destination: "LHR",
          lateCheckin: true,
          bagEmbargo: 0,
          shortCheckinReason: "",
        }),
        route({
          segmentId: "2",
          flight: "632",
          origin: "BAH",
          destination: "MCT",
          lateCheckin: false,
          bagEmbargo: 2,
          shortCheckinReason: "",
        }),
        route({
          segmentId: "4",
          flight: "942",
          origin: "MCT",
          destination: "DXB",
          lateCheckin: false,
          bagEmbargo: 0,
          shortCheckinReason: SHORT_REASON,
        }),
        route({
          segmentId: "6",
          flight: "12",
          origin: "BAH",
          destination: "CDG",
          lateCheckin: false,
          bagEmbargo: 0,
          shortCheckinReason: "",
        }),
      ],
    },
  ],
};

function showsEmbargoCode(text: string, code: number): boolean {
  const tail = new RegExp(`embargo[^\\d]{0,24}${code}(?!\\d)`, "i");
  const head = new RegExp(`(?<!\\d)${code}(?!\\d)[^\\d]{0,24}embargo`, "i");
  return tail.test(text) || head.test(text);
}

function renderSheet() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PassengerDetailSheet
        open
        onOpenChange={() => {}}
        flightNumber="2057"
        origin="BAH"
        date="2026-03-18"
        pnr="ABC123"
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("live passenger baggage card", () => {
  it("warns for late check-in, a non-zero embargo code, and a short-check reason while HK stays confirmed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (!url.includes("/flights/2057/passengers/ABC123/detail")) {
          throw new Error(`unexpected request ${url}`);
        }
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => payload,
        };
      }),
    );

    const user = userEvent.setup();
    renderSheet();

    await user.click(await screen.findByRole("tab", { name: "Baggage" }));

    const late = await screen.findByRole("row", { name: /GF2057/ });
    const embargo = screen.getByRole("row", { name: /GF632/ });
    const short = screen.getByRole("row", { name: /GF942/ });
    const clear = screen.getByRole("row", { name: /GF12/ });

    expect(late.textContent).toMatch(/HK/);
    expect(late.textContent).toMatch(/late check-?\s?in/i);
    expect(late.textContent ?? "").not.toMatch(/embargo/i);
    expect(late.textContent).not.toContain(SHORT_REASON);

    expect(embargo.textContent).toMatch(/HK/);
    expect(showsEmbargoCode(embargo.textContent ?? "", 2)).toBe(true);
    expect(embargo.textContent ?? "").not.toMatch(/late check-?\s?in/i);
    expect(embargo.textContent).not.toContain(SHORT_REASON);

    expect(short.textContent).toMatch(/HK/);
    expect(short.textContent).toContain(SHORT_REASON);
    expect(short.textContent ?? "").not.toMatch(/late check-?\s?in/i);
    expect(short.textContent ?? "").not.toMatch(/embargo/i);

    expect(clear.textContent).toMatch(/HK/);
    expect(clear.textContent ?? "").not.toMatch(/late check-?\s?in/i);
    expect(clear.textContent ?? "").not.toMatch(/embargo/i);
    expect(clear.textContent).not.toContain(SHORT_REASON);
  });
});
