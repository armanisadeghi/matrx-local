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
 * `--self-test` proves the detector can FAIL: it runs the same patterns over a
 * synthetic hand-rolled picker and exits non-zero if they come back clean. A
 * guard you cannot demonstrate failing is not a guard.
 */

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const AGENT_CANONICAL_IMPORT = "@ai-matrx/agents/catalog/react";
const AGENT_EXEMPTION = /canonical-agent-picker-exempt:\s*(.{12,})/;

/** Names and shapes that only exist when someone rebuilt the roster. */
const AGENT_SIGNALS = [
  // `\w*` and not `[A-Z]\w*`: the frontend's original required a prefix
  // BEFORE "Agent", so a component named exactly `AgentPicker` — which is what
  // this repo actually shipped — slipped straight through it.
  /(?:export\s+)?function\s+\w*Agent(?:Picker|Selector|Select|Dropdown|List)\b/,
  /const\s+\w*Agent(?:Picker|Selector|Select|Dropdown|List)\b\s*=\s*(?:\([^)]*\)|[^=])*=>/,
  /<SelectValue\b[^>]*placeholder\s*=\s*["'][^"']*(?:select|choose|pick)[^"']*agent/i,
  /<select\b[^>]*aria-label\s*=\s*["'][^"']*agent/i,
  /\b(?:agentOptions|availableAgents|displayAgents|allAgents|filteredAgents)\.map\s*\(/,
  // The row read itself: only the package may call the catalog RPCs.
  /\bagx_get_list(?:_full)?\b/,
  /\bagx_search\b/,
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

function scan(text) {
  if (text.includes(AGENT_CANONICAL_IMPORT)) return null;
  if (AGENT_EXEMPTION.test(text)) return null;
  return firstMatch(text, AGENT_SIGNALS);
}

const SELF_TEST_FIXTURE = `
import { useState } from "react";
export function AgentPicker({ agents }) {
  const [q, setQ] = useState("");
  const filteredAgents = agents.filter((a) => a.name.includes(q));
  return <div>{filteredAgents.map((a) => <button key={a.id}>{a.name}</button>)}</div>;
}
`;

function selfTest() {
  const hit = scan(SELF_TEST_FIXTURE);
  if (!hit) {
    console.error(
      "🚨 check:canonical-pickers SELF-TEST FAILED — the detector did not flag a\n" +
        "hand-rolled AgentPicker. A guard that cannot fail is not a guard; fix the\n" +
        "patterns in scripts/check-canonical-pickers.mjs before trusting a green run.",
    );
    process.exit(1);
  }
  const clean = scan(
    `import { AgentListDropdown } from "${AGENT_CANONICAL_IMPORT}";\n` +
      "export function Surface() { return <AgentListDropdown onSelect={() => {}} />; }\n",
  );
  if (clean) {
    console.error(
      "🚨 check:canonical-pickers SELF-TEST FAILED — the detector flagged a surface\n" +
        "that DOES render the package picker. It would block correct adoption.",
    );
    process.exit(1);
  }
  console.log(
    "✅ check:canonical-pickers self-test: the detector fails on a hand-rolled\n" +
      "   picker and passes a package-rendered one.",
  );
}

function main() {
  if (process.argv.includes("--self-test")) {
    selfTest();
    return;
  }

  const findings = [];
  for (const file of sourceFiles()) {
    const text = readFileSync(path.join(ROOT, file), "utf8");
    const hit = scan(text);
    if (hit) findings.push({ file, line: lineFor(text, hit.index), signal: hit.source });
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
    console.error(`  ✗ ${finding.file}:${finding.line} — matched /${finding.signal}/`);
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
