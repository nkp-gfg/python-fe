/**
 * The passenger-detail itinerary card must show third-party ground handling
 * in the same header row as the operating carrier.
 *
 * This loads the real passenger-detail-sheet.tsx (ItinerarySection) through
 * the TypeScript compiler that ships with the frontend, then renders it.
 * Leaf UI modules are pass-through elements so the card's own markup is what
 * the assertion reads. The test fails until a segment with
 * thirdPartyGroundHandled === true shows the text "Third-party ground handling"
 * in that header, next to the operating airline and flight, and a segment
 * with the flag false does not.
 *
 * Run from the repository root:
 *   cd frontend && npm install && node components/dashboard/passenger-detail-sheet.ground-handling.test.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { Module } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "../..");
const require = createRequire(path.join(frontendRoot, "package.json"));
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

function stubComponent(props) {
  return React.createElement(
    "span",
    { className: props?.className },
    props?.children,
  );
}

function componentModule() {
  return new Proxy(
    { __esModule: true, default: stubComponent },
    {
      get(target, prop) {
        if (prop in target) return target[prop];
        return stubComponent;
      },
    },
  );
}

function loadItinerarySection() {
  const sourcePath = path.join(here, "passenger-detail-sheet.tsx");
  const source = `${fs.readFileSync(sourcePath, "utf8")}\nexport { ItinerarySection };\n`;
  const output = ts.transpileModule(source, {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: sourcePath,
  }).outputText;

  const localRequire = (id) => {
    if (id === "react" || id === "react/jsx-runtime" || id === "react/jsx-dev-runtime") {
      return require(id);
    }
    if (id === "@/lib/utils") {
      return { cn: (...parts) => parts.filter(Boolean).join(" ") };
    }
    if (id === "@/lib/api") {
      return { fetchPassengerDetail: () => Promise.resolve(null) };
    }
    if (id === "@tanstack/react-query") {
      return { useQuery: () => ({ data: undefined, isLoading: false, error: null }) };
    }
    if (id.startsWith("@/") || id.startsWith("./") || id === "lucide-react") {
      return componentModule();
    }
    return require(id);
  };

  const compiled = { exports: {} };
  const runner = Module.wrap(output);
  const fn = require("node:vm").runInThisContext(runner, { filename: sourcePath });
  fn(compiled.exports, localRequire, compiled, sourcePath, here);
  if (typeof compiled.exports.ItinerarySection !== "function") {
    throw new Error("ItinerarySection was not loaded from passenger-detail-sheet.tsx");
  }
  return compiled.exports.ItinerarySection;
}

function segment(overrides) {
  return {
    segmentId: "1",
    airline: "EY",
    flight: "6418",
    operatingAirline: "VA",
    operatingFlight: "880",
    marketingAirline: "EY",
    marketingFlight: "6418",
    bookingClass: "L",
    marketingBookingClass: "L",
    operatingBookingClass: "N",
    origin: "SYD",
    destination: "MEL",
    departureDate: "2019-05-29",
    aircraftType: "C2C",
    cabin: "Y",
    seat: "",
    passengerType: "F",
    priority: "",
    bagCount: 0,
    totalBagWeight: "0",
    checkInNumber: "",
    editCodes: [],
    bagTags: [],
    vcrInfo: [],
    aeDetails: [],
    requiredInfo: [],
    thirdPartyGroundHandled: false,
    ...overrides,
  };
}

function headerRow(html) {
  const match = html.match(/<div class="flex items-center gap-2">([\s\S]*?)<\/div>/);
  if (!match) {
    throw new Error(`itinerary header row not found in:\n${html}`);
  }
  return match[1];
}

function assertHeader(html, { operatingAirline, operatingFlight, showsGroundHandling }) {
  const header = headerRow(html);
  const operating = new RegExp(`${operatingAirline}\\s*${operatingFlight}`);
  if (!operating.test(header)) {
    throw new Error(
      `header row does not show operating carrier ${operatingAirline}${operatingFlight}:\n${header}`,
    );
  }
  const phrase = "Third-party ground handling";
  const shown = header.includes(phrase);
  if (showsGroundHandling && !shown) {
    throw new Error(
      `header row does not show "${phrase}" next to ${operatingAirline}${operatingFlight}:\n${header}`,
    );
  }
  if (!showsGroundHandling && shown) {
    throw new Error(
      `header row shows "${phrase}" for a segment that is not third-party ground handled:\n${header}`,
    );
  }
}

const ItinerarySection = loadItinerarySection();

const thirdParty = renderToStaticMarkup(
  React.createElement(ItinerarySection, {
    segments: [segment({ thirdPartyGroundHandled: true })],
  }),
);
assertHeader(thirdParty, {
  operatingAirline: "VA",
  operatingFlight: "880",
  showsGroundHandling: true,
});
if (!thirdParty.includes("EY") || !thirdParty.includes("6418")) {
  throw new Error("marketing carrier label EY6418 is missing from the itinerary card");
}

const ordinary = renderToStaticMarkup(
  React.createElement(ItinerarySection, {
    segments: [segment({
      segmentId: "2",
      airline: "EY",
      flight: "1",
      operatingAirline: "EY",
      operatingFlight: "1",
      marketingAirline: "EY",
      marketingFlight: "1",
      origin: "AUH",
      destination: "FRA",
      thirdPartyGroundHandled: false,
    })],
  }),
);
assertHeader(ordinary, {
  operatingAirline: "EY",
  operatingFlight: "1",
  showsGroundHandling: false,
});

console.log("PASS: passenger-detail itinerary card shows third-party ground handling");
