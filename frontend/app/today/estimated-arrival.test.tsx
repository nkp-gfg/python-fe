/**
 * Spec for Today's Flights arrival clocks.
 *
 * GET /otp/flights already returns estimatedBlockOnUtc. The row prints a
 * revised estimatedBlockOffUtc beside STD, and it prints actualBlockOnUtc
 * under STA, but a revised estimated block-on never appears beside STA.
 *
 * These tests drive the real Today page: they answer the page's fetch with
 * an OTP flight payload and read the STD/STA cells the operator sees.
 * They fail until a revised estimate is printed beside STA the same way a
 * revised estimate is printed beside STD (a rose "→ HH:MM" marker, and only
 * when the displayed minute differs).
 */
import { cleanup, render, waitFor, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import TodayPage from "./page";
import { Providers } from "@/components/providers";

type OtpPayload = {
  flightSequenceNumber: number;
  carrierCode: string;
  flightNumber: string;
  origin: string;
  destination: string;
  flightDate: string;
  sabreDepartureDate: string;
  scheduledDepartureUtc: string | null;
  estimatedBlockOffUtc: string | null;
  scheduledArrivalUtc: string | null;
  estimatedBlockOnUtc: string | null;
  actualBlockOffUtc: string | null;
  actualBlockOnUtc: string | null;
  flightStatus: string;
  isCancelled: boolean;
  totalPax: number;
  delayDetails: null;
};

const REVISED_CLASS = "text-rose-500";

function clock(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function flight(partial: Partial<OtpPayload> & Pick<OtpPayload, "flightSequenceNumber" | "flightNumber">): OtpPayload {
  return {
    carrierCode: "GF",
    origin: "BAH",
    destination: "LHR",
    flightDate: "2026-03-30",
    sabreDepartureDate: "2026-03-30",
    scheduledDepartureUtc: "2026-03-30T09:00:00",
    estimatedBlockOffUtc: "2026-03-30T09:00:00",
    scheduledArrivalUtc: "2026-03-30T14:00:00",
    estimatedBlockOnUtc: "2026-03-30T14:00:00",
    actualBlockOffUtc: null,
    actualBlockOnUtc: null,
    flightStatus: "SCHEDULED",
    isCancelled: false,
    totalPax: 120,
    delayDetails: null,
    ...partial,
  };
}

function installOtpFetch(flights: OtpPayload[]) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const fetchMock: typeof fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    calls.push({ url, init });
    if (url.includes("/otp/flights?")) {
      return new Response(JSON.stringify(flights), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
  };
  globalThis.fetch = fetchMock;
  return calls;
}

function renderToday() {
  return render(
    <Providers>
      <TodayPage />
    </Providers>,
  );
}

function rowGrid(flightNumber: string): HTMLElement {
  const label = screen.getByText(flightNumber);
  const grid = label.closest(".transition-colors");
  if (!(grid instanceof HTMLElement)) {
    throw new Error(`No flight row grid for ${flightNumber}`);
  }
  return grid;
}

function column(grid: HTMLElement, header: "STD" | "STA"): HTMLElement {
  const headerGrid = screen.getByText(header).parentElement;
  if (!(headerGrid instanceof HTMLElement)) {
    throw new Error(`Missing ${header} header`);
  }
  const headerIndex = Array.from(headerGrid.children).findIndex((cell) => cell.textContent === header);
  if (headerIndex < 0) {
    throw new Error(`${header} header column not found`);
  }
  const cell = grid.children.item(headerIndex);
  if (!(cell instanceof HTMLElement)) {
    throw new Error(`${header} cell missing from flight row`);
  }
  return cell;
}

function revisedMarker(cell: HTMLElement): HTMLElement | null {
  const marker = cell.querySelector(`.${REVISED_CLASS}`);
  return marker instanceof HTMLElement ? marker : null;
}

beforeAll(() => {
  if (typeof window.matchMedia !== "function") {
    window.matchMedia = (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList;
  }
});

afterEach(() => {
  cleanup();
});

describe("Today's Flights estimated arrival", () => {
  it("loads GET /otp/flights with no credential and prints a later estimated block-on beside STA", async () => {
    const row = flight({
      flightSequenceNumber: 9001,
      flightNumber: "2057",
      scheduledDepartureUtc: "2026-03-30T09:00:00",
      estimatedBlockOffUtc: "2026-03-30T09:00:00",
      scheduledArrivalUtc: "2026-03-30T14:00:00",
      estimatedBlockOnUtc: "2026-03-30T15:25:00",
    });
    const calls = installOtpFetch([row]);
    renderToday();

    await waitFor(() => {
      expect(screen.getByText("GF2057")).toBeTruthy();
    });

    expect(calls.length).toBeGreaterThan(0);
    const request = calls[0];
    expect(request.url).toMatch(/\/otp\/flights\?date=\d{4}-\d{2}-\d{2}$/);
    const headers = new Headers(request.init?.headers);
    expect(headers.get("authorization")).toBeNull();
    expect(headers.get("cookie")).toBeNull();
    expect(request.init?.credentials).toBeUndefined();

    const grid = rowGrid("GF2057");
    const sta = column(grid, "STA");
    const std = column(grid, "STD");
    expect(std.textContent).toContain(clock(row.scheduledDepartureUtc));
    expect(revisedMarker(std)).toBeNull();
    expect(sta.textContent).toContain(clock(row.scheduledArrivalUtc));
    expect(revisedMarker(sta)?.textContent).toBe(`→ ${clock(row.estimatedBlockOnUtc)}`);
  });

  it("prints an earlier estimated block-on beside STA", async () => {
    const row = flight({
      flightSequenceNumber: 9002,
      flightNumber: "118",
      estimatedBlockOffUtc: null,
      scheduledArrivalUtc: "2026-03-30T14:00:00",
      estimatedBlockOnUtc: "2026-03-30T13:10:00",
    });
    installOtpFetch([row]);
    renderToday();

    await waitFor(() => {
      expect(screen.getByText("GF118")).toBeTruthy();
    });

    const sta = column(rowGrid("GF118"), "STA");
    expect(sta.textContent).toContain(clock(row.scheduledArrivalUtc));
    expect(revisedMarker(sta)?.textContent).toBe(`→ ${clock(row.estimatedBlockOnUtc)}`);
  });

  it("keeps the estimated block-on beside STA when actual block-on is also present", async () => {
    const row = flight({
      flightSequenceNumber: 9003,
      flightNumber: "042",
      estimatedBlockOffUtc: "2026-03-30T09:40:00",
      actualBlockOffUtc: "2026-03-30T09:42:00",
      scheduledArrivalUtc: "2026-03-30T14:00:00",
      estimatedBlockOnUtc: "2026-03-30T15:25:00",
      actualBlockOnUtc: "2026-03-30T16:05:00",
    });
    installOtpFetch([row]);
    renderToday();

    await waitFor(() => {
      expect(screen.getByText("GF042")).toBeTruthy();
    });

    const grid = rowGrid("GF042");
    const std = column(grid, "STD");
    const sta = column(grid, "STA");
    expect(revisedMarker(std)?.textContent).toBe(`→ ${clock(row.estimatedBlockOffUtc)}`);
    expect(std.textContent).toContain(`ATD ${clock(row.actualBlockOffUtc)}`);
    expect(sta.textContent).toContain(clock(row.scheduledArrivalUtc));
    expect(sta.textContent).toContain(`ATA ${clock(row.actualBlockOnUtc)}`);
    expect(revisedMarker(sta)?.textContent).toBe(`→ ${clock(row.estimatedBlockOnUtc)}`);
  });

  it("does not mark a second flight's STA with another flight's estimate", async () => {
    const onTime = flight({
      flightSequenceNumber: 9004,
      flightNumber: "300",
      scheduledArrivalUtc: "2026-03-30T11:15:00",
      estimatedBlockOnUtc: "2026-03-30T11:15:00",
    });
    const revised = flight({
      flightSequenceNumber: 9005,
      flightNumber: "301",
      scheduledArrivalUtc: "2026-03-30T18:00:00",
      estimatedBlockOnUtc: "2026-03-30T19:45:00",
    });
    installOtpFetch([onTime, revised]);
    renderToday();

    await waitFor(() => {
      expect(screen.getByText("GF301")).toBeTruthy();
    });

    const onTimeSta = column(rowGrid("GF300"), "STA");
    const revisedSta = column(rowGrid("GF301"), "STA");
    expect(revisedMarker(onTimeSta)).toBeNull();
    expect(onTimeSta.textContent).not.toContain(clock(revised.estimatedBlockOnUtc));
    expect(revisedMarker(revisedSta)?.textContent).toBe(`→ ${clock(revised.estimatedBlockOnUtc)}`);
  });

  it("leaves STA unmarked when the estimate is missing or the same displayed minute", async () => {
    const sameMinute = flight({
      flightSequenceNumber: 9006,
      flightNumber: "400",
      scheduledDepartureUtc: "2026-03-30T09:00:00",
      estimatedBlockOffUtc: "2026-03-30T09:40:00",
      scheduledArrivalUtc: "2026-03-30T14:00:00",
      estimatedBlockOnUtc: "2026-03-30T14:00:59",
    });
    const missing = flight({
      flightSequenceNumber: 9007,
      flightNumber: "401",
      estimatedBlockOffUtc: null,
      scheduledArrivalUtc: "2026-03-30T16:30:00",
      estimatedBlockOnUtc: null,
    });
    const equal = flight({
      flightSequenceNumber: 9008,
      flightNumber: "402",
      scheduledArrivalUtc: "2026-03-30T17:05:00",
      estimatedBlockOnUtc: "2026-03-30T17:05:00",
    });
    installOtpFetch([sameMinute, missing, equal]);
    renderToday();

    await waitFor(() => {
      expect(screen.getByText("GF400")).toBeTruthy();
    });

    const sameGrid = rowGrid("GF400");
    expect(revisedMarker(column(sameGrid, "STD"))?.textContent).toBe(`→ ${clock(sameMinute.estimatedBlockOffUtc)}`);
    expect(revisedMarker(column(sameGrid, "STA"))).toBeNull();
    expect(column(sameGrid, "STA").textContent).toContain(clock(sameMinute.scheduledArrivalUtc));

    expect(revisedMarker(column(rowGrid("GF401"), "STA"))).toBeNull();
    expect(revisedMarker(column(rowGrid("GF402"), "STA"))).toBeNull();
  });
});
