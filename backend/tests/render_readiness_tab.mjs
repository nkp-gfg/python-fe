/**
 * Render the real Readiness tab from flight-insight-tabs.tsx.
 *
 * Chart, icon, and Next.js dynamic imports are replaced with components that
 * only pass children through. They do not compute readiness. The Operational
 * Ready headline is produced by ReadinessInsightsTab itself.
 *
 * Usage: node render_readiness_tab.mjs <insights.json> <totalPassengers>
 * RENDER_DEPS must point at a directory whose node_modules contains react,
 * react-dom, esbuild, clsx, and tailwind-merge.
 */
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const deps = process.env.RENDER_DEPS;
if (!deps) {
  console.error("RENDER_DEPS is not set");
  process.exit(2);
}

const esbuild = require(path.join(deps, "node_modules", "esbuild"));
const nodeModules = path.join(deps, "node_modules");
const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");
const frontend = path.join(repoRoot, "frontend");
const insightsPath = process.argv[2];
const totalPassengers = Number(process.argv[3]);
if (!insightsPath || !Number.isFinite(totalPassengers)) {
  console.error("Usage: node render_readiness_tab.mjs <insights.json> <totalPassengers>");
  process.exit(2);
}

const stubSource = {
  "lucide-react": `
    import React from "react";
    ${[
      "AlertTriangle", "BarChart3", "Briefcase", "ClipboardCheck", "Clock3",
      "CreditCard", "FileCheck2", "Info", "Luggage", "ShieldAlert", "Ticket",
      "Timer", "TrendingUp", "Users",
    ].map((name) => `export function ${name}() { return React.createElement("span", { "data-icon": "${name}" }); }`).join("\n")}
  `,
  "next/dynamic": `
    const React = require("react");
    function dynamic(_loader, options) {
      const Loading = options && options.loading;
      return function DynamicStub(props) {
        return Loading ? React.createElement(Loading, props) : null;
      };
    }
    module.exports = dynamic;
    module.exports.default = dynamic;
  `,
  recharts: `
    const React = require("react");
    function Pass({ children }) { return React.createElement("div", null, children); }
    module.exports = new Proxy({}, {
      get: (_, prop) => (prop === "__esModule" ? true : Pass),
    });
  `,
  "@/components/ui/badge": `
    const React = require("react");
    function Badge({ children }) { return React.createElement("span", null, children); }
    exports.Badge = Badge;
  `,
  "@/components/ui/chart": `
    const React = require("react");
    function ChartContainer({ children }) { return React.createElement("div", null, children); }
    function ChartTooltip() { return null; }
    function ChartTooltipContent() { return null; }
    exports.ChartContainer = ChartContainer;
    exports.ChartTooltip = ChartTooltip;
    exports.ChartTooltipContent = ChartTooltipContent;
  `,
  "@/components/ui/echarts": `
    const React = require("react");
    function EChart(props) { return React.createElement("div", { "aria-label": props.ariaLabel }); }
    exports.EChart = EChart;
  `,
};

const bundledPackages = new Set([
  "react",
  "react-dom",
  "react-dom/server",
  "react/jsx-runtime",
  "react/jsx-dev-runtime",
  "clsx",
  "tailwind-merge",
]);

function resolveExisting(base) {
  const fs = require("fs");
  const candidates = [
    base,
    `${base}.tsx`,
    `${base}.ts`,
    `${base}.jsx`,
    `${base}.js`,
    path.join(base, "index.ts"),
    path.join(base, "index.tsx"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

const stubPlugin = {
  name: "readiness-stubs",
  setup(build) {
    build.onResolve({ filter: /.*/ }, (args) => {
      if (Object.prototype.hasOwnProperty.call(stubSource, args.path)) {
        return { path: args.path, namespace: "readiness-stub" };
      }
      if (args.path.startsWith("@/")) {
        const resolved = resolveExisting(path.join(frontend, args.path.slice(2)));
        if (!resolved) {
          return { errors: [{ text: `Cannot resolve ${args.path}` }] };
        }
        return { path: resolved };
      }
      if (bundledPackages.has(args.path)) {
        return { path: require.resolve(args.path, { paths: [nodeModules] }) };
      }
      return null;
    });
    build.onLoad({ filter: /.*/, namespace: "readiness-stub" }, (args) => ({
      contents: stubSource[args.path],
      loader: "js",
      resolveDir: nodeModules,
    }));
  },
};

const outfile = path.join(tmpdir(), `readiness-tab-${process.pid}.cjs`);
await esbuild.build({
  stdin: {
    contents: `
      import React from "react";
      import { readFileSync } from "node:fs";
      import { renderToStaticMarkup } from "react-dom/server";
      import { ReadinessInsightsTab } from "./components/dashboard/flight-insight-tabs";
      const insights = JSON.parse(readFileSync(${JSON.stringify(insightsPath)}, "utf8"));
      const html = renderToStaticMarkup(
        React.createElement(ReadinessInsightsTab, {
          insights,
          totalPassengers: ${totalPassengers},
        }),
      );
      module.exports = html;
    `,
    resolveDir: frontend,
    loader: "tsx",
    sourcefile: "render-entry.tsx",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  outfile,
  jsx: "automatic",
  plugins: [stubPlugin],
  logLevel: "silent",
});

process.stdout.write(require(outfile));
