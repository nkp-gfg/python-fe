/**
 * Render the dashboard views that decide standby clearance from no-show data.
 * Reads {scenario: FlightDashboard} JSON on stdin and writes rendered HTML.
 * This file is a test harness only. It does not decide pass or fail.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { BriefingTab } from "@/components/dashboard/briefing-tab";
import { DecisionBriefing } from "@/components/dashboard/decision-briefing";
import { RevenueTab } from "@/components/dashboard/revenue-tab";
import { RiskTab } from "@/components/dashboard/risk-tab";
import { StatePanels } from "@/components/dashboard/state-panels";
import type { FlightDashboard } from "@/lib/types";

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function renderOne(dashboard: FlightDashboard) {
  const phase = dashboard.flightPhase!;
  return {
    briefing: renderToStaticMarkup(<BriefingTab dashboard={dashboard} />),
    decision: renderToStaticMarkup(<DecisionBriefing dashboard={dashboard} />),
    revenue: renderToStaticMarkup(<RevenueTab dashboard={dashboard} />),
    risk: renderToStaticMarkup(<RiskTab dashboard={dashboard} />),
    operations: renderToStaticMarkup(
      <StatePanels
        stateSummary={dashboard.stateSummary}
        phase={phase.phase}
        focusCard={phase.focusCard}
        onInfoClick={() => {}}
      />,
    ),
  };
}

async function main() {
  const scenarios = JSON.parse(await readStdin()) as Record<string, FlightDashboard>;
  const rendered: Record<string, ReturnType<typeof renderOne> | { error: string }> = {};
  for (const [name, dashboard] of Object.entries(scenarios)) {
    try {
      rendered[name] = renderOne(dashboard);
    } catch (err) {
      rendered[name] = { error: err instanceof Error ? err.stack ?? err.message : String(err) };
    }
  }
  process.stdout.write(JSON.stringify(rendered));
}

void main();
