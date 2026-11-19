import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BoardingProgress } from "@/components/dashboard/boarding-progress";

/**
 * Renders the boarding card the flight workbench mounts on the overview tab
 * (`flight-workbench.tsx` overview grid) and the activity tab. The payload is
 * the JSON produced by `get_boarding_progress` for changes from `detect_changes`.
 * `FlightWorkbench` itself is not mounted: both tabs pass the same flight
 * number, origin, and date into this component, and the wrong totals are
 * computed here.
 */

interface ExpectedCard {
  flightNumber: string;
  origin: string;
  date: string;
  total: number;
  checkedIn: number;
  boarded: number;
  remaining: number;
  milestoneStatus: string;
}

const payload = JSON.parse(readFileSync(process.env.BOARDING_PROGRESS_PAYLOAD!, "utf8"));
const expected: ExpectedCard = JSON.parse(readFileSync(process.env.BOARDING_PROGRESS_EXPECT!, "utf8"));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function quickStat(label: string): string | null {
  const statLabel = screen.getAllByText(label).find((node) => node.classList.contains("uppercase"));
  const text = statLabel?.parentElement?.textContent ?? "";
  const match = text.match(/\d+/);
  return match ? match[0] : null;
}

describe("BoardingProgress activity and overview card", () => {
  it("shows the check-in and boarding totals from the boarding-progress payload", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const expectedPath = `/flights/${expected.flightNumber}/boarding-progress`;
      if (!url.includes(expectedPath)) {
        return new Response(`unexpected ${url}`, { status: 404 });
      }
      const parsed = new URL(url);
      if (parsed.searchParams.get("origin") !== expected.origin || parsed.searchParams.get("date") !== expected.date) {
        return new Response(`unexpected query ${parsed.search}`, { status: 404 });
      }
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    if (typeof globalThis.ResizeObserver === "undefined") {
      globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
    }

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <BoardingProgress
          flightNumber={expected.flightNumber}
          origin={expected.origin}
          date={expected.date}
        />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText(`Check-in and boarding rates for ${expected.flightNumber}`)).toBeTruthy();
    });

    const checkinPct = Math.round((expected.checkedIn / expected.total) * 100);
    const boardingPct = Math.round((expected.boarded / expected.total) * 100);
    const problems: string[] = [];
    const record = (name: string, actual: string | null | undefined, wanted: string) => {
      if (actual !== wanted) problems.push(`${name}: rendered ${actual ?? "nothing"}, expected ${wanted}`);
    };

    record("total", quickStat("Total"), String(expected.total));
    record("checked in", quickStat("Checked In"), String(expected.checkedIn));
    record("boarded", quickStat("Boarded"), String(expected.boarded));
    record("remaining", quickStat("Remaining"), String(expected.remaining));

    const body = document.body.textContent ?? "";
    if (body.includes("No progress data available")) {
      problems.push("progress chart says no progress data is available");
    }
    if (!new RegExp(`${expected.checkedIn}\\s*/\\s*${expected.total}`).test(body)) {
      problems.push(`chart is missing checked-in ratio ${expected.checkedIn} / ${expected.total}`);
    }
    if (!new RegExp(`${expected.boarded}\\s*/\\s*${expected.total}`).test(body)) {
      problems.push(`chart is missing boarded ratio ${expected.boarded} / ${expected.total}`);
    }
    if (!body.includes(`${checkinPct}% of total passengers`)) {
      problems.push(`chart is missing ${checkinPct}% checked-in progress`);
    }
    if (!body.includes(`${boardingPct}% of total passengers`)) {
      problems.push(`chart is missing ${boardingPct}% boarded progress`);
    }
    if (!body.includes(expected.milestoneStatus)) {
      problems.push(`milestone list does not show status ${expected.milestoneStatus}`);
    }
    if (fetchMock.mock.calls.length === 0) {
      problems.push("card did not request boarding-progress");
    }

    view.unmount();
    queryClient.clear();

    if (problems.length > 0) {
      throw new Error(`${problems.join("\n")}\n\nRendered text:\n${body}`);
    }
  });
});
