# Current App Errors — Matrx Local

Last processed: **2026-07-14** (from Matrx Log export) · Last hygiene pass: 2026-07-14 (Inbox cleared — the 2026-07-12 startup-log paste was an entirely PRE-FIX build: replicate/matrx-ai init crash + tools-degraded cascade + NOT-INSTALLED banner all fixed 2026-07-13; Civitai/FLUX/Z-Image download states = MXL-D-047, fixed. Live build now v1.3.111. Only T009 remains open.)

> **Error-dump inbox.** Arman pastes raw log exports (often hundreds of lines,
> hours after the fact — this is a shipped app, re-testing needs a new build)
> into **Inbox** below; a triaging agent reconciles EVERY line into a home,
> then clears the Inbox. This file must SHRINK on every triage pass — it is
> never an archive.
>
> **Triage contract** (`/task-hygiene errors`):
> 1. Fingerprint each error — ignore timestamps, log channel
>    (`[tauri]` / `[server]` / `[syslog]`), and traceback frame noise; same
>    message / root exception = same error. Dedupe against **Unique errors**.
> 2. Every NEW signature gets exactly one home: (a) **quick fix now** — and if
>    something is fixable RIGHT NOW, STOP and tell Arman directly what can be
>    resolved immediately; (b) a `FOUND_DEFECTS.md` entry; (c) a proposed
>    agent task (needs Arman approval); or (d) an ask-Arman item →
>    `.matrx/ARMAN_TASKS.md`. Errors owned by another repo (e.g. server 500s
>    from aidream) get filed in THAT repo, noted here by ID.
> 3. **Clear the Inbox** after triage — the table + IDs are the durable record.
> 4. Resolved rows: prune once a shipped build confirms the fix (or ~2 weeks).
>    An error that returns after resolution gets a NEW row referencing the
>    old ID — never silently re-open.

---

## Inbox (raw paste)

<!-- Paste the next Matrx Log export below this line. -->

_(empty — last paste triaged 2026-07-14: all signatures were pre-fix build noise already resolved; see hygiene note at top.)_

<!-- ARCHIVED pre-fix paste removed 2026-07-14 — full log preserved in git history (CURRENT_ERRORS.md @ 735a8bf10). Every line mapped to a fixed defect: replicate PackageNotFoundError → MXL-D-040; NOT-INSTALLED banner → copy_metadata fix; tools degraded → MXL-D-029 cascade; download fails → MXL-D-047. -->

<!-- former paste start:
=== Matrx Startup Log — 7/12/2026, 7:36:25 PM ===
[stdout] [phase:starting] Engine initializing...

[stdout] [phase:preflight] Checking for lingering processes from previous session...

[stdout] [preflight] Scanning for lingering processes from previous session (services: engine, playwright_driver, playwright_browser, cloudflared)

[stdout] [preflight]   user='armanisadeghi'  protected_pids=[26552, 26579, 26633]

[stdout] [preflight]   engine        : ✓ clean (no lingering processes)

[stdout] [preflight]   playwright_driver: ✓ clean (no lingering processes)

[stdout] [preflight]   playwright_browser: ✓ clean (no lingering processes)

[stdout] [preflight]   cloudflared   : ✓ clean (no lingering processes)

[stdout] [preflight] Done: inspected=1057 orphans_found=0 killed=0 survived=0 protected=0 by_service={'engine': 0, 'playwright_driver': 0, 'playwright_browser': 0, 'cloudflared': 0}

[stdout] [phase:port] Finding available port...

[stdout] [preflight]   engine        : ✓ port 22140 (default)

[stdout] [phase:port] Engine will bind to port 22140

[stdout] [phase:server] Starting server...

[stdout] INFO - Started server process [26633]

[stdout] INFO - Waiting for application startup.

[stdout] INFO - [app/main.py] ── Matrx Local startup ─────────────────────────────────────

[stdout] INFO - [app/main.py] CORS allowed origins: ['https://aimatrx.com', 'https://www.aimatrx.com', 'https://appmatrx.com', 'https://www.appmatrx.com', 'https://mymatrx.com', 'https://www.mymatrx.com', 'https://codematrx.com', 'https://www.codematrx.com', 'https://matrxserver.com', 'https://www.matrxserver.com', 'https://ai-matrx-admin.vercel.app', 'https://ccmjgggbdngllppncmidllcjablcdepl.chromiumapp.org', 'http://localhost:1420', 'http://localhost:3000', 'http://localhost:3001', 'http://localhost:3002', 'http://localhost:5173', 'http://127.0.0.1:1420', 'http://127.0.0.1:3000', 'http://127.0.0.1:3001', 'http://127.0.0.1:3002', 'http://127.0.0.1:5173', 'tauri://localhost', 'http://tauri.localhost']

[stdout] [phase:database] Opening local database...

[stdout] INFO - [app/main.py] Phase 0a: Opening local database...

[stdout] INFO - [local_db] Connected to /Users/armanisadeghi/.matrx/matrx.db

[stdout] INFO - [app/main.py] Phase 0a: Local database ready ✓ (/Users/armanisadeghi/.matrx/matrx.db)

[stdout] [phase:database] Local database ready

[stdout] INFO - [engine] JWT cache warmed from SQLite (user_id=4cf62e4e-2679-484f-b652-034e697418df)

[stdout] DEBUG - [key_manager] Injected key for provider 'anthropic' into os.environ (deprecated shim)

[stdout] DEBUG - [key_manager] Injected key for provider 'google' into os.environ (deprecated shim)

[stdout] DEBUG - [key_manager] Injected key for provider 'cerebras' into os.environ (deprecated shim)

[stdout] DEBUG - [key_manager] Injected key for provider 'xai' into os.environ (deprecated shim)

[stdout] INFO - [key_manager] Loaded 4 user-stored API key(s) into resolver cache ✓

[stdout] INFO - [app/main.py] Phase 0a: Loaded 4 user API key(s) into env ✓

[stdout] INFO - [downloads] Manager started — MAX_CONCURRENT=3

[stdout] INFO - [app/main.py] Phase 0a: Download manager started ✓

[stdout] DEBUG - [image_gen_installer] dynamic_module_utils.py already patched — skipping

[stdout] INFO - [app/main.py] Phase 0a: image-gen packages injected into sys.path ✓

[stdout] INFO - [app/main.py] Phase 0a: media-gen service deps reloaded: image=True video=True

[stdout] [phase:browsers] Checking browser engine...

[stdout] INFO - [app/main.py] Phase 0b: Checking Playwright browsers...

[stdout] DEBUG - [app/main.py] Playwright browsers already present at /Users/armanisadeghi/.matrx/playwright-browsers

[stdout] INFO - [app/main.py] Phase 0b: Playwright browsers ready ✓

[stdout] [phase:browsers] Browser engine ready

[stdout] INFO - [app/main.py] Phase 0c: Probing platform capabilities...

[stdout] INFO - [app/main.py] Phase 0c: Platform capabilities probed ✓

[stdout] INFO - [app/main.py] Phase 0d: Detecting system hardware...

[stdout] INFO - [app/main.py] Phase 0d: Hardware detection complete ✓

[stdout] [phase:ai] Initializing AI engine...

[stdout] INFO - [app/main.py] Phase 1: Initializing matrx-ai engine...

[stdout] INFO - ============================================================

[stdout] INFO - [engine] matrx-ai STARTUP — client-host mode

[stdout] INFO - [engine]   matrx-ai   = NOT INSTALLED

[stdout] INFO - [engine]   matrx-utils= NOT INSTALLED

[stdout] INFO - [engine]   AIDREAM_SERVER_URL_LIVE  = https://server.app.matrxserver.com

[stdout] ERROR - [app/main.py] Phase 1: matrx-ai initialization FAILED — AI endpoints will not work. Check SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in .env

[stdout] Traceback (most recent call last):

[stdout]   File "importlib/metadata/__init__.py", line 407, in from_name

[stdout] StopIteration

[stdout] 

[stdout] During handling of the above exception, another exception occurred:

[stdout] 

[stdout] Traceback (most recent call last):

[stdout]   File "app/main.py", line 452, in lifespan

[stdout]     initialize_matrx_ai()

[stdout]     ~~~~~~~~~~~~~~~~~~~^^

[stdout]   File "app/services/ai/engine.py", line 208, in initialize_matrx_ai

[stdout]     matrx_ai.configure(

[stdout]     ~~~~~~~~~~~~~~~~~~^

[stdout]         api_key_resolver=get_key_resolver(),

[stdout]         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

[stdout]     ...<6 lines>...

[stdout]         source_app="matrx_local",

[stdout]         ^^^^^^^^^^^^^^^^^^^^^^^^^

[stdout]     )

[stdout]     ^

[stdout]   File "matrx_ai/__init__.py", line 177, in configure

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "matrx_ai/client_host/validate.py", line 17, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "matrx_ai/catalog/__init__.py", line 10, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "matrx_ai/catalog/controls.py", line 50, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "matrx_ai/catalog/models.py", line 64, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "matrx_ai/providers/__init__.py", line 49, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "matrx_ai/providers/replicate/__init__.py", line 1, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "matrx_ai/providers/replicate/replicate_image_api.py", line 12, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "replicate/__init__.py", line 1, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "replicate/client.py", line 22, in <module>

[stdout]   File "pyimod02_importers.py", line 457, in exec_module

[stdout]   File "replicate/__about__.py", line 3, in <module>

[stdout]   File "importlib/metadata/__init__.py", line 987, in version

[stdout]   File "importlib/metadata/__init__.py", line 960, in distribution

[stdout]   File "importlib/metadata/__init__.py", line 409, in from_name

[stdout] importlib.metadata.PackageNotFoundError: No package metadata was found for replicate

[stdout] [phase:ai] AI engine init FAILED

[stdout] ERROR - [app.launcher] [launcher] ai_engine → ✗ FAILED — No package metadata was found for replicate

[stdout] ERROR - [app.launcher] [launcher] ai_engine → diagnostic snapshot: /Users/armanisadeghi/.matrx/diagnostics/2026-07-12T19-36-18_ai_engine.json

[stdout] INFO - [app/main.py] Phase 1b: Mounting host-owned /ai surface...

[stdout] INFO - [ai_routes] host-owned /ai surface built (heartbeat=5.0s)

[stdout] INFO - [app/main.py] Phase 1b: /ai surface mounted at /ai and /chat/ai ✓

[stdout] [phase:tools] Loading tool registry...

[stdout] INFO - [app/main.py] Phase 2: Loading tool registry...

[stdout] WARNING - [engine] matrx-ai not initialized — skipping tool registry load. Call initialize_matrx_ai() first.

[stdout] INFO - [app/main.py] Phase 2: Tool registry loaded ✓

[stdout] [phase:tools] Tool registry loaded

[stdout] WARNING - [app.launcher] [launcher] tools → ⚠ degraded — 0 tools registered — AI tool calls will fail

[stdout] INFO - [app/main.py] Phase 2b: Starting background data sync...

[stdout] INFO - [sync_engine] Background sync started (interval=600s)

[stdout] INFO - [app/main.py] Phase 2b: Background sync started ✓

[stdout] [phase:scraper] Starting scraper engine...

[stdout] INFO - [app/main.py] Phase 3: Starting scraper engine...

[stdout] INFO - [downloads] STATE | active=0 queued=0 completed=11 fails=3 cancelled=2 bandwidth_bps=0 peak_bps=0 active_slots=1 max_concurrent=3 | active=[] queued=[] fails=[{"id": "2f704646-7762-403b-9f28-78629fb645d2", "filename": "civitai--580857-2674760", "error_msg": "Civitai API key required or invalid \u2014 add your key under Settings \u2192 API Keys \u2192 Civitai, then retry the download."}, {"id": "18add2b4-b22a-4d15-a752-033bb1ebae0b", "filename": "black-forest-labs--FLUX.1-schnell", "error_msg": "Hugging Face download failed for black-forest-labs/FLUX.1-schnell: 401 Client Error. (Request ID: Root=1-6a507376-5dc496f13b17fc5a1ce87a95;0b69f50d-7a0a-4018-ae18-242d4203d089)\n\nCannot access gated repo for url https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/model_index.json.\nAccess to model black-forest-labs/FLUX.1-schnell is restricted. You must have access to it and be authenticated to access it. Please log in."}, {"id": "f98e0567-12e3-4c0c-b217-0543eb293b41", "filename": "Tongyi-MAI--Z-Image-Turbo", "error_msg": "huggingface_hub is not importable \u2014 the AI packages are not installed. Run the in-app installer (POST /image-gen/install) before downloading model weights."}]

[stdout] INFO - [sync_engine] Models synced: 53 cached (0 removed)

[stdout] INFO - [sync_engine] Agents synced: 494 agents in unified catalog

[stdout] INFO - [sync_engine] Tools synced: 108 cached

[stdout] INFO - [app/main.py] Phase 3: Scraper engine started ✓

[stdout] [phase:scraper] Scraper engine ready

[stdout] INFO - [app/main.py] Phase 4: HTTP proxy enabled=True

[stdout] [phase:proxy] Starting local HTTP proxy...

[stdout] INFO - [app/main.py] Phase 4: Starting proxy on 127.0.0.1:22180...

[stdout] INFO - [app/main.py] Phase 4: HTTP proxy started ✓ on port 22180

[stdout] [phase:proxy] HTTP proxy ready on port 22180

[stdout] INFO - [app/main.py] Phase 5: Tunnel enabled=True

[stdout] [phase:tunnel] Starting Cloudflare tunnel...

=== END ===
former paste end -->


---

## Unique errors

| ID | First seen | Level | Signature |
|----|------------|-------|-----------|
| E001 | 2026-07-09 12:14:58 | WARN | `[engine] matrx-ai: server tool registry returned 0 tools — falling back to the local manifest` |
| E002 | 2026-07-09 12:15:05 | WARN | `[app.services.documents.file_manager] Permission denied listing folders in ~/Documents/Matrx/Notes` (`[Errno 1] Operation not permitted`) |
| E003 | 2026-07-09 12:15:05 | WARN | `[app.services.documents.file_manager] Corrupt sync state, resetting` |
| E004 | 2026-07-09 12:15:05 | WARN | `[app.services.documents.file_manager] Permission denied listing conflicts in ~/Documents/Matrx/Notes/.sync/conflicts` |
| E005 | 2026-07-09 12:15:05 | ERR | ASGI `PermissionError` renaming sync temp → `~/Documents/Matrx/Notes/.sync/state.json` (`[Errno 1] Operation not permitted`) |
| E006 | 2026-07-09 12:15:15 | WARN | `Device Permissions: warning — Not granted: Screen Recording` (Mic/Camera granted) |
| E007 | 2026-07-09 12:15:16 | WARN | `[downloads] CANCELLED: file=stabilityai--sdxl-turbo` |
| E008 | 2026-07-09 12:18:04 | WARN | `[app.services.scraper.retry_queue] RetryQueue: remote queue unreachable` — scraper `GET /api/scraper/queue/pending` returned `500` |
| E009 | 2026-07-09 12:18:04 | WARN | `[launcher] scraper_retry_queue → degraded — remote retry queue unreachable` (same 500 as E008) |
| E010 | 2026-07-19 12:12:55 | ERR | `[image_gen_runtime_migration] FAILED: '_ClassNamespace' object is not iterable` + `[runtime_hook] Managed media runtime withheld (state 'failed')` — **root-caused & fixed 2026-07-19**: in-engine repair appended the runtime slot to sys.path AFTER `ner-packages` (torch 2.13), mixing capability torch with slot torchvision. Fix: `_insert_runtime_sys_path` certified precedence + origin-check-before-native-ops. See `app/services/image_gen/FEATURE.md`; deeper conflict filed as MXL-D-070. |
| E011 | 2026-07-19 12:12:52 | ERR | `[uvicorn] ASGI callable returned without sending handshake.` — a WS upgrade returned before accept/close during startup congestion. Single occurrence, no traceback; watch for recurrence post-E010 fix. |
| E012 | 2026-07-19 12:12:53 | WARN | React: `Select is changing from uncontrolled to controlled` — a Select's `value` starts undefined then becomes defined. Cosmetic; needs component hunt. |
| E013 | 2026-07-19 12:13:12 | ERR | `Engine did not respond within 30s` cluster (media-library, prompt-matrix, image-gen, video-gen) — symptom of E010's failed migration stalling the engine at startup; expected gone once E010 fix ships. |
| E014 | 2026-07-19 12:11:48 | WARN | `[downloads] Previous failure restored` (3 GGUF LLMs from 2026-03-29, size-mismatch validation) — by-design restore of durable failure records; the `action_needed` list (civitai_key_rejected / hf_gate_not_accepted / ai_packages_missing) is user-action state, not a bug. |
| E015 | 2026-07-19 12:11:57 | WARN | `[launcher] app_config / catalogs → degraded — running on cache until remote fetch completes` — by-design loud-degraded; both refreshed successfully seconds later in the same boot. Cosmetic. |
| E016 | 2026-07-19 12:13:06 | WARN | `[catalogs] llm_model overlay unavailable — AbortError` — frontend catalog fetch aborted while the engine was stalled (E013 symptom). |

### Notes from first export

- E002–E005 are the same macOS Documents / Full Disk Access failure surfacing as list, conflict, corrupt-state, and ASGI write errors. Kept as separate IDs because they are distinct log signatures.
- E008/E009 are the same remote 500; E009 is the launcher state transition that follows E008.
- Duplicate channels (`[tauri]` vs `[server]` vs `[syslog]`) for the same event were collapsed.

---

## Tasks

- [ ] **T009** ← E009 — Confirm `scraper_retry_queue` degraded state recovers when remote queue is healthy again (blocked on the aidream-side 500 fix, see T008)

---

## Resolved

<!-- Move checked tasks here with date resolved, e.g.:
- [x] **T000** ← E000 — … (resolved 2026-07-09)
-->

- [x] **T001** ← E001 — Server-side (aidream): `GET /api/ai-tools/app/matrx_local` 404 — filed in aidream `FOUND_DEFECTS.md` + already an ask in `.matrx/ARMAN_TASKS.md` ("Reconcile AIDream server ↔ matrx-ai tool registry"). Client already degrades honestly (v1.3.92). (routed 2026-07-12)
- [x] **T002** ← E002 — macOS Full Disk Access grant is an Arman action → added to `.matrx/ARMAN_TASKS.md` quick wins; degraded-state handling shipped in v1.3.93 (T004/T005). (routed 2026-07-12) **2026-07-13:** the UI now actually PROMPTS — Documents renders `NotesAccessPrompt` (FDA explanation, System Settings deep-link, Check again + auto-poll) off `GET /notes/access`; also fixed the `sync/status` raw 500 (unguarded `.exists()` in `list_conflicts`). Tracker W8 (notes) row.
- [x] **T006** ← E006 — Screen Recording grant already an ask in `.matrx/ARMAN_TASKS.md`; the Review & Grant path was fixed 2026-07-11 (MXL-D-032). (routed 2026-07-12)
- [x] **T008** ← E008 — Server-side (aidream scraper service): persistent 500 on `/api/scraper/queue/pending` — filed in aidream `FOUND_DEFECTS.md`. Client hardening shipped v1.3.92. Local follow-up is T009. (routed 2026-07-12)
- [x] **T003** ← E003 — Permission-denied is no longer misdiagnosed as corrupt sync state: `load_sync_state` only logs "Corrupt sync state, resetting" on `json.JSONDecodeError`; PermissionError returns defaults quietly. (resolved 2026-07-09, ships v1.3.93)
- [x] **T004** ← E004 — All notes permission-denied paths (folders/notes/conflicts/mappings listing) route through a once-per-run access guard: ONE warning + `registry.degraded("notes_sync", …)` with the Full Disk Access hint, sync cycles skipped while degraded, auto-recovers to ready. No more WARN spam. (resolved 2026-07-09, ships v1.3.93)
- [x] **T005** ← E005 — `state.json` writes now raise a structured `NotesAccessError` handled by a global exception handler → clean 503 with actionable reason (no more unhandled ASGI PermissionError); `GET /notes/sync/status` exposes `notes_access_degraded` + reason for the UI. (resolved 2026-07-09, ships v1.3.93)
- [x] **T007** ← E007 — Cancelled download `stabilityai--sdxl-turbo` was a deliberate cancel issued during live diagnosis by the coding agent (dev session shares the ~/.matrx downloads DB with the packaged app). Not a bug. (resolved 2026-07-09)
