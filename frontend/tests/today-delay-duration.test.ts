/**
 * Today's Flights delay column must show every ISO 8601 duration stored on
 * the OTP flight, including values that omit a zero hour, minute, or second
 * component. The cell is a dash only when the duration is empty, zero, or
 * not a duration.
 *
 * This renders the real Today page from the JSON GET /otp/flights returns.
 * It fails while parseDurationMin only accepts "PT<hours>H<minutes>M".
 *
 * Run from frontend/:
 *   npx --yes tsx --tsconfig tsconfig.json tests/today-delay-duration.test.ts
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { format } from "date-fns";
import { ThemeProvider } from "next-themes";

import TodayPage from "../app/today/page";
import { ToastProvider } from "../components/ui/toast";
import { fetchOtpFlights } from "../lib/api";
import type { OtpFlight } from "../lib/types";

/** Expected delay-cell text. null means a dash and no "+Nm" badge. */
const EXPECTED: Record<string, string | null> = {
  "9001": "+24m", // PT24M
  "9002": "+120m", // PT2H
  "9003": "+24m", // PT0H24M — the shape the current pattern accepts
  "9004": "+150m", // PT2H30M
  "9005": "+90.5m", // PT1H30M30S — seconds must be kept
  "9006": "+1440m", // P1D
  "9007": "+0.75m", // PT45S
  "9008": "+135m", // PT15M + PT2H
  "9009": "+1590m", // P1DT2H30M
  "9010": null, // empty string
  "9011": null, // not a duration
  "9012": null, // no delay_details
  "9013": "+30m", // PT0H10M + PT20M
  "9014": "+24m", // PT24M plus an empty string
  "9015": null, // "XXPT1H2MYY" is not a duration
  "9016": null, // "PT1H30M30S extra" is not a duration
};

function repoRoot(): string {
  const cwd = process.cwd();
  return cwd.endsWith(`${path.sep}frontend`) ? path.resolve(cwd, "..") : cwd;
}

function loadApiFlights(): { body: string; flights: OtpFlight[] } {
  const body = execFileSync(
    "python3",
    ["backend/tests/test_otp_delay_duration.py", "--http-body"],
    { cwd: repoRoot(), encoding: "utf8" },
  );
  return { body, flights: JSON.parse(body) as OtpFlight[] };
}

function rowSlice(html: string, flightNumber: string, flightNumbers: string[]): string {
  const label = `GF${flightNumber}`;
  const start = html.indexOf(label);
  if (start < 0) {
    throw new Error(`Flight ${label} was not rendered. Markup starts: ${html.slice(0, 400)}`);
  }
  let end = html.length;
  for (const other of flightNumbers) {
    if (other === flightNumber) continue;
    const at = html.indexOf(`GF${other}`, start + label.length);
    if (at >= 0 && at < end) end = at;
  }
  return html.slice(start, end);
}

function delayText(slice: string): string | null {
  const match = slice.match(/\+(\d+(?:\.\d+)?)m/);
  return match ? match[0] : null;
}

async function main(): Promise<void> {
  const { body, flights } = loadApiFlights();
  const parsed = JSON.parse(body) as OtpFlight[];
  const failures: string[] = [];

  for (const flight of parsed) {
    const stored = flight.delayDetails?.map((item) => item.DelayDuration) ?? null;
    if (flight.flightNumber === "9001" && stored?.[0] !== "PT24M") {
      failures.push(`API changed PT24M before the page: ${JSON.stringify(stored)}`);
    }
    if (flight.flightNumber === "9002" && stored?.[0] !== "PT2H") {
      failures.push(`API changed PT2H before the page: ${JSON.stringify(stored)}`);
    }
  }

  const dateStr = format(new Date(), "yyyy-MM-dd");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (!url.includes(`/otp/flights?date=${dateStr}`)) {
      throw new Error(`unexpected fetch ${url}`);
    }
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  try {
    await client.prefetchQuery({
      queryKey: ["otp-flights", dateStr],
      queryFn: () => fetchOtpFlights(dateStr),
    });

    const html = renderToStaticMarkup(
      React.createElement(
        ThemeProvider,
        { attribute: "class", defaultTheme: "dark", enableSystem: false },
        React.createElement(
          QueryClientProvider,
          { client },
          React.createElement(ToastProvider, null, React.createElement(TodayPage)),
        ),
      ),
    );

    if (html.includes("Loading flights") || html.includes("Failed to load flights")) {
      throw new Error(`Today page did not render the OTP rows.\n${html.slice(0, 500)}`);
    }

    const numbers = flights.map((flight) => flight.flightNumber);
    for (const flightNumber of numbers) {
      const expected = EXPECTED[flightNumber];
      const slice = rowSlice(html, flightNumber, numbers);
      const actual = delayText(slice);
      if (expected === null) {
        if (actual !== null) {
          failures.push(`GF${flightNumber}: expected a dash, rendered ${actual}`);
        } else if (!slice.includes("—")) {
          failures.push(`GF${flightNumber}: expected the delay cell to be a dash`);
        }
      } else if (actual !== expected) {
        failures.push(
          `GF${flightNumber}: expected ${expected}, rendered ${actual ?? "—"}`,
        );
      }
    }
  } finally {
    globalThis.fetch = originalFetch;
    client.clear();
  }

  if (failures.length > 0) {
    console.error(failures.join("\n"));
    process.exit(1);
  }
  console.log("PASS: today's flights show every stored delay duration");
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
