/**
 * Render the live activity card and the change-velocity panel.
 *
 * The dashboard payload is produced by the Python test from the real differ
 * and get_flight_dashboard(). This script only renders those objects through
 * the production components.
 */
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "..");
const entry = path.join(here, "render-change-labels-entry.jsx");

function loadEsbuild() {
  const candidates = [
    process.env.ESBUILD_MODULE,
    path.join(frontendRoot, "node_modules/esbuild"),
    "/tmp/label-tools/node_modules/esbuild",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, "package.json"))) {
      return require(candidate);
    }
  }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "change-label-esbuild-"));
  const installed = spawnSync(
    "npm",
    ["install", "--no-audit", "--no-fund", "--prefix", dir, "esbuild@0.25.0"],
    { stdio: "inherit" },
  );
  if (installed.status !== 0) {
    throw new Error("Could not install esbuild to transform the dashboard components");
  }
  return require(path.join(dir, "node_modules/esbuild"));
}

function resolveWithExtension(base) {
  const candidates = [
    base,
    `${base}.tsx`,
    `${base}.ts`,
    `${base}.jsx`,
    `${base}.js`,
    path.join(base, "index.tsx"),
    path.join(base, "index.ts"),
    path.join(base, "index.js"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return candidate;
    }
  }
  return null;
}

async function bundle() {
  const esbuild = loadEsbuild();
  const outfile = path.join(os.tmpdir(), "render-change-labels-bundle.cjs");
  await esbuild.build({
    absWorkingDir: frontendRoot,
    entryPoints: [entry],
    outfile,
    bundle: true,
    platform: "node",
    format: "cjs",
    jsx: "automatic",
    logLevel: "silent",
    define: {
      "process.env.NODE_ENV": '"production"',
    },
    plugins: [
      {
        name: "frontend-test-aliases",
        setup(build) {
          const stubs = {
            "next/navigation": path.join(here, "mocks/next-navigation.js"),
            "next/link": path.join(here, "mocks/next-link.js"),
            "next/dynamic": path.join(here, "mocks/next-dynamic.js"),
          };
          build.onResolve({ filter: /^next\/(navigation|link|dynamic)$/ }, (args) => ({
            path: stubs[args.path],
          }));
          build.onResolve({ filter: /^@\// }, (args) => {
            const resolved = resolveWithExtension(path.join(frontendRoot, args.path.slice(2)));
            if (!resolved) {
              return { errors: [{ text: `Cannot resolve ${args.path}` }] };
            }
            return { path: resolved };
          });
        },
      },
    ],
  });
  return outfile;
}

const bundled = await bundle();
const { renderChangeLabels } = require(bundled);
const payload = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const cases = payload.cases.map((item) => {
  const rendered = renderChangeLabels(item.dashboard, item.insights, item.selection);
  return { name: item.name, ...rendered };
});
process.stdout.write(JSON.stringify({ cases }));
