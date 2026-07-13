/**
 * Unit-test config (Vitest). Separate from vite.config.ts because that config
 * exports an async factory for the Tauri dev server, which Vitest has no use
 * for. Scope is deliberately narrow: pure `src/**` logic — the Playwright
 * suite in e2e/ owns anything that needs a browser or a live engine.
 */
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
