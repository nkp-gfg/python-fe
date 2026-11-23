import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  value: ResizeObserverStub,
});

HTMLCanvasElement.prototype.getContext = function getContext() {
  return new Proxy(
    {},
    {
      get: (_target, prop) => {
        if (prop === "measureText") {
          return () => ({ width: 0 });
        }
        if (prop === "canvas") {
          return this;
        }
        return () => {};
      },
    },
  );
} as unknown as HTMLCanvasElement["getContext"];

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return false;
    },
  }),
});
