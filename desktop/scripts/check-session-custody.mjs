#!/usr/bin/env node
/**
 * THE SESSION-CUSTODY GUARD — the webview holds no session, and nothing here believes it does.
 *
 * The custody cutover (FS-C5b) made the sync daemon the device's only session holder. Its proof
 * was a grep somebody ran by hand and pasted into a commit message, which is not a guard: the
 * next file to reintroduce a second session holder would ship unnoticed. This script is that grep
 * with teeth.
 *
 * Two rules, and a violation of either is the split-brain class — a React app that believes it is
 * signed in while the daemon says `signed_out`:
 *
 *  1. No file under `desktop/src` may call `setSession`, or set `persistSession` /
 *     `autoRefreshToken`, or name a `refresh_token` field. There is no refresh token in this
 *     process to persist or rotate.
 *  2. Only `lib/legacy-session-handover.ts` may touch a `sb-…-auth-token` browser-storage entry,
 *     and it exists solely to evacuate the one such entry left by the pre-cutover releases into
 *     daemon custody. When every installed copy has upgraded past that, both it and this
 *     exemption are deleted.
 *
 * Generated API types are exempt from rule 1: they describe the server's OAuth routes, which
 * legitimately have a `refresh_token` field, and this app reads none of them for its own session.
 *
 * `--self-test` plants each violation in a scratch tree and fails unless the scan catches it.
 */

import { readdirSync, readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { tmpdir } from "node:os";

const SRC = new URL("../src/", import.meta.url).pathname;

/** The one file allowed to read a pre-cutover session out of browser storage, and its test. */
const HANDOVER = ["lib/legacy-session-handover.ts", "lib/legacy-session-handover.test.ts"];

/** Generated from the server's OpenAPI document; describes routes, asserts nothing about us. */
const GENERATED = "types/python-generated/";

const RULES = [
  {
    id: "second-session-holder",
    pattern: /\b(setSession|persistSession|autoRefreshToken|refresh_token|refreshToken)\b/,
    exempt: (rel) => rel.startsWith(GENERATED) || HANDOVER.includes(rel),
    message:
      "this process holds no session: the daemon is the device's only session holder, so there " +
      "is nothing here to persist, auto-refresh, or carry a refresh token for",
  },
  {
    id: "supabase-storage-entry",
    pattern: /sb-[^\s"'`]*-auth-token|["'`]sb-\$\{/,
    exempt: (rel) => HANDOVER.includes(rel),
    message:
      "only lib/legacy-session-handover.ts may touch a pre-cutover Supabase storage entry, and " +
      "only to hand it to the daemon",
  },
];

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(full);
    } else if (/\.(ts|tsx)$/.test(entry.name)) {
      yield full;
    }
  }
}

/** Prose and negative assertions are not second session holders.
 *
 *  A real violation is executable code. A comment explaining WHY there is no refresh token here,
 *  and a test asserting a URL does NOT carry one, are the opposite of the thing being guarded
 *  against — so comment lines and `.not.` assertions are skipped, and only code is judged. */
function isProseOrNegation(line) {
  const trimmed = line.trim();
  return (
    trimmed.startsWith("*") ||
    trimmed.startsWith("//") ||
    trimmed.startsWith("/*") ||
    trimmed.includes(".not.") ||
    trimmed.includes("not.toMatch") ||
    trimmed.includes("not.toContain")
  );
}

function scan(root) {
  const findings = [];
  for (const file of walk(root)) {
    const rel = relative(root, file).split(sep).join("/");
    const lines = readFileSync(file, "utf8").split("\n");
    for (const rule of RULES) {
      if (rule.exempt(rel)) continue;
      lines.forEach((line, index) => {
        if (rule.pattern.test(line) && !isProseOrNegation(line)) {
          findings.push({ rel, line: index + 1, rule, text: line.trim() });
        }
      });
    }
  }
  return findings;
}

function report(findings) {
  for (const f of findings) {
    console.error(`${f.rel}:${f.line}  [${f.rule.id}] ${f.rule.message}\n    ${f.text}`);
  }
}

if (process.argv.includes("--self-test")) {
  const root = mkdtempSync(join(tmpdir(), "session-custody-"));
  mkdirSync(join(root, "lib"), { recursive: true });
  mkdirSync(join(root, "types/python-generated"), { recursive: true });

  // Rule 1, planted.
  writeFileSync(
    join(root, "lib/rogue.ts"),
    "// a comment about setSession must not trip the guard\nawait client.auth.setSession(saved);\n",
  );
  // Rule 2, planted.
  writeFileSync(
    join(root, "lib/rogue-storage.ts"),
    'const raw = localStorage.getItem("sb-db-auth-token");\n',
  );
  // Both exemptions, which must NOT be reported.
  writeFileSync(
    join(root, "types/python-generated/api-types.ts"),
    "export type T = { refresh_token: string };\n",
  );
  writeFileSync(
    join(root, "lib/legacy-session-handover.ts"),
    'const LEGACY = /^sb-.+-auth-token$/; const t = parsed.refresh_token;\n',
  );

  const findings = scan(root);
  rmSync(root, { recursive: true, force: true });

  // The prose skip must not have swallowed the real line beneath the comment.
  const rogue = findings.filter((f) => f.rel === "lib/rogue.ts");
  const caught = new Set(findings.map((f) => f.rule.id));
  const wrongly = findings.filter(
    (f) => f.rel.startsWith(GENERATED) || HANDOVER.includes(f.rel),
  );
  const missing = RULES.map((r) => r.id).filter((id) => !caught.has(id));
  if (missing.length || wrongly.length || rogue.length !== 1) {
    console.error("SELF-TEST FAILED");
    if (missing.length) console.error(`  rules that missed a planted violation: ${missing.join(", ")}`);
    if (wrongly.length) console.error(`  exempt files wrongly reported: ${wrongly.map((f) => f.rel).join(", ")}`);
    if (rogue.length !== 1) console.error(`  the prose skip must catch code and skip the comment: ${rogue.length} hit(s) in lib/rogue.ts`);
    process.exit(1);
  }
  console.log(`session-custody guard self-test passed (${RULES.length} rules, each proven catching)`);
  process.exit(0);
}

const findings = scan(SRC);
if (findings.length) {
  console.error(
    `\nTHE SESSION-CUSTODY GUARD found ${findings.length} place(s) where this app would hold a ` +
      `session of its own.\n`,
  );
  report(findings);
  console.error(
    "\nIdentity and tokens come from the daemon through lib/custodian.ts. A React app that " +
      "believes it is signed in while the daemon says signed_out is the split-brain class this " +
      "guard exists to keep closed.\n",
  );
  process.exit(1);
}
console.log("session-custody guard: this app holds no session of its own.");
