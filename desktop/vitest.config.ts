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
  define: {
    // `vite.config.ts` compiles this in for the app; unit tests get the same literal so the
    // modules that read it (lib/dev-harness-custody, lib/engine-ports) load at all. `false` is
    // the shipped value, and every test that cares passes the flag explicitly instead.
    __MATRX_HARNESS_BRIDGE__: "false",
  },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "node",
  },
});
