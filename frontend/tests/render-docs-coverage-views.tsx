import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ReadinessInsightsTab } from "@/components/dashboard/flight-insight-tabs";
import { RiskTab } from "@/components/dashboard/risk-tab";
import { FlightInsightsPanel } from "@/components/dashboard/flight-insights";
import { InsightInfoPanel } from "@/components/dashboard/insight-info-panel";
import type { FlightDashboard, FlightInsights } from "@/lib/types";

export type DocsCoveragePayload = {
  insights: FlightInsights;
  totalPassengers: number;
  dashboard: FlightDashboard;
};

function visibleText(html: string) {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function markup(element: ReactElement) {
  return visibleText(renderToStaticMarkup(element));
}

export function renderDocsCoverage(payload: DocsCoveragePayload) {
  const insights = payload.insights;
  const totalPassengers = payload.totalPassengers;
  return {
    readiness: markup(createElement(ReadinessInsightsTab, { insights, totalPassengers })),
    risk: markup(createElement(RiskTab, { dashboard: payload.dashboard })),
    flightInsights: markup(createElement(FlightInsightsPanel, { insights, totalPassengers })),
    documentInfo: markup(createElement(InsightInfoPanel, { activeKey: "documentCompliance" })),
    editCodesInfo: markup(createElement(InsightInfoPanel, { activeKey: "editCodes" })),
  };
}
