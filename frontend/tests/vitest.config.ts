import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  root: path.resolve(__dirname, ".."),
  test: {
    environment: "jsdom",
    setupFiles: [path.resolve(__dirname, "setup.ts")],
    include: ["tests/**/*.test.tsx"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, ".."),
    },
  },
});
