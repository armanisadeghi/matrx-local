# Landing checklist — 30 seconds, right before you finish

Scan the triggers. Touched none of them? You're done — say so and finish.
Touched one? Stop and verify the paired item. Every line below is a shipped
production regression, not a hypothetical.

1. **Added or moved a Python import/dependency?** → declare it in
   `pyproject.toml` AND all 4 `specs/*.spec` hidden-imports AND the
   `scripts/build-sidecar.sh` fallback. The dev engine imports fine either
   way; only the compiled sidecar dies — silently, on users' machines.
2. **Anything touching torch/transformers/numpy/ML packages?** → the managed
   media runtime slot is the ONLY torch provider
   (`app/services/optional_packages/FEATURE.md`). Never install a second
   stack, never import the slot outside its contract, never weaken the
   tripwire tests.
3. **Startup, shutdown, or process spawn/kill?** → each layer stops only its
   OWN children (CLAUDE.md Hard Rule 0), then run `./scripts/smoke.sh` — a
   green frontend typecheck (`cd desktop && pnpm typecheck`) is not evidence
   the app starts, quits cleanly, or leaves no orphans.
4. **Shell commands, filesystem paths, signals, or signing?** → must work on
   macOS AND Windows (pkill vs taskkill, `/` vs `\`, codesign vs signtool).
   Green on your Mac ≠ done.
5. **Ports, `~/.matrx`, or discovery files?** → dev and live are separate
   worlds (dev = `~/.matrx-dev`, ports 22240+). Never hardcode a live
   port/path into anything a dev run executes (MXL-D-043).
6. **React hooks returning actions, or any polling?** → `useMemo` the actions
   object, never list `actions` in a useEffect dep array, gate intervals on
   the specific boolean (`docs/REACT_PATTERNS.md` — this class flooded the
   production engine).
7. **Wrote a `.sql` migration?** → it changed nothing until it is applied to
   live Supabase, verified, and types are regenerated — in this same session.
8. **New config value, URL, or key?** → pick its category first (CLAUDE.md
   § Configuration posture): env vars are dev-only, no shipped
   behavior may depend on `.env`, and our secrets never exist on the client.
9. **Anything that shows a permission status, count, or "Grant" button?** →
   there is ONE model (`app/services/permissions/FEATURE.md`): the authority
   table decides who reads and who prompts; counts come only from
   `summarizePermissions()`; a not-yet-asked permission is never "denied",
   and nobody is sent to a Settings pane where the app is not listed yet.
   The Dashboard once showed "6/18" then "10/19" and a Location "Request
   access" that did nothing.

10. **Any app-exit, relaunch, or updater path in `lib.rs`?** → on Unix EVERY
    path must end in `libc::_exit(0)` (GGML's atexit destructors call
    `ggml_abort()` → a real SIGABRT crash report), and a relaunch must spawn
    its own successor after releasing the single-instance socket, because
    `_exit(0)` also skips tauri's relaunch. Gating the bypass on the exit code
    is what crashed the app on every single release (17 in 72 h).
    Guards: `shutdown_exit_tests`, `successor_spawn_tests`,
    `relaunch_target_tests` in `desktop/src-tauri/src/lib.rs`.
    **And the bypass on tauri's event loop is only half the class**: tao
    implements no `applicationShouldTerminate:`, so `-[NSApplication
    terminate:]` (menu-bar Quit, Cmd+Q, Dock Quit, AppleEvent quit, OS logout)
    runs libc `exit()` with no tauri frame at all — SR-01 went on crashing
    through 1.4.115 for exactly that reason. That path is swizzled in
    `desktop/src-tauri/src/appkit_terminate.rs`; guard:
    `appkit_terminate::tests`.
11. **Any engine spawn/exit handling, or a surface that reports the engine
    down?** → a dead engine must come back on its own (bounded, backed off,
    `supervise_engine_exit`), and the user must SEE it with the engine's own
    cause — never a silent dead engine, never a spinner, never a bare
    "something went wrong". Guards: `engine_supervisor_tests` (Rust) +
    `desktop/src/components/recovery/EngineSupervisorBanner.test.tsx`.
12. **Anything that decides whether a browser/model/binary is "installed"?** →
    presence means the exact build THIS process resolves, not "a directory
    exists": a complete install of the wrong revision is unlaunchable, and
    calling it present made the one-click repair skip the only download that
    could fix it (18.6+ hours of silently dead browser scraping).
    Guard: `tests/unit/test_browser_build_mismatch.py`.
13. **Any page, hook, or poller that fetches from the engine at mount?** → it
    must never render a red failure for "auth has not resolved yet". The one
    seam is `EngineAPI.request()` + `resolveNativeVaultEngineAccessToken()`:
    the provider waits for the session daemon's first answer before reporting
    no token, and a 401 we sent with no Authorization header is re-asked once
    before it becomes an error or arms the signed-out fence. Five Coding
    Sessions reads 401'd at mount on installed 1.4.115 and a signed-in Arman
    read "Couldn't read your conversations" until he clicked Refresh.
    Guards: `desktop/src/lib/api-mount-auth-readiness.test.ts`,
    `desktop/src/lib/native-vault-auth-readiness.test.ts`.
