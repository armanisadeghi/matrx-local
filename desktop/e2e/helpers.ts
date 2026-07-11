/**
 * Shared helpers for the Matrx Local E2E suite.
 *
 * - Credentials come from desktop/.env.test (gitignored; provisioned by
 *   e2e/setup/create-test-user.mjs, rotated by e2e/setup/rotate-password.mjs).
 * - Engine policy: tests probe ~/.matrx/local.json for a live engine and use
 *   it READ-ONLY (status/list/health endpoints). Never trigger downloads,
 *   generation jobs, or vault mutations against the user's engine.
 */
import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Page } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export interface TestCreds {
  email: string;
  password: string;
}

function parseEnvFile(p: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!existsSync(p)) return out;
  for (const line of readFileSync(p, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    const key = m?.[1];
    const value = m?.[2];
    if (key !== undefined && value !== undefined && !line.trim().startsWith("#")) out[key] = value;
  }
  return out;
}

/** Load the test account from desktop/.env.test, or null if not provisioned. */
export function loadTestCreds(): TestCreds | null {
  const env = parseEnvFile(path.resolve(__dirname, "..", ".env.test"));
  if (env.TEST_USER_EMAIL && env.TEST_USER_PASSWORD) {
    return { email: env.TEST_USER_EMAIL, password: env.TEST_USER_PASSWORD };
  }
  return null;
}

/**
 * Probe for a live local engine via the discovery file (~/.matrx/local.json).
 * Returns the engine base URL if it responds to GET /health, else null.
 * READ-ONLY policy: specs may use a live engine for status/list reads only.
 */
export async function probeEngine(): Promise<string | null> {
  const discovery = path.join(os.homedir(), ".matrx", "local.json");
  if (!existsSync(discovery)) return null;
  try {
    const parsed = JSON.parse(readFileSync(discovery, "utf8")) as {
      url?: string;
    };
    if (!parsed.url) return null;
    const res = await fetch(`${parsed.url}/health`, {
      signal: AbortSignal.timeout(2500),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { status?: string };
    return body.status === "ok" ? parsed.url : null;
  } catch {
    return null;
  }
}

/**
 * Log in through the REAL Login page (email/password form) and wait for the
 * authenticated shell (sidebar nav) to render.
 *
 * Browser-mode timeline after submit: Supabase session → useEngine port scan
 * (22140-22159 via JS fetch) → StartupScreen while "discovering" → AppLayout.
 * With no engine running the scan takes a while before the shell renders with
 * an error status, hence the long timeout.
 */
export async function loginViaUI(page: Page, creds: TestCreds): Promise<void> {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Matrx Local" }),
  ).toBeVisible({ timeout: 45_000 }); // generous: first load compiles the full Vite dep graph
  await page.getByLabel("Email").fill(creds.email);
  await page.getByLabel("Password").fill(creds.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  // Login errors surface inline on the card — fail fast with a useful message
  // instead of timing out on the nav assertion.
  //
  // The Engine Monitor dialog can auto-open at ANY point after auth (engine
  // status flaps to "error" mid port-scan). Radix modals mark the rest of the
  // app aria-hidden, which makes the shell-nav role query unmatchable even
  // though the shell rendered — so dismiss the dialog inside the retry loop,
  // not only after login completes.
  const shellNav = page.getByRole("link", { name: "Dashboard" });
  await expect(async () => {
    await dismissEngineMonitorIfOpen(page);
    await expect(shellNav).toBeVisible({ timeout: 5_000 });
  }).toPass({ timeout: 90_000 });
}

/**
 * Close the Engine Monitor dialog if it auto-opened (it does when the app is
 * authenticated and the engine transitions to "error", e.g. no local engine
 * running in browser mode). Radix dialogs close on Escape.
 */
export async function dismissEngineMonitorIfOpen(page: Page): Promise<void> {
  const dialog = page.getByRole("dialog");
  if (await dialog.first().isVisible().catch(() => false)) {
    await page.keyboard.press("Escape");
    await expect(dialog.first()).toBeHidden({ timeout: 5_000 });
  }
}
