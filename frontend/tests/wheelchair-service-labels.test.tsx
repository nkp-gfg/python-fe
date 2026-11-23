/**
 * Renders the network analytics wheelchair panel with the distribution produced
 * by the backend test (Trip Search XML -> convert_reservations ->
 * _run_network_analytics). The panel must name the wheelchair service for every
 * Sabre wheelchair code and keep the raw code visible.
 *
 * These assertions fail while the panel prints walking-ability phrases, or
 * prints nothing, for a code the API returned.
 */
import fs from "node:fs";
import type { ReactNode } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

const fixturePath = process.env.WHEELCHAIR_ANALYTICS_FIXTURE;
if (!fixturePath) {
  throw new Error(
    "WHEELCHAIR_ANALYTICS_FIXTURE must point at the analytics JSON written by backend/tests/test_wheelchair_service_labels.py",
  );
}
const analyticsPayload = JSON.parse(fs.readFileSync(fixturePath, "utf8"));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("@/components/ui/echarts", () => ({
  EChart: () => <div data-testid="chart" />,
}));

vi.mock("@/lib/api", () => ({
  fetchFlights: vi.fn(async () => []),
  fetchNetworkAnalytics: vi.fn(async () => analyticsPayload),
}));

import AnalyticsPage from "@/app/analytics/page";

type ServiceExpectation = {
  allOf: string[];
  anyOf?: string[];
};

/**
 * Service wording required under each code. Phrases that only describe walking
 * ability are rejected even if a service word is added beside them.
 */
const SERVICE_LABELS: Record<string, ServiceExpectation> = {
  WCHR: { allOf: ["wheelchair", "ramp"] },
  WCHS: { allOf: ["wheelchair"], anyOf: ["stair", "step"] },
  WCHC: { allOf: ["wheelchair"], anyOf: ["seat", "cabin", "aisle"] },
  WCBD: { allOf: ["wheelchair", "battery"], anyOf: ["dry", "non-spillable", "nonspillable"] },
  WCBW: { allOf: ["wheelchair", "battery", "wet"] },
  WCMP: { allOf: ["wheelchair", "manual"] },
  WCOB: { allOf: ["wheelchair"], anyOf: ["on board", "onboard", "on-board"] },
};

const CAPABILITY_PHRASES = [
  "can walk short distance",
  "cannot walk steps",
  "completely immobile",
];

function rowText(card: HTMLElement, code: string): string {
  const codeNode = within(card).getByText((_, element) => {
    return element?.tagName === "SPAN" && element.textContent === code;
  });
  const row =
    codeNode.closest("div.space-y-1") ??
    codeNode.parentElement?.parentElement;
  if (!row) {
    throw new Error(`No wheelchair row for ${code}`);
  }
  return row.textContent ?? "";
}

describe("network analytics wheelchair services", () => {
  it("names the wheelchair service beside each code returned by analytics", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <AnalyticsPage />
      </QueryClientProvider>,
    );

    const heading = await screen.findByRole("heading", {
      name: "Wheelchair / Accessibility",
    });
    const card = heading.parentElement?.parentElement as HTMLElement;
    expect(card).toBeTruthy();

    const distribution = analyticsPayload.wheelchairDistribution as {
      code: string;
      count: number;
    }[];
    expect(distribution.map((item) => item.code).sort()).toEqual(
      Object.keys(SERVICE_LABELS).sort(),
    );

    await waitFor(() => {
      expect(within(card).getByText("WCHR")).toBeTruthy();
    });

    const problems: string[] = [];
    for (const item of distribution) {
      const expectation = SERVICE_LABELS[item.code];
      const text = rowText(card, item.code);
      const normalized = text.toLowerCase();
      const reasons: string[] = [];

      if (!new RegExp(`${item.count.toLocaleString()} \\(`).test(text)) {
        reasons.push(`count ${item.count} is not shown beside the code`);
      }
      if (normalized.trim() === "" || normalized === item.code.toLowerCase()) {
        reasons.push("service description is blank");
      }
      for (const phrase of CAPABILITY_PHRASES) {
        if (normalized.includes(phrase)) {
          reasons.push(`uses walking-ability phrase "${phrase}"`);
        }
      }
      for (const required of expectation.allOf) {
        if (!normalized.includes(required)) {
          reasons.push(`description does not mention "${required}"`);
        }
      }
      if (
        expectation.anyOf &&
        !expectation.anyOf.some((option) => normalized.includes(option))
      ) {
        reasons.push(
          `description does not mention one of: ${expectation.anyOf.join(", ")}`,
        );
      }
      if (reasons.length > 0) {
        problems.push(`${item.code} rendered "${text}": ${reasons.join("; ")}`);
      }
    }

    expect(problems).toEqual([]);
  });
});
