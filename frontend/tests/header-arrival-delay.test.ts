/**
 * The flight-board header chip must show an arrival delay that the dashboard
 * already returns on flightStatus.schedule. These tests fail until ScheduleDelay
 * prints STA and ETA beside STD and ETD.
 *
 * Clocks are Sabre 12-hour times. The displayed arrival may be 12-hour with a
 * meridiem (08:10PM or 8:10 PM) or 24-hour (20:10). The minute delta is the
 * difference between those clocks.
 */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ScheduleDelay } from "@/components/dashboard/workbench-utils";

type Schedule = {
  scheduledDeparture: string;
  estimatedDeparture: string;
  scheduledArrival: string;
  estimatedArrival: string;
  durationMinutes: number;
};

const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..");
const LOADER = path.resolve(import.meta.dirname, "load_dashboard_schedule.py");

function baseItinerary(overrides: Record<string, string> = {}): Record<string, string> {
  return {
    Airline: "GF",
    Flight: "2057",
    Origin: "BOM",
    DepartureDate: "2026-03-19",
    DepartureTime: "05:40PM",
    ScheduledDepartureDate: "2026-03-19",
    ScheduledDepartureTime: "05:40PM",
    EstimatedDepartureDate: "2026-03-19",
    EstimatedDepartureTime: "05:40PM",
    ArrivalDate: "2026-03-19",
    ArrivalTime: "09:25PM",
    ScheduledArrivalTime: "08:10PM",
    EstimatedArrivalDate: "2026-03-19",
    EstimatedArrivalTime: "09:25PM",
    FlightDurationInMinutes: "375",
    ...overrides,
  };
}

function loadSchedules(cases: Record<string, Record<string, string>>): Record<string, Schedule> {
  const result = spawnSync("python3", [LOADER], {
    cwd: REPO_ROOT,
    input: JSON.stringify(cases),
    encoding: "utf8",
  });
  assert.equal(
    result.status,
    0,
    `schedule loader failed\n${result.stderr}\n${result.stdout}`,
  );
  return JSON.parse(result.stdout) as Record<string, Schedule>;
}

function renderChip(schedule: Schedule): string {
  const element = createElement(ScheduleDelay, { schedule }) as ReactElement;
  return renderToStaticMarkup(element);
}

function textOf(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function showsClock(text: string, hhmmMeridiem: string): boolean {
  const match = hhmmMeridiem.match(/^(\d{2}):(\d{2})(AM|PM)$/i);
  assert.ok(match, hhmmMeridiem);
  let hour = Number(match[1]);
  const minute = match[2];
  const meridiem = match[3].toUpperCase();
  if (meridiem === "PM" && hour < 12) hour += 12;
  if (meridiem === "AM" && hour === 12) hour = 0;
  const compact = text.replace(/\s+/g, "").toUpperCase();
  const withMeridiem = `${match[1]}:${minute}${meridiem}`;
  const unpadded = `${String(Number(match[1]))}:${minute}${meridiem}`;
  const hour24 = `${String(hour).padStart(2, "0")}:${minute}`;
  return compact.includes(withMeridiem) || compact.includes(unpadded) || text.includes(hour24);
}

function assertDelay(text: string, minutes: number): void {
  const sign = minutes > 0 ? "\\+" : "-";
  assert.match(text, new RegExp(`${sign}${Math.abs(minutes)}\\s*m`), text);
}

const schedules = loadSchedules({
  arrivalOnly: baseItinerary(),
  bothDelayed: baseItinerary({ EstimatedDepartureTime: "06:00PM", DepartureTime: "06:00PM" }),
  departureOnly: baseItinerary({
    EstimatedDepartureTime: "05:50PM",
    DepartureTime: "05:50PM",
    EstimatedArrivalTime: "08:10PM",
    ArrivalTime: "08:10PM",
  }),
  onTime: baseItinerary({
    EstimatedArrivalTime: "08:10PM",
    ArrivalTime: "08:10PM",
  }),
  earlyArrival: baseItinerary({
    EstimatedArrivalTime: "07:40PM",
    ArrivalTime: "07:40PM",
  }),
});

test("dashboard schedule keeps an arrival delay when departure is on time", () => {
  const schedule = schedules.arrivalOnly;
  assert.equal(schedule.scheduledDeparture, "2026-03-19T05:40PM");
  assert.equal(schedule.estimatedDeparture, "2026-03-19T05:40PM");
  assert.equal(schedule.scheduledArrival, "2026-03-19T08:10PM");
  assert.equal(schedule.estimatedArrival, "2026-03-19T09:25PM");
});

test("header chip shows STA and ETA when only the arrival is late", () => {
  const text = textOf(renderChip(schedules.arrivalOnly));
  assert.match(text, /\bSTD\b/);
  assert.match(text, /\bSTA\b/);
  assert.match(text, /\bETA\b/);
  assert.ok(showsClock(text, "08:10PM"), text);
  assert.ok(showsClock(text, "09:25PM"), text);
  assertDelay(text, 75);
});

test("header chip shows both the departure delay and a different arrival delay", () => {
  const schedule = schedules.bothDelayed;
  assert.equal(schedule.scheduledDeparture, "2026-03-19T05:40PM");
  assert.equal(schedule.estimatedDeparture, "2026-03-19T06:00PM");
  assert.equal(schedule.scheduledArrival, "2026-03-19T08:10PM");
  assert.equal(schedule.estimatedArrival, "2026-03-19T09:25PM");

  const text = textOf(renderChip(schedule));
  assert.match(text, /\bSTD\b/);
  assert.match(text, /\bETD\b/);
  assert.match(text, /\bSTA\b/);
  assert.match(text, /\bETA\b/);
  assert.ok(showsClock(text, "06:00PM"), text);
  assert.ok(showsClock(text, "08:10PM"), text);
  assert.ok(showsClock(text, "09:25PM"), text);
  assertDelay(text, 20);
  assertDelay(text, 75);
});

test("header chip shows an early arrival as well as a late one", () => {
  const schedule = schedules.earlyArrival;
  assert.equal(schedule.estimatedArrival, "2026-03-19T07:40PM");
  assert.notEqual(schedule.estimatedArrival, schedule.scheduledArrival);

  const text = textOf(renderChip(schedule));
  assert.match(text, /\bSTA\b/);
  assert.match(text, /\bETA\b/);
  assert.ok(showsClock(text, "08:10PM"), text);
  assert.ok(showsClock(text, "07:40PM"), text);
  assertDelay(text, -30);
});

test("header chip does not invent an arrival delay when the clocks match", () => {
  const onTime = textOf(renderChip(schedules.onTime));
  assert.match(onTime, /\bSTD\b/);
  assert.doesNotMatch(onTime, /\bETA\b/);
  assert.doesNotMatch(onTime, /\bSTA\b/);

  const departureOnly = textOf(renderChip(schedules.departureOnly));
  assert.match(departureOnly, /\bETD\b/);
  assert.ok(showsClock(departureOnly, "05:50PM"), departureOnly);
  assertDelay(departureOnly, 10);
  assert.doesNotMatch(departureOnly, /\bETA\b/);
  assert.doesNotMatch(departureOnly, /\bSTA\b/);
});
