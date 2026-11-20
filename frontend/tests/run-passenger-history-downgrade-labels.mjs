/**
 * Boots a DOM and the timeline payload server, then runs
 * passenger-history-downgrade-labels.test.tsx against the real component.
 *
 * Usage (from frontend/):
 *   npm install
 *   npm install --no-save jsdom esbuild
 *   node tests/run-passenger-history-downgrade-labels.mjs
 */
import http from "node:http";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import esbuild from "esbuild";
import { JSDOM } from "jsdom";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
  url: "http://127.0.0.1/",
  pretendToBeVisual: true,
});
const { window } = dom;

const globals = {
  window,
  document: window.document,
  HTMLElement: window.HTMLElement,
  SVGElement: window.SVGElement,
  Element: window.Element,
  Node: window.Node,
  DocumentFragment: window.DocumentFragment,
  MutationObserver: window.MutationObserver,
  NodeFilter: window.NodeFilter,
  DOMParser: window.DOMParser,
  getComputedStyle: window.getComputedStyle.bind(window),
};

for (const [key, value] of Object.entries(globals)) {
  Object.defineProperty(globalThis, key, {
    value,
    configurable: true,
    writable: true,
  });
}

globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.matchMedia = () => ({
  matches: false,
  media: "",
  onchange: null,
  addEventListener() {},
  removeEventListener() {},
  addListener() {},
  removeListener() {},
  dispatchEvent() {
    return false;
  },
});
globalThis.matchMedia = window.matchMedia;

let payload = {};
globalThis.__setTimelinePayload = (body) => {
  payload = body;
};

const server = http.createServer((_request, response) => {
  response.writeHead(200, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
});

await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});

const { port } = server.address();
process.env.NEXT_PUBLIC_API_URL = `http://127.0.0.1:${port}`;

const outfile = path.join("/tmp", "passenger-history-downgrade-labels.cjs");
await esbuild.build({
  absWorkingDir: frontendRoot,
  entryPoints: ["tests/passenger-history-downgrade-labels.test.tsx"],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile,
  jsx: "automatic",
  alias: { "@": frontendRoot },
  define: { "process.env.NODE_ENV": '"test"' },
  external: ["jsdom"],
  logLevel: "silent",
});

try {
  const loaded = await import(pathToFileURL(outfile).href);
  let run = loaded.default;
  if (typeof run !== "function" && run && typeof run.default === "function") {
    run = run.default;
  }
  if (typeof run !== "function") {
    throw new Error(`timeline test entry is ${typeof run}`);
  }
  await run();
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  server.closeAllConnections?.();
  server.close();
  setTimeout(() => process.exit(process.exitCode || 0), 20);
}
