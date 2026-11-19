import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { format, subDays } from "date-fns";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundaryHandler } from "next/dist/client/components/error-boundary";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import SegmentError from "../frontend/app/error";
import AnalyticsPage from "../frontend/app/analytics/page";
import { ErrorBoundary } from "../frontend/components/error-boundary";

/**
 * Same construction the App Router uses for useSearchParams:
 * new URL(canonicalUrl, window.location.href).searchParams
 */
function searchParamsFor(href: string): URLSearchParams {
  window.history.replaceState({}, "", href);
  return new URL(href, window.location.href).searchParams;
}

function defaultRange(now = new Date()) {
  const from = subDays(now, 25);
  return {
    fromLabel: format(from, "MMM d"),
    toLabel: format(now, "MMM d"),
    fromIso: format(from, "yyyy-MM-dd"),
    toIso: format(now, "yyyy-MM-dd"),
  };
}

const requested: string[] = [];

function renderAnalytics(href: string) {
  const now = new Date();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const view = render(
    <SearchParamsContext.Provider value={searchParamsFor(href)}>
      <QueryClientProvider client={client}>
        <ErrorBoundary>
          <ErrorBoundaryHandler pathname="/analytics" errorComponent={SegmentError}>
            <AnalyticsPage />
          </ErrorBoundaryHandler>
        </ErrorBoundary>
      </QueryClientProvider>
    </SearchParamsContext.Provider>,
  );
  return { ...view, now, client };
}

function dateRangeLabels(): string[] {
  const label = screen.getByText("Date Range");
  const buttons = label.parentElement?.querySelectorAll("button") ?? [];
  return [...buttons].map((button) => (button.textContent ?? "").replace(/\s+/g, " ").trim());
}

async function expectRange(fromLabel: string, toLabel: string, fromIso: string, toIso: string) {
  expect(screen.queryByText("Invalid time value")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Something went wrong" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Network Analytics" })).toBeTruthy();
  expect(dateRangeLabels()).toEqual([fromLabel, toLabel]);
  await waitFor(() => {
    const analyticsUrls = requested.filter((url) => url.includes("/analytics/network?"));
    expect(analyticsUrls.length).toBeGreaterThan(0);
    for (const url of analyticsUrls) {
      const parsed = new URL(url);
      expect(parsed.searchParams.get("date_from")).toBe(fromIso);
      expect(parsed.searchParams.get("date_to")).toBe(toIso);
    }
  });
}

describe("network analytics date query", () => {
  beforeEach(() => {
    requested.length = 0;
    vi.stubGlobal(
      "fetch",
      (input: RequestInfo | URL) => {
        requested.push(String(input));
        return new Promise(() => {});
      },
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it.each([
    ["non-date text", "/analytics?date=not-a-date"],
    ["impossible calendar day", "/analytics?date=2026-02-31"],
    ["non-leap 29 February", "/analytics?date=2026-02-29"],
    ["month out of range", "/analytics?date=2026-13-01"],
    ["day out of range", "/analytics?date=2026-01-32"],
    ["trailing junk", "/analytics?date=2026-01-15xyz"],
    ["unpadded month and day", "/analytics?date=2026-1-5"],
    ["leading space", "/analytics?date=%202026-01-15"],
    ["iso datetime", "/analytics?date=2026-01-15T00:00:00"],
    ["empty date", "/analytics?date="],
    ["missing date", "/analytics"],
  ])("keeps the default range for %s", async (_name, href) => {
    const { now } = renderAnalytics(href);
    const range = defaultRange(now);
    await expectRange(range.fromLabel, range.toLabel, range.fromIso, range.toIso);
  });

  it("uses a real YYYY-MM-DD query for both ends of the range", async () => {
    renderAnalytics("/analytics?date=2026-01-15");
    await expectRange("Jan 15", "Jan 15", "2026-01-15", "2026-01-15");
  });

  it("accepts a real leap day", async () => {
    renderAnalytics("/analytics?date=2024-02-29");
    await expectRange("Feb 29", "Feb 29", "2024-02-29", "2024-02-29");
  });
});
