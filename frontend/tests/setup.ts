import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// zrender treats a user agent containing "Node.js" as a headless node
// environment and then ignores showTip. jsdom's default agent includes
// that token; the chart under test runs in a browser.
Object.defineProperty(navigator, "userAgent", {
  configurable: true,
  value:
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
}

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// ECharts sizes the canvas from clientWidth/clientHeight. jsdom reports 0.
Object.defineProperty(HTMLElement.prototype, "clientWidth", {
  configurable: true,
  get() {
    return 800;
  },
});
Object.defineProperty(HTMLElement.prototype, "clientHeight", {
  configurable: true,
  get() {
    const styled = Number.parseInt(this.style?.height ?? "", 10);
    return Number.isFinite(styled) && styled > 0 ? styled : 280;
  },
});

afterEach(() => {
  cleanup();
});
