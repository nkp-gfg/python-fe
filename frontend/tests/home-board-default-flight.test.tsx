/**
 * The operations home page loads GET /flights and paints the rail with the
 * newest departure first. The board may open that same departure, or no
 * departure. It must not open a different one.
 *
 * A flight opened from its own URL stays that departure.
 */
import { readFileSync } from "node:fs";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Providers } from "@/components/providers";
import type { FlightListItem } from "@/lib/types";

const flightsFile = process.env.HOME_BOARD_FLIGHTS_FILE;
if (!flightsFile) {
  throw new Error("HOME_BOARD_FLIGHTS_FILE is required");
}
const storedFlights = JSON.parse(readFileSync(flightsFile, "utf8")) as FlightListItem[];

const calls = {
  dashboard: [] as { flightNumber: string; origin?: string; date?: string }[],
  tree: [] as { flightNumber: string; origin?: string; date?: string }[],
  ingest: [] as { flightNumber: string; origin: string; departureDate: string; airline?: string }[],
};

const navigation = vi.hoisted(() => ({
  pathname: "/",
  params: {} as Record<string, string>,
  search: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: () => {},
    push: () => {},
  }),
  usePathname: () => navigation.pathname,
  useParams: () => navigation.params,
  useSearchParams: () => navigation.search,
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("@/lib/api", () => ({
  fetchFlights: () => Promise.resolve(storedFlights),
  fetchDashboard: (flightNumber: string, origin?: string, date?: string) => {
    calls.dashboard.push({ flightNumber, origin, date });
    const flight = storedFlights.find(
      (item) =>
        item.flightNumber === flightNumber &&
        item.origin === origin &&
        item.departureDate === date,
    );
    return Promise.resolve({
      flightStatus: { gate: flight?.gate ?? "", status: flight?.status ?? "" },
      flightPhase: flight?.flightPhase,
      passengerSummary: {
        cabinSummary: [],
        totalPassengers: flight?.passengerSummary?.totalPassengers ?? 0,
        adultCount: flight?.passengerSummary?.adultCount ?? 0,
        childCount: flight?.passengerSummary?.childCount ?? 0,
        infantCount: flight?.passengerSummary?.infantCount ?? 0,
      },
      overview: {
        soulsOnBoard: flight?.passengerSummary?.totalPassengers ?? 0,
        totalSouls: flight?.passengerSummary?.totalSouls ?? 0,
        manifestRecords: flight?.passengerSummary?.totalPassengers ?? 0,
        trackedChanges: 0,
      },
      route: { origin: origin ?? "", destination: flight?.destination ?? "" },
    });
  },
  fetchFlightTree: (flightNumber: string, origin?: string, date?: string) => {
    calls.tree.push({ flightNumber, origin, date });
    return Promise.resolve({ flightNumber, origin, date, passengers: [] });
  },
  ingestFlight: (payload: {
    flightNumber: string;
    origin: string;
    departureDate: string;
    airline?: string;
  }) => {
    calls.ingest.push(payload);
    return new Promise(() => {});
  },
}));

function railHead(flights: FlightListItem[]): FlightListItem {
  return [...flights].sort((a, b) => {
    const dateCmp = b.departureDate.localeCompare(a.departureDate);
    if (dateCmp !== 0) return dateCmp;
    return a.flightNumber.localeCompare(b.flightNumber);
  })[0];
}

function oldestDeparture(flights: FlightListItem[]): FlightListItem {
  return [...flights].sort((a, b) => {
    const dateCmp = a.departureDate.localeCompare(b.departureDate);
    if (dateCmp !== 0) return dateCmp;
    return a.flightNumber.localeCompare(b.flightNumber);
  })[0];
}

function desktopRail(): HTMLElement {
  const rail = document.querySelector("div.w-\\[280px\\]");
  if (!(rail instanceof HTMLElement)) {
    throw new Error("desktop flight rail was not rendered");
  }
  return rail;
}

function flightButtons(rail: HTMLElement): HTMLButtonElement[] {
  return [...rail.querySelectorAll("button")].filter(
    (button): button is HTMLButtonElement => /GF\d+/.test(button.textContent ?? ""),
  );
}

function identity(flight: FlightListItem) {
  return {
    flightNumber: flight.flightNumber,
    origin: flight.origin,
    date: flight.departureDate,
  };
}

beforeEach(() => {
  calls.dashboard.length = 0;
  calls.tree.length = 0;
  calls.ingest.length = 0;
  navigation.pathname = "/";
  navigation.params = {};
  navigation.search = new URLSearchParams();
});

describe("operations home board", () => {
  it("opens the newest rail flight, or no flight", async () => {
    const head = railHead(storedFlights);
    const oldest = oldestDeparture(storedFlights);
    expect(head.flightNumber).not.toBe(oldest.flightNumber);

    const { default: Home } = await import("@/app/page");
    const user = userEvent.setup();
    render(
      <Providers>
        <Home />
      </Providers>,
    );

    await waitFor(() => {
      expect(flightButtons(desktopRail())).toHaveLength(storedFlights.length);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
    });

    const buttons = flightButtons(desktopRail());
    const first = buttons[0];
    expect(first.textContent).toContain(`GF${head.flightNumber}`);
    expect(first.textContent).toContain(head.departureDate);

    const active = buttons.find((button) => button.className.includes("bg-emerald-600"));
    if (!active) {
      expect(calls.dashboard).toEqual([]);
      expect(calls.tree).toEqual([]);
      expect(calls.ingest).toEqual([]);
      expect(screen.getByRole("heading", { name: "No flight selected" })).toBeTruthy();
      return;
    }

    expect(active.textContent, "highlighted rail row is not the newest departure").toContain(
      `GF${head.flightNumber}`,
    );
    expect(active.textContent).toContain(head.departureDate);

    const selectedCards = screen.getAllByText("Currently Selected").map((label) => label.parentElement);
    expect(selectedCards.some((card) => card?.textContent?.includes(`GF${head.flightNumber}`))).toBe(true);
    expect(selectedCards.some((card) => card?.textContent?.includes(head.departureDate))).toBe(true);

    await waitFor(() => {
      expect(calls.dashboard).toEqual([identity(head)]);
      expect(calls.tree).toEqual([identity(head)]);
    });
    await waitFor(() => {
      expect(document.body.textContent).toContain(`Gate ${head.gate}`);
    });
    expect(document.body.textContent).not.toContain(`Gate ${oldest.gate}`);

    const reingest = active.querySelector('[title="Re-ingest from Sabre"]');
    if (!(reingest instanceof HTMLElement)) {
      throw new Error("active flight has no re-ingest control");
    }
    await user.click(reingest);
    await user.click(await screen.findByRole("button", { name: /Start Background Ingest/ }));
    expect(calls.ingest).toEqual([
      expect.objectContaining({
        airline: head.airline,
        flightNumber: head.flightNumber,
        origin: head.origin,
        departureDate: head.departureDate,
      }),
    ]);
  });

  it("keeps the departure named in the flight URL", async () => {
    const head = railHead(storedFlights);
    const oldest = oldestDeparture(storedFlights);
    expect(oldest.departureDate < head.departureDate).toBe(true);

    navigation.pathname = `/flights/${oldest.flightNumber}`;
    navigation.params = { flightNumber: oldest.flightNumber };
    navigation.search = new URLSearchParams({
      origin: oldest.origin,
      date: oldest.departureDate,
    });

    const { default: FlightDashboardPage } = await import("@/app/flights/[flightNumber]/page");
    render(
      <Providers>
        <FlightDashboardPage />
      </Providers>,
    );

    await waitFor(() => {
      expect(calls.dashboard).toEqual([identity(oldest)]);
      expect(calls.tree).toEqual([identity(oldest)]);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
    });

    expect(calls.dashboard).toEqual([identity(oldest)]);
    const buttons = flightButtons(desktopRail());
    expect(buttons[0].textContent).toContain(`GF${head.flightNumber}`);
    const active = buttons.find((button) => button.className.includes("bg-emerald-600"));
    expect(active?.textContent).toContain(`GF${oldest.flightNumber}`);
    expect(active?.textContent).toContain(oldest.departureDate);
    await waitFor(() => {
      expect(document.body.textContent).toContain(`Gate ${oldest.gate}`);
    });

    const user = userEvent.setup();
    const reingest = active?.querySelector('[title="Re-ingest from Sabre"]');
    if (!(reingest instanceof HTMLElement)) {
      throw new Error("URL flight has no re-ingest control");
    }
    await user.click(reingest);
    await user.click(await screen.findByRole("button", { name: /Start Background Ingest/ }));
    expect(calls.ingest).toEqual([
      expect.objectContaining({
        airline: oldest.airline,
        flightNumber: oldest.flightNumber,
        origin: oldest.origin,
        departureDate: oldest.departureDate,
      }),
    ]);
  });
});
