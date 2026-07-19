# Smoke Harness — run the app, capture what it logs

**For agents: if you changed anything that runs at startup, run this before you
tell Arman it works. Do not ask him to relay logs or screenshots — get them
yourself.**

```bash
./scripts/smoke.sh              # web — ~1-2 min, run this always
./scripts/smoke.sh packaged     # full build + launch + log capture (~10-20 min)
./scripts/smoke.sh packaged --no-build   # relaunch the last build (fast reruns)
./scripts/smoke.sh all
```

**Windows:** `scripts\smoke.ps1` (same arguments). It is a thin wrapper that
runs `smoke.sh` under Git Bash — the same shell CI already uses to drive
`build-sidecar.sh` on `windows-latest` — so there is one implementation to keep
correct, not two that drift. Requires Git for Windows. macOS, Windows, and
Linux all take the same code path, with only process control (`taskkill` vs.
signals) and the binary location differing.

Everything lands in `.smoke/runs/<timestamp>/` (gitignored). **Read
`summary.md` first** — it is written for you, not for a human, and it says
exactly which check failed and pastes the offending log lines. `app.log` /
`web.log` hold the full output when you need more.

Exit code is the verdict: `0` clean, `1` something failed.

## Why this exists

v1.3.104 shipped a crash that killed the app on boot for **every user**: a
component calling `useNavigate()` was mounted outside `<HashRouter>`, so React
threw on the first render and everyone got the ErrorBoundary screen.

Typecheck passed. The bundle built. CI was green. `release.sh` was happy.

Nothing in the entire pipeline ever *ran the app*. That is the hole this closes.

## What each mode actually proves

| Mode | Runs | Catches | Misses |
|---|---|---|---|
| `web` | Production Vite bundle in real Chromium (`e2e/boot.spec.ts`) | Render crashes, router bugs, uncaught errors, console errors — in the **minified bundle we ship**, not just dev | Anything Rust or Python: sidecar spawn, tray, updater, engine boot |
| `packaged` | PyInstaller sidecar + real `tauri build`, then launches the packaged `.app` | Startup errors that **only exist in the compiled artifact** (missing PyInstaller hidden imports, sidecar spawn failures, engine boot errors), engine not reaching `/health`, app not exiting on SIGTERM, **orphaned children** (the lifecycle-ownership contract in CLAUDE.md) | Anything needing a signed/notarized build or a real user session |

`packaged` runs the app's binary directly rather than via `open -a` (macOS),
because `open` hands the process to launchd and its stdout/stderr — the whole
point — is lost.

## How close is `packaged` to the real thing, and what does it touch?

**Close, with two named gaps.** It is the same PyInstaller sidecar and the same
`tauri build` output that `release.yml` produces, launched as a real process,
with the real Rust setup, real sidecar spawn, and real engine boot. What it is
**not**:

1. **Not signed or notarized.** `release.yml` does the codesigning and the
   llama-server re-sign (Hard Rule 7). Gatekeeper rejections and notarization
   failures are invisible here — they can only be caught by installing a real
   release build.
2. **Not a real user session.** It launches the app and watches it start; it
   does not click through the UI. `web` mode is what exercises the React tree.
3. **Not an installer test.** It builds only the runnable app
   (`tauri build --bundles app`), never the DMG/NSIS/MSI installer and never
   the updater tarball. Install/upgrade paths are out of scope — they belong to
   `release.yml`.

   This is deliberate, and learned the hard way: a full `tauri build` runs
   `bundle_dmg.sh`, which **mounts the disk image and pops a Finder window over
   your screen** mid-run, and then hard-fails on a missing
   `TAURI_SIGNING_PRIVATE_KEY` for the updater bundle — after the `.app` was
   already built fine. Neither has anything to do with "does the app start".
   Do not "fix" a smoke failure by adding the signing key; remove the bundle.

**It never uses live or dev state.** Every run creates a third TEST world:

- `MATRX_HOME_DIR` and OS application-data roots live under
  `.smoke/runs/<timestamp>/`; the test application never reads or writes
  `~/.matrx` or `~/.matrx-dev`. The harness only takes a read-only live PID/
  health snapshot for its non-interference assertion.
- The engine receives an exact run-specific port in the 23000–65000 range;
  the production renderer is compiled to scan only that same test range.
- Cloud coordination and global orphan sweeps are disabled. Shutdown remains
  strict but targets only child PIDs owned by the test process.
- Packaged smoke disables single-instance forwarding so the test binary starts
  beside the installed app instead of handing control to it.
- If a live engine existed before packaged smoke, the harness verifies its PID
  and health are unchanged afterward.

The installed app and development engines may remain running throughout both
web and packaged smoke. Pre-existing `cloudflared`/`llama-server` PIDs are
snapshotted only for the orphan diff and are never signaled by the test.

## Reading a failure

`summary.md` sections are the checks, in order, each ✅ or ❌. A ❌ includes the
log lines that triggered it. The fatal-line patterns live in `FATAL_PATTERNS`
in [scripts/smoke.sh](../scripts/smoke.sh) — Rust panics, Python tracebacks,
the ErrorBoundary headline, React Router's context error, `ended unexpectedly`,
missing-module errors, port collisions, and `[launcher] <svc> → failed`.

If a real startup failure ever slips through with a distinctive log line, **add
its pattern** — a smoke signal nobody trusts is a smoke signal nobody reads.
Equally: do not widen `BENIGN_PATTERNS` to make a red run go green. That is how
v1.3.104 happened.

## What is NOT wired up

`smoke.sh` is **deliberately not part of `scripts/release.sh`**. It has to earn
trust on real releases first. Arman decides when it becomes a release gate.

What *is* automatic: `e2e/boot.spec.ts` runs in CI (`.github/workflows/ci.yml`,
frontend job) against the production bundle on every push. The authenticated
half self-skips without `desktop/.env.test`, so it needs no secrets.

Related: [UI_TESTING.md](UI_TESTING.md) (the Playwright suite and its test
account), [official/lifecycle-ownership.md](official/lifecycle-ownership.md)
(the shutdown/orphan contract `packaged` mode checks).
