/**
 * Render the dashboard surfaces that act on classMismatch.
 * The dashboard JSON is produced by the Python tests from the real API path.
 */
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const frontendRoot = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "../../frontend",
);
const require = createRequire(path.join(frontendRoot, "package.json"));
const esbuild = require("esbuild");

const entry = `
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DecisionBriefing } from "@/components/dashboard/decision-briefing";
import { BriefingTab } from "@/components/dashboard/briefing-tab";
import { RevenueTab } from "@/components/dashboard/revenue-tab";
import { RiskTab } from "@/components/dashboard/risk-tab";
import { InsightInfoPanel } from "@/components/dashboard/insight-info-panel";
import { FlightInsightsPanel } from "@/components/dashboard/flight-insights";
import { ExceptionsInsightsTab } from "@/components/dashboard/flight-insight-tabs";

function paint(name, node) {
  try {
    return renderToStaticMarkup(node);
  } catch (error) {
    const message = error && error.stack ? error.stack : String(error);
    return "RENDER_ERROR: " + message;
  }
}

export function renderDashboard(dashboard) {
  const insights = dashboard.insights;
  const totalPassengers = dashboard.passengerSummary.totalPassengers;
  return {
    decisionBriefing: paint("decisionBriefing", createElement(DecisionBriefing, { dashboard })),
    briefingTab: paint("briefingTab", createElement(BriefingTab, { dashboard })),
    revenueTab: paint("revenueTab", createElement(RevenueTab, { dashboard })),
    riskTab: paint("riskTab", createElement(RiskTab, { dashboard })),
    infoPanel: paint("infoPanel", createElement(InsightInfoPanel, { activeKey: "classMismatch" })),
    flightInsights: paint("flightInsights", createElement(FlightInsightsPanel, { insights, totalPassengers })),
    exceptionsTab: paint(
      "exceptionsTab",
      createElement(ExceptionsInsightsTab, { insights, totalPassengers }),
    ),
  };
}
`;

const inputPath = process.argv[2];
const payload = JSON.parse(readFileSync(inputPath, "utf8"));
const dir = mkdtempSync(path.join(tmpdir(), "class-mismatch-render-"));
const outfile = path.join(dir, "render.cjs");

await esbuild.build({
  absWorkingDir: frontendRoot,
  stdin: {
    contents: entry,
    resolveDir: frontendRoot,
    sourcefile: "class-mismatch-render.tsx",
    loader: "tsx",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  outfile,
  jsx: "automatic",
  alias: { "@": frontendRoot },
  logLevel: "silent",
});

const rendered = require(outfile);
const screens = {};
for (const [name, dashboard] of Object.entries(payload)) {
  screens[name] = rendered.renderDashboard(dashboard);
}
process.stdout.write(JSON.stringify(screens));
rmSync(dir, { recursive: true, force: true });
