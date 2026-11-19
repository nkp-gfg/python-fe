/**
 * Renders the dashboard surfaces that show documentCompliance.DOCV.
 *
 * The count passed in is produced by the backend from GetPassengerList edit
 * codes. The label next to that count must say the documents were verified
 * and must not call the count a visa. DOCO-only and TWOV passengers are not
 * part of this count.
 *
 * Invoked by backend/tests/test_docv_presented_as_visa.py.
 */
import { readFileSync } from "node:fs";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { FlightInsightsPanel } from "../components/dashboard/flight-insights";
import { CommercialInsightsTab, ReadinessInsightsTab } from "../components/dashboard/flight-insight-tabs";
import { InsightInfoPanel } from "../components/dashboard/insight-info-panel";
import type { FlightInsights } from "../lib/types";

type Payload = {
  insights: FlightInsights;
  totalPassengers: number;
  docvCount: number;
  docoCount: number;
  twovCount: number;
};

function visibleText(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function fail(surface: string, detail: string): never {
  throw new Error(`${surface}: ${detail}`);
}

function docvGloss(text: string): string {
  const match = text.match(/DOCV\s*(?:=|\()?\s*([^.]{0,90})/i);
  if (!match) {
    fail("DOCV copy", `no DOCV label in: ${text.slice(0, 500)}`);
  }
  return match[1];
}

function assertVerifiedGloss(text: string, surface: string) {
  const gloss = docvGloss(text);
  if (!/verified/i.test(gloss)) {
    fail(surface, `DOCV is not labeled as documents verified. Gloss: ${gloss}`);
  }
  if (/visa/i.test(gloss)) {
    fail(surface, `DOCV is labeled as visa coverage. Gloss: ${gloss}`);
  }
}

function assertCountIsVerifiedDocuments(text: string, payload: Payload, surface: string) {
  const match = text.match(/DOCV([^0-9]{0,80})(\d+)/i);
  if (!match) {
    fail(surface, `DOCV count is not shown. Text: ${text.slice(0, 700)}`);
  }
  const gloss = match[1];
  const shown = Number(match[2]);
  if (shown !== payload.docvCount) {
    fail(
      surface,
      `shown count ${shown} is not the DOCV passenger count ${payload.docvCount} (DOCO passengers ${payload.docoCount}, TWOV passengers ${payload.twovCount}). Gloss: ${gloss}`,
    );
  }
  if (shown === payload.docoCount || shown === payload.twovCount) {
    fail(surface, `DOCV row collapsed onto DOCO or TWOV. Gloss: ${gloss}`);
  }
  if (!/verified/i.test(gloss)) {
    fail(surface, `count ${shown} is not labeled as documents verified. Gloss: ${JSON.stringify(gloss)}`);
  }
  if (/visa/i.test(gloss)) {
    fail(surface, `count ${shown} is labeled as visa coverage. Gloss: ${JSON.stringify(gloss)}`);
  }
}

function render(component: ReactElement): string {
  return visibleText(renderToStaticMarkup(component));
}

function check(errors: string[], fn: () => void) {
  try {
    fn();
  } catch (error) {
    errors.push(error instanceof Error ? error.message : String(error));
  }
}

function main() {
  const payload = JSON.parse(readFileSync(process.argv[2], "utf8")) as Payload;
  const insights = payload.insights;
  const total = payload.totalPassengers;
  const errors: string[] = [];

  const bars = render(
    createElement(FlightInsightsPanel, { insights, totalPassengers: total }),
  );
  check(errors, () => assertCountIsVerifiedDocuments(bars, payload, "FlightInsightsPanel document compliance bar"));

  const readiness = render(
    createElement(ReadinessInsightsTab, { insights, totalPassengers: total }),
  );
  check(errors, () => assertCountIsVerifiedDocuments(readiness, payload, "ReadinessInsightsTab document compliance row"));

  const commercial = render(
    createElement(CommercialInsightsTab, { insights, totalPassengers: total }),
  );
  check(errors, () => {
    if (/DOCV[^.]{0,40}visa/i.test(commercial)) {
      fail("CommercialInsightsTab", "commercial tab labels DOCV as visa");
    }
  });

  const complianceInfo = render(
    createElement(InsightInfoPanel, { activeKey: "documentCompliance" }),
  );
  check(errors, () => assertVerifiedGloss(complianceInfo, "InsightInfoPanel document compliance"));

  const editCodeInfo = render(
    createElement(InsightInfoPanel, { activeKey: "editCodes" }),
  );
  check(errors, () => assertVerifiedGloss(editCodeInfo, "InsightInfoPanel edit codes"));

  if (errors.length > 0) {
    throw new Error(errors.join("\n"));
  }
  console.log("  PASS: rendered DOCV count is labeled documents verified");
}

main();
