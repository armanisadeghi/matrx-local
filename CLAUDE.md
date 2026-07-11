# CLAUDE.md -- Matrx Local

> AI assistant instructions. See ARCHITECTURE.md for full technical reference. See LESSONS.md for hard-won CI/build gotchas.

## Project Overview

Matrx Local is a **Tauri v2 desktop app** (Rust + React) with a **Python/FastAPI sidecar engine** exposing ~80 tools (filesystem, shell, scraping, documents, etc.) via REST and WebSocket for AI Matrx cloud. End-user desktop app, not a developer tool.

**Not a Next.js/Vercel project.** Stack: Tauri v2 (Rust), React 19, TS 7.0 (native compiler), Vite 6, Tailwind 3.4 + shadcn/ui (`darkMode: "class"`), Python 3.13+/FastAPI/Uvicorn, Supabase Auth, pnpm (desktop), uv (Python).

## Key Entry Points

- **Python:** `run.py` → `app/main.py` → `app/tools/dispatcher.py` (tools in `app/tools/tools/`)
- **React:** `desktop/src/App.tsx` → pages in `desktop/src/pages/`, hooks in `desktop/src/hooks/`
- **Rust:** `desktop/src-tauri/src/lib.rs` (sidecar lifecycle, tray, transcription, LLM)
- **Build:** `scripts/build-sidecar.sh`, `specs/*.spec` (PyInstaller per-platform)
- **Auth:** Supabase instance `txzxabzwovsujtloxrus`, publishable key in `desktop/.env`

## Development Commands

```bash
# Python engine (Terminal 1)
uv sync && uv run python run.py

# React frontend (Terminal 2)
cd desktop && pnpm install && pnpm dev   # http://localhost:1420

# Full Tauri desktop
cd desktop && pnpm tauri:dev
```

## Hard Rules

0. **Lifecycle ownership is non-negotiable.** Each level of the process tree
   only touches its own children. When the parent triggers a start or stop,
   that level must cascade the same to its children before reporting done.
   See **ARCHITECTURE.md → Lifecycle & Ownership** for the full contract.

   - **Rust never pkills cloudflared, the scraper, the proxy, or any other
     engine-spawned process.** Cloudflared and friends are children of the
     Python engine, not of Rust. Rust signals the engine via
     `POST /admin/shutdown` (or SIGTERM as fallback); the engine cascades to
     its own children during its lifespan teardown. Adding a `pkill` to
     `lib.rs` for an engine-owned process re-introduces the race that
     produces "ended unexpectedly" crash reports.
   - **The Python engine never touches llama-server.** llama-server is a
     Rust-owned child (`desktop/src-tauri/src/lib.rs` setup() auto-start +
     `kill_orphaned_llama_server` + `LlmServer::start/stop`). It is
     INTENTIONALLY OMITTED from `app/preflight.py` SERVICES — the Tauri
     setup auto-starts llama-server within ~1s of boot, and if preflight
     listed it, the engine would kill the llama-server Rust just spawned
     ~7s later. That was a real bug; do not re-introduce it. If a Python
     code path needs to reason about llama-server status, talk to it via
     `/connect-local-llm` (or `app/services/ai/local_llm_registry.py`) —
     never via process scanning or signals.
   - **The engine never expects Rust to clean up its children.** When the
     engine receives a shutdown signal, it stops every child it owns — and
     reports done only after the last one is stopped.
   - **llama-server spawns are observable.** Every llama-server spawn —
     auto-start in `lib.rs setup()` AND every `start_llm_server` Tauri
     command invocation — emits `[llm-autostart]` / `[llm-cmd]` log lines
     to the unified log. If you ever see llama-server running and don't
     know who started it, grep those prefixes.
   - **Every state change goes through `app/launcher.py`.** Call
     `registry.starting/ready/degraded/failed/stopping/stopped`. The
     `[launcher] <service> → <state>` lines are the source of truth — do
     not duplicate them in feature modules. Adding a new managed service is
     two lines (call `starting()` before, `ready()` or `failed()` after).
   - **Failures auto-emit a diagnostic snapshot** to
     `~/.matrx/diagnostics/`. If you find yourself wanting to add an
     ad-hoc `print(state)` in a stop/start path, you instead want to attach
     metadata to the registry record (`registry.annotate(name, ...)` or
     pass kwargs to `failed()`). The snapshot will pick it up automatically.
   - **The detached safety-net subprocess in `lib.rs` is the parachute, not
     the primary chute.** It only fires after `graceful_shutdown_sync` has
     had a chance to complete (5s SIGTERM-then-SIGKILL ladder). If the
     normal shutdown chain ran to completion, every pkill in the safety net
     is a no-op. Do not extend it as a substitute for fixing a real
     ownership bug.

1. **scraper-service/ is read-only** — Git subtree from `aidream` repo. Never edit directly. Use `./scripts/update-scraper.sh`. Source repo: `/Users/armanisadeghi/Code/aidream-current/scraper-service` (editable there).

2. **Module isolation** — Scraper's `app/` aliased as `scraper_app/` via `sys.modules` in `app/services/scraper/engine.py`. No naming conflicts.

3. **Graceful degradation** — Engine works without PostgreSQL (memory cache) or Brave API (search disabled). Never add hard dependencies on these. Playwright, psutil, zeroconf are always-available core deps.

4. **Port 22140** — Default engine port. Auto-scans 22140–22159. Discovery file: `~/.matrx/local.json`.

5. **Every Python import must be in pyproject.toml** — No bare `try/except ImportError` as a substitute for declaring deps. Add package and `uv sync` in the same commit. Optional extras: `[transcription]` (openai-whisper), `[image-gen]` (torch+diffusers, multi-GB, not in `all`). TTS deps (kokoro-onnx, soundfile) are core — always installed.

6. **PyInstaller hidden imports must sync** — Packages PyInstaller can't auto-discover (e.g., `python_multipart`) go in all 4 `.spec` files under `specs/` AND `scripts/build-sidecar.sh` fallback. Use Python import name, not pip name. Omitting causes silent runtime failures in compiled sidecar only.

7. **llama-server must be signed on macOS** — Re-sign with `codesign --force --timestamp --options runtime --sign "$APPLE_SIGNING_IDENTITY"` before `tauri-action`. Ad-hoc signatures from llama.cpp releases are rejected by Gatekeeper on end-user machines.

8. **Tauri JSON Configs must be strict** — Do not use `"$comment"`, `"_comment"`, or any other non-schema properties in `tauri.conf.json` (or platform overlays like `tauri.macos.conf.json`). The Tauri CLI v2 strictly validates the merged config against its schema, and unexpected properties will fail the CI build.

## External Connections

Three separate concerns — do not confuse them:

1. **Supabase Auth** — Instance `txzxabzwovsujtloxrus`. Uses **publishable key** (not anon key). All ops use user JWT. Never use service role key. **Never reference `SUPABASE_JWT_SECRET`** — this is a desktop app running on the user's machine; there is no secure place to keep a server-side JWT signing secret. The `/extension/*` surface validates incoming tokens via JWKS for asymmetric algorithms (RS256/ES256) when `SUPABASE_URL` is set, and falls back to bearer-presence verification over loopback for HS256 tokens. See `app/api/extension_auth.py` for the full posture and `docs/MATRX_EXTEND_CONNECTION.md` for the rationale.
2. **Remote Scraper Server** — `scraper.app.matrxserver.com`. REST API with Bearer token (API key or Supabase JWT). Its PostgreSQL is internal-only — no direct DB access.
3. **Local Scraper Cache** — Optional local PostgreSQL via `DATABASE_URL` for persistent scrape cache. Defaults to in-memory TTLCache. This is NOT the remote server's DB.

## Env Files

- **Root `.env`** — Python engine config (API_KEY, SCRAPER_API_KEY, etc.). Not committed.
- **`desktop/.env`** — Supabase client (VITE_* vars only). Not committed.
- Comment out values instead of deleting, with a note for Arman.
- Full env var reference in ARCHITECTURE.md.

## Database Migrations

**Rule: Never create a migration without immediately applying it.**

Migrations live in `migrations/NNN_name.sql`. Apply via Supabase MCP (`apply_migration`) — project `txzxabzwovsujtloxrus`. Verify with `execute_sql`. Update task trackers.

Unapplied migrations cause `PGRST204` runtime errors. If you find one on disk, apply it before doing anything else.

## React Patterns — Critical Rules

These prevent **production outages** (infinite API polling loops that flooded the engine). Every rule maps to a shipped bug.

### `actions` objects must be stable

Every hook returning `[state, actions]` must wrap `actions` in `useMemo`:

```ts
// WRONG — new reference every render → infinite loops
const actions = { doThing, doOtherThing };

// CORRECT
const actions = useMemo(() => ({ doThing, doOtherThing }), [doThing, doOtherThing]);
```

### Never use `actions` as a useEffect dependency

```ts
// WRONG
useEffect(() => { actions.refresh(); }, [actions]);

// CORRECT — list the specific stable callback
useEffect(() => { refresh(); }, []);
```

### Init fetches belong in the hook, not the page

A page-level `useEffect([actions])` re-runs every render (state update → re-render → new ref → loop). Put init fetches in `useEffect([])` inside the hook.

### Persistent state belongs in Context, not page-level hooks

State surviving tab switches must live in a Context Provider at app level (`App.tsx`). Pages call `useFooApp()` (context) not `useFoo()` (new instance).

Existing singletons: `LlmProvider`, `TtsProvider`, `TranscriptionProvider`, `WakeWordProvider`, `TranscriptionSessionsProvider`, `PermissionsProvider`, `AudioDevicesProvider`, `DownloadManagerProvider`.

### Polling intervals must be narrowly gated

Depend on the specific boolean being watched, not a broad object. Always include cleanup.

```ts
// WRONG — restarts every render because of `actions` dep
useEffect(() => {
  if (state.status?.is_downloading) {
    const id = setInterval(() => actions.refreshStatus(), 2000);
    return () => clearInterval(id);
  }
}, [state.status?.is_downloading, actions]);

// CORRECT
useEffect(() => {
  if (!status?.is_downloading) return;
  const id = setInterval(() => void refreshStatus(), 2000);
  return () => clearInterval(id);
}, [status?.is_downloading, refreshStatus]);
```

### Focus/visibility handlers must be intentional

Only for re-fetching data changed externally (e.g., HF token set in browser). Never re-initialize state or trigger full reloads on focus — causes loops in production.

## Task Tracking

- **`AGENT_TASKS.md`** — Bugs, issues, improvements. Update immediately on discovery or resolution.
- **`KNOWN_DEFECTS.md`** — Platform-standard defect ledger (find-it-own-it: every defect you spot gets an entry with evidence; fixes flip status, entries are never deleted).
- **`.arman/ARMAN_TASKS.md`** — Manual tasks for Arman. Simple checkbox + brief instructions.

Never let a discovered issue go untracked. Add it to the right file and continue.

## Preferences

- Work systematically, one task at a time
- Track all issues immediately in AGENT_TASKS.md
- Production-grade only — no stubs, no TODOs, no placeholder logic
- Keep solutions simple; avoid over-engineering
- Keep going until done or stuck
- OK to edit .env files — comment out, don't delete
- Update docs when code changes

---

## Cross-Repo Integration with matrx-extend

The matrx-extend Chrome extension is a primary client of this engine. Integration map and protocols:
- Connection details: [docs/MATRX_EXTEND_CONNECTION.md](./docs/MATRX_EXTEND_CONNECTION.md)
- Skill for working on this connection: `.cursor/skills/connect-matrx-extend/SKILL.md`
- Master cross-repo doc (in matrx-extend): `/Users/armanisadeghi/code/matrx-extend/docs/CROSS_REPO_INTEGRATION.md`
- Task pipeline for cross-repo work: `.matrx/` (TASKS_FROM_USER → AGENT_TASKS → AGENT_INSTRUCTIONS). Note: legacy root `AGENT_TASKS.md` retained as read-only history; new tasks go in `.matrx/`.

**Channel B status (matrx-extend ↔ matrx-local): FULLY ACTIVE, engine-side verified (2026-07-10).** `/extension/rpc` dispatches `health` / `version` / `capabilities` / `tool` (→ the full ~80-tool dispatcher); `/extension/ws` services engine→extension tool invocations (callId protocol, `app/api/extension_invoke.py`); and the inbound Supabase-Broadcast router (`app/api/cross_component_router.py`) dispatches `kind:"rpc"` envelopes into the SAME `HANDLERS` registry via `extension_handlers.invoke_command` and replies on the channel (envelope `v: 2`). Round trips are pinned by `tests/smoke/test_extension_channel.py` (real engine on 22199: HTTP `tool`→SystemInfo, WS hello/ping/pong, full reverse-invoke round trip incl. error path) and `tests/characterization/test_broadcast_rpc_dispatch.py`. Only true in-browser E2E remains manual — steps in `docs/MATRX_EXTEND_CONNECTION.md` § Verification status. The remote-control (tunnel) chain runbook lives in the same doc.
