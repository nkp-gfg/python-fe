/**
 * Bundle the analytics page and render it in jsdom.
 *
 * Usage (from frontend/):
 *   npm install
 *   npm install --no-save jsdom@26.1.0
 *   node tests/run-special-meal-chart-labels.mjs
 */
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
const { JSDOM } = require("jsdom");

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outfile = path.join("/tmp", "special-meal-chart-labels.mjs");

await esbuild.build({
  absWorkingDir: frontendRoot,
  entryPoints: ["tests/special-meal-chart-labels.entry.tsx"],
  bundle: true,
  format: "esm",
  platform: "browser",
  outfile,
  jsx: "automatic",
  define: {
    "process.env.NODE_ENV": '"test"',
    "process.env.NEXT_PUBLIC_API_URL": '"http://127.0.0.1:8000"',
  },
  alias: {
    "next/navigation": path.join(frontendRoot, "tests/stubs/next-navigation.tsx"),
    "next/link": path.join(frontendRoot, "tests/stubs/next-link.tsx"),
    "next/dynamic": path.join(frontendRoot, "tests/stubs/next-dynamic.tsx"),
    "react/jsx-runtime": path.join(frontendRoot, "tests/stubs/jsx-runtime.tsx"),
    "react/jsx-dev-runtime": path.join(frontendRoot, "tests/stubs/jsx-runtime.tsx"),
  },
  plugins: [
    {
      name: "at-alias",
      setup(build) {
        build.onResolve({ filter: /^@\// }, (args) => {
          const base = path.join(frontendRoot, args.path.slice(2));
          const candidates = [
            base,
            `${base}.tsx`,
            `${base}.ts`,
            `${base}.jsx`,
            `${base}.js`,
            path.join(base, "index.tsx"),
            path.join(base, "index.ts"),
          ];
          const found = candidates.find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile());
          return { path: found ?? base };
        });
      },
    },
  ],
});

const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
  url: "http://127.0.0.1:3000/analytics",
  pretendToBeVisual: true,
});

const { window } = dom;
const assignGlobal = (name, value) => {
  try {
    globalThis[name] = value;
  } catch {
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  }
};
assignGlobal("window", window);
assignGlobal("document", window.document);
assignGlobal("HTMLElement", window.HTMLElement);
assignGlobal("Element", window.Element);
assignGlobal("Node", window.Node);
assignGlobal("navigator", window.navigator);
assignGlobal("DOMParser", window.DOMParser);
assignGlobal("getComputedStyle", window.getComputedStyle.bind(window));
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
window.ResizeObserver = globalThis.ResizeObserver;
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
globalThis.matchMedia = window.matchMedia.bind(window);

await import(pathToFileURL(outfile).href);
