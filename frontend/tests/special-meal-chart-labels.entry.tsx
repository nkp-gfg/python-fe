/**
 * Renders the network analytics page with the meal distribution the analytics
 * API returns for Sabre codes NLML, LSML, and an unknown code.
 *
 * The Special Meal Requests chart must label NLML as non-lactose, keep LSML
 * as the low-sodium meal, show each raw Sabre code beside its name, and leave
 * an unknown code visible. This fails while NLML is drawn as "No Salt".
 */
import { act } from "react";
import { createRoot } from "react-dom/client";

import { Providers } from "@/components/providers";
import { capturedChartOptions as captured } from "./stubs/jsx-runtime";

type Bar = { label: string; value: number };

const MEAL_DISTRIBUTION = [
  { mealCode: "NLML", count: 4 },
  { mealCode: "LSML", count: 2 },
  { mealCode: "ZZML", count: 1 },
];

function analyticsPayload() {
  return {
    dateRange: { from: "2099-11-21", to: "2099-11-21" },
    filters: { origin: null, cabin: null, flightNumber: null },
    overview: {
      totalFlights: 1,
      uniqueRoutes: 1,
      uniqueStations: 1,
      daysTracked: 1,
      totalPassengers: 8,
      totalSouls: 8,
      avgPassengersPerFlight: 8,
      maxPassengersOnFlight: 8,
      economyPassengers: 8,
      businessPassengers: 0,
      avgLoadFactor: 0,
    },
    routePerformance: [],
    bookingClassDistribution: [],
    nationalityDistribution: [],
    loyaltyDistribution: [],
    mealDistribution: MEAL_DISTRIBUTION,
    wheelchairDistribution: [],
    changeDistribution: [],
    totalChanges: 0,
    dailyTrends: [],
    partySizeDistribution: [],
    pointOfSaleDistribution: [],
    operationalRates: {
      totalPassengers: 8,
      checkedIn: 0,
      boarded: 0,
      revenue: 0,
      standby: 0,
      checkInRate: 0,
      boardingRate: 0,
    },
    upgradeAnalytics: { cabinChanges: 0, upgradesConfirmed: 0 },
    bookingLeadTime: { sameDay: 0, within7d: 0, within30d: 0, within90d: 0, over90d: 0 },
  };
}

function jsonResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
  };
}

globalThis.fetch = (async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.includes("/analytics/network")) return jsonResponse(analyticsPayload());
  if (url.includes("/flights")) return jsonResponse([]);
  throw new Error(`unexpected request ${url}`);
}) as typeof fetch;

const pageModule = await import("@/app/analytics/page");
const AnalyticsPage = pageModule.default;

function barsFrom(option: (typeof captured)[number]): Bar[] {
  const labels = (option.yAxis?.data ?? []).map((item) => String(item));
  const seriesData = option.series?.[0]?.data;
  if (!Array.isArray(seriesData)) return [];
  const values = seriesData.map((item) =>
    typeof item === "object" && item !== null && "value" in item
      ? Number((item as { value: number }).value)
      : Number(item),
  );
  return labels.map((label, index) => ({ label, value: values[index] }));
}

function fail(message: string): never {
  throw new Error(message);
}

const rootElement = document.getElementById("root");
if (!rootElement) fail("test page is missing #root");

const root = createRoot(rootElement);
await act(async () => {
  root.render(
    <Providers>
      <AnalyticsPage />
    </Providers>,
  );
});

let mealBars: Bar[] | null = null;
for (let attempt = 0; attempt < 40 && !mealBars; attempt += 1) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 25));
  });
  for (const option of captured) {
    const pairs = barsFrom(option);
    const counts = new Set(pairs.map((pair) => pair.value));
    if (counts.has(4) && counts.has(2) && counts.has(1)) mealBars = pairs;
  }
}

if (!mealBars) {
  const text = document.body?.textContent?.replace(/\s+/g, " ").slice(0, 500) ?? "";
  fail(
    "Special Meal Requests chart did not receive the NLML, LSML, and unknown-code counts. Captured axes: "
      + JSON.stringify(captured.map((option) => option.yAxis?.data))
      + ` Page text: ${text}`,
  );
}

function barWithCount(count: number): Bar {
  const found = mealBars!.filter((pair) => pair.value === count);
  if (found.length !== 1) {
    fail(`expected one Special Meal Requests bar with count ${count}, saw ${JSON.stringify(mealBars)}`);
  }
  return found[0];
}

const nonLactose = barWithCount(4);
const lowSodium = barWithCount(2);
const unknown = barWithCount(1);

if (!nonLactose.label.includes("NLML")) {
  fail(`NLML requests are labeled ${JSON.stringify(nonLactose.label)} without the Sabre code`);
}
if (!/non[-\s]?lactose/i.test(nonLactose.label)) {
  fail(`NLML requests are labeled ${JSON.stringify(nonLactose.label)} instead of a non-lactose meal`);
}
if (/salt/i.test(nonLactose.label)) {
  fail(`NLML requests are labeled as salt-restricted: ${JSON.stringify(nonLactose.label)}`);
}
if (!lowSodium.label.includes("LSML")) {
  fail(`LSML requests are labeled ${JSON.stringify(lowSodium.label)} without the Sabre code`);
}
if (!/low[\s-]?(sodium|salt)/i.test(lowSodium.label)) {
  fail(`LSML requests are labeled ${JSON.stringify(lowSodium.label)} instead of a low-sodium meal`);
}
if (nonLactose.label === lowSodium.label) {
  fail("NLML and LSML were drawn as the same meal");
}
if (!unknown.label.includes("ZZML")) {
  fail(`an unknown Sabre meal code was renamed to ${JSON.stringify(unknown.label)}`);
}

const visibleTotal = nonLactose.value + lowSodium.value + unknown.value;
if (visibleTotal !== 7) {
  fail(`special-meal counts were dropped or merged: ${JSON.stringify(mealBars)}`);
}

console.log("PASS special meal chart labels");
console.log(JSON.stringify(mealBars));
