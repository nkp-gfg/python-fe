/**
 * Renders PassengerTable against a manifest and the audit payload produced
 * by the flight audit route. Prints which watched rows contain the audit
 * warning icon.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerTable } from "../components/dashboard/passenger-table";

interface RenderInput {
  flightNumber: string;
  origin: string;
  date: string;
  manifest: unknown;
  audit: unknown;
  watched: string[];
}

const raw = readFileSync(0, "utf8");
const input = JSON.parse(raw) as RenderInput;

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});
queryClient.setQueryData(
  ["passengers", input.flightNumber, input.origin, input.date, undefined],
  input.manifest,
);
queryClient.setQueryData(
  ["audit", input.flightNumber, input.origin, input.date],
  input.audit,
);

const noop = () => {};
const html = renderToStaticMarkup(
  createElement(
    QueryClientProvider,
    { client: queryClient },
    createElement(PassengerTable, {
      flightNumber: input.flightNumber,
      origin: input.origin,
      date: input.date,
      filterCabin: "all",
      setFilterCabin: noop,
      filterStatus: "all",
      setFilterStatus: noop,
      filterType: "all",
      setFilterType: noop,
      filterLoyalty: "all",
      setFilterLoyalty: noop,
      filterNationality: "all",
      setFilterNationality: noop,
    }),
  ),
);

const icons: Record<string, boolean | null> = {};
for (const label of input.watched) {
  const at = html.indexOf(label);
  if (at < 0) {
    icons[label] = null;
    continue;
  }
  const start = html.lastIndexOf("<tr", at);
  const next = html.indexOf("<tr", at + label.length);
  const row = html.slice(start, next === -1 ? html.length : next);
  icons[label] =
    row.includes("lucide-triangle-alert") || row.includes("audit alert");
}

process.stdout.write(
  JSON.stringify({
    icons,
    loading: html.includes("Loading manifest"),
    error: html.includes("Failed to load passenger list"),
  }),
);
