/**
 * Operating day for Today's Flights must be Asia/Bahrain (Gulf Air hub),
 * not the viewer's browser calendar day.
 *
 * Frozen instant 2026-11-22T22:30:00Z is 2026-11-23 01:30 in Asia/Bahrain and
 * still 2026-11-22 in America/Los_Angeles. Run with TZ=America/Los_Angeles
 * (npm script test:operating-day). These tests fail while the pages format
 * `new Date()` in the process-local zone.
 *
 * OTP membership is the real list_otp_flights handler. The only stand-in is
 * PostgreSQL: timestamp-without-time-zone :: date keeps the stored calendar
 * fields (see tests/support/list_otp_flights.py).
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "next-themes";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

import { ToastProvider } from "@/components/ui/toast";
import type { FlightListItem } from "@/lib/types";

vi.mock("@/components/ui/toast", () => ({
  ToastProvider: ({ children }: { children: React.ReactNode }) => children,
  useToast: () => ({ pushToast: () => {} }),
}));

const INSTANT = new Date("2026-11-22T22:30:00.000Z");
const OPS_ZONE = "Asia/Bahrain";
const VIEWER_ZONE = "America/Los_Angeles";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../..");
const bridge = path.join(here, "support/list_otp_flights.py");

function calendarDay(timeZone: string, instant: Date): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(instant);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function addCalendarDays(ymd: string, days: number): string {
  const [year, month, day] = ymd.split("-").map(Number);
  const utc = new Date(Date.UTC(year, month - 1, day));
  utc.setUTCDate(utc.getUTCDate() + days);
  return utc.toISOString().slice(0, 10);
}

const OPS_DAY = calendarDay(OPS_ZONE, INSTANT);
const VIEWER_DAY = calendarDay(VIEWER_ZONE, INSTANT);
const OPS_NEXT = addCalendarDays(OPS_DAY, 1);
const OPS_RANGE_FROM = addCalendarDays(OPS_DAY, -25);

type OtpRow = Record<string, unknown>;

function otpRow(partial: OtpRow): OtpRow {
  return {
    carrier_code: "GF",
    actual_origin: null,
    actual_destination: null,
    local_flight_date: null,
    leg_departure_date: null,
    estimated_block_off_utc: null,
    estimated_block_on_utc: null,
    actual_block_off_utc: null,
    actual_block_on_utc: null,
    actual_takeoff_utc: null,
    actual_touchdown_utc: null,
    published_departure_local: null,
    published_arrival_local: null,
    aircraft_type: "32N",
    aircraft_registration: "A9C-FE",
    service_type_code: "J",
    cancel_reason_code: null,
    passenger_counts: [{ Count: 10 }],
    delay_details: null,
    source: "test",
    ...partial,
  };
}

const OTP_ROWS: OtpRow[] = [
  otpRow({
    flight_sequence_number: 1001,
    flight_number: "100",
    scheduled_origin: "BAH",
    scheduled_destination: "LHR",
    flight_date: OPS_DAY,
    local_flight_date: OPS_DAY,
    scheduled_departure_local: `${OPS_DAY}T01:10:00`,
    scheduled_arrival_local: `${OPS_DAY}T08:40:00`,
    scheduled_departure_utc: "2026-11-22T22:10:00",
    scheduled_arrival_utc: "2026-11-23T05:40:00",
    flight_status: "SCHEDULED",
  }),
  otpRow({
    flight_sequence_number: 2002,
    flight_number: "200",
    scheduled_origin: "BAH",
    scheduled_destination: "DXB",
    flight_date: VIEWER_DAY,
    local_flight_date: VIEWER_DAY,
    scheduled_departure_local: `${VIEWER_DAY}T18:00:00`,
    scheduled_arrival_local: `${VIEWER_DAY}T21:00:00`,
    scheduled_departure_utc: `${VIEWER_DAY}T15:00:00`,
    scheduled_arrival_utc: `${VIEWER_DAY}T17:00:00`,
    flight_status: "SCHEDULED",
  }),
  otpRow({
    flight_sequence_number: 3003,
    flight_number: "300",
    scheduled_origin: "LHR",
    scheduled_destination: "BAH",
    flight_date: VIEWER_DAY,
    local_flight_date: VIEWER_DAY,
    scheduled_departure_local: `${VIEWER_DAY}T23:40:00`,
    scheduled_arrival_local: `${OPS_DAY}T05:10:00`,
    scheduled_departure_utc: `${VIEWER_DAY}T23:40:00`,
    scheduled_arrival_utc: `${OPS_DAY}T02:10:00`,
    flight_status: "SCHEDULED",
  }),
];

const requests: { method: string; url: string; body: unknown }[] = [];

function listOtpFlights(date: string) {
  const result = spawnSync("python3", [bridge], {
    input: JSON.stringify({ date, rows: OTP_ROWS }),
    encoding: "utf8",
    cwd: repoRoot,
    env: { ...process.env, PYTHONPATH: repoRoot },
  });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || "OTP bridge failed");
  }
  return JSON.parse(result.stdout);
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = init?.method ?? "GET";
    let body: unknown = null;
    if (typeof init?.body === "string") body = JSON.parse(init.body);
    requests.push({ method, url, body });
    const parsed = new URL(url);

    if (parsed.pathname === "/otp/flights") {
      const date = parsed.searchParams.get("date") ?? "";
      return jsonResponse(listOtpFlights(date));
    }

    if (parsed.pathname === "/flights/ingest/batch" && method === "POST") {
      const flights = (body as { flights: { flightNumber: string; departureDate: string }[] }).flights;
      return jsonResponse({
        jobId: "job-operating-day",
        status: "accepted",
        flightsQueued: flights.length,
        message: "accepted",
        pollUrl: "/flights/ingest/jobs/job-operating-day",
      });
    }

    if (parsed.pathname === "/flights/ingest/jobs/job-operating-day") {
      const posted = [...requests].reverse().find((call) => call.url.includes("/flights/ingest/batch"));
      const flights = (posted?.body as { flights: Record<string, unknown>[] } | undefined)?.flights ?? [];
      return jsonResponse({
        jobId: "job-operating-day",
        status: "completed",
        flightsQueued: flights.length,
        flightsProcessed: flights.length,
        submittedAt: INSTANT.toISOString(),
        results: flights.map((flight) => ({
          flight,
          success: true,
          apis: {},
        })),
      });
    }

    if (parsed.pathname === "/flights") {
      const raw = parsed.searchParams.get("x-flights");
      return jsonResponse(raw ? JSON.parse(raw) : []);
    }

    return jsonResponse({ detail: `unhandled ${method} ${parsed.pathname}` }, 500);
  }));
}

function flightItem(flightNumber: string, departureDate: string): FlightListItem {
  return {
    airline: "GF",
    flightNumber,
    origin: "BAH",
    destination: "LHR",
    departureDate,
    status: "SCHEDULED",
    gate: "A1",
    aircraft: { type: "32N", registration: "A9C-FE" },
    schedule: { scheduledDeparture: `${departureDate}T08:00:00` },
    passengerCounts: {},
    jumpSeat: { cockpit: 0, cabin: 0 },
    fetchedAt: INSTANT.toISOString(),
  } as FlightListItem;
}

function renderApp(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
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

function otpCalls() {
  return requests.filter((call) => call.url.includes("/otp/flights"));
}

function otpDate(call: { url: string }) {
  return new URL(call.url).searchParams.get("date");
}

function ingestBodies() {
  return requests
    .filter((call) => call.method === "POST" && call.url.includes("/flights/ingest/batch"))
    .map((call) => call.body as { flights: { flightNumber: string; origin: string; departureDate: string; departureDateTime: string }[] });
}

beforeEach(() => {
  requests.length = 0;
  vi.useFakeTimers({ now: INSTANT, toFake: ["Date"] });
  installFetch();
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  Element.prototype.scrollIntoView = () => {};
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
    Element.prototype.setPointerCapture = () => {};
    Element.prototype.releasePointerCapture = () => {};
  }
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("operating day is Asia/Bahrain, not the browser calendar", () => {
  it("the frozen instant is a different calendar day in Bahrain than in the viewer zone", () => {
    expect(OPS_DAY).toBe("2026-11-23");
    expect(VIEWER_DAY).toBe("2026-11-22");
    expect(calendarDay(OPS_ZONE, new Date())).toBe(OPS_DAY);
    const localParts = [
      String(new Date().getFullYear()),
      String(new Date().getMonth() + 1).padStart(2, "0"),
      String(new Date().getDate()).padStart(2, "0"),
    ].join("-");
    expect(localParts).toBe(VIEWER_DAY);
    expect(localParts).not.toBe(OPS_DAY);
  });

  it("opens Today's Flights on the Bahrain day and ingests those rows", async () => {
    const user = userEvent.setup();
    const { default: TodayPage } = await import("@/app/today/page");
    renderApp(<TodayPage />);

    await waitFor(() => expect(otpCalls().length).toBeGreaterThan(0));
    expect(otpDate(otpCalls()[0])).toBe(OPS_DAY);

    const todayButton = await screen.findByRole("button", { name: /Today/ });
    expect(todayButton).toHaveTextContent(OPS_DAY);
    expect(todayButton).toHaveTextContent("Today");

    expect(await screen.findByText("GF100")).toBeInTheDocument();
    expect(screen.getByText("GF300")).toBeInTheDocument();
    expect(screen.queryByText("GF200")).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Select all flights" }));
    await user.click(screen.getByRole("button", { name: /^Ingest \d+ flights$/ }));

    await waitFor(() => expect(ingestBodies().length).toBe(1));
    const payload = ingestBodies()[0].flights;
    expect(payload.map((flight) => flight.flightNumber).sort()).toEqual(["100", "300"]);
    expect(payload.find((flight) => flight.flightNumber === "100")).toMatchObject({
      airline: "GF",
      origin: "BAH",
      departureDate: OPS_DAY,
      departureDateTime: "2026-11-22T22:10:00",
      flightSequenceNumber: 1001,
      serviceTypeCode: "J",
    });
    expect(payload.find((flight) => flight.flightNumber === "300")).toMatchObject({
      origin: "LHR",
      departureDate: VIEWER_DAY,
      flightSequenceNumber: 3003,
    });
    expect(payload.some((flight) => flight.flightNumber === "200")).toBe(false);
  });

  it("ingests one visible operating-day row with its station departure date", async () => {
    const user = userEvent.setup();
    const { default: TodayPage } = await import("@/app/today/page");
    renderApp(<TodayPage />);

    await user.click(await screen.findByRole("button", { name: "Ingest flight GF100" }));
    await waitFor(() => expect(ingestBodies().length).toBe(1));
    expect(ingestBodies()[0].flights).toEqual([
      expect.objectContaining({
        flightNumber: "100",
        origin: "BAH",
        departureDate: OPS_DAY,
        departureDateTime: "2026-11-22T22:10:00",
      }),
    ]);
  });

  it("Today, Reset to today, and the next operating day stay on the Bahrain calendar", async () => {
    const user = userEvent.setup();
    const { default: TodayPage } = await import("@/app/today/page");
    renderApp(<TodayPage />);
    await waitFor(() => expect(otpCalls().length).toBeGreaterThan(0));
    expect(otpDate(otpCalls()[0])).toBe(OPS_DAY);
    await screen.findByText("GF100");

    await user.click(screen.getByRole("button", { name: "Next day" }));
    await waitFor(() => expect(otpDate(otpCalls().at(-1)!)).toBe(OPS_NEXT));
    expect(screen.getByRole("button", { name: "Reset to today" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: new RegExp(`Today\\s+${OPS_NEXT}`) })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset to today" }));
    await waitFor(() => expect(otpDate(otpCalls().at(-1)!)).toBe(OPS_DAY));
    expect(screen.getByRole("button", { name: new RegExp(`Today\\s+${OPS_DAY}`) })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next day" }));
    await waitFor(() => expect(otpDate(otpCalls().at(-1)!)).toBe(OPS_NEXT));
    await user.click(screen.getByRole("button", { name: new RegExp(OPS_NEXT) }));
    await user.click(await screen.findByRole("button", { name: "Today" }));
    await waitFor(() => expect(otpDate(otpCalls().at(-1)!)).toBe(OPS_DAY));
    expect(screen.getByRole("button", { name: new RegExp(`Today\\s+${OPS_DAY}`) })).toBeInTheDocument();
  });

  it("an explicit calendar day is queried as the day that was picked", async () => {
    const user = userEvent.setup();
    const { default: TodayPage } = await import("@/app/today/page");
    renderApp(<TodayPage />);
    await waitFor(() => expect(otpCalls().length).toBeGreaterThan(0));

    const trigger = screen.getAllByRole("button").find((button) =>
      /\d{4}-\d{2}-\d{2}/.test(button.textContent ?? ""),
    );
    if (!trigger) throw new Error("operating-day trigger was not rendered");
    await user.click(trigger);
    const picked = await screen.findByRole("button", { name: /November 20/ });
    await user.click(picked);
    await waitFor(() => expect(otpDate(otpCalls().at(-1)!)).toBe("2026-11-20"));
    expect(screen.getByRole("button", { name: /2026-11-20/ })).toBeInTheDocument();
  });

  it("the workbench Today shortcut lists and re-ingests the Bahrain operating day", async () => {
    const user = userEvent.setup();
    const board = [
      flightItem("501", OPS_DAY),
      flightItem("502", VIEWER_DAY),
    ];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const method = init?.method ?? "GET";
      let body: unknown = null;
      if (typeof init?.body === "string") body = JSON.parse(init.body);
      requests.push({ method, url, body });
      const parsed = new URL(url);
      if (parsed.pathname === "/flights") return jsonResponse(board);
      if (parsed.pathname === "/flights/ingest/batch" && method === "POST") {
        return jsonResponse({
          jobId: "job-board",
          status: "accepted",
          flightsQueued: 1,
          message: "accepted",
          pollUrl: "/flights/ingest/jobs/job-board",
        });
      }
      if (parsed.pathname === "/flights/ingest/jobs/job-board") {
        return jsonResponse({
          jobId: "job-board",
          status: "completed",
          flightsQueued: 1,
          flightsProcessed: 1,
          submittedAt: INSTANT.toISOString(),
          results: [{
            flight: (body as { flights?: unknown[] } | null)?.flights?.[0] ?? {},
            success: true,
            apis: {},
          }],
        });
      }
      return jsonResponse({ detail: "dashboard unavailable" }, 500);
    });

    const { FlightWorkbench } = await import("@/components/dashboard/flight-workbench");
    renderApp(<FlightWorkbench />);

    const today = await screen.findByRole("button", { name: /Today quick filter/ });
    expect(today).toHaveTextContent("Today (1)");
    await user.click(today);

    const list = screen.getAllByRole("button", { name: /GF50/ });
    const names = list.map((node) => node.textContent ?? "");
    expect(names.some((name) => name.includes("GF501"))).toBe(true);
    expect(names.some((name) => name.includes("GF502"))).toBe(false);
    expect(names.some((name) => name.includes(OPS_DAY))).toBe(true);
    expect(names.some((name) => name.includes(VIEWER_DAY))).toBe(false);

    const opsButton = list.find((node) => (node.textContent ?? "").includes("GF501"))!;
    await user.click(within(opsButton).getByTitle("Re-ingest from Sabre"));
    await user.click(await screen.findByRole("button", { name: "Start Background Ingest" }));
    await waitFor(() => expect(ingestBodies().length).toBe(1));
    expect(ingestBodies()[0].flights[0]).toMatchObject({
      airline: "GF",
      flightNumber: "501",
      origin: "BAH",
      departureDate: OPS_DAY,
    });

    expect(screen.getByRole("link", { name: "Today" })).toHaveAttribute("href", "/today");
  });

  it("defaults the manual Sabre ingest date to the Bahrain day and still honors a typed day", async () => {
    const user = userEvent.setup();
    const { IngestionPanel } = await import("@/components/dashboard/ingestion-panel");
    renderApp(<IngestionPanel />);

    const dateInput = screen.getByPlaceholderText("DD/MM/YYYY");
    expect(dateInput).toHaveValue("23/11/2026");

    await user.type(screen.getByPlaceholderText("e.g. 2006"), "100");
    await user.type(screen.getByPlaceholderText("e.g. LHR"), "BAH");
    await user.click(screen.getByRole("button", { name: "Start Ingest" }));
    await waitFor(() => expect(ingestBodies().length).toBe(1));
    expect(ingestBodies()[0].flights[0]).toMatchObject({
      flightNumber: "100",
      origin: "BAH",
      departureDate: OPS_DAY,
      departureDateTime: `${OPS_DAY}T08:00:00`,
    });

    await user.clear(dateInput);
    await user.type(dateInput, "01122026");
    await user.click(screen.getByRole("button", { name: "Start Ingest" }));
    await waitFor(() => expect(ingestBodies().length).toBe(2));
    expect(ingestBodies()[1].flights[0].departureDate).toBe("2026-12-01");
  });

  it("defaults network analytics and its Today filter to the Bahrain operating day", async () => {
    const user = userEvent.setup();
    const board = [
      flightItem("901", OPS_DAY),
      flightItem("902", VIEWER_DAY),
    ];
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const method = init?.method ?? "GET";
      requests.push({ method, url, body: null });
      const parsed = new URL(url);
      if (parsed.pathname === "/flights") return jsonResponse(board);
      return jsonResponse({ detail: "analytics unavailable" }, 500);
    });

    const { default: AnalyticsPage } = await import("@/app/analytics/page");
    renderApp(<AnalyticsPage />);

    await waitFor(() => {
      const analytics = requests.find((call) => call.url.includes("/analytics/network"));
      expect(analytics).toBeTruthy();
    });
    const analytics = requests.find((call) => call.url.includes("/analytics/network"))!;
    const params = new URL(analytics.url).searchParams;
    expect(params.get("date_to")).toBe(OPS_DAY);
    expect(params.get("date_from")).toBe(OPS_RANGE_FROM);

    expect(screen.getByText("Nov 23")).toBeInTheDocument();
    expect(screen.getByText("Oct 29")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: /Today quick filter/ }));
    expect(screen.getByRole("button", { name: /GF901/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /GF902/ })).not.toBeInTheDocument();
  });
});
