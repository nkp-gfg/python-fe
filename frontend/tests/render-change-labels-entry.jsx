import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/ui/toast";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { FlightInsightsPanel } from "@/components/dashboard/flight-insights";

function activityLabels(html) {
  if (!html.includes(">Activity Stream<")) {
    const errorAt = html.indexOf("Overview");
    const hint = html.slice(Math.max(0, errorAt), Math.max(0, errorAt) + 500);
    throw new Error(`Activity Stream card was not rendered. Nearby markup: ${hint}`);
  }
  const rows = [];
  const re = /<span class="text-muted-foreground capitalize">([^<]*)<\/span>/g;
  let match;
  while ((match = re.exec(html))) {
    rows.push(match[1]);
  }
  return rows;
}

function velocityLabels(html) {
  const start = html.indexOf(">Change Velocity<");
  if (start < 0) {
    throw new Error("Change Velocity card was not rendered");
  }
  const end = html.indexOf(">Multi-Segment Itinerary<", start);
  const slice = html.slice(start, end < 0 ? start + 8000 : end);
  const rows = [];
  const re = /<span class="text-muted-foreground">([^<]*)<\/span>\s*<span class="[^"]*tabular-nums[^"]*">([^<]*)<\/span>/g;
  let match;
  while ((match = re.exec(slice))) {
    rows.push(match[1]);
  }
  return rows;
}

export function renderChangeLabels(dashboard, insights, selection) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["flights"], []);
  client.setQueryData(
    ["dashboard", selection.flightNumber, selection.origin, selection.date, null],
    dashboard,
  );
  client.setQueryData(
    ["tree", selection.flightNumber, selection.origin, selection.date, null],
    { title: "tree", nodes: [], edges: [], statusCards: [] },
  );

  const activityHtml = renderToStaticMarkup(
    React.createElement(
      QueryClientProvider,
      { client },
      React.createElement(
        ToastProvider,
        null,
        React.createElement(FlightWorkbench, { initialSelection: selection }),
      ),
    ),
  );

  const velocityHtml = renderToStaticMarkup(
    React.createElement(FlightInsightsPanel, {
      insights,
      totalPassengers: 1,
    }),
  );

  return {
    activity: activityLabels(activityHtml),
    velocity: velocityLabels(velocityHtml),
    activityHtmlHasStream: activityHtml.includes("Activity Stream"),
  };
}
