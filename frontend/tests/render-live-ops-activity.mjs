import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const entry = path.join(here, "live-ops-activity-entry.tsx");
const outfile = path.join(here, ".cache", "live-ops-activity.mjs");

const OPERATIONAL = ["GATE_CHANGE", "STATUS_CHANGE", "TERMINAL_CHANGE"];

function changeTypeVisible(text, changeType) {
  const spaced = changeType.replace(/([A-Z])/g, " $1").trim();
  const words = changeType.toLowerCase().replace(/_/g, " ");
  return text.includes(spaced) || text.includes(changeType) || text.toLowerCase().includes(words);
}

fs.mkdirSync(path.dirname(outfile), { recursive: true });
await esbuild.build({
  absWorkingDir: frontendRoot,
  entryPoints: [entry],
  bundle: true,
  platform: "node",
  format: "esm",
  jsx: "automatic",
  packages: "external",
  outfile,
  alias: {
    "next/link": path.join(here, "support/next-link-stub.js"),
    "next/navigation": path.join(here, "support/next-navigation-stub.js"),
    "next/dynamic": path.join(here, "support/next-dynamic-stub.js"),
  },
  plugins: [
    {
      name: "at-alias",
      setup(build) {
        build.onResolve({ filter: /^@\// }, async (args) => {
          const target = path.join(frontendRoot, args.path.slice(2));
          return build.resolve(target, {
            resolveDir: frontendRoot,
            kind: "import-statement",
          });
        });
      },
    },
  ],
});

const inputPath = process.argv[2];
if (!inputPath) {
  console.error("usage: node frontend/tests/render-live-ops-activity.mjs <dashboard.json>");
  process.exit(2);
}
const input = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const mod = await import(pathToFileURL(outfile).href);
const rendered = mod.renderLiveOps(input);
const types = Object.keys(input.dashboard?.changeSummary ?? {});
const report = {
  workbenchHasActivityHeading: rendered.workbenchHasActivityHeading,
  insightsHasVelocityHeading: rendered.insightsHasVelocityHeading,
  activityText: rendered.activityText,
  velocityText: rendered.velocityText,
  activity: Object.fromEntries(types.map((type) => [type, changeTypeVisible(rendered.activityText, type)])),
  velocity: Object.fromEntries(types.map((type) => [type, changeTypeVisible(rendered.velocityText, type)])),
  operational: OPERATIONAL,
};
process.stdout.write(JSON.stringify(report));
