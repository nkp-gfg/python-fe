/**
 * Render the Activity timeline with a timeline JSON document and print the
 * summary numbers the operator would see.
 *
 * The payload is the body of GET /flights/{flight}/timeline. This runner loads
 * the real FlightTimeline component, prefetches through fetchFlightTimeline
 * (frontend/lib/api.ts), and server-renders the tree. It does not reimplement
 * the summary.
 */
import { createJiti } from "jiti";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const payloadPath = process.argv[2];

if (!payloadPath) {
  console.error("usage: node render-activity-summary.mjs <payload.json>");
  process.exit(2);
}

const payload = JSON.parse(readFileSync(payloadPath, "utf8"));
const { flightNumber, origin, date, timeline } = payload;

const jiti = createJiti(import.meta.url, {
  jsx: { runtime: "automatic" },
  fsCache: false,
  moduleCache: false,
  alias: {
    "@": frontendRoot,
  },
});

const React = await jiti.import("react");
const { renderToStaticMarkup } = await jiti.import("react-dom/server");
const { QueryClient, QueryClientProvider } = await jiti.import(
  "@tanstack/react-query",
);
const { FlightTimeline } = await jiti.import(
  pathToFileURL(path.join(frontendRoot, "components/dashboard/flight-timeline.tsx")).href,
);
const { fetchFlightTimeline } = await jiti.import(
  pathToFileURL(path.join(frontendRoot, "lib/api.ts")).href,
);

const requested = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  requested.push({ url, method: init?.method ?? "GET" });
  const expectedPath = `/flights/${flightNumber}/timeline`;
  if (url.includes(expectedPath)) {
    return new Response(JSON.stringify(timeline), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  return new Response(JSON.stringify({ detail: "not found" }), {
    status: 404,
    headers: { "content-type": "application/json" },
  });
};

try {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  await queryClient.prefetchQuery({
    queryKey: ["flightTimeline", flightNumber, origin, date],
    queryFn: () => fetchFlightTimeline(flightNumber, origin, date),
  });

  const html = renderToStaticMarkup(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(FlightTimeline, { flightNumber, origin, date }),
    ),
  );

  const summary = {
    checkins: countBeforeLabel(html, "Check-ins"),
    boarded: countBeforeLabel(html, "Boarded"),
    upgrades: countBeforeLabel(html, "Upgrades"),
    seatChanges: countBeforeLabel(html, "Seat Changes"),
    totalEvents: totalEventsValue(html),
    requested,
    showsCheckinEvent: html.includes("Passenger checked in"),
    showsBoardingEvent: html.includes("Passenger boarded aircraft"),
    showsUpgradeEvent: html.includes("Cabin upgrade"),
    showsSeatEvent: html.includes("Seat changed:"),
    showsLoading: html.includes("Loading timeline"),
    showsEmpty: html.includes("No activity recorded yet"),
    showsSummary: html.includes("Activity Summary"),
  };

  process.stdout.write(`${JSON.stringify(summary)}\n`);
} finally {
  globalThis.fetch = originalFetch;
}

function countBeforeLabel(html, label) {
  const idx = html.indexOf(label);
  if (idx < 0) {
    throw new Error(`Activity summary label ${JSON.stringify(label)} was not rendered`);
  }
  const before = html.slice(Math.max(0, idx - 300), idx);
  const matches = [...before.matchAll(/>(\d+)</g)];
  if (matches.length === 0) {
    throw new Error(
      `No count rendered before ${JSON.stringify(label)}. Nearby markup: ${before}`,
    );
  }
  return Number(matches[matches.length - 1][1]);
}

function totalEventsValue(html) {
  const match = html.match(/Total Events:\s*(\d+)/);
  if (!match) {
    throw new Error("Total Events line was not rendered");
  }
  return Number(match[1]);
}
