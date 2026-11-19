/**
 * Render the Revenue tab and print the loyalty headcount it shows.
 *
 * Input: a JSON file whose value is an array of flight dashboard payloads.
 * Output: one line per payload, `COUNT (PCT%) width=PCT`.
 *
 * Run from the frontend directory so React resolves and `@/` follows tsconfig:
 *   npx --yes tsx@4.20.6 tests/render-revenue-loyalty.tsx dashboards.json
 */
import fs from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { RevenueTab } from "../components/dashboard/revenue-tab.tsx";

const payloadPath = process.argv[2];
if (!payloadPath) {
  throw new Error("usage: render-revenue-loyalty.tsx <dashboards.json>");
}

const dashboards = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
if (!Array.isArray(dashboards)) {
  throw new Error("dashboard file must be a JSON array");
}

for (const dashboard of dashboards) {
  const html = renderToStaticMarkup(
    React.createElement(RevenueTab, { dashboard }),
  );
  const marker = html.indexOf(">Loyalty<");
  if (marker < 0) {
    throw new Error("Revenue tab did not render a Loyalty row");
  }
  const window = html.slice(marker, marker + 600);
  const count = window.match(/>(\d+)\s+\((\d+)%\)</);
  const width = window.match(/width:(\d+)%/);
  if (!count || !width) {
    throw new Error(`Loyalty row has no headcount: ${window}`);
  }
  process.stdout.write(`${count[1]} (${count[2]}%) width=${width[1]}\n`);
}
