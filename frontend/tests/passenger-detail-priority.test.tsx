import { readFileSync } from "node:fs";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerDetailSheet } from "@/components/dashboard/passenger-detail-sheet";
import type { ItinerarySegment, PassengerDetailResponse } from "@/lib/types";

const fixturePath = process.env.PASSENGER_DETAIL_FIXTURE;
if (!fixturePath) {
  throw new Error(
    "PASSENGER_DETAIL_FIXTURE must be the passenger-detail JSON produced by the API test",
  );
}

const detail = JSON.parse(readFileSync(fixturePath, "utf8")) as PassengerDetailResponse;

function installDomShims() {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  const element = HTMLElement.prototype as HTMLElement & {
    hasPointerCapture?: (pointerId: number) => boolean;
    setPointerCapture?: (pointerId: number) => void;
    releasePointerCapture?: (pointerId: number) => void;
    scrollIntoView?: () => void;
  };
  element.hasPointerCapture ??= () => false;
  element.setPointerCapture ??= () => {};
  element.releasePointerCapture ??= () => {};
  element.scrollIntoView ??= () => {};
}

function segmentCard(seat: string): HTMLElement {
  const seatNode = screen.getByText(seat);
  let node: HTMLElement | null = seatNode.parentElement;
  while (node) {
    if ((node.textContent ?? "").includes("Class:")) return node;
    node = node.parentElement;
  }
  throw new Error(`No itinerary segment card contains seat ${seat}`);
}

function cardText(card: HTMLElement): string {
  return (card.textContent ?? "").replace(/\s+/g, " ").trim();
}

function priorityListingPattern(code: string): RegExp {
  const escaped = code.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    `(priority listing(?: code)?\\s*:?\\s*${escaped}(?![A-Za-z0-9])|${escaped}(?![A-Za-z0-9])\\s+priority listing)`,
    "i",
  );
}

beforeEach(() => {
  installDomShims();
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/passengers/") && url.includes("/detail")) {
      return new Response(JSON.stringify(detail), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ detail: "not found" }), { status: 404 });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("itinerary cards show each segment priority listing code from the detail response", async () => {
  const segments = detail.passengers[0]?.itinerary ?? [];
  const bySeat = new Map<string, ItinerarySegment>(segments.map((segment) => [segment.seat, segment]));
  const listed = bySeat.get("17D");
  const second = bySeat.get("4A");
  const omitted = bySeat.get("9Z");
  expect(listed?.priority, "detail response dropped the vendor priority code").toBe("EOS");
  expect(listed?.passengerType).toBe("F");
  expect(second?.priority).toBe("PL9");
  expect(second?.passengerType).toBe("C");
  expect(omitted?.priority ?? "").toBe("");

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PassengerDetailSheet
        open
        onOpenChange={() => {}}
        flightNumber="1"
        origin="AUH"
        date="2018-05-05"
        pnr="VWLGVM"
      />
    </QueryClientProvider>,
  );

  await waitFor(() => {
    expect(screen.getByText("17D")).toBeTruthy();
    expect(screen.getByText("4A")).toBeTruthy();
    expect(screen.getByText("9Z")).toBeTruthy();
  });

  for (const segment of [listed!, second!]) {
    const card = segmentCard(segment.seat);
    const text = cardText(card);
    expect(text, text).toContain(`Class: ${segment.bookingClass}`);
    expect(text, text).toContain(segment.cabin);
    expect(text, text).toMatch(priorityListingPattern(segment.priority));
    if (segment.passengerType && segment.passengerType !== segment.priority) {
      expect(text, text).not.toMatch(priorityListingPattern(segment.passengerType));
    }
  }

  const blank = segmentCard("9Z");
  const blankText = cardText(blank);
  expect(blankText).toContain("Class:");
  expect(blankText).not.toMatch(priorityListingPattern("EOS"));
  expect(blankText).not.toMatch(priorityListingPattern("PL9"));
  expect(blankText).not.toMatch(/priority listing(?: code)?\s*:?\s*[A-Za-z0-9]/i);
});
