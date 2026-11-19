import path from "node:path";
import { defineConfig } from "vitest/config";

const frontendRoot = path.resolve(__dirname, "..");

export default defineConfig({
  root: frontendRoot,
  esbuild: {
    jsx: "automatic",
  },
  resolve: {
    alias: {
      "@": frontendRoot,
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/boarding-progress-activity.test.tsx"],
    testTimeout: 20_000,
    fileParallelism: false,
  },
});
