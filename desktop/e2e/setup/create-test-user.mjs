/**
 * create-test-user.mjs — one-time (idempotent) provisioning of the dedicated
 * E2E test account for the Matrx Local desktop UI test suite.
 *
 * Usage (from desktop/):
 *   node e2e/setup/create-test-user.mjs
 *
 * Behavior:
 *   - Reads VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY from
 *     desktop/.env (same credentials the app itself uses — public signup API,
 *     no service-role key anywhere).
 *   - If desktop/.env.test already exists with TEST_USER_EMAIL/PASSWORD, it
 *     verifies those credentials by signing in and exits (idempotent).
 *   - Otherwise generates a strong random password, calls auth.signUp, then
 *     verifies with auth.signInWithPassword, and writes desktop/.env.test.
 *   - NEVER prints the password to stdout/stderr. It only lands in .env.test
 *     (gitignored).
 *
 * Exit codes: 0 = credentials verified working; 2 = account created/exists
 * but sign-in blocked (email confirmation or signups disabled) — .env.test
 * is still written so the suite can skip gracefully; 1 = hard failure.
 */
import { createClient } from "@supabase/supabase-js";
import { randomBytes } from "node:crypto";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(__dirname, "..", "..");

function parseEnvFile(p) {
  const out = {};
  if (!existsSync(p)) return out;
  for (const line of readFileSync(p, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (m && !line.trim().startsWith("#")) out[m[1]] = m[2];
  }
  return out;
}

const appEnv = parseEnvFile(path.join(desktopDir, ".env"));
const SUPABASE_URL = appEnv.VITE_SUPABASE_URL;
const SUPABASE_KEY = appEnv.VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY;
if (!SUPABASE_URL || !SUPABASE_KEY) {
  console.error("FATAL: VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY missing from desktop/.env");
  process.exit(1);
}

const PRIMARY_EMAIL = "matrx-local-e2e@titaniumsuccess.com";
const FALLBACK_EMAIL = "arman+matrx-local-e2e@titaniumsuccess.com";
const envTestPath = path.join(desktopDir, ".env.test");

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY, {
  auth: { persistSession: false, autoRefreshToken: false },
});

function writeEnvTest(email, password, status) {
  const body = [
    "# Matrx Local E2E test account — used by desktop/e2e/* (Playwright).",
    "# NEVER COMMIT. Managed by desktop/e2e/setup/create-test-user.mjs.",
    `# status: ${status} (${new Date().toISOString()})`,
    `TEST_USER_EMAIL=${email}`,
    `TEST_USER_PASSWORD=${password}`,
    "",
  ].join("\n");
  writeFileSync(envTestPath, body, { mode: 0o600 });
}

async function trySignIn(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  return { ok: !!data?.session && !error, error };
}

async function main() {
  // 1. Idempotent path: verify existing .env.test
  const existing = parseEnvFile(envTestPath);
  if (existing.TEST_USER_EMAIL && existing.TEST_USER_PASSWORD) {
    const { ok, error } = await trySignIn(existing.TEST_USER_EMAIL, existing.TEST_USER_PASSWORD);
    if (ok) {
      console.log(`OK: existing .env.test credentials verified (${existing.TEST_USER_EMAIL})`);
      process.exit(0);
    }
    console.log(`Existing .env.test credentials failed sign-in (${error?.message ?? "no session"}); re-provisioning...`);
  }

  const password = randomBytes(24).toString("base64url"); // 32 chars, high entropy

  for (const email of [PRIMARY_EMAIL, FALLBACK_EMAIL]) {
    console.log(`Attempting signup: ${email}`);
    const { data, error } = await supabase.auth.signUp({ email, password });

    if (error) {
      const msg = error.message.toLowerCase();
      if (msg.includes("already registered") || msg.includes("already exists")) {
        // Account exists from a previous run but we lost the password.
        console.log(`Account ${email} already exists but password unknown.`);
        writeEnvTest(email, password, "EXISTS-PASSWORD-UNKNOWN — Arman must reset password in Supabase dashboard");
        process.exit(2);
      }
      if (msg.includes("invalid") && email === PRIMARY_EMAIL) {
        console.log(`Signup rejected for ${email} (${error.message}); trying fallback address.`);
        continue;
      }
      if (msg.includes("signup") && msg.includes("disabled")) {
        console.log("Signups are disabled on this Supabase instance.");
        writeEnvTest(email, password, "SIGNUPS-DISABLED — Arman must create this user in Supabase dashboard");
        process.exit(2);
      }
      console.error(`Signup failed for ${email}: ${error.message}`);
      if (email === PRIMARY_EMAIL) continue;
      process.exit(1);
    }

    // Signup accepted. Session returned => confirmation not required.
    if (data?.session) {
      writeEnvTest(email, password, "ACTIVE — verified via signup session");
      console.log(`OK: account created with live session (${email}). Credentials written to desktop/.env.test`);
      process.exit(0);
    }

    // No session — email confirmation may be required. Try signing in anyway.
    const { ok } = await trySignIn(email, password);
    if (ok) {
      writeEnvTest(email, password, "ACTIVE — verified via sign-in");
      console.log(`OK: account created and sign-in verified (${email}). Credentials written to desktop/.env.test`);
      process.exit(0);
    }
    writeEnvTest(email, password, "PENDING-EMAIL-CONFIRMATION — Arman must confirm this email (or confirm the user in Supabase dashboard)");
    console.log(`Account created but sign-in blocked pending email confirmation (${email}). Credentials written to desktop/.env.test.`);
    process.exit(2);
  }
  console.error("FATAL: all signup attempts failed");
  process.exit(1);
}

main().catch((e) => {
  console.error("FATAL:", e?.message ?? e);
  process.exit(1);
});
