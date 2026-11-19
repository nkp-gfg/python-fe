/**
 * Render the live passenger sheet the operator opens and print the visible
 * text of each itinerary segment. The Python test supplies the detail JSON
 * returned by the passenger-detail route.
 */
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const require = createRequire(import.meta.url);

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
  url: "http://127.0.0.1/",
  pretendToBeVisual: true,
});
const { window } = dom;

function install(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
}

install("window", window);
install("document", window.document);
install("navigator", window.navigator);
install("getComputedStyle", window.getComputedStyle.bind(window));
install("requestAnimationFrame", window.requestAnimationFrame.bind(window));
install("cancelAnimationFrame", window.cancelAnimationFrame.bind(window));
install("MutationObserver", window.MutationObserver);
for (const key of Object.getOwnPropertyNames(window)) {
  if (/Event$/.test(key) || /^(HTML|SVG|Node|Document|Element|CSS|DOM)/.test(key)) {
    install(key, (window as unknown as Record<string, unknown>)[key]);
  }
}
install("IS_REACT_ACT_ENVIRONMENT", true);

const React = require("react");
const { createRoot } = require("react-dom/client");
const { act } = require("react");
const { QueryClient, QueryClientProvider } = require("@tanstack/react-query");
const { PassengerDetailSheet } = require("../components/dashboard/passenger-detail-sheet.tsx");

const detail = JSON.parse(readFileSync(process.argv[2], "utf8"));
const flightNumber = process.argv[3];
const origin = process.argv[4];
const date = process.argv[5];
const pnr = process.argv[6];

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      staleTime: Infinity,
      refetchOnMount: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  },
});
queryClient.setQueryData(
  ["passengerDetail", flightNumber, pnr, origin, date],
  detail,
);

const host = document.createElement("div");
document.body.appendChild(host);
const root = createRoot(host);

await act(async () => {
  root.render(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(PassengerDetailSheet, {
        open: true,
        onOpenChange: () => {},
        flightNumber,
        origin,
        date,
        pnr,
      }),
    ),
  );
});

function isVisible(el: Element): boolean {
  for (let node: Element | null = el; node; node = node.parentElement) {
    if (node.hasAttribute("hidden") || node.getAttribute("aria-hidden") === "true") {
      return false;
    }
  }
  return true;
}

function smallestVisibleText(needles: string[]): string | null {
  const all = [document.body, ...Array.from(document.body.querySelectorAll("*"))];
  let best: Element | null = null;
  let bestLen = Infinity;
  for (const el of all) {
    if (!isVisible(el)) continue;
    const text = el.textContent ?? "";
    if (needles.every((needle) => text.includes(needle)) && text.length < bestLen) {
      best = el;
      bestLen = text.length;
    }
  }
  return best ? (best.textContent ?? "") : null;
}

const segments = detail.passengers[0].itinerary.map((seg: {
  airline: string;
  flight: string;
  seat: string;
  departureDate: string;
  origin: string;
  destination: string;
  bookingClass: string;
  passengerType: string;
}) => {
  const needles = [
    `${seg.airline}${seg.flight}`,
    seg.seat,
    seg.departureDate,
    seg.origin,
    seg.destination,
    `Class: ${seg.bookingClass}`,
  ];
  return {
    flight: `${seg.airline}${seg.flight}`,
    passengerType: seg.passengerType,
    text: smallestVisibleText(needles),
  };
});

process.stdout.write(JSON.stringify({
  bodyHasItinerary: (document.body.textContent ?? "").includes("Itinerary"),
  segments,
}));
