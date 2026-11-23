import path from "node:path";
import { defineConfig } from "vitest/config";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  esbuild: {
    jsx: "automatic",
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    env: {
      NEXT_PUBLIC_API_URL: apiUrl,
    },
    testTimeout: 20_000,
  },
});
