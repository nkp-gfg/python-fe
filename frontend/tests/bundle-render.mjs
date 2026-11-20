/**
 * Bundle the History-tab renderer with the app's own path alias.
 * Output stays under node_modules so Node can resolve the app's packages.
 */
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outfile = path.join(frontendDir, "node_modules/.cache/render-cabin-surfaces.cjs");

await esbuild.build({
  entryPoints: [path.join(frontendDir, "tests/render-cabin-surfaces.tsx")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile,
  jsx: "automatic",
  packages: "external",
  plugins: [
    {
      name: "at-alias",
      setup(build) {
        build.onResolve({ filter: /^@\// }, (args) => {
          const base = path.join(frontendDir, args.path.slice(2));
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
          const resolved = candidates.find((candidate) => {
            try {
              return fs.statSync(candidate).isFile();
            } catch {
              return false;
            }
          });
          return { path: resolved ?? base };
        });
      },
    },
  ],
});

console.log(outfile);
