#!/usr/bin/env node
/**
 * Sync generated Python API/stream types from AIDream into the desktop app.
 *
 * Usage:
 *   pnpm sync-types                 # live/default backend
 *   pnpm sync-types:local           # http://localhost:8000
 *   pnpm sync-types -- --url URL    # explicit backend
 *
 * This delegates to aidream/scripts/sync-types.mjs, which calls /schema/manifest
 * and /schema/all, then generates OpenAPI types from the returned schema.
 */

import { execSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, "..");
const args = process.argv.slice(2);

function getArg(name, fallback) {
  const idx = args.indexOf(name);
  if (idx !== -1 && idx + 1 < args.length) return args[idx + 1];
  return fallback;
}

const useLocal = args.includes("--local") || args.includes("--fast");
const localBackendUrl = "http://localhost:8000";
// This developer-only schema generator accepts --url for an explicit target.
// It must not perpetuate an environment-based shipped server-URL path.
const liveBackendUrl = "https://server.app.matrxserver.com";
const backendUrl = getArg("--url", useLocal ? localBackendUrl : liveBackendUrl);
const outDir = resolve(PROJECT_ROOT, "src/types/python-generated");
const aidreamSyncScript = resolve(PROJECT_ROOT, "../../aidream/scripts/sync-types.mjs");

console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  desktop sync-types");
console.log(`  Backend: ${backendUrl}`);
console.log(`  Output:  ${outDir}`);
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

if (!existsSync(aidreamSyncScript)) {
  console.error(`  ✗ AIDream sync script not found: ${aidreamSyncScript}`);
  console.error("    Expected aidream to be cloned next to matrx-local.");
  process.exit(1);
}

// openapi-typescript needs the TypeScript JS API (`ts.factory`), which the
// desktop's TypeScript 7 (native) no longer ships. matrx-extend pins a
// TypeScript 6 peer for exactly this reason, so when the aidream script has
// written the bundles but its `openapi-typescript` step fails here, finish
// api-types.ts with the sibling matrx-extend install (same generator, same
// flags). Verified 2026-08-22.
const siblingOpenapiTs = resolve(
  PROJECT_ROOT,
  "../../matrx-extend/node_modules/.bin/openapi-typescript",
);
const openapiJson = resolve(outDir, "openapi.json");
const apiTypes = resolve(outDir, "api-types.ts");

function bundlesWrittenThisRun(startedAt) {
  return existsSync(openapiJson) && statSync(openapiJson).mtimeMs >= startedAt;
}

const startedAt = Date.now();
try {
  execSync(`node "${aidreamSyncScript}" --url "${backendUrl}" --out "${outDir}"`, {
    stdio: "inherit",
    cwd: PROJECT_ROOT,
  });
} catch {
  if (bundlesWrittenThisRun(startedAt) && existsSync(siblingOpenapiTs)) {
    console.log("\n  openapi-typescript failed under TypeScript 7 — using matrx-extend's install...");
    try {
      execSync(
        `"${siblingOpenapiTs}" "${openapiJson}" --default-non-nullable false -o "${apiTypes}"`,
        { stdio: "inherit", cwd: resolve(PROJECT_ROOT, "../../matrx-extend") },
      );
      console.log("  ✓ api-types.ts (via matrx-extend's openapi-typescript)");
    } catch {
      console.error("\n  ✗ openapi-typescript failed in matrx-extend too; api-types.ts is stale.");
      process.exit(1);
    }
  } else {
    console.error("\n  ✗ Failed to sync generated Python types.");
    console.error(`    Backend attempted: ${backendUrl}`);
    console.error("    If the live server is down, run: pnpm sync-types:local");
    console.error(
      "    If only openapi-typescript failed: clone matrx-extend beside this repo and `pnpm install` there.",
    );
    process.exit(1);
  }
}

console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  desktop sync-types complete");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");
