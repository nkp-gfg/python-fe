import { readFileSync } from "node:fs";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ComponentType } from "react";

/**
 * The Changes tab must show every change the summary counts.
 * The All chip, and a type chip, may print a count only when that many rows
 * are on screen. These tests fail while the tab requests a single 100-row
 * page and prints the uncapped summary above it.
 */

interface Fixture {
  origin: string;
  date: string;
  fullPage: { flight: string; total: number; required: string[] };
  truncated: { flight: string; total: number; required: string[] };
  checkins: {
    flight: string;
    totalCheckins: number;
    required: string[];
    forbidden: string[];
    chip: string;
  };
  overCap: {
    flight: string;
    total: number;
    required: string[];
    serverPageCap: number;
  };
}

let ChangeTimeline: ComponentType<{
  flightNumber: string;
  origin: string;
  date: string;
}>;
let fixture: Fixture;
let apiBase = "";

beforeAll(async () => {
  apiBase = readFileSync("/tmp/falconeye-change-history-url", "utf8").trim();
  process.env.NEXT_PUBLIC_API_URL = apiBase;
  fixture = await fetch(`${apiBase}/__fixture`).then((response) => response.json());
  ({ ChangeTimeline } = await import("@/components/dashboard/change-timeline"));
});

afterEach(() => {
  cleanup();
});

function renderTimeline(flightNumber: string) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <ChangeTimeline
        flightNumber={flightNumber}
        origin={fixture.origin}
        date={fixture.date}
      />
    </QueryClientProvider>
  );
}

function rowTexts(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("p.text-sm")).map(
    (node) => node.textContent ?? ""
  );
}

function chip(container: HTMLElement, label: string): string {
  const button = Array.from(container.querySelectorAll("button")).find((node) =>
    (node.textContent ?? "").trim().startsWith(label)
  );
  return button?.textContent?.trim() ?? "";
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function expectRows(
  container: HTMLElement,
  options: {
    total: number;
    required: string[];
    chipLabel: string;
    forbidden?: string[];
    rowIncludes?: string;
  }
) {
  const deadline = Date.now() + 15_000;
  let previous = "";
  let stablePasses = 0;
  let lastProblem = "timeline did not render";

  while (Date.now() < deadline) {
    const rows = rowTexts(container);
    const relevant = options.rowIncludes
      ? rows.filter((row) => row.includes(options.rowIncludes as string))
      : rows;
    const missing = options.required.filter(
      (name) => !rows.some((row) => row.includes(name))
    );
    const label = chip(container, options.chipLabel);
    const count = label.match(/(\d+)\s*$/);
    const chipCount = count ? Number(count[1]) : null;
    const forbidden = (options.forbidden ?? []).filter((prefix) =>
      rows.some((row) => row.includes(prefix))
    );
    const complete =
      missing.length === 0 &&
      relevant.length === options.total &&
      forbidden.length === 0 &&
      chipCount === relevant.length;

    if (complete) return;

    lastProblem = `showing ${relevant.length} of ${options.total} rows; missing ${missing.join(", ") || "none"}; chip "${label}"`;
    if (forbidden.length > 0) {
      lastProblem += `; still contains ${forbidden.join(", ")}`;
    }

    const signature = `${relevant.length}|${label}|${missing.join(",")}`;
    if (rows.length > 0 && signature === previous) stablePasses += 1;
    else stablePasses = 0;
    previous = signature;

    // A settled short page will not grow on its own. Report it instead of waiting out the timeout.
    if (stablePasses >= 4 && rows.length > 0 && relevant.length !== options.total) {
      throw new Error(lastProblem);
    }
    await sleep(40);
  }
  throw new Error(lastProblem);
}

describe("Changes tab history", () => {
  it("labels a single page as the full history when every change fits", async () => {
    expect(fixture.fullPage.total).toBe(100);
    const { container } = renderTimeline(fixture.fullPage.flight);
    await expectRows(container, {
      total: fixture.fullPage.total,
      required: fixture.fullPage.required,
      chipLabel: "All",
    });
  });

  it("shows the early add, seat move, and check-in once the history passes one page", async () => {
    expect(fixture.truncated.total).toBeGreaterThan(100);
    const { container } = renderTimeline(fixture.truncated.flight);
    await expectRows(container, {
      total: fixture.truncated.total,
      required: fixture.truncated.required,
      chipLabel: "All",
    });
  });

  it("shows every check-in when that type chip counts more than one page", async () => {
    expect(fixture.checkins.totalCheckins).toBeGreaterThan(100);
    const { container } = renderTimeline(fixture.checkins.flight);
    await waitFor(() => {
      const button = Array.from(container.querySelectorAll("button")).find((node) =>
        (node.textContent ?? "").includes(fixture.checkins.chip)
      );
      if (!button) throw new Error("check-in chip is not rendered");
    });
    const button = Array.from(container.querySelectorAll("button")).find((node) =>
      (node.textContent ?? "").includes(fixture.checkins.chip)
    );
    fireEvent.click(button as HTMLButtonElement);
    await expectRows(container, {
      total: fixture.checkins.totalCheckins,
      required: fixture.checkins.required,
      forbidden: fixture.checkins.forbidden,
      chipLabel: fixture.checkins.chip,
      rowIncludes: "CK",
    });
  });

  it("shows the oldest change when the history is longer than the server page cap", async () => {
    expect(fixture.overCap.total).toBe(fixture.overCap.serverPageCap + 1);
    const { container } = renderTimeline(fixture.overCap.flight);
    await expectRows(container, {
      total: fixture.overCap.total,
      required: fixture.overCap.required,
      chipLabel: "All",
    });
  });
});
