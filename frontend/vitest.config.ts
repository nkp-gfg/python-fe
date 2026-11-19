import path from "node:path"
import { defineConfig } from "vitest/config"

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  test: {
    environment: "jsdom",
    include: ["components/ui/echarts-stale-option.test.tsx"],
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
})
