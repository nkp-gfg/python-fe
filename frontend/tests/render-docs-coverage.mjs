import { register } from "node:module";
import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";

register("./tsx-loader.mjs", import.meta.url);

const source = process.argv[2] === "-"
  ? readFileSync(0, "utf8")
  : readFileSync(process.argv[2], "utf8");
const payload = JSON.parse(source);
const { renderDocsCoverage } = await import("./render-docs-coverage-views.tsx");
const rendered = renderDocsCoverage(payload);
process.stdout.write(JSON.stringify(rendered));
