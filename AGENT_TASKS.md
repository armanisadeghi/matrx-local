# Matrx Local — Task Tracker

> **NOTE (2026-05-06):** The canonical task inbox is now `.matrx/AGENT_TASKS.md` (with `.matrx/TASKS_FROM_USER.md` as the user-facing inbox and `.matrx/AGENT_INSTRUCTIONS.md` as the rules). Existing entries below are read-only history; new tasks go to `.matrx/`.

---

> **Living doc.** Add new issues the moment you find them. Mark done items immediately.
> Last cleaned: 2026-03-30.

---

## 🔴 Blocked — Requires External Fix

These cannot be resolved by changes to this repo. They are blocked on upstream packages or external services.

- [x] **matrx-ai circular import — GenericOpenAIChat completely broken** — RESOLVED for matrx-local 2026-07-10 (matrx-ai 0.3.0 migration, Phase 3): local LLM routing works — the engine imports `matrx_ai.orchestrator` before any provider import (grep `import-order fix` in app/services/ai/), which breaks the cycle deterministically; the ImportError guard in local_llm_registry.py is deleted and registration is verified by `tests/smoke/test_ai_client_host.py::test_runtime_model_registration`. The UNDERLYING cycle still exists in matrx-ai 0.3.0 for a cold `matrx_ai.providers` import — tracked as an upstream task in `.matrx/AGENT_TASKS.md` (fix: lazy import in orchestrator/executor.py; then delete the three workaround imports here).

- [ ] **matrx-ai server-side ORM leaking into desktop** — The `matrx-ai` / chat stack still hits the server-side DB in places. Blocks confident production shipping until client-only/local paths through `matrx-ai` are verified and any server-side assumptions are guarded.

---

## 🟠 Active Bugs & Regressions

Known broken things in the current release that need fixing.

- [x] **🔴 P0 — Download manager: bytes never reach disk (audit 2026-05-04)** — **RESOLVED 2026-07-10**: Python `_download_part` had already gained a real write path (`app/services/downloads/manager.py:814` tmp open, `:827` `fh.write(chunk)` before counting, `:873` `tmp.replace(dest)`); the Rust internal path was still discarding and was fixed in commit `a158612a7` (per-chunk write → `.part` tmp → fsync → atomic rename; `metadata.dest_dir` required, loud failure otherwise). Ledger: `KNOWN_DEFECTS.md` MXL-D-001. Remaining audit phases → MXL-D-012/013/014. Original claim: — Both `desktop/src-tauri/src/downloads/manager.rs:download_part:993` and `app/services/downloads/manager.py:_download_part:643` read chunks from the network, count them, and discard them with no `file.write_all` in the loop (Rust `buf.clear()` → bytes vanish; Python identical pattern). The progress bar reports network reads accurately, but the file never lands on disk. Anything routed through these paths "completes" at 100% with no data → LLM/Whisper/TTS loaders hang or crash. Root cause and full architectural fix plan in [docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md](docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md). Phase 1 of that doc is the immediate fix (open file before loop, write per-chunk before counting). Phases 2–6 cover progress accuracy, hooking 5 orphan downloaders into the manager, unifying the dual Rust/Python managers, elastic bandwidth-aware concurrency, and a queue-aware UI panel.

- [ ] **🟠 P1 — Download progress freezes on slow connections (audit 2026-05-04)** — Both managers accumulate chunks in a `chunk_size` buffer (`if buf.len() < chunk_size { continue; }`) before counting/emitting. On <1 MB/s connections the progress bar visibly freezes for 30+ seconds at a time. Also: `content_length().unwrap_or(0)` makes percent stay 0% forever for chunked-encoding responses (HF, Cloudfront). And there's no heartbeat — 60s idle timeout with nothing in between makes stalls indistinguishable from hangs. See [docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md](docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md) Phase 2.

- [ ] **🟠 P1 — Five downloads bypass the unified manager (audit 2026-05-04)** — `desktop/src-tauri/src/transcription/downloader.rs`, `app/services/wake_word/models.py`, `app/api/setup_routes.py:_download_transcription_model`, `app/api/setup_routes.py:_download_cloudflared`, `app/tools/tools/transfer.py` all do their own downloads with progress reported via callback or custom SSE events, none appear in the global download panel; the `transfer` tool is fully silent on multi-GB files. Plus: Rust + Python managers are independent with separate SQLite stores → duplicate work and conflicting state. Audit doc Phases 3–4.

- [ ] **🟡 P2 — Queue panel doesn't show pending or aggregate state (audit 2026-05-04)** — `DownloadManagerModal` lists downloads but doesn't separate active from pending or show aggregate bandwidth, stage badges (downloading vs verifying vs extracting vs moving), or stall detection. Users don't know "there are 4 more queued behind this one." Bandwidth-aware concurrency exists in `manager.rs:should_expand_slots:267` but never expands in practice — source-rate-limited downloads (HF) leave bandwidth on the table. Audit doc Phases 5–6 + UI mockup in the same doc.

- [x] **Process lifecycle & shutdown overhaul (2026-04-01)** — Root-caused and fixed: (1) `graceful_shutdown_sync` was blocking the main thread for up to 28s, causing macOS to SIGKILL Tauri and log a crash/"unexpected shutdown"; (2) `activation_policy` was never changed, so the Dock icon persisted when the window was hidden to tray — users relaunched the app from Finder, producing two Dock icons and a second instance; (3) Python S5 network service stops (`proxy.stop()`, `tm.stop()`, `engine.stop()`) had no timeout caps, blocking teardown indefinitely on stuck Playwright/TCP. Fixes: all three `graceful_shutdown_sync` call sites now spawn background threads (main thread stays responsive to macOS watchdog); `set_activation_policy(Accessory)` on window-hide / `set_activation_policy(Regular)` on window-show (macOS only); `asyncio.wait_for` with 5s/7s/8s caps on proxy/tunnel/scraper stops; parent-watchdog poll reduced from 2s → 0.5s; `use-llm.ts` listener cleanup fixed to unlisten on effect re-run.

- [x] **Image gen tab: 401 "Authorization header required"** — Fixed 2026-03-31: `imageGenFetch` in `api.ts` did not attach the same `Authorization: Bearer` JWT as other engine calls; auth middleware rejected `/image-gen/*`. Also added client `emitClientLog` on failures, Python `logger.warning` on missing token, and clearer Image tab error UI (sign-in vs HF token vs engine). **Commit and push** these files if not yet on `origin/main`.
- [x] **Image gen E2E audit follow-ups (2026-03-31)** — HunyuanDiT catalog used wrong pipeline (now `hunyuan` + `HunyuanDiTPipeline`); generation no longer holds the service lock for the full diffusion forward pass; HF UI copy points to **Settings → API keys**; `delete_user_key` clears injected `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN`; removed dead `desktop/src/lib/image-gen/*` client; smoke tests use sentinel ids to avoid accidental multi-GB downloads in CI.
- [x] **Image gen: consumer installer (2026-03-31)** — Replaced "run `uv sync --extra image-gen`" developer error screen with a one-click "Install now" button + real-time progress bar. Packages install into `~/.matrx/image-gen-packages/` (or Windows `%LOCALAPPDATA%\AI Matrx\image-gen-packages\`). `runtime_hook.py` + `main.py` lifespan inject the directory into `sys.path` on engine start. New: `app/services/image_gen/installer.py`, new routes `POST /image-gen/install`, `GET /image-gen/install/status`, `GET /image-gen/install/stream` (SSE), new `ImageGenInstaller` component in `LocalModels.tsx`.

- [x] **Windows: /downloads/stream SSE auth loop (2026-04-02)** — `DownloadManagerContext.tsx` opened `EventSource` to `/downloads/stream` without a `?token=` query param. `AuthMiddleware` rejected every attempt (401), causing exponential-backoff reconnects that saturated to 30s. Fixed: `connect()` now calls `engine.getAccessToken()` and appends `?token=` to the SSE URL, matching the pattern used by `connectWebSocket` and `streamImageGenInstall`. Added `getAccessToken()` public method on the engine API class.

- [x] **Windows: tunnel/start 503 — cloudflared URL never captured (2026-04-02)** — `POST /tunnel/start` returned 503 after 30s timeout on Windows. Root cause unclear (binary not found, immediate exit, or firewall blocking). Fixed: (1) added `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` creation flags on Windows to prevent console window popup in sidecar context; (2) upgraded cloudflared output logging from DEBUG to INFO for first 30 lines so failures are visible in sidecar logs; (3) `start()` now races `process.wait()` against the URL event — exits early if cloudflared crashes instead of waiting the full 30s; (4) improved 503 error messages to distinguish "exited early" vs "running but no URL". Next step: check Windows sidecar logs after rebuild to see what cloudflared actually prints.

- [x] **First-run setup incomplete — core components downloaded on-demand instead of at install (2026-04-02)** — Previous setup only installed Playwright + storage dirs. TTS model (~337 MB), cloudflared binary (~30 MB), and transcription model (~150 MB) were downloaded lazily on first use, causing features to appear broken on fresh installs. Fixed: (1) setup wizard now uses `mode=first_run` which downloads all core dependencies in one pass; (2) added `_check_tts()` and `_check_cloudflared()` status checks to `GET /setup/status`; (3) added `_download_tts_model()` and `_download_cloudflared()` async generators with SSE progress to `_install_stream()`; (4) both added to `_BLOCKING_COMPONENTS` so setup won't complete until they're ready; (5) all download functions use `.tmp` → rename pattern for crash safety; (6) fixed `Path.rename()` on Windows (must `unlink` destination first); (7) fixed Windows ARM64 machine normalization in cloudflared download URL resolver.

- [x] **Cloudflared ARM64 and download safety (2026-04-02)** — `_get_download_url()` used case-sensitive machine comparison that failed on Windows ARM64 (`"ARM64"` vs `"arm64"`). `_ensure_binary()` wrote directly to final path (no `.tmp`). Fixed both: case-aware normalization per-platform, and atomic `.tmp` → rename download pattern.

- [ ] **Health check false-positives on slow engine start** — `isHealthy()` in `api.ts` and Rust's `check_engine_health` both call `/tools/list` with a 2-second timeout. Heavy first-load (scraper init, model warmup) can exceed 2 s and incorrectly flip the engine to "disconnected" in the UI. Fix: either switch to a lightweight `/health` endpoint, or increase the timeout to 5–8 s for the startup grace window.

- [ ] **Chat: "Agent not found" silent failure after agent pick** — `ChatPanel` calls `.find()` across the loaded agent list; if the agent ID isn't present (e.g. sync hasn't completed), `activeAgent` silently becomes `null` — no error toast, next send proceeds agentlessly. Fix: show a toast / retry when `found === undefined`, and gate the send button until sync completes.

- [ ] **`transcription_auto_init` setting has no effect** — The toggle exists in `Configurations.tsx` and syncs to cloud, but neither `Voice.tsx` nor any transcription hook reads it. The Rust layer auto-initializes unconditionally at startup, ignoring the user's preference. Fix: read `transcriptionAutoInit` from settings in `use-transcription.ts` and call `init_transcription` conditionally.

- [ ] **Voice E2E needs real-device confirmation** — Full setup → record → transcript with the current Rust pipeline has not been confirmed on physical hardware. Specifically verify: model download, model load, mic permission grant, wake-word trigger, segment streaming, and session persistence.

---

## 🟡 Important Missing Features

These are gaps in committed functionality — things we said the app does but it doesn't yet, or things users will expect.

- [x] **Gemma 4 llama.cpp support unblocked (2026-05-08)** — Bumped `LLAMA_VERSION` from `b8519` → `b9076` in `scripts/download-llama-server.sh`. `gemma4` architecture support landed in early April 2026 via PRs #21309/#21326/#21390/#21418, with parser/tokenizer fixes through `b8950`. All four catalog entries (E2B, E4B, 26B-A4B, 31B) refreshed with byte-precise sizes verified against HuggingFace `X-Linked-Size` (8 main GGUFs + 4 mmproj F16). Issue #21655 (M4 Metal regression) was closed by the reporter as a false alarm. Issue #21402 (CUDA mmproj SIGABRT) only affects RTX 5090 sm_120a with stale unsloth mmproj checksums; current unsloth files (refreshed 2026-04-04) are clean. `--jinja` is already enabled globally in `build_server_args` so the embedded Jinja chat template ships with each Gemma 4 GGUF works without further changes. Audio input still pending separate libmtmd work (see line below).

- [ ] **Multimodal image input UI for local LLM (2026-04-02)** — Infrastructure for multimodal is in place: `ChatMessage.content` now supports OpenAI-compatible content arrays (`text` + `image_url` parts), `LlmModelInfo` has `mmproj_filename`/`mmproj_url` fields, `build_server_args` passes `--mmproj` to llama-server, and auto-start resolves mmproj from the catalog. Missing: (1) UI for attaching/pasting images in the chat input, (2) base64 encoding + content array construction in the API layer, (3) mmproj download integration in the download manager (download mmproj alongside the main model).

- [ ] **Gemma 4 E2B/E4B audio input via llama.cpp** — The E2B and E4B models support audio input natively, but llama.cpp audio support is experimental (only Ultravox/Voxtral/Qwen2.5-Omni). Gemma 4 audio encoding will need a dedicated libmtmd integration in llama.cpp. Video input is also WIP (PR #20224). These capabilities will require llama.cpp upstream work before we can expose them.

- [ ] **Chat: "Local" tab routing to llama-server** — `Chat.tsx` has no tab or mechanism to route messages to the local llama-server. Users can only use cloud providers from Chat. The LLM page runs inference but there is no in-chat UI for it. Need a "Local" mode/tab that sends messages to the running llama-server via `/v1/chat/completions`.

- [ ] **Image gen: FLUX.1 Dev token gate** — FLUX.1 Dev requires a HuggingFace token AND license acceptance. Before loading the model, surface a pre-check: read the HF token from `/settings/api-keys/huggingface/value` and show a blocking warning if absent or if the license hasn't been accepted.

- [x] **Image gen: model download progress** — Done 2026-07-09 (media-gen overhaul): weights now download via the universal DownloadManager (per-file `hf_hub_download` loop + on-disk byte polling → real percent/speed/ETA on `/downloads/stream`). `POST /image-gen/download`; `from_pretrained` never downloads (`local_files_only=True`).

- [x] **Image gen: VRAM gating** — Done 2026-07-09: `app/services/media_gen/hardware.py` cross-references the cached `/hardware` profile (unified memory on Apple Silicon, CUDA VRAM otherwise); `/image-gen/models` and `/video-gen/models` return `hardware_ok` + `hardware_reason` per model, and `/load` refuses (409) incompatible models.

- [ ] **Voice: no partial/streaming transcription results** — Whisper operates on full 5-second chunks; users see nothing until each chunk completes. Consider overlapping windows or VAD-triggered early flush to reduce perceived latency.

- [ ] **Voice: English-only hardcoded** — All Whisper params hardcode `.language("en")`. No UI for language selection. Should read from a settings key and expose a picker in the Voice setup tab.

- [ ] **Voice: transcription sessions not synced to cloud** — Sessions live entirely in `localStorage` (500-entry cap). Settings sync exists but session data does not. Users lose sessions on reinstall or device switch.

- [ ] **QA: Launch at login and tray minimize end-to-end** — Code is wired (`tauri_plugin_autostart`, `CloseToTray`), but these have not been confirmed working on both macOS and Windows in a signed production build. Needs physical-device QA pass.

- [ ] **QA: Headless scraping toggle** — The `headless_scraping` setting exists in the UI but has not been confirmed to flow through to Playwright's `headless` flag in the actual scrape paths. Needs a live smoke test.

---

## 🔵 Tech Debt & Code Quality

Refactors and cleanups that improve maintainability but don't change user-facing behavior.

- [ ] **Voice.tsx needs splitting into component files** — `Voice.tsx` is ~2,800 lines with `SetupTab`, `TranscribeTab`, `ModelsTab`, `DevicesTab`, and utilities all inline. Extract each tab into `desktop/src/components/voice/*.tsx`. No behavior change, pure refactor.

- [ ] **Health check: add a lightweight `/health` endpoint** — The Python engine has no dedicated health route; `/tools/list` is a heavyweight endpoint used as a proxy for "engine alive". Add `GET /health` → `{"status": "ok"}` and update both `api.ts` and Rust's `check_engine_health` to use it.

- [ ] **Zustand for shared app state** — `App.tsx` currently prop-drills auth/engine/settings state. A `desktop/src/stores/app-store.ts` with slices would clean this up. Keep `EngineAPI` singleton. Defer until active bugs are resolved.

- [x] **Image gen: move model cache to `~/.matrx/image-models/`** — Done 2026-07-09: weights land in `~/.matrx/image-models/<sanitized-id>/` (video: `~/.matrx/video-models/`) with a `.download-complete` marker; loaders use `local_files_only=True` against that dir. See `app/services/media_gen/paths.py`.

- [ ] **API key extras** — Rotation timestamps and optional OS keychain storage (macOS Keychain, Windows Credential Manager). Currently all keys stored as SQLite plaintext blob.

- [ ] **Configurations consistency gaps** — `CONFIGURATIONS.md` §6 documents concrete issues: theme saved from Configurations does not update `matrx-theme` / live DOM; `chatMaxConversations` exists in `AppSettings` but `use-chat.ts` still caps with hardcoded `100`; `transcriptionAudioDevice` vs `matrx-selected-audio-device` dual storage; wake keyword in cloud vs Rust `transcription.json`. Close each gap or document intentional behavior.

- [ ] **Dark mode audit** — A spot-check of Settings.tsx, Tools.tsx, and Activity.tsx shows clean semantic tokens (no hardcoded light colors found). Remaining risk: modal overlays, dropdown menus, and third-party component wrappers. Do a full-app visual pass in light mode to catch edge cases.

---

## ⚪ Wish List / Future Work

Nice-to-haves and exploratory ideas. Not on the immediate roadmap.

- [x] **Video generation engine** — Done 2026-07-09: `app/services/video_gen/` (Wan 2.2 TI2V-5B default, Wan 2.1 1.3B, LTX-Video) + `/video-gen/*` routes (status/models/download/load/unload/generate + job polling + mp4 result). One job at a time, step-callback progress, history in `~/.matrx/generated/videos/jobs.json`, hard hardware gate (Apple Silicon 16GB+ or CUDA 8GB+).

- [ ] **ComfyUI sidecar** — Evaluate embedding ComfyUI as a second optional sidecar for advanced image/video workflows. Would replace or augment the Diffusers integration. See `local-llm-inference-integration.md` for the sidecar pattern.

- [ ] **Cloud AI relay** — Authenticated relay endpoint on AIDream so users don't need to paste provider API keys. `matrx-ai` should prefer the relay when no user key is set. Requires server + client coordination.

- [ ] **Cloud-assigned job queue** — Cloud pushes scrape jobs to the desktop app for background execution. Needs a durable job queue and status callbacks.

- [x] **Tools tab UX overhaul** — Fixed 2026-03-31: `ConsumerPanel` in `Tools.tsx` was dispatching by category instead of by selected tool — all category panels (FilesPanel, NetworkPanel, etc.) ignored `selectedSchema` and showed a fixed tab UI regardless of which tool was clicked. Replaced with `GenericToolPanel` for all tools, which renders a schema-driven form for the exact selected tool. `key={schema.toolName}` on `ToolForm` ensures clean state resets on tool switches. Only `SystemResources`/`TopProcesses` retain the live-gauge MonitoringPanel. Both simple (form) and advanced (JSON) modes now respond correctly to per-tool selection.

- [ ] **Welcome cards → agent IDs + Settings favorites** — Chat page welcome screen should deep-link to specific agents and surface user-pinned settings. Product design needed first.

- [ ] **Proxy full end-to-end test** — `POST /proxy/test` exercises the local forward proxy only. A real E2E test would hit `MAIN_SERVER` with a callback. Needs the `MAIN_SERVER` URL from Arman (see `.arman/ARMAN_TASKS.md`).

- [ ] **Wake word: sherpa-onnx KWS** — When stable Rust bindings exist, replace or augment the current dual-WhisperContext approach. Would reduce CPU usage significantly.

- [ ] **Wake-on-LAN / smart home APIs** — Future tool integrations.

- [ ] **Reverse tunnel** — Cloud-to-local proxy path for receiving inbound calls without a public IP.

- [ ] **App icon** — Replace the placeholder icon with final branded artwork before a major public launch.

---

## 📋 Doc Hygiene

Files that need attention. Do not delete without Arman's confirmation.

| Path | Action |
|------|--------|
| `.arman/in-progress/proxy/INITIAL.md` | **Delete** — generic Electron proxy essay, not project-specific |
| `.arman/pending/ui-overhaul/INITIAL.md` | **Archive or delete** — large draft superseded by incremental work |
| `PLATFORM_AUDIT.md` | **Delete or rewrite** — claims `initPlatformCtx` is never called; this is false (`use-engine.ts` calls it). Misleading if left as-is |

_Valid reference docs (keep, update when features change):_ `ARCHITECTURE.md`, `CONFIGURATIONS.md`, `CLAUDE.md`, `local-llm-inference-integration.md`, `whisper-transcription-integration.md`, `docs/react-migration-notes-api.md`, `docs/proxy-integration-guide.md`, `docs/proxy-testing-guide.md`, `docs/local-storage-architecture.md`, `docs/activity-log.md`.
