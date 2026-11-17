/**
 * Renders chart options from the product's tooltip builders in headless Chrome
 * and reports whether a reservation string was parsed as HTML.
 *
 * Stdin: JSON array of { name, kind, label }.
 * kind is one of: horizontal, vertical, donut, analytics-donut, pretty-horizontal, cabin-horizontal.
 *
 * Exit 0 when every charted label is shown and none of them becomes an element
 * or runs an error handler. Exit 1 while a label is still inserted as HTML.
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const repoRoot = path.resolve(import.meta.dirname, "..", "..");
const frontendRoot = path.join(repoRoot, "frontend");
const lockPath = path.join(frontendRoot, "package-lock.json");
const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
const echartsVersion = lock.packages["node_modules/echarts"].version;
const depsRoot = path.join(os.tmpdir(), `falconeye-tooltip-xss-echarts-${echartsVersion}`);

function ensureDeps() {
  const echartsPkg = path.join(depsRoot, "node_modules", "echarts", "package.json");
  const esbuildPkg = path.join(depsRoot, "node_modules", "esbuild", "package.json");
  const puppeteerPkg = path.join(depsRoot, "node_modules", "puppeteer-core", "package.json");
  if (fs.existsSync(echartsPkg) && fs.existsSync(esbuildPkg) && fs.existsSync(puppeteerPkg)) {
    const installed = JSON.parse(fs.readFileSync(echartsPkg, "utf8")).version;
    if (installed === echartsVersion) return;
  }
  fs.mkdirSync(depsRoot, { recursive: true });
  if (!fs.existsSync(path.join(depsRoot, "package.json"))) {
    fs.writeFileSync(path.join(depsRoot, "package.json"), '{"private":true}\n');
  }
  const install = spawnSync(
    "npm",
    ["install", "--silent", `echarts@${echartsVersion}`, "esbuild@0.25.5", "puppeteer-core@24.2.0"],
    { cwd: depsRoot, stdio: "inherit" },
  );
  if (install.status !== 0) {
    throw new Error("failed to install the chart renderer dependencies");
  }
}

function sliceBetween(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  if (start < 0) throw new Error(`missing ${startMarker}`);
  const end = source.indexOf(endMarker, start + startMarker.length);
  if (end < 0) throw new Error(`missing ${endMarker}`);
  return source.slice(start, end).trim();
}

function constDeclaration(source, name) {
  const match = source.match(new RegExp(`const ${name} =[^\\n]+`));
  if (!match) throw new Error(`missing const ${name}`);
  return match[0];
}

function chromePath() {
  const candidates = [
    process.env.CHROME_PATH,
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) throw new Error("google-chrome is required to render ECharts tooltips");
  return found;
}

function readStdin() {
  return fs.readFileSync(0, "utf8");
}

ensureDeps();

const analyticsSource = fs.readFileSync(path.join(frontendRoot, "app", "analytics", "page.tsx"), "utf8");
const tabsSource = fs.readFileSync(
  path.join(frontendRoot, "components", "dashboard", "flight-insight-tabs.tsx"),
  "utf8",
);
const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "falconeye-tooltip-xss-"));
const extractedPath = path.join(workDir, "extracted.ts");
fs.writeFileSync(
  extractedPath,
  [
    constDeclaration(analyticsSource, "ECHARTS_TEXT_COLOR"),
    constDeclaration(analyticsSource, "ECHARTS_AXIS_COLOR"),
    constDeclaration(analyticsSource, "ECHARTS_SPLIT_LINE"),
    constDeclaration(analyticsSource, "PALETTE"),
    `export ${sliceBetween(analyticsSource, "function buildDonutOption", "function buildMultiLineAreaOption")}`,
    `export ${sliceBetween(tabsSource, "function prettyLabel", "function shortLabel")}`,
    "",
  ].join("\n"),
);

const cases = JSON.parse(readStdin());
const entryPath = path.join(workDir, "entry.ts");
fs.writeFileSync(
  entryPath,
  `
import * as echarts from "echarts";
import { buildDonutChartOption, buildHorizontalBarOption, buildVerticalBarOption } from ${JSON.stringify(path.join(frontendRoot, "components", "dashboard", "echarts-option-builders.ts"))};
import { buildDonutOption, prettyLabel } from "./extracted.ts";

const cases = ${JSON.stringify(cases)};

function optionFor(entry) {
  const label = entry.label;
  if (entry.kind === "horizontal") {
    return buildHorizontalBarOption({ data: [{ label, value: 2 }], valueLabel: "Count" });
  }
  if (entry.kind === "pretty-horizontal") {
    return buildHorizontalBarOption({ data: [{ label: prettyLabel(label), value: 2 }], valueLabel: "Count" });
  }
  if (entry.kind === "cabin-horizontal") {
    return buildHorizontalBarOption({ data: [{ label: "Cabin " + label, shortLabel: "Cabin " + label, value: 2 }], valueLabel: "Count" });
  }
  if (entry.kind === "vertical") {
    return buildVerticalBarOption({ data: [{ label, value: 2 }], valueLabel: "Passengers" });
  }
  if (entry.kind === "donut") {
    return buildDonutChartOption({ title: "Channels", centerLabel: "Channels", data: [{ label, value: 2 }] });
  }
  if (entry.kind === "analytics-donut") {
    return buildDonutOption([{ name: label, value: 2 }], "FF");
  }
  throw new Error("unknown chart kind " + entry.kind);
}

async function run() {
  const host = document.getElementById("c");
  const results = [];
  for (const entry of cases) {
    window.__tooltipHit = 0;
    const option = optionFor(entry);
    const chart = echarts.init(host, null, { renderer: "svg", width: 640, height: 360 });
    chart.setOption(option);
    chart.dispatchAction({ type: "showTip", seriesIndex: 0, dataIndex: 0 });
    await new Promise((resolve) => setTimeout(resolve, 50));
    const imgs = [...document.querySelectorAll("img")].map((el) => el.outerHTML);
    const tooltipHtml = [...document.querySelectorAll("div")]
      .map((el) => el.innerHTML)
      .filter((html) => html.includes("FALCONXSS") || html.includes("Count:") || html.includes("Passengers:") || html.includes("passengers"))
      .sort((a, b) => a.length - b.length)[0] || "";
    results.push({
      name: entry.name,
      hit: window.__tooltipHit || 0,
      imgCount: imgs.length,
      renderMode: option.tooltip && option.tooltip.renderMode || "",
      showed: tooltipHtml.length > 0 || (option.tooltip && option.tooltip.renderMode === "richText"),
      tooltipHasRawImage: /<\\s*img\\b/i.test(tooltipHtml),
      tooltipHtml: tooltipHtml.slice(0, 500),
    });
    chart.dispose();
    host.replaceChildren();
    document.querySelectorAll("body > div").forEach((el) => {
      if (el.id !== "c") el.remove();
    });
  }
  document.getElementById("out").textContent = JSON.stringify(results);
}
run();
`,
);

const esbuild = (await import(pathToFileURL(path.join(depsRoot, "node_modules", "esbuild", "lib", "main.js")).href)).default;
await esbuild.build({
  entryPoints: [entryPath],
  bundle: true,
  format: "iife",
  outfile: path.join(workDir, "bundle.js"),
  platform: "browser",
  absWorkingDir: workDir,
  nodePaths: [path.join(depsRoot, "node_modules")],
});
fs.writeFileSync(
  path.join(workDir, "page.html"),
  "<!doctype html><html><body><div id=\"c\" style=\"width:640px;height:360px\"></div><pre id=\"out\"></pre><script src=\"bundle.js\"></script></body></html>",
);

const puppeteerModule = await import(pathToFileURL(path.join(depsRoot, "node_modules", "puppeteer-core", "lib", "esm", "puppeteer", "puppeteer-core.js")).href);
const puppeteer = puppeteerModule.default ?? puppeteerModule;
const browser = await puppeteer.launch({
  executablePath: chromePath(),
  headless: true,
  args: ["--no-sandbox", "--disable-gpu"],
});
const page = await browser.newPage();
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));
await page.goto(pathToFileURL(path.join(workDir, "page.html")).href, { waitUntil: "networkidle0" });
await page.waitForFunction(() => document.getElementById("out").textContent.length > 0, { timeout: 20000 });
const results = JSON.parse(await page.$eval("#out", (el) => el.textContent));
await browser.close();

const failures = results.filter((result) => (
  result.hit
  || result.imgCount
  || result.tooltipHasRawImage
  || !result.showed
));
const report = { echartsVersion, failures, results, pageErrors };
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
process.exit(failures.length === 0 ? 0 : 1);
