/**
 * Render the real StandbyPanel against a standby-list payload.
 *
 * The payload is the JSON returned by GET /flights/{flight}/passengers/standby-list
 * after the product parser and converter have run. This script does not invent
 * cabin numbers; it only mounts the component the dashboard mounts.
 *
 * Usage: tsx frontend/tests/render-standby-panel.tsx <payload.json>
 * stdout: rendered HTML
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StandbyPanel } from "../components/dashboard/standby-panel";
import { fetchStandbyList } from "../lib/api";

interface RenderSpec {
  flightNumber: string;
  origin: string;
  date: string;
  payload: unknown;
}

async function main() {
  const spec = JSON.parse(readFileSync(process.argv[2], "utf8")) as RenderSpec;

  globalThis.fetch = async () =>
    new Response(JSON.stringify(spec.payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: Infinity,
        gcTime: Infinity,
      },
    },
  });

  const queryKey = ["standby", spec.flightNumber, spec.origin, spec.date, undefined] as const;

  await queryClient.prefetchQuery({
    queryKey,
    queryFn: () => fetchStandbyList(spec.flightNumber, spec.origin, spec.date),
  });

  const html = renderToString(
    createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(StandbyPanel, {
        flightNumber: spec.flightNumber,
        origin: spec.origin,
        date: spec.date,
      }),
    ),
  );

  process.stdout.write(html);
}

main();
