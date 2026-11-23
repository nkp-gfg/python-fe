/**
 * Spec for the phase-journey Transition Details card.
 *
 * The card must keep every real status change (booked, checked-in, boarded)
 * visible after additions and removals are split out. Unchanged passengers
 * must not crowd a smaller reversal, such as boarded back to booked, off the
 * card. Rows that are still omitted must be disclosed with an explicit count.
 *
 * This fails while the card keeps only the first six remaining movements and
 * does not say the list was cut off.
 */
import fs from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PhaseTransitionChart } from "@/components/dashboard/phase-transition-chart";
import type { PhaseJourneyResponse, PhaseTransition, TransitionFlow } from "@/lib/types";

const STATE_LABELS: Record<string, string> = {
  booked: "Booked",
  checkedIn: "Checked-In",
  boarded: "Boarded",
};

const LABEL_TO_STATE: Record<string, string> = {
  Booked: "booked",
  "Checked-In": "checkedIn",
  Boarded: "boarded",
};

function visibleText(html: string): string {
  const labelled = [...html.matchAll(/\b(?:aria-label|title)="([^"]*)"/g)].map((match) => match[1]);
  const text = html.replace(/<[^>]+>/g, " ");
  return `${text} ${labelled.join(" ")}`.replace(/\s+/g, " ").trim();
}

function movementRows(text: string): Array<{ fromState: string; toState: string; count: number }> {
  const rows: Array<{ fromState: string; toState: string; count: number }> = [];
  const pattern = /(Booked|Checked-In|Boarded)\s*→\s*(Booked|Checked-In|Boarded)\s*(\d+)/g;
  for (const match of text.matchAll(pattern)) {
    rows.push({
      fromState: LABEL_TO_STATE[match[1]],
      toState: LABEL_TO_STATE[match[2]],
      count: Number(match[3]),
    });
  }
  return rows;
}

function disclosedOmittedCount(text: string, remaining: number): number | null {
  const disclosed = new Set<number>();
  for (const match of text.matchAll(/(\d+)\s+(?:more|additional|not shown|omitted|hidden)\b/gi)) {
    disclosed.add(Number(match[1]));
  }
  for (const match of text.matchAll(/\+\s*(\d+)\s+(?:more|rows)\b/gi)) {
    disclosed.add(Number(match[1]));
  }
  for (const match of text.matchAll(/showing\s+(\d+)\s+of\s+(\d+)/gi)) {
    disclosed.add(Number(match[2]) - Number(match[1]));
  }
  if (disclosed.has(remaining)) return remaining;
  return disclosed.size > 0 ? [...disclosed][0] : null;
}

function remainingMovements(flows: TransitionFlow[]): TransitionFlow[] {
  return flows.filter((flow) => flow.fromState !== "new" && flow.toState !== "removed" && flow.count > 0);
}

function assertTransitionCard(transition: PhaseTransition, detailsHtml: string): void {
  const text = visibleText(detailsHtml);
  const remaining = remainingMovements(transition.flows);
  const changes = remaining.filter((flow) => flow.fromState !== flow.toState);
  const shown = movementRows(text);
  const missing = changes.filter(
    (flow) =>
      !shown.some(
        (row) =>
          row.fromState === flow.fromState &&
          row.toState === flow.toState &&
          row.count === flow.count,
      ),
  );

  const shownKeys = new Set(shown.map((row) => `${row.fromState}->${row.toState}:${row.count}`));
  const omitted = remaining.filter((flow) => !shownKeys.has(`${flow.fromState}->${flow.toState}:${flow.count}`));
  const notice = disclosedOmittedCount(text, omitted.length);

  const problems: string[] = [];
  if (missing.length > 0) {
    const rendered = shown
      .map((row) => `${STATE_LABELS[row.fromState]} → ${STATE_LABELS[row.toState]} ${row.count}`)
      .join(", ");
    const absent = missing
      .map((flow) => `${STATE_LABELS[flow.fromState]} → ${STATE_LABELS[flow.toState]} ${flow.count}`)
      .join(", ");
    problems.push(
      `status changes missing from ${transition.fromLabel} → ${transition.toLabel}: ${absent}. ` +
        `Rendered rows: ${rendered || "(none)"}`,
    );
  }
  if (omitted.length > 0 && notice !== omitted.length) {
    problems.push(
      `${omitted.length} status movement(s) were not printed and the card did not disclose that count ` +
        `(notice=${notice === null ? "absent" : notice}).`,
    );
  }
  if (transition.addedCount > 0 && !new RegExp(`\\+\\s*Added\\s+${transition.addedCount}\\b`).test(text)) {
    problems.push(`added passengers (${transition.addedCount}) were not shown on the card`);
  }
  if (transition.removedCount > 0 && !new RegExp(`-\\s*Removed\\s+${transition.removedCount}\\b`).test(text)) {
    problems.push(`removed passengers (${transition.removedCount}) were not shown on the card`);
  }
  if (problems.length > 0) {
    throw new Error(problems.join("\n"));
  }
}

export function assertPhaseJourneyCards(journey: PhaseJourneyResponse): void {
  if (journey.phases.length < 2) {
    throw new Error("phase journey did not produce two phases, so the chart is not mounted");
  }
  if (journey.transitions.length === 0) {
    throw new Error("phase journey returned no transitions");
  }

  const html = renderToStaticMarkup(
    React.createElement(PhaseTransitionChart, {
      phases: journey.phases,
      transitions: journey.transitions,
    }),
  );
  const detailsAt = html.indexOf("Transition Details");
  if (detailsAt < 0) {
    throw new Error("Transition Details card was not rendered");
  }
  const details = html.slice(detailsAt);
  for (const transition of journey.transitions) {
    assertTransitionCard(transition, details);
  }
}

const payloadPath = process.argv[2];
if (payloadPath) {
  const journey = JSON.parse(fs.readFileSync(payloadPath, "utf8")) as PhaseJourneyResponse;
  try {
    assertPhaseJourneyCards(journey);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`${message}\n`);
    process.exit(1);
  }
  process.stdout.write("phase journey transition details keep status changes visible\n");
}
