/**
 * Render the flight-board passenger manifest and report which edit codes
 * an operator can read in each Codes cell.
 *
 * Input: a passenger-list JSON document (the /flights/{fn}/passengers body).
 * Output: one JSON object on stdout. Does not print the document itself.
 */
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const payloadPath = process.argv[2];
if (!payloadPath) {
  console.error("usage: render-manifest-codes.tsx <passenger-list.json>");
  process.exit(2);
}

const payload = JSON.parse(readFileSync(payloadPath, "utf8")) as {
  flightNumber: string;
  origin: string;
  departureDate: string;
  passengers: Array<{
    lastName: string;
    firstName: string;
    pnr: string;
    seat: string;
    editCodes?: string[];
  }>;
};

const dom = new JSDOM(
  "<!DOCTYPE html><html><body><div id=\"root\"></div></body></html>",
  { url: "http://127.0.0.1:3000/", pretendToBeVisual: true },
);
const { window } = dom;

function copyGlobal(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, {
    value,
    configurable: true,
    writable: true,
  });
}

copyGlobal("window", window);
copyGlobal("document", window.document);
copyGlobal("navigator", window.navigator);
copyGlobal("HTMLElement", window.HTMLElement);
copyGlobal("Element", window.Element);
copyGlobal("Node", window.Node);
copyGlobal("DocumentFragment", window.DocumentFragment);
copyGlobal("SVGElement", window.SVGElement);
copyGlobal("MutationObserver", window.MutationObserver);
copyGlobal("getComputedStyle", window.getComputedStyle.bind(window));
const requestFrame = (callback: FrameRequestCallback) => {
  return setTimeout(() => callback(Date.now()), 0) as unknown as number;
};
const cancelFrame = (handle: number) => {
  clearTimeout(handle);
};
copyGlobal("requestAnimationFrame", requestFrame);
copyGlobal("cancelAnimationFrame", cancelFrame);
window.requestAnimationFrame = requestFrame;
window.cancelAnimationFrame = cancelFrame;
copyGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const passengersUrl = `/flights/${payload.flightNumber}/passengers`;
const auditUrl = `/flights/${payload.flightNumber}/audit`;

async function fetchStub(input: RequestInfo | URL): Promise<Response> {
  const url = String(input);
  if (url.includes(passengersUrl)) {
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (url.includes(auditUrl)) {
    return new Response(JSON.stringify({ passengerAlerts: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response(JSON.stringify({ detail: "not found" }), {
    status: 404,
    headers: { "Content-Type": "application/json" },
  });
}

copyGlobal("fetch", fetchStub);
window.fetch = fetchStub as typeof window.fetch;

async function main() {
// tsx compiles this file to CommonJS. A dynamic import() of react-query loads a
// second copy from the package's ESM entry, so its context never reaches the
// table. Require the same CJS entry the compiled component uses.
const { createRequire } = await import("node:module");
const require = createRequire(`${process.cwd()}/tests/render-manifest-codes.tsx`);
const React = require("react") as typeof import("react");
const { createRoot } = require("react-dom/client") as typeof import("react-dom/client");
const { QueryClient, QueryClientProvider } = require("@tanstack/react-query") as typeof import("@tanstack/react-query");
const { PassengerTable } = require("../components/dashboard/passenger-table") as typeof import("../components/dashboard/passenger-table");

function Harness() {
  const [filterCabin, setFilterCabin] = React.useState<"all" | "Y" | "J">("all");
  const [filterStatus, setFilterStatus] = React.useState<"all" | "booked" | "checkedIn" | "boarded">("all");
  const [filterType, setFilterType] = React.useState<
    "all" | "revenue" | "nonRevenue" | "child" | "infant" | "group" | "unnamedGroup"
  >("all");
  const [filterLoyalty, setFilterLoyalty] = React.useState<"all" | "FF" | "BLU" | "SLV" | "GLD" | "BLK">("all");
  const [filterNationality, setFilterNationality] = React.useState("all");
  return (
    <PassengerTable
      flightNumber={payload.flightNumber}
      origin={payload.origin}
      date={payload.departureDate}
      filterCabin={filterCabin}
      setFilterCabin={setFilterCabin}
      filterStatus={filterStatus}
      setFilterStatus={setFilterStatus}
      filterType={filterType}
      setFilterType={setFilterType}
      filterLoyalty={filterLoyalty}
      setFilterLoyalty={setFilterLoyalty}
      filterNationality={filterNationality}
      setFilterNationality={setFilterNationality}
    />
  );
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

const rootEl = window.document.getElementById("root");
if (!rootEl) {
  throw new Error("missing root");
}
const root = createRoot(rootEl);

await React.act(async () => {
  root.render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
});

function bodyHasPassenger(): boolean {
  return (window.document.body.textContent || "").includes(payload.passengers[0]?.lastName || "");
}

for (let i = 0; i < 40 && !bodyHasPassenger(); i += 1) {
  await React.act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 25));
  });
}

if (!bodyHasPassenger()) {
  const loading = (window.document.body.textContent || "").includes("Loading manifest");
  throw new Error(loading ? "manifest still loading" : "manifest did not render passengers");
}

function badgeLabels(scope: ParentNode): string[] {
  return [...scope.querySelectorAll("[data-slot='badge']")]
    .map((el) => (el.textContent || "").replace(/\s+/g, " ").trim())
    .filter((label) => label.length > 0);
}

async function labelsAfterOverflow(cell: Element): Promise<string[]> {
  const badges = [...cell.querySelectorAll("[data-slot='badge']")];
  const targets = new Set<Element>();
  for (const badge of badges) {
    if (/^\+\d+$/.test((badge.textContent || "").trim())) {
      targets.add(badge);
      const control = badge.closest("button, summary, a, [role='button']");
      if (control && cell.contains(control)) targets.add(control);
    }
  }
  for (const el of cell.querySelectorAll("button, summary, a, [role='button']")) {
    targets.add(el);
  }
  for (const el of targets) {
    await React.act(async () => {
      el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
  }
  const labels = new Set(badgeLabels(cell));
  for (const portal of window.document.querySelectorAll(
    "[role='dialog'], [role='menu'], [role='listbox'], [data-state='open'], [popover]",
  )) {
    if (cell.contains(portal)) continue;
    for (const label of badgeLabels(portal)) labels.add(label);
  }
  return [...labels];
}

const headers = [...window.document.querySelectorAll("thead th")].map((th) =>
  (th.textContent || "").trim(),
);
const codesIdx = headers.findIndex((header) => header === "Codes");
if (codesIdx < 0) {
  throw new Error(`Codes column missing from headers: ${headers.join("|")}`);
}

const rows = [...window.document.querySelectorAll("tbody tr")];
const failures: Array<{
  pnr: string;
  lastName: string;
  visible: string[];
  missing: string[];
}> = [];

for (const passenger of payload.passengers) {
  const expected = passenger.editCodes ?? [];
  const row = rows.find((tr) => {
    const cells = [...tr.querySelectorAll("td")];
    const name = cells[0]?.textContent || "";
    const pnr = cells[1]?.textContent || "";
    const seat = (cells[4]?.textContent || "").trim();
    const expectedSeat = passenger.seat || "—";
    return (
      name.includes(passenger.lastName) &&
      name.includes(passenger.firstName) &&
      pnr.includes(passenger.pnr) &&
      seat === expectedSeat
    );
  });
  if (!row) {
    failures.push({
      pnr: passenger.pnr,
      lastName: passenger.lastName,
      visible: [],
      missing: expected,
    });
    continue;
  }
  const cell = row.querySelectorAll("td")[codesIdx];
  if (!cell) {
    failures.push({
      pnr: passenger.pnr,
      lastName: passenger.lastName,
      visible: [],
      missing: expected,
    });
    continue;
  }
  const visible = await labelsAfterOverflow(cell);
  const readable = new Set(visible);
  const missing = expected.filter((code) => !readable.has(code));
  if (missing.length > 0) {
    failures.push({
      pnr: passenger.pnr,
      lastName: passenger.lastName,
      visible,
      missing,
    });
  }
}

const baboo = failures.find((failure) => failure.pnr === "ESMMQK") ?? null;
process.stdout.write(JSON.stringify({
  renderedPassengers: rows.length,
  codesColumn: codesIdx,
  failureCount: failures.length,
  baboo,
  sampleFailures: failures.slice(0, 3),
}));
// Query timers stay open after the table renders. Exit once the report is written.
process.exit(0);
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.stack || error.message : error);
  process.exit(1);
});
