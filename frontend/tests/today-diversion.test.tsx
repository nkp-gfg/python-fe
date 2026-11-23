/**
 * Today's Flights must show a diversion on the row that was diverted.
 *
 * When actualOrigin or actualDestination is present after trim and differs
 * from the scheduled airport ignoring case, that end of the route column
 * shows the operational code as its own label and the filed code as secondary
 * text "filed <CODE>" (blank scheduled airports use "filed —"). The arrow
 * stays between the origin side and the destination side.
 *
 * Comparison trims and ignores case, so " bah " is the same airport as BAH.
 * Null, "", and whitespace-only actuals are not a diversion.
 *
 * Sabre ingest keeps using the scheduled departure airport (flight.origin),
 * including batch ingest of the same rows.
 *
 * The route column is the grid child that contains the lucide arrow-right icon
 * (lucide-react 0.577 ArrowRight). Labels must be real text on that column,
 * not title/aria-only and not sr-only, hidden, or invisible.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import TodayPage from "@/app/today/page";
import { Providers } from "@/components/providers";
import type { OtpFlight, SabreIngestRequest } from "@/lib/types";

const FILED = "\u2014";

interface CapturedRequest {
  url: string;
  body: { flights: SabreIngestRequest[] };
}

function flight(partial: Partial<OtpFlight> & Pick<OtpFlight, "flightSequenceNumber" | "flightNumber" | "origin" | "destination">): OtpFlight {
  return {
    carrierCode: "GF",
    flightDate: "2026-03-30",
    sabreDepartureDate: "2026-03-30",
    scheduledDepartureUtc: "2026-03-30T08:00:00",
    scheduledArrivalUtc: "2026-03-30T12:00:00",
    flightStatus: "SCHEDULED",
    isCancelled: false,
    ...partial,
  };
}

const BOARD: OtpFlight[] = [
  flight({
    flightSequenceNumber: 205,
    flightNumber: "205",
    origin: "BAH",
    destination: "LHR",
    actualOrigin: "AUH",
    actualDestination: "LHR",
  }),
  flight({
    flightSequenceNumber: 206,
    flightNumber: "206",
    origin: "BAH",
    destination: "LHR",
    actualOrigin: "BAH",
    actualDestination: "DXB",
  }),
  flight({
    flightSequenceNumber: 207,
    flightNumber: "207",
    origin: "BAH",
    destination: "LHR",
    actualOrigin: "MCT",
    actualDestination: "DOH",
  }),
  flight({
    flightSequenceNumber: 208,
    flightNumber: "208",
    origin: "BAH",
    destination: "CAI",
    actualOrigin: "BAH",
    actualDestination: "CAI",
  }),
  flight({
    flightSequenceNumber: 209,
    flightNumber: "209",
    origin: "BAH",
    destination: "CAI",
    actualOrigin: " bah ",
    actualDestination: "cai",
  }),
  flight({
    flightSequenceNumber: 210,
    flightNumber: "210",
    origin: "BAH",
    destination: "RUH",
    actualOrigin: null,
    actualDestination: "",
  }),
  flight({
    flightSequenceNumber: 211,
    flightNumber: "211",
    origin: "KWI",
    destination: "DOH",
    actualOrigin: "   ",
    actualDestination: "\n",
  }),
  flight({
    flightSequenceNumber: 212,
    flightNumber: "212",
    origin: "BAH",
    destination: "",
    actualOrigin: null,
    actualDestination: "JED",
  }),
  flight({
    flightSequenceNumber: 213,
    flightNumber: "213",
    origin: "RUH",
    destination: "",
    actualOrigin: null,
    actualDestination: null,
  }),
  flight({
    flightSequenceNumber: 214,
    flightNumber: "214",
    origin: "BAH",
    destination: "CAI",
    actualOrigin: "AUH",
    actualDestination: "CAI",
    flightStatus: "CANCELLED",
    isCancelled: true,
  }),
  flight({
    flightSequenceNumber: 215,
    flightNumber: "215",
    origin: "BAH",
    destination: "LHR",
    actualOrigin: "BAH",
    actualDestination: "DXB",
    flightDate: "2026-03-29",
    sabreDepartureDate: "2026-03-30",
  }),
  flight({
    flightSequenceNumber: 216,
    flightNumber: "216",
    origin: "BAH",
    destination: "LHR",
    actualOrigin: " AUH ",
    actualDestination: " DXB ",
  }),
];

let requests: CapturedRequest[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installMatchMedia() {
  // next-themes 0.4.6 reads window.matchMedia("(prefers-color-scheme: dark)")
  // and calls addListener/removeListener. jsdom does not provide matchMedia.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => {
      const listeners = new Set<(event: { matches: boolean; media: string }) => void>();
      return {
        media: query,
        matches: false,
        onchange: null,
        addListener(listener: (event: { matches: boolean; media: string }) => void) {
          listeners.add(listener);
        },
        removeListener(listener: (event: { matches: boolean; media: string }) => void) {
          listeners.delete(listener);
        },
        addEventListener(_type: string, listener: (event: { matches: boolean; media: string }) => void) {
          listeners.add(listener);
        },
        removeEventListener(_type: string, listener: (event: { matches: boolean; media: string }) => void) {
          listeners.delete(listener);
        },
        dispatchEvent() {
          return true;
        },
      };
    },
  });
}

function installFetch() {
  installMatchMedia();
  requests = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/otp/flights?")) {
      return json(BOARD);
    }
    if (url.includes("/flights/ingest/batch") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { flights: SabreIngestRequest[] };
      requests.push({ url, body });
      return json({
        jobId: "job-test",
        status: "accepted",
        flightsQueued: body.flights.length,
        message: "accepted",
        pollUrl: "/flights/ingest/jobs/job-test",
      });
    }
    if (url.includes("/flights/ingest/jobs/job-test")) {
      const flights = requests.at(-1)?.body.flights ?? [];
      return json({
        jobId: "job-test",
        status: "completed",
        flightsQueued: flights.length,
        flightsProcessed: flights.length,
        submittedAt: "2026-03-30T00:00:00Z",
        startedAt: "2026-03-30T00:00:00Z",
        completedAt: "2026-03-30T00:00:01Z",
        error: null,
        results: flights.map((item) => ({
          flight: {
            airline: item.airline ?? "GF",
            flightNumber: item.flightNumber,
            origin: item.origin,
            departureDate: item.departureDate,
            departureDateTime: item.departureDateTime,
            flightSequenceNumber: item.flightSequenceNumber ?? null,
          },
          success: true,
          apis: {
            flightStatus: { status: "success" },
            passengerList: { status: "success" },
            reservations: { status: "success" },
          },
        })),
      });
    }
    throw new Error(`unexpected fetch ${init?.method ?? "GET"} ${url}`);
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderBoard() {
  installFetch();
  const view = render(
    <Providers>
      <TodayPage />
    </Providers>,
  );
  await screen.findByRole("button", { name: "Ingest flight GF205" });
  return view;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function routeColumn(ingestName: string): HTMLElement {
  const button = screen.getByRole("button", { name: ingestName });
  let row: HTMLElement | null = button.parentElement;
  while (row) {
    const route = [...row.children].find((child) => child.querySelector("svg.lucide-arrow-right"));
    if (route instanceof HTMLElement) return route;
    row = row.parentElement;
  }
  throw new Error(`Route column for ${ingestName} was not found`);
}

function arrow(route: HTMLElement): Element {
  const icon = route.querySelector("svg.lucide-arrow-right");
  if (!icon) throw new Error("route arrow missing");
  return icon;
}

function precedes(left: Element, right: Element): boolean {
  return (left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
}

function classNameOf(el: Element): string {
  return typeof el.className === "string" ? el.className : "";
}

function assertVisibleLabel(el: HTMLElement, route: HTMLElement) {
  let current: HTMLElement | null = el;
  while (current) {
    if (/\b(sr-only|hidden|invisible)\b/.test(classNameOf(current))) {
      throw new Error(`label "${el.textContent}" is not shown on the row (${classNameOf(current)})`);
    }
    if (current === route) return;
    current = current.parentElement;
  }
}

function smallestMatching(route: HTMLElement, predicate: (text: string) => boolean): HTMLElement[] {
  return [...route.querySelectorAll<HTMLElement>("*")].filter((el) => {
    const text = (el.textContent ?? "").trim();
    if (!predicate(text)) return false;
    return ![...el.children].some((child) => predicate((child.textContent ?? "").trim()));
  });
}

function codeMatches(text: string, code: string): boolean {
  const shown = text.trim();
  const expected = code.trim();
  return shown === expected || shown.toUpperCase() === expected.toUpperCase();
}

function filedPattern(scheduled: string): RegExp {
  const code = scheduled.trim() === "" ? FILED : scheduled.trim();
  return new RegExp(`^filed\\s+${escapeRegExp(code)}$`, "i");
}

function onSide(el: Element, route: HTMLElement, side: "origin" | "destination"): boolean {
  const icon = arrow(route);
  return side === "origin" ? precedes(el, icon) : precedes(icon, el);
}

function expectDiverted(route: HTMLElement, side: "origin" | "destination", operational: string, scheduled: string) {
  const filed = filedPattern(scheduled);
  const operationalLabels = smallestMatching(route, (text) => codeMatches(text, operational) && !/^filed\b/i.test(text))
    .filter((el) => onSide(el, route, side));
  const filedLabels = smallestMatching(route, (text) => filed.test(text))
    .filter((el) => onSide(el, route, side));
  if (operationalLabels.length !== 1 || filedLabels.length !== 1) {
    throw new Error(
      `${side} of "${route.textContent?.replace(/\s+/g, " ").trim()}" should show operational ${operational.trim()} and secondary "filed ${scheduled.trim() === "" ? FILED : scheduled.trim()}"`,
    );
  }
  assertVisibleLabel(operationalLabels[0], route);
  assertVisibleLabel(filedLabels[0], route);
  if (!precedes(operationalLabels[0], filedLabels[0])) {
    throw new Error(`${side} shows the filed airport before the operational airport`);
  }
}

function expectFiled(route: HTMLElement, side: "origin" | "destination", scheduled: string) {
  const filed = filedPattern(scheduled);
  const display = scheduled.trim() === "" ? FILED : scheduled.trim();
  const primary = smallestMatching(route, (text) => codeMatches(text, display) && !/^filed\b/i.test(text))
    .filter((el) => onSide(el, route, side));
  const secondary = smallestMatching(route, (text) => filed.test(text) || /^filed\b/i.test(text))
    .filter((el) => onSide(el, route, side));
  if (primary.length !== 1 || secondary.length !== 0) {
    throw new Error(
      `${side} of "${route.textContent?.replace(/\s+/g, " ").trim()}" should show only ${display} with no filed label`,
    );
  }
  assertVisibleLabel(primary[0], route);
}

describe("Today's Flights route column", () => {
  it("shows the operational airport and keeps the filed airport as secondary text", async () => {
    await renderBoard();
    const failures: string[] = [];
    const cases: Array<[string, "origin" | "destination", string, string, "diverted" | "filed"]> = [
      ["Ingest flight GF205", "origin", "AUH", "BAH", "diverted"],
      ["Ingest flight GF205", "destination", "LHR", "LHR", "filed"],
      ["Ingest flight GF206", "origin", "BAH", "BAH", "filed"],
      ["Ingest flight GF206", "destination", "DXB", "LHR", "diverted"],
      ["Ingest flight GF207", "origin", "MCT", "BAH", "diverted"],
      ["Ingest flight GF207", "destination", "DOH", "LHR", "diverted"],
      ["Ingest flight GF208", "origin", "BAH", "BAH", "filed"],
      ["Ingest flight GF208", "destination", "CAI", "CAI", "filed"],
      ["Ingest flight GF209", "origin", "BAH", "BAH", "filed"],
      ["Ingest flight GF209", "destination", "CAI", "CAI", "filed"],
      ["Ingest flight GF210", "origin", "BAH", "BAH", "filed"],
      ["Ingest flight GF210", "destination", "RUH", "RUH", "filed"],
      ["Ingest flight GF211", "origin", "KWI", "KWI", "filed"],
      ["Ingest flight GF211", "destination", "DOH", "DOH", "filed"],
      ["Ingest flight GF212", "origin", "BAH", "BAH", "filed"],
      ["Ingest flight GF212", "destination", "JED", "", "diverted"],
      ["Ingest flight GF213", "origin", "RUH", "RUH", "filed"],
      ["Ingest flight GF213", "destination", FILED, "", "filed"],
      ["Ingest flight GF214", "origin", "AUH", "BAH", "diverted"],
      ["Ingest flight GF214", "destination", "CAI", "CAI", "filed"],
      ["Ingest flight GF215", "origin", "BAH", "BAH", "filed"],
      ["Ingest flight GF215", "destination", "DXB", "LHR", "diverted"],
      ["Ingest flight GF216", "origin", "AUH", "BAH", "diverted"],
      ["Ingest flight GF216", "destination", "DXB", "LHR", "diverted"],
    ];

    for (const [name, side, operational, scheduled, kind] of cases) {
      try {
        const route = routeColumn(name);
        if (kind === "diverted") expectDiverted(route, side, operational, scheduled);
        else expectFiled(route, side, scheduled);
      } catch (error) {
        failures.push(`${name} ${side}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }

    expect(failures).toEqual([]);
  });

  it("ingests a diverted row with the scheduled departure airport", async () => {
    await renderBoard();
    fireEvent.click(screen.getByRole("button", { name: "Ingest flight GF205" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    const payload = requests[0].body.flights[0];
    expect(payload).toMatchObject({
      airline: "GF",
      flightNumber: "205",
      origin: "BAH",
      departureDate: "2026-03-30",
      departureDateTime: "2026-03-30T08:00:00",
      flightSequenceNumber: 205,
    });
    expect(payload.origin).not.toBe("AUH");
  });

  it("ingests a rollover diversion with the scheduled origin and Sabre date", async () => {
    await renderBoard();
    fireEvent.click(screen.getByRole("button", { name: "Ingest flight GF215" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    const payload = requests[0].body.flights[0];
    expect(payload.origin).toBe("BAH");
    expect(payload.origin).not.toBe("DXB");
    expect(payload.departureDate).toBe("2026-03-30");
    expect(payload.flightNumber).toBe("215");
  });

  it("batch-ingests each selected flight with that flight's scheduled origin", async () => {
    await renderBoard();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select flight GF205" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select flight GF207" }));
    fireEvent.click(screen.getByRole("button", { name: /Ingest 2 flights/ }));
    await waitFor(() => expect(requests).toHaveLength(1));
    const byNumber = Object.fromEntries(requests[0].body.flights.map((item) => [item.flightNumber, item]));
    expect(Object.keys(byNumber).sort()).toEqual(["205", "207"]);
    expect(byNumber["205"].origin).toBe("BAH");
    expect(byNumber["207"].origin).toBe("BAH");
    expect(byNumber["205"].origin).not.toBe("AUH");
    expect(byNumber["207"].origin).not.toBe("MCT");
    expect(byNumber["207"].origin).not.toBe("DOH");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Ingest flight GF205" }).textContent).toContain("Done");
      expect(screen.getByRole("button", { name: "Ingest flight GF207" }).textContent).toContain("Done");
    }, { timeout: 10_000 });
  });
});
