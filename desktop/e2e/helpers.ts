/**
 * Shared helpers for the Matrx Local E2E suite.
 *
 * ## Signing in (MXL-D-091)
 *
 * There is no password form in this app any more, and there must not be: the sync daemon is the
 * device's only session holder. So the harness does not type a password into a screen — it hands
 * the DEV-WORLD daemon a session through its own control API, before the browser starts, and the
 * app then adopts an ordinary session. `node e2e/setup/harness-session.mjs` does that and prints
 * the `MATRX_HOME_DIR` the dev server must run with; [`signInViaHarness`] then only waits for the
 * authenticated shell, because the session is already installed.
 *
 * ## The engine, and whose engine it is
 *
 * Everything here reads the **dev world's** home (`MATRX_HOME_DIR`, else `~/.matrx-dev`) and the
 * dev engine band. `~/.matrx` and ports 22140–22159 belong to the installed app — the person
 * using this Mac — and no test ever reads, probes or touches them (matrx-local CLAUDE.md,
 * Hard Rule 9). A dev engine is still used READ-ONLY by specs that need one: status, list and
 * health reads, never a download, a generation job or a vault mutation.
 */
import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, type Page } from "@playwright/test";

/**
 * Where `harness-session.mjs` records the home it signed in, so the Playwright process finds the
 * same daemon the dev server was started against without any environment plumbing. Gitignored.
 */
export const HARNESS_HOME_FILE = "desktop/.harness-home";

/** The DEV world's home — never `~/.matrx`, which is the installed app's. */
export function harnessHome(): string {
  const override = process.env.MATRX_HOME_DIR;
  if (override && override.length > 0) return override;
  const recorded = path.resolve(process.cwd(), ".harness-home");
  try {
    const home = readFileSync(recorded, "utf8").trim();
    if (home.length > 0) return home;
  } catch {
    /* nobody has run the harness script in this checkout yet */
  }
  return path.join(os.homedir(), ".matrx-dev");
}

/** The daemon's identity on this device, or `null` when the harness has not signed it in. */
export interface HarnessIdentity {
  readonly email: string;
  readonly userId: string;
}

/**
 * Ask the dev-world daemon who it is signed in as.
 *
 * This is the gate every authenticated spec uses: it is the actual precondition (a session exists
 * on this device) rather than a proxy for it (a credentials file exists somewhere).
 */
export async function harnessIdentity(): Promise<HarnessIdentity | null> {
  const session = await harnessSession();
  if (!session?.signed_in || !session.email || !session.user_id) return null;
  return { email: session.email, userId: session.user_id };
}

/** What the dev-world daemon's `GET /v1/session` answers, verbatim. */
export interface HarnessSession {
  readonly signed_in?: boolean;
  readonly state?: string;
  readonly state_reason?: string | null;
  readonly email?: string | null;
  readonly user_id?: string | null;
}

/**
 * The dev-world daemon's own session snapshot, or `null` when no dev daemon is answering.
 *
 * A spec that asserts the signed-out screen must assert what THIS device's daemon says, not what a
 * never-signed-in device would say: a device that signed out is told "Sign in again to continue"
 * with the daemon's reason, a fresh one is told "Sign in to your workspace" (C5b-5).
 */
export async function harnessSession(): Promise<HarnessSession | null> {
  const home = harnessHome();
  try {
    const discovery = JSON.parse(
      readFileSync(path.join(home, "syncd.json"), "utf8"),
    ) as { tcp_port?: number; world?: string };
    if (discovery.world !== "dev" || typeof discovery.tcp_port !== "number") return null;
    const token = readFileSync(path.join(home, "syncd.token"), "utf8").split("\n")[1]?.trim();
    if (!token) return null;
    const response = await fetch(`http://127.0.0.1:${discovery.tcp_port}/v1/session`, {
      headers: { Authorization: `Bearer ${token}`, "X-Matrx-Client": "harness" },
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) return null;
    return (await response.json()) as HarnessSession;
  } catch {
    return null;
  }
}

/**
 * The sentence a spec skips with when the device IS signed in and the spec needs it not to be.
 *
 * The unauthenticated screen cannot be asserted on a signed-in device, and pretending otherwise
 * is how a test starts passing for the wrong reason.
 */
export const HARNESS_ALREADY_SIGNED_IN =
  "This device has a dev-world session, so there is no unauthenticated screen to assert. Run " +
  "`node e2e/setup/harness-session.mjs` against a fresh home, or sign this one out, to run it.";

/** The sentence a spec skips with when nobody signed the harness in. */
export const HARNESS_NOT_SIGNED_IN =
  "No dev-world session on this device. Run: node e2e/setup/harness-session.mjs, then start the " +
  "dev server with the MATRX_HOME_DIR it prints (see docs/UI_TESTING.md).";

/**
 * Probe for a DEV-WORLD engine via the discovery file in the harness home.
 * Returns the engine base URL if it responds to GET /health, else null.
 * READ-ONLY policy: specs may use it for status/list reads only.
 *
 * It deliberately never looks in `~/.matrx`: that discovery file belongs to the installed app.
 */
export async function probeEngine(): Promise<string | null> {
  const discovery = path.join(harnessHome(), "local.json");
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
 * Open the app on a device the harness has already signed in, and wait for the authenticated
 * shell (sidebar nav).
 *
 * There is nothing to type: `e2e/setup/harness-session.mjs` installed the session in the
 * dev-world daemon, the dev server's harness bridge hands this page that daemon's endpoint and
 * read token, and the app adopts it exactly as it adopts a real sign-in.
 *
 * Browser-mode timeline: daemon session → useEngine port scan (the DEV band, 22240–22259, via JS
 * fetch) → StartupScreen while "discovering" → AppLayout. With no dev engine running the scan
 * takes a while before the shell renders with an error status, hence the long timeout.
 */
export async function signInViaHarness(page: Page): Promise<void> {
  await page.goto("/");
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
  }).toPass({ timeout: 120_000 });
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
