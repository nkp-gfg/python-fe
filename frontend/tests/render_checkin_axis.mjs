/**
 * Render ReadinessInsightsTab and the ECharts option it builds.
 *
 * stdin: { "insights": FlightInsights, "totalPassengers": number }
 * stdout: {
 *   categories, series, tooltipNames, tooltips, svgCategoryLabels
 * }
 *
 * The chart component and buildCheckInTimelineAreaOption are the product
 * sources. UI chrome is stubbed so this can run under Node. ECharts 6 (the
 * version in frontend/package-lock.json) paints the category axis and
 * resolves tooltip names.
 */
import { createRequire } from "node:module";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const requireFromFrontend = createRequire(join(frontendRoot, "package.json"));

function loadEsbuild() {
  const candidates = [
    process.env.ESBUILD_MODULE,
    "/tmp/python-fe-esbuild/node_modules/esbuild/lib/main.js",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      return requireFromFrontend(candidate);
    } catch {
      // try the next location
    }
  }
  try {
    return requireFromFrontend("esbuild");
  } catch (err) {
    throw new Error(
      "esbuild is not installed. Install it with: npm install --prefix /tmp/python-fe-esbuild esbuild@0.25.12",
      { cause: err },
    );
  }
}

const esbuild = loadEsbuild();
const work = mkdtempSync(join(tmpdir(), "checkin-axis-"));
const stubPath = join(work, "stubs.jsx");
const entryPath = join(work, "entry.jsx");
const outfile = join(work, "bundle.cjs");

writeFileSync(
  stubPath,
  `
const React = require("react");
function passthrough(props) { return React.createElement("div", props); }
function icon(props) { return React.createElement("svg", props); }
exports.cn = function cn() { return ""; };
exports.Card = passthrough;
exports.CardContent = passthrough;
exports.CardHeader = passthrough;
exports.CardTitle = passthrough;
exports.CardDescription = passthrough;
exports.Badge = passthrough;
exports.ChartContainer = passthrough;
exports.ChartTooltip = function ChartTooltip() { return null; };
exports.ChartTooltipContent = function ChartTooltipContent() { return null; };
exports.EChart = function EChart(props) {
  const captured = global.__capturedChartOptions || (global.__capturedChartOptions = []);
  captured.push({ ariaLabel: props.ariaLabel, option: props.option });
  return React.createElement("div", { "data-aria-label": props.ariaLabel });
};
exports.AlertTriangle = icon;
exports.BarChart3 = icon;
exports.Briefcase = icon;
exports.ClipboardCheck = icon;
exports.Clock3 = icon;
exports.CreditCard = icon;
exports.FileCheck2 = icon;
exports.Info = icon;
exports.Luggage = icon;
exports.ShieldAlert = icon;
exports.Ticket = icon;
exports.Timer = icon;
exports.TrendingUp = icon;
exports.Users = icon;
exports.Bar = function Bar() { return null; };
exports.BarChart = function BarChart() { return null; };
exports.Pie = function Pie() { return null; };
exports.PieChart = function PieChart() { return null; };
exports.XAxis = function XAxis() { return null; };
exports.YAxis = function YAxis() { return null; };
`,
);

const tabsPath = join(frontendRoot, "components/dashboard/flight-insight-tabs.tsx");
const buildersPath = join(frontendRoot, "components/dashboard/echarts-option-builders.ts");

writeFileSync(
  entryPath,
  `
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { ReadinessInsightsTab } = require(${JSON.stringify(tabsPath)});
const echarts = require(process.env.ECHARTS_ENTRY);

const payload = JSON.parse(require("fs").readFileSync(0, "utf8"));
global.__capturedChartOptions = [];
renderToStaticMarkup(React.createElement(ReadinessInsightsTab, {
  insights: payload.insights,
  totalPassengers: payload.totalPassengers,
}));
const hit = global.__capturedChartOptions.find((item) => item.ariaLabel === "Check-in Timeline");
if (!hit) {
  console.error(JSON.stringify(global.__capturedChartOptions.map((item) => item.ariaLabel)));
  process.exit(2);
}
const axis = hit.option.xAxis;
const categories = Array.isArray(axis) ? axis[0].data : axis.data;
const series = hit.option.series[0].data;
const formatter = hit.option.tooltip.formatter;

const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 720, height: 280 });
chart.setOption(hit.option);
const svg = chart.renderToSVGString();
const svgCategoryLabels = Array.from(svg.matchAll(/<text[^>]*>([^<]*)<\\/text>/g))
  .map((match) => match[1])
  .filter((text) => /^\\d{2}:\\d{2}(?::\\d{2})?$/.test(text));
const seriesModel = chart.getModel().getSeriesByIndex(0);
const tooltipNames = [];
const tooltips = [];
for (let i = 0; i < categories.length; i += 1) {
  const params = seriesModel.getDataParams(i);
  tooltipNames.push(params.name);
  tooltips.push(formatter([params]));
}
chart.dispose();
process.stdout.write(JSON.stringify({ categories, series, tooltipNames, tooltips, svgCategoryLabels }));
`,
);

await esbuild.build({
  absWorkingDir: frontendRoot,
  entryPoints: [entryPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  outfile,
  jsx: "automatic",
  nodePaths: [join(frontendRoot, "node_modules")],
  plugins: [
    {
      name: "stub-ui",
      setup(build) {
        build.onResolve({ filter: /.*/ }, (args) => {
          if (args.path === tabsPath || args.path.endsWith("flight-insight-tabs.tsx")) return;
          if (args.path === buildersPath || args.path.endsWith("echarts-option-builders.ts")) return;
          if (args.path === "@/components/dashboard/echarts-option-builders") {
            return { path: buildersPath };
          }
          if (
            args.path.startsWith("@/") ||
            args.path === "lucide-react" ||
            args.path === "recharts"
          ) {
            return { path: stubPath };
          }
          return undefined;
        });
      },
    },
  ],
});

const { spawnSync } = await import("node:child_process");
const echartsEntry = requireFromFrontend.resolve("echarts");
const stdin = await new Promise((resolve, reject) => {
  const chunks = [];
  process.stdin.on("data", (chunk) => chunks.push(chunk));
  process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  process.stdin.on("error", reject);
});
const run = spawnSync(process.execPath, [outfile], {
  input: stdin,
  encoding: "utf8",
  env: { ...process.env, ECHARTS_ENTRY: echartsEntry },
});
rmSync(work, { recursive: true, force: true });
if (run.status !== 0) {
  process.stderr.write(run.stderr || "");
  process.exit(run.status || 1);
}
process.stdout.write(run.stdout);
