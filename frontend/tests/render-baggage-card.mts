/**
 * Mount the live passenger-detail sheet and print each baggage-route row.
 *
 * Stdin is the JSON object returned by get_passenger_detail. The sheet loads
 * it through fetchPassengerDetail; this script does not paint the table itself.
 */
import { JSDOM } from "jsdom";

const dom = new JSDOM(
  "<!DOCTYPE html><html><body><div id=\"root\"></div></body></html>",
  { url: "http://127.0.0.1:3000/", pretendToBeVisual: true },
);
const { window } = dom;

function defineGlobal(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, {
    value,
    configurable: true,
    writable: true,
  });
}

function installDom() {
  defineGlobal("window", window);
  defineGlobal("document", window.document);
  defineGlobal("navigator", window.navigator);
  defineGlobal("HTMLElement", window.HTMLElement);
  defineGlobal("Element", window.Element);
  defineGlobal("Node", window.Node);
  defineGlobal("DocumentFragment", window.DocumentFragment);
  defineGlobal("SVGElement", window.SVGElement);
  defineGlobal("MutationObserver", window.MutationObserver);
  defineGlobal("Event", window.Event);
  defineGlobal("CustomEvent", window.CustomEvent);
  defineGlobal("KeyboardEvent", window.KeyboardEvent);
  defineGlobal("MouseEvent", window.MouseEvent);
  defineGlobal("FocusEvent", window.FocusEvent);
  defineGlobal("PointerEvent", window.PointerEvent);
  defineGlobal("NodeFilter", window.NodeFilter);
  defineGlobal("getComputedStyle", window.getComputedStyle.bind(window));
  defineGlobal(
    "requestAnimationFrame",
    (cb: FrameRequestCallback) =>
      setTimeout(() => cb(Date.now()), 0) as unknown as number,
  );
  defineGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  defineGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  for (const key of Object.getOwnPropertyNames(window)) {
    if (key in globalThis) continue;
    try {
      defineGlobal(key, (window as unknown as Record<string, unknown>)[key]);
    } catch {
      /* some window properties are not copyable */
    }
  }
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
  const proto = window.Element.prototype as Element & {
    hasPointerCapture?: (id: number) => boolean;
    setPointerCapture?: (id: number) => void;
    releasePointerCapture?: (id: number) => void;
    scrollIntoView?: (arg?: boolean | ScrollIntoViewOptions) => void;
  };
  proto.hasPointerCapture ??= () => false;
  proto.setPointerCapture ??= () => {};
  proto.releasePointerCapture ??= () => {};
  proto.scrollIntoView ??= () => {};
}

installDom();

interface DetailDocument {
  flightNumber: string;
  origin: string;
  departureDate: string;
  passengers: { lastName: string; pnr: string }[];
}

const detail = JSON.parse(await new Response(process.stdin).text()) as DetailDocument;
const passenger = detail.passengers[0];

const mockFetch: typeof fetch = async (input) => {
  const url = String(input);
  if (url.includes("/timeline")) {
    return new Response(JSON.stringify({ events: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response(JSON.stringify(detail), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};
window.fetch = mockFetch;
globalThis.fetch = mockFetch;

const React = await import("react");
const { createRoot } = await import("react-dom/client");
// The sheet is loaded as CJS and binds this build's QueryClientContext.
const { QueryClient, QueryClientProvider } = await import(
  "../node_modules/@tanstack/react-query/build/modern/index.cjs"
);
const { PassengerDetailSheet } = await import(
  "../components/dashboard/passenger-detail-sheet.tsx"
);

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});
const root = createRoot(window.document.getElementById("root")!);

await React.act(async () => {
  root.render(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(PassengerDetailSheet, {
        open: true,
        onOpenChange: () => {},
        flightNumber: detail.flightNumber,
        origin: detail.origin,
        date: detail.departureDate,
        pnr: passenger.pnr,
      }),
    ),
  );
});

async function settle() {
  await React.act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

let loaded = false;
for (let i = 0; i < 40; i++) {
  await settle();
  if (window.document.body.textContent?.includes(passenger.lastName)) {
    loaded = true;
    break;
  }
}
if (!loaded) {
  throw new Error("passenger detail did not render");
}

const baggageTab = [...window.document.querySelectorAll("[role='tab']")].find(
  (el) => el.textContent?.trim() === "Baggage",
);
if (!baggageTab) {
  throw new Error("baggage tab was not rendered");
}
await React.act(async () => {
  baggageTab.dispatchEvent(
    new window.MouseEvent("mousedown", { button: 0, bubbles: true, cancelable: true }),
  );
});
await settle();

const panel = [...window.document.querySelectorAll("[role='tabpanel']")].find(
  (el) => el.textContent?.includes("Baggage Routing"),
);
if (!panel) {
  throw new Error("baggage routing card was not rendered");
}
const rows = [...panel.querySelectorAll("tbody tr")].map((row) =>
  (row.textContent ?? "").replace(/\s+/g, " ").trim(),
);

process.stdout.write(JSON.stringify({ rows }));
process.exit(0);
