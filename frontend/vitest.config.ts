import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: { url: "http://127.0.0.1:3000" },
    },
    setupFiles: ["./vitest.setup.ts"],
    include: ["components/dashboard/passenger-detail-checkin-sequence.test.tsx"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
});
