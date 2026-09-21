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
const HOST_SRC = new URL("../src-tauri/src/", import.meta.url).pathname;

/** The one file allowed to read a pre-cutover session out of browser storage, and its test. */
const HANDOVER = ["lib/legacy-session-handover.ts", "lib/legacy-session-handover.test.ts"];

/** Generated from the server's OpenAPI document; describes routes, asserts nothing about us. */
const GENERATED = "types/python-generated/";

const RULES = [
  {
    id: "second-session-holder",
    // PRECISION (X-2): `setSession` is a session holder only when it is called ON something —
    // `client.auth.setSession(...)` — or destructured off an auth client. A React
    // `const [session, setSession] = useState(...)` is a local state setter that happens to
    // share the word, and four of them in ContinueSessionDialog.tsx kept this guard red on main
    // for three days. A guard the fleet learns to ignore protects nothing.
    match: (line) =>
      /\b(persistSession|autoRefreshToken|refresh_token|refreshToken)\b/.test(line) ||
      /\.setSession\s*\(/.test(line) ||
      (/\bsetSession\b/.test(line) && /\b(auth|supabase|createClient|GoTrue)\b/.test(line)),
    exempt: (rel) => rel.startsWith(GENERATED) || HANDOVER.includes(rel),
    message:
      "this process holds no session: the daemon is the device's only session holder, so there " +
      "is nothing here to persist, auto-refresh, or carry a refresh token for",
  },
  {
    id: "supabase-storage-entry",
    match: (line) => /sb-[^\s"'`]*-auth-token|["'`]sb-\$\{/.test(line),
    exempt: (rel) => HANDOVER.includes(rel),
    message:
      "only lib/legacy-session-handover.ts may touch a pre-cutover Supabase storage entry, and " +
      "only to hand it to the daemon",
  },
];

/** Rules for the Rust host (`desktop/src-tauri/src`), scanned as its own tree.
 *
 *  The daemon holds the PKCE verifier, so the authorization code is useless to the webview — but
 *  only while the webview cannot GET it. FS-C5b shipped with `get_pending_oauth_url` still
 *  registered, stashing the whole `aimatrx://auth/callback?code=…` URL for any script in the
 *  webview to read (finding C5b-2). The class is "the host keeps the callback URL somewhere the
 *  webview can reach", and it is closed here by name and by emit. */
const HOST_RULES = [
  {
    id: "callback-url-reachable-from-webview",
    match: (line) =>
      /\b(PendingOAuthUrl|pending_oauth_url|get_pending_oauth_url|oauth_callback_url)\b/.test(line) ||
      (/\.emit(_to)?\s*\(/.test(line) && /aimatrx:\/\/|url_str|callback_url/.test(line)),
    exempt: () => false,
    message:
      "the OAuth callback URL carries the authorization code: the host forwards it to the " +
      "daemon and keeps no copy the webview can fetch or receive",
  },
];

function* walk(dir, extensions = /\.(ts|tsx)$/) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "target" || entry.name === "node_modules") continue;
      yield* walk(full, extensions);
    } else if (extensions.test(entry.name)) {
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

function scan(root, rules = RULES, extensions = /\.(ts|tsx)$/) {
  const findings = [];
  for (const file of walk(root, extensions)) {
    const rel = relative(root, file).split(sep).join("/");
    const lines = readFileSync(file, "utf8").split("\n");
    for (const rule of rules) {
      if (rule.exempt(rel)) continue;
      lines.forEach((line, index) => {
        if (rule.match(line) && !isProseOrNegation(line)) {
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

  // Rule 1, planted — both shapes a real second session holder takes.
  writeFileSync(
    join(root, "lib/rogue.ts"),
    "// a comment about setSession must not trip the guard\nawait client.auth.setSession(saved);\n",
  );
  writeFileSync(
    join(root, "lib/rogue-destructured.ts"),
    "const { setSession } = supabase.auth;\nawait setSession(saved);\n",
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
  // The precision case X-2 was red on: a React state setter that merely shares the word.
  writeFileSync(
    join(root, "lib/react-state.tsx"),
    "const [session, setSession] = useState<Verdict | null>(null);\nsetSession({ verdict, error: null });\n",
  );

  const findings = scan(root);

  // Host rules, planted in their own tree.
  const hostRoot = mkdtempSync(join(tmpdir(), "session-custody-host-"));
  writeFileSync(
    join(hostRoot, "rogue_host.rs"),
    "struct PendingOAuthUrl(Mutex<Option<String>>);\n",
  );
  writeFileSync(
    join(hostRoot, "rogue_emit.rs"),
    '            let _ = window.emit("oauth-callback", url_str.clone());\n',
  );
  // The honest host: it forwards the URL to the daemon and emits only a refusal sentence.
  writeFileSync(
    join(hostRoot, "honest_host.rs"),
    'syncd::syncd_sign_in_callback(url_str).await;\nlet _ = window.emit("syncd-sign-in-failed", message);\n',
  );
  const hostFindings = scan(hostRoot, HOST_RULES, /\.rs$/);

  rmSync(root, { recursive: true, force: true });
  rmSync(hostRoot, { recursive: true, force: true });

  // The prose skip must not have swallowed the real line beneath the comment.
  const rogue = findings.filter((f) => f.rel === "lib/rogue.ts");
  const caught = new Set(findings.map((f) => f.rel === "lib/rogue-destructured.ts" ? "destructured" : f.rule.id));
  const wrongly = findings.filter(
    (f) => f.rel.startsWith(GENERATED) || HANDOVER.includes(f.rel) || f.rel === "lib/react-state.tsx",
  );
  const missing = [...RULES.map((r) => r.id), "destructured"].filter((id) => !caught.has(id));
  const hostCaught = new Set(hostFindings.map((f) => f.rel));
  const hostMissing = ["rogue_host.rs", "rogue_emit.rs"].filter((f) => !hostCaught.has(f));
  const hostWrongly = hostFindings.filter((f) => f.rel === "honest_host.rs");

  if (missing.length || wrongly.length || rogue.length !== 1 || hostMissing.length || hostWrongly.length) {
    console.error("SELF-TEST FAILED");
    if (missing.length) console.error(`  rules that missed a planted violation: ${missing.join(", ")}`);
    if (wrongly.length) console.error(`  files wrongly reported: ${wrongly.map((f) => `${f.rel}:${f.line}`).join(", ")}`);
    if (rogue.length !== 1) console.error(`  the prose skip must catch code and skip the comment: ${rogue.length} hit(s) in lib/rogue.ts`);
    if (hostMissing.length) console.error(`  host rules that missed a planted violation: ${hostMissing.join(", ")}`);
    if (hostWrongly.length) console.error(`  honest host wrongly reported: ${hostWrongly.map((f) => `${f.rel}:${f.line}`).join(", ")}`);
    process.exit(1);
  }
  console.log(
    `session-custody guard self-test passed (${RULES.length + HOST_RULES.length} rules, each proven ` +
      `catching a planted violation and clearing a legitimate look-alike)`,
  );
  process.exit(0);
}

const findings = [...scan(SRC), ...scan(HOST_SRC, HOST_RULES, /\.rs$/)];
if (findings.length) {
  console.error(
    `\nTHE SESSION-CUSTODY GUARD found ${findings.length} place(s) where this app would hold a ` +
      `session of its own, or hand the webview a sign-in code.\n`,
  );
  report(findings);
  console.error(
    "\nIdentity and tokens come from the daemon through lib/custodian.ts. A React app that " +
      "believes it is signed in while the daemon says signed_out is the split-brain class this " +
      "guard exists to keep closed.\n",
  );
  process.exit(1);
}
console.log("session-custody guard: this app holds no session of its own, and no sign-in code reaches the webview.");
