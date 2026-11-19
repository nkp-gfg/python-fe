/**
 * The live itinerary card must show the clock and gate the detail API returns.
 *
 * ItinerarySection is the card PassengerDetailContent renders for
 * passengers[0].itinerary. This renders that function with the segment shape
 * the detail route is required to return. It fails while the card prints only
 * departureDate.
 */
import assert from "node:assert/strict";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

const root = fileURLToPath(new URL("..", import.meta.url));

const sheetUrl = pathToFileURL(
  join(root, "components/dashboard/passenger-detail-sheet.tsx"),
).href;
const { ItinerarySection } = await import(sheetUrl);

const outbound = {
  segmentId: "1",
  airline: "AZ",
  flight: "2071",
  operatingAirline: "",
  operatingFlight: "",
  marketingAirline: "",
  marketingFlight: "",
  bookingClass: "Y",
  marketingBookingClass: "",
  operatingBookingClass: "",
  origin: "LIN",
  destination: "FCO",
  departureDate: "2016-03-16",
  estimatedDepartureTime: "03:00PM",
  departureGate: "12A",
  aircraftType: "E90",
  cabin: "",
  seat: "",
  passengerType: "F",
  priority: "",
  bagCount: 1,
  totalBagWeight: "",
  checkInNumber: "1",
  editCodes: [],
  bagTags: [],
  vcrInfo: [],
  aeDetails: [],
  requiredInfo: [],
};

const connection = {
  ...outbound,
  segmentId: "2",
  flight: "2088",
  origin: "FCO",
  destination: "JFK",
  estimatedDepartureTime: "06:40PM",
  departureGate: "4B",
  bagCount: 0,
};

const html = renderToStaticMarkup(
  createElement(ItinerarySection, { segments: [outbound, connection] }),
);

function cardText(flight) {
  const marker = `>${flight}<`;
  const at = html.indexOf(marker);
  assert.notEqual(at, -1, `missing flight ${flight} in ${html}`);
  const start = html.lastIndexOf("<div", at);
  const end = html.indexOf("</div></div>", at);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return html.slice(start, end).replace(/<[^>]+>/g, " ");
}

const first = cardText("AZ2071");
const second = cardText("AZ2088");

assert.match(first, /2016-03-16/);
assert.match(first, /03:00PM/);
assert.match(first, /12A/);
assert.match(second, /2016-03-16/);
assert.match(second, /06:40PM/);
assert.match(second, /4B/);
assert.doesNotMatch(first, /06:40PM/);
assert.doesNotMatch(first, /4B/);
assert.doesNotMatch(second, /03:00PM/);
assert.doesNotMatch(second, /12A/);

const undated = renderToStaticMarkup(
  createElement(ItinerarySection, {
    segments: [{
      ...outbound,
      estimatedDepartureTime: "",
      departureGate: "",
    }],
  }),
);
assert.match(undated, /2016-03-16/);
assert.doesNotMatch(undated, /03:00PM/);
assert.doesNotMatch(undated, /12A/);

console.log("itinerary card shows estimated departure time and gate");
