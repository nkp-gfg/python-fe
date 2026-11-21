/**
 * Spec for the operating-day shortcut bug.
 *
 * These tests fail while Today / Yesterday / Tomorrow are taken from the
 * browser's local calendar and compared with stored Sabre departure dates.
 * They pass once those shortcuts, the analytics date window, the Today page,
 * and the ingest form's default departure date use the operations calendar
 * and the shortcut chip shows that calendar date.
 *
 * Frozen instant: 2026-11-21T07:30:00.000Z
 *   Pacific/Honolulu (this process): 2026-11-20
 *   UTC, the clock SabreClient uses for a default departure date: 2026-11-21
 *   Asia/Bahrain (UTC+3): 2026-11-21
 *
 * Run:
 *   cd frontend
 *   npm ci
 *   npm install --no-save vitest@3.2.4 jsdom@26.1.0 @testing-library/react@16.3.0 @testing-library/dom@10.4.1
 *   TZ=Pacific/Honolulu npx vitest run test/operating-day.test.tsx
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { FlightListItem } from "@/lib/types";
import { ToastProvider } from "@/components/ui/toast";

const FROZEN_INSTANT = "2026-11-21T07:30:00.000Z";
const BROWSER_LOCAL_DATE = "2026-11-20";
const OPERATIONS_TODAY = "2026-11-21";
const OPERATIONS_YESTERDAY = "2026-11-20";
const OPERATIONS_TOMORROW = "2026-11-22";

const fetchFlights = vi.fn();
const fetchNetworkAnalytics = vi.fn();
const fetchDashboard = vi.fn();
const fetchFlightTree = vi.fn();
const fetchOtpFlights = vi.fn();
const ingestFlight = vi.fn();

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("@/lib/api", () => ({
  fetchFlights: (...args: unknown[]) => fetchFlights(...args),
  fetchNetworkAnalytics: (...args: unknown[]) => fetchNetworkAnalytics(...args),
  fetchDashboard: (...args: unknown[]) => fetchDashboard(...args),
  fetchFlightTree: (...args: unknown[]) => fetchFlightTree(...args),
  fetchOtpFlights: (...args: unknown[]) => fetchOtpFlights(...args),
  ingestFlight: (...args: unknown[]) => ingestFlight(...args),
  fetchJobStatus: vi.fn(),
  getIngestFailureMessage: vi.fn(() => "failed"),
  ingestBatch: vi.fn(),
}));

function assertBrowserIsBehindOperationsDay() {
  const local = new Date(FROZEN_INSTANT);
  const localDate = [
    local.getFullYear(),
    String(local.getMonth() + 1).padStart(2, "0"),
    String(local.getDate()).padStart(2, "0"),
  ].join("-");
  if (localDate !== BROWSER_LOCAL_DATE || local.toISOString().slice(0, 10) !== OPERATIONS_TODAY) {
    throw new Error(
      `Run with TZ=Pacific/Honolulu. Local calendar date was ${localDate}; ` +
      `UTC date was ${local.toISOString().slice(0, 10)}.`,
    );
  }
}

function flight(flightNumber: string, departureDate: string): FlightListItem {
  return {
    airline: "GF",
    flightNumber,
    origin: "BAH",
    destination: "LHR",
    departureDate,
    status: "SCHEDULED",
    gate: "A1",
    aircraft: { type: "789", registration: "A9C-XX" },
    schedule: {
      scheduledDeparture: `${departureDate}T08:00:00`,
      estimatedDeparture: `${departureDate}T08:00:00`,
      scheduledArrival: `${departureDate}T14:00:00`,
      estimatedArrival: `${departureDate}T14:00:00`,
      durationMinutes: 360,
    },
    passengerCounts: {},
    jumpSeat: { cockpit: 0, cabin: 0, cockpitInUse: false, cabinInUse: false },
    fetchedAt: FROZEN_INSTANT,
  };
}

const storedFlights = [
  flight("100", OPERATIONS_YESTERDAY),
  flight("101", OPERATIONS_TODAY),
  flight("102", OPERATIONS_TOMORROW),
];

function renderPage(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  );
}

function quickFilters(label: "Today" | "Yesterday" | "Tomorrow") {
  return screen.getAllByRole("button").filter((button) =>
    (button.getAttribute("aria-label") ?? "").startsWith(`${label} quick filter`),
  );
}

function expectChipShowsDate(label: "Today" | "Yesterday" | "Tomorrow", isoDate: string) {
  const chips = quickFilters(label);
  expect(chips.length, `${label} chip`).toBeGreaterThan(0);
  for (const chip of chips) {
    const visible = `${chip.textContent ?? ""} ${chip.getAttribute("aria-label") ?? ""}`;
    expect(visible).toContain(label);
    expect(visible).toContain(isoDate);
  }
}

describe("operating-day shortcuts", () => {
  beforeEach(() => {
    assertBrowserIsBehindOperationsDay();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(FROZEN_INSTANT));
    fetchFlights.mockReset();
    fetchNetworkAnalytics.mockReset();
    fetchDashboard.mockReset();
    fetchFlightTree.mockReset();
    fetchOtpFlights.mockReset();
    ingestFlight.mockReset();
    fetchFlights.mockResolvedValue(storedFlights);
    fetchNetworkAnalytics.mockResolvedValue(null);
    fetchDashboard.mockResolvedValue(null);
    fetchFlightTree.mockResolvedValue(null);
    fetchOtpFlights.mockResolvedValue([]);
    ingestFlight.mockResolvedValue({
      message: "ok",
      processedFlights: 1,
      result: {
        success: true,
        flight: { airline: "GF", flightNumber: "7", origin: "BAH", departureDate: OPERATIONS_TODAY },
        apis: {},
      },
    });
  });

  it("keeps the operations-day flight in network analytics when Today is clicked", async () => {
    const { default: AnalyticsPage } = await import("@/app/analytics/page");
    renderPage(<AnalyticsPage />);

    expect(await screen.findByRole("button", { name: /GF100/ })).toBeTruthy();

    fireEvent.click(quickFilters("Today")[0]);

    expect(
      screen.queryByRole("button", { name: /GF101/ }),
      "Today must keep GF101, whose stored departure date is the operations day",
    ).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: /GF100/ }),
      "Today must drop GF100, whose stored departure date is only the browser-local day",
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /GF102/ })).toBeNull();
    expectChipShowsDate("Today", OPERATIONS_TODAY);
    expectChipShowsDate("Yesterday", OPERATIONS_YESTERDAY);

    fireEvent.click(screen.getByRole("button", { name: /GF101/ }));

    await waitFor(() => {
      expect(fetchNetworkAnalytics).toHaveBeenCalled();
    });
    for (const call of fetchNetworkAnalytics.mock.calls) {
      expect(call[1]).toBe(OPERATIONS_TODAY);
      expect(call[0] <= OPERATIONS_TODAY).toBe(true);
    }
    const withFlight = fetchNetworkAnalytics.mock.calls.find((call) => call[2]?.flightNumber === "101");
    expect(withFlight?.[1]).toBe(OPERATIONS_TODAY);
  });

  it("filters the flight workbench sidebar with the operations calendar", async () => {
    const { FlightWorkbench } = await import("@/components/dashboard/flight-workbench");
    renderPage(<FlightWorkbench />);

    expect(await screen.findByRole("button", { name: /GF101/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /GF100/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /GF102/ })).toBeTruthy();

    fireEvent.click(quickFilters("Today")[0]);
    expect(
      screen.queryAllByRole("button", { name: /GF101/ }).length,
      "Today must keep GF101, whose stored departure date is the operations day",
    ).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: /GF100/ }),
      "Today must drop GF100, whose stored departure date is only the browser-local day",
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /GF102/ })).toBeNull();
    expectChipShowsDate("Today", OPERATIONS_TODAY);

    fireEvent.click(quickFilters("Tomorrow")[0]);
    expect(screen.getAllByRole("button", { name: /GF102/ }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /GF101/ })).toBeNull();
    expectChipShowsDate("Tomorrow", OPERATIONS_TOMORROW);

    fireEvent.click(quickFilters("Yesterday")[0]);
    expect(screen.getAllByRole("button", { name: /GF100/ }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /GF101/ })).toBeNull();
    expectChipShowsDate("Yesterday", OPERATIONS_YESTERDAY);
  });

  it("loads the Today page for the operations departure date", async () => {
    const { default: TodayPage } = await import("@/app/today/page");
    renderPage(<TodayPage />);

    expect(await screen.findByText(OPERATIONS_TODAY)).toBeTruthy();
    expect(screen.queryByText(BROWSER_LOCAL_DATE)).toBeNull();
    await waitFor(() => {
      expect(fetchOtpFlights).toHaveBeenCalledWith(OPERATIONS_TODAY);
    });

    fireEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(screen.getByText(OPERATIONS_TOMORROW)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Reset to today" }));
    expect(screen.getByText(OPERATIONS_TODAY)).toBeTruthy();
    await waitFor(() => {
      const dates = fetchOtpFlights.mock.calls.map((call) => call[0]);
      expect(dates.at(-1)).toBe(OPERATIONS_TODAY);
    });
  });

  it("submits the ingest form's untouched departure date as the operations day", async () => {
    const { IngestionPanel } = await import("@/components/dashboard/ingestion-panel");
    renderPage(<IngestionPanel />);

    const dateInput = screen.getByPlaceholderText("DD/MM/YYYY") as HTMLInputElement;
    expect(dateInput.value).toBe("21/11/2026");

    fireEvent.change(screen.getByPlaceholderText("e.g. 2006"), { target: { value: "7" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. LHR"), { target: { value: "BAH" } });
    fireEvent.click(screen.getByRole("button", { name: "Start Ingest" }));

    await waitFor(() => {
      expect(ingestFlight).toHaveBeenCalledTimes(1);
    });
    expect(ingestFlight.mock.calls[0][0].departureDate).toBe(OPERATIONS_TODAY);
    expect(ingestFlight.mock.calls[0][0].departureDateTime.startsWith(`${OPERATIONS_TODAY}T`)).toBe(true);
  });
});
