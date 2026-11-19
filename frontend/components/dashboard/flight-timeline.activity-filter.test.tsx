/**
 * Activity tab filter must stay usable when the timeline API returns the
 * categories produced for operational flight changes.
 *
 * The payload is built by the backend test from convert_flight_status →
 * detect_changes → get_flight_timeline. This file only renders that payload
 * through FlightTimeline, inside the same Activity error boundary the
 * workbench uses.
 */
import { readFileSync } from "node:fs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "@/components/error-boundary";
import { FlightTimeline } from "@/components/dashboard/flight-timeline";
import type { FlightTimelineResponse } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  fetchFlightTimeline: vi.fn(),
}));

import { fetchFlightTimeline } from "@/lib/api";

const BOARDING_PROGRESS = "Boarding progress";

function loadPayload(): FlightTimelineResponse {
  const path = process.env.TIMELINE_FIXTURE;
  if (!path) {
    throw new Error("TIMELINE_FIXTURE is required");
  }
  return JSON.parse(readFileSync(path, "utf8")) as FlightTimelineResponse;
}

beforeAll(() => {
  // Radix menu items call these while opening. jsdom does not implement them.
  const element = HTMLElement.prototype;
  if (!element.hasPointerCapture) {
    element.hasPointerCapture = () => false;
  }
  if (!element.setPointerCapture) {
    element.setPointerCapture = () => {};
  }
  if (!element.releasePointerCapture) {
    element.releasePointerCapture = () => {};
  }
  if (!element.scrollIntoView) {
    element.scrollIntoView = () => {};
  }
  if (!("ResizeObserver" in globalThis)) {
    class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = ResizeObserver;
  }
});

function renderActivityTab(payload: FlightTimelineResponse) {
  vi.mocked(fetchFlightTimeline).mockResolvedValue(payload);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ErrorBoundary compact label="Activity">
        <div>
          <div>{BOARDING_PROGRESS}</div>
          <FlightTimeline
            flightNumber={payload.flightNumber}
            origin={payload.origin ?? ""}
            date={payload.departureDate ?? ""}
          />
        </div>
      </ErrorBoundary>
    </QueryClientProvider>,
  );
}

describe("Activity filter with operational flight changes", () => {
  it("opens a labeled row for every timeline category without replacing the tab", async () => {
    const payload = loadPayload();
    const user = userEvent.setup();
    renderActivityTab(payload);

    const descriptions = [...new Set(payload.events.map((event) => event.description))];
    expect(descriptions.length).toBeGreaterThan(0);

    // The filter builds its rows while FlightTimeline renders, so a missing
    // category config takes down the tab before anyone can use it.
    await waitFor(() => {
      expect(screen.queryByText("Loading timeline...")).toBeNull();
    });
    expect(
      screen.queryByText("Activity failed"),
      "Activity error boundary replaced the tab",
    ).toBeNull();
    for (const description of descriptions) {
      expect(screen.getAllByText(description).length).toBeGreaterThan(0);
    }
    expect(screen.getByText(BOARDING_PROGRESS)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /filter/i }));

    const categories = [...new Set(payload.events.map((event) => event.category))];
    const menu = await screen.findByRole("menu");
    const rows = within(menu).getAllByRole("menuitemcheckbox");
    expect(rows).toHaveLength(categories.length);
    for (const row of rows) {
      expect(row.textContent ?? "").toMatch(/[A-Za-z0-9]/);
      expect(row.textContent ?? "").not.toMatch(/undefined/);
    }

    await waitFor(() => {
      expect(screen.queryByText("Activity failed")).toBeNull();
    });
    for (const description of descriptions) {
      expect(screen.getAllByText(description).length).toBeGreaterThan(0);
    }
    expect(screen.getByText(BOARDING_PROGRESS)).toBeTruthy();
    expect(fetchFlightTimeline).toHaveBeenCalledWith(
      payload.flightNumber,
      payload.origin,
      payload.departureDate,
    );
  });
});
