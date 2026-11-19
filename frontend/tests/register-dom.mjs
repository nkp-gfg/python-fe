import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://127.0.0.1:3000/",
});
const { window } = dom;

globalThis.window = window;
globalThis.document = window.document;
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: window.navigator,
});
for (const key of Object.getOwnPropertyNames(window)) {
  if (key in globalThis) continue;
  try {
    globalThis[key] = window[key];
  } catch {
    // Some browser globals are not assignable. The ones the tests touch are.
  }
}
for (const key of [
  "Event",
  "CustomEvent",
  "KeyboardEvent",
  "MouseEvent",
  "PointerEvent",
  "FocusEvent",
  "InputEvent",
  "UIEvent",
]) {
  globalThis[key] = window[key];
}
globalThis.MutationObserver = window.MutationObserver;
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
const idle = new AbortController();
AbortSignal.timeout = () => idle.signal;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: window.localStorage,
});
