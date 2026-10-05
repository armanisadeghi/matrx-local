#!/usr/bin/env node
// await-matrx-latest — WAIT UNTIL NPM SERVES THE TARBALL OF EVERY @ai-matrx PACKAGE'S `latest`,
// BEFORE a package update touches the lockfile.
//
// WHY (2026-10-05): npm lists a freshly published version as `latest` minutes before its tarball
// stops answering 404. `pnpm update "@ai-matrx/*" --latest` (and `npm update`) inside a release
// then died with ERR_PNPM_FETCH_404 (agents-0.45.1 in matrx-extend, design-system-0.66.1 in the
// matrx-local hosted release). This is the ONE behavior every repo's `sync:matrx-packages` and
// release catch-up runs first: poll until all answer 200, or exit 1 after the window so a 404 that
// outlives it still fails the release.
//
// Canonical source: matrx-ship/scripts/await-matrx-latest.mjs. It is dependency-free so each repo
// carries a byte-identical copy in its own scripts/ (matrx-frontend has the same logic inside
// scripts/check-matrx-lockfile.mjs --await-latest). Change it HERE, then copy to every repo.
//
//   node scripts/await-matrx-latest.mjs [--root <dir>] [--max-wait-minutes N] [--poll-seconds N]
//   node scripts/await-matrx-latest.mjs --self-test
//
// Packages checked: every @ai-matrx/* named in package.json files under --root (root + up to two
// levels down, node_modules skipped) and in pnpm-lock.yaml / package-lock.json.
// Env: MATRX_AWAIT_REGISTRY (default https://registry.npmjs.org) — tests only.
// Exit: 0 all served · 1 still not served after the window · 2 registry unreachable (UNMEASURED).

import { createServer } from "node:http";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCOPE = "@ai-matrx/";
const CONCURRENCY = 8;
const REQUEST_TIMEOUT_MS = 15_000;
// Limits are knobs: agent-chosen starting values, review 2026-11-05.
export const AWAIT_MAX_MINUTES = 10;
export const AWAIT_POLL_SECONDS = 20;

const registryOf = () => (process.env.MATRX_AWAIT_REGISTRY ?? "https://registry.npmjs.org").replace(/\/$/, "");

function manifests(root, depth = 0, out = []) {
  const pj = join(root, "package.json");
  if (existsSync(pj)) out.push(pj);
  if (depth >= 2) return out;
  for (const e of readdirSync(root, { withFileTypes: true })) {
    if (!e.isDirectory() || e.name === "node_modules" || e.name.startsWith(".")) continue;
    manifests(join(root, e.name), depth + 1, out);
  }
  return out;
}

export function packageNames(root) {
  const names = new Set();
  for (const file of manifests(root)) {
    let json;
    try { json = JSON.parse(readFileSync(file, "utf8")); } catch { continue; }
    for (const section of ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"]) {
      for (const n of Object.keys(json[section] ?? {})) if (n.startsWith(SCOPE)) names.add(n);
    }
  }
  const lockDirs = [root, ...readdirSync(root, { withFileTypes: true }).filter((e) => e.isDirectory() && e.name !== "node_modules" && !e.name.startsWith(".")).map((e) => join(root, e.name))];
  for (const dir of lockDirs) {
    for (const lock of ["pnpm-lock.yaml", "package-lock.json"]) {
      const p = join(dir, lock);
      if (!existsSync(p) || !statSync(p).isFile()) continue;
      for (const m of readFileSync(p, "utf8").matchAll(/@ai-matrx\/[a-z0-9._-]+/g)) names.add(m[0]);
    }
  }
  return [...names].sort();
}

export function tarballUrl(name, version) {
  return `${registryOf()}/${name}/-/${name.slice(SCOPE.length)}-${version}.tgz`;
}

async function pool(items, fn) {
  const results = new Array(items.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, items.length) }, async () => {
      while (next < items.length) {
        const i = next++;
        results[i] = await fn(items[i]);
      }
    }),
  );
  return results;
}

/** { name, version, url, status } — status is the tarball's HTTP status, or a string for packument/network trouble. */
async function check(name) {
  try {
    const res = await fetch(`${registryOf()}/${name}`, {
      headers: { accept: "application/vnd.npm.install-v1+json" },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!res.ok) return { name, version: "?", url: `${registryOf()}/${name}`, status: `packument HTTP ${res.status}` };
    const doc = await res.json();
    const version = doc["dist-tags"]?.latest;
    if (!version) return { name, version: "?", url: `${registryOf()}/${name}`, status: "no latest dist-tag" };
    const url = doc.versions?.[version]?.dist?.tarball ?? tarballUrl(name, version);
    const head = await fetch(url, { method: "HEAD", redirect: "follow", signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
    return { name, version, url, status: head.status };
  } catch (err) {
    return { name, version: "?", url: registryOf(), status: `network: ${err?.cause?.code ?? err?.name ?? err}` };
  }
}

export async function awaitLatest(root, maxMinutes = AWAIT_MAX_MINUTES, pollSeconds = AWAIT_POLL_SECONDS) {
  const names = packageNames(root);
  if (!names.length) {
    console.log("[await-matrx-latest] no @ai-matrx package declared or locked — nothing to wait for.");
    return 0;
  }
  const deadline = Date.now() + maxMinutes * 60_000;
  for (let attempt = 1; ; attempt++) {
    const bad = (await pool(names, check)).filter((r) => r.status !== 200);
    if (!bad.length) {
      console.log(`[await-matrx-latest] npm serves latest for all ${names.length} @ai-matrx package(s) — safe to update.`);
      return 0;
    }
    if (bad.every((r) => typeof r.status === "string" && r.status.startsWith("network:"))) {
      console.error(`[await-matrx-latest] UNMEASURED — the npm registry could not be reached (${bad[0].status}).`);
      return 2;
    }
    if (Date.now() + pollSeconds * 1000 > deadline) {
      console.error(`[FAIL] [await-matrx-latest] npm latest is still NOT served after ${maxMinutes} min — nothing was updated:`);
      for (const r of bad) console.error(`    ${r.name}@${r.version}  ${r.status}  ${r.url}`);
      console.error("  Re-run once the tarball answers 200.");
      return 1;
    }
    console.log(`[await-matrx-latest] waiting for npm to serve ${bad.map((r) => `${r.name}@${r.version} (${r.status})`).join(", ")} — attempt ${attempt}, retry in ${pollSeconds}s`);
    await new Promise((r) => setTimeout(r, pollSeconds * 1000));
  }
}

async function selfTest() {
  // Stub registry: pkg "stub" latest 1.0.0; tarball 404 until `serveAt`, then 200. "gone" never serves.
  let tarballOk = false;
  let hits = 0;
  const server = createServer((req, res) => {
    const base = `http://127.0.0.1:${server.address().port}`;
    const sendJson = (o) => { res.setHeader("content-type", "application/json"); res.end(JSON.stringify(o)); };
    if (req.url === "/@ai-matrx/stub") return sendJson({ "dist-tags": { latest: "1.0.0" }, versions: { "1.0.0": { dist: { tarball: `${base}/stub.tgz` } } } });
    if (req.url === "/@ai-matrx/gone") return sendJson({ "dist-tags": { latest: "9.9.9" }, versions: { "9.9.9": { dist: { tarball: `${base}/gone.tgz` } } } });
    if (req.url === "/stub.tgz") { hits++; res.statusCode = tarballOk ? 200 : 404; return res.end(); }
    res.statusCode = 404; res.end();
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  process.env.MATRX_AWAIT_REGISTRY = `http://127.0.0.1:${server.address().port}`;
  const { mkdtempSync, writeFileSync, rmSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const dir = mkdtempSync(join(tmpdir(), "await-matrx-"));
  const failures = [];
  try {
    const mk = (name) => writeFileSync(join(dir, "package.json"), JSON.stringify({ dependencies: { [`${SCOPE}${name}`]: "latest" } }));
    mk("stub");
    // RED: tarball 404 for the whole (tiny) window -> waits (>1 poll) then exits 1.
    const t0 = Date.now();
    const red = await awaitLatest(dir, 0.03, 0.5);
    if (red !== 1) failures.push(`RED: a never-served tarball exited ${red}, expected 1`);
    if (hits < 2) failures.push(`RED: expected the await to poll at least twice, polled ${hits}`);
    if (Date.now() - t0 < 500) failures.push("RED: returned without waiting");
    // WAIT-THEN-PASS: tarball starts answering 200 mid-wait.
    setTimeout(() => { tarballOk = true; }, 1200);
    const t1 = Date.now();
    const mid = await awaitLatest(dir, 0.5, 0.5);
    if (mid !== 0) failures.push(`WAIT: a tarball that appears mid-wait exited ${mid}, expected 0`);
    if (Date.now() - t1 < 1000) failures.push("WAIT: passed before the tarball was served");
    // GREEN: already served.
    const green = await awaitLatest(dir, 0.1, 0.5);
    if (green !== 0) failures.push(`GREEN: a served tarball exited ${green}, expected 0`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
    server.close();
  }
  if (failures.length) {
    console.error("await-matrx-latest --self-test FAILED:");
    for (const f of failures) console.error(`  - ${f}`);
    return 1;
  }
  console.log("await-matrx-latest --self-test OK — waits then fails on a 404 tarball, passes once it answers 200.");
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const argv = process.argv.slice(2);
  const arg = (flag) => { const i = argv.indexOf(flag); return i === -1 ? undefined : argv[i + 1]; };
  let code;
  try {
    code = argv.includes("--self-test")
      ? await selfTest()
      : await awaitLatest(resolve(arg("--root") ?? join(fileURLToPath(import.meta.url), "..", "..")), Number(arg("--max-wait-minutes") ?? AWAIT_MAX_MINUTES), Number(arg("--poll-seconds") ?? AWAIT_POLL_SECONDS));
  } catch (err) {
    console.error(`[await-matrx-latest] UNMEASURED — could not run: ${err?.stack ?? err}`);
    code = 2;
  }
  process.exitCode = code;
}
