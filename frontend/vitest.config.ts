import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: { url: "http://127.0.0.1:3000/" },
    },
    testTimeout: 30_000,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
      "next/navigation": path.resolve(__dirname, "tests/stubs/next-navigation.tsx"),
      "next/link": path.resolve(__dirname, "tests/stubs/next-link.tsx"),
      "next/dynamic": path.resolve(__dirname, "tests/stubs/next-dynamic.tsx"),
    },
  },
});
