/**
 * rotate-password.mjs — rotate the E2E test account password.
 *
 * Usage (from desktop/):
 *   node e2e/setup/rotate-password.mjs
 *     → signs in with the credentials in desktop/.env.test, sets a fresh
 *       strong random password, and rewrites .env.test.
 *
 *   OLD_PASSWORD=<current> TEST_EMAIL=<email> node e2e/setup/rotate-password.mjs
 *     → bootstrap/recovery mode: use an explicitly provided current password
 *       (e.g. one set via the Supabase dashboard) instead of .env.test.
 *
 * The new password is generated locally and written ONLY to desktop/.env.test
 * (gitignored, mode 600). It is never printed.
 */
import { createClient } from "@supabase/supabase-js";
import { randomBytes } from "node:crypto";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(__dirname, "..", "..");
const envTestPath = path.join(desktopDir, ".env.test");

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
  console.error("FATAL: Supabase vars missing from desktop/.env");
  process.exit(1);
}

const testEnv = parseEnvFile(envTestPath);
const email = process.env.TEST_EMAIL || testEnv.TEST_USER_EMAIL;
const oldPassword = process.env.OLD_PASSWORD || testEnv.TEST_USER_PASSWORD;
if (!email || !oldPassword) {
  console.error("FATAL: no current credentials (need .env.test or TEST_EMAIL/OLD_PASSWORD env vars)");
  process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY, {
  auth: { persistSession: false, autoRefreshToken: false },
});

const { data: signIn, error: signInErr } = await supabase.auth.signInWithPassword({
  email,
  password: oldPassword,
});
if (signInErr || !signIn?.session) {
  console.error(`FATAL: sign-in failed for ${email}: ${signInErr?.message ?? "no session"}`);
  process.exit(1);
}

const newPassword = randomBytes(24).toString("base64url");
const { error: updErr } = await supabase.auth.updateUser({ password: newPassword });
if (updErr) {
  console.error(`FATAL: password update failed: ${updErr.message}`);
  process.exit(1);
}

// Verify the new password actually works before persisting it.
await supabase.auth.signOut();
const { data: verify, error: verifyErr } = await supabase.auth.signInWithPassword({
  email,
  password: newPassword,
});
if (verifyErr || !verify?.session) {
  console.error(`FATAL: new password verification failed: ${verifyErr?.message ?? "no session"}`);
  process.exit(1);
}

writeFileSync(
  envTestPath,
  [
    "# Matrx Local E2E test account — used by desktop/e2e/* (Playwright).",
    "# NEVER COMMIT. Rotate with: node e2e/setup/rotate-password.mjs",
    `# status: ACTIVE — rotated + verified ${new Date().toISOString()}`,
    `TEST_USER_EMAIL=${email}`,
    `TEST_USER_PASSWORD=${newPassword}`,
    "",
  ].join("\n"),
  { mode: 0o600 },
);
console.log(`OK: password rotated and verified for ${email}; written to desktop/.env.test`);
