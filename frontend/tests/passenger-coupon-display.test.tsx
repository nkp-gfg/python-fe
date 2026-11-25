/**
 * The live passenger sheet and the flight-board ticket cell must show the
 * coupon stored with the ticket. Fixtures are the JSON the API tests produced
 * from Sabre XML, passed in COUPON_FIXTURE.
 */
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerDetailSheet } from "@/components/dashboard/passenger-detail-sheet";
import { BottomDetailPanel } from "@/components/dashboard/bottom-detail-panel";

interface DetailCase {
  name: string;
  flightNumber: string;
  origin: string;
  date: string;
  pnr: string;
  response: { passengers: { vcrNumber?: string; couponNumber?: string; pnr?: string }[] };
  ticket: string;
  coupon: string | null;
  hiddenCoupons: string[];
}

interface ListCase {
  name: string;
  flightNumber: string;
  origin: string;
  date: string;
  response: { passengers: { ticketNumber?: string; vcrCouponNumber?: string; pnr?: string }[] };
  rows: { ticket: string; coupon: string | null }[];
}

interface Fixtures {
  detailCases: DetailCase[];
  listCases: ListCase[];
}

const fixturePath = process.env.COUPON_FIXTURE;
if (!fixturePath) {
  throw new Error("COUPON_FIXTURE is required");
}
const fixtures = JSON.parse(readFileSync(fixturePath, "utf8")) as Fixtures;

function couponLabel(coupon: string): RegExp {
  return new RegExp(`(?:coupon|cpn)(?:\\s+\\w+){0,3}\\s*[:#]?\\s*${coupon}\\b`, "i");
}

function renderSheet(detail: DetailCase) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PassengerDetailSheet
        open
        onOpenChange={() => {}}
        flightNumber={detail.flightNumber}
        origin={detail.origin}
        date={detail.date}
        pnr={detail.pnr}
      />
    </QueryClientProvider>,
  );
}

function headerAroundTicket(ticket: string): HTMLElement {
  const ticketNode = screen.getByText(new RegExp(ticket));
  let el: HTMLElement | null = ticketNode as HTMLElement;
  while (el) {
    if (el.querySelector("h3")) return el;
    el = el.parentElement;
  }
  throw new Error(`ticket ${ticket} is not inside the passenger header`);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("live passenger detail header", () => {
  for (const detail of fixtures.detailCases) {
    it(detail.name, async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL) => {
          const url = String(input);
          if (url.includes(`/passengers/${detail.pnr}/detail`)) {
            return new Response(JSON.stringify(detail.response), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("/timeline")) {
            return new Response(JSON.stringify({ events: [] }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          return new Response("not found", { status: 404 });
        }),
      );

      renderSheet(detail);

      await waitFor(() => {
        expect(document.body.textContent ?? "").toContain(detail.ticket);
      });

      const header = headerAroundTicket(detail.ticket);
      const text = header.textContent ?? "";
      expect(text).toContain(detail.ticket);
      if (detail.coupon) {
        expect(text).toMatch(couponLabel(detail.coupon));
      } else {
        expect(text).not.toMatch(/coupon|cpn/i);
      }
      for (const hidden of detail.hiddenCoupons) {
        expect(text).not.toMatch(couponLabel(hidden));
      }
    });
  }
});

describe("flight board ticket cell", () => {
  for (const listCase of fixtures.listCases) {
    it(listCase.name, async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL) => {
          const url = String(input);
          if (url.includes(`/flights/${listCase.flightNumber}/passengers`)) {
            return new Response(JSON.stringify(listCase.response), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          return new Response("not found", { status: 404 });
        }),
      );

      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      render(
        <QueryClientProvider client={client}>
          <BottomDetailPanel
            view="records"
            flightNumber={listCase.flightNumber}
            origin={listCase.origin}
            date={listCase.date}
            dashboard={{} as never}
            onClose={() => {}}
          />
        </QueryClientProvider>,
      );

      await waitFor(() => {
        expect(document.body.textContent ?? "").toContain(listCase.rows[0].ticket);
      });

      for (const row of listCase.rows) {
        const ticketNode = screen.getByText(new RegExp(row.ticket));
        const cell = ticketNode.closest("td");
        expect(cell).not.toBeNull();
        const text = cell?.textContent ?? "";
        expect(text).toContain(row.ticket);
        if (row.coupon) {
          expect(text).toMatch(couponLabel(row.coupon));
        } else {
          expect(text).not.toMatch(/coupon|cpn/i);
        }
      }
    });
  }
});
