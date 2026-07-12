# Lifecycle & Ownership

> **NON-NEGOTIABLE.** Summary in [CLAUDE.md](../../CLAUDE.md) Hard Rules §0. Read this doc before changing `lib.rs`, `run.py`, `app/main.py`, `app/launcher.py`, or any service `start()`/`stop()`.

## Principle

Each level only touches **its own children**. Parent start/stop cascades to children before reporting done. No cross-level child management — that causes orphans and "ended unexpectedly" crash reports.

---

## Ownership tree

```
Tauri Rust (OS-owned)
├── TranscriptionManager, WakeWordState (in-process)
├── LlmServer → llama-server subprocess     ← Rust ONLY
└── Engine sidecar → Python subprocess
        ├── cloudflared (TunnelManager)
        ├── HTTP proxy, scraper/Playwright
        ├── sync, scheduler, file watchers
        ├── download manager, matrx-ai mount
        └── …future children
```

---

## Rules (contract)

1. **Rust never pkills cloudflared** (or scraper, proxy, other engine children). Signal engine via `POST /admin/shutdown` or SIGTERM; engine stops cloudflared in lifespan teardown.

2. **Rust owns llama-server end-to-end.** Python has **no** llama-server in `app/preflight.py` SERVICES. Spawn: `lib.rs` setup (`[llm-autostart]`) or `start_llm_server` (`[llm-cmd]`). Reclaim: `kill_orphaned_llama_server()`. Status from Python: `/connect-local-llm` or `local_llm_registry.py` — never signals/pkill.

3. **Engine cleans its own children on shutdown** — reverse startup order in `app/main.py` lifespan.

4. **`preflight.py` reclaims only engine's previous-life children** — cloudflared, Playwright driver/browser (orphan-only, scoped to `PLAYWRIGHT_BROWSERS_PATH`). **Never** llama-server. **Never** kill a live sibling engine answering `/health` on 22140–22159 (`protect_live_owner=True`).

5. **All state transitions via `app/launcher.py`** — `registry.starting/ready/degraded/failed/stopping/stopped`. `[launcher] <service> → <state>` is source of truth. FAILED → auto dump to `~/.matrx/diagnostics/`.

6. **Safety-net subprocess in `lib.rs`** — parachute after 5s SIGTERM/SIGKILL ladder if graceful path crashes mid-shutdown. Not a substitute for correct ownership.

---

## Shutdown sequence (normal)

```
User Quit → Rust graceful_shutdown_sync()
  → safety-net spawn (detached)
  → drop wake-word, transcription, stop llama-server
  → POST /admin/shutdown → engine SIGTERM
  → run.py _handle_exit → uvicorn lifespan teardown
      (sync, scheduler, proxy, tunnel, scraper, …)
  → engine exit → Rust sigterm_then_kill → kill_orphaned_sidecars (engine binaries only)
```

HTTP failure → Rust SIGTERM fallback. Mid-teardown SIGABRT → safety-net pkills orphans.

---

## Startup sequence (normal)

```
Launch → Rust setup: kill_orphaned_llama_server, spawn sidecar (TAURI_SIDECAR=1)
  → run.py: signals, parent watchdog
  → preflight clean_orphans (NOT llama-server)
  → assign_engine_port → ~/.matrx/local.json
  → uvicorn + lifespan (registry.starting/ready per service)
  → UI polls /health
```

---

## Diagnostics

- Auto on FAILED: `~/.matrx/diagnostics/{timestamp}_{service}.json`
- On demand: `POST /admin/diagnose?reason=...`
- Introspect: `GET /admin/status`

---

## Implementing files

| File | Role |
|------|------|
| `app/launcher.py` | ServiceRegistry, diagnostics |
| `app/api/admin_routes.py` | `/admin/status`, `/admin/shutdown`, `/admin/diagnose` |
| `app/main.py` | Lifespan phases |
| `app/preflight.py` | Orphan reclaim, port assignment |
| `app/services/tunnel/manager.py` | cloudflared subprocess |
| `desktop/src-tauri/src/lib.rs` | Rust shutdown chain |
| `desktop/src-tauri/src/llm/server.rs` | LlmServer |
