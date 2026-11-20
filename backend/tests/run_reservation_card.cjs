/**
 * Load the bundled reservation card test after a DOM exists.
 * Usage: node run_reservation_card.cjs <bundle.cjs>
 */
const { JSDOM } = require("jsdom");

const bundlePath = process.argv[2];
if (!bundlePath) {
  console.error("missing bundle path");
  process.exit(2);
}

const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
  url: "http://127.0.0.1:3000/",
});
const { window } = dom;

global.window = window;
global.document = window.document;
global.navigator = window.navigator;
global.HTMLElement = window.HTMLElement;
global.Element = window.Element;
global.Node = window.Node;
global.DocumentFragment = window.DocumentFragment;
global.SVGElement = window.SVGElement;
global.MutationObserver = window.MutationObserver;
global.MouseEvent = window.MouseEvent;
global.Event = window.Event;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.requestAnimationFrame = (callback) => setTimeout(() => callback(Date.now()), 0);
global.cancelAnimationFrame = (handle) => clearTimeout(handle);
global.IS_REACT_ACT_ENVIRONMENT = true;

const { run } = require(bundlePath);
run()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error(error && error.stack ? error.stack : error);
    process.exit(1);
  });
