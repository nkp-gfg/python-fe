/**
 * The passenger detail sheet must show each ancillary purchase date and time
 * on the Ancillary Purchases table. Fails until those cells are rendered.
 *
 * Run from frontend/:
 *
 *   npm ci
 *   npm install --no-save jsdom@26.1.0 esbuild@0.25.12
 *   node components/dashboard/run-ancillary-purchase-time-test.mjs
 */
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM(
  "<!DOCTYPE html><html><body><div id=\"root\"></div></body></html>",
  { url: "http://127.0.0.1:3000/" },
);

const windowRef = dom.window;

function installGlobal(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, {
    value,
    configurable: true,
    writable: true,
  });
}

installGlobal("window", windowRef);
installGlobal("document", windowRef.document);
installGlobal("Document", windowRef.Document);
installGlobal("HTMLElement", windowRef.HTMLElement);
installGlobal("Element", windowRef.Element);
installGlobal("Node", windowRef.Node);
installGlobal("DocumentFragment", windowRef.DocumentFragment);
installGlobal("navigator", windowRef.navigator);
installGlobal("MutationObserver", windowRef.MutationObserver);
installGlobal("Event", windowRef.Event);
installGlobal("CustomEvent", windowRef.CustomEvent);
installGlobal("MouseEvent", windowRef.MouseEvent);
installGlobal("KeyboardEvent", windowRef.KeyboardEvent);
installGlobal("PointerEvent", windowRef.PointerEvent);
installGlobal("FocusEvent", windowRef.FocusEvent);
installGlobal("getComputedStyle", windowRef.getComputedStyle.bind(windowRef));
for (const key of Object.getOwnPropertyNames(windowRef)) {
  if (key in globalThis) continue;
  try {
    installGlobal(key, windowRef[key]);
  } catch {
    // Some window properties are not transferable; the render uses the ones that are.
  }
}
installGlobal("IS_REACT_ACT_ENVIRONMENT", true);
installGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
  setTimeout(() => callback(Date.now()), 0),
);
installGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

const detail = {
  airline: "GF",
  flightNumber: "317",
  origin: "CDG",
  departureDate: "2016-03-17",
  fetchedAt: "2016-03-17T12:00:00Z",
  status: "Success",
  completionStatus: "Complete",
  errors: [],
  totalPassengers: 1,
  passengers: [
    {
      lineNumber: 1,
      lastName: "JOHN",
      firstName: "M",
      passengerId: "B7FCF1EE0001",
      pnr: "OLRKTG",
      nameAssociationId: "1",
      groupCode: "",
      vcrNumber: "",
      couponNumber: "",
      gender: "",
      docsData: {},
      baggageRoutes: [],
      requiredInfo: [],
      freeText: [],
      passengerEdits: [],
      timaticInfo: [],
      itinerary: [
        {
          segmentId: "1",
          airline: "AZ",
          flight: "317",
          operatingAirline: "",
          operatingFlight: "",
          marketingAirline: "",
          marketingFlight: "",
          bookingClass: "Y",
          marketingBookingClass: "",
          operatingBookingClass: "",
          origin: "CDG",
          destination: "FCO",
          departureDate: "2016-03-17",
          aircraftType: "32S",
          cabin: "Y",
          seat: "",
          passengerType: "F",
          priority: "",
          bagCount: 1,
          totalBagWeight: "10",
          checkInNumber: "1",
          editCodes: [],
          bagTags: [],
          vcrInfo: [],
          requiredInfo: [],
          aeDetails: [
            {
              itemId: "5",
              groupCode: "BG",
              statusCode: "HD",
              usedEMD: "false",
              quantity: 1,
              price: "30.00",
              currency: "USD",
              purchaseDate: "2016-03-17",
              purchaseTime: "11:59PM",
            },
            {
              itemId: "6",
              groupCode: "SA",
              statusCode: "HK",
              usedEMD: "false",
              quantity: 1,
              price: "0",
              currency: "USD",
              purchaseDate: "2016-03-16",
              purchaseTime: "01:15AM",
            },
          ],
        },
      ],
    },
  ],
};

globalThis.fetch = async () =>
  new windowRef.Response(JSON.stringify(detail), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const React = await import("react");
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { QueryClient, QueryClientProvider } = await import(
  "../../node_modules/@tanstack/react-query/build/modern/index.js"
);
const { PassengerDetailSheet } = await import("./passenger-detail-sheet.tsx");

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      staleTime: Infinity,
      refetchOnMount: false,
      refetchOnWindowFocus: false,
    },
  },
});
queryClient.setQueryData(
  ["passengerDetail", "317", "OLRKTG", "CDG", "2016-03-17"],
  detail,
);

const rootElement = document.getElementById("root");
assert.ok(rootElement);
const root = createRoot(rootElement);

await act(async () => {
  root.render(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(PassengerDetailSheet, {
        open: true,
        onOpenChange: () => {},
        flightNumber: "317",
        origin: "CDG",
        date: "2016-03-17",
        pnr: "OLRKTG",
      }),
    ),
  );
});

const extras = [...document.querySelectorAll("button")].find((button) =>
  button.textContent?.includes("Extras"),
);
assert.ok(extras, "Extras tab was not rendered");

await act(async () => {
  extras.dispatchEvent(
    new windowRef.MouseEvent("mousedown", { bubbles: true, button: 0, buttons: 1 }),
  );
});

const purchaseHeading = [...document.querySelectorAll("[data-slot='card-title']")].find((node) =>
  (node.textContent ?? "").includes("Ancillary Purchases"),
);
assert.ok(purchaseHeading, "Ancillary Purchases section was not rendered");
const purchaseCard = purchaseHeading.closest("[data-slot='card']");
assert.ok(purchaseCard, "Ancillary Purchases card was not rendered");
const purchaseText = purchaseCard.textContent ?? "";

for (const value of ["2016-03-17", "11:59PM", "2016-03-16", "01:15AM", "BG", "SA"]) {
  assert.ok(
    purchaseText.includes(value),
    `Ancillary Purchases table is missing ${value}. Visible text: ${purchaseText}`,
  );
}

root.unmount();
console.log("PASS: passenger detail purchase table shows each sale date and time");
