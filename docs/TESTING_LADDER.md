# The Testing Ladder — what each mode proves, and what it can never prove

**Read this before deciding how to verify a change.** The worst outcome in
this repo is not a slow test — it is a test in an unrealistic setting that
green-lights code that will die in the packaged app (or breaks code that was
fine). Every rung below is fully trustworthy for a specific class of change
and permanently blind to others. Route each change to the **cheapest rung
whose blind spots don't overlap what you touched.**

## Dev/live isolation — the ground rule (MXL-D-043)

Dev and live are **two parallel worlds** that never touch:

| | LIVE world | DEV world |
|---|---|---|
| Who lives here | The installed/packaged app only (`sys.frozen`) | Every source-run engine, `pnpm dev`, `pnpm tauri:dev` (debug Rust) |
| Home / discovery | `~/.matrx` / `~/.matrx/local.json` | `~/.matrx-dev` / `~/.matrx-dev/local.json` |
| Engine ports | 22140–22159 | 22240–22259 |
| Local DB / settings | `~/.matrx/matrx.db` | `~/.matrx-dev/matrx.db` |
| Cloud instance id | hardware hash | hardware hash salted `"dev"` |
| Orphan sweeps | normal | **off** (a dev engine never kills other processes) |

This is enforced in code, not by discipline: `run.py` self-isolates whenever
it is not the frozen sidecar; debug Rust builds and dev Vite builds
(`import.meta.env.DEV`) look only at the dev world. You cannot pollute the
live app by running `uv run python run.py` anymore. Escape hatch (rare, on
purpose only): `MATRX_LIVE_ENGINE=1` or `./scripts/dev.sh --live`.

**Many agents at once:** the shared dev world is fine for most work — extra
engines bind 22241, 22242, … and the discovery-file clobber guard keeps the
first live owner in place. If your run must not share `matrx.db`/settings with
other agents (schema migrations, settings tests), use `./scripts/dev.sh
--fresh` for a throwaway private home, or set `MATRX_HOME_DIR` yourself.

## The rungs

### 1. Engine only — `./scripts/dev.sh` (or `uv run python run.py`) — seconds
**Trust for:** tool handlers, API endpoints, request/response contracts,
DB/business logic — anything reachable via `curl http://127.0.0.1:22240/...`
or pytest (`tests/smoke/`, `tests/characterization/`).
**Blind to:** packaging (see the poison list), lifecycle ownership, how
Rust/React consume the API.
Remember: `uv sync --all-extras`, never plain `uv sync`.

### 2. React in browser — `cd desktop && pnpm dev` — instant HMR
**Trust for:** rendering, layout, hooks, state, React↔engine REST/WS (against
your rung-1 engine).
**Blind to:** everything behind `invoke()` (tray, llama-server, transcription,
sidecar lifecycle — Rust does not exist in a browser); minified-bundle-only
failures.

### 3. `pnpm tauri:dev` — ~1–2 min
**Trust for:** real Rust — Tauri commands, tray, windows, llama-server
auto-start, the React↔Rust boundary. Engine still runs separately via rung 1
(debug Rust discovers it in the dev world automatically).
**Blind to:** Rust-spawns-sidecar lifecycle, PyInstaller, bundle paths.

### 4. `./scripts/smoke.sh` (web) — ~1–2 min
Production Vite bundle in real Chromium. Run after **any** frontend change —
this is what catches the v1.3.104 class (crash only in the shipped bundle).

### 5. `./scripts/smoke.sh packaged` — ~10–20 min (`--no-build` reruns in seconds)
Real PyInstaller sidecar + real `tauri build`, launched and log-checked.
Catches the entire "works in dev, dead in the bundle" class: hidden imports,
sidecar spawn, engine boot, orphaned children. **This replaces the
push-and-wait-an-hour cycle for almost everything.** Quit the installed app
first. See [SMOKE_HARNESS.md](SMOKE_HARNESS.md).

### 6. Real release via CI — ~1 hour
The only rung that proves codesigning/notarization (Gatekeeper), the
llama-server re-sign (Hard Rule 7), the updater, and installers. Nothing else
needs it.

## The poison list — dev-mode green means NOTHING for these

If your change touches any of these, go straight to rung 5 (or 6 where noted):

- **New Python import or dependency.** Dev has the whole venv; the frozen
  binary only has what PyInstaller discovered. pyproject.toml + all 4
  `specs/*.spec` + `build-sidecar.sh` (Hard Rule 6), then `smoke.sh packaged`.
- **Files read relative to the code** (data files, templates, assets) —
  frozen paths go through `sys._MEIPASS`, nothing like the repo layout.
- **Sidecar lifecycle** (start/stop/kill/orphan logic) — in dev, Rust never
  spawns the engine, so the ownership contract is unexercised.
- **Anything that runs at startup** — green typecheck ≠ the app starts
  (CLAUDE.md). Smoke it.
- **Signing, notarization, updater, installers** — rung 6 only.

## Decision rule

One line: **read your change's blast radius against each rung's "blind to"
list, and run the cheapest rung with no overlap.** A pure tool-handler fix
never needs a build; a new dependency always does.
