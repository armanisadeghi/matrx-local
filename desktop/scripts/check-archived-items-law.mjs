#!/usr/bin/env node
/**
 * check:archived-items-law — every list over an archivable entity carries an
 * archive control, and the default hides archived rows.
 *
 * THE LAW (Arman, 2026-09-09, verbatim — full text at
 * `../../../common-docs/policies/archived-items.md`):
 *
 *   "everything should have an archive filter, and the default should always
 *    hide archived, but seeing archived items should be one or two clicks
 *    away … this is a system wide decision for every single item everywhere in
 *    our system, for every single table and every single page."
 *
 * THE DEFECT THIS REPO SHIPPED (census row C2,
 * `common-docs/projects/archived-items-law/CENSUS-clients.md`): the Claude
 * History Inventory had the archive plumbing END TO END — `archived?: boolean`
 * on the API client, `archived: bool | None` on the FastAPI route, an
 * `is_archived = ?` clause in the SQLite reader — and the table never set it.
 * The request always said nothing, the reader added no clause, and 1,671 real
 * archived Claude sessions rendered mixed in with live ones, unlabelled.
 *
 * That is why this guard watches BOTH halves of this app. A control that
 * exists in three layers and is set in none is invisible to a guard that only
 * reads one of them.
 *
 * Ported from matrx-frontend `scripts/check-archived-items-law.ts` — SAME
 * CONTRACT, three deliberate differences this repo forces:
 *
 *  1. matrx-frontend DERIVES its archivable-table set from generated Supabase
 *     types. This client's archivable rows live in a local SQLite mirror with
 *     no generated types, so the archive COLUMN/FIELD NAME is the signal:
 *     `is_archived` / `archived_at` / an `archived` request field.
 *  2. It scans Python (`app/`) as well as TS/TSX (`desktop/src/`) — the
 *     sidecar is a reader like any other.
 *  3. It knows this app's HTTP boundary shape (`archived=false` in a query
 *     string, `archived: false` in a filters object).
 *
 * WHAT THIS GUARD FAILS ON
 *
 *   1. HARDCODED PREDICATE — a multi-row read whose archive predicate is a
 *      literal, with nothing in the file that could ever flip it.
 *   2. COLUMN WITHOUT A CONTROL — a `.tsx` that names an archive field, maps
 *      rows to JSX, and offers no archive control at all.
 *
 * WHAT IT DELIBERATELY DOES NOT FAIL ON
 *
 *   • Single-record reads. One record is not a list.
 *   • `deleted_at` (soft delete). Deletion is not archiving — db-rules §6d.
 *   • Writes — archiving a row IS a write of `is_archived`.
 *   • A file carrying a real control (`archiveFilter`, `archFilter`,
 *     `ArchiveFilter`, `showArchived`, `include_archived`, `p_archived`, …).
 *
 * WHAT IT CANNOT SEE — say so; never let green imply more than it proves.
 *
 *   The very defect above reads GREEN to rule 1: `archived?: boolean` in a
 *   signature IS a control signal, so a caller that never passes it is
 *   invisible here. Static analysis cannot tell an unused option from a used
 *   one across a React effect, a fetch and a FastAPI route. That gap is closed
 *   by tests, not by this file:
 *     • `src/components/coding-sessions/HistoryInventoryTable.test.ts` — the
 *       request the controls add up to ALWAYS carries an archive state.
 *     • `tests/unit/test_claude_history_import.py` — the engine hides archived
 *       by default, reveals on request, and counts honestly.
 *   This guard's job is the OTHER half: a new list that hardcodes the
 *   predicate, or renders archive-bearing rows with no control at all.
 *
 * ESCAPE HATCH: an internal reader that genuinely must not offer a control (a
 * machine walk, a backup, a health probe) declares it at the query:
 *
 *     # archived-items-law-exempt: sync-all backup walk, not a rendered list
 *
 * with 12+ characters of reason. A bare marker does not count.
 *
 * Run:   pnpm check:archived-items-law
 * Prove: pnpm check:archived-items-law:self-test
 */

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(DESKTOP, "..");

const EXEMPTION = /archived-items-law-exempt:\s*(.{12,})/;

const ARCHIVE_COLUMNS = ["is_archived", "archived_at"];

/** Anything that could ever flip the archive predicate. */
const CONTROL_SIGNALS = [
  /\binclude_?[Aa]rchived\b/,
  /\bshow_?[Aa]rchived\b/,
  /\bwith_?[Aa]rchived\b/,
  /\barchive[dD]?Filter\b/,
  /\bArchiveFilter\b/,
  /\bArchivedFilter\b/,
  /\barchFilter\b/,
  /\bp_archived\b/,
  /\barchived\s*[?:]\s*(?:str|bool|ArchiveFilter)/,
  /\barchived\s*=\s*(?:archiv|state|filter|value)/i,
];

/** A chain carrying one of these is a single-record read, not a list. */
const SINGLE_RECORD_SIGNALS = [
  /\.maybeSingle\s*(?:<[^;]*?>)?\s*\(/,
  /\.single\s*(?:<[^;]*?>)?\s*\(/,
  /\bfetchone\s*\(/,
  /\.eq\s*\(\s*['"`]id['"`]\s*,/,
  /head\s*:\s*true/,
];

/** A chain carrying one of these is a write, not a read. */
const WRITE_SIGNALS = [
  /\.update\s*\(/,
  /\.insert\s*\(/,
  /\.upsert\s*\(/,
  /\.delete\s*\(/,
  /\bUPDATE\s+\w+\s+SET\b/i,
  /\bINSERT\s+INTO\b/i,
];

/** The literal predicate, in every shape either half of this app writes one. */
const HARDCODED_PREDICATES = [
  /\.(?:eq|is|neq)\s*\(\s*['"`](?:is_archived|archived_at)['"`]\s*,\s*(?:false|true|null)\s*\)/,
  /\b(?:is_archived|archived|p_archived)\s*:\s*(?:false|true|False|True)\b/,
  /\b(?:archived|is_archived)\s*=\s*(?:false|true|False|True)\b/,
  /\bis_archived\s*=\s*[01]\b(?![^'"`\n]*\?)/,
];

/**
 * Blank out comments, preserving offsets so reported line numbers stay exact.
 * Nothing in a comment may trip the guard OR whitelist a file; the ONE
 * exception is the exemption marker, read from the RAW text on purpose.
 */
function stripComments(text, python) {
  const blank = (chunk) => chunk.replace(/[^\n]/g, " ");
  if (python) {
    return text.replace(/(^|[^\\])#[^\n]*/g, (match, lead) => lead + " ".repeat(match.length - lead.length));
  }
  return text
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, lead) => lead + " ".repeat(match.length - lead.length));
}

function lineFor(text, index) {
  return text.slice(0, Math.max(0, index)).split("\n").length;
}

function anyMatch(text, patterns) {
  return patterns.some((pattern) => pattern.test(text));
}

/**
 * The ONE statement a match belongs to. 🚨 The window MUST stop at the
 * statement boundary — a fixed character budget lets a neighbour's predicate
 * hide a real finding (the bug matrx-frontend's copy fixed).
 */
const CHAIN_BUDGET = 2400;

function chainWindow(code, at) {
  const lineStart = code.lastIndexOf("\n", at) + 1;
  const hardEnd = Math.min(code.length, at + CHAIN_BUDGET);
  const semicolon = code.indexOf(";", at);
  const end = semicolon >= 0 ? Math.min(semicolon + 1, hardEnd) : hardEnd;
  return code.slice(lineStart, end);
}

export function scanFile(file, raw) {
  const python = file.endsWith(".py");
  const code = stripComments(raw, python);
  const findings = [];

  // 🚨 The control test runs over the code with every LITERAL archive
  // predicate blanked out. `p_archived: False` and `archFilter` are the same
  // words — one is the control, the other is the violation — so counting the
  // violation as its own control is how this guard would whitelist exactly
  // what it exists to catch.
  let controlProbe = code;
  for (const pattern of HARDCODED_PREDICATES) {
    controlProbe = controlProbe.replace(new RegExp(pattern.source, "g"), (chunk) =>
      chunk.replace(/[^\n]/g, " "),
    );
  }
  if (anyMatch(controlProbe, CONTROL_SIGNALS)) return findings;

  const seenLines = new Set();
  for (const pattern of HARDCODED_PREDICATES) {
    const global = new RegExp(pattern.source, "g");
    let match;
    while ((match = global.exec(code)) !== null) {
      const window = chainWindow(code, match.index);
      if (anyMatch(window, WRITE_SIGNALS)) continue;
      if (anyMatch(window, SINGLE_RECORD_SIGNALS)) continue;

      // The exemption is read from the RAW text — an exemption IS a comment.
      const rawWindow = raw.slice(Math.max(0, match.index - 800), match.index + 400);
      if (EXEMPTION.test(rawWindow)) continue;

      const line = lineFor(code, match.index);
      if (seenLines.has(line)) continue;
      seenLines.add(line);
      findings.push({
        file,
        line,
        reason:
          `list read hardcodes \`${match[0].trim()}\` with no archive control anywhere in ` +
          "the file — archived rows are impossible to reveal",
      });
    }
  }

  // RULE 2 — THE SCREEN THAT LIES. A component that names the archive field
  // (so it knows perfectly well which rows are archived), maps rows to JSX,
  // and offers no control: archived and active land in one list,
  // indistinguishable. Scoped to .tsx that actually renders — a service
  // handing the field to a caller that owns the control is doing the RIGHT
  // thing, and a guard that fires on every layer is one agents delete.
  if (findings.length === 0 && file.endsWith(".tsx") && /\.map\s*\(/.test(code)) {
    for (const column of ARCHIVE_COLUMNS) {
      const match = new RegExp(`\\b${column}\\b`).exec(code);
      if (!match) continue;
      const rawWindow = raw.slice(Math.max(0, match.index - 800), match.index + 400);
      if (EXEMPTION.test(rawWindow)) break;
      findings.push({
        file,
        line: lineFor(code, match.index),
        reason:
          `component names \`${column}\`, renders the rows, and offers no archive ` +
          "control — archived and active render mixed and unlabelled",
      });
      break;
    }
  }

  return findings;
}

function sourceFiles() {
  const out = execSync("git ls-files --cached --others --exclude-standard", {
    cwd: REPO,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  return out
    .split("\n")
    .filter(Boolean)
    .filter((file) => /^(desktop\/src|app)\//.test(file))
    .filter((file) => /\.(ts|tsx|py)$/.test(file))
    .filter((file) => !/\.(test|spec)\.(ts|tsx)$/.test(file))
    .filter((file) => !/(^|\/)test_[^/]*\.py$/.test(file));
}

// ── Self-test — a guard you cannot demonstrate failing is not a guard ──────

/** RED — a TS list read hidden with zero clicks to reveal. */
const RED_TS_PREDICATE = `
const { data } = await db
  .from("saved_sessions")
  .select("id, title, is_archived")
  .eq("is_archived", false)
  .order("created_at", { ascending: false });
`;

/** RED — the HTTP shape this repo's own defect wore. */
const RED_HTTP_PARAM = `
const res = await fetch(\`\${base}/coding-session/claude/history/scans/\${id}?archived=false\`);
`;

/** RED — the Python reader shape. */
const RED_PY_CLAUSE = `
rows = await self._db.fetchall(
    "SELECT * FROM coding_session_history_scan_rows WHERE scan_id = ? AND is_archived = 0"
)
`;

/** RED — a component that knows which rows are archived and says nothing. */
const RED_COLUMN_NO_CONTROL = `
export function Panel({ rows }: { rows: Row[] }) {
  return <ul>{rows.map((row) => <li key={row.id}>{row.title}{row.is_archived}</li>)}</ul>;
}
`;

/** RED — a comment mentioning a control must not whitelist a hard predicate. */
const RED_COMMENT_ONLY_CONTROL = `
// TODO: add an archiveFilter here one day
const { data } = await db.from("saved_sessions").select("*").eq("is_archived", false);
`;

/** GREEN — the predicate is bound to a parameter the caller can set. */
const GREEN_PY_PARAMETER = `
if archived not in ARCHIVE_FILTERS:
    raise ValueError("Unsupported archive filter")
clauses.append(_ARCHIVE_CLAUSES[archived])
`;

/** GREEN — the platform control, wired to the reader. */
const GREEN_TS_CONTROL = `
import { ArchiveFilter } from "@/components/archive-filter";
const page = await engine.getClaudeHistoryInventoryPage(scanId, { archived: archiveFilter });
`;

/** GREEN — the package tri-state on an agent list. */
const GREEN_PACKAGE_FILTER = `
import type { AgentArchFilter } from "@ai-matrx/agents/catalog";
consumer.setArchFilter(next);
`;

/** GREEN — a single record is not a list. */
const GREEN_SINGLE = `
const { data } = await db.from("saved_sessions").select("id").eq("is_archived", false).maybeSingle();
`;

/** GREEN — archiving a row IS a write of the column. */
const GREEN_WRITE = `
await db.from("saved_sessions").update({ is_archived: true }).eq("id", id);
`;

/** GREEN — a declared, reasoned exemption. */
const GREEN_EXEMPT = `
# archived-items-law-exempt: sync-all backup walk, never a rendered list
rows = await self._db.fetchall("SELECT * FROM scan_rows WHERE is_archived = 0")
`;

/** GREEN — soft delete is not archiving. */
const GREEN_DELETED_AT = `
const { data } = await db.from("saved_sessions").select("*").is("deleted_at", null);
`;

function selfTest() {
  const failures = [];
  const expectRed = (name, source, file = "self-test.ts") => {
    if (scanFile(file, source).length === 0) {
      failures.push(
        `${name}: the detector stayed GREEN on a source that breaks the law — ` +
          "a guard that cannot fail proves nothing.",
      );
    }
  };
  const expectGreen = (name, source, file = "self-test.ts") => {
    const found = scanFile(file, source);
    if (found.length > 0) {
      failures.push(
        `${name}: false positive — ${found[0].reason}. False positives get guards deleted.`,
      );
    }
  };

  expectRed("TS-PREDICATE", RED_TS_PREDICATE);
  expectRed("HTTP-PARAM", RED_HTTP_PARAM);
  expectRed("PY-CLAUSE", RED_PY_CLAUSE, "self-test.py");
  expectRed("COLUMN-NO-CONTROL", RED_COLUMN_NO_CONTROL, "self-test.tsx");
  expectRed("COMMENT-ONLY-CONTROL", RED_COMMENT_ONLY_CONTROL);
  expectGreen("PY-PARAMETER", GREEN_PY_PARAMETER, "self-test.py");
  expectGreen("TS-CONTROL", GREEN_TS_CONTROL);
  expectGreen("PACKAGE-FILTER", GREEN_PACKAGE_FILTER);
  expectGreen("SINGLE-RECORD", GREEN_SINGLE);
  expectGreen("WRITE", GREEN_WRITE);
  expectGreen("EXEMPT", GREEN_EXEMPT, "self-test.py");
  expectGreen("DELETED-AT", GREEN_DELETED_AT);

  if (failures.length > 0) {
    console.error("\n🚨 check:archived-items-law SELF-TEST FAILED\n");
    for (const failure of failures) console.error(`  ✗ ${failure}`);
    console.error(
      "\nFix desktop/scripts/check-archived-items-law.mjs before trusting a green run.\n",
    );
    process.exit(1);
  }

  console.log(
    "✅ self-test: RED on a hardcoded TS predicate, an `archived=false` query string, a\n" +
      "   literal SQLite `is_archived = 0`, a list component that names the column with no\n" +
      "   control, and a comment-only 'control'; GREEN on a parameter-bound Python\n" +
      "   predicate, the wired TS control, the package tri-state, a single-record read, a\n" +
      "   write, a reasoned exemption, and `deleted_at`.",
  );
}

function main() {
  if (process.argv.includes("--self-test")) {
    selfTest();
    return;
  }

  const findings = [];
  for (const file of sourceFiles()) {
    findings.push(...scanFile(file, readFileSync(path.join(REPO, file), "utf8")));
  }

  if (findings.length === 0) {
    console.log(
      "✅ THE ARCHIVED-ITEMS LAW holds across desktop/src and app/: no list read hides\n" +
        "   archived rows with no way to reveal them.",
    );
    return;
  }

  console.error("\n🚨 ARCHIVED-ITEMS LAW VIOLATIONS\n");
  for (const finding of findings) {
    console.error(`  ✗ ${finding.file}:${finding.line} — ${finding.reason}`);
  }
  console.error(
    "\nEvery list over an archivable entity carries an archive control, the default hides\n" +
      "archived rows, and revealing them is one or two clicks — Arman, 2026-09-09\n" +
      "(../../common-docs/policies/archived-items.md).\n\n" +
      "In this repo:\n" +
      "  • agent lists → @ai-matrx/agents/catalog `archFilter` (the chip is already on the\n" +
      "    package picker's filter bar)\n" +
      "  • anything else → the tri-state ArchiveFilter control, its value passed to the\n" +
      "    READER (the engine's `archived=`, the SQLite clause) so counts do not lie.\n" +
      "    Three states, never a boolean: a boolean cannot say 'archived only'.\n\n" +
      "An internal reader that is genuinely not a user-facing list declares it at the\n" +
      "query: `archived-items-law-exempt: <reason>` (12+ characters of reason).\n",
  );
  process.exit(1);
}

main();
