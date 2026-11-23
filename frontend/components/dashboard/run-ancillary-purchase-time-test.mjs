/**
 * Bundle and run the passenger-detail purchase-time render test.
 *
 * jsdom and esbuild are test-only and are not app dependencies. From frontend/:
 *
 *   npm ci
 *   npm install --no-save jsdom@26.1.0 esbuild@0.25.12
 *   node components/dashboard/run-ancillary-purchase-time-test.mjs
 */
import { spawnSync } from "node:child_process";
import { unlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, "../..");
const outfile = path.join(frontend, ".purchase-time-test.mjs");

await build({
  entryPoints: [path.join(here, "passenger-detail-sheet.purchase-time.test.mts")],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile,
  jsx: "automatic",
  alias: { "@": frontend },
  external: ["jsdom"],
  logLevel: "silent",
});

let status = 1;
try {
  const result = spawnSync(process.execPath, [outfile], {
    cwd: frontend,
    stdio: "inherit",
  });
  status = result.status ?? 1;
} finally {
  try {
    unlinkSync(outfile);
  } catch {
    // The bundle is removed after the run.
  }
}

process.exit(status);
