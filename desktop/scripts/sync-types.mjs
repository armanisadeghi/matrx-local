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
import { existsSync } from "node:fs";
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
const liveBackendUrl =
  process.env.VITE_AIDREAM_SERVER_URL_LIVE ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://server.app.matrxserver.com";
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

try {
  execSync(`node "${aidreamSyncScript}" --url "${backendUrl}" --out "${outDir}"`, {
    stdio: "inherit",
    cwd: PROJECT_ROOT,
  });
} catch {
  console.error("\n  ✗ Failed to sync generated Python types.");
  console.error(`    Backend attempted: ${backendUrl}`);
  console.error("    If the live server is down, run: pnpm sync-types:local");
  process.exit(1);
}

console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
console.log("  desktop sync-types complete");
console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");
