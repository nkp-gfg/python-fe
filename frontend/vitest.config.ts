import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  esbuild: {
    jsx: "automatic",
  },
  resolve: {
    alias: {
      "@": root,
    },
  },
  test: {
    environment: "jsdom",
    // Station clocks in these tests differ from both UTC digits and this zone
    // (EDT, UTC-4, on 2026-03-30). Set before the worker starts so Date uses it.
    env: {
      TZ: "America/New_York",
    },
  },
});
