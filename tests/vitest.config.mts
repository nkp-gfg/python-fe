import path from "node:path";
import { fileURLToPath } from "node:url";

const testsDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(testsDir, "..");
const frontendDir = path.join(repoRoot, "frontend");
const frontendModules = path.join(frontendDir, "node_modules");

export default {
  resolve: {
    alias: [
      { find: /^@\//, replacement: `${frontendDir}/` },
      {
        find: /^@tanstack\/react-query$/,
        replacement: path.join(frontendModules, "@tanstack/react-query"),
      },
      { find: /^date-fns$/, replacement: path.join(frontendModules, "date-fns") },
      { find: /^react-dom$/, replacement: path.join(frontendModules, "react-dom") },
      { find: /^react\/jsx-dev-runtime$/, replacement: path.join(frontendModules, "react/jsx-dev-runtime") },
      { find: /^react\/jsx-runtime$/, replacement: path.join(frontendModules, "react/jsx-runtime") },
      { find: /^react$/, replacement: path.join(frontendModules, "react") },
      { find: /^next\//, replacement: `${path.join(frontendModules, "next")}/` },
    ],
  },
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "react",
  },
  server: {
    fs: { allow: [repoRoot] },
  },
  test: {
    environment: "jsdom",
    root: testsDir,
    include: ["analytics-date-query.test.tsx"],
    server: {
      deps: {
        moduleDirectories: [frontendModules, "node_modules"],
      },
    },
  },
};
