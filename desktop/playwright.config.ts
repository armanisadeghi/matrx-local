/**
 * Playwright E2E config for the Matrx Local desktop UI (browser mode).
 *
 * Runs the real React app via `pnpm dev` (Vite, port 1420) in Chromium and
 * logs in with the dedicated Supabase test account from desktop/.env.test.
 * See docs/UI_TESTING.md for the full picture (Tauri-API limitation, engine
 * policy, credential rotation).
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // One worker: a single shared Vite app + a single shared (optional) live
  // engine; parallel logins would just fight Supabase rate limits.
  workers: 1,
  fullyParallel: false,
  // Engine discovery in browser mode port-scans 22140-22159 before the shell
  // renders, so allow generous per-test time.
  timeout: 120_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:1420",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    // SMOKE_PREVIEW=1 tests the REAL production bundle (the artifact we ship)
    // instead of the dev server. Worth the extra build: a boot crash can be
    // introduced by minification/tree-shaking and be invisible in dev.
    // reuseExistingServer must be false there — otherwise a stale `pnpm dev`
    // on 1420 gets tested and the prod bundle silently never runs.
    command: process.env.SMOKE_PREVIEW
      ? "pnpm build && pnpm preview --port 1420 --strictPort"
      : "pnpm dev",
    url: "http://localhost:1420",
    reuseExistingServer: !process.env.SMOKE_PREVIEW,
    timeout: process.env.SMOKE_PREVIEW ? 180_000 : 60_000,
  },
});
