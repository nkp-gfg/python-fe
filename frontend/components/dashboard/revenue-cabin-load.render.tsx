/**
 * Renders RevenueTab for cabin-load tests.
 * Reads a JSON object of { caseName: FlightDashboard } and prints
 * { caseName: { businessLoad, yieldMix, upgradeNote, jCabinLine } }.
 */
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { RevenueTab } from "./revenue-tab";
import type { FlightDashboard } from "@/lib/types";

function visibleText(html: string): string {
  return html
    .replace(/<[^>]+>/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/\n+/g, "\n")
    .trim();
}

function fieldAfter(text: string, label: string): string {
  const needle = `\n${label}\n`;
  const at = text.indexOf(needle);
  if (at < 0) {
    throw new Error(`Label ${JSON.stringify(label)} not found in Revenue tab text`);
  }
  const rest = text.slice(at + needle.length);
  const line = rest.split("\n", 1)[0]?.trim() ?? "";
  if (!line) {
    throw new Error(`No value under ${JSON.stringify(label)}`);
  }
  return line;
}

function renderCase(dashboard: FlightDashboard) {
  const html = renderToStaticMarkup(<RevenueTab dashboard={dashboard} />);
  const text = visibleText(html);
  const businessLoadLine = fieldAfter(text, "Business Load");
  const loadMatch = businessLoadLine.match(/^(\d+)%$/);
  if (!loadMatch) {
    throw new Error(`Business Load is ${JSON.stringify(businessLoadLine)}, expected a whole-number percent`);
  }
  const upgradeAt = text.indexOf("\nUpgrade Potential\n");
  if (upgradeAt < 0) {
    throw new Error("Upgrade Potential card is missing");
  }
  const upgradeCard = text.slice(upgradeAt);
  const note = upgradeCard
    .split("\n")
    .map((line) => line.trim())
    .find(
      (line) =>
        line.startsWith("Business cabin at ") ||
        line === "No class mismatches detected" ||
        line.includes("downgraded"),
    );
  if (!note) {
    throw new Error("Upgrade Potential card has no load recommendation");
  }
  return {
    businessLoad: Number(loadMatch[1]),
    yieldMix: fieldAfter(text, "Yield Mix"),
    upgradeNote: note,
    jCabinLine: fieldAfter(text, "J Cabin"),
  };
}

const inputPath = process.argv[2];
if (!inputPath) {
  throw new Error("Usage: tsx revenue-cabin-load.render.tsx <dashboards.json>");
}

const cases = JSON.parse(readFileSync(inputPath, "utf8")) as Record<string, FlightDashboard>;
const rendered: Record<string, ReturnType<typeof renderCase>> = {};
for (const [name, dashboard] of Object.entries(cases)) {
  try {
    rendered[name] = renderCase(dashboard);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${name}: ${message}`);
  }
}

process.stdout.write(JSON.stringify(rendered));
