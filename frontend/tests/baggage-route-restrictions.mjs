/**
 * The passenger detail baggage table must show the restriction strings the
 * live detail payload carries. Values are tokens that do not appear in the
 * component source, so the table has to render the route fields:
 * petRestriction, shortCheckinReason, baggageRestrictionLimit, homePrintedBagTag.
 *
 * Run from the repository root:
 *   node frontend/tests/baggage-route-restrictions.mjs
 */
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "..");
const sheetPath = join(frontend, "components/dashboard/passenger-detail-sheet.tsx");
const require = createRequire("/tmp/esbuild-runner/package.json");
const esbuild = require("esbuild");

const PET = "PET-RESTRICTED-TOKEN-4417";
const SHORT_CHECK = "SHORT-CHECK-TOKEN-4417";
const LIMIT = "BAG-LIMIT-TOKEN-4417";
const TAG = "HOME-TAG-TOKEN-4417 true STATION-BAN-TOKEN-4417";
const PET_2 = "PET-SECOND-TOKEN-4417";
const SHORT_2 = "SHORT-SECOND-TOKEN-4417";
const LIMIT_2 = "BAG-LIMIT-SECOND-TOKEN-4417";
const TAG_2 = "HOME-TAG-SECOND-TOKEN-4417";

const passenger = {
  baggageRoutes: [
    {
      segmentId: "1",
      airline: "LA",
      flight: "129",
      origin: "ANF",
      destination: "SCL",
      departureDate: "2016-07-04",
      bookingClass: "Y",
      segmentStatus: "HK",
      petRestriction: PET,
      shortCheckinReason: SHORT_CHECK,
      baggageRestrictionLimit: LIMIT,
      homePrintedBagTag: TAG,
    },
    {
      segmentId: "9",
      airline: "LA",
      flight: "999",
      origin: "LIM",
      destination: "GRU",
      departureDate: "2016-07-06",
      bookingClass: "J",
      segmentStatus: "HK",
      petRestriction: PET_2,
      shortCheckinReason: SHORT_2,
      baggageRestrictionLimit: LIMIT_2,
      homePrintedBagTag: TAG_2,
    },
  ],
};

function visibleText(html) {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

const source = readFileSync(sheetPath, "utf8").replace(
  "function BaggageSection",
  "export function BaggageSection",
);
const dashboard = join(frontend, "components/dashboard");

const dir = mkdtempSync(join(here, ".baggage-ui-"));
const outfile = join(dir, "bundle.mjs");
await esbuild.build({
  stdin: {
    contents: `import { renderToStaticMarkup } from "react-dom/server";
import { BaggageSection } from "./passenger-detail-sheet";
export function renderBaggage(passenger) {
  return renderToStaticMarkup(<BaggageSection passenger={passenger} />);
}
`,
    resolveDir: dashboard,
    sourcefile: "baggage-entry.tsx",
    loader: "tsx",
  },
  bundle: true,
  format: "esm",
  platform: "node",
  packages: "external",
  jsx: "automatic",
  outfile,
  absWorkingDir: frontend,
  nodePaths: [join(frontend, "node_modules")],
  alias: { "@": frontend },
  plugins: [
    {
      name: "export-baggage-section",
      setup(build) {
        build.onLoad({ filter: /passenger-detail-sheet\.tsx$/ }, () => ({
          contents: source,
          loader: "tsx",
          resolveDir: dashboard,
        }));
      },
    },
  ],
});

const { renderBaggage } = await import(pathToFileURL(outfile).href);
let html = "";
try {
  html = renderBaggage(passenger);
} finally {
  rmSync(dir, { recursive: true, force: true });
}

const text = visibleText(html);
const required = [PET, SHORT_CHECK, LIMIT, TAG, PET_2, SHORT_2, LIMIT_2, TAG_2, "LA129", "LA999"];
const missing = required.filter((part) => !text.includes(part));
if (missing.length) {
  console.error("Baggage table hid restriction text:", missing.join(", "));
  console.error(text.slice(0, 2000));
  process.exit(1);
}
console.log("baggage table shows restriction strings");
