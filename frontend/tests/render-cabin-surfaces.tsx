/**
 * Render the passenger History tab, flight timeline, and activity feed
 * with timeline payloads produced by the backend. Prints the HTML.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerTimeline } from "@/components/dashboard/passenger-timeline";
import { FlightTimeline } from "@/components/dashboard/flight-timeline";
import { ActivityFeed } from "@/components/dashboard/activity-feed";
import type { PassengerTimelineResponse } from "@/lib/types";
import type { FlightTimelineResponse } from "@/lib/types";
import type { ActivityFeedResponse } from "@/lib/types";

interface PassengerCase {
  flightNumber: string;
  pnr: string;
  origin: string;
  date: string;
  data: PassengerTimelineResponse;
}

interface FlightCase {
  flightNumber: string;
  origin: string;
  date: string;
  data: FlightTimelineResponse;
}

interface ActivityCase {
  date?: string;
  limit?: number;
  categories?: string;
  data: ActivityFeedResponse;
}

interface RenderRequest {
  passenger?: PassengerCase;
  flight?: FlightCase;
  activity?: ActivityCase;
}

function renderWithCache(queryKey: readonly unknown[], data: unknown, node: React.ReactNode): string {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, refetchOnMount: false },
    },
  });
  client.setQueryData(queryKey, data);
  return renderToStaticMarkup(
    createElement(QueryClientProvider, { client }, node),
  );
}

function sliceBetween(html: string, start: string, end: string | null): string {
  const from = html.indexOf(start);
  if (from < 0) {
    return "";
  }
  if (!end) {
    return html.slice(from);
  }
  const to = html.indexOf(end, from + start.length);
  return html.slice(from, to < 0 ? html.length : to);
}

const request = JSON.parse(readFileSync(process.argv[2], "utf8")) as RenderRequest;
const result: Record<string, unknown> = {};

if (request.passenger) {
  const item = request.passenger;
  const html = renderWithCache(
    ["passengerTimeline", item.flightNumber, item.pnr, item.origin, item.date],
    item.data,
    createElement(PassengerTimeline, {
      flightNumber: item.flightNumber,
      pnr: item.pnr,
      origin: item.origin,
      date: item.date,
    }),
  );
  result.passenger = {
    html,
    statusHtml: sliceBetween(html, "Current Status", "Event History"),
    eventHtml: sliceBetween(html, "Event History", null),
  };
}

if (request.flight) {
  const item = request.flight;
  result.flight = {
    html: renderWithCache(
      ["flightTimeline", item.flightNumber, item.origin, item.date],
      item.data,
      createElement(FlightTimeline, {
        flightNumber: item.flightNumber,
        origin: item.origin,
        date: item.date,
      }),
    ),
  };
}

if (request.activity) {
  const item = request.activity;
  const limit = item.limit ?? 50;
  result.activity = {
    html: renderWithCache(
      ["activityFeed", item.date, limit, item.categories],
      item.data,
      createElement(ActivityFeed, {
        date: item.date,
        limit,
        categories: item.categories,
      }),
    ),
  };
}

process.stdout.write(JSON.stringify(result));
