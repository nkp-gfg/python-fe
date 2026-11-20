/**
 * Renders the readiness, risk, and briefing views with dashboard payloads
 * produced by the passenger-list pipeline. Fails while those views treat
 * baggage data-presence as checked-bag coverage.
 */
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";

import { BriefingTab } from "../components/dashboard/briefing-tab";
import { DecisionBriefing } from "../components/dashboard/decision-briefing";
import { ReadinessInsightsTab } from "../components/dashboard/flight-insight-tabs";
import { RiskTab } from "../components/dashboard/risk-tab";

type Scenario = {
  name: string;
  dashboard: {
    insights: NonNullable<unknown>;
    passengerSummary: { totalPassengers: number };
  };
  checkedBagPct: number;
  dataPresencePct: number;
  maxOverall: number | null;
};

const failures: string[] = [];

function fail(name: string, message: string) {
  failures.push(`${name}: ${message}`);
}

function percentAfter(html: string, label: string): number | null {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = html.match(new RegExp(`${escaped}[\\s\\S]{0,400}?(\\d+(?:\\.\\d+)?)%`));
  return match ? Number(match[1]) : null;
}

function samePct(actual: number | null, expected: number): boolean {
  return actual !== null && Math.abs(actual - expected) < 0.05;
}

function baggageTracking(html: string): { present: boolean; pct: number | null; passed: boolean } {
  const labelAt = html.indexOf("Baggage tracking");
  if (labelAt < 0) {
    return { present: false, pct: null, passed: false };
  }
  const before = html.slice(Math.max(0, labelAt - 500), labelAt);
  const after = html.slice(labelAt, labelAt + 300);
  const pctMatch = after.match(/(\d+(?:\.\d+)?)%/);
  const passed = /text-emerald-500/.test(before);
  return { present: true, pct: pctMatch ? Number(pctMatch[1]) : null, passed };
}

function readinessNumbers(html: string): number[] {
  return [...html.matchAll(/Departure readiness (\d+(?:\.\d+)?)%/g)].map((match) => Number(match[1]));
}

function assertScenario(scenario: Scenario) {
  const { name, dashboard, checkedBagPct, dataPresencePct, maxOverall } = scenario;
  const insights = dashboard.insights as never;
  const total = dashboard.passengerSummary.totalPassengers;

  const readiness = renderToStaticMarkup(
    <ReadinessInsightsTab insights={insights} totalPassengers={total} />,
  );
  const bagCoverage = percentAfter(readiness, "Bag Coverage");
  if (!samePct(bagCoverage, checkedBagPct)) {
    fail(name, `Bag Coverage is ${bagCoverage}, expected checked-bag share ${checkedBagPct}`);
  }

  const completenessAt = readiness.search(/Data completeness/i);
  if (completenessAt < 0) {
    fail(name, "readiness baggage panel does not label the data-presence percentage as Data completeness");
  } else {
    const completeness = percentAfter(readiness.slice(completenessAt), "Data completeness");
    if (!samePct(completeness, dataPresencePct)) {
      fail(name, `Data completeness is ${completeness}, expected presence ${dataPresencePct}`);
    }
  }

  const risk = renderToStaticMarkup(<RiskTab dashboard={dashboard as never} />);
  const tracking = baggageTracking(risk);
  const shouldPass = checkedBagPct >= 80;
  if (!tracking.present) {
    fail(name, "Safety checklist no longer has Baggage tracking");
  } else {
    if (!samePct(tracking.pct, Math.round(checkedBagPct))) {
      fail(name, `Baggage tracking shows ${tracking.pct}%, expected ${Math.round(checkedBagPct)}%`);
    }
    if (tracking.passed !== shouldPass) {
      fail(name, `Baggage tracking passed=${tracking.passed}, expected ${shouldPass}`);
    }
  }

  const briefing = renderToStaticMarkup(<BriefingTab dashboard={dashboard as never} />);
  const decision = renderToStaticMarkup(<DecisionBriefing dashboard={dashboard as never} />);
  for (const [view, html] of [["briefing", briefing], ["decision briefing", decision]] as const) {
    const scores = readinessNumbers(html);
    if (scores.length === 0) {
      fail(name, `${view} did not render a departure readiness score`);
      continue;
    }
    for (const score of scores) {
      if (maxOverall !== null && score >= maxOverall) {
        fail(name, `${view} departure readiness ${score} is not below ${maxOverall}`);
      }
    }
    const bags = percentAfter(html, ">Bags<") ?? percentAfter(html, "Bags");
    if (bags !== null && !samePct(bags, checkedBagPct) && Math.abs((bags ?? 0) - dataPresencePct) < 0.05 && checkedBagPct !== dataPresencePct) {
      fail(name, `${view} Bags bar shows data presence ${bags}% instead of checked-bag share ${checkedBagPct}`);
    }
  }
}

const raw = readFileSync(0, "utf8");
const scenarios = JSON.parse(raw) as Scenario[];
for (const scenario of scenarios) {
  try {
    assertScenario(scenario);
  } catch (error) {
    fail(scenario.name, error instanceof Error ? error.stack ?? error.message : String(error));
  }
}

if (failures.length > 0) {
  for (const failure of failures) {
    console.error(failure);
  }
  process.exit(1);
}

console.log(`ok ${scenarios.map((scenario) => scenario.name).join(", ")}`);
