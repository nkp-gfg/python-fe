import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    // The suite is the spec for a browser whose local calendar date is not
    // the operations departure date. The process timezone has to be set
    // before Node constructs dates; the npm command also prefixes TZ.
    env: {
      TZ: "Pacific/Honolulu",
    },
    fileParallelism: false,
  },
});
