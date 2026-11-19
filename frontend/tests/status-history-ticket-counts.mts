/**
 * Status history must show paper and electronic ticket counts beside booked.
 *
 * The component loads GET /flights/{flight}/status/history through fetchStatusHistory.
 * The payload below is that response after ingestion has kept the cabin counts.
 * Rendering fails until the history badges and the booked total show both counts.
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToString } from "react-dom/server";

const require = createRequire(import.meta.url);

process.env.NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000";

const historyRecord = {
  airline: "GF",
  flightNumber: "2274",
  origin: "DMM",
  departureDate: "2026-03-19",
  fetchedAt: "2026-03-19T13:54:54Z",
  status: "FINAL",
  gate: "20",
  terminal: "1",
  aircraft: { type: "321", registration: "A9CXA" },
  schedule: {
    scheduledDeparture: "2026-03-19T16:15",
    estimatedDeparture: "2026-03-19T16:15",
    scheduledArrival: "2026-03-19T23:35",
    estimatedArrival: "2026-03-19T23:35",
    durationMinutes: 290,
  },
  boarding: { time: "15:35", indicator: "BDG" },
  legs: [],
  jumpSeat: { cockpit: 2, cabin: 2, cockpitInUse: false, cabinInUse: false },
  passengerCounts: {
    Y: {
      authorized: 180,
      booked: 180,
      available: 0,
      thru: 0,
      local: 180,
      onBoard: 180,
      boardingPasses: 180,
      meals: 0,
      revenue: 180,
      nonRevenue: 0,
      paperTicket: 180,
      electronicTicket: 0,
    },
    J: {
      authorized: 12,
      booked: 12,
      available: 0,
      thru: 0,
      local: 12,
      onBoard: 12,
      boardingPasses: 12,
      meals: 0,
      revenue: 12,
      nonRevenue: 0,
      paperTicket: 12,
      electronicTicket: 0,
    },
  },
};

const requested: string[] = [];

globalThis.fetch = (async (input: RequestInfo | URL) => {
  const url = String(input);
  requested.push(url);
  if (url.includes("/status/history")) {
    return new Response(JSON.stringify([historyRecord]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (url.includes("/snapshots")) {
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
}) as typeof fetch;

function visibleText(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function assertLabeledCount(section: string, label: RegExp, value: number) {
  const name = `(?:${label.source})`;
  const forward = new RegExp(`${name}\\D{0,24}${value}\\b`, "i");
  const backward = new RegExp(`\\b${value}\\D{0,24}${name}`, "i");
  assert.ok(
    forward.test(section) || backward.test(section),
    `expected ${label} ${value} in: ${section}`,
  );
}

// StatusHistory's compiled import and an ESM import of this package bind
// different module instances. The provider has to be the same instance the
// component calls useQuery from.
const { QueryClient, QueryClientProvider } = require("@tanstack/react-query");
const { fetchStatusHistory, fetchSnapshots } = await import("../lib/api.ts");
const { StatusHistory } = await import("../components/dashboard/status-history.tsx");

const flightNumber = "2274";
const origin = "DMM";
const date = "2026-03-19";
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

await queryClient.prefetchQuery({
  queryKey: ["statusHistory", flightNumber, origin, date],
  queryFn: () => fetchStatusHistory(flightNumber, origin, date),
});
await queryClient.prefetchQuery({
  queryKey: ["snapshots", flightNumber, origin, date],
  queryFn: () => fetchSnapshots(flightNumber, origin, date, "passenger_list"),
});

const html = renderToString(
  createElement(
    QueryClientProvider,
    { client: queryClient },
    createElement(StatusHistory, { flightNumber, origin, date }),
  ),
);
const text = visibleText(html);

assert.ok(
  requested.some((url) => url.includes("/flights/2274/status/history")),
  `status history was not loaded, requests: ${requested.join(", ")}`,
);
assert.equal(text.includes("Loading flight history"), false, text);

const economyStart = text.indexOf("Y");
const businessStart = text.indexOf("J");
const totalStart = text.toLowerCase().indexOf("total:");
assert.ok(economyStart >= 0 && businessStart > economyStart && totalStart > businessStart, text);

const economy = text.slice(economyStart, businessStart);
const business = text.slice(businessStart, totalStart);
const total = text.slice(totalStart);

assertLabeledCount(economy, /paper/, 180);
assertLabeledCount(economy, /electronic/, 0);
assertLabeledCount(economy, /bkd|booked/, 180);
assertLabeledCount(business, /paper/, 12);
assertLabeledCount(business, /electronic/, 0);
assertLabeledCount(business, /bkd|booked/, 12);
assertLabeledCount(total, /paper/, 192);
assertLabeledCount(total, /electronic/, 0);
assertLabeledCount(total, /booked/, 192);

console.log("PASS: status history shows paper and electronic ticket counts");
