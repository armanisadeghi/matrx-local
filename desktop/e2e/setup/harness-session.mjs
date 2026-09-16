#!/usr/bin/env node
/**
 * THE HARNESS SIGN-IN (MXL-D-091).
 *
 * Puts the canonical admin test account in front of every screen of Matrx Local, in the DEV
 * WORLD only, without the product regaining a password field.
 *
 * ## What it does
 *
 *   1. Reads `AI_ADMIN_USERNAME` / `AI_ADMIN_PASSWORD`. Never prints, logs or writes either one.
 *   2. Performs the Supabase password grant ITSELF. The password exists in this script's memory
 *      for the length of one HTTPS request and reaches neither the app, the daemon, nor disk.
 *   3. Makes sure a **dev-world** `matrx-syncd` is publishing in a private home, starting one if
 *      it must.
 *   4. Hands the daemon the granted session through its dev-world-only control route
 *      `POST /v1/harness/session`. The daemon installs it exactly as it installs a real sign-in,
 *      so the app adopts an ordinary session with no test-only branch.
 *   5. Verifies `GET /v1/session` really says `signed_in` as that account, and prints the home
 *      directory the Vite dev server must be started with.
 *
 * ## How the harness uses it
 *
 *     eval "$(node desktop/e2e/setup/harness-session.mjs --export)"   # sets MATRX_HOME_DIR
 *     MATRX_HOME_DIR=$MATRX_HOME_DIR pnpm dev                          # the bridge reads it
 *
 * `desktop/vite-plugins/dev-syncd-bridge.ts` then serves that daemon's endpoint and READ token to
 * the browser page, which is the seam Tauri normally provides.
 *
 * ## Why it can never touch the live world
 *
 * It refuses to run against `~/.matrx` (the installed app's home), it starts the daemon with
 * `--world dev`, and the daemon's own two guards refuse the route and the call outside the dev
 * world. `--world live` is not an option this script has.
 *
 * Nothing fails silently: every exit carries the reason and the remedy on stderr.
 */
import { spawn } from "node:child_process";
import { closeSync, existsSync, mkdtempSync, openSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const LIVE_HOME = path.join(os.homedir(), ".matrx");
const DAEMON = path.join(REPO, "desktop", "src-tauri", "target", "debug", "matrx-syncd");

/** Every credential and config source, in the order the repo documents them. */
const ENV_FILES = [
  path.join(REPO, "desktop", ".env.test"),
  path.join(REPO, "desktop", ".env"),
  path.join(REPO, ".env"),
  path.resolve(REPO, "..", "aidream", ".env"),
  path.resolve(REPO, "..", "matrx-frontend", ".env"),
];

function die(reason, remedy) {
  console.error(`\nharness sign-in FAILED: ${reason}\n  Remedy: ${remedy}\n`);
  process.exit(1);
}

function parseEnvFile(file) {
  const out = {};
  if (!existsSync(file)) return out;
  for (const line of readFileSync(file, "utf8").split("\n")) {
    if (line.trim().startsWith("#")) continue;
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (m?.[1] && m[2] !== undefined) {
      out[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  }
  return out;
}

/** Merge the env files, earliest file winning, then the real environment on top. */
function loadEnv() {
  const merged = {};
  for (const file of ENV_FILES) {
    for (const [k, v] of Object.entries(parseEnvFile(file))) {
      if (merged[k] === undefined && v !== "") merged[k] = v;
    }
  }
  for (const key of [
    "AI_ADMIN_USERNAME",
    "AI_ADMIN_PASSWORD",
    "VITE_SUPABASE_URL",
    "VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY",
  ]) {
    if (process.env[key]) merged[key] = process.env[key];
  }
  return merged;
}

function readTokens(home) {
  const lines = readFileSync(path.join(home, "syncd.token"), "utf8").split("\n");
  const control = lines[0]?.trim();
  const read = lines[1]?.trim();
  if (!control || !read) {
    die(
      `${path.join(home, "syncd.token")} does not hold two tokens`,
      "restart the harness daemon; it mints both scoped tokens at every start",
    );
  }
  return { control, read };
}

function readDiscovery(home) {
  const file = path.join(home, "syncd.json");
  if (!existsSync(file)) return null;
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForDaemon(home, budgetMs = 20_000) {
  const deadline = Date.now() + budgetMs;
  while (Date.now() < deadline) {
    const discovery = readDiscovery(home);
    if (
      discovery &&
      typeof discovery.tcp_port === "number" &&
      existsSync(path.join(home, "syncd.token"))
    ) {
      return discovery;
    }
    await sleep(200);
  }
  return null;
}

async function main() {
  const args = process.argv.slice(2);
  const exportMode = args.includes("--export");
  const say = (line) => {
    if (!exportMode) console.log(line);
  };

  // ---------------------------------------------------------------- the home
  let home = process.env.MATRX_HOME_DIR;
  if (home && path.resolve(home) === path.resolve(LIVE_HOME)) {
    die(
      `MATRX_HOME_DIR points at ${LIVE_HOME}, which is the installed app's own home`,
      "unset MATRX_HOME_DIR, or point it at a throwaway directory; the installed app is never a test target",
    );
  }
  if (!home) {
    home = mkdtempSync(path.join(os.tmpdir(), "matrx-harness-"));
  }

  // --------------------------------------------------------- the credentials
  const env = loadEnv();
  const email = env.AI_ADMIN_USERNAME;
  const password = env.AI_ADMIN_PASSWORD;
  const supabaseUrl = env.VITE_SUPABASE_URL;
  const supabaseKey = env.VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY;
  if (!email || !password) {
    die(
      "AI_ADMIN_USERNAME / AI_ADMIN_PASSWORD were not found",
      `add them to one of:\n    ${ENV_FILES.join("\n    ")}\n  They are the shared canonical admin test account — never create a replacement`,
    );
  }
  if (!supabaseUrl || !supabaseKey) {
    die(
      "VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY were not found",
      `copy desktop/.env from the main checkout, or set them in the environment`,
    );
  }

  // ------------------------------------------------------------- the daemon
  let discovery = readDiscovery(home);
  let child = null;
  if (!discovery) {
    if (!existsSync(DAEMON)) {
      die(
        `the sync daemon is not built at ${DAEMON}`,
        "build it with `cargo build -p matrx-syncd` from the repo root",
      );
    }
    say(`starting a dev-world sync daemon in ${home}`);
    // The daemon's stderr goes to a FILE, not to a pipe this process holds: a detached child
    // whose pipe the parent still owns keeps the parent's event loop alive forever, and a setup
    // script that never returns is a harness that hangs with no explanation.
    const logFile = path.join(home, "harness-syncd.err.log");
    const log = openSync(logFile, "a");
    child = spawn(DAEMON, ["--world", "dev"], {
      env: { ...process.env, MATRX_HOME_DIR: home },
      stdio: ["ignore", "ignore", log],
      detached: true,
    });
    child.unref();
    closeSync(log);
    discovery = await waitForDaemon(home);
    if (!discovery) {
      let stderr = "";
      try {
        stderr = readFileSync(logFile, "utf8").trim();
      } catch {
        /* the log may not exist if the binary never started */
      }
      die(
        `the dev-world sync daemon did not publish its endpoint within 20s${stderr ? `:\n${stderr}` : ""}`,
        `check ${logFile} and that no other dev daemon already owns this home, then run this script again`,
      );
    }
  }
  if (discovery.world !== "dev") {
    die(
      `the daemon publishing in ${home} says its world is ${JSON.stringify(discovery.world)}`,
      "point MATRX_HOME_DIR at a dev-world home; the harness never signs a live-world daemon in",
    );
  }
  const port = discovery.tcp_port;
  const { control } = readTokens(home);
  const base = `http://127.0.0.1:${port}`;
  const daemonHeaders = {
    Authorization: `Bearer ${control}`,
    "X-Matrx-Client": "harness",
    "Content-Type": "application/json",
  };

  // -------------------------------------------- the grant (password is HERE)
  let granted;
  try {
    const response = await fetch(`${supabaseUrl}/auth/v1/token?grant_type=password`, {
      method: "POST",
      headers: { apikey: supabaseKey, "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      signal: AbortSignal.timeout(30_000),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok || !body?.access_token || !body?.refresh_token) {
      // The error message is the provider's own. The password is never in it.
      die(
        `Supabase refused the password grant for ${email} (HTTP ${response.status}: ${
          body?.error_description ?? body?.msg ?? body?.error ?? "no reason given"
        })`,
        "report that exact failure — never sign up another account or rotate this shared password locally",
      );
    }
    granted = body;
  } catch (cause) {
    die(
      `the Supabase password grant could not be reached: ${cause instanceof Error ? cause.message : String(cause)}`,
      "check this machine's internet connection and that VITE_SUPABASE_URL is right, then run this again",
    );
  }
  if (granted.user?.email && granted.user.email !== email) {
    die(
      `the grant signed in ${granted.user.email}, not ${email}`,
      "fix AI_ADMIN_USERNAME; all agent testing is admin@admin.com and never a personal account",
    );
  }

  // ------------------------------------------------------------- the install
  let installed;
  try {
    const response = await fetch(`${base}/v1/harness/session`, {
      method: "POST",
      headers: daemonHeaders,
      body: JSON.stringify({
        access_token: granted.access_token,
        refresh_token: granted.refresh_token,
        expires_in: granted.expires_in ?? 3600,
      }),
      signal: AbortSignal.timeout(30_000),
    });
    const body = await response.json().catch(() => null);
    if (response.status === 404) {
      die(
        "this daemon does not serve the harness session route",
        "rebuild it with `cargo build -p matrx-syncd`; the route exists only in builds that carry MXL-D-091's fix, and only in the dev world",
      );
    }
    if (!response.ok) {
      die(
        `the daemon refused the harness session: ${
          body?.error?.message ?? `HTTP ${response.status}`
        }`,
        body?.error?.remedy ?? "read the daemon's log in the harness home's logs/ directory",
      );
    }
    installed = body;
  } catch (cause) {
    die(
      `the daemon at ${base} could not be reached: ${cause instanceof Error ? cause.message : String(cause)}`,
      "check that the harness daemon is still running; its stderr is in the harness home",
    );
  }

  // -------------------------------------------------------------- the proof
  const sessionResponse = await fetch(`${base}/v1/session`, {
    headers: { Authorization: `Bearer ${control}`, "X-Matrx-Client": "harness" },
  });
  const session = await sessionResponse.json().catch(() => null);
  if (!session?.signed_in || session.email !== email) {
    die(
      `the daemon accepted the session but does not report it as signed in (state ${
        session?.state ?? "unknown"
      })`,
      session?.state_reason ?? "run this script again; nothing was left signed in",
    );
  }

  say(`signed in as ${session.email} on the dev-world daemon at ${base}`);
  if (installed.credential_persisted === false) {
    say(
      "note: this computer's credential store would not keep the refresh token, so the session " +
        "ends when the daemon stops. Re-run this script after restarting it.",
    );
  }
  // Record the home so the Vite dev server and the Playwright process find this same daemon
  // without any environment plumbing. Gitignored; overridden by MATRX_HOME_DIR when set.
  const record = path.join(REPO, "desktop", ".harness-home");
  writeFileSync(record, `${home}\n`, { mode: 0o600 });
  say(`recorded in ${record}`);
  say(`MATRX_HOME_DIR=${home}`);
  if (exportMode) {
    process.stdout.write(`export MATRX_HOME_DIR=${home}\n`);
  }
}

await main();
