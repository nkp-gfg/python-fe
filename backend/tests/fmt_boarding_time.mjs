/**
 * Run the History tab's fmtTime on one JSON value.
 *
 * The function body is taken from frontend/components/dashboard/status-history.tsx
 * so this exercises that formatter rather than a reimplementation.
 * Usage: node fmt_boarding_time.mjs '<json>'
 */
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const sourcePath = fileURLToPath(
  new URL("../../frontend/components/dashboard/status-history.tsx", import.meta.url),
);
const source = fs.readFileSync(sourcePath, "utf8");
const start = source.indexOf("function fmtTime(");
const next = source.indexOf("\nfunction ", start + 1);
if (start < 0 || next < 0) {
  console.error("fmtTime was not found in status-history.tsx");
  process.exit(2);
}

const raw = source.slice(start, next);
const javascript = raw.replace(
  /function fmtTime\([^)]*\)\s*:\s*string\s*\{/,
  "function fmtTime(v) {",
);
const context = {};
vm.createContext(context);
vm.runInContext(`${javascript}\nthis.fmtTime = fmtTime;`, context);

let value;
try {
  value = JSON.parse(process.argv[2]);
} catch (error) {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exit(2);
}

try {
  process.stdout.write(String(context.fmtTime(value)));
} catch (error) {
  const message = error instanceof Error ? error.stack : String(error);
  console.error(message);
  process.exit(1);
}
