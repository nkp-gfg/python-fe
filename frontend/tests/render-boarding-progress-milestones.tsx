// @ts-nocheck
/**
 * Render BoardingProgress against the boarding-progress JSON served at
 * NEXT_PUBLIC_API_URL. Writes the card's HTML to MILESTONE_HTML_OUT.
 *
 * The component, fetchBoardingProgress, and react-dom are the app's own code.
 * jsdom only supplies the document the renderer attaches to.
 */
import { writeFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const apiBase = process.env.NEXT_PUBLIC_API_URL;
const htmlOut = process.env.MILESTONE_HTML_OUT;
if (!apiBase || !htmlOut) {
  throw new Error("NEXT_PUBLIC_API_URL and MILESTONE_HTML_OUT are required");
}

const dom = new JSDOM(
  "<!DOCTYPE html><html><body><div id=\"root\"></div></body></html>",
  { url: "http://127.0.0.1:3000/", pretendToBeVisual: true },
);
const { window } = dom;

function installGlobal(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value,
  });
}

installGlobal("window", window);
installGlobal("document", window.document);
installGlobal("navigator", window.navigator);
installGlobal("HTMLElement", window.HTMLElement);
installGlobal("Element", window.Element);
installGlobal("Node", window.Node);
installGlobal("DocumentFragment", window.DocumentFragment);
installGlobal("SVGElement", window.SVGElement);
installGlobal("MutationObserver", window.MutationObserver);
installGlobal("DOMParser", window.DOMParser);
installGlobal("getComputedStyle", window.getComputedStyle.bind(window));
installGlobal("requestAnimationFrame", window.requestAnimationFrame.bind(window));
installGlobal("cancelAnimationFrame", window.cancelAnimationFrame.bind(window));
installGlobal("IS_REACT_ACT_ENVIRONMENT", true);

async function renderCard() {
  const { createRoot } = await import("react-dom/client");
  const { act } = await import("react");
  const { BoardingProgressCardHarness } = await import(
    "./boarding-progress-card-harness"
  );

  const root = createRoot(document.getElementById("root")!);

  await act(async () => {
    root.render(<BoardingProgressCardHarness />);
  });

  const deadline = Date.now() + 10000;
  let html = "";
  while (Date.now() < deadline) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    html = document.getElementById("root")?.innerHTML ?? "";
    const text = document.getElementById("root")?.textContent ?? "";
    if (
      text.includes("Milestones") ||
      text.includes("Failed to load progress") ||
      text.includes("No data available")
    ) {
      break;
    }
  }

  writeFileSync(htmlOut, html, "utf8");
  process.stdout.write(`${document.getElementById("root")?.textContent ?? ""}\n`);

  await act(async () => {
    root.unmount();
  });
}

renderCard().then(
  () => process.exit(0),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);
