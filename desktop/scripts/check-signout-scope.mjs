#!/usr/bin/env node
/**
 * check:signout-scope — A SIGN-OUT ENDS ONLY THE DEVICE THAT ASKED.
 *
 * supabase-js `signOut()` and GoTrue `POST /logout` default to scope "global":
 * they delete EVERY session the account holds — the web app's, this desktop
 * app's, and every OAuth-client session (Claude Code, claude.ai and Codex hold
 * their AI Dream MCP sign-in as one of these). On 2026-09-15 this app's
 * account switch (admin@admin.com ↔ a personal account) ran a bare signOut()
 * — Supabase auth log 00:43:39Z and 02:01:19Z (65 ms bulk delete) — and each
 * one deleted the Claude Code plugin's session on that account; ~6,050
 * refresh_token_not_found retries followed and every agent lost its MCP tools.
 * Fixed in 4f6fe85be (`use-auth.ts` → `signOut({ scope: "local" })`); this
 * guard keeps it fixed. Same contract as matrx-frontend
 * `scripts/check-signout-scope.ts` and aidream `scripts/check_logout_scope.py`.
 *
 * Findings: `.signOut(` whose argument list carries no literal
 * `scope: "local"` / `"others"`; a GoTrue logout URL without `scope=`; or an
 * explicit global scope anywhere (global is a user-facing "sign out
 * everywhere" decision and must be deliberate — so it is reported).
 *
 * Run:   pnpm check:signout-scope
 * Prove: pnpm check:signout-scope:self-test
 */
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const EXT = /\.(ts|tsx|js|mjs|rs|py|sh)$/;
const SKIP = /(^|\/)(node_modules|dist|target|build|\.next|\.venv|__pycache__)\//;
const SIGN_OUT = /\.(?:signOut|sign_out)\((?<args>[^)]*)\)/g;
const LOGOUT_URL = /(?:\/auth\/v1\/logout|["'`]\/logout)(?<rest>[^"'`\n]*)/g;
const LITERAL_SCOPE = /scope\s*[:=]\s*["'`](?<scope>local|others|global)["'`]/;
const COMMENT = /^\s*(?:\/\/|\*|\/\*|#)/;

export function findingsIn(source, file) {
  const out = [];
  const lines = source.split("\n");
  lines.forEach((line, i) => {
    if (COMMENT.test(line)) return;
    const where = `${file}:${i + 1}`;
    for (const m of line.matchAll(SIGN_OUT)) {
      const scope = LITERAL_SCOPE.exec(m.groups.args ?? "");
      if (!scope) out.push(`${where}: .signOut( without a literal scope (supabase-js defaults to GLOBAL)`);
      else if (scope.groups.scope === "global") out.push(`${where}: explicit scope "global" sign-out`);
    }
    for (const m of line.matchAll(LOGOUT_URL)) {
      const rest = m.groups.rest ?? "";
      if (!/scope=/.test(rest) && !/\{scope\}/.test(rest)) out.push(`${where}: GoTrue logout URL without scope= (defaults to GLOBAL)`);
      else if (/scope=global/.test(rest)) out.push(`${where}: explicit scope=global logout URL`);
    }
  });
  return out;
}

function trackedFiles() {
  return execSync("git ls-files --cached --others --exclude-standard", { cwd: REPO_ROOT, encoding: "utf8" })
    .split("\n")
    .filter((f) => f && EXT.test(f) && !SKIP.test(f) && !/(^|\/)(tests?|__tests__)\//.test(f) && !/\.test\.|self-test/.test(f) && !f.endsWith("check-signout-scope.mjs"));
}

function scan() {
  const findings = [];
  for (const rel of trackedFiles()) {
    let text;
    try { text = readFileSync(resolve(REPO_ROOT, rel), "utf8"); } catch { continue; }
    if (!/signOut|sign_out|logout/.test(text)) continue;
    findings.push(...findingsIn(text, rel));
  }
  return findings;
}

function selfTest() {
  const cases = [
    ["await supabase.auth.signOut();", 1, "bare signOut"],
    ["await supabase.auth.signOut({});", 1, "empty options"],
    ['await supabase.auth.signOut({ scope: SOME_VAR });', 1, "scope in a variable"],
    ['await supabase.auth.signOut({ scope: "global" });', 1, "explicit global"],
    ['await supabase.auth.signOut({ scope: "local" });', 0, "literal local"],
    ['await x.signOut({ scope: "others" });', 0, "literal others"],
    ["fetch(`${url}/auth/v1/logout`, { method: \"POST\" });", 1, "logout URL without scope"],
    ["fetch(`${url}/auth/v1/logout?scope=local`);", 0, "logout URL local"],
    ["// supabase.auth.signOut() must never be bare", 0, "comment"],
  ];
  let bad = 0;
  for (const [src, expected, label] of cases) {
    const got = findingsIn(src, "planted.ts").length;
    const ok = (got > 0) === (expected > 0);
    if (!ok) bad += 1;
    console.log(`  ${ok ? "ok " : "BAD"} ${label}: expected ${expected}, got ${got}`);
  }
  console.log(`check:signout-scope self-test: ${bad === 0 ? "PASS" : `FAIL (${bad})`}`);
  return bad === 0 ? 0 : 1;
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  if (process.argv.includes("--self-test")) process.exit(selfTest());
  const findings = scan();
  if (findings.length) {
    console.error("A SIGN-OUT ENDS ONLY THE DEVICE THAT ASKED — every signOut( names a literal local/others scope.");
    for (const f of findings) console.error("  " + f);
    console.error('\nFix: signOut({ scope: "local" }) / `/auth/v1/logout?scope=local`. A global sign-out deletes every session the account holds, including the OAuth session Claude Code uses for the AI Dream MCP.');
    process.exit(1);
  }
  console.log("check:signout-scope: every sign-out in this repo names a literal local/others scope.");
}
