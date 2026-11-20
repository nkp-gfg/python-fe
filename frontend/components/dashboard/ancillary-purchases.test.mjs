/**
 * The ancillary purchases table must show the product subcode and description
 * beside the group. Two BG rows that share flight, status, quantity, price,
 * and EMD are distinguishable only by those fields.
 *
 * Run from frontend/ after npm install:
 *   node components/dashboard/ancillary-purchases.test.mjs
 *
 * Fails while AncillarySection renders only the group code.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dashboardDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(dashboardDir, "../..");
const sheetPath = path.join(dashboardDir, "passenger-detail-sheet.tsx");
const typesPath = path.join(frontendDir, "lib/types.ts");

const products = [
  {
    groupCode: "BG",
    subCode: "123",
    description: "UPTO50LB 23KG BAGGAGE",
    statusCode: "HD",
    usedEMD: "false",
    quantity: 0,
    price: "",
    currency: "",
  },
  {
    groupCode: "BG",
    subCode: "0C3",
    description: "PREPAID BAG 23KG",
    statusCode: "HD",
    usedEMD: "false",
    quantity: 0,
    price: "",
    currency: "",
  },
];

function fail(message) {
  const error = new Error(message);
  error.alreadyReported = true;
  throw error;
}

function assertAeDetailType() {
  const source = fs.readFileSync(typesPath, "utf8");
  const match = source.match(/export interface AeDetail \{[\s\S]*?\n\}/);
  if (!match) fail("AeDetail interface not found in frontend/lib/types.ts");
  const body = match[0];
  for (const field of ["subCode: string", "description: string"]) {
    if (!body.includes(field)) {
      fail(`AeDetail is missing \`${field}\`.\n${body}`);
    }
  }
}

function rowText(rowHtml) {
  return rowHtml
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function renderPurchases() {
  const source = fs.readFileSync(sheetPath, "utf8");
  let rewritten = source;
  if (!source.includes("export function AncillarySection")) {
    if (!source.includes("function AncillarySection")) {
      fail("AncillarySection is not defined in passenger-detail-sheet.tsx");
    }
    rewritten = source.replace(
      "function AncillarySection",
      "export function AncillarySection",
    );
  }

  const stamp = `${process.pid}-${Date.now()}`;
  const underTest = path.join(dashboardDir, `_ancillary_under_test_${stamp}.tsx`);
  const entry = path.join(dashboardDir, `_render_ancillary_${stamp}.tsx`);
  const outfile = path.join(os.tmpdir(), `render-ancillary-${stamp}.cjs`);
  fs.writeFileSync(underTest, rewritten);
  fs.writeFileSync(
    entry,
    `import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AncillarySection } from "./${path.basename(underTest, ".tsx")}";

const segments = [{
  segmentId: "1",
  airline: "AZ",
  flight: "317",
  operatingAirline: "",
  operatingFlight: "",
  marketingAirline: "",
  marketingFlight: "",
  bookingClass: "Y",
  marketingBookingClass: "",
  operatingBookingClass: "",
  origin: "CDG",
  destination: "FCO",
  departureDate: "2016-03-17",
  aircraftType: "",
  cabin: "Y",
  seat: "",
  passengerType: "F",
  priority: "",
  bagCount: 1,
  totalBagWeight: "",
  checkInNumber: "1",
  editCodes: [],
  bagTags: [],
  vcrInfo: [],
  aeDetails: ${JSON.stringify(products.map((item, index) => ({ itemId: String(index + 5), ...item })))},
  requiredInfo: [],
}];

const html = renderToStaticMarkup(React.createElement(AncillarySection, { segments }));
if (!html.includes("Ancillary Purchases")) {
  console.error(html);
  process.exit(2);
}
const body = html.split("<tbody")[1].split("</tbody>")[0];
const rows = [...body.matchAll(/<tr[\\s\\S]*?<\\/tr>/g)].map((match) => match[0]);
console.log(JSON.stringify(rows.map(${rowText.toString()})));
`,
  );

  try {
    const bundled = spawnSync(
      "npx",
      [
        "--yes",
        "esbuild",
        entry,
        "--bundle",
        "--platform=node",
        "--format=cjs",
        "--jsx=automatic",
        `--outfile=${outfile}`,
      ],
      { cwd: frontendDir, encoding: "utf8" },
    );
    if (bundled.status !== 0) {
      fail(`esbuild failed\n${bundled.stdout}\n${bundled.stderr}`);
    }
    const rendered = spawnSync(process.execPath, [outfile], { encoding: "utf8" });
    if (rendered.status !== 0) {
      fail(`render failed\n${rendered.stdout}\n${rendered.stderr}`);
    }
    return JSON.parse(rendered.stdout);
  } finally {
    fs.rmSync(underTest, { force: true });
    fs.rmSync(entry, { force: true });
    fs.rmSync(outfile, { force: true });
  }
}

const failures = [];
try {
  assertAeDetailType();
} catch (err) {
  failures.push(err && err.message ? err.message : String(err));
}
let rows;
try {
  rows = renderPurchases();
} catch (err) {
  failures.push(err && err.message ? err.message : String(err));
}
if (!rows) {
  failures.push("purchases table did not render");
} else if (rows.length !== products.length) {
  failures.push(`expected ${products.length} purchase rows, got ${rows.length}: ${JSON.stringify(rows)}`);
} else {
  for (const product of products) {
    const row = rows.find((text) => text.includes(product.subCode));
    if (!row) {
      failures.push(
        `purchases table has no visible subcode ${product.subCode}. rows=${JSON.stringify(rows)}`,
      );
      continue;
    }
    if (!row.includes(product.groupCode)) {
      failures.push(`subcode ${product.subCode} is not on the same row as group ${product.groupCode}: ${row}`);
    }
    if (product.description && !row.includes(product.description)) {
      failures.push(`description for ${product.subCode} is not visible: ${row}`);
    }
  }
  if (new Set(rows).size !== rows.length) {
    failures.push(`purchase rows are identical, so the two BG products cannot be told apart: ${JSON.stringify(rows)}`);
  }
}

if (failures.length) {
  console.error(failures.join("\n\n"));
  process.exit(1);
}
console.log("ancillary purchases table shows subcode and description");
