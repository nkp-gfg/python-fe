import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
    dedupe: ["react", "react-dom"],
  },
  server: {
    deps: {
      inline: ["react", "react-dom", "next-themes"],
    },
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        url: "http://127.0.0.1:3000/today",
      },
    },
    // The bug only shows when the process calendar day differs from Asia/Bahrain.
    // Vitest workers inherit this from the parent; the npm script sets it as well.
    env: {
      TZ: "America/Los_Angeles",
    },
  },
});
