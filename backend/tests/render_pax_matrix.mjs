/**
 * Render the passenger matrix the operations board and the comparison page show.
 *
 * The board mounts PassengerTree with mode "matrix". The comparison page mounts
 * PassengerTree with the default mode, which also renders PaxMatrix. Both pass
 * the flight-tree payload through unchanged. This script bundles those components
 * and prints the first table's text cells for each tree on stdin.
 *
 * stdin: { [name]: FlightTree }
 * stdout: { [name]: { matrix, board, compare } } where each value is
 *         [[label, total, pax, staff, m, f, chd, inf], ...]
 */
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, "../../frontend");
const esbuildPath = process.argv[2];
if (!esbuildPath) {
  console.error("usage: node render_pax_matrix.mjs <esbuild-module>");
  process.exit(2);
}
const esbuild = require(esbuildPath);
const trees = JSON.parse(fs.readFileSync(0, "utf8"));

const built = await esbuild.build({
  absWorkingDir: frontend,
  stdin: {
    contents: `
      const React = require("react");
      const { renderToStaticMarkup } = require("react-dom/server");
      const { PaxMatrix } = require("./components/dashboard/pax-matrix.tsx");
      const { PassengerTree } = require("./components/dashboard/passenger-tree.tsx");

      function passengerRows(html) {
        const table = html.match(/<table[\\s\\S]*?<\\/table>/);
        if (!table) throw new Error("passenger matrix rendered no table");
        const body = table[0].match(/<tbody>([\\s\\S]*?)<\\/tbody>/);
        if (!body) throw new Error("passenger matrix rendered no tbody");
        return [...body[1].matchAll(/<tr[\\s\\S]*?<\\/tr>/g)].map((row) => {
          return [...row[0].matchAll(/<t[dh][^>]*>([\\s\\S]*?)<\\/t[dh]>/g)].map((cell) =>
            cell[1].replace(/<[^>]+>/g, "").replace(/\\s+/g, " ").trim()
          );
        });
      }

      function renderTree(tree) {
        return {
          matrix: passengerRows(renderToStaticMarkup(React.createElement(PaxMatrix, { tree }))),
          board: passengerRows(renderToStaticMarkup(React.createElement(PassengerTree, { tree, mode: "matrix" }))),
          compare: passengerRows(renderToStaticMarkup(React.createElement(PassengerTree, { tree }))),
        };
      }

      module.exports = { renderTree };
    `,
    resolveDir: frontend,
    sourcefile: "pax-matrix-render-entry.js",
    loader: "js",
  },
  bundle: true,
  platform: "node",
  format: "cjs",
  jsx: "automatic",
  write: false,
  alias: { "@": frontend },
  logLevel: "silent",
});

const module = { exports: {} };
const fn = new Function("require", "module", "exports", built.outputFiles[0].text);
fn(require, module, module.exports);
const rendered = {};
for (const [name, tree] of Object.entries(trees)) {
  rendered[name] = module.exports.renderTree(tree);
}
process.stdout.write(JSON.stringify(rendered));
