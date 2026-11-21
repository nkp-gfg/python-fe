/**
 * Render the real PaxMatrix component and print the passenger-table cells.
 *
 * The dashboard and compare screens both mount this component with the flight
 * tree returned by the API. This script is the display step of the staff-gender
 * tests: it does not reimplement the column math.
 */
import { readFileSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PaxMatrix } from "../components/dashboard/pax-matrix";

type CellValue = number | string;

interface MatrixRow {
  total: CellValue;
  pax: CellValue;
  staff: CellValue;
  m: CellValue;
  f: CellValue;
  chd: CellValue;
  inf: CellValue;
}

function cellText(html: string): string {
  return html.replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
}

function asValue(text: string): CellValue {
  if (text === "—" || text === "-") return text;
  const value = Number(text);
  if (!Number.isFinite(value)) {
    throw new Error(`Passenger matrix cell is not a number: ${JSON.stringify(text)}`);
  }
  return value;
}

function rowFromCells(cells: string[]): MatrixRow {
  if (cells.length < 8) {
    throw new Error(`Passenger matrix row has ${cells.length} cells: ${cells.join(" | ")}`);
  }
  return {
    total: asValue(cells[1]),
    pax: asValue(cells[2]),
    staff: asValue(cells[3]),
    m: asValue(cells[4]),
    f: asValue(cells[5]),
    chd: asValue(cells[6]),
    inf: asValue(cells[7]),
  };
}

function parsePassengerTable(html: string): Record<string, MatrixRow> {
  const table = html.match(/<table[\s\S]*?<\/table>/);
  if (!table) throw new Error("PaxMatrix did not render a passenger table");
  const rows = [...table[0].matchAll(/<tr[\s\S]*?<\/tr>/g)].map((match) =>
    [...match[0].matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/g)].map((cell) => cellText(cell[1])),
  );
  const parsed: Record<string, MatrixRow> = {};
  for (const cells of rows) {
    const label = cells[0];
    if (label === "Economy" || label === "Business" || label === "Total") {
      parsed[label.toLowerCase()] = rowFromCells(cells);
    }
  }
  for (const label of ["economy", "business", "total"]) {
    if (!parsed[label]) throw new Error(`PaxMatrix passenger table is missing the ${label} row`);
  }
  return parsed;
}

const treePath = process.argv[2];
if (!treePath) {
  throw new Error("Usage: render-pax-matrix.ts <tree.json>");
}

const tree = JSON.parse(readFileSync(treePath, "utf8"));
const html = renderToStaticMarkup(React.createElement(PaxMatrix, { tree }));
process.stdout.write(JSON.stringify(parsePassengerTable(html)));
