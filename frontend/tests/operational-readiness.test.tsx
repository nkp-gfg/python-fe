/**
 * Displayed readiness slices for manifests that contain boarded passengers
 * who are not checked in. The API under NEXT_PUBLIC_API_URL must be the
 * FalconEye server seeded by backend/tests/test_operational_readiness_mix.py.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import AnalyticsPage from "@/app/analytics/page";
import { FlightInsightsPanel } from "@/components/dashboard/flight-insights";
import { PaxMatrix } from "@/components/dashboard/pax-matrix";
import { PhaseAlertBanner } from "@/components/dashboard/phase-alert-banner";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { ToastProvider } from "@/components/ui/toast";

const nav = vi.hoisted(() => ({
  search: new URLSearchParams("date=2026-06-15"),
}));

const captured = vi.hoisted(() => ({
  status: [] as { name: string; value: number }[][],
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => nav.search,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/",
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("@/components/ui/echarts", () => ({
  EChart: ({ option }: { option: { graphic?: { style?: { text?: string } }[]; series?: { data?: { name: string; value: number }[] }[] } }) => {
    const center = option.graphic?.[0]?.style?.text;
    if (center === "Status") {
      captured.status.push(
        (option.series?.[0]?.data ?? []).map((item) => ({ name: item.name, value: item.value })),
      );
    }
    return <div data-testid="echart" data-center={center ?? ""} />;
  },
}));

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  );
}

function slicesOf(data: { name: string; value: number }[]) {
  return Object.fromEntries(data.map((item) => [item.name, item.value]));
}

function latestStatus() {
  const data = captured.status.at(-1);
  if (!data) throw new Error("Operational Readiness chart was not drawn");
  return slicesOf(data);
}

async function openAnalytics(date: string) {
  nav.search = new URLSearchParams(`date=${date}`);
  captured.status = [];
  renderWithQuery(<AnalyticsPage />);
  await waitFor(() => {
    expect(captured.status.length).toBeGreaterThan(0);
  });
  return latestStatus();
}

function expectPartition(
  slices: Record<string, number>,
  boarded: number,
  checkedInNotBoarded: number,
  notCheckedIn: number,
) {
  expect(slices["Boarded"]).toBe(boarded);
  expect(slices["Checked In (not boarded)"]).toBe(checkedInNotBoarded);
  expect(slices["Not Checked In"]).toBe(notCheckedIn);
  for (const value of Object.values(slices)) {
    expect(value).toBeGreaterThanOrEqual(0);
  }
}

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8765";

afterEach(() => {
  cleanup();
  captured.status = [];
});

describe("network analytics operational readiness chart", () => {
  test("partitions a manifest that mixes boarded and checked-in flags", async () => {
    const slices = await openAnalytics("2026-06-15");
    expectPartition(slices, 2, 1, 1);
  });

  test("does not draw a negative checked-in slice when boarded exceeds checked-in", async () => {
    const slices = await openAnalytics("2026-06-16");
    expectPartition(slices, 2, 0, 1);
  });

  test("uses only the latest passenger list for each flight", async () => {
    const slices = await openAnalytics("2026-06-17");
    expectPartition(slices, 1, 0, 1);
  });

  test("sums flights as partitions", async () => {
    const slices = await openAnalytics("2026-06-19");
    expectPartition(slices, 1, 1, 1);
  });

  test("cabin filter counts only that cabin's flags", async () => {
    await openAnalytics("2026-06-18");
    captured.status = [];
    fireEvent.click(screen.getByRole("button", { name: "Economy" }));
    await waitFor(() => {
      const slices = latestStatus();
      const total = slices["Boarded"] + slices["Checked In (not boarded)"] + slices["Not Checked In"];
      expect(total).toBe(1);
    });
    expectPartition(latestStatus(), 1, 0, 0);
  });
});

describe("flight dashboard readiness partition", () => {
  test("banner, boarding funnel, and matrix use the flag partition", async () => {
    const dashboardResponse = await fetch(`${API}/flights/100/dashboard?origin=BAH&date=2026-06-15`);
    expect(dashboardResponse.status).toBe(200);
    const dashboard = await dashboardResponse.json();

    const treeResponse = await fetch(`${API}/flights/100/tree?origin=BAH&date=2026-06-15`);
    expect(treeResponse.status).toBe(200);
    const tree = await treeResponse.json();

    renderWithQuery(
      <>
        <PhaseAlertBanner flightPhase={dashboard.flightPhase} />
        <FlightInsightsPanel insights={dashboard.insights} totalPassengers={4} />
        <PaxMatrix tree={tree} />
      </>,
    );

    expect(screen.getByText("Flight closed — 1 no-show, 1 offload")).toBeInTheDocument();

    const funnel = screen.getByRole("heading", { name: "Boarding Funnel" }).closest(".shadow-sm");
    if (!funnel) throw new Error("Boarding Funnel was not rendered");
    const funnelScope = within(funnel as HTMLElement);
    expect(funnelScope.getByText("Checked in (not boarded)").parentElement?.textContent).toMatch(/^Checked in \(not boarded\)1\b/);
    expect(funnelScope.getByText("Not checked in").parentElement?.textContent).toMatch(/^Not checked in1\b/);
    expect(funnelScope.getByText("Boarded").parentElement?.textContent).toMatch(/^Boarded2\b/);

    const matrixCard = screen.getByText("Not Checked-In").parentElement;
    expect(matrixCard?.textContent).toMatch(/Not Checked-In\s*1/);
  });

  test("workbench status tile subtracts nothing and keeps every slice at the flag count", async () => {
    renderWithQuery(
      <FlightWorkbench initialSelection={{ flightNumber: "100", origin: "BAH", date: "2026-06-15" }} />,
    );

    await waitFor(() => {
      expect(screen.getAllByText("Checked-In").length).toBeGreaterThan(0);
    });

    const statusCard = screen.getAllByText("Status", { selector: "p" })
      .map((node) => node.closest(".rounded-lg"))
      .find((card) => card?.textContent?.includes("Checked-In") && card.textContent.includes("Booked"));
    if (!statusCard) throw new Error("Status tile was not rendered");
    const tile = (label: string) => within(statusCard).getAllByRole("button").find((button) =>
      [...button.querySelectorAll("p")].some((node) => node.textContent?.trim() === label),
    );
    expect(tile("Checked-In")?.textContent).toMatch(/1\s*Checked-In|1Checked-In/);
    expect(tile("Booked")?.textContent).toMatch(/1\s*Booked|1Booked/);
    expect(tile("Boarded")?.textContent).toMatch(/2\s*Boarded|2Boarded/);
  });
});
