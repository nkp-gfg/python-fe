import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(here, "../..");

const appReact = path.join(frontendRoot, "node_modules/react");
const appReactDom = path.join(frontendRoot, "node_modules/react-dom");

export default defineConfig({
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: [
      { find: "@", replacement: frontendRoot },
      // The app and the test renderer must share the React the frontend installs.
      { find: /^react-dom$/, replacement: appReactDom },
      { find: /^react-dom\/(.*)/, replacement: `${appReactDom}/$1` },
      { find: /^react$/, replacement: appReact },
      { find: /^react\/(.*)/, replacement: `${appReact}/$1` },
    ],
  },
  server: {
    fs: {
      allow: [path.resolve(frontendRoot, "..")],
    },
  },
  test: {
    environment: "jsdom",
    globalSetup: [path.join(here, "global-setup.ts")],
    include: ["change-timeline.history.test.tsx"],
    fileParallelism: false,
    testTimeout: 30_000,
    hookTimeout: 60_000,
  },
});
