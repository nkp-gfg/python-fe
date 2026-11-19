import { createElement, type ReactNode } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FlightTimeline } from "../components/dashboard/flight-timeline";
import { ActivityFeed } from "../components/dashboard/activity-feed";

async function renderSurface(element: ReactNode): Promise<string> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root: Root = createRoot(host);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });

  await act(async () => {
    root.render(createElement(QueryClientProvider, { client }, element));
  });

  let text = "";
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 25));
    });
    text = host.textContent || "";
    if (!text.includes("Loading") && text.trim().length > 0) {
      break;
    }
  }

  await act(async () => {
    root.unmount();
  });
  host.remove();
  return text;
}

export async function renderActivitySurfaces(): Promise<{ timeline: string; feed: string }> {
  const timeline = await renderSurface(
    createElement(FlightTimeline, {
      flightNumber: "2057",
      origin: "BAH",
      date: "2026-03-19",
    }),
  );
  const feed = await renderSurface(
    createElement(ActivityFeed, { date: "2026-03-19", limit: 50 }),
  );
  return { timeline, feed };
}
