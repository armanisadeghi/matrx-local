#!/usr/bin/env node
/**
 * check:org-default-ban — nothing that builds a request may read a saved
 * "default organization", and nothing may fall back to the personal org.
 *
 * THE RULING (Arman, 2026-09-19)
 *
 *   A "default organization" is at most a per-client DISPLAY preference. A
 *   client MAY remember the organization the user THEMSELVES SET on this
 *   device. If nothing is set, the request is HELD, the picker is shown, the
 *   user SETS one, and the request proceeds. Never fail with "no default
 *   organization".
 *
 *   "one missed org check that should have just failed turns into 50 in a
 *   month and 5,000 in a year, and suddenly we don't have orgs any more, we
 *   have a user and a default org, which means we just have user now."
 *
 * WHAT THIS FAILS ON
 *
 *   1. Any read of `defaultOrganizationId` / `default_organization_id` — the
 *      user-level preference row. The rung this ruling deleted.
 *   2. Any `current_personal_org_id` call, or a `resolve`/`pick`-shaped
 *      function choosing by `is_personal` / `isPersonal`. The personal org is
 *      a LABEL on a row, never an answer to "which organization is this
 *      request for".
 *   3. The phrase "default organization" in user-facing copy — a screen that
 *      says it teaches the user something that does not exist.
 *
 * WHAT IT DELIBERATELY DOES NOT FAIL ON
 *
 *   Comments and Python docstrings. This file, and the resolvers themselves,
 *   have to be able to NAME the thing they ban; a comment reads nothing and
 *   shows nobody anything. Code and string literals are what get scanned.
 *   `isPersonal` as a display flag (the picker's "(personal)" suffix) is
 *   fine — only a resolver CHOOSING by it is not.
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
const SKIP =
  /(^|\/)(node_modules|dist|build|\.venv|venv|__pycache__|target|src-tauri\/gen)\//;

const PREFERENCE_READ = /\bdefault_?[Oo]rganization_?[Ii]d\b/;
const PERSONAL_RPC = /\bcurrent_personal_org_id\b/;
/**
 * The two files that answer "which organization is this request for". Only
 * here is `is_personal` in a BOOLEAN position a defect: everywhere else the
 * flag is a label (the picker's "(personal)" suffix, a column list).
 */
const RESOLVER_FILES =
  /(desktop\/src\/lib\/org\/active-org|app\/services\/aidream\/organization)\.(ts|py)$/;
const PERSONAL_TOKEN = /\b(is_personal|isPersonal)\b/;
const BOOLEAN_POSITION = /(\bif\s*\(|\.find\(|\.filter\(|&&|\|\|)/;
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
  if (PERSONAL_RPC.test(code)) {
    out.push(`${where}: current_personal_org_id — the personal org is not a fallback`);
  }
  if (RESOLVER_FILES.test(where) || where.startsWith("planted-resolver")) {
    for (const [index, line] of code.split("\n").entries()) {
      if (PERSONAL_TOKEN.test(line) && BOOLEAN_POSITION.test(line)) {
        out.push(
          `${where}:${index + 1}: the resolver chooses by is_personal — the personal org is a label, not an answer`,
        );
        break;
      }
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
    [
      "const personal = organizations.find((o) => o.isPersonal);",
      "planted-resolver.ts",
      1,
      "resolver choosing by isPersonal",
    ],
    [
      "const personal = organizations.find((o) => o.isPersonal);",
      "desktop/src/features/org/OrganizationPickerDialog.tsx",
      0,
      "the same line outside a resolver (a label, not an answer)",
    ],
    [
      '.select("id,name,is_personal")',
      "desktop/src/lib/org/active-org.ts",
      0,
      "is_personal in a column list inside the resolver",
    ],
    ['throw new Error("No default organization is set.");', "x.ts", 1, "copy in a string"],
    ["// the default organization rung is gone; never read it", "x.ts", 0, "TS comment"],
    ['"""There is no default organization any more."""', "x.py", 0, "py docstring"],
    ["# no default organization is ever read here", "x.py", 0, "py comment"],
    ['label = org.isPersonal ? `${org.name} (personal)` : org.name;', "x.ts", 0, "personal as a label"],
    ["const id = readStoredSelection()?.id ?? null;", "x.ts", 0, "device selection"],
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
      "  RED on a preference read (TS + py), the personal-org RPC, a resolver choosing\n" +
      "  by is_personal, and the phrase in a user-facing string. GREEN on comments and\n" +
      "  docstrings that name the ban, on isPersonal used as a label, and on this\n" +
      "  device's own stored selection.",
  );
  return bad === 0 ? 0 : 1;
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  if (process.argv.includes("--self-test")) process.exit(selfTest());
  const findings = scan();
  if (findings.length) {
    console.error(
      "\n🚨 A DEFAULT ORGANIZATION IS BACK\n\n" +
        "Nothing that builds a request may read a saved default-organization preference,\n" +
        "and the personal organization is never a fallback. If this device has nothing\n" +
        "SET, HOLD the request, show the picker, and continue once the user picks.\n",
    );
    for (const f of findings) console.error("  ✗ " + f);
    console.error(
      "\nThe one resolver is desktop/src/lib/org/active-org.ts (TS) /\n" +
        "app/services/aidream/organization.py (engine). Arman, 2026-09-19: \"one missed org\n" +
        "check that should have just failed turns into 50 in a month and 5,000 in a year,\n" +
        "and suddenly we don't have orgs any more, we have a user and a default org, which\n" +
        "means we just have user now.\"\n",
    );
    process.exit(1);
  }
  console.log(
    "check:org-default-ban: no saved default-organization read, no personal-org fallback,\n" +
      '  and no "default organization" in user-facing copy.',
  );
}
