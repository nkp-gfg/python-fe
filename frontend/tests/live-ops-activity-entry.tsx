import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/ui/toast";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";
import { FlightInsightsPanel } from "@/components/dashboard/flight-insights";
import type { FlightDashboard } from "@/lib/types";

export interface RenderInput {
  flightNumber: string;
  origin: string;
  date: string;
  dashboard: FlightDashboard;
}

function activityText(html: string): string {
  const start = html.indexOf("Activity Stream");
  if (start < 0) return "";
  const slice = html.slice(start, start + 4000);
  const published = slice.indexOf("Published Schedule");
  const bounded = published === -1 ? slice : slice.slice(0, published);
  return bounded.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function velocityText(html: string): string {
  const start = html.indexOf("Change Velocity");
  if (start < 0) return "";
  const slice = html.slice(start, start + 4000);
  return slice.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

export function changeTypeVisible(text: string, changeType: string): boolean {
  const spaced = changeType.replace(/([A-Z])/g, " $1").trim();
  const words = changeType.toLowerCase().replace(/_/g, " ");
  const lower = text.toLowerCase();
  return text.includes(spaced) || text.includes(changeType) || lower.includes(words);
}

export function renderLiveOps(input: RenderInput) {
  const { flightNumber, origin, date, dashboard } = input;
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: Infinity,
        refetchOnMount: false,
        refetchOnWindowFocus: false,
        refetchInterval: false,
      },
    },
  });
  client.setQueryData(["flights"], [
    {
      airline: "GF",
      flightNumber,
      origin,
      departureDate: date,
      destination: dashboard.route?.destination || "",
      status: dashboard.flightStatus?.status || "",
    },
  ]);
  client.setQueryData(
    ["dashboard", flightNumber, origin, date, null],
    dashboard,
  );
  client.setQueryData(
    ["tree", flightNumber, origin, date, null],
    { title: "", badge: "", width: 0, height: 0, nodes: [], edges: [], statusCards: [] },
  );

  const workbenchHtml = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(
        ToastProvider,
        null,
        createElement(FlightWorkbench, {
          initialSelection: { flightNumber, origin, date },
        }),
      ),
    ),
  );
  const insightsHtml = renderToStaticMarkup(
    createElement(FlightInsightsPanel, {
      insights: dashboard.insights,
      totalPassengers: dashboard.passengerSummary?.totalPassengers ?? 0,
    }),
  );

  return {
    activityText: activityText(workbenchHtml),
    velocityText: velocityText(insightsHtml),
    workbenchHasActivityHeading: workbenchHtml.includes("Activity Stream"),
    insightsHasVelocityHeading: insightsHtml.includes("Change Velocity"),
  };
}
