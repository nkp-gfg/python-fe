/**
 * Boots a jsdom window, then renders the network analytics page against a
 * fixture produced by the analytics route. DOM globals must exist before
 * React and the page module load.
 */
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const fixturePath = process.argv[2];
if (!fixturePath) {
  console.error("usage: change-events-distribution-boot.mjs <fixture.json>");
  process.exit(2);
}

const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
  url: "http://127.0.0.1:3000/analytics",
  pretendToBeVisual: true,
});
const { window } = dom;
globalThis.window = window;
globalThis.self = window;
globalThis.document = window.document;
Object.defineProperty(globalThis, "navigator", { value: window.navigator, configurable: true });
for (const key of ["HTMLElement", "SVGElement", "Element", "Node", "DocumentFragment", "MutationObserver"]) {
  globalThis[key] = window[key];
}
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
// Do not schedule callbacks. ECharts' animation loop would starve the test.
let rafSeq = 0;
globalThis.requestAnimationFrame = () => ++rafSeq;
globalThis.cancelAnimationFrame = () => {};
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
globalThis.__CHANGE_EVENTS_FIXTURE__ = fixture;
globalThis.fetch = async (url) => {
  const target = String(url);
  const body = target.includes("/analytics/network") ? fixture.network : [];
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

await import("./change-events-distribution-render.tsx");
