import { createRequire } from "node:module"

const depsRoot = process.env.CHART_TEST_DEPS
if (!depsRoot) {
  throw new Error("CHART_TEST_DEPS must point at a directory whose node_modules contains jsdom")
}
const require = createRequire(`${depsRoot}/package.json`)
const { JSDOM } = require("jsdom")

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  pretendToBeVisual: true,
  url: "http://localhost/",
})

const { window } = dom

function expose(name, value) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value,
  })
}

expose("window", window)
expose("document", window.document)
expose("navigator", window.navigator)
expose("HTMLElement", window.HTMLElement)
expose("HTMLDivElement", window.HTMLDivElement)
expose("HTMLCanvasElement", window.HTMLCanvasElement)
expose("SVGElement", window.SVGElement)
expose("Node", window.Node)
expose("Element", window.Element)
expose("DocumentFragment", window.DocumentFragment)
expose("getComputedStyle", window.getComputedStyle.bind(window))
expose("requestAnimationFrame", (callback) => setTimeout(() => callback(Date.now()), 16))
expose("cancelAnimationFrame", (id) => clearTimeout(id))
expose("ResizeObserver", class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
})
expose("IS_REACT_ACT_ENVIRONMENT", true)

Object.defineProperty(window.HTMLElement.prototype, "clientWidth", {
  configurable: true,
  get() {
    return 640
  },
})
Object.defineProperty(window.HTMLElement.prototype, "clientHeight", {
  configurable: true,
  get() {
    return 360
  },
})

function createCanvas2dContext(canvas) {
  const gradient = { addColorStop() {} }
  const context = {
    canvas,
    font: "12px sans-serif",
    fillStyle: "#000",
    strokeStyle: "#000",
    textAlign: "left",
    textBaseline: "alphabetic",
    lineWidth: 1,
    globalAlpha: 1,
    measureText(text) {
      return { width: String(text ?? "").length * 8 }
    },
    getImageData() {
      return { data: new Uint8ClampedArray(4), width: 1, height: 1 }
    },
    createLinearGradient() {
      return gradient
    },
    createRadialGradient() {
      return gradient
    },
    createPattern() {
      return {}
    },
    getTransform() {
      return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }
    },
  }
  return new Proxy(context, {
    get(target, property) {
      if (property in target) return target[property]
      return () => {}
    },
    set(target, property, value) {
      target[property] = value
      return true
    },
  })
}

window.HTMLCanvasElement.prototype.getContext = function getContext() {
  if (!this.__context2d) this.__context2d = createCanvas2dContext(this)
  return this.__context2d
}

window.matchMedia = () => ({
  matches: false,
  media: "",
  onchange: null,
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() {
    return false
  },
})
expose("matchMedia", window.matchMedia)
