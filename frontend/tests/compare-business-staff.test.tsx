/**
 * The comparison column must show business-cabin staff the dashboard already counted.
 *
 * These tests fail while the passenger breakdown prints economy staff and omits
 * the business staff line. They pass once that line is rendered and the revenue
 * gender boxes plus the staff count equal the business heading.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import ComparePage from "@/app/compare/page";
import type { FlightDashboard } from "@/lib/types";

interface StaffCounts {
  total: number;
  male: number;
  female: number;
  children: number;
  infants: number;
}

interface CabinAnalysis {
  total: number;
  passengers: StaffCounts;
  staff: StaffCounts;
}

interface CasePayload {
  flightNumber: string;
  origin: string;
  date: string;
  dashboard: FlightDashboard;
}

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

const harness = vi.hoisted(() => ({
  flightsParam: "",
  dashboards: new Map<string, FlightDashboard>(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(`flights=${harness.flightsParam}`),
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("@/lib/api", () => ({
  fetchFlights: vi.fn(async () => []),
  fetchDashboard: vi.fn(async (flightNumber: string) => {
    const dashboard = harness.dashboards.get(flightNumber);
    if (!dashboard) {
      throw new Error(`no dashboard for ${flightNumber}`);
    }
    return dashboard;
  }),
  fetchFlightTree: vi.fn(async (flightNumber: string) => {
    const dashboard = harness.dashboards.get(flightNumber);
    if (!dashboard?.tree) {
      throw new Error(`no tree for ${flightNumber}`);
    }
    return dashboard.tree;
  }),
}));

function staffLine(staff: StaffCounts): string {
  // Same concatenation the economy block uses in app/compare/page.tsx.
  return `Staff: ${staff.total} (${staff.male > 0 ? `M:${staff.male} ` : ""}${staff.female > 0 ? `F:${staff.female}` : ""})`;
}

function badgeNumbers(text: string) {
  const match = text.match(/(\d+)Male(\d+)Female(\d+)Children(\d+)Infants/);
  if (!match) {
    throw new Error(`gender boxes missing from breakdown: ${text}`);
  }
  return {
    male: Number(match[1]),
    female: Number(match[2]),
    children: Number(match[3]),
    infants: Number(match[4]),
  };
}

function renderComparison(flight: CasePayload) {
  harness.flightsParam = `${flight.flightNumber}:${flight.origin}:${flight.date}`;
  harness.dashboards.set(flight.flightNumber, flight.dashboard);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ComparePage />
    </QueryClientProvider>,
  );
}

async function breakdownBlock(label: string): Promise<HTMLElement> {
  const heading = await screen.findByText(label);
  const block = heading.parentElement;
  if (!block) {
    throw new Error(`${label} has no breakdown block`);
  }
  return block;
}

let withBusinessStaff: CasePayload;
let withoutBusinessStaff: CasePayload;

beforeAll(() => {
  const raw = execFileSync("python3", ["backend/tests/compare_business_staff_manifest.py"], {
    cwd: repoRoot,
    encoding: "utf8",
  });
  const cases = JSON.parse(raw) as {
    withBusinessStaff: CasePayload;
    withoutBusinessStaff: CasePayload;
  };
  withBusinessStaff = cases.withBusinessStaff;
  withoutBusinessStaff = cases.withoutBusinessStaff;
});

describe("flight comparison passenger breakdown", () => {
  it("prints business staff, with gender, under the business heading", async () => {
    const analysis = withBusinessStaff.dashboard.analysis;
    const business = analysis.business as CabinAnalysis;
    const economy = analysis.economy as CabinAnalysis;

    expect(business.staff.total).toBeGreaterThan(0);
    expect(business.total).toBe(business.passengers.total + business.staff.total);
    expect(
      business.passengers.male
      + business.passengers.female
      + business.passengers.children
      + business.staff.total,
    ).toBe(business.total);
    expect(economy.staff.total).toBeGreaterThan(0);

    renderComparison(withBusinessStaff);

    const economyBlock = await breakdownBlock(`Economy (${economy.total})`);
    expect(economyBlock.textContent).toContain(staffLine(economy.staff));

    const businessBlock = await breakdownBlock(`Business (${business.total})`);
    const text = businessBlock.textContent ?? "";
    const badges = badgeNumbers(text);
    expect(badges.male).toBe(business.passengers.male);
    expect(badges.female).toBe(business.passengers.female);
    expect(badges.children).toBe(business.passengers.children);
    expect(badges.infants).toBe(business.passengers.infants);
    expect(text).toContain(staffLine(business.staff));

    const shownStaff = text.match(/Staff: (\d+)/);
    expect(shownStaff).not.toBeNull();
    const staffTotal = Number(shownStaff?.[1]);
    expect(badges.male + badges.female + badges.children + staffTotal).toBe(business.total);
  });

  it("does not print a business staff line when that cabin has no staff", async () => {
    const business = withoutBusinessStaff.dashboard.analysis.business as CabinAnalysis;
    const economy = withoutBusinessStaff.dashboard.analysis.economy as CabinAnalysis;
    expect(business.staff.total).toBe(0);
    expect(economy.staff.total).toBeGreaterThan(0);

    renderComparison(withoutBusinessStaff);

    const economyBlock = await breakdownBlock(`Economy (${economy.total})`);
    expect(economyBlock.textContent).toContain(staffLine(economy.staff));

    const businessBlock = await breakdownBlock(`Business (${business.total})`);
    const text = businessBlock.textContent ?? "";
    expect(text).not.toMatch(/Staff:/);
    const badges = badgeNumbers(text);
    expect(badges.male + badges.female + badges.children).toBe(business.total);
  });

  it("still lists business staff in the comparison tree matrix", async () => {
    const business = withBusinessStaff.dashboard.analysis.business as CabinAnalysis;
    renderComparison(withBusinessStaff);

    await waitFor(() => {
      expect(document.querySelector("table")).not.toBeNull();
    });
    const table = document.querySelector("table");
    const row = Array.from(table?.querySelectorAll("tr") ?? []).find((tr) =>
      tr.textContent?.includes("Business"),
    );
    const cells = row?.querySelectorAll("td");
    expect(cells?.[3]?.textContent).toBe(String(business.staff.total));
  });
});
