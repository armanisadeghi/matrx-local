#!/usr/bin/env node
/**
 * check:code-prompts — no platform prompt lives in desktop code.
 *
 * THE LAW (common-docs/systems/intelligence/mandates/STATE.md §9): every
 * intelligence invocation resolves through a Mandate, both halves of the agent —
 * its id AND its definition. "A prompt in code is the same violation": a
 * system prompt written into this repo is a hardcoded agent in disguise, and a
 * Mandate rebind can never reach it. matrx-local shipped exactly that —
 * `PIPELINE_TEMPLATES` in `desktop/src/hooks/use-llm-pipeline.ts`, six system
 * prompts run against the local llama-server — until they became the Holders
 * of the `local.*` mandates (2026-09-25). This guard keeps that shape from
 * coming back. Offline is a data LOCATION, never a licence to carry prompts.
 *
 * WHAT THIS FAILS ON (code only — comments are stripped)
 *
 *   1. A prompt-template property: `system: "…"` / `systemPrompt: "…"` /
 *      `instructions: "…"` whose value is a string literal.
 *   2. A system message built from a literal: `role: "system"` with a
 *      `content: "…"` literal in the same object.
 *   3. An instruction-voice literal: a string that opens "You are …" (the
 *      persona line every system prompt starts with).
 *
 * WHAT IT DELIBERATELY DOES NOT FAIL ON
 *
 *   Runtime data around a resolved agent (a variable's value, the person's own
 *   typed system text held in state) — those are not literals. Tests. Files in
 *   KNOWN_OPEN below, each with its reason: they are OPEN bypasses tracked for
 *   conversion, listed here so the ratchet shows them and a NEW file cannot join.
 *
 * WHAT IT CANNOT SEE — never let green imply more than it proves
 *
 *   A prompt assembled from fragments behind variables, or fetched from a
 *   non-mandate source at runtime, reads green. It proves the SHAPE is gone.
 *
 * Run:            pnpm check:code-prompts          (from desktop/)
 * Prove it works: pnpm check:code-prompts:self-test
 */

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const SCAN = /^src\/.*\.(ts|tsx)$/;
const SKIP = /(\.test\.tsx?$|\.spec\.tsx?$|^src\/types\/python-generated\/)/;

/**
 * Open bypasses — each is a platform prompt still in code, awaiting its own
 * mandate conversion. The list may only SHRINK. Adding a file here to get
 * green is the defect this guard exists to stop.
 */
const KNOWN_OPEN = new Map([
  [
    "src/lib/polish-presets.ts",
    "Voice page's built-in AI Polish styles (six system prompts). The run itself resolves local.polish_transcript; the built-in STYLES are still code-authored prompts awaiting conversion (reported 2026-09-25).",
  ],
  [
    "src/lib/system-prompts.ts",
    "Compiled fallback of the prompt-library catalog (the live set is the remote catalog_entries overlay); a person picks one as their own chat system text. Awaiting a ruling on the offline fallback copy.",
  ],
]);

const RULES = [
  {
    name: "prompt-template property with a literal value",
    // A non-empty literal of prompt length; `systemPrompt: ""` is an empty state.
    re: /\b(system|systemPrompt|instructions)\s*:\s*["'`][^"'`]{20,}/,
  },
  {
    name: "system message built from a literal",
    re: /role\s*:\s*["']system["'][^}]{0,200}?\bcontent\s*:\s*["'`]/s,
  },
  {
    name: "instruction-voice literal (\"You are …\")",
    re: /["'`]You are (an?|the) \w/,
    // UI example text (an input placeholder, a help sentence) shows the person
    // how to write THEIR OWN prompt; it is never sent to a model.
    ignoreWhen: (before) => /placeholder|<em>/.test(before.slice(-150)),
  },
];

function codeOnly(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

export function findingsIn(text, where) {
  const code = codeOnly(text);
  const out = [];
  for (const rule of RULES) {
    const re = new RegExp(rule.re.source, rule.re.flags.includes("g") ? rule.re.flags : rule.re.flags + "g");
    let m;
    while ((m = re.exec(code)) !== null) {
      if (rule.ignoreWhen && rule.ignoreWhen(code.slice(0, m.index))) continue;
      const line = code.slice(0, m.index).split("\n").length;
      out.push(`${where}:${line} — ${rule.name}: ${code.split("\n")[line - 1].trim().slice(0, 120)}`);
    }
  }
  return out;
}

function trackedFiles() {
  return execSync("git ls-files -co --exclude-standard src", { cwd: DESKTOP, encoding: "utf8" })
    .split("\n")
    .filter((f) => SCAN.test(f) && !SKIP.test(f));
}

function run() {
  const findings = [];
  const openSeen = new Set();
  for (const file of trackedFiles()) {
    let text;
    try {
      text = readFileSync(resolve(DESKTOP, file), "utf8");
    } catch {
      continue;
    }
    const hits = findingsIn(text, file);
    if (hits.length === 0) continue;
    if (KNOWN_OPEN.has(file)) {
      openSeen.add(file);
      continue;
    }
    findings.push(...hits);
  }
  for (const [file, reason] of KNOWN_OPEN) {
    console.log(`  open bypass (tracked): ${file} — ${reason}`);
    if (!openSeen.has(file)) {
      findings.push(
        `${file} is listed as an open bypass but no longer carries a prompt — delete it from KNOWN_OPEN so the list only shrinks.`,
      );
    }
  }
  if (findings.length > 0) {
    console.error("\ncheck:code-prompts FAILED — a platform prompt is written into desktop code:\n");
    for (const f of findings) console.error(`  ${f}`);
    console.error(
      "\nDeclare a mandate in aidream (aidream/services/mandates/client_mandates.py), seed its Holder with this prompt, " +
        "and run it through useLlmPipeline / resolveLocalMandate (desktop/src/lib/local-mandates.ts). Never a prompt in code.",
    );
    process.exit(1);
  }
  console.log("check:code-prompts: no platform prompt in desktop code (outside the tracked open bypasses).");
}

function selfTest() {
  const planted = [
    [
      "the PIPELINE_TEMPLATES shape",
      `export const PIPELINE_TEMPLATES = {\n  summarize: {\n    system:\n      "Summarize the provided text.",\n    user: "{{text}}",\n  },\n};`,
    ],
    [
      "an inline system message",
      `const messages = [{ role: "system" as const, content: "Answer tersely." }, { role: "user", content: q }];`,
    ],
    ["a persona literal", `const p = "You are an expert editor specializing in transcripts.";`],
  ];
  const clean = [
    [
      "the person's own system text from state",
      `const m = [{ role: "system" as const, content: systemPrompt }];`,
    ],
    ["a comment naming the shape", `// system: "never do this" — a comment reads nothing`],
    ["a resolved Holder message", `messages.unshift({ role: "system", content: override });`],
    ["an empty system-prompt state", `const conv = { id, systemPrompt: "", messages: [] };`],
    ["an input placeholder example", `<Textarea placeholder="You are a helpful assistant that…" />`],
  ];
  let ok = true;
  for (const [label, text] of planted) {
    if (findingsIn(text, "planted").length === 0) {
      console.error(`self-test: MISSED ${label}`);
      ok = false;
    }
  }
  for (const [label, text] of clean) {
    const hits = findingsIn(text, "clean");
    if (hits.length > 0) {
      console.error(`self-test: FALSE POSITIVE on ${label}: ${hits.join("; ")}`);
      ok = false;
    }
  }
  if (!ok) process.exit(1);
  console.log(`check:code-prompts self-test: ${planted.length} plants caught, ${clean.length} clean shapes passed.`);
}

if (process.argv.includes("--self-test")) selfTest();
else run();
