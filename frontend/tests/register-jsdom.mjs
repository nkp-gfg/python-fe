import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
});

const { window } = dom;

function install(name, value) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value,
  });
}

install("window", window);
install("document", window.document);
install("navigator", window.navigator);
const skip = new Set([
  "setTimeout",
  "setInterval",
  "clearTimeout",
  "clearInterval",
  "setImmediate",
  "clearImmediate",
  "queueMicrotask",
  "requestAnimationFrame",
  "cancelAnimationFrame",
]);
for (const name of Object.getOwnPropertyNames(window)) {
  if (skip.has(name)) continue;
  let value;
  try {
    value = window[name];
  } catch {
    continue;
  }
  if (typeof value === "function") {
    install(name, value);
  }
}
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
