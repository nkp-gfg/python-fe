/**
 * Render the Live Ops published-schedule card from a dashboard payload.
 *
 * The payload is the JSON object produced by the backend schedule path
 * (stdin). This file mounts the real FlightWorkbench component and prints
 * the clocks that component paints for Depart, Arrive, and each segment.
 */
require("./register-tsx.cjs");

const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { QueryClient, QueryClientProvider } = require("@tanstack/react-query");
const { AppRouterContext } = require("next/dist/shared/lib/app-router-context.shared-runtime");
const { PathnameContext } = require("next/dist/shared/lib/hooks-client-context.shared-runtime");

const { FlightWorkbench } = require("../components/dashboard/flight-workbench.tsx");
const { ToastProvider } = require("../components/ui/toast.tsx");

const router = {
  back() {},
  forward() {},
  refresh() {},
  push() {},
  replace() {},
  prefetch() {},
};

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

function clocksFromMarkup(html) {
  const summary = [];
  const summaryPattern =
    /<p class="text-sm font-semibold">([^<]*)<\/p><p class="text-\[10px\] text-muted-foreground">(Depart|Arrive)<\/p>/g;
  let match;
  while ((match = summaryPattern.exec(html)) !== null) {
    summary.push({ label: match[2], text: match[1] });
  }

  const segments = [];
  const segmentPattern = /<span>([^<]*→[^<]*)<\/span>/g;
  while ((match = segmentPattern.exec(html)) !== null) {
    segments.push(match[1]);
  }

  const stdMatch = html.match(/STD ([^<]+)/);
  const etdMatch = html.match(/ETD ([^<(]+) \(([^)]+)m\)/);

  return {
    summary,
    segments,
    hasPublishedSchedule: html.includes("Published Schedule"),
    scheduleDelay: {
      std: stdMatch ? stdMatch[1] : null,
      etd: etdMatch ? etdMatch[1].trim() : null,
      delayMinutes: etdMatch ? etdMatch[2] : null,
    },
  };
}

async function main() {
  const raw = await readStdin();
  const payload = JSON.parse(raw);
  const dashboard = payload.dashboard;
  const selection = payload.selection;

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(
    ["dashboard", selection.flightNumber, selection.origin, selection.date, null],
    dashboard,
  );
  client.setQueryData(["flights"], [
    {
      airline: "GF",
      flightNumber: selection.flightNumber,
      origin: selection.origin,
      destination: dashboard.schedule?.destination || dashboard.route?.destination || "",
      departureDate: selection.date,
      status: dashboard.flightStatus?.status || "SCHEDULED",
      schedule: dashboard.flightStatus?.schedule || {},
    },
  ]);
  client.setQueryData(
    ["tree", selection.flightNumber, selection.origin, selection.date, null],
    { nodes: [] },
  );

  const html = renderToStaticMarkup(
    React.createElement(
      AppRouterContext.Provider,
      { value: router },
      React.createElement(
        PathnameContext.Provider,
        { value: `/flights/${selection.flightNumber}` },
        React.createElement(
          QueryClientProvider,
          { client },
          React.createElement(
            ToastProvider,
            null,
            React.createElement(FlightWorkbench, { initialSelection: selection }),
          ),
        ),
      ),
    ),
  );

  process.stdout.write(`${JSON.stringify(clocksFromMarkup(html))}\n`);
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
