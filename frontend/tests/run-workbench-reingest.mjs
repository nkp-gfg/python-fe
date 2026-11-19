import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(frontendRoot, "package.json"));
const esbuild = require("esbuild");

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://127.0.0.1:3000/",
  pretendToBeVisual: true,
});
const { window } = dom;
const globals = {
  window,
  document: window.document,
  HTMLElement: window.HTMLElement,
  Element: window.Element,
  Node: window.Node,
  DocumentFragment: window.DocumentFragment,
  SVGElement: window.SVGElement,
  MutationObserver: window.MutationObserver,
  getComputedStyle: window.getComputedStyle.bind(window),
};
for (const [key, value] of Object.entries(globals)) {
  Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
}
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.matchMedia = () => ({
  matches: false,
  media: "",
  addEventListener() {},
  removeEventListener() {},
  addListener() {},
  removeListener() {},
  dispatchEvent() { return false; },
});
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
class IntersectionObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
for (const name of [
  "MouseEvent",
  "KeyboardEvent",
  "PointerEvent",
  "Event",
  "CustomEvent",
  "HTMLElement",
  "Element",
  "Node",
  "DocumentFragment",
  "SVGElement",
]) {
  if (name in window) {
    Object.defineProperty(globalThis, name, {
      value: window[name],
      configurable: true,
      writable: true,
    });
  }
}
window.ResizeObserver = ResizeObserverStub;
window.IntersectionObserver = IntersectionObserverStub;
globalThis.ResizeObserver = ResizeObserverStub;
globalThis.IntersectionObserver = IntersectionObserverStub;

function resolveAt(source) {
  const base = path.join(frontendRoot, source);
  const candidates = [
    base,
    `${base}.tsx`,
    `${base}.ts`,
    `${base}.jsx`,
    `${base}.js`,
    path.join(base, "index.tsx"),
    path.join(base, "index.ts"),
    path.join(base, "index.js"),
  ];
  const found = candidates.find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
  if (!found) {
    throw new Error(`Cannot resolve @/${source}`);
  }
  return found;
}

const outfile = path.join(os.tmpdir(), "workbench-reingest.bundle.cjs");
await esbuild.build({
  absWorkingDir: frontendRoot,
  entryPoints: [path.join(frontendRoot, "tests", "workbench-reingest.tsx")],
  outfile,
  bundle: true,
  format: "cjs",
  platform: "node",
  jsx: "automatic",
  target: "es2022",
  define: {
    "process.env.NODE_ENV": '"test"',
    "process.env.NEXT_PUBLIC_API_URL": '"http://127.0.0.1:8000"',
  },
  alias: {
    "next/navigation": path.join(frontendRoot, "tests", "mocks", "next-navigation.js"),
    "next/link": path.join(frontendRoot, "tests", "mocks", "next-link.js"),
    "next/dynamic": path.join(frontendRoot, "tests", "mocks", "next-dynamic.js"),
  },
  loader: { ".css": "empty" },
  plugins: [
    {
      name: "at-alias",
      setup(build) {
        build.onResolve({ filter: /^@\// }, (args) => ({ path: resolveAt(args.path.slice(2)) }));
      },
    },
  ],
});

await import(pathToFileURL(outfile).href);
