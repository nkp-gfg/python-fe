/**
 * Render the reservation card with two bookings whose purge dates differ.
 * Exits 0 only when each expanded card shows its own EstimatedPurgeTimestamp.
 */
import { createRequire } from "node:module";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const frontend = path.join(repo, "frontend");
const requireFromFrontend = createRequire(path.join(frontend, "package.json"));

const esbuild = requireFromFrontend("esbuild");
const { Window } = requireFromFrontend("happy-dom");

const payload = {
  airline: "GF",
  flightNumber: "2057",
  departureAirport: "BOM",
  departureDate: "2026-03-18",
  fetchedAt: "2026-03-18T00:00:00Z",
  totalResults: 2,
  reservations: [
    {
      pnr: "CQVJQD",
      numberInParty: 1,
      numberOfInfants: 0,
      createdAt: "2026-03-17T15:15:00",
      updatedAt: "2026-03-18T02:53:39",
      estimatedPurgeTimestamp: "2026-03-27T00:00:00",
      passengers: [{ lastName: "ONE", firstName: "TEST", nameType: "S" }],
      segments: [],
      tickets: [],
    },
    {
      pnr: "GCPVKA",
      numberInParty: 1,
      numberOfInfants: 0,
      createdAt: "2026-03-16T22:53:00",
      updatedAt: "2026-03-17T10:48:33",
      estimatedPurgeTimestamp: "2026-03-18T00:00:00",
      passengers: [{ lastName: "TWO", firstName: "TEST", nameType: "S" }],
      segments: [],
      tickets: [],
    },
  ],
};

const mockApi = {
  name: "mock-fetch-reservations",
  setup(build) {
    build.onResolve({ filter: /^@\/lib\/api$/ }, (args) => ({
      path: args.path,
      namespace: "mock-api",
    }));
    build.onLoad({ filter: /.*/, namespace: "mock-api" }, () => ({
      loader: "js",
      contents: `export function fetchReservations(){ return Promise.resolve(${JSON.stringify(payload)}); }\n`,
    }));
  },
};

const outdir = mkdtempSync(path.join(tmpdir(), "reservation-card-"));
const outfile = path.join(outdir, "bundle.mjs");

await esbuild.build({
  stdin: {
    contents: `
      import React, { act } from "react";
      import { createRoot } from "react-dom/client";
      import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
      import { ReservationView } from "./components/dashboard/reservation-view.tsx";

      export async function mount() {
        const queryClient = new QueryClient({
          defaultOptions: { queries: { retry: false } },
        });
        const container = document.createElement("div");
        document.body.appendChild(container);
        const root = createRoot(container);
        await act(async () => {
          root.render(
            React.createElement(
              QueryClientProvider,
              { client: queryClient },
              React.createElement(ReservationView, {
                flightNumber: "2057",
                origin: "BOM",
                date: "2026-03-18",
              })
            )
          );
        });
        for (let i = 0; i < 20; i += 1) {
          if (document.body.textContent.includes("CQVJQD")) return;
          await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 25));
          });
        }
        throw new Error("reservation cards did not render");
      }

      export async function expandedText(pnr) {
        const button = [...document.querySelectorAll("button")].find((node) =>
          node.textContent.includes(pnr)
        );
        if (!button) throw new Error("missing card " + pnr);
        await act(async () => {
          button.click();
        });
        const card = button.closest("[data-slot='card']");
        return card ? card.textContent : "";
      }
    `,
    resolveDir: frontend,
    sourcefile: "card-harness.tsx",
    loader: "tsx",
  },
  bundle: true,
  format: "esm",
  platform: "browser",
  target: "es2022",
  outfile,
  jsx: "automatic",
  alias: { "@": frontend },
  define: { "process.env.NODE_ENV": '"test"' },
  plugins: [mockApi],
  logLevel: "silent",
});

const window = new Window({ url: "http://127.0.0.1/" });

function installGlobal(name, value) {
  Object.defineProperty(globalThis, name, {
    value,
    configurable: true,
    writable: true,
  });
}

installGlobal("window", window);
installGlobal("document", window.document);
installGlobal("HTMLElement", window.HTMLElement);
installGlobal("Node", window.Node);
installGlobal("Element", window.Element);
installGlobal("DocumentFragment", window.DocumentFragment);
installGlobal("navigator", window.navigator);
installGlobal("MutationObserver", window.MutationObserver);
installGlobal("getComputedStyle", window.getComputedStyle.bind(window));
installGlobal("requestAnimationFrame", (callback) => setTimeout(() => callback(Date.now()), 0));
installGlobal("cancelAnimationFrame", (id) => clearTimeout(id));
installGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const harness = await import(pathToFileURL(outfile).href);
await harness.mount();

function assertShowsPurge(text, pnr, isoDate, formattedDate) {
  if (!/purge/i.test(text)) {
    throw new Error(`${pnr} card does not show a purge time`);
  }
  if (!text.includes(formattedDate) && !text.includes(isoDate)) {
    throw new Error(`${pnr} card does not show ${isoDate}`);
  }
}

const later = await harness.expandedText("CQVJQD");
const sameNight = await harness.expandedText("GCPVKA");
if (!later.includes("Created") || !later.includes("Updated")) {
  throw new Error("expanded card did not render the booking timestamps");
}
assertShowsPurge(later, "CQVJQD", "2026-03-27T00:00:00", "27 Mar 2026");
assertShowsPurge(sameNight, "GCPVKA", "2026-03-18T00:00:00", "18 Mar 2026");

if (later.includes("27 Mar 2026") && sameNight.includes("27 Mar 2026")) {
  throw new Error("both cards show the later purge date");
}

console.log("reservation card shows distinct purge dates");
writeFileSync(path.join(outdir, "ok"), "ok");
