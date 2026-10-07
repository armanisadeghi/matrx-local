#!/usr/bin/env node
/**
 * check:org-default-ban — the term "default organization" is retired, only the
 * window's load ladder reads the two account organization columns, and the
 * personal-organization type stays dead.
 *
 * THE RULING (Arman, 2026-10-07; STATE rules 11-14)
 *
 *   The window sets its organization once at load, never none: this device's
 *   last choice -> the account's `last_active_organization_id` ->
 *   `startup_organization_id` -> the first organization. Only
 *   `desktop/src/lib/org/active-org.ts` reads those columns, to choose what the
 *   window opens to. The headless sidecar (Python, Rust, Swift) shows no
 *   organization, keeps rule 13 (the person's choice for the connection, or
 *   ask once) and never reads either column. His 2026-09-19 reason stands:
 *
 *   "one missed org check that should have just failed turns into 50 in a
 *   month and 5,000 in a year, and suddenly we don't have orgs any more, we
 *   have a user and a default org, which means we just have user now."
 *
 * WHAT THIS FAILS ON
 *
 *   0. Any file other than the ladder (`desktop/src/lib/org/active-org.ts`) and
 *      tests naming `last_active_organization_id` / `startup_organization_id`.
 *   1. Any read of `defaultOrganizationId` / `default_organization_id` — the
 *      user-level preference row. The rung this ruling deleted.
 *   2. Any call to the deleted `current_personal_org_id` /
 *      `ensure_personal_organization` RPCs, and ANY `is_personal` /
 *      `isPersonal` in code or a string literal, anywhere it scans (TS, JS,
 *      Python, Swift, Rust). Organizations are unlimited and equal (access
 *      ladder T-3); the column is dropped from `iam.organizations` and the
 *      server's organizations report no longer carries it, so there is no
 *      label, column list, or wire field left that may name it.
 *   3. The phrase "default organization" in user-facing copy — a screen that
 *      says it teaches the user something that does not exist.
 *
 * WHAT IT DELIBERATELY DOES NOT FAIL ON
 *
 *   Comments and Python docstrings. This file, and the resolvers themselves,
 *   have to be able to NAME the thing they ban; a comment reads nothing and
 *   shows nobody anything. Code and string literals are what get scanned.
 *
 *   A declared exemption: `org-default-exempt: <reason, 20+ chars>` on the
 *   offending line or the line above it. A test that has to NAME the banned
 *   endpoint in order to refuse it is the intended use; "it was noisy" is not.
 *
 * WHAT IT CANNOT SEE — say so; never let green imply more than it proves.
 *
 *   It is a text scan over this repo. A preference read hidden behind a
 *   variable (`const key = "default" + "OrganizationId"`), or one living in a
 *   server this repo only calls, reads green here. It proves the SHAPE is
 *   gone from matrx-local; the forcing-function tests in
 *   `desktop/src/lib/org/active-org.test.ts` and `tests/test_organization_resolver.py`
 *   are what prove the BEHAVIOUR.
 *
 * Run:            pnpm check:org-default-ban      (from desktop/)
 * Prove it works: pnpm check:org-default-ban:self-test
 */

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

/**
 * Every language this repo builds a request in. `.swift` and `.rs` were
 * missing, and that is not hypothetical: the macOS AutoFill credential
 * provider (`desktop/native-vault-provider/NativeVaultPassword.swift`) read
 * `default_organization_id` out of the organizations report and built the
 * Vault matches + materialize requests under it, which is a saved password
 * handed out of a tenant nobody chose on this Mac. A guard that only reads the
 * languages the last regression happened in is a guard for that regression.
 */
const SCAN = /\.(ts|tsx|js|jsx|mjs|cjs|py|swift|rs)$/;
/**
 * `src/types/python-generated/` is a verbatim snapshot of the aidream server's
 * OpenAPI schema; it is regenerated, never hand-edited, so a stale field there
 * is fixed by regenerating, not here. It is skipped — and that is a blind spot:
 * code that READS a generated `is_personal` still goes red where it reads it.
 */
const SKIP =
  /(^|\/)(node_modules|dist|build|\.venv|venv|__pycache__|target|src-tauri\/gen|src\/types\/python-generated)\//;

const PREFERENCE_READ = /\bdefault_?[Oo]rganization_?[Ii]d\b/;
const PERSONAL_RPC = /\b(?:current_personal_org_id|ensure_personal_organization)\b/;
/** The retired organization type. Banned in code and literals everywhere. */
const PERSONAL_TOKEN = /\b(is_personal|isPersonal)\b/;
const ACCOUNT_COLUMNS = /\b(?:last_active_organization_id|startup_organization_id)\b/;
/** The ONLY non-test file that may read the two account columns: the load ladder. */
const LADDER_FILE = "desktop/src/lib/org/active-org.ts";
const isLadderOrTest = (where) =>
  where === LADDER_FILE || /\.test\.[cm]?[jt]sx?$/.test(where) || /(^|\/)tests?\//.test(where);
const COPY_PHRASE = /default\s+organization/i;
const EXEMPT = /org-default-exempt:\s*\S.{19,}/;

/** Blank out lines carrying a declared, reasoned exemption. */
function dropExempt(text) {
  const lines = text.split("\n");
  return lines
    .map((line, i) => (EXEMPT.test(line) || EXEMPT.test(lines[i - 1] ?? "") ? "" : line))
    .join("\n");
}

/**
 * Strip comments and Python docstrings — a comment reads nothing. Swift and
 * Rust use the same `//` and comment syntax as TS, so they take the same
 * branch.
 */
function codeOnly(text, isPython) {
  if (isPython) {
    return text
      .replace(/"""[\s\S]*?"""/g, '""')
      .replace(/'''[\s\S]*?'''/g, "''")
      .replace(/(^|\n)\s*#[^\n]*/g, "$1");
  }
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/** String literals only — what a user can actually be shown. */
function stringLiterals(code) {
  const out = [];
  const re = /"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/g;
  let m;
  while ((m = re.exec(code)) !== null) out.push(m[0]);
  return out;
}

export function findingsIn(text, where) {
  const isPython = where.endsWith(".py");
  const code = codeOnly(dropExempt(text), isPython);
  const out = [];
  if (PREFERENCE_READ.test(code)) {
    out.push(
      `${where}: reads a saved default-organization preference — a request may never read it`,
    );
  }
  if (ACCOUNT_COLUMNS.test(code) && !isLadderOrTest(where)) {
    out.push(
      `${where}: reads an account organization column outside the load ladder (${LADDER_FILE})`,
    );
  }
  if (PERSONAL_RPC.test(code)) {
    out.push(`${where}: calls a deleted personal-organization RPC — organizations are all equal`);
  }
  for (const [index, line] of code.split("\n").entries()) {
    if (PERSONAL_TOKEN.test(line)) {
      out.push(
        `${where}:${index + 1}: names is_personal — there is no organization type; organizations are all equal`,
      );
      break;
    }
  }
  for (const literal of stringLiterals(code)) {
    if (COPY_PHRASE.test(literal)) {
      out.push(`${where}: user-facing copy says "default organization" — there is no such thing`);
      break;
    }
  }
  return out;
}

function trackedFiles() {
  return execSync("git ls-files --cached --others --exclude-standard", {
    cwd: REPO_ROOT,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  })
    .split("\n")
    .filter(
      (f) =>
        f &&
        SCAN.test(f) &&
        !SKIP.test(f) &&
        !f.endsWith("check-org-default-ban.mjs"),
    );
}

function scan() {
  const findings = [];
  for (const rel of trackedFiles()) {
    let text;
    try {
      text = readFileSync(resolve(REPO_ROOT, rel), "utf8");
    } catch {
      continue;
    }
    if (
      !PREFERENCE_READ.test(text) &&
      !PERSONAL_RPC.test(text) &&
      !ACCOUNT_COLUMNS.test(text) &&
      !COPY_PHRASE.test(text) &&
      !/is_personal|isPersonal/.test(text)
    )
      continue;
    findings.push(...findingsIn(text, rel));
  }
  return findings;
}

function selfTest() {
  const cases = [
    ["const id = prefs.organization.defaultOrganizationId;", "x.ts", 1, "TS preference read"],
    ['default_id = organization.get("default_organization_id")', "x.py", 1, "py preference read"],
    ['url = f"{BASE}/rpc/current_personal_org_id"', "x.py", 1, "personal org RPC"],
    ['await supabase.rpc("ensure_personal_organization", { p_user_id: id });', "x.ts", 1, "ensure-personal-org RPC"],
    [
      "const personal = organizations.find((o) => o.isPersonal);",
      "planted-resolver.ts",
      1,
      "resolver choosing by isPersonal",
    ],
    [
      "const personal = organizations.find((o) => o.isPersonal);",
      "desktop/src/features/org/OrganizationPickerDialog.tsx",
      1,
      "the same line outside a resolver (still an organization type)",
    ],
    [
      '.select("id,name,is_personal")',
      "desktop/src/lib/org/active-org.ts",
      1,
      "is_personal in a column list inside the resolver",
    ],
    ['throw new Error("No default organization is set.");', "x.ts", 1, "copy in a string"],
    ["// the default organization rung is gone; never read it", "x.ts", 0, "TS comment"],
    ['"""There is no default organization any more."""', "x.py", 0, "py docstring"],
    ["# no default organization is ever read here", "x.py", 0, "py comment"],
    ['label = org.isPersonal ? `${org.name} (personal)` : org.name;', "x.ts", 1, "personal as a label"],
    [
      'principal: org.isPersonal ? "user" : "organization"',
      "desktop/native-vault-provider/NativeVaultPasskey.swift",
      1,
      "the Vault provider choosing a principal by organization type (Swift)",
    ],
    [
      'Set(["id", "name", "is_personal", "abbreviation"])',
      "desktop/native-vault-provider/NativeVaultCodec.swift",
      1,
      "the wire key in a Swift decoder's key set",
    ],
    ["/// organizations carry no is_personal type any more", "x.swift", 0, "Swift doc comment"],
    [
      '// org-default-exempt: fixture names the retired key to prove the decoder refuses it\nlet row = "{\\"is_personal\\":true}"',
      "desktop/native-vault-provider/tests/NativeVaultPasswordCorpus.swift",
      0,
      "declared exemption in a rejection fixture",
    ],
    ["const id = readStoredSelection()?.id ?? null;", "x.ts", 0, "device selection"],
    [
      '.select("last_active_organization_id,startup_organization_id")',
      "desktop/src/lib/org/active-org.ts",
      0,
      "the load ladder reading the account columns (allowed here only)",
    ],
    [
      '.select("last_active_organization_id")',
      "desktop/src/lib/aidream-client.ts",
      1,
      "a second reader of the account columns in the window",
    ],
    [
      'row = prefs.get("startup_organization_id")',
      "app/services/aidream/organization.py",
      1,
      "the headless engine reading the account columns",
    ],
    [
      'await supabase.schema("users").rpc("set_last_active_organization", { p_organization_id: id });',
      "desktop/src/features/org/Anything.tsx",
      0,
      "the write door RPC (not a column read)",
    ],
    [
      '// org-default-exempt: named here only so the fake client can refuse it\nBANNED = ("current_personal_org_id",)',
      "x.py",
      0,
      "declared exemption on the line above",
    ],
    ['BANNED = ("current_personal_org_id",)  # org-default-exempt: short', "x.py", 1, "exemption with no real reason"],
    [
      'if let preferred, let match = organizations.first(where: { $0.id == preferred }) { return match }\nlet id = object["default_organization_id"]',
      "desktop/native-vault-provider/NativeVaultPassword.swift",
      1,
      "the AutoFill provider reading the account default (Swift)",
    ],
    [
      'let organization_id = row.default_organization_id.clone();',
      "desktop/src-tauri/src/syncd.rs",
      1,
      "the Rust side reading the account default",
    ],
  ];
  let bad = 0;
  for (const [src, file, expected, label] of cases) {
    const got = findingsIn(src, file).length;
    const ok = (got > 0) === (expected > 0);
    if (!ok) bad += 1;
    console.log(`  ${ok ? "ok " : "BAD"} ${label}: expected ${expected ? "RED" : "GREEN"}, got ${got} finding(s)`);
  }
  console.log(
    `check:org-default-ban self-test: ${bad === 0 ? "PASS" : `FAIL (${bad})`}\n` +
      "  RED on a preference read (TS + py + Swift + Rust), the personal-org RPC, any\n" +
      "  is_personal/isPersonal in code or a literal (resolver, label, Swift decoder),\n" +
      "  and the phrase in a user-facing string. GREEN on comments and docstrings that\n" +
      "  name the ban, declared exemptions, and this device's own stored selection.",
  );
  return bad === 0 ? 0 : 1;
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  if (process.argv.includes("--self-test")) process.exit(selfTest());
  const findings = scan();
  if (findings.length) {
    console.error(
      "\n🚨 THE ORGANIZATION LADDER CONTRACT IS BROKEN\n\n" +
        "The window sets its organization once at load (device choice, account last active,\n" +
        "start-up organization, first organization). Only the ladder reads the two account\n" +
        "columns; the headless engine never does; \"default organization\" is a retired term.\n",
    );
    for (const f of findings) console.error("  ✗ " + f);
    console.error(
      "\nThe ladder is desktop/src/lib/org/active-org.ts; the engine's headless resolver is\n" +
        "app/services/aidream/organization.py. Arman, 2026-09-19: \"one missed org check that\n" +
        "should have just failed turns into 50 in a month and 5,000 in a year, and suddenly we\n" +
        "don't have orgs any more, we have a user and a default org.\"\n",
    );
    process.exit(1);
  }
  console.log(
    "check:org-default-ban: only the ladder reads the account organization columns, no personal-org\n" +
      '  RPC or fallback, and no "default organization" in user-facing copy.',
  );
}
