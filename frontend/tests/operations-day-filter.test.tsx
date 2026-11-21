/**
 * Spec for the flight board's operating day.
 *
 * Gulf Air operations use the Asia/Bahrain calendar (UTC+3, no daylight
 * saving). Stored departure dates are Sabre calendar dates, copied through
 * as YYYY-MM-DD strings. Today, Yesterday, and Tomorrow must be that
 * calendar, and each chip must show the date it filters to.
 *
 * These tests fail while the screens still take that day from the browser's
 * local calendar.
 *
 * Not covered here: the calendar day-cell click, which writes the civil date
 * of the cell the operator points at, and SabreClient.get_flight_status's UTC
 * fallback when a caller omits departure_date. Analytics Tomorrow uses the
 * same map as Today; the default analytics window ends on today, so that chip
 * is exercised on the flight board instead.
 */
import type { ReactElement } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: () => {},
    push: () => {},
    refresh: () => {},
    back: () => {},
    prefetch: () => {},
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

import { Providers } from "@/components/providers";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { IngestionPanel } from "@/components/dashboard/ingestion-panel";
import type { FlightListItem, OtpFlight } from "@/lib/types";
import AnalyticsPage from "@/app/analytics/page";
import TodayPage from "@/app/today/page";

const OPERATIONS_TIME_ZONE = "Asia/Bahrain";

const SCENARIOS = [
  {
    name: "browser calendar is still the previous day",
    timeZone: "America/Los_Angeles",
    instant: "2026-06-15T22:30:00.000Z",
  },
  {
    name: "browser calendar is already the next day",
    timeZone: "Pacific/Kiritimati",
    instant: "2026-06-15T20:30:00.000Z",
  },
] as const;

type RestoreClock = () => void;

let restoreClock: RestoreClock = () => {};
const originalTz = process.env.TZ;

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  cleanup();
  restoreClock();
  if (originalTz === undefined) delete process.env.TZ;
  else process.env.TZ = originalTz;
  vi.unstubAllGlobals();
});

function calendarInZone(instant: Date, timeZone: string): string {
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

function shiftIso(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day + days));
  const yyyy = shifted.getUTCFullYear();
  const mm = String(shifted.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(shifted.getUTCDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function browserCalendar(instant: Date): string {
  const year = instant.getFullYear();
  const month = String(instant.getMonth() + 1).padStart(2, "0");
  const day = String(instant.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function freezeClock(timeZone: string, instantIso: string): Date {
  process.env.TZ = timeZone;
  const fixedMs = Date.parse(instantIso);
  const RealDate = Date;

  function FrozenDate(this: Date, ...args: unknown[]) {
    if (!new.target) return RealDate();
    if (args.length === 0) return new RealDate(fixedMs);
    return new RealDate(...(args as ConstructorParameters<typeof RealDate>));
  }

  FrozenDate.prototype = RealDate.prototype;
  FrozenDate.now = () => fixedMs;
  FrozenDate.parse = RealDate.parse;
  FrozenDate.UTC = RealDate.UTC;
  globalThis.Date = FrozenDate as unknown as DateConstructor;

  restoreClock = () => {
    globalThis.Date = RealDate;
  };

  const instant = new Date();
  if (instant.getTime() !== fixedMs) {
    throw new Error(`clock freeze failed: ${instant.toISOString()}`);
  }
  return instant;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const requestedUrls: string[] = [];

function boardFlight(departureDate: string): FlightListItem {
  return {
    airline: "GF",
    flightNumber: departureDate.slice(5).replace("-", ""),
    origin: "BAH",
    destination: "LHR",
    departureDate,
    status: "SCHEDULED",
    gate: "A1",
    aircraft: { type: "789", registration: "A9C-BAH" },
    schedule: {
      scheduledDeparture: `${departureDate}T09:00:00`,
      estimatedDeparture: `${departureDate}T09:00:00`,
      scheduledArrival: `${departureDate}T15:00:00`,
      estimatedArrival: `${departureDate}T15:00:00`,
      durationMinutes: 360,
    },
    passengerCounts: {},
    jumpSeat: { cockpit: 0, cabin: 0, cockpitInUse: false, cabinInUse: false },
    fetchedAt: "2026-06-15T18:00:00Z",
  };
}

function otpFlight(departureDate: string): OtpFlight {
  return {
    flightSequenceNumber: Number(departureDate.replaceAll("-", "")),
    carrierCode: "GF",
    flightNumber: departureDate.slice(5).replace("-", ""),
    origin: "BAH",
    destination: "LHR",
    flightDate: departureDate,
    scheduledDepartureLocal: `${departureDate}T09:00:00`,
    scheduledArrivalLocal: `${departureDate}T15:00:00`,
    scheduledDepartureUtc: `${departureDate}T06:00:00Z`,
    scheduledArrivalUtc: `${departureDate}T12:00:00Z`,
    flightStatus: "SCHEDULED",
    isCancelled: false,
    sabreDepartureDate: departureDate,
    totalPax: 1,
  };
}

/**
 * Same predicate as backend/api/routes/otp.py list query:
 * scheduled_departure_local::date = requested OR scheduled_arrival_local::date = requested.
 */
function otpRowsForRequestedDate(rows: OtpFlight[], requested: string | null): OtpFlight[] {
  return rows.filter((row) => {
    const departure = (row.scheduledDepartureLocal ?? "").slice(0, 10);
    const arrival = (row.scheduledArrivalLocal ?? "").slice(0, 10);
    return departure === requested || arrival === requested;
  });
}

function installFetch(board: FlightListItem[], otp: OtpFlight[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      requestedUrls.push(url);
      const path = new URL(url).pathname;

      if (path === "/otp/flights") {
        const requested = new URL(url).searchParams.get("date");
        return jsonResponse(otpRowsForRequestedDate(otp, requested));
      }
      if (path === "/analytics/network") {
        return jsonResponse({ detail: "unused" }, 503);
      }
      if (/^\/flights\/[^/]+\/(dashboard|tree)$/.test(path)) {
        return jsonResponse({ detail: "unused" }, 404);
      }
      if (path === "/flights") {
        return jsonResponse(board);
      }
      return jsonResponse({ detail: `unhandled ${path}` }, 404);
    }),
  );
}

function shownFlightNumbers(root: ParentNode): string[] {
  const matches = [...(root.textContent ?? "").matchAll(/GF(\d{4})/g)].map((match) => match[1]);
  return [...new Set(matches)].sort();
}

function flightNumbers(root: ParentNode): string[] {
  const buttons = [...root.querySelectorAll("button")];
  const numbers = new Set<string>();
  for (const button of buttons) {
    const match = button.textContent?.match(/GF(\d{4})/);
    if (match) numbers.add(match[1]);
  }
  return [...numbers].sort();
}

function chip(root: HTMLElement, label: "Yesterday" | "Today" | "Tomorrow"): HTMLElement {
  const matches = [...root.querySelectorAll("button")].filter((button) => {
    const blob = `${button.getAttribute("aria-label") ?? ""}\n${button.textContent ?? ""}`;
    return new RegExp(`\\b${label}\\b`).test(blob) && /quick filter/i.test(blob);
  });
  if (matches.length === 0) {
    throw new Error(`missing ${label} chip`);
  }
  return matches[0];
}

function chipLabel(root: HTMLElement, label: "Yesterday" | "Today" | "Tomorrow"): string {
  const button = chip(root, label);
  return `${button.getAttribute("aria-label") ?? ""}\n${button.textContent ?? ""}`;
}

function renderWithProviders(ui: ReactElement) {
  return render(<Providers>{ui}</Providers>);
}

describe.each(SCENARIOS)("operating day when the $name", ({ timeZone, instant }) => {
  let opsToday = "";
  let opsYesterday = "";
  let opsTomorrow = "";
  let browserToday = "";
  let board: FlightListItem[] = [];
  let otp: OtpFlight[] = [];

  beforeEach(() => {
    const now = freezeClock(timeZone, instant);
    opsToday = calendarInZone(now, OPERATIONS_TIME_ZONE);
    opsYesterday = shiftIso(opsToday, -1);
    opsTomorrow = shiftIso(opsToday, 1);
    browserToday = browserCalendar(now);
    expect(browserToday).not.toBe(opsToday);

    const dates = [...new Set([opsYesterday, opsToday, opsTomorrow, shiftIso(browserToday, -1), shiftIso(browserToday, 1), browserToday])].sort();
    board = dates.map(boardFlight);
    otp = dates.map(otpFlight);
    requestedUrls.length = 0;
    installFetch(board, otp);
  });

  it("keeps the flight board on the Bahrain day after Today, Yesterday, and Tomorrow", async () => {
    const view = renderWithProviders(<FlightWorkbench />);
    await screen.findByRole("button", { name: /Today quick filter/i });

    fireEvent.click(chip(view.container, "Today"));
    expect(flightNumbers(view.container)).toEqual([opsToday.slice(5).replace("-", "")]);

    fireEvent.click(chip(view.container, "Yesterday"));
    expect(flightNumbers(view.container)).toEqual([opsYesterday.slice(5).replace("-", "")]);

    fireEvent.click(chip(view.container, "Tomorrow"));
    expect(flightNumbers(view.container)).toEqual([opsTomorrow.slice(5).replace("-", "")]);

    expect(chipLabel(view.container, "Yesterday")).toContain(opsYesterday);
    expect(chipLabel(view.container, "Today")).toContain(opsToday);
    expect(chipLabel(view.container, "Tomorrow")).toContain(opsTomorrow);
  });

  it("keeps the analytics board on the Bahrain day, including the default range", async () => {
    const view = renderWithProviders(<AnalyticsPage />);
    await screen.findByRole("button", { name: /Today quick filter/i });

    fireEvent.click(chip(view.container, "Today"));
    expect(flightNumbers(view.container)).toEqual([opsToday.slice(5).replace("-", "")]);

    fireEvent.click(chip(view.container, "Yesterday"));
    expect(flightNumbers(view.container)).toEqual([opsYesterday.slice(5).replace("-", "")]);

    const opsDayLabel = new Intl.DateTimeFormat("en-US", {
      timeZone: OPERATIONS_TIME_ZONE,
      month: "short",
      day: "numeric",
    }).format(new Date(instant));
    const rangeButtons = screen.getAllByRole("button").map((button) => button.textContent?.replace(/\s+/g, " ").trim());
    expect(rangeButtons).toContain(opsDayLabel);

    expect(chipLabel(view.container, "Yesterday")).toContain(opsYesterday);
    expect(chipLabel(view.container, "Today")).toContain(opsToday);
  });

  it("opens today's flights on the Bahrain day and returns there from Today and Reset", async () => {
    const view = renderWithProviders(<TodayPage />);
    const opsNumber = `GF${opsToday.slice(5).replace("-", "")}`;
    const nextNumber = `GF${opsTomorrow.slice(5).replace("-", "")}`;

    await screen.findByText(/GF\d{4}/);
    expect(shownFlightNumbers(view.container)).toEqual([opsToday.slice(5).replace("-", "")]);
    expect(view.container.textContent).toContain(opsToday);
    expect(requestedUrls.filter((url) => url.includes("/otp/flights")).at(-1)).toContain(`date=${opsToday}`);

    fireEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(await screen.findByText(nextNumber)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Reset to today" }));
    expect(await screen.findByText(opsNumber)).toBeTruthy();
    expect(shownFlightNumbers(view.container)).toEqual([opsToday.slice(5).replace("-", "")]);

    fireEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(await screen.findByText(nextNumber)).toBeTruthy();

    const dateTrigger = screen.getAllByRole("button").find((button) =>
      (button.textContent ?? "").includes(opsTomorrow),
    );
    if (!dateTrigger) throw new Error("missing date trigger");
    fireEvent.click(dateTrigger);
    const todayInPopover = await screen.findByRole("button", { name: "Today" });
    fireEvent.click(todayInPopover);
    expect(await screen.findByText(opsNumber)).toBeTruthy();
    expect(shownFlightNumbers(view.container)).toEqual([opsToday.slice(5).replace("-", "")]);
  });

  it("prefills Sabre ingest with the Bahrain departure date", () => {
    renderWithProviders(<IngestionPanel />);
    const [year, month, day] = opsToday.split("-");
    const input = document.querySelector<HTMLInputElement>('input[name="departureDate"]');
    expect(input?.value).toBe(`${day}/${month}/${year}`);
  });
});
