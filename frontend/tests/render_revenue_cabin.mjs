/**
 * Render the dashboard surfaces that split booking classes into cabins.
 *
 * The chart widget is replaced with a placeholder. next/dynamic (used by
 * EChart) does not paint during this plain Node render, and the figures
 * under test are the text nodes those tabs compute before the chart.
 */
import { createRequire } from "node:module";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const requireFromFrontend = createRequire(join(frontendRoot, "package.json"));
const ts = requireFromFrontend("typescript");
const React = requireFromFrontend("react");
const { renderToStaticMarkup } = requireFromFrontend("react-dom/server");

const moduleCache = new Map();

function resolveLocal(base) {
  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    `${base}.js`,
    `${base}.mjs`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
    join(base, "index.js"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  throw new Error(`Cannot resolve ${base}`);
}

function loadTs(absPath) {
  const cached = moduleCache.get(absPath);
  if (cached) return cached.exports;

  const source = readFileSync(absPath, "utf8");
  const js = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
      allowSyntheticDefaultImports: true,
    },
    fileName: absPath,
  }).outputText;

  const mod = { exports: {} };
  moduleCache.set(absPath, mod);
  const localRequire = (spec) => {
    if (spec === "@/components/ui/echarts") {
      return {
        EChart() {
          return React.createElement("div", { "data-testid": "echart-placeholder" });
        },
      };
    }
    if (spec.startsWith("@/")) {
      return loadTs(resolveLocal(join(frontendRoot, spec.slice(2))));
    }
    if (spec.startsWith(".")) {
      return loadTs(resolveLocal(resolve(dirname(absPath), spec)));
    }
    return requireFromFrontend(spec);
  };
  const run = new Function("exports", "require", "module", "__filename", "__dirname", js);
  run(mod.exports, localRequire, mod, absPath, dirname(absPath));
  return mod.exports;
}

function component(mod, name) {
  if (typeof mod[name] === "function") return mod[name];
  if (mod.default && typeof mod.default[name] === "function") return mod.default[name];
  if (typeof mod.default === "function" && name === "default") return mod.default;
  throw new Error(`Missing export ${name}`);
}

const dashboardPath = process.argv[2];
if (!dashboardPath) {
  console.error("usage: node render_revenue_cabin.mjs <dashboard.json>");
  process.exit(2);
}

const dashboard = JSON.parse(readFileSync(dashboardPath, "utf8"));
const revenueMod = loadTs(join(frontendRoot, "components/dashboard/revenue-tab.tsx"));
const commercialMod = loadTs(join(frontendRoot, "components/dashboard/flight-insight-tabs.tsx"));
const briefingMod = loadTs(join(frontendRoot, "components/dashboard/decision-briefing.tsx"));

const RevenueTab = component(revenueMod, "RevenueTab");
const CommercialInsightsTab = component(commercialMod, "CommercialInsightsTab");
const DecisionBriefing = component(briefingMod, "DecisionBriefing");

const revenue = renderToStaticMarkup(React.createElement(RevenueTab, { dashboard }));
const commercial = dashboard.insights
  ? renderToStaticMarkup(
      React.createElement(CommercialInsightsTab, {
        insights: dashboard.insights,
        totalPassengers: dashboard.passengerSummary.totalPassengers,
      }),
    )
  : "";
const briefing = renderToStaticMarkup(React.createElement(DecisionBriefing, { dashboard }));

process.stdout.write(JSON.stringify({ revenue, commercial, briefing }));
