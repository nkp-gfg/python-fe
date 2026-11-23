/**
 * Paint the passenger tree with the app's compiled CSS and the same
 * documentElement class next-themes applies, then measure contrast in Chrome.
 *
 * Usage:
 *   node tests/measure-pax-tree-contrast.mjs <tree.json> <label> <value>
 *
 * Prints one JSON object to stdout.
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import postcss from "postcss";
import tailwind from "@tailwindcss/postcss";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const treePath = process.argv[2];
const label = process.argv[3];
const value = process.argv[4];

if (!treePath || !label || value === undefined) {
  console.error("usage: measure-pax-tree-contrast.mjs <tree.json> <label> <value>");
  process.exit(2);
}

const cssSource = readFileSync(path.join(frontendRoot, "app/globals.css"), "utf8");
const compiled = await postcss([tailwind()]).process(cssSource, {
  from: path.join(frontendRoot, "app/globals.css"),
});

const dir = mkdtempSync(path.join(tmpdir(), "pax-contrast-"));
const fragmentsPath = path.join(dir, "fragments.json");
const localTsx = path.join(frontendRoot, "node_modules", ".bin", "tsx");
const renderScript = path.join(frontendRoot, "tests", "render-passenger-tree.tsx");
const renderCmd = existsSync(localTsx)
  ? [localTsx, renderScript, treePath, fragmentsPath]
  : ["npx", "--yes", "tsx@4.20.6", renderScript, treePath, fragmentsPath];
const rendered = spawnSync(renderCmd[0], renderCmd.slice(1), {
  cwd: frontendRoot,
  encoding: "utf8",
});
if (rendered.status !== 0) {
  console.error(rendered.stdout);
  console.error(rendered.stderr);
  process.exit(rendered.status || 1);
}

const fragments = JSON.parse(readFileSync(fragmentsPath, "utf8"));
const sections = fragments
  .map(
    (fragment) =>
      `<section data-surface="${fragment.surface}" data-mode="${fragment.mode}">${fragment.html}</section>`,
  )
  .join("\n");

const page = `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>${compiled.css}</style>
</head>
<body>
<div id="theme-sentinel" class="bg-card">sentinel</div>
${sections}
<pre id="contrast-result"></pre>
<script>
const EXPECTED_LABEL = ${JSON.stringify(label)};
const EXPECTED_VALUE = ${JSON.stringify(String(value))};

function applyTheme(theme) {
  // next-themes 0.4.6 attribute="class": remove "light" and "dark", then add the resolved theme.
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(theme);
  root.style.colorScheme = theme;
}

const paintCanvas = document.createElement("canvas");
paintCanvas.width = 1;
paintCanvas.height = 1;
const paintCtx = paintCanvas.getContext("2d", { willReadFrequently: true });

function channel(c) {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

function resolveColor(input) {
  paintCtx.clearRect(0, 0, 1, 1);
  paintCtx.fillStyle = "#010203";
  paintCtx.fillStyle = input;
  const serialized = paintCtx.fillStyle;
  paintCtx.fillRect(0, 0, 1, 1);
  const pixel = paintCtx.getImageData(0, 0, 1, 1).data;
  const unresolved = serialized === "#010203" && String(input).trim().toLowerCase() !== "#010203";
  return {
    r: pixel[0],
    g: pixel[1],
    b: pixel[2],
    a: pixel[3] / 255,
    unresolved,
  };
}

function composite(front, back) {
  const a = front.a + back.a * (1 - front.a);
  if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
  const mix = (fc, bc) => (fc * front.a + bc * back.a * (1 - front.a)) / a;
  return { r: mix(front.r, back.r), g: mix(front.g, back.g), b: mix(front.b, back.b), a };
}

function luminance(rgb) {
  return 0.2126 * channel(rgb.r) + 0.7152 * channel(rgb.g) + 0.0722 * channel(rgb.b);
}

function contrast(a, b) {
  const l1 = luminance(a);
  const l2 = luminance(b);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

function stackedPaint(el, property) {
  const layers = [];
  let node = el;
  while (node) {
    const resolved = resolveColor(getComputedStyle(node)[property]);
    if (!resolved.unresolved) layers.push(resolved);
    if (!resolved.unresolved && resolved.a >= 0.99) break;
    node = node.parentElement;
  }
  if (!layers.length) return null;
  let acc = layers[layers.length - 1];
  if (acc.a < 1) acc = composite(acc, { r: 0, g: 0, b: 0, a: 0 });
  for (let i = layers.length - 2; i >= 0; i--) acc = composite(layers[i], acc);
  return acc;
}

function measureTheme(theme) {
  applyTheme(theme);
  const sentinel = stackedPaint(document.getElementById("theme-sentinel"), "backgroundColor");
  const surfaces = [];
  for (const section of document.querySelectorAll("section[data-surface]")) {
    const svgs = [...section.querySelectorAll('svg[aria-label="Passenger breakdown tree diagram"]')];
    const copies = svgs.map((svg, svgIndex) => {
      const group = [...svg.querySelectorAll("g")].find((node) =>
        [...node.querySelectorAll(":scope > text")].some((text) => text.textContent.trim() === EXPECTED_LABEL),
      );
      if (!group) {
        return { svgIndex, found: false };
      }
      const valueText = [...group.querySelectorAll(":scope > text")].find(
        (text) => text.textContent.trim() === EXPECTED_VALUE,
      );
      const rect = group.querySelector(":scope > rect");
      if (!valueText || !rect) {
        return { svgIndex, found: false, hasValue: Boolean(valueText), hasRect: Boolean(rect) };
      }
      const textColor = stackedPaint(valueText, "fill");
      const cardColor = stackedPaint(rect, "fill");
      return {
        svgIndex,
        found: true,
        text: getComputedStyle(valueText).fill,
        card: getComputedStyle(rect).fill,
        textRgb: textColor,
        cardRgb: cardColor,
        contrast: textColor && cardColor ? contrast(textColor, cardColor) : null,
      };
    });
    let matrix = null;
    const totalLabel = [...section.querySelectorAll("td")].find(
      (cell) => cell.textContent.trim() === "Total" && cell.nextElementSibling?.textContent.trim() === EXPECTED_VALUE,
    );
    if (totalLabel) {
      const cell = totalLabel.nextElementSibling;
      const textColor = stackedPaint(cell, "color");
      const cardColor = stackedPaint(cell, "backgroundColor");
      matrix = {
        text: getComputedStyle(cell).color,
        card: getComputedStyle(cell).backgroundColor,
        contrast: contrast(textColor, cardColor),
      };
    }
    surfaces.push({
      surface: section.dataset.surface,
      mode: section.dataset.mode,
      svgCount: svgs.length,
      copies,
      matrix,
    });
  }
  return {
    theme,
    sentinel: {
      background: getComputedStyle(document.getElementById("theme-sentinel")).backgroundColor,
      luminance: luminance(sentinel),
    },
    surfaces,
  };
}

document.getElementById("contrast-result").textContent = JSON.stringify({
  themes: [measureTheme("light"), measureTheme("dark")],
});
</script>
</body>
</html>`;

const pagePath = path.join(dir, "page.html");
writeFileSync(pagePath, page);

const chrome = process.env.CHROME_PATH || "google-chrome";
const chromeProfile = path.join(dir, "chrome");
const child = spawn(
  chrome,
  [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    `--user-data-dir=${chromeProfile}`,
    "--dump-dom",
    pathToFileURL(pagePath).href,
  ],
  { stdio: ["ignore", "pipe", "pipe"] },
);
let dom = "";
let chromeErr = "";
const domReady = new Promise((resolve, reject) => {
  const timer = setTimeout(() => {
    child.kill("SIGKILL");
    resolve();
  }, 20000);
  child.stdout.on("data", (chunk) => {
    dom += chunk.toString();
    if (dom.includes("</pre>")) {
      clearTimeout(timer);
      child.kill("SIGKILL");
      resolve();
    }
  });
  child.stderr.on("data", (chunk) => {
    chromeErr += chunk.toString();
  });
  child.on("error", reject);
  child.on("close", () => {
    clearTimeout(timer);
    resolve();
  });
});
try {
  await domReady;
} catch (error) {
  console.error(String(error));
  console.error(chromeErr);
  process.exit(1);
}
const match = dom.match(/<pre id="contrast-result">([\s\S]*?)<\/pre>/);
if (!match || !match[1].trim()) {
  console.error(chromeErr);
  console.error(dom.slice(0, 2000));
  process.exit(1);
}

const payload = JSON.parse(match[1].replaceAll("&quot;", '"').replaceAll("&amp;", "&").replaceAll("&lt;", "<").replaceAll("&gt;", ">"));
process.stdout.write(JSON.stringify(payload));
