import { Window } from "happy-dom";

const window = new Window({ url: "http://127.0.0.1:3000/" });
const document = window.document;

function assignGlobal(key, value) {
  try {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value,
    });
  } catch {
    // Node owns a few globals, such as navigator, with a non-configurable getter.
  }
}

assignGlobal("window", window);
assignGlobal("document", document);
assignGlobal("navigator", window.navigator);
assignGlobal("IS_REACT_ACT_ENVIRONMENT", true);

for (const key of Object.getOwnPropertyNames(window)) {
  if (key in globalThis) continue;
  assignGlobal(key, window[key]);
}

for (const key of [
  "Event",
  "CustomEvent",
  "MouseEvent",
  "KeyboardEvent",
  "FocusEvent",
  "PointerEvent",
  "InputEvent",
  "UIEvent",
  "EventTarget",
]) {
  if (typeof window[key] === "function") assignGlobal(key, window[key]);
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
