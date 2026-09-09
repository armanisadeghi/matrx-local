#!/usr/bin/env node
/**
 * check:canonical-pickers — stop agent-picker forks at source.
 *
 * THERE IS ONE AGENT PICKER on this platform and it lives in
 * `@ai-matrx/agents/catalog/react` (`AgentListDropdown` /
 * `AgentListInlinePicker`). Ruling D1 (Arman, 2026-09-08): the package owns
 * the picker end to end and the host receives `onSelect(agentId)`. Ruling D4:
 * **matrx-local is never an exception** — offline is a data location, not a
 * different list, sort, filter or UI. This desktop shipped a 912-line
 * `components/chat/AgentPicker.tsx` with its own reimplementation of the
 * frontend's sorts and filters, and its own hardcoded default-agent NAME. This
 * guard is what stops that class from coming back.
 *
 * Ported from matrx-frontend `scripts/check-canonical-pickers.ts`, agent half
 * only: this repo's model selection is local llama-server models, a different
 * identity domain with no platform picker to fork.
 *
 * A surface that genuinely is not an agent CHOICE may declare
 * `canonical-agent-picker-exempt: <reason>` (12+ reason characters) nearby.
 *
 * `--self-test` proves the detector can FAIL: it runs the same patterns over
 * synthetic forks and exits non-zero if they come back clean. A guard you
 * cannot demonstrate failing is not a guard.
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const AGENT_CANONICAL_IMPORT = "@ai-matrx/agents/catalog/react";
const AGENT_EXEMPTION = /canonical-agent-picker-exempt:\s*(.{12,})/;

/**
 * 🚨 THE SUBSTRING HOLE (fixed 2026-09-08, review of P7). The canonical-import
 * test used to be `text.includes("@ai-matrx/agents/catalog/react")`, so ANY
 * mention of the path whitelisted the WHOLE file — a tombstone comment, a doc
 * line, a string. A file could name the package in a comment (or import it for
 * one purpose) and hand-roll a second picker underneath it, green. Two changes
 * close it, and they are byte-identical in all four copies of this guard
 * (matrx-frontend, matrx-extend, matrx-local/desktop,
 * aidream/apps/workflow-studio):
 *
 *  1. Every scan runs over a COMMENT-STRIPPED copy of the file (offsets and
 *     therefore reported line numbers are preserved), so nothing in a comment
 *     can whitelist — or trip — the guard.
 *  2. The import test matches a real `import … from "<path>"` /
 *     `export … from "<path>"` / `require("<path>")` / `import("<path>")`
 *     statement, never a substring.
 *
 * The EXEMPTION is read from the RAW text: an exemption IS a comment.
 */
function stripComments(text) {
  const blank = (chunk) => chunk.replace(/[^\n]/g, " ");
  return text
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(
      /(^|[^:])\/\/[^\n]*/g,
      (match, lead) => lead + " ".repeat(match.length - lead.length),
    );
}

function escapeForRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function importSignals(modulePath) {
  const quoted = `['"\`]${escapeForRegExp(modulePath)}['"\`]`;
  return [
    // `import X from "p"`, `import { X } from "p"` (multi-line included), `import "p"`
    new RegExp(`\\bimport\\s+(?:[^;'"\`]*?\\bfrom\\s*)?${quoted}`),
    // `export { X } from "p"`, `export * from "p"`
    new RegExp(`\\bexport\\s+[^;'"\`]*?\\bfrom\\s*${quoted}`),
    new RegExp(`\\brequire\\s*\\(\\s*${quoted}`),
    new RegExp(`\\bimport\\s*\\(\\s*${quoted}`),
  ];
}

const IMPORT_SIGNALS = importSignals(AGENT_CANONICAL_IMPORT);

/**
 * WHAT A CANONICAL IMPORT EXCUSES: rendering the package's own components, and
 * nothing else. A file that imports the package is STILL scanned. A
 * hand-rolled `Agent(Picker|Selector|Select|Dropdown|List)` definition is
 * excused only when the file actually renders `AgentListDropdown` /
 * `AgentListInlinePicker` (the thin-wrapper shape — a wrapper named
 * `AgentPicker.tsx` that renders the package component is fine). A hand-built
 * roster (`agentOptions|availableAgents|displayAgents|allAgents|filteredAgents`
 * .map, a native `<select>` of agents) and a direct catalogue RPC read are
 * findings EITHER WAY: rendering the package once does not buy the right to
 * fork a second list beneath it.
 */
const PACKAGE_RENDER = /<\s*(?:AgentListDropdown|AgentListInlinePicker)\b/;

// The prefix is OPTIONAL and not `[A-Z]\w*`: the frontend's original required a
// character BEFORE "Agent", so a component named exactly `AgentPicker` — which
// is what this repo actually shipped — slipped straight through it. It is a
// NAMED prefix class rather than a bare `\w*` (which is what this port used
// 2026-09-08 morning): bare `\w*` also matches `handleAgentSelect`, the handler
// name every one of these surfaces has, and flagged four innocent files when it
// was carried back to matrx-frontend the same day. `fetch|get|load|build|create`
// keep `fetchAgentList` and friends — the retired hand-rolled loaders — caught.
const NAME_PREFIX = "(?:[A-Z]\\w*|use|fetch|get|load|build|create)?";

const NAME_SIGNALS = [
  new RegExp(
    `(?:export\\s+)?function\\s+${NAME_PREFIX}Agent(?:Picker|Selector|Select|Dropdown|List)\\b`,
  ),
  new RegExp(
    `const\\s+${NAME_PREFIX}Agent(?:Picker|Selector|Select|Dropdown|List)\\b\\s*=\\s*(?:\\([^)]*\\)|[^=])*=>`,
  ),
];

const ROSTER_SIGNALS = [
  /<SelectValue\b[^>]*placeholder\s*=\s*["'][^"']*(?:select|choose|pick)[^"']*agent/i,
  /<select\b[^>]*aria-label\s*=\s*["'][^"']*agent/i,
  /\b(?:agentOptions|availableAgents|displayAgents|allAgents|filteredAgents)\.map\s*\(/,
  // The row read itself: only the package may CALL the catalog RPCs. The pattern
  // requires the `.rpc("<name>"` call form on purpose — a bare mention also matches a
  // test double whose `rpc()` ANSWERS the RPC for the package's own store (this is
  // what it flagged in workflow-studio once the substring whitelist was closed), and
  // exempting a real file to get green is how a guard dies.
  /\.rpc\s*\(\s*['"`]agx_get_list(?:_full)?\b/,
  /\.rpc\s*\(\s*['"`]agx_search\b/,
];

function sourceFiles() {
  const out = execSync(
    "git ls-files --cached --others --exclude-standard 'src/*.ts' 'src/*.tsx'",
    { cwd: ROOT, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
  );
  return out
    .split("\n")
    .filter(Boolean)
    // Generated API types mirror the server's own names and are not UI.
    .filter((file) => !file.includes("src/types/python-generated/"))
    // `--cached` still lists files deleted in the working tree.
    .filter((file) => existsSync(path.join(ROOT, file)));
}

function lineFor(text, index) {
  return text.slice(0, Math.max(0, index)).split("\n").length;
}

function firstMatch(text, patterns) {
  for (const pattern of patterns) {
    const match = pattern.exec(text);
    if (match) return { index: match.index, source: pattern.source };
  }
  return null;
}

function readFileText(absolutePath) {
  const raw = readFileSync(absolutePath, "utf8");
  return { raw, code: stripComments(raw) };
}

/** Findings for one file. Detection reads `code`; the exemption reads `raw`. */
function scan({ raw, code }) {
  if (AGENT_EXEMPTION.test(raw)) return [];
  const canonical = IMPORT_SIGNALS.some((pattern) => pattern.test(code));
  const wrapsCanonical = canonical && PACKAGE_RENDER.test(code);

  const findings = [];
  const named = firstMatch(code, NAME_SIGNALS);
  if (named && !wrapsCanonical) {
    findings.push({
      line: lineFor(code, named.index),
      reason: canonical
        ? `defines its own agent picker beside the canonical import without rendering AgentListDropdown / AgentListInlinePicker (matched /${named.source}/)`
        : `agent-selection UI does not render ${AGENT_CANONICAL_IMPORT} (matched /${named.source}/)`,
    });
  }
  const roster = firstMatch(code, ROSTER_SIGNALS);
  if (roster) {
    findings.push({
      line: lineFor(code, roster.index),
      reason: canonical
        ? `builds its own agent roster (or reads the catalogue) beside the canonical import — importing the package excuses rendering its components, nothing else (matched /${roster.source}/)`
        : `agent-selection UI does not render ${AGENT_CANONICAL_IMPORT} (matched /${roster.source}/)`,
    });
  }
  return findings;
}

/**
 * RED 1 is the shape this repo actually shipped (a component named EXACTLY
 * `AgentPicker` rebuilding the roster). RED 2 and RED 3 are the substring hole
 * the P7 review found: a file whose ONLY mention of the package is a comment,
 * and a file that imports the package for one purpose and forks a second picker
 * beneath it. GREEN 1 renders the package picker; GREEN 2 is a thin wrapper
 * NAMED `AgentPicker` that renders it (legitimate — the name is not the defect,
 * the second roster is); the handler fixture is the `handleAgentSelect` false
 * positive a bare `\w*` prefix brings back.
 */
const SELF_TEST_RED_BARE_FORK = `
import { useState } from "react";
export function AgentPicker({ agents }) {
  const [q, setQ] = useState("");
  const filteredAgents = agents.filter((a) => a.name.includes(q));
  return <div>{filteredAgents.map((a) => <button key={a.id}>{a.name}</button>)}</div>;
}
`;

const SELF_TEST_RED_COMMENT_MENTION = `
import { useState } from "react";
// The platform picker lives in ${AGENT_CANONICAL_IMPORT} and we should adopt it
// some day; AgentListDropdown does most of this already.
export function ChooseAgent({ agents }) {
  const [value, setValue] = useState("");
  return (
    <select aria-label="Select agent" value={value}>
      {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
    </select>
  );
}
`;

const SELF_TEST_RED_IMPORT_AND_FORK = `
import { AgentListDropdown } from "${AGENT_CANONICAL_IMPORT}";
export function AgentSurface() {
  return <AgentListDropdown consumerId="matrx-local.chat" onSelect={() => {}} />;
}
export function AgentPicker({ availableAgents, onPick }) {
  return (
    <select onChange={(e) => onPick(e.target.value)}>
      {availableAgents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
    </select>
  );
}
`;

const SELF_TEST_GREEN = `
import { AgentListDropdown } from "${AGENT_CANONICAL_IMPORT}";
export function Surface() { return <AgentListDropdown onSelect={() => {}} />; }
`;

const SELF_TEST_GREEN_WRAPPER = `
import { AgentListInlinePicker } from "${AGENT_CANONICAL_IMPORT}";
export function AgentPicker({ onSelect }) {
  return <AgentListInlinePicker consumerId="matrx-local.chat" onSelect={onSelect} />;
}
`;

const SELF_TEST_HANDLER = `
export function ChatSurface() {
  const handleAgentSelect = useCallback((agent) => open(agent.id), []);
  return <button onClick={() => handleAgentSelect({ id: '1' })}>Pick</button>;
}
`;

function selfTest() {
  const failures = [];
  const check = (fixture) => scan({ raw: fixture, code: stripComments(fixture) });

  if (check(SELF_TEST_RED_BARE_FORK).length === 0) {
    failures.push(
      "RED 1: the detector did NOT flag a hand-rolled `AgentPicker` — the name-prefix gap is back.",
    );
  }
  if (check(SELF_TEST_RED_COMMENT_MENTION).length === 0) {
    failures.push(
      "RED 2: the detector did NOT flag a hand-rolled <select> picker in a file whose ONLY mention of the package is a comment — the substring hole is back.",
    );
  }
  if (check(SELF_TEST_RED_IMPORT_AND_FORK).length === 0) {
    failures.push(
      "RED 3: the detector did NOT flag a file that imports the package AND forks its own `AgentPicker` beneath it — a canonical import excuses rendering the package components, nothing else.",
    );
  }
  if (check(SELF_TEST_GREEN).length > 0) {
    failures.push(
      "GREEN 1: the detector flagged a surface that DOES render the package picker — it would block correct adoption.",
    );
  }
  if (check(SELF_TEST_GREEN_WRAPPER).length > 0) {
    failures.push(
      "GREEN 2: the detector flagged a thin wrapper named `AgentPicker` that renders AgentListInlinePicker — the name is not the defect, a second roster is.",
    );
  }
  if (check(SELF_TEST_HANDLER).length > 0) {
    failures.push(
      "HANDLER: the detector flagged a plain `handleAgentSelect` callback — a false positive makes agents delete the guard instead of the fork.",
    );
  }
  if (failures.length > 0) {
    console.error("\n🚨 check:canonical-pickers SELF-TEST FAILED\n");
    for (const failure of failures) console.error(`  ✗ ${failure}`);
    console.error(
      "\nFix scripts/check-canonical-pickers.mjs before trusting a green run.\n",
    );
    process.exit(1);
  }
  console.log(
    "✅ self-test: RED on a hand-rolled `AgentPicker`, on a comment-only package mention\n" +
      "   beside a <select> picker, and on a canonical import with a fork beneath it;\n" +
      "   GREEN on the package picker and on a thin `AgentPicker` wrapper; silent on a\n" +
      "   `handleAgentSelect` handler.",
  );
}

function main() {
  if (process.argv.includes("--self-test")) {
    selfTest();
    return;
  }

  const findings = [];
  for (const file of sourceFiles()) {
    const text = readFileText(path.join(ROOT, file));
    for (const finding of scan(text)) findings.push({ file, ...finding });
  }

  if (findings.length === 0) {
    console.log(
      "✅ Canonical picker holds: every agent choice in this desktop renders\n" +
        `   ${AGENT_CANONICAL_IMPORT}.`,
    );
    return;
  }

  console.error("\n🚨 AN ALTERNATE AGENT PICKER (OR CATALOG READ) WAS FOUND\n");
  for (const finding of findings) {
    console.error(`  ✗ ${finding.file}:${finding.line} — ${finding.reason}`);
  }
  console.error(
    "\nTHERE IS ONE AGENT PICKER: render AgentListDropdown or\n" +
      `AgentListInlinePicker from ${AGENT_CANONICAL_IMPORT} and take\n` +
      "onSelect(agentId). A surface that needs different sizing, tabs, filters or a\n" +
      "default row configures the package component — it never rebuilds the list, and\n" +
      "it never calls agx_get_list_full / agx_search itself. matrx-local is NEVER an\n" +
      "exception (ruling D4): offline is a data location, not a different UI.\n" +
      "A non-choice surface may declare `canonical-agent-picker-exempt: <reason>`.\n",
  );
  process.exit(1);
}

main();
