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
    command: "pnpm dev",
    url: "http://localhost:1420",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
