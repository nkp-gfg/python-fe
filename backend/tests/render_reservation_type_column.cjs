/**
 * Render ReservationView the way the flight board does and read the Type
 * column after expanding a booking.
 *
 * The query cache is seeded with the reservations JSON the API returned.
 * A network fetch means the component did not use that payload.
 */
const fs = require("fs");
const path = require("path");

const frontendRoot = path.resolve(__dirname, "../../frontend");
const { JSDOM } = require(path.join(frontendRoot, "node_modules/jsdom"));

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="root"></div></body></html>',
  { url: "http://127.0.0.1:3000/", pretendToBeVisual: true },
);
const { window } = dom;

global.window = window;
global.document = window.document;
global.navigator = window.navigator;
global.HTMLElement = window.HTMLElement;
global.Element = window.Element;
global.Node = window.Node;
global.SVGElement = window.SVGElement;
global.DocumentFragment = window.DocumentFragment;
global.MutationObserver = window.MutationObserver;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.IS_REACT_ACT_ENVIRONMENT = true;
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
global.cancelAnimationFrame = (id) => clearTimeout(id);
global.fetch = () => {
  throw new Error(
    "ReservationView called fetch. The test seeds the query cache with the API payload.",
  );
};

const ts = require(path.join(frontendRoot, "node_modules/typescript"));
const Module = require("module");

function resolveAlias(request) {
  if (!request.startsWith("@/")) return null;
  const base = path.join(frontendRoot, request.slice(2));
  const candidates = [
    base,
    `${base}.tsx`,
    `${base}.ts`,
    path.join(base, "index.tsx"),
    path.join(base, "index.ts"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return candidate;
    }
  }
  throw new Error(`Cannot resolve ${request}`);
}

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, isMain, options) {
  const aliased = resolveAlias(request);
  return origResolve.call(this, aliased || request, parent, isMain, options);
};

function compile(module, filename) {
  const source = fs.readFileSync(filename, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: filename,
  }).outputText;
  module._compile(output, filename);
}

Module._extensions[".tsx"] = compile;
Module._extensions[".ts"] = compile;

function resolveFromFrontend(specifier) {
  return require.resolve(specifier, { paths: [frontendRoot] });
}

const React = require(resolveFromFrontend("react"));
const { createRoot } = require(resolveFromFrontend("react-dom/client"));
const { act } = React;
const { QueryClient, QueryClientProvider } = require(
  resolveFromFrontend("@tanstack/react-query"),
);
const { ReservationView } = require(path.join(
  frontendRoot,
  "components/dashboard/reservation-view.tsx",
));

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

function typeColumn(pnr) {
  const card = [...document.querySelectorAll("button")].find((button) =>
    (button.textContent || "").includes(pnr),
  );
  if (!card) {
    throw new Error(`No reservation card for ${pnr}`);
  }
  const table = card.parentElement && card.parentElement.querySelector("table");
  if (!table) {
    throw new Error(`Reservation ${pnr} did not expand a passenger table`);
  }
  const headers = [...table.querySelectorAll("thead th")].map((cell) =>
    (cell.textContent || "").trim(),
  );
  const typeIndex = headers.indexOf("Type");
  if (typeIndex < 0) {
    throw new Error(`Reservation ${pnr} table has no Type column: ${headers.join(",")}`);
  }
  return [...table.querySelectorAll("tbody tr")].map((row) => {
    const cells = [...row.querySelectorAll("td")];
    return {
      pnr,
      name: (cells[0]?.textContent || "").trim(),
      type: (cells[typeIndex]?.textContent || "").trim(),
    };
  });
}

async function main() {
  const input = JSON.parse(await readStdin());
  const flightNumber = input.flightNumber;
  const origin = input.origin;
  const date = input.date;
  const snapshotSequence = input.snapshotSequence ?? undefined;

  // Same defaults as frontend/components/providers.tsx, except retry is off
  // so a cache miss fails this process instead of waiting on the network.
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: false,
        refetchOnWindowFocus: false,
      },
    },
  });
  queryClient.setQueryData(
    ["reservations", flightNumber, origin, date, snapshotSequence],
    input.response,
  );

  const root = createRoot(document.getElementById("root"));
  await act(async () => {
    root.render(
      React.createElement(
        QueryClientProvider,
        { client: queryClient },
        React.createElement(ReservationView, {
          flightNumber,
          origin,
          date,
          snapshotSequence,
        }),
      ),
    );
  });

  const rows = [];
  for (const pnr of input.expand) {
    const button = [...document.querySelectorAll("button")].find((candidate) =>
      (candidate.textContent || "").includes(pnr),
    );
    if (!button) {
      throw new Error(
        `Flight board did not list ${pnr}. Visible text: ${(document.body.textContent || "").slice(0, 300)}`,
      );
    }
    await act(async () => {
      button.click();
    });
    rows.push(...typeColumn(pnr));
    await act(async () => {
      button.click();
    });
  }

  process.stdout.write(JSON.stringify({ rows }));
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exit(1);
});
