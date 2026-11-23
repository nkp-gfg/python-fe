// @ts-nocheck
/**
 * Renders the real Network Analytics page and checks that flight-level change
 * types survive the Change Events Distribution chart.
 *
 * The page loads the JSON body returned by GET /analytics/network. Chart
 * categories live on the ECharts option the page passes in, and any omitted-type
 * list or separate flight-level chart shows up as text or as another axis.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime.js";
import AnalyticsPage from "../app/analytics/page";

const FLIGHT_LEVEL_ALIASES: Record<string, string[]> = {
  GATE_CHANGE: ["Gate Change", "GATE_CHANGE"],
  STATUS_CHANGE: ["Status Change", "STATUS_CHANGE"],
  TERMINAL_CHANGE: ["Terminal", "TERMINAL_CHANGE"],
  BOARDING_TIME_CHANGE: ["Board Time", "BOARDING_TIME_CHANGE"],
  JUMPSEAT_CHANGE: ["JUMPSEAT_CHANGE", "Jump Seat", "Jumpseat"],
};

type Axis = { categories: string[] };

function chartAxes(host: HTMLElement): Axis[] {
  const fiberKey = Object.keys(host).find(
    (key) => key.startsWith("__reactContainer") || key.startsWith("__reactFiber"),
  );
  const axes: Axis[] = [];
  const walk = (fiber: { memoizedProps?: { option?: { yAxis?: { data?: unknown }; xAxis?: { data?: unknown } } }; child?: unknown; sibling?: unknown } | null, depth: number) => {
    if (!fiber || depth > 8000) return;
    const option = fiber.memoizedProps?.option;
    const yData = option?.yAxis?.data;
    const xData = option?.xAxis?.data;
    const categories = Array.isArray(yData) ? yData : Array.isArray(xData) ? xData : null;
    if (categories && categories.length > 0) {
      axes.push({ categories: categories.map((item) => String(item)) });
    }
    walk(fiber.child as typeof fiber, depth + 1);
    walk(fiber.sibling as typeof fiber, depth + 1);
  };
  if (fiberKey) walk((host as unknown as Record<string, unknown>)[fiberKey] as Parameters<typeof walk>[0], 0);
  return axes;
}

function surfaceIncludes(surface: string, aliases: string[]): boolean {
  return aliases.some((alias) => surface.includes(alias));
}

async function main() {
  const fixture = (globalThis as unknown as { __CHANGE_EVENTS_FIXTURE__: { network: { totalChanges: number } } }).__CHANGE_EVENTS_FIXTURE__;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = document.getElementById("root");
  if (!host) throw new Error("render host missing");
  const root = createRoot(host);

  await act(async () => {
    root.render(
      <SearchParamsContext.Provider value={new URLSearchParams() as never}>
        <QueryClientProvider client={client}>
          <AnalyticsPage />
        </QueryClientProvider>
      </SearchParamsContext.Provider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });

  const text = document.body.textContent ?? "";
  const axes = chartAxes(host);
  const surface = [text, ...axes.flatMap((axis) => axis.categories)].join("\n");
  const missing = Object.entries(FLIGHT_LEVEL_ALIASES)
    .filter(([, aliases]) => !surfaceIncludes(surface, aliases))
    .map(([changeType]) => changeType);

  const failures: string[] = [];
  if (!text.includes("Change Events Distribution")) {
    failures.push("Network Analytics did not render Change Events Distribution");
  }
  if (!text.includes(String(fixture.network.totalChanges))) {
    failures.push(
      `headline total ${fixture.network.totalChanges} is not shown; the chart must not replace the full count`,
    );
  }
  if (missing.length > 0) {
    failures.push(
      `flight-level change types missing from Change Events Distribution (chart categories and surrounding text): ${missing.join(", ")}`,
    );
  }

  if (failures.length > 0) {
    console.error(failures.join("\n"));
    console.error("chart categories:");
    for (const axis of axes) console.error(`  ${JSON.stringify(axis.categories)}`);
    process.exit(1);
  }
  process.exit(0);
}

void main();
