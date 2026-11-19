/**
 * Today's Flights must label station scheduled time as STD/STA.
 *
 * The OTP payload carries both a UTC instant and the departure/arrival
 * station's local schedule. These tests render the real page against that
 * payload (through fetchOtpFlights) and require the station clocks.
 *
 * Run from frontend/:
 *   TZ=America/New_York npx vitest run --config vitest.config.ts app/today/station-time.test.tsx
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { OtpFlight } from "@/lib/types";
import TodayPage from "./page";

function browserClock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

beforeAll(() => {
  // next-themes reads matchMedia; jsdom does not provide it. This does not
  // affect Date parsing or the flight clocks under test.
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

  // Lock the process zone so an offset-bearing UTC instant cannot accidentally
  // equal the Bahrain (+3) or London (+1) station clocks under test.
  expect(browserClock("2026-03-30T22:00:00+00:00")).toBe("18:00");
  expect(browserClock("2026-03-30T22:00:00")).toBe("22:00");
  expect(browserClock("2026-03-30T06:10:00+00:00")).toBe("02:10");
  expect(browserClock("2026-03-30T14:20:00+00:00")).toBe("10:20");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function flight(partial: Partial<OtpFlight> & Pick<OtpFlight, "flightNumber" | "flightSequenceNumber">): OtpFlight {
  return {
    carrierCode: "GF",
    origin: "BAH",
    destination: "LHR",
    flightDate: "2026-03-30",
    flightStatus: "SCHEDULED",
    isCancelled: false,
    totalPax: 0,
    delayDetails: null,
    ...partial,
  };
}

async function renderOtpFlights(flights: OtpFlight[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/otp/flights?")) {
      return new Response(JSON.stringify(flights), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);

  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  });

  render(
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <QueryClientProvider client={client}>
        <ToastProvider>
          <TodayPage />
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );

  await waitFor(() => {
    expect(screen.getByText(`GF${flights[0].flightNumber}`)).toBeTruthy();
  });

  expect(fetchMock).toHaveBeenCalled();
  const requested = String(fetchMock.mock.calls[0][0]);
  expect(requested).toContain("/otp/flights?date=");
}

function rowCells(flightNumber: string): HTMLElement[] {
  const label = screen.getByText(`GF${flightNumber}`);
  const row = label.closest("[class*='sm:grid']");
  if (!row) throw new Error(`flight row not found for GF${flightNumber}`);
  return Array.from(row.children) as HTMLElement[];
}

function text(el: HTMLElement): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}

function expectStationHeaders() {
  const header = screen.getByText("STD").parentElement;
  expect(header).toBeTruthy();
  const labels = Array.from(header!.children).map((el) => el.textContent?.trim());
  expect(labels[6]).toBe("STD");
  expect(labels[7]).toBe("STA");
}

describe("Today's Flights station clocks", () => {
  it("shows station STD, STA, estimate, ATD, and ATA when UTC strings have no offset", async () => {
    // Naive ISO strings are what datetime.isoformat() emits for a timestamp
    // without a zone. The digits are the UTC clock, not Bahrain or London.
    await renderOtpFlights([
      flight({
        flightNumber: "101",
        flightSequenceNumber: 101,
        scheduledDepartureUtc: "2026-03-30T06:10:00",
        scheduledDepartureLocal: "2026-03-30T09:10:00",
        estimatedBlockOffUtc: "2026-03-30T06:45:00",
        actualBlockOffUtc: "2026-03-30T07:05:00",
        scheduledArrivalUtc: "2026-03-30T14:20:00",
        scheduledArrivalLocal: "2026-03-30T15:20:00",
        actualBlockOnUtc: "2026-03-30T14:40:00",
      }),
    ]);

    expectStationHeaders();
    const cells = rowCells("101");
    const std = text(cells[6]);
    const sta = text(cells[7]);

    expect(std).toContain("09:10");
    expect(std).toContain("→ 09:45");
    expect(std).toContain("ATD 10:05");
    expect(std).not.toMatch(/06:10|06:45|07:05/);

    expect(sta).toContain("15:20");
    expect(sta).toContain("ATA 15:40");
    expect(sta).not.toMatch(/14:20|14:40/);
  });

  it("shows station clocks when UTC strings carry an explicit offset", async () => {
    // An offset is what datetime.isoformat() emits for an aware UTC instant.
    // Formatting that instant in America/New_York yields 02:10, not 09:10.
    await renderOtpFlights([
      flight({
        flightNumber: "202",
        flightSequenceNumber: 202,
        scheduledDepartureUtc: "2026-03-30T06:10:00+00:00",
        scheduledDepartureLocal: "2026-03-30T09:10:00",
        estimatedBlockOffUtc: "2026-03-30T06:45:00+00:00",
        actualBlockOffUtc: "2026-03-30T07:05:00+00:00",
        scheduledArrivalUtc: "2026-03-30T14:20:00+00:00",
        scheduledArrivalLocal: "2026-03-30T15:20:00",
        actualBlockOnUtc: "2026-03-30T14:40:00+00:00",
      }),
    ]);

    const cells = rowCells("202");
    const std = text(cells[6]);
    const sta = text(cells[7]);

    expect(std).toContain("09:10");
    expect(std).toContain("→ 09:45");
    expect(std).toContain("ATD 10:05");
    expect(std).not.toMatch(/02:10|02:45|03:05|06:10/);

    expect(sta).toContain("15:20");
    expect(sta).toContain("ATA 15:40");
    expect(sta).not.toMatch(/10:20|10:40|14:20/);
  });

  it("does not flag a delay when the UTC instant matches but only one string has an offset", async () => {
    await renderOtpFlights([
      flight({
        flightNumber: "303",
        flightSequenceNumber: 303,
        scheduledDepartureUtc: "2026-03-30T06:10:00",
        scheduledDepartureLocal: "2026-03-30T09:10:00",
        estimatedBlockOffUtc: "2026-03-30T06:10:00+00:00",
        scheduledArrivalUtc: "2026-03-30T14:20:00+00:00",
        scheduledArrivalLocal: "2026-03-30T15:20:00",
      }),
    ]);

    const cells = rowCells("303");
    const std = text(cells[6]);
    const sta = text(cells[7]);

    expect(std).toBe("09:10");
    expect(std).not.toContain("→");
    expect(std).not.toMatch(/06:10|02:10/);
    expect(sta).toBe("15:20");
  });

  it("converts estimated and actual instants into each station zone across midnight", async () => {
    // Bahrain local is the next calendar day. London is only +1.
    // Applying one offset to both ends, or showing the UTC digits, is wrong.
    await renderOtpFlights([
      flight({
        flightNumber: "404",
        flightSequenceNumber: 404,
        scheduledDepartureUtc: "2026-03-30T22:00:00+00:00",
        scheduledDepartureLocal: "2026-03-31T01:00:00",
        estimatedBlockOffUtc: "2026-03-30T22:40:00",
        actualBlockOffUtc: "2026-03-30T22:15:00+00:00",
        scheduledArrivalUtc: "2026-03-31T05:30:00",
        scheduledArrivalLocal: "2026-03-31T06:30:00",
        actualBlockOnUtc: "2026-03-31T05:50:00+00:00",
      }),
    ]);

    const cells = rowCells("404");
    const std = text(cells[6]);
    const sta = text(cells[7]);

    expect(std).toContain("01:00");
    expect(std).toContain("→ 01:40");
    expect(std).toContain("ATD 01:15");
    expect(std).not.toMatch(/22:00|22:40|22:15|18:00|18:15/);

    expect(sta).toContain("06:30");
    expect(sta).toContain("ATA 06:50");
    expect(sta).not.toMatch(/05:30|05:50|01:50|08:30|08:50/);
  });

  it("shows the station schedule when the UTC instant is absent", async () => {
    await renderOtpFlights([
      flight({
        flightNumber: "505",
        flightSequenceNumber: 505,
        scheduledDepartureUtc: null,
        scheduledDepartureLocal: "2026-03-30T11:25:00",
        scheduledArrivalUtc: null,
        scheduledArrivalLocal: "2026-03-30T16:05:00",
      }),
    ]);

    const cells = rowCells("505");
    expect(text(cells[6])).toBe("11:25");
    expect(text(cells[7])).toBe("16:05");
  });
});
