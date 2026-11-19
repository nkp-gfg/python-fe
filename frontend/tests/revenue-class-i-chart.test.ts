/**
 * Rendered revenue charts must count Sabre booking class I as business and
 * must keep every other booking class in the grouped totals.
 *
 * The GF2006 mix is produced by the backend parse (SabreClient XML parse,
 * convert_passenger_list, _build_dashboard_payload). These assertions fail
 * until the chart classifiers stop dropping class I.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { createElement, type ReactElement } from "react";

import { CommercialInsightsTab } from "@/components/dashboard/flight-insight-tabs";
import { DecisionBriefing } from "@/components/dashboard/decision-briefing";
import { RevenueTab } from "@/components/dashboard/revenue-tab";
import type { FlightDashboard, FlightInsights } from "@/lib/types";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

type Mix = Record<string, number>;

type Gf2006Case = {
  revenueClassMix: Mix;
  classICount: number;
  manifestBusiness: number;
  passengerCount: number;
};

function loadGf2006(): Gf2006Case {
  const stdout = execFileSync(
    process.env.PYTHON ?? "python3",
    [path.join(repoRoot, "backend/tests/test_revenue_class_i_chart.py"), "--json"],
    {
      cwd: repoRoot,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH: repoRoot },
    },
  );
  const line = stdout
    .split("\n")
    .map((entry) => entry.trim())
    .reverse()
    .find((entry) => entry.startsWith("GF2006_JSON:"));
  assert.ok(line, `backend case did not print GF2006_JSON:\n${stdout}`);
  return JSON.parse(line.slice("GF2006_JSON:".length)) as Gf2006Case;
}

function insightsFor(mix: Mix, upgradeTotal = 0): FlightInsights {
  const zero = { count: 0, pct: 0 };
  return {
    connectingPassengers: { connecting: 0, local: 0, connectingPct: 0 },
    bookingChannels: {
      channels: {},
      categories: { online: 0, agent: 0, corporate: 0, other: 0 },
    },
    paymentMethods: {},
    documentCompliance: {
      DOCS: { count: 0, pct: 100 },
      DOCV: { count: 0, pct: 100 },
      DOCA: { count: 0, pct: 100 },
    },
    checkInSequence: { total: 0, earliest: 0, latest: 0, gaps: [] },
    bookingLeadTime: null,
    seatOccupancy: { seated: 0, unseated: 0, seatPct: 100 },
    baggage: { withBags: 0, totalBags: 0, dataAvailablePct: 100 },
    editCodes: { codes: {}, total: 0 },
    multiSegment: { count: 0, pct: 0 },
    pnrPartySize: { average: 0, largest: 0, distribution: {} },
    infantTracking: { total: 0, details: [] },
    wheelchairTypes: {},
    mealCodes: {},
    boardingRate: {
      boarded: 0,
      checkedIn: 0,
      notCheckedIn: 0,
      boardedPct: 100,
      checkedInPct: 100,
    },
    changeVelocity: { totalChanges: 0, changeTypes: {} },
    revenueClassMix: mix,
    ticketStatus: { vcrTypes: {}, withTicket: 0, withoutTicket: 0, ticketPct: 100 },
    flightInfo: null,
    corporateTravel: { totalCorporate: 0, corporatePct: 0, companies: {} },
    priorityPassengers: { total: 0, codes: {} },
    seniority: { withSeniority: 0, pct: 0 },
    connectionRisk: { atRiskCount: 0, totalConnecting: 0, riskPct: 0 },
    classMismatch: { total: 0, upgrades: 0, downgrades: 0 },
    passengerTypes: {},
    boardingPasses: { issued: 0, notIssued: 0, issuedPct: 100 },
    reservationRecency: null,
    equipment: { aircraftType: "", seatConfig: "" },
    checkInTimeline: { totalWithTime: 0, hourDistribution: {}, peakHour: null, coveragePct: 0 },
    emergencyContacts: { withContact: 0, withoutContact: 0, coveragePct: 0 },
    nationalityBreakdown: { countries: {}, uniqueCountries: 0, unknown: 0, coveragePct: 0 },
    baggageRouting: { destinations: {}, paxWithRoutes: 0, coveragePct: 0 },
    standbyUpgrade: { standbyTotal: 0, upgradeTotal, standbyCabins: {}, standbyPct: 0 },
    operationalReadiness: {
      noSeat: 0,
      checkedInNoBP: 0,
      notCheckedIn: 0,
      thruNoSeat: 0,
      readinessPct: 100,
    },
  } as FlightInsights;
}

function dashboardFor(mix: Mix, upgradeTotal = 0): FlightDashboard {
  const total = Object.values(mix).reduce((sum, count) => sum + count, 0);
  const insights = insightsFor(mix, upgradeTotal);
  return {
    insights,
    passengerSummary: { totalPassengers: total },
    stateSummary: { others: { noShow: 0 } },
    analysis: { revenue: total, nonRevenue: 0, loyaltyCounts: {} },
    flightPhase: { phase: "SCHEDULED", label: "Scheduled" },
  } as FlightDashboard;
}

function render(node: ReactElement): string {
  return renderToStaticMarkup(node);
}

function plain(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function sliceBetween(text: string, start: string, end: string): string {
  const startAt = text.indexOf(start);
  assert.ok(startAt >= 0, `missing "${start}" in: ${text.slice(0, 500)}`);
  const from = startAt + start.length;
  const endAt = text.indexOf(end, from);
  assert.ok(endAt >= 0, `missing "${end}" after "${start}" in: ${text.slice(from, from + 500)}`);
  return text.slice(from, endAt);
}

type CabinGroup = { name: string; total: number; segments: Array<{ bookingClass: string; value: number }> };

function commercialGroups(mix: Mix): CabinGroup[] {
  const html = render(
    createElement(CommercialInsightsTab, {
      insights: insightsFor(mix),
      totalPassengers: Object.values(mix).reduce((sum, count) => sum + count, 0),
    }),
  );
  const text = plain(html);
  const section = sliceBetween(text, "Revenue Class Mix", "Booking Window Distribution");
  const groups: CabinGroup[] = [];
  const pattern = /([A-Za-z]+) Class (\d+)(.*?)(?=(?:[A-Za-z]+ Class \d+)|$)/g;
  for (const match of section.matchAll(pattern)) {
    const body = match[3] ?? "";
    const segments: CabinGroup["segments"] = [];
    for (const segment of body.matchAll(/Class (\S+) (\d+)/g)) {
      segments.push({ bookingClass: segment[1], value: Number(segment[2]) });
    }
    groups.push({ name: match[1], total: Number(match[2]), segments });
  }
  return groups;
}

function groupNamed(groups: CabinGroup[], name: string): CabinGroup {
  const found = groups.find((group) => group.name === name);
  assert.ok(found, `missing ${name} group in ${JSON.stringify(groups)}`);
  return found;
}

function sumMix(mix: Mix): number {
  return Object.values(mix).reduce((sum, count) => sum + count, 0);
}

function revenueSections(mix: Mix): { jText: string; yText: string; jCount: number; yCount: number; whole: string } {
  const html = render(createElement(RevenueTab, { dashboard: dashboardFor(mix) }));
  const text = plain(html);
  const section = sliceBetween(text, "Revenue Class Distribution", "Passenger Value Segmentation");
  const j = section.match(/J Cabin (\d+) pax \([^)]*\)(.*?)(?=Y Cabin|$)/);
  const y = section.match(/Y Cabin (\d+) pax \([^)]*\)(.*)$/);
  assert.ok(j && y, `could not read cabin bars from: ${section}`);
  return {
    jCount: Number(j[1]),
    yCount: Number(y[1]),
    jText: j[2],
    yText: y[2],
    whole: section,
  };
}

const failures: string[] = [];

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`PASS ${name}`);
  } catch (error) {
    failures.push(name);
    console.error(`FAIL ${name}`);
    console.error(error instanceof Error ? error.stack ?? error.message : error);
  }
}

const gf2006 = loadGf2006();

test("GF2006 commercial bars include class I on J and match the manifest", () => {
  const groups = commercialGroups(gf2006.revenueClassMix);
  const business = groupNamed(groups, "J");
  const economy = groupNamed(groups, "Y");
  const shown = groups.reduce((sum, group) => sum + group.total, 0);
  const classI = business.segments.find((segment) => segment.bookingClass.trim().toUpperCase() === "I");

  assert.equal(shown, gf2006.passengerCount, "grouped cabin totals dropped passengers");
  assert.equal(business.total, gf2006.manifestBusiness, "J bar does not match cabin J on the manifest");
  assert.ok(classI, `class I is not on the J bar: ${JSON.stringify(business)}`);
  assert.equal(classI.value, gf2006.classICount);
  assert.equal(
    economy.segments.some((segment) => segment.bookingClass.trim().toUpperCase() === "I"),
    false,
  );
  assert.equal(
    business.total,
    business.segments.reduce((sum, segment) => sum + segment.value, 0),
  );
});

test("commercial grouping keeps an unmapped class and still files I under J", () => {
  const mix: Mix = { Y: 10, D: 3, I: 4, i: 2, " I ": 6, P: 7 };
  const groups = commercialGroups(mix);
  const business = groupNamed(groups, "J");
  const economy = groupNamed(groups, "Y");
  const shown = groups.reduce(
    (sum, group) => sum + group.segments.reduce((inner, segment) => inner + segment.value, 0),
    0,
  );
  const businessLetters = business.segments.reduce((sum, segment) => sum + segment.value, 0);

  assert.equal(shown, sumMix(mix), "a booking class was dropped from the Revenue Class Mix card");
  assert.equal(groups.reduce((sum, group) => sum + group.total, 0), sumMix(mix));
  assert.equal(business.total, 3 + 4 + 2 + 6, "D, I, i, and padded I must make up the J total");
  assert.equal(businessLetters, business.total);
  assert.ok(economy.segments.some((segment) => segment.bookingClass === "Y" && segment.value === 10));
  assert.ok(
    groups.some((group) => group.segments.some((segment) => segment.bookingClass === "P" && segment.value === 7)),
    `unmapped class P is missing from ${JSON.stringify(groups)}`,
  );
  assert.equal(
    business.segments.some((segment) => segment.bookingClass.trim().toUpperCase() === "P"),
    false,
    "unmapped class P must stay visible without being counted as business",
  );
});

test("revenue tab counts class I on the J cabin of the GF2006 mix", () => {
  const bars = revenueSections(gf2006.revenueClassMix);
  assert.equal(bars.jCount + bars.yCount, gf2006.passengerCount);
  assert.equal(bars.jCount, gf2006.manifestBusiness);
  assert.match(bars.jText, new RegExp(`I:\\s*${gf2006.classICount}\\b`));
  assert.doesNotMatch(bars.yText, /I:\s*\d+/);
});

test("revenue tab keeps an unmapped class visible and does not file I as economy", () => {
  const mix: Mix = { Y: 10, D: 3, I: 4, P: 7 };
  const bars = revenueSections(mix);
  assert.equal(bars.jCount, 7);
  assert.equal(bars.jCount + bars.yCount, sumMix(mix));
  assert.match(bars.jText, /I:\s*4\b/);
  assert.match(bars.jText, /D:\s*3\b/);
  assert.doesNotMatch(bars.yText, /I:\s*\d+/);
  assert.match(bars.whole, /P:\s*7\b/);
});

test("decision briefing business cabin includes the GF2006 class I passenger", () => {
  const fromManifest = plain(
    render(createElement(DecisionBriefing, { dashboard: dashboardFor(gf2006.revenueClassMix, 1) })),
  );
  assert.match(fromManifest, new RegExp(`Business cabin has ${gf2006.manifestBusiness} passengers`));
});

test("decision briefing counts a lowercase i fare as business", () => {
  const onlyDiscountBusiness = plain(
    render(createElement(DecisionBriefing, { dashboard: dashboardFor({ i: 4, Y: 20 }, 2) })),
  );
  assert.match(onlyDiscountBusiness, /Business cabin has 4 passengers/);
});

if (failures.length > 0) {
  console.error(`${failures.length} test(s) failed`);
  process.exit(1);
}
