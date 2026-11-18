/**
 * Renders the flight-header, history, and schedule-delay views with the
 * ACS schedule strings produced by the Python feeder. Prints JSON text
 * so the Python tests can assert the clocks an operator would read.
 *
 * Reads one JSON object from stdin:
 *   { "cases": { "<id>": { "kind": "schedule" | "header" | "history", ... } } }
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ScheduleDelay } from "../components/dashboard/workbench-utils";
import { FlightHeader } from "../components/dashboard/flight-header";
import { StatusHistory } from "../components/dashboard/status-history";
import { fetchSnapshots, fetchStatusHistory } from "../lib/api";

type Schedule = {
  scheduledDeparture?: string;
  estimatedDeparture?: string;
  scheduledArrival?: string;
  estimatedArrival?: string;
};

type HistoryRecord = {
  airline: string;
  flightNumber: string;
  origin: string;
  departureDate: string;
  fetchedAt: string;
  status: string;
  schedule: Schedule;
  boarding?: { time?: string };
  [key: string]: unknown;
};

type FlightHeaderInput = {
  airline: string;
  flightNumber: string;
  origin: string;
  departureDate: string;
  status: string;
  gate: string;
  aircraft: { type: string; registration: string };
  schedule: Schedule;
  passengerCounts: Record<string, unknown>;
  jumpSeat: { cockpit: number; cabin: number; cockpitInUse: boolean; cabinInUse: boolean };
  fetchedAt: string;
};

type Case =
  | { kind: "schedule"; schedule: Schedule }
  | { kind: "header"; flight: FlightHeaderInput }
  | { kind: "history"; record: HistoryRecord };

function visibleText(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

async function renderHistory(record: HistoryRecord): Promise<string> {
  const flightNumber = record.flightNumber;
  const origin = record.origin;
  const date = record.departureDate;
  const historyUrl = `/flights/${flightNumber}/status/history`;
  const snapshotUrl = `/flights/${flightNumber}/snapshots`;

  const previous = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes(historyUrl)) {
      return Response.json([record]);
    }
    if (url.includes(snapshotUrl)) {
      return Response.json([]);
    }
    return Response.json({ detail: `unexpected ${url}` }, { status: 404 });
  }) as typeof fetch;

  try {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    await client.prefetchQuery({
      queryKey: ["statusHistory", flightNumber, origin, date],
      queryFn: () => fetchStatusHistory(flightNumber, origin, date),
    });
    await client.prefetchQuery({
      queryKey: ["snapshots", flightNumber, origin, date],
      queryFn: () => fetchSnapshots(flightNumber, origin, date, "passenger_list"),
    });
    const html = renderToStaticMarkup(
      createElement(
        QueryClientProvider,
        { client },
        createElement(StatusHistory, {
          flightNumber,
          origin,
          date,
        }),
      ),
    );
    return visibleText(html);
  } finally {
    globalThis.fetch = previous;
  }
}

async function renderCase(entry: Case): Promise<string> {
  if (entry.kind === "schedule") {
    return visibleText(
      renderToStaticMarkup(createElement(ScheduleDelay, { schedule: entry.schedule })),
    );
  }
  if (entry.kind === "header") {
    return visibleText(
      renderToStaticMarkup(
        createElement(FlightHeader, {
          flightStatus: entry.flight,
          flightNumber: entry.flight.flightNumber,
        }),
      ),
    );
  }
  return renderHistory(entry.record);
}

async function main() {
  const raw = await new Promise<string>((resolve, reject) => {
    const chunks: Buffer[] = [];
    process.stdin.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
  const payload = JSON.parse(raw) as { cases: Record<string, Case> };
  const rendered: Record<string, string> = {};
  for (const [id, entry] of Object.entries(payload.cases)) {
    rendered[id] = await renderCase(entry);
  }
  process.stdout.write(JSON.stringify(rendered));
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  process.stderr.write(message + "\n");
  process.exit(1);
});
