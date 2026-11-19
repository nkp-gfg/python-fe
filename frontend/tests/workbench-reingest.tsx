import assert from "node:assert/strict";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";

import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { ToastProvider } from "@/components/ui/toast";

const BOARD_SEQUENCE = 1001;

const flight = {
  airline: "GF",
  flightNumber: "70",
  origin: "BAH",
  destination: "LHR",
  departureDate: "2026-03-20",
  status: "OPENCI",
  gate: "A12",
  flightSequenceNumber: BOARD_SEQUENCE,
  fetchedAt: "2026-03-19T22:00:00Z",
  schedule: {
    scheduledDeparture: "2026-03-20T01:30AM",
    estimatedDeparture: "2026-03-20T01:30AM",
    scheduledArrival: "2026-03-20T05:00AM",
    estimatedArrival: "2026-03-20T05:00AM",
    durationMinutes: 210,
  },
};

const posts: unknown[] = [];

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (init?.method === "POST" && url.includes("/flights/ingest/batch")) {
    posts.push(JSON.parse(String(init.body)));
    return jsonResponse({
      jobId: "job-reingest",
      status: "accepted",
      flightsQueued: 1,
      message: "queued",
      pollUrl: "/flights/ingest/jobs/job-reingest",
    });
  }
  if (url.includes("/flights/ingest/jobs/")) {
    return jsonResponse({
      jobId: "job-reingest",
      status: "completed",
      flightsQueued: 1,
      flightsProcessed: 1,
      submittedAt: "2026-03-20T02:00:00Z",
      results: [
        {
          flight: {
            airline: "GF",
            flightNumber: "70",
            origin: "BAH",
            departureDate: "2026-03-20",
            departureDateTime: "2026-03-20T01:30:00",
            flightSequenceNumber: BOARD_SEQUENCE,
          },
          success: true,
          apis: {
            flightStatus: { status: "success" },
            passengerList: { status: "success" },
            reservations: { status: "success" },
          },
        },
      ],
    });
  }
  if (url.includes("/dashboard") || url.includes("/tree")) {
    return new Promise(() => {});
  }
  if (url.includes("/flights")) {
    return jsonResponse([flight]);
  }
  return jsonResponse({});
}) as typeof fetch;

function textOf(element: Element): string {
  return element.textContent ?? "";
}

async function click(element: Element) {
  await act(async () => {
    element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

async function main() {
  const mount = document.createElement("div");
  document.body.appendChild(mount);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  let root: Root | undefined;
  await act(async () => {
    root = createRoot(mount);
    root.render(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <QueryClientProvider client={client}>
          <ToastProvider>
            <FlightWorkbench />
          </ToastProvider>
        </QueryClientProvider>
      </ThemeProvider>,
    );
  });

  let reingest: Element | null = null;
  for (let attempt = 0; attempt < 20 && !reingest; attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    reingest = document.querySelector('[title="Re-ingest from Sabre"]');
  }
  assert.ok(reingest, "flight board rendered the re-ingest control for the loaded row");
  await click(reingest);

  let confirm: Element | null = null;
  for (let attempt = 0; attempt < 20 && !confirm; attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 25));
    });
    confirm = [...document.querySelectorAll("button")].find((button) =>
      textOf(button).includes("Start Background Ingest"),
    ) ?? null;
  }
  assert.ok(confirm, "confirmation opened the board re-ingest action");
  await click(confirm);

  for (let attempt = 0; attempt < 20 && posts.length === 0; attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 25));
    });
  }

  assert.equal(posts.length, 1);
  const body = posts[0] as { flights: Array<Record<string, unknown>> };
  const sent = body.flights[0];
  assert.equal(sent.airline, flight.airline);
  assert.equal(sent.flightNumber, flight.flightNumber);
  assert.equal(sent.origin, flight.origin);
  assert.equal(sent.departureDate, flight.departureDate);
  assert.equal(sent.departureDateTime, "2026-03-20T01:30:00");
  assert.equal(
    sent.flightSequenceNumber,
    flight.flightSequenceNumber,
    "re-ingest must send the flightSequenceNumber already loaded on the list row",
  );

  await act(async () => {
    root?.unmount();
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
