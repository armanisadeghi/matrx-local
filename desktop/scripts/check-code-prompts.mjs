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
 * of the `local.*` mandates (2026-09-25). Round 2 (same day) converted the
 * rest: the Voice page's AI Polish styles, the Confidential Chat prompt
 * library (and its `system_prompt` catalog copies), and the Tools / Raw JSON
 * modes that called the local model with no mandate at all. This guard keeps
 * those shapes from coming back. Offline is a data LOCATION, never a licence
 * to carry prompts — the engine caches each mandate's last answer
 * (GET /local-mandates/{key}).
 *
 * WHAT THIS FAILS ON (code only — comments are stripped)
 *
 *   1. A prompt-template property: `system: "…"` / `systemPrompt: "…"` /
 *      `instructions: "…"` whose value is a string literal.
 *   2. A system message built from a literal: `role: "system"` with a
 *      `content: "…"` literal in the same object.
 *   3. An instruction-voice literal: a string that opens "You are …" (the
 *      persona line every system prompt starts with), or an output-format
 *      instruction that opens "Return ONLY / Respond ONLY / Output ONLY …"
 *      (the JSON instruction polish-presets.ts used to append).
 *   4. A read of the retired `system_prompt` catalog kind (a prompt library
 *      served around the mandate system).
 *   5. A LOCAL-MODEL CALL WITH NO MANDATE: every call of a local-model
 *      primitive (chatCompletion / streamCompletion / structuredOutput /
 *      runAgenticLoop / callWithTools, or a POST to /v1/chat/completions) must
 *      have a mandate resolution (resolveLocalMandate / runLocalMandate, or a
 *      RESOLVING_HELPERS function) earlier in its ENCLOSING function. The
 *      function boundary is found by pattern (the nearest preceding
 *      `function` / `const x = async` / `useCallback(async`), so it proves the
 *      resolution is on the call's path by text — not that its messages are
 *      the ones sent.
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
const KNOWN_OPEN = new Map([]);

/** The module that DEFINES the local-model primitives (it calls none on its own). */
const LOCAL_MODEL_PRIMITIVES = "src/lib/llm/api.ts";

/**
 * Files that call the local model with no mandate — OPEN bypasses awaiting a
 * ruling. May only SHRINK.
 */
const KNOWN_UNMANDATED = new Map([]);

const LOCAL_MODEL_CALL =
  /\b(chatCompletion|streamCompletion|structuredOutput|runAgenticLoop|callWithTools)\s*(<[^>()]*>)?\s*\(|["'`][^"'`\n]*\/v1\/chat\/completions/g;

/**
 * Functions that resolve a local-model mandate themselves, so a call made
 * after them is on a mandated path. Each is defined next to its
 * resolveLocalMandate call — keep this list that short.
 *   confidentialChatPrefix — pages/LocalModels.tsx, resolves local.confidential_chat.
 */
const RESOLVING_HELPERS = ["confidentialChatPrefix"];
const MANDATE_RESOLUTION = new RegExp(
  `\\b(resolveLocalMandate|runLocalMandate|${RESOLVING_HELPERS.join("|")})\\s*\\(`,
);
const FUNCTION_START =
  /(^|\n)[ \t]*(export\s+)?(async\s+)?function\b|(^|\n)[ \t]*(export\s+)?const\s+\w+\s*=\s*(useCallback\(\s*)?async\b/g;

/** Line numbers of local-model calls with no mandate resolution on their path. */
export function unmandatedLocalCalls(text) {
  const code = codeOnly(text);
  const starts = [...code.matchAll(FUNCTION_START)].map((m) => m.index);
  const out = [];
  for (const m of code.matchAll(LOCAL_MODEL_CALL)) {
    let start = 0;
    for (const s of starts) if (s < m.index) start = s;
    if (!MANDATE_RESOLUTION.test(code.slice(start, m.index))) {
      out.push(code.slice(0, m.index).split("\n").length);
    }
  }
  return out;
}

export function unmandatedLocalCall(text) {
  return unmandatedLocalCalls(text).length > 0;
}

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
    name: "output-format instruction literal (\"Return ONLY …\")",
    re: /["'`](Return|Respond|Output) ONLY\b/,
  },
  {
    name: "read of the retired system_prompt catalog kind",
    re: /fetchCatalog\s*(<[^>()]*>)?\s*\(\s*["'`]system_prompt["'`]/s,
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
  const unmandatedSeen = new Set();
  for (const file of trackedFiles()) {
    let text;
    try {
      text = readFileSync(resolve(DESKTOP, file), "utf8");
    } catch {
      continue;
    }
    const unmandatedLines = file === LOCAL_MODEL_PRIMITIVES ? [] : unmandatedLocalCalls(text);
    if (unmandatedLines.length > 0) {
      if (KNOWN_UNMANDATED.has(file)) unmandatedSeen.add(file);
      else
        findings.push(
          `${file}:${unmandatedLines.join(",")} — calls the local model with no mandate: resolve one first (resolveLocalMandate / runLocalMandate in src/lib/local-mandates.ts, or useLlmPipeline) and run ITS messages and settings.`,
        );
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
  for (const [file, reason] of KNOWN_UNMANDATED) {
    console.log(`  open unmandated local-model call (tracked): ${file} — ${reason}`);
    if (!unmandatedSeen.has(file)) {
      findings.push(
        `${file} is listed as an unmandated local-model caller but no longer is one — delete it from KNOWN_UNMANDATED so the list only shrinks.`,
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
    [
      "the POLISH_JSON_INSTRUCTION shape",
      `export const X = 'Return ONLY a JSON object with exactly four fields: "title"';`,
    ],
    ["a system_prompt catalog read", `const e = await fetchCatalog<{ id: string }>("system_prompt");`],
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
  const unmandated = [
    [
      "a second handler borrowing the first one's resolution",
      `const handleA = async () => {\n  await resolveLocalMandate(K.a);\n};\nconst handleRaw = async () => {\n  await fetch(\`http://127.0.0.1:\${port}/v1/chat/completions\`, {});\n};`,
    ],
    ["a Tools-mode loop with no mandate", `await runAgenticLoop(port, history, tools, invoke, onStep, signal);`],
    ["a raw POST with no mandate", "await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, { method: \"POST\" });"],
    ["a typed structured call", `const r = await structuredOutput<Out>(port, messages, schema);`],
  ];
  const mandated = [
    [
      "a run through the mandate",
      `const h = await resolveLocalMandate(K.toolCallingChat);\nawait runAgenticLoop(port, [...lead, ...history], tools, invoke, onStep, signal, 10, h.settings);`,
    ],
    ["a comment naming the call", `// runAgenticLoop(port, …) is only ever called after resolveLocalMandate`],
    [
      "a chat send after the page's resolving helper",
      `const handleSend = async () => {\n  const prefix = await confidentialChatPrefix();\n  const stream = streamCompletion(port, [...prefix, ...msgs], {});\n};`,
    ],
  ];
  let ok = true;
  for (const [label, text] of unmandated) {
    if (!unmandatedLocalCall(text)) {
      console.error(`self-test: MISSED ${label}`);
      ok = false;
    }
  }
  for (const [label, text] of mandated) {
    if (unmandatedLocalCall(text)) {
      console.error(`self-test: FALSE POSITIVE on ${label}`);
      ok = false;
    }
  }
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
  console.log(
    `check:code-prompts self-test: ${planted.length + unmandated.length} plants caught, ${clean.length + mandated.length} clean shapes passed.`,
  );
}

if (process.argv.includes("--self-test")) selfTest();
else run();
