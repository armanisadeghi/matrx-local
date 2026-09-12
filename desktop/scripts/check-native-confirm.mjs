#!/usr/bin/env node
/**
 * Blocks native browser confirmation dialogs in desktop source.
 *
 * The canonical path is `confirm({...})` imported from
 * `@ai-matrx/kit/confirm-opener`, rendered by the design system host. Native
 * browser calls block the renderer and do not respect the app's dialog stack.
 *
 * This is deliberately a narrow static guard, not a JavaScript sandbox: it
 * catches the direct and computed global access forms we have demonstrated.
 * A same-line `native-confirm-exempt: <specific reason>` may suppress exactly
 * that finding; there are no file-wide exemptions.
 */

import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE_ROOT = path.join(ROOT, "src");
const SELF_TEST = process.argv.includes("--self-test");
const EXEMPTION = /native-confirm-exempt:\s*(.{12,})/;
const CANONICAL_IMPORT = "@ai-matrx/kit/confirm-opener";

function sourceFiles(dir = SOURCE_ROOT) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return /\.(?:ts|tsx)$/.test(entry.name) ? [full] : [];
  });
}

function stripComments(text) {
  const blank = (chunk) => chunk.replace(/[^\n]/g, " ");
  return text
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/(^|[^:])\/\/[^\n]*/gm, (match, lead) => lead + " ".repeat(match.length - lead.length));
}

function hasCanonicalConfirmImport(text) {
  const imports = text.match(/\bimport\s+[\s\S]*?\s+from\s+['"]@ai-matrx\/kit\/confirm-opener['"];?/g) ?? [];
  return imports.some((statement) => /\{[^}]*\bconfirm\b[^}]*\}/.test(statement));
}

function lineAt(text, index) {
  return text.slice(0, index).split("\n").length;
}

function isLineExempt(rawText, line) {
  return EXEMPTION.test(rawText.split("\n")[line - 1] ?? "");
}

function addFindings(text, rawText, regex, label, findings) {
  for (const match of text.matchAll(regex)) {
    const line = lineAt(text, match.index ?? 0);
    if (!isLineExempt(rawText, line)) findings.push({ line, label });
  }
}

export function scanNativeConfirm(rawText) {
  const text = stripComments(rawText);
  const findings = [];

  // Direct global aliases, including optional chaining: globalThis.confirm(),
  // window?.confirm().
  addFindings(
    text,
    rawText,
    /\b(?:globalThis|window)(?:\.|\?\.)confirm\s*\(/g,
    "native global confirm",
    findings,
  );

  // Computed aliases with a literal key, including the demonstrated static
  // concatenation form: window["confirm"](), globalThis?.["con" + "firm"]().
  addFindings(
    text,
    rawText,
    /\b(?:globalThis|window)(?:\?\.)?\s*\[\s*(?:(['"`])confirm\1|(['"`])con\2\s*\+\s*(['"`])firm\3)\s*\]\s*(?:\?\.)?\s*\(/g,
    "computed native global confirm",
    findings,
  );

  // Plain `confirm()` resolves to the browser global unless it is the named
  // canonical import. That keeps the approved `await confirm({ ... })` path
  // green while failing the native calls this migration replaces.
  if (!hasCanonicalConfirmImport(text)) {
    addFindings(text, rawText, /(?<![.$\w])confirm\s*\(/g, "unimported confirm", findings);
  }

  return findings;
}

function runCheck() {
  const failures = sourceFiles().flatMap((file) =>
    scanNativeConfirm(readFileSync(file, "utf8")).map(({ line, label }) =>
      `${path.relative(ROOT, file)}:${line}: ${label}; use the imported confirm({ ... }) from ${CANONICAL_IMPORT}`,
    ),
  );
  if (failures.length) {
    console.error("Native browser confirmation is forbidden:\n" + failures.join("\n"));
    process.exitCode = 1;
    return false;
  }
  console.log(`check:native-confirm OK — ${sourceFiles().length} source file(s) scanned.`);
  return true;
}

function expect(condition, message) {
  if (!condition) throw new Error(message);
}

function selfTest() {
  const canonical = `import { confirm } from "${CANONICAL_IMPORT}";\nawait confirm({ title: "Delete" });`;
  expect(scanNativeConfirm(canonical).length === 0, "canonical imported confirm must pass");
  for (const sample of [
    'globalThis.confirm("x")',
    'window?.confirm("x")',
    'globalThis["confirm"]("x")',
    'window?.["confirm"]("x")',
    'globalThis?.["con" + "firm"]("x")',
    'confirm("x")',
  ]) {
    expect(scanNativeConfirm(sample).length === 1, `expected native bypass to fail: ${sample}`);
  }

  // Exercise the actual package command, rather than only this module's
  // helper. It must propagate a non-zero status when a violating source file
  // exists, which is the same command CI and release.sh call.
  const fixture = path.join(SOURCE_ROOT, `__native_confirm_guard_${process.pid}.tsx`);
  writeFileSync(fixture, 'window?.confirm("self-test")\n');
  try {
    let status = 0;
    try {
      execFileSync("pnpm", ["--silent", "check:native-confirm"], {
        cwd: ROOT,
        stdio: "pipe",
      });
    } catch (error) {
      status = error.status ?? 1;
    }
    expect(status !== 0, "the package gate accepted a native confirm fixture");
  } finally {
    rmSync(fixture, { force: true });
  }
  console.log("check:native-confirm self-test PASSED — canonical usage passes and the package gate fails native bypasses.");
}

if (SELF_TEST) selfTest();
else runCheck();
