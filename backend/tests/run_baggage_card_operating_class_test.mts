/**
 * Registers a DOM, then renders the real passenger-detail sheet.
 * Static imports of React must stay out of this file so the DOM exists first.
 *
 * happy-dom 21.0.4 exposes Window and does not export a global registrar
 * (frontend/node_modules/happy-dom/package.json, lib/index.js). Copy the
 * window's own properties that Node does not already have, then set window
 * and document so React and the sheet share one document.
 */
import { Window } from "happy-dom";

const domWindow = new Window({ url: "http://127.0.0.1:3000/" });
const hostOwned = new Set([
  "window",
  "document",
  "self",
  "globalThis",
  "navigator",
  "getComputedStyle",
  "requestAnimationFrame",
  "cancelAnimationFrame",
]);
for (const key of Object.getOwnPropertyNames(domWindow)) {
  if (hostOwned.has(key) || key in globalThis) continue;
  try {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      get: () => (domWindow as unknown as Record<string, unknown>)[key],
    });
  } catch {
    // Some host properties cannot be redefined.
  }
}
for (const [key, value] of [
  ["window", domWindow],
  ["self", domWindow],
  ["document", domWindow.document],
  ["navigator", domWindow.navigator],
  ["HTMLElement", domWindow.HTMLElement],
  ["Element", domWindow.Element],
  ["Node", domWindow.Node],
  ["DocumentFragment", domWindow.DocumentFragment],
  ["SVGElement", domWindow.SVGElement],
  ["MutationObserver", domWindow.MutationObserver],
] as const) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    writable: true,
    value,
  });
}
for (const [key, value] of [
  ["getComputedStyle", domWindow.getComputedStyle.bind(domWindow)],
  ["requestAnimationFrame", domWindow.requestAnimationFrame.bind(domWindow)],
  ["cancelAnimationFrame", domWindow.cancelAnimationFrame.bind(domWindow)],
  ["Event", domWindow.Event],
  ["CustomEvent", domWindow.CustomEvent],
  ["FocusEvent", domWindow.FocusEvent],
  ["KeyboardEvent", domWindow.KeyboardEvent],
  ["MouseEvent", domWindow.MouseEvent],
  ["PointerEvent", domWindow.PointerEvent],
  ["UIEvent", domWindow.UIEvent],
  ["AnimationEvent", domWindow.AnimationEvent],
] as const) {
  Object.defineProperty(globalThis, key, {
    configurable: true,
    writable: true,
    value,
  });
}

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { runBaggageCardOperatingClassTest } = await import(
  "./baggage_card_operating_class.test.tsx"
);

await runBaggageCardOperatingClassTest();
