/**
 * Verify and install the canonical AI Matrx frontend admin credentials for
 * the Matrx Local desktop Playwright suite.
 *
 * Usage (from desktop/):
 *   node e2e/setup/create-test-user.mjs
 *
 * This script deliberately never signs up a replacement user. The canonical
 * login is shared across AI Matrx frontend testing and documented in
 * CLAUDE.md. If it fails, report that failure instead of creating identity
 * drift between repositories.
 */
import { createClient } from "@supabase/supabase-js";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(__dirname, "..", "..");

function parseEnvFile(filePath) {
  const values = {};
  if (!existsSync(filePath)) return values;
  for (const line of readFileSync(filePath, "utf8").split("\n")) {
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (match && !line.trim().startsWith("#"))
      values[match[1]] = match[2].replace(/^(["'])(.*)\1$/, "$2");
  }
  return values;
}

const appEnv = parseEnvFile(path.join(desktopDir, ".env"));
const supabaseUrl = appEnv.VITE_SUPABASE_URL;
const supabaseKey = appEnv.VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY;
if (!supabaseUrl || !supabaseKey) {
  console.error(
    "FATAL: VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_DEFAULT_KEY missing from desktop/.env",
  );
  process.exit(1);
}

const canonicalEmail = "admin@admin.com";
const canonicalPassword =
  process.env.AI_ADMIN_PASSWORD || appEnv.AI_ADMIN_PASSWORD;
if (!canonicalPassword) {
  console.error(
    "FATAL: AI_ADMIN_PASSWORD missing — set it in desktop/.env (gitignored) or the environment. " +
      "Never hardcode the canonical password.",
  );
  process.exit(1);
}
const envTestPath = path.join(desktopDir, ".env.test");
const supabase = createClient(supabaseUrl, supabaseKey, {
  auth: { persistSession: false, autoRefreshToken: false },
});

async function main() {
  const { data, error } = await supabase.auth.signInWithPassword({
    email: canonicalEmail,
    password: canonicalPassword,
  });
  if (error || !data.session) {
    console.error(
      `FATAL: canonical AI Matrx test login failed (${error?.message ?? "no session"}). ` +
        "Do not create a replacement account; report the canonical credential failure.",
    );
    process.exit(1);
  }

  const body = [
    "# Canonical AI Matrx frontend test login — used by desktop/e2e/*.",
    "# Gitignored. Source of truth: CLAUDE.md and docs/UI_TESTING.md.",
    `AI_ADMIN_USERNAME=${canonicalEmail}`,
    `AI_ADMIN_PASSWORD=${canonicalPassword}`,
    "",
  ].join("\n");
  writeFileSync(envTestPath, body, { mode: 0o600 });
  console.log(`OK: canonical frontend credentials verified (${canonicalEmail})`);
}

main().catch((error) => {
  console.error("FATAL:", error?.message ?? error);
  process.exit(1);
});
