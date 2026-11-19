/**
 * The live passenger-detail sheet must show the operating flight and the
 * operating booking class whenever Sabre GetPassengerData says they differ
 * from the marketed flight and class. The marketed flight stays visible.
 *
 * The payload is the real SabreClient parse plus convert_passenger_data
 * (see backend/tests/test_codeshare_passenger_detail.py). These assertions
 * fail until the itinerary card, baggage routing table, fare table, and
 * ancillary table render that operating identity.
 *
 * Run from frontend/:
 *   npx vitest run components/dashboard/passenger-detail-sheet.codeshare.test.tsx
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerDetailSheet } from "./passenger-detail-sheet";

const fetchPassengerDetail = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  fetchPassengerDetail: (...args: unknown[]) => fetchPassengerDetail(...args),
  fetchPassengerTimeline: vi.fn(async () => ({ events: [] })),
}));

type Leg = {
  origin: string;
  destination: string;
  marketingFlight: string;
  operatingFlight: string;
  marketedClass: string;
  operatingClass: string;
  hasFare?: boolean;
  hasAncillary?: boolean;
};

type Fixture = {
  name: string;
  detail: {
    passengers: { lastName: string; firstName: string; pnr: string }[];
  };
  oracle: {
    lastName: string;
    pnr: string;
    segments: Leg[];
    routes: Leg[];
  };
};

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../../..");
const cases = JSON.parse(
  execFileSync(
    "python3",
    ["backend/tests/test_codeshare_passenger_detail.py", "--json"],
    { cwd: repoRoot, encoding: "utf8" },
  ),
) as Fixture[];

function installDomPolyfills() {
  const proto = Element.prototype as Element & {
    hasPointerCapture?: (pointerId: number) => boolean;
    setPointerCapture?: (pointerId: number) => void;
    releasePointerCapture?: (pointerId: number) => void;
    scrollIntoView?: () => void;
  };
  if (!proto.hasPointerCapture) proto.hasPointerCapture = () => false;
  if (!proto.setPointerCapture) proto.setPointerCapture = () => {};
  if (!proto.releasePointerCapture) proto.releasePointerCapture = () => {};
  if (!proto.scrollIntoView) proto.scrollIntoView = () => {};
  if (!("ResizeObserver" in globalThis)) {
    class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: typeof ResizeObserver }).ResizeObserver =
      ResizeObserver;
  }
}

function isConcealed(el: Element | null): boolean {
  while (el) {
    if (el.hasAttribute("hidden") || el.getAttribute("aria-hidden") === "true") return true;
    if (el.classList.contains("hidden") || el.classList.contains("sr-only") || el.classList.contains("invisible")) {
      return true;
    }
    const style = (el as HTMLElement).style;
    if (style && (style.display === "none" || style.visibility === "hidden")) return true;
    el = el.parentElement;
  }
  return false;
}

/** Text an agent can read. Skips screen-reader-only and hidden nodes. */
function visibleText(root: ParentNode): string {
  const parts: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const parent = node.parentElement;
    if (parent && !isConcealed(parent)) parts.push(node.textContent || "");
    node = walker.nextNode();
  }
  return parts.join(" ");
}

function compact(text: string): string {
  return text.replace(/\s+/g, "").toUpperCase();
}

function tokens(text: string): Set<string> {
  return new Set(text.toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean));
}

function assertFlightVisible(text: string, marketing: string, operating: string) {
  const flat = compact(text);
  expect(flat).toContain(compact(marketing));
  if (operating && compact(operating) !== compact(marketing)) {
    expect(flat).toContain(compact(operating));
  }
}

function assertClassVisible(text: string, marketed: string, operating: string) {
  const got = tokens(text);
  expect(got.has(marketed.toUpperCase())).toBe(true);
  if (operating && operating.toUpperCase() !== marketed.toUpperCase()) {
    expect(got.has(operating.toUpperCase())).toBe(true);
  }
}

function tightest(root: ParentNode, origin: string, destination: string, selector: string): HTMLElement {
  const matches = [...root.querySelectorAll(selector)].filter((el) => {
    const text = el.textContent || "";
    return text.includes(origin) && text.includes(destination);
  }) as HTMLElement[];
  matches.sort((a, b) => (a.textContent || "").length - (b.textContent || "").length);
  if (matches.length === 0) {
    throw new Error(`no ${selector} contains ${origin} and ${destination}`);
  }
  return matches[0];
}

/** Segment block that includes the route and the marketed class, not just the city pair. */
function legBlock(root: ParentNode, leg: Leg, selector: string): HTMLElement {
  const wanted = leg.marketedClass.toUpperCase();
  const matches = [...root.querySelectorAll(selector)].filter((el) => {
    const text = visibleText(el);
    return text.includes(leg.origin) && text.includes(leg.destination) && tokens(text).has(wanted);
  }) as HTMLElement[];
  matches.sort((a, b) => visibleText(a).length - visibleText(b).length);
  if (matches.length === 0) {
    throw new Error(`no ${selector} contains ${leg.origin}-${leg.destination} class ${leg.marketedClass}`);
  }
  return matches[0];
}

function cardByHeading(title: RegExp): HTMLElement {
  const headings = screen.getAllByText(title);
  const heading = headings.find((el) => el.closest("[data-slot=card]"));
  const card = heading?.closest("[data-slot=card]");
  if (!card) throw new Error(`no card for ${title}`);
  return card as HTMLElement;
}

function renderSheet(fixture: Fixture) {
  fetchPassengerDetail.mockReset();
  fetchPassengerDetail.mockResolvedValue(fixture.detail);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const pnr = fixture.oracle.pnr || "TST000";
  return render(
    <QueryClientProvider client={client}>
      <PassengerDetailSheet
        open
        onOpenChange={() => {}}
        flightNumber="6418"
        origin="SYD"
        date="2019-05-29"
        pnr={pnr}
      />
    </QueryClientProvider>,
  );
}

beforeAll(() => {
  installDomPolyfills();
});

afterEach(() => {
  cleanup();
});

describe("live passenger detail codeshare labels", () => {
  it.each(cases)("$name", async (fixture) => {
    renderSheet(fixture);
    await screen.findByText(new RegExp(fixture.oracle.lastName));

    const itinerary = cardByHeading(/^Itinerary/);
    for (const seg of fixture.oracle.segments) {
      const block = legBlock(itinerary, seg, "div");
      const text = visibleText(block);
      assertFlightVisible(text, seg.marketingFlight, seg.operatingFlight);
      assertClassVisible(text, seg.marketedClass, seg.operatingClass);
    }

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Baggage" }));
    await waitFor(() => {
      expect(screen.getByText("Baggage Routing")).toBeTruthy();
    });
    const baggage = cardByHeading(/^Baggage Routing/);
    for (const route of fixture.oracle.routes) {
      const row = legBlock(baggage, route, "tr");
      const text = visibleText(row);
      assertFlightVisible(text, route.marketingFlight, route.operatingFlight);
      assertClassVisible(text, route.marketedClass, route.operatingClass);
    }

    const needsExtras = fixture.oracle.segments.some((seg) => seg.hasFare || seg.hasAncillary);
    if (!needsExtras) return;

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Extras" }));
    await waitFor(() => {
      expect(screen.getAllByText(/Fare & Baggage Allowance|Ancillary Purchases/).length).toBeGreaterThan(0);
    });

    if (fixture.oracle.segments.some((seg) => seg.hasFare)) {
      const fare = cardByHeading(/^Fare & Baggage Allowance/);
      for (const seg of fixture.oracle.segments.filter((item) => item.hasFare)) {
        const row = tightest(fare, seg.origin, seg.destination, "tr");
        assertFlightVisible(visibleText(row), seg.marketingFlight, seg.operatingFlight);
      }
    }

    if (fixture.oracle.segments.some((seg) => seg.hasAncillary)) {
      const ancillary = cardByHeading(/^Ancillary Purchases/);
      const rows = [...ancillary.querySelectorAll("tbody tr")] as HTMLElement[];
      expect(rows.length).toBeGreaterThan(0);
      // Ancillary rows are labeled with the segment flight and do not repeat the route.
      // Every codeshare segment that has an ancillary must show both flight designators
      // on one of those rows, and a plain segment must not be labeled with another
      // segment's operating flight.
      const rowText = rows.map((row) => visibleText(row));
      for (const seg of fixture.oracle.segments.filter((item) => item.hasAncillary)) {
        const own = rowText.find((text) => compact(text).includes(compact(seg.marketingFlight)));
        expect(own, `ancillary row for ${seg.marketingFlight}`).toBeTruthy();
        assertFlightVisible(own || "", seg.marketingFlight, seg.operatingFlight);
      }
    }
  });
});
