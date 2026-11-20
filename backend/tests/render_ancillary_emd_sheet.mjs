/**
 * Render PassengerDetailSheet against one converted GetPassengerData document
 * and check the ancillary table. JSON on stdin:
 * { detail, flightNumber, origin, date, pnr, inUse, document }
 * document is the EMD ticket number, or null when the response omits it.
 */
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");
const frontend = path.join(repo, "frontend");
const require = createRequire(path.join(frontend, "package.json"));

function missing(pkg) {
  console.error(
    `Missing ${pkg}. From frontend/ run: npm ci && npm install --no-save jsdom esbuild`,
  );
  process.exit(2);
}

let esbuild;
let JSDOM;
try {
  esbuild = require("esbuild");
} catch {
  missing("esbuild");
}
try {
  ({ JSDOM } = require("jsdom"));
} catch {
  missing("jsdom");
}

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://127.0.0.1:3000/",
  pretendToBeVisual: true,
});
const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
Object.defineProperty(globalThis, "navigator", {
  value: window.navigator,
  configurable: true,
});
for (const key of Object.getOwnPropertyNames(window)) {
  if (["window", "navigator", "document", "self", "top"].includes(key)) continue;
  if (Object.prototype.hasOwnProperty.call(globalThis, key)) continue;
  try {
    globalThis[key] = window[key];
  } catch {
    /* host property */
  }
}
for (const key of [
  "Event",
  "CustomEvent",
  "FocusEvent",
  "MouseEvent",
  "PointerEvent",
  "KeyboardEvent",
  "InputEvent",
  "UIEvent",
]) {
  Object.defineProperty(globalThis, key, {
    value: window[key],
    configurable: true,
    writable: true,
  });
}
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.matchMedia = () => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
  addListener() {},
  removeListener() {},
  dispatchEvent() {
    return false;
  },
});
for (const name of ["hasPointerCapture", "setPointerCapture", "releasePointerCapture", "scrollIntoView"]) {
  if (!window.Element.prototype[name]) {
    window.Element.prototype[name] = () => (name === "hasPointerCapture" ? false : undefined);
  }
}
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

globalThis.fetch = async (url) => {
  const href = String(url);
  const body = href.includes("/detail")
    ? input.detail
    : { events: [] };
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

const harnessPath = path.join(os.tmpdir(), `emd-harness-${process.pid}.tsx`);
const outfile = path.join(frontend, "node_modules", `.emd-harness-${process.pid}.mjs`);
fs.writeFileSync(
  harnessPath,
  `import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PassengerDetailSheet } from ${JSON.stringify(path.join(frontend, "components/dashboard/passenger-detail-sheet.tsx"))};

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

export function Harness(props) {
  return (
    <QueryClientProvider client={queryClient}>
      <PassengerDetailSheet
        open
        onOpenChange={() => {}}
        flightNumber={props.flightNumber}
        origin={props.origin}
        date={props.date}
        pnr={props.pnr}
      />
    </QueryClientProvider>
  );
}
`,
);

function norm(value) {
  return String(value ?? "")
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function tableText(table) {
  const headers = [...table.querySelectorAll("thead th")].map((th) =>
    th.textContent.replace(/\s+/g, " ").trim(),
  );
  const row = table.querySelector("tbody tr");
  const cells = row
    ? [...row.querySelectorAll("td")].map((td) => td.textContent.replace(/\s+/g, " ").trim())
    : [];
  return { headers, cells };
}

const PLACEHOLDERS = new Set(["", "—", "–", "-", "n/a", "none", "not issued"]);

try {
  await esbuild.build({
    entryPoints: [harnessPath],
    bundle: true,
    format: "esm",
    platform: "node",
    packages: "external",
    jsx: "automatic",
    outfile,
    alias: { "@": frontend },
  });

  const { Harness } = await import(pathToFileURL(outfile).href);
  const React = require("react");
  const { createRoot } = require("react-dom/client");
  const { act } = require("react");

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      React.createElement(Harness, {
        flightNumber: input.flightNumber,
        origin: input.origin,
        date: input.date,
        pnr: input.pnr,
      }),
    );
  });

  const bodyText = () => document.body.textContent.replace(/\s+/g, " ");
  for (let i = 0; i < 50 && !bodyText().includes("Extras") && !bodyText().includes("Failed to load"); i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
  }
  if (bodyText().includes("Failed to load passenger details")) {
    throw new Error("detail request failed: " + bodyText().slice(0, 400));
  }

  const extras = [...document.querySelectorAll("button")].find((button) =>
    norm(button.textContent) === "extras",
  );
  if (!extras) throw new Error("Extras tab was not rendered: " + bodyText().slice(0, 400));
  await act(async () => {
    extras.dispatchEvent(
      new window.MouseEvent("mousedown", { bubbles: true, button: 0, buttons: 1 }),
    );
  });

  let table = null;
  for (let i = 0; i < 20; i++) {
    const title = [...document.querySelectorAll("[data-slot='card-title']")].find((el) =>
      el.textContent.includes("Ancillary Purchases"),
    );
    table = title ? title.closest("[data-slot='card']")?.querySelector("table") : null;
    if (table) break;
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
  }
  if (!table) {
    throw new Error("Ancillary Purchases table was not rendered: " + bodyText().slice(0, 500));
  }

  const { headers, cells } = tableText(table);
  const headerNorm = headers.map(norm);
  const inUseIndex = headerNorm.indexOf("emd in use");
  const documentIndex = headerNorm.findIndex(
    (header, index) =>
      index !== inUseIndex &&
      [
        "emd",
        "emd number",
        "emd ticket",
        "emd ticket number",
        "emd document",
        "document",
        "ticket",
      ].includes(header),
  );
  const shown = `headers=${JSON.stringify(headers)} cells=${JSON.stringify(cells)}`;
  if (inUseIndex < 0 || documentIndex < 0) {
    throw new Error(
      `expected an "EMD in use" column and a separate EMD document column\n${shown}`,
    );
  }
  const inUseCell = cells[inUseIndex] ?? "";
  const documentCell = cells[documentIndex] ?? "";
  if (norm(inUseCell) !== norm(input.inUse)) {
    throw new Error(
      `EMD in use cell should be ${JSON.stringify(input.inUse)} but was ${JSON.stringify(inUseCell)}\n${shown}`,
    );
  }
  if (input.document == null) {
    if (!PLACEHOLDERS.has(norm(documentCell)) && !PLACEHOLDERS.has(documentCell.trim())) {
      throw new Error(
        `EMD document cell should be empty when the response has no ticket number, was ${JSON.stringify(documentCell)}\n${shown}`,
      );
    }
  } else if (!documentCell.includes(input.document)) {
    throw new Error(
      `EMD document cell should show ${input.document} but was ${JSON.stringify(documentCell)}\n${shown}`,
    );
  }
  if (input.document && inUseCell.includes(input.document)) {
    throw new Error(`EMD in use cell also contains the document number\n${shown}`);
  }
  root.unmount();
} catch (error) {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
} finally {
  fs.rmSync(harnessPath, { force: true });
  fs.rmSync(outfile, { force: true });
  process.exit(process.exitCode || 0);
}
