/**
 * A batch whose every flight fails is stored with status "failed" and the
 * full per-flight Sabre results. backend/tests/test_failed_batch_api_outcomes.py
 * produces that job: GF2006 keeps a successful flight-status and reservation
 * pull while every passenger-list call fails, and GF2007 keeps flight-status
 * and the passenger list while reservations fail. The job error names only
 * the first of those failures.
 *
 * These tests render the screens that poll that job. They fail until a failed
 * job shows the same per-flight outcomes as a partial job, with the summary
 * error still visible.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "next-themes";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IngestionPanel } from "@/components/dashboard/ingestion-panel";
import { ToastProvider } from "@/components/ui/toast";
import TodayPage from "@/app/today/page";
import type { OtpFlight, SabreApiResult, SabreFlightIngestResult, SabreJobStatus } from "@/lib/types";

const PASSENGER_LIST_FAILURE = "All 4 passenger list calls failed";
const RESERVATION_FAILURE = "RESERVATIONS-2007-rejected";

function api(status: "success" | "error", error?: string): SabreApiResult {
  return error === undefined ? { status } : { status, error };
}

function flightResult(
  flightNumber: string,
  origin: string,
  sequence: number,
  departureDateTime: string,
  outcomes: {
    flightStatus: SabreApiResult;
    passengerList: SabreApiResult;
    reservations: SabreApiResult;
  },
): SabreFlightIngestResult {
  const failed = Object.values(outcomes).some((item) => item.status === "error");
  return {
    flight: {
      airline: "GF",
      flightNumber,
      origin,
      departureDate: "2026-03-19",
      departureDateTime,
      flightSequenceNumber: sequence,
    },
    success: !failed,
    apis: outcomes,
  };
}

const gf2006 = flightResult("2006", "BAH", 101, "2026-03-19T08:00:00", {
  flightStatus: api("success"),
  passengerList: api("error", PASSENGER_LIST_FAILURE),
  reservations: api("success"),
});

const gf2007 = flightResult("2007", "LHR", 102, "2026-03-19T09:00:00", {
  flightStatus: api("success"),
  passengerList: api("success"),
  reservations: api("error", RESERVATION_FAILURE),
});

const gf2008 = flightResult("2008", "DXB", 103, "2026-03-19T10:00:00", {
  flightStatus: api("success"),
  passengerList: api("success"),
  reservations: api("success"),
});

function jobDocument(
  status: SabreJobStatus["status"],
  results: SabreFlightIngestResult[],
  error: string | null,
): SabreJobStatus {
  return {
    jobId: "job-all-failed",
    status,
    flightsQueued: results.length,
    flightsProcessed: results.length,
    submittedAt: "2026-03-19T08:00:00Z",
    startedAt: "2026-03-19T08:00:01Z",
    completedAt: "2026-03-19T08:00:02Z",
    results,
    error,
  };
}

const failedBatchJob = jobDocument(
  "failed",
  [gf2006, gf2007],
  `All 2 flights failed. First error: ${PASSENGER_LIST_FAILURE}`,
);

const partialBatchJob = jobDocument(
  "partial",
  [gf2006, gf2007, gf2008],
  `2 of 3 flights failed. First error: ${PASSENGER_LIST_FAILURE}`,
);

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(job: SabreJobStatus, extra?: (url: string) => Response | undefined) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const special = extra?.(url);
    if (special) return special;
    if (url.includes("/flights/ingest/batch")) {
      return jsonResponse({
        jobId: job.jobId,
        status: "accepted",
        flightsQueued: job.flightsQueued,
        message: "Batch ingestion accepted",
        pollUrl: `/flights/ingest/jobs/${job.jobId}`,
      });
    }
    if (url.includes("/flights/ingest/jobs/")) {
      return jsonResponse(job);
    }
    throw new Error(`Unexpected request ${url}`);
  }));
}

function renderPanel(ui: ReactElement = <IngestionPanel />) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <QueryClientProvider client={client}>
        <ToastProvider>{ui}</ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

async function queueFlight(number: string, origin: string) {
  const user = userEvent.setup();
  const flight = screen.getByPlaceholderText("e.g. 2006");
  const airport = screen.getByPlaceholderText("e.g. LHR");
  await user.clear(flight);
  await user.type(flight, number);
  await user.clear(airport);
  await user.type(airport, origin);
  await user.click(screen.getByRole("button", { name: "Add to Batch" }));
}

function apiOutcome(flightLabel: string, apiName: string): "success" | "error" {
  const link = screen.getByRole("link", { name: new RegExp(flightLabel) });
  const card = link.closest("div.rounded-lg");
  if (!card) {
    throw new Error(`No result card for ${flightLabel}`);
  }
  const label = within(card as HTMLElement).getByText(apiName);
  const row = label.closest("div");
  const svg = row?.querySelector("svg");
  const cls = svg?.getAttribute("class") ?? "";
  if (cls.includes("text-emerald-500")) return "success";
  if (cls.includes("text-destructive")) return "error";
  throw new Error(`No outcome icon for ${flightLabel} ${apiName} (class: ${cls})`);
}

function expectMixedOutcomes() {
  expect(apiOutcome("GF2006", "Flight Status")).toBe("success");
  expect(apiOutcome("GF2006", "Passenger List")).toBe("error");
  expect(apiOutcome("GF2006", "Reservations")).toBe("success");
  expect(apiOutcome("GF2007", "Flight Status")).toBe("success");
  expect(apiOutcome("GF2007", "Passenger List")).toBe("success");
  expect(apiOutcome("GF2007", "Reservations")).toBe("error");
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("batch ingest panel", () => {
  it("shows each Sabre call for a partial batch", async () => {
    installFetch(partialBatchJob);
    renderPanel();
    await queueFlight("2006", "BAH");
    await queueFlight("2007", "LHR");
    await queueFlight("2008", "DXB");
    await userEvent.setup().click(screen.getByRole("button", { name: "Run Batch" }));

    expect(await screen.findByText(/2 of 3 flights failed/)).toBeTruthy();
    expectMixedOutcomes();
    expect(apiOutcome("GF2008", "Passenger List")).toBe("success");
  });

  it("shows each Sabre call when every flight in the batch failed", async () => {
    installFetch(failedBatchJob);
    renderPanel();
    await queueFlight("2006", "BAH");
    await queueFlight("2007", "LHR");
    await userEvent.setup().click(screen.getByRole("button", { name: "Run Batch" }));

    expect((await screen.findAllByText(/All 2 flights failed/)).length).toBeGreaterThan(0);
    expectMixedOutcomes();
  });

  it("shows each Sabre call after Start Ingest when the one flight failed", async () => {
    const failedSingle = jobDocument("failed", [gf2006], PASSENGER_LIST_FAILURE);
    installFetch(failedSingle);
    renderPanel();
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText("e.g. 2006"), "2006");
    await user.type(screen.getByPlaceholderText("e.g. LHR"), "BAH");
    await user.click(screen.getByRole("button", { name: "Start Ingest" }));

    expect((await screen.findAllByText(PASSENGER_LIST_FAILURE)).length).toBeGreaterThan(0);
    expect(apiOutcome("GF2006", "Flight Status")).toBe("success");
    expect(apiOutcome("GF2006", "Passenger List")).toBe("error");
    expect(apiOutcome("GF2006", "Reservations")).toBe("success");
  });
});

function otpFlight(sequence: number, flightNumber: string, origin: string, destination: string): OtpFlight {
  return {
    flightSequenceNumber: sequence,
    carrierCode: "GF",
    flightNumber,
    origin,
    destination,
    flightDate: "2026-03-19",
    sabreDepartureDate: "2026-03-19",
    scheduledDepartureUtc: "2026-03-19T08:00:00Z",
    scheduledArrivalUtc: "2026-03-19T12:00:00Z",
    flightStatus: "SCHEDULED",
    isCancelled: false,
    totalPax: 10,
    serviceTypeCode: "J",
  };
}

async function ingestSelectedTodayFlights() {
  const user = userEvent.setup();
  await screen.findByRole("checkbox", { name: "Select flight GF2006" });
  await user.click(screen.getByRole("checkbox", { name: "Select flight GF2006" }));
  await user.click(screen.getByRole("checkbox", { name: "Select flight GF2007" }));
  await user.click(screen.getByRole("button", { name: "Ingest 2 flights" }));
}

describe("today page batch ingest", () => {
  function renderToday(job: SabreJobStatus) {
    installFetch(job, (url) => {
      if (url.includes("/otp/flights")) {
        return jsonResponse([
          otpFlight(101, "2006", "BAH", "LHR"),
          otpFlight(102, "2007", "LHR", "BAH"),
        ]);
      }
      return undefined;
    });
    renderPanel(<TodayPage />);
  }

  it("shows each flight's own Sabre failure for a partial batch", async () => {
    renderToday(partialBatchJob);
    await ingestSelectedTodayFlights();

    expect(await screen.findByText(RESERVATION_FAILURE, {}, { timeout: 8000 })).toBeTruthy();
  });

  it("shows each flight's own Sabre failure when every flight failed", async () => {
    renderToday(failedBatchJob);
    await ingestSelectedTodayFlights();

    expect((await screen.findAllByText(/All 2 flights failed/, {}, { timeout: 8000 })).length).toBeGreaterThan(0);
    expect(screen.getByText(RESERVATION_FAILURE)).toBeTruthy();
    expect(screen.getAllByText(PASSENGER_LIST_FAILURE).length).toBeGreaterThan(0);
  });
});
