# Matrx Local — Packaged Desktop Architecture

Code-derived reference for what runs when the desktop app is built and launched. Not a feature guide — just the process tree, connection paths, and where to look in the repo.

## Packaged runtime (4 layers)

1. **Tauri 2 (Rust)** — native shell: WebView, tray, autostart, updater, deep links. Spawns and owns the Python sidecar + **llama-server**. Also runs **Whisper transcription**, **wake-word**, and a **Rust download manager** (SQLite) via Tauri `invoke` (not HTTP).

2. **React UI (Vite build)** — almost all product logic talks to the local engine over **HTTP/WS on `127.0.0.1:22140–22159`** (port from `~/.matrx/local.json`). Also hits **Supabase** and **AIDream cloud** directly from the browser layer (not through Python).

3. **Python sidecar (`matrx-engine`)** — PyInstaller-frozen **FastAPI/Uvicorn** on loopback. Hub for ~80 tools, local AI orchestration, scraping, TTS, image gen, permissions, extension bridge, etc. On startup it brings up **local SQLite**, optional **Playwright/Chromium** (scraping), an optional **local HTTP proxy**, and optionally **cloudflared** (remote tunnel).

4. **Bundled binaries** — `matrx-engine`, `llama-server`, `cloudflared` (Tauri `externalBin`).

## APIs and data paths

- **UI → local engine** — primary path; HTTP/WS on loopback (`22140–22159`)
- **UI → Supabase** — auth and shared cloud data (direct from React, not via Python)
- **UI → AIDream server** — cloud models, agents, compute targets (direct HTTPS)
- **Engine → remote scraper** — `scraper.app.matrxserver.com` via engine client
- **Engine → local Postgres** — optional scrape cache (`DATABASE_URL`); degrades to in-memory if absent
- **Chrome extension ↔ engine** — `/extension/rpc` and `/extension/ws`
- **Local SQLite** — `~/.matrx/matrx.db`; accessed through the engine only, not from the UI

## Process ownership

Each layer kills only its own children on shutdown:

| Owner | Children |
|---|---|
| Rust (Tauri) | Python sidecar, llama-server |
| Python engine | cloudflared, Playwright/Chromium, local proxy, scraper subprocesses |

llama-server is **not** in the engine's preflight registry — Rust owns it exclusively.

## Heavy subsystems

Non-trivial capabilities that usually need setup, model downloads, or health checks — not all running at launch.

**Rust (Tauri `invoke`)**

- **Local LLM** — llama-server / llama.cpp; model file + setup wizard
- **Whisper transcription** — ONNX model download + auto-load on startup
- **Wake word** — mic thread; shares transcription model dir
- **Download manager** — Rust SQLite queue for HF / large file pulls

**Python engine**

- **Playwright + Chromium** — auto-install to `~/.matrx/playwright-browsers` on first use
- **Local scraper** — Playwright pool; starts with engine
- **Remote scraper client** — cloud API; needs API key
- **TTS (Kokoro)** — ONNX auto-download on first request
- **Image generation** — Diffusers/torch; explicit download → load (no silent pulls)
- **Video generation** — lazy model load on demand
- **Cloud tunnel** — cloudflared subprocess when enabled
- **Local HTTP proxy** — optional loopback proxy
- **AI engine** — cloud provider keys, JWT cache, tool registry
- **Extension bridge** — `/extension/*` for matrx-extend Chrome extension

**Optional / external**

- **Local Postgres scrape cache** — `DATABASE_URL`; falls back to in-memory
- **Supabase settings sync** — cloud ↔ local when configured

## Packaging

`pnpm build` (Vite) → Tauri bundles WebView app + sidecars → platform installers (NSIS / DMG / DEB). Python frozen separately via PyInstaller (`scripts/build-sidecar.sh`).

## Dev vs packaged

| Mode | Engine | llama-server |
|---|---|---|
| Dev (`pnpm tauri:dev`) | Run manually: `uv run python run.py` | Auto-started by Rust if model on disk |
| Packaged | Rust spawns `matrx-engine` sidecar | Same |

## Code references

| Thing | Files |
|---|---|
| Tauri shell + sidecar spawn | [`desktop/src-tauri/src/lib.rs`](../../desktop/src-tauri/src/lib.rs), [`desktop/src-tauri/tauri.conf.json`](../../desktop/src-tauri/tauri.conf.json) |
| UI → engine HTTP client | [`desktop/src/lib/api.ts`](../../desktop/src/lib/api.ts), [`desktop/src/lib/sidecar.ts`](../../desktop/src/lib/sidecar.ts) |
| PyInstaller sidecar build | [`scripts/build-sidecar.sh`](../../scripts/build-sidecar.sh), [`specs/`](../../specs/) |
| Python engine (FastAPI) | [`app/main.py`](../../app/main.py), [`run.py`](../../run.py) |
| llama-server (Rust-owned) | [`desktop/src-tauri/src/llm/`](../../desktop/src-tauri/src/llm/), [`desktop/src-tauri/binaries/`](../../desktop/src-tauri/binaries/) |
| Whisper + wake-word (Rust) | [`desktop/src-tauri/src/transcription/`](../../desktop/src-tauri/src/transcription/) |
| Rust download manager | [`desktop/src-tauri/src/downloads/`](../../desktop/src-tauri/src/downloads/) |
| cloudflared tunnel | [`app/services/tunnel/manager.py`](../../app/services/tunnel/manager.py), [`app/preflight.py`](../../app/preflight.py) |
| Local SQLite DB | [`app/services/local_db/database.py`](../../app/services/local_db/database.py) |
| Supabase (direct from UI) | [`desktop/src/lib/supabase.ts`](../../desktop/src/lib/supabase.ts) |
| AIDream cloud (direct from UI) | [`desktop/src/lib/aidream-client.ts`](../../desktop/src/lib/aidream-client.ts) |
| Chrome extension bridge | [`app/api/extension_routes.py`](../../app/api/extension_routes.py) |
| Playwright / local scraper | [`app/services/scraper/engine.py`](../../app/services/scraper/engine.py), [`app/main.py`](../../app/main.py) |
| Remote scraper API | [`app/services/scraper/remote_client.py`](../../app/services/scraper/remote_client.py) |
| TTS | [`app/services/tts/service.py`](../../app/services/tts/service.py) |
| Image generation | [`app/services/image_gen/service.py`](../../app/services/image_gen/service.py) |
| Video generation | [`app/services/video_gen/service.py`](../../app/services/video_gen/service.py) |
| Process ownership / orphans | [`app/preflight.py`](../../app/preflight.py) |
