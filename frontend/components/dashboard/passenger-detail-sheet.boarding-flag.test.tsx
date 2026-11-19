/**
 * The live passenger sheet's itinerary panel must show whether each segment
 * is boarded.
 *
 * The detail payload uses the same segment contract as the live detail API:
 * boardingPassFlag "*" is checked in and not boarded, "-" is boarded, and an
 * empty flag is neither. The panel already shows seat, cabin, and check-in
 * requirements. Each segment card must also show:
 *
 * - "Checked in, not boarded" when boardingPassFlag is "*"
 * - "Boarded" when boardingPassFlag is "-" (and not the not-boarded sentence)
 * - neither label when the flag is empty
 *
 * The workbench opens this sheet for one record locator. The sheet renders
 * passengers[0], so this test uses that passenger. The Python tests cover
 * every passenger the route returns.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PassengerDetailSheet } from "./passenger-detail-sheet";

function segment(overrides: Record<string, unknown>) {
  return {
    segmentId: "1",
    airline: "AZ",
    flight: "4",
    operatingAirline: "",
    operatingFlight: "",
    marketingAirline: "",
    marketingFlight: "",
    bookingClass: "Y",
    marketingBookingClass: "",
    operatingBookingClass: "",
    origin: "FCO",
    destination: "TRN",
    departureDate: "2016-03-16",
    aircraftType: "32S",
    cabin: "Y",
    seat: "42C",
    passengerType: "F",
    priority: "",
    bagCount: 0,
    totalBagWeight: "",
    checkInNumber: "1",
    editCodes: [],
    bagTags: [],
    vcrInfo: [],
    aeDetails: [],
    requiredInfo: [],
    boardingPassFlag: "",
    isCheckedIn: false,
    isBoarded: false,
    ...overrides,
  };
}

const detail = {
  airline: "AZ",
  flightNumber: "4",
  origin: "FCO",
  departureDate: "2016-03-16",
  fetchedAt: "2016-03-16T13:07:59Z",
  status: "Success",
  completionStatus: "Complete",
  errors: [],
  totalPassengers: 1,
  passengers: [
    {
      lineNumber: 1,
      lastName: "STEVE",
      firstName: "J",
      passengerId: "B784C60F0001",
      pnr: "OJLCOK",
      nameAssociationId: "1",
      groupCode: "",
      vcrNumber: "0552120002468",
      couponNumber: "1",
      gender: "F",
      docsData: {},
      baggageRoutes: [],
      requiredInfo: [],
      freeText: [],
      passengerEdits: [],
      timaticInfo: [],
      itinerary: [
        segment({
          segmentId: "1",
          seat: "42C",
          cabin: "Y",
          boardingPassFlag: "*",
          isCheckedIn: true,
          isBoarded: false,
          requiredInfo: [
            { code: "DOCS", detailStatus: "OK", freeText: "passport" },
          ],
        }),
        segment({
          segmentId: "2",
          flight: "9",
          origin: "TRN",
          destination: "FCO",
          seat: "2A",
          cabin: "C",
          boardingPassFlag: "-",
          isCheckedIn: true,
          isBoarded: true,
        }),
        segment({
          segmentId: "3",
          flight: "11",
          seat: "10B",
          cabin: "Y",
          boardingPassFlag: "",
          isCheckedIn: false,
          isBoarded: false,
        }),
      ],
    },
  ],
};

function cardForSeat(seat: string): HTMLElement {
  const node = screen.getByText(seat);
  const card = node.closest("div.rounded-md");
  if (!card) {
    throw new Error(`segment card for seat ${seat} was not rendered`);
  }
  return card as HTMLElement;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("live passenger itinerary boarded state", () => {
  it("shows checked-in and boarded state on each segment card", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/passengers/OJLCOK/detail")) {
          return new Response(JSON.stringify(detail), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ events: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <PassengerDetailSheet
          open
          onOpenChange={() => {}}
          flightNumber="4"
          origin="FCO"
          date="2016-03-16"
          pnr="OJLCOK"
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("42C")).toBeTruthy();
    expect(screen.getByText("2A")).toBeTruthy();
    expect(screen.getByText("10B")).toBeTruthy();
    expect(screen.getByText(/Check-In Requirements/)).toBeTruthy();

    const checkedIn = cardForSeat("42C");
    const boarded = cardForSeat("2A");
    const unmarked = cardForSeat("10B");

    expect(checkedIn.textContent).toContain("Checked in, not boarded");
    expect(checkedIn.textContent).toContain("42C");
    expect(checkedIn.textContent).toContain("Y");
    expect(boarded.textContent).toContain("Boarded");
    expect(boarded.textContent).not.toContain("not boarded");
    expect(unmarked.textContent).not.toContain("Checked in, not boarded");
    expect(unmarked.textContent).not.toContain("Boarded");
  });
});
