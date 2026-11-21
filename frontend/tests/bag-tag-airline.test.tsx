/**
 * The passenger sheet prints each bag tag on its own line. That line must
 * name the carrier that issued the tag, which can differ from the itinerary
 * flight. Fails while the line renders only the tag number, weight, and cities.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createRoot } from "react-dom/client";
import { act } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerDetailSheet } from "../components/dashboard/passenger-detail-sheet";

const FLIGHT_NUMBER = "1";
const PNR = "VWLGVM";
const ORIGIN = "AUH";
const DATE = "2018-05-05";

const detail = {
  airline: "GF",
  flightNumber: FLIGHT_NUMBER,
  origin: ORIGIN,
  departureDate: DATE,
  fetchedAt: "2018-05-04T20:41:07Z",
  status: "Success",
  completionStatus: "Complete",
  errors: [],
  totalPassengers: 1,
  passengers: [
    {
      lineNumber: 1,
      lastName: "TEST",
      firstName: "PAX",
      passengerId: "DB15B93A0101",
      pnr: PNR,
      nameAssociationId: "1",
      groupCode: "",
      vcrNumber: "",
      couponNumber: "",
      gender: "",
      docsData: {},
      baggageRoutes: [],
      requiredInfo: [],
      freeText: [],
      passengerEdits: [],
      timaticInfo: [],
      itinerary: [
        {
          segmentId: "2",
          airline: "EY",
          flight: "1",
          operatingAirline: "EY",
          operatingFlight: "1",
          marketingAirline: "EY",
          marketingFlight: "1",
          bookingClass: "Y",
          marketingBookingClass: "",
          operatingBookingClass: "",
          origin: "AUH",
          destination: "FRA",
          departureDate: "2018-05-05",
          aircraftType: "789",
          cabin: "Y",
          seat: "17D",
          passengerType: "F",
          priority: "",
          bagCount: 2,
          totalBagWeight: "10",
          checkInNumber: "1",
          editCodes: [],
          vcrInfo: [],
          aeDetails: [],
          requiredInfo: [],
          bagTags: [
            {
              bagTagNumber: "0061464375",
              airline: "HM",
              weight: "",
              unit: "",
              origin: "AUH",
              destination: "FRA",
            },
            {
              bagTagNumber: "0061999001",
              airline: "LH",
              weight: "",
              unit: "",
              origin: "FRA",
              destination: "LHR",
            },
          ],
        },
      ],
    },
  ],
};

function bagTagLabels(tagNumber: string): string[] {
  const node = Array.from(document.querySelectorAll("span")).find(
    (element) => (element.textContent ?? "").trim() === tagNumber,
  );
  assert.ok(node, `bag tag ${tagNumber} was not rendered`);
  const line = node.closest("div");
  assert.ok(line);
  return Array.from(line.querySelectorAll("span"))
    .map((element) => (element.textContent ?? "").replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function showsCarrier(labels: string[], carrier: string): boolean {
  return labels.some((label) => label.split(" ").includes(carrier));
}

test("passenger sheet shows the issuing carrier next to each bag tag", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  queryClient.setQueryData(
    ["passengerDetail", FLIGHT_NUMBER, PNR, ORIGIN, DATE],
    detail,
  );

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <PassengerDetailSheet
          open
          onOpenChange={() => {}}
          flightNumber={FLIGHT_NUMBER}
          origin={ORIGIN}
          date={DATE}
          pnr={PNR}
        />
      </QueryClientProvider>,
    );
  });

  const hmLabels = bagTagLabels("0061464375");
  const lhLabels = bagTagLabels("0061999001");
  assert.equal(showsCarrier(hmLabels, "HM"), true, hmLabels.join(" | "));
  assert.equal(showsCarrier(lhLabels, "LH"), true, lhLabels.join(" | "));
  // The line must not substitute the itinerary carrier for the tag carrier.
  assert.equal(showsCarrier(hmLabels, "EY"), false, hmLabels.join(" | "));
  assert.equal(showsCarrier(lhLabels, "EY"), false, lhLabels.join(" | "));
  assert.equal(showsCarrier(hmLabels, "LH"), false, hmLabels.join(" | "));
  assert.equal(showsCarrier(lhLabels, "HM"), false, lhLabels.join(" | "));

  await act(async () => {
    root.unmount();
  });
  container.remove();
});
