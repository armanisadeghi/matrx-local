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
