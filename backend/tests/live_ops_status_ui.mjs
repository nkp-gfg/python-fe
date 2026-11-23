/**
 * Renders the live operations workbench and the passenger table it opens.
 * The fixture is the dashboard payload and manifest produced by the API.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const frontendDir = path.join(repoRoot, "frontend");
const require = createRequire(path.join(frontendDir, "package.json"));
const esbuild = require("esbuild");
const { Window } = require("happy-dom");

const fixturePath = process.argv[2];
if (!fixturePath) {
  console.error("usage: node live_ops_status_ui.mjs <fixture.json>");
  process.exit(2);
}
const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));

const window = new Window({ url: "http://127.0.0.1:3000/" });
const domGlobals = [
  "window",
  "document",
  "navigator",
  "HTMLElement",
  "HTMLButtonElement",
  "Element",
  "Node",
  "DocumentFragment",
  "SVGElement",
  "Event",
  "MouseEvent",
  "KeyboardEvent",
  "CustomEvent",
  "NodeFilter",
  "DOMParser",
  "MutationObserver",
  "getComputedStyle",
  "requestAnimationFrame",
  "cancelAnimationFrame",
];
for (const name of domGlobals) {
  if (name === "navigator") continue;
  try {
    globalThis[name] = window[name];
  } catch {
    Object.defineProperty(globalThis, name, { configurable: true, get: () => window[name] });
  }
}
globalThis.localStorage = window.localStorage;
globalThis.sessionStorage = window.sessionStorage;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
if (!window.matchMedia) {
  window.matchMedia = () => ({
    matches: false,
    media: "",
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent() { return false; },
  });
}
globalThis.ResizeObserver = window.ResizeObserver;
globalThis.matchMedia = window.matchMedia.bind(window);
globalThis.setTimeout = window.setTimeout.bind(window);
globalThis.clearTimeout = window.clearTimeout.bind(window);
globalThis.setInterval = window.setInterval.bind(window);
globalThis.clearInterval = window.clearInterval.bind(window);

const stubDir = fs.mkdtempSync(path.join(os.tmpdir(), "live-ops-stubs-"));
fs.writeFileSync(
  path.join(stubDir, "link.jsx"),
  `export default function Link({ children, href, ...props }) {
    return <a href={typeof href === "string" ? href : "#"} {...props}>{children}</a>;
  }
  `,
);
fs.writeFileSync(
  path.join(stubDir, "navigation.js"),
  `export function useRouter() {
    return { push() {}, replace() {}, prefetch() {}, back() {} };
  }
  export function usePathname() { return "/"; }
  export function useSearchParams() { return new URLSearchParams(); }
  `,
);
fs.writeFileSync(
  path.join(stubDir, "dynamic.js"),
  `export default function dynamic(loader) {
    let Resolved = null;
    let pending = null;
    function Dynamic(props) {
      if (!Resolved) {
        if (!pending) {
          pending = Promise.resolve(loader()).then((mod) => {
            Resolved = mod.default || mod;
          });
        }
        throw pending;
      }
      return <Resolved {...props} />;
    }
    return Dynamic;
  }
  `,
);

const workbenchPlugin = {
  name: "workbench-passenger-table",
  setup(build) {
    build.onResolve({ filter: /^@\// }, (args) => {
      const base = path.join(frontendDir, args.path.slice(2));
      const candidates = [base, `${base}.tsx`, `${base}.ts`, `${base}.jsx`, `${base}.js`];
      const resolved = candidates.find((candidate) => fs.existsSync(candidate));
      return { path: resolved || `${base}.tsx` };
    });
    build.onLoad({ filter: /flight-workbench\.tsx$/ }, async (args) => {
      let text = await fs.promises.readFile(args.path, "utf8");
      text = text.replace(
        /const (\w+) = dynamic\(\(\) => import\([^;]+;/g,
        (full, name) => {
          if (name === "PassengerTable") {
            return "const PassengerTable = __RealPassengerTable;";
          }
          return `const ${name} = () => null;`;
        },
      );
      text =
        'import { PassengerTable as __RealPassengerTable } from "@/components/dashboard/passenger-table";\n' +
        text;
      return { contents: text, loader: "tsx" };
    });
  },
};

const entry = `
import { createRoot } from "react-dom/client";
import { act } from "react";
import { Providers } from "@/components/providers";
import { FlightWorkbench } from "@/components/dashboard/flight-workbench";

function statusTile(label) {
  const button = [...document.querySelectorAll("button")].find((candidate) =>
    [...candidate.querySelectorAll("p")].some((node) => node.textContent.trim() === label),
  );
  if (!button) throw new Error("Missing status tile button: " + label);
  const numberNode = [...button.querySelectorAll("p")].find((node) => node.textContent.trim() !== label);
  const value = Number(numberNode.textContent.trim());
  if (!Number.isFinite(value)) throw new Error("Status tile " + label + " is not a number: " + numberNode.textContent);
  return { button, value };
}

function phaseValue(label) {
  const heading = [...document.querySelectorAll("h3")].find((node) =>
    node.textContent.includes("Operational Phase"),
  );
  if (!heading) throw new Error("Operational Phase card is not on the page");
  const card = heading.parentElement.parentElement;
  const row = [...card.querySelectorAll("div")].find((node) => {
    const spans = [...node.children].filter((child) => child.tagName === "SPAN");
    return spans[0] && spans[0].textContent.trim() === label;
  });
  if (!row) throw new Error("Operational Phase row missing: " + label);
  const spans = [...row.children].filter((child) => child.tagName === "SPAN");
  const value = Number(spans[spans.length - 1].textContent.trim());
  if (!Number.isFinite(value)) throw new Error("Operational Phase " + label + " is not a number");
  return value;
}

function listCount() {
  const node = [...document.querySelectorAll("span")].find((span) =>
    /\\d+ of \\d+ passengers/.test(span.textContent),
  );
  if (!node) throw new Error("Passenger list count is not on the page. Body: " + document.body.textContent.slice(0, 500));
  const match = node.textContent.match(/(\\d+) of (\\d+) passengers/);
  return { shown: Number(match[1]), total: Number(match[2]) };
}

async function clickAndWait(button) {
  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if ([...document.querySelectorAll("span")].some((span) => /\\d+ of \\d+ passengers/.test(span.textContent))) {
      return;
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 25));
    });
  }
  throw new Error("Opened passenger list did not render. Body: " + document.body.textContent.slice(0, 800));
}

function assertScenario(name, tile, shown, phase, total) {
  const failures = [];
  if (tile.value !== shown) {
    failures.push(name + " tile " + tile.label + " shows " + tile.value + " but the opened list has " + shown);
  }
  if (tile.value < 0) {
    failures.push(name + " tile " + tile.label + " is negative (" + tile.value + ")");
  }
  if (phase !== undefined && phase !== shown) {
    failures.push(name + " operational phase " + tile.phaseLabel + " shows " + phase + " but the opened list has " + shown);
  }
  return failures;
}

export async function run(scenarios) {
  const failures = [];
  for (const scenario of scenarios) {
    globalThis.__LIVE_OPS_FIXTURE = scenario;
    document.body.innerHTML = '<div id="root"></div>';
    const root = createRoot(document.getElementById("root"));
    await act(async () => {
      root.render(
        <Providers>
          <FlightWorkbench initialSelection={{ flightNumber: "2057", origin: "BAH", date: "2026-11-23" }} />
        </Providers>,
      );
    });
    let ready = false;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const found = [...document.querySelectorAll("button p")].some((node) => node.textContent.trim() === "Checked-In");
      if (found) {
        ready = true;
        break;
      }
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 25));
      });
    }
    if (!ready) {
      failures.push(scenario.name + " did not render the status tile. Body: " + document.body.textContent.slice(0, 800));
      root.unmount();
      continue;
    }

    const checkedIn = statusTile("Checked-In");
    const booked = statusTile("Booked");
    const boarded = statusTile("Boarded");
    const phaseCheckedIn = phaseValue("Checked in");
    const phaseNotCheckedIn = phaseValue("Not checked in");
    const phaseBoarded = phaseValue("Boarded");

    await clickAndWait(checkedIn.button);
    let list = listCount();
    failures.push(
      ...assertScenario(
        scenario.name,
        { ...checkedIn, label: "Checked-In", phaseLabel: "Checked in" },
        list.shown,
        phaseCheckedIn,
        list.total,
      ),
    );
    const checkedInText = document.body.textContent;
    if (scenario.name === "boarded_without_checkin") {
      if (checkedInText.includes("CHO, CHRIS") || checkedInText.includes("CHO, CASEY")) {
        failures.push(scenario.name + " Checked-In list includes a boarded passenger who is not checked in");
      }
    }

    await clickAndWait(booked.button);
    list = listCount();
    failures.push(
      ...assertScenario(
        scenario.name,
        { ...booked, label: "Booked", phaseLabel: "Not checked in" },
        list.shown,
        phaseNotCheckedIn,
        list.total,
      ),
    );
    const bookedText = document.body.textContent;
    if (scenario.name === "boarded_without_checkin") {
      if (bookedText.includes("CHO, CHRIS") || bookedText.includes("CHO, CASEY")) {
        failures.push(scenario.name + " Booked list includes a boarded passenger who is not checked in");
      }
      if (!bookedText.includes("DIAZ, DANA")) {
        failures.push(scenario.name + " Booked list is missing the passenger who is neither checked in nor boarded");
      }
    }

    await clickAndWait(boarded.button);
    list = listCount();
    failures.push(
      ...assertScenario(
        scenario.name,
        { ...boarded, label: "Boarded", phaseLabel: "Boarded" },
        list.shown,
        phaseBoarded,
        list.total,
      ),
    );

    const sum = checkedIn.value + booked.value + boarded.value;
    if (sum !== list.total) {
      failures.push(scenario.name + " status tiles sum to " + sum + " but the manifest has " + list.total);
    }

    const infoButton = document.querySelector('button[aria-label="Status info"]');
    if (!infoButton) {
      failures.push(scenario.name + " is missing the status calculation info button");
    } else {
      await act(async () => {
        infoButton.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      });
      const docs = [...document.querySelectorAll("h2")].find((node) => node.textContent.includes("Tile Documentation"));
      const panelText = document.body.textContent;
      if (!docs) {
        failures.push(scenario.name + " status info panel did not open");
      } else if (/checkedIn\\s*-\\s*analysis\\.boarded/.test(panelText)) {
        failures.push(scenario.name + " status info still defines Checked-In as checkedIn minus boarded");
      } else if (/Booked \\(not checked in\\) = analysis\\.notCheckedIn/.test(panelText)) {
        failures.push(scenario.name + " status info still defines Booked as the raw notCheckedIn counter");
      }
    }

    root.unmount();
  }
  if (failures.length) {
    throw new Error(failures.join("\\n"));
  }
}
`;

const built = await esbuild.build({
  stdin: {
    contents: entry,
    resolveDir: frontendDir,
    sourcefile: "live-ops-status-entry.tsx",
    loader: "tsx",
  },
  bundle: true,
  format: "esm",
  platform: "browser",
  absWorkingDir: frontendDir,
  nodePaths: [path.join(frontendDir, "node_modules")],
  outfile: path.join(stubDir, "bundle.mjs"),
  jsx: "automatic",
  plugins: [workbenchPlugin],
  alias: {
    "next/link": path.join(stubDir, "link.jsx"),
    "next/navigation": path.join(stubDir, "navigation.js"),
    "next/dynamic": path.join(stubDir, "dynamic.js"),
  },
  define: {
    "process.env.NODE_ENV": '"development"',
    "process.env.NEXT_PUBLIC_API_URL": '"http://127.0.0.1:8000"',
  },
  loader: { ".css": "empty" },
  logLevel: "silent",
});

if (built.errors && built.errors.length) {
  console.error(built.errors);
  process.exit(1);
}

globalThis.fetch = async (url) => {
  const target = String(url);
  const scenario = globalThis.__LIVE_OPS_FIXTURE;
  let body = {};
  if (target.includes("/dashboard")) body = scenario.dashboard;
  else if (target.includes("/passengers")) body = scenario.passengers;
  else if (target.includes("/audit")) body = scenario.audit;
  else if (target.includes("/tree")) body = scenario.tree;
  else if (target.includes("/flights")) body = [];
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
};
window.fetch = globalThis.fetch;

const originalConsoleError = console.error;
console.error = (...args) => {
  const text = args.map((arg) => String(arg)).join(" ");
  if (text.includes("not wrapped in act")) return;
  originalConsoleError(...args);
};

const bundled = await import(pathToFileURL(path.join(stubDir, "bundle.mjs")).href);
try {
  await bundled.run(fixture.scenarios);
  console.log("live operations status tiles match the passenger lists they open");
} catch (error) {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
}
