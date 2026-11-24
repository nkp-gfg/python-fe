/**
 * Render the operations cards and the passenger lists those cards open.
 *
 * Reads one JSON scenario on stdin and writes extracted headlines, cabin
 * subtotals, and list lengths on stdout. Assertions live in
 * backend/tests/test_lap_infant_status_headlines.py.
 */
import { createRequire } from "node:module";
import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Client components are loaded as CommonJS, so they bind the require()
// copy of react-query. Use that same copy or the provider context is empty.
const require = createRequire(import.meta.url);
const { QueryClient, QueryClientProvider } = require("@tanstack/react-query") as {
  QueryClient: typeof import("@tanstack/react-query").QueryClient;
  QueryClientProvider: typeof import("@tanstack/react-query").QueryClientProvider;
};
import * as statePanelModule from "../components/dashboard/state-panels";
import * as detailPanelModule from "../components/dashboard/bottom-detail-panel";
import * as passengerTableModule from "../components/dashboard/passenger-table";
import type { FlightDashboard, FlightPhaseCode } from "../lib/types";
import type { DetailView } from "../components/dashboard/bottom-detail-panel";
import type { FilterCabin, FilterStatus, FilterType } from "../components/dashboard/passenger-table";

// tsx loads these client components as a CommonJS namespace. Next's bundler
// exposes the same functions as named exports; read either shape.
function componentExport<T>(mod: object, name: string): T {
  const record = mod as Record<string, unknown>;
  const direct = record[name];
  if (typeof direct === "function") return direct as T;
  const wrapped = record.default as Record<string, unknown> | undefined;
  const nested = wrapped?.[name];
  if (typeof nested === "function") return nested as T;
  throw new Error(`missing component export ${name}`);
}

const StatePanels = componentExport<typeof import("../components/dashboard/state-panels").StatePanels>(
  statePanelModule,
  "StatePanels",
);
const BottomDetailPanel = componentExport<
  typeof import("../components/dashboard/bottom-detail-panel").BottomDetailPanel
>(detailPanelModule, "BottomDetailPanel");
const PassengerTable = componentExport<
  typeof import("../components/dashboard/passenger-table").PassengerTable
>(passengerTableModule, "PassengerTable");

// The lists under test are fed from the query cache. A refetch must not
// leave this process and hit an API.
globalThis.fetch = async () => {
  throw new Error("operation-card test must not call the network");
};

interface Scenario {
  id: string;
  flightNumber: string;
  origin: string;
  date: string;
  phases: FlightPhaseCode[];
  stateSummary: FlightDashboard["stateSummary"];
  passengers: Record<string, unknown>[];
}

interface CardSnapshot {
  title: string;
  headline: number | null;
  cabins: { Economy: number | null; Business: number | null };
  soulsLabel: number | null;
}

function textContent(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function splitCards(html: string): string[] {
  const marks = [...html.matchAll(/<div data-slot="card"/g)].map((match) => match.index ?? 0);
  return marks.map((start, index) => html.slice(start, marks[index + 1] ?? html.length));
}

function headlineOf(cardHtml: string): number | null {
  const match = cardHtml.match(
    /<button[^>]*class="[^"]*\btext-xl\b[^"]*"[^>]*>(\d+)<\/button>/,
  );
  return match ? Number(match[1]) : null;
}

function cabinOf(cardHtml: string, label: "Economy" | "Business"): number | null {
  const marker = `>${label}<`;
  const at = cardHtml.indexOf(marker);
  if (at < 0) return null;
  const rest = cardHtml.slice(at + marker.length);
  const match = rest.match(/<button[^>]*>(\d+)<\/button>/);
  return match ? Number(match[1]) : null;
}

function soulsLabelOf(cardHtml: string): number | null {
  const text = textContent(cardHtml);
  const match = text.match(/\b(\d+)\s+souls?\b/i) ?? text.match(/\bsouls?\s+(\d+)\b/i);
  return match ? Number(match[1]) : null;
}

function titleOf(cardHtml: string): string {
  const text = textContent(cardHtml);
  const known = [
    "Awaiting Check-in",
    "Not Checked In",
    "No Show",
    "Checked-In",
    "Boarded",
    "Booked",
    "Other Passengers",
  ];
  return known.find((title) => text.includes(title)) ?? "";
}

function snapshotCards(html: string): CardSnapshot[] {
  return splitCards(html).map((cardHtml) => ({
    title: titleOf(cardHtml),
    headline: headlineOf(cardHtml),
    cabins: {
      Economy: cabinOf(cardHtml, "Economy"),
      Business: cabinOf(cardHtml, "Business"),
    },
    soulsLabel: soulsLabelOf(cardHtml),
  }));
}

function countPassengerRows(html: string): number {
  const body = html.match(/<tbody[\s\S]*?<\/tbody>/);
  if (!body) return 0;
  const rows = body[0].match(/<tr[\s\S]*?<\/tr>/g) ?? [];
  return rows.filter(
    (row) =>
      !row.includes("No passengers match the current filters.") &&
      !row.includes("No passengers in this category."),
  ).length;
}

function renderWithPassengers(
  scenario: Scenario,
  node: ReactNode,
): string {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnMount: false } },
  });
  client.setQueryData(
    ["passengers", scenario.flightNumber, scenario.origin, scenario.date, null],
    { passengers: scenario.passengers },
  );
  client.setQueryData(
    ["audit", scenario.flightNumber, scenario.origin, scenario.date],
    { passengerAlerts: {} },
  );
  return renderToStaticMarkup(
    createElement(QueryClientProvider, { client }, node),
  );
}

function renderPanels(scenario: Scenario, phase: FlightPhaseCode): string {
  return renderToStaticMarkup(
    createElement(StatePanels, {
      stateSummary: scenario.stateSummary,
      phase,
      focusCard: "booked",
      onInfoClick: () => {},
    }),
  );
}

const DETAIL_VIEWS: DetailView[] = ["booked", "checkedIn", "boarded"];

function renderDetail(scenario: Scenario, view: DetailView): string {
  return renderWithPassengers(
    scenario,
    createElement(BottomDetailPanel, {
      view,
      flightNumber: scenario.flightNumber,
      origin: scenario.origin,
      date: scenario.date,
      snapshotSequence: null,
      dashboard: { stateSummary: scenario.stateSummary } as FlightDashboard,
      onClose: () => {},
    }),
  );
}

const noop = () => {};

function renderTable(
  scenario: Scenario,
  filterStatus: FilterStatus,
  filterCabin: FilterCabin,
  filterType: FilterType = "all",
): string {
  return renderWithPassengers(
    scenario,
    createElement(PassengerTable, {
      flightNumber: scenario.flightNumber,
      origin: scenario.origin,
      date: scenario.date,
      snapshotSequence: null,
      filterCabin,
      setFilterCabin: noop as (v: FilterCabin) => void,
      filterStatus,
      setFilterStatus: noop as (v: FilterStatus) => void,
      filterType,
      setFilterType: noop as (v: FilterType) => void,
      filterLoyalty: "all",
      setFilterLoyalty: noop,
      filterNationality: "all",
      setFilterNationality: noop,
    }),
  );
}

function cardClicks(): { status: FilterStatus; cabin: FilterCabin }[] {
  // Status values StatePanels wires into each card. The click handler in
  // flight-workbench copies this payload onto the passenger table.
  return [
    { status: "booked", cabin: "all" },
    { status: "checkedIn", cabin: "all" },
    { status: "boarded", cabin: "all" },
    { status: "booked", cabin: "Y" },
    { status: "booked", cabin: "J" },
    { status: "checkedIn", cabin: "Y" },
    { status: "checkedIn", cabin: "J" },
    { status: "boarded", cabin: "Y" },
    { status: "boarded", cabin: "J" },
  ];
}

async function main() {
  const raw = await new Promise<string>((resolve, reject) => {
    const chunks: Buffer[] = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk as Buffer));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
  const scenario = JSON.parse(raw) as Scenario;
  const phases: Record<string, { cards: CardSnapshot[] }> = {};
  for (const phase of scenario.phases) {
    phases[phase] = { cards: snapshotCards(renderPanels(scenario, phase)) };
  }
  const detail: Record<string, number> = {};
  for (const view of DETAIL_VIEWS) {
    detail[view] = countPassengerRows(renderDetail(scenario, view));
  }
  const table: Record<string, number> = {};
  for (const click of cardClicks()) {
    const key = `${click.status}:${click.cabin}`;
    table[key] = countPassengerRows(
      renderTable(scenario, click.status, click.cabin),
    );
  }
  process.stdout.write(JSON.stringify({ id: scenario.id, phases, detail, table }));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
