# FOUND_DEFECTS.md — Matrx Local

> **Holding area — not an approved worklist.** Spot something while doing other
> work? File it here with evidence, then keep going. Do **not** treat open entries
> as assigned tasks.
>
> **Who acts on what**
> - **Working agent (opportunistic):** If you hit an open entry and can fix it
>   cleanly in scope, fix it and mark `fixed` (date + commit). That is fine while
>   it is still only here.
> - **Cleanup / promotion agent:** Does **not** silently implement open entries.
>   Proposes ≤3 promotions into `.matrx/AGENT_TASKS.md` for Arman’s approval.
> - **Any agent re-encountering an open entry:** Remind Arman — get approval to
>   fix **now**, or promote it to an agent task. Do not keep rediscovering in silence.
>
> Fixed/closed entries stay for history (never delete). Related:
> `.matrx/AGENT_TASKS.md` (Arman-approved work), `.matrx/ARMAN_TASKS.md` (ask Arman).

Format: one defect per entry — id, area, symptom, evidence (file:line), status
(`open` / `fixed` / `needs-hw-verification` / `blocked-external` / `wontfix`),
owner hint.

---

## Data integrity

### MXL-D-001 — Rust download manager discarded bytes (never wrote to disk)
- **Area:** desktop / downloads
- **Symptom:** `dm_enqueue` → `download_part` read chunks, counted them, and cleared
  the buffer without ever opening a file. Retries from the Downloads UI and resumed
  queue entries "completed" at 100% with no data on disk.
- **Evidence:** `desktop/src-tauri/src/downloads/manager.rs` (pre-fix `download_part`,
  the `buf.clear()` loop). Python half of the original 2026-05-04 claim was already
  fixed earlier: `app/services/downloads/manager.py:814` (open tmp), `:827`
  (`fh.write(chunk)` before counting), `:873` (`tmp.replace(dest)`).
- **Status:** fixed (2026-07-10, commit a158612a7) — per-chunk write to `.part` tmp,
  flush + fsync + atomic rename; internal path now REQUIRES `metadata.dest_dir` and
  fails loudly instead of discarding.
- **Owner hint:** downloads

### MXL-D-002 — Folder delete is destructive and untombstoned
- **Area:** engine / documents sync
- **Symptom:** `DELETE /documents/folders/{id}` hard-deletes the local folder and
  fire-and-forgets ONE cloud call; contained notes get no SQLite tombstones, so an
  offline delete → next `full_sync` resurrects every note from the cloud.
- **Evidence:** `app/api/document_routes.py::delete_folder`; contract gap #2 in
  `docs/SYNC_CONTRACT.md`; enforcement pattern in `tests/parity/test_sync_contract.py`.
- **Status:** open. Fix: soft-delete each contained note (`repo.soft_delete`) +
  per-note `soft_delete_note` cloud calls so tombstone propagation handles offline.
- **Owner hint:** documents/sync

### MXL-D-003 — Silent no-op PATCHes to `app_instances`
- **Area:** engine / cloud sync
- **Symptom:** `settings_sync.heartbeat` and `instance_manager.update_tunnel_url`
  PATCH with `user_id=eq.&instance_id=eq.` filters; PostgREST returns 2xx on ZERO
  matched rows, so an orphaned instance "heartbeats successfully" forever.
- **Evidence:** `app/services/cloud_sync/settings_sync.py`,
  `app/services/cloud_sync/instance_manager.py`.
- **Status:** open. Fix: `Prefer: return=representation` (or `count=exact`) and treat
  0 affected rows as an orphan signal.
- **Owner hint:** cloud_sync

### MXL-D-004 — `num_images_per_prompt` > 1 silently drops images 2..N
- **Area:** engine / media-gen
- **Symptom:** exposed in `/image-gen/params` advanced; service persists/returns only
  `images[0]`.
- **Evidence:** `app/services/image_gen/service.py` (single-image result handling).
- **Status:** open. Either persist all N to the media library or reject >1 loudly.
- **Owner hint:** media_gen

## Lifecycle / correctness

### MXL-D-005 — LlmServerState mutex held across 120s health wait
- **Area:** desktop / llm (Rust)
- **Symptom:** `start_llm_server` held the tokio mutex through spawn + up-to-120s
  health poll → `get_llm_server_status` / `check_llm_server_health` froze for the
  whole model load.
- **Evidence:** pre-fix `llm/commands.rs:90`, `llm/server.rs:39-189`.
- **Status:** fixed (2026-07-10, commit de04a3dbf) — `server::start_server` locks only
  around stop + install; one-start-at-a-time guard; mid-start stop kills by PID and
  aborts the install via the PID-handle ownership check.
- **Owner hint:** llm

### MXL-D-006 — vulkaninfo VRAM parse order inverted (AMD/Intel VRAM never detected)
- **Area:** desktop / hardware detection (Rust)
- **Symptom:** parser armed on `DEVICE_LOCAL` then looked for a following `size` line;
  real vulkaninfo prints `size =` BEFORE the flags line, so VRAM parsed as None and
  tier selection lowballed (24 GB GPU → tiny model, gpu_layers=1).
- **Evidence:** pre-fix `desktop/src-tauri/src/transcription/hardware.rs:227-260`.
- **Status:** fixed (2026-07-10, commit 269b8e90f) — order-independent per-heap parse,
  validated against real-format output. Runtime confirmation still wants a real
  AMD/Intel box (`cfg(not(macos))` — not compiled locally).
- **Owner hint:** transcription/hardware

### MXL-D-007 — Rust audio capture is f32-only
- **Area:** desktop / transcription (Rust)
- **Symptom:** `build_input_stream` fails on I16/U16-default devices (common on
  Linux/ALSA); needs `sample_format()` match + conversion.
- **Evidence:** `desktop/src-tauri/src/transcription/audio_capture.rs:69-102`.
- **Status:** open (deliberately deferred — needs hardware to verify the fix).
- **Owner hint:** transcription

### MXL-D-008 — Floating overlay mixes physical/logical px on scaled monitors
- **Area:** desktop / floating overlay (Rust)
- **Symptom:** wrong position/size on scaled secondary monitors. (The companion
  `sidecar_status` hardcoded-port defect was fixed 2026-07-10, commit 5ac3748e4.)
- **Evidence:** `desktop/src-tauri/src/floating_overlay.rs`.
- **Status:** needs-hw-verification (multi-monitor setup required).
- **Owner hint:** overlay

### MXL-D-009 — Circular import: `app.config` ↔ `app.common`
- **Area:** engine / imports
- **Symptom:** when `app.config` is the FIRST app module imported, its own import
  fails (`app/config.py:5` → `app.common.platform_ctx` → `app/common/__init__.py` →
  `fancy_prints` → `from app.config import LOG_VCPRINT` mid-init). App works only
  because normal entry points import `app.common` first.
- **Evidence:** `app/config.py:5`, `app/common/__init__.py`; workaround imports in
  `tests/characterization/test_documents_sync_characterization.py` and
  `test_local_db_sync_characterization.py` (remove them when fixing).
- **Status:** open. Fix: lazy `app/common/__init__.py` or move `LOG_VCPRINT` out.
- **Owner hint:** engine core

### MXL-D-010 — Heartbeat never bumps `app_instances.tunnel_updated_at`
- **Area:** engine / cloud sync
- **Symptom:** heartbeat re-asserts tunnel fields every 5 min but omits
  `tunnel_updated_at`, which goes stale while a tunnel is live (observed stuck at
  2026-05-10). Nothing gates on it today; it misleads humans reading the row.
- **Evidence:** `app/services/cloud_sync/settings_sync.py::heartbeat`.
- **Status:** open. Fix: include `tunnel_updated_at = now` whenever the tunnel payload
  is included.
- **Owner hint:** cloud_sync

### MXL-D-011 — `app_instances` cross-user duplicate rows
- **Area:** cloud DB / product decision
- **Symptom:** one `instance_id` exists under two user_ids; invisible to the client
  under RLS, cannot self-heal. Needs admin cleanup + Arman decision (a
  `unique(instance_id)` constraint would break second-account registration under
  current RLS — wants a SECURITY DEFINER claim RPC).
- **Evidence:** `.matrx/AGENT_TASKS.md` entry 2026-07-10 (row id
  `b7900a03-5fed-480a-8f02-29636f5a0606` is the stale copy).
- **Status:** open (blocked on Arman).
- **Owner hint:** cloud_sync / Arman

## Downloads (audit 2026-05-04 remainder — see docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md)

### MXL-D-012 — Download progress freezes / 0% on chunked-encoding sources
- **Area:** both download managers
- **Symptom:** `content_length().unwrap_or(0)` keeps percent at 0% for
  chunked-encoding responses; no heartbeat between 60s idle timeouts, so stalls look
  like hangs. (The Rust buffer-accumulation half was removed 2026-07-10 — writes are
  per-chunk now; progress bookkeeping is still throttled by design.)
- **Evidence:** `desktop/src-tauri/src/downloads/manager.rs::download_part_to`,
  `app/services/downloads/manager.py::_download_part`; audit doc Phase 2.
- **Status:** open.
- **Owner hint:** downloads

### MXL-D-013 — Five downloads bypass the unified manager; dual Rust/Python managers
- **Area:** downloads architecture
- **Symptom:** `transcription/downloader.rs`, `wake_word/models.py`,
  `setup_routes.py:_download_transcription_model`, `setup_routes.py:_download_cloudflared`,
  `app/tools/tools/transfer.py` all self-download, invisible to the global panel; the
  Rust and Python managers keep separate SQLite stores.
- **Evidence:** audit doc Phases 3–4.
- **Status:** open.
- **Owner hint:** downloads

### MXL-D-014 — Queue panel lacks pending/aggregate state; slots never expand in practice
- **Area:** desktop / downloads UI
- **Symptom:** no active-vs-pending split, aggregate bandwidth, stage badges, or stall
  detection; `should_expand_slots` never fires on source-rate-limited downloads.
- **Evidence:** `DownloadManagerModal.tsx`; `manager.rs::should_expand_slots`; audit
  doc Phases 5–6.
- **Status:** open.
- **Owner hint:** downloads

## UI / UX correctness

### MXL-D-015 — Orphaned tool panels with latent bugs
- **Area:** desktop / tools
- **Symptom:** 12 unreachable panels (Tools.tsx routes only Monitoring/Generic)
  carried swallowed errors, stale closures, and a render loop.
- **Evidence:** pre-fix `desktop/src/components/tools/panels/`.
- **Status:** fixed (2026-07-10, commit 256e8f7c1) — panels deleted (git history keeps
  them); live `KeyValueField` row-identity bug fixed for real in the same commit.
- **Owner hint:** desktop/tools

### MXL-D-016 — Chat: "Agent not found" silent failure after agent pick
- **Area:** desktop / chat
- **Symptom:** `ChatPanel` `.find()` miss (sync not complete) silently nulls
  `activeAgent`; next send proceeds agentless with no toast.
- **Evidence:** previously root AGENT_TASKS (deleted 2026-07-12); `desktop/src` ChatPanel.
- **Status:** open.
- **Owner hint:** chat

### MXL-D-017 — `transcription_auto_init` setting has no effect
- **Area:** desktop / transcription
- **Symptom:** toggle exists and syncs to cloud, but nothing reads it — Rust
  auto-initializes unconditionally.
- **Evidence:** `Configurations.tsx`, `use-transcription.ts`.
- **Status:** open.
- **Owner hint:** transcription

### MXL-D-018 — Health check false-positives on slow engine start
- **Area:** desktop ↔ engine
- **Symptom:** `isHealthy()` (api.ts) and Rust `check_engine_health` hit `/tools/list`
  with a 2s timeout; heavy first-load flips UI to "disconnected".
- **Evidence:** `desktop/src/lib/api.ts`, `desktop/src-tauri/src/lib.rs`.
- **Status:** open. Fix: lightweight `/health` or startup grace timeout.
- **Owner hint:** desktop core

### MXL-D-019 — use-chat-tts: sentence-gap between chunks
- **Area:** desktop / TTS
- **Symptom:** `speakText` resolves at synthesis end, not playback drain — small gap
  between sentences can remain. Gapless append-mode scheduling is the follow-up.
- **Evidence:** `desktop/src/hooks/use-chat-tts.ts`.
- **Status:** open.
- **Owner hint:** tts

## Logging / hygiene

### MXL-D-020 — extension_auth reject-log window dead + unbounded state
- **Area:** engine / extension bridge
- **Symptom:** `_REJECT_LOG_WINDOW_SECONDS` defined but never read; `_reject_log_state`
  grew without bound (one entry per rejected path).
- **Evidence:** pre-fix `app/api/extension_auth.py:147,152`.
- **Status:** fixed (2026-07-10, commit 637828ff7) — quiet-gap re-WARN wired, dict
  capped at 256 keys with longest-idle eviction.
- **Owner hint:** extension bridge

### MXL-D-021 — Dead stub modules in app/services
- **Area:** engine
- **Symptom:** zero-byte / unimported modules (`transcription/`, `audio/`, `files/`,
  `tts/player.py`) rotting since March.
- **Evidence:** pre-fix `app/services/{transcription,audio,files}`.
- **Status:** fixed (2026-07-10, commit 2847904bd) — deleted after repo-wide reference
  check (incl. PyInstaller specs).
- **Owner hint:** engine core

### MXL-D-022 — Stale "SQLite is the single source of truth" claims
- **Area:** engine / docs-in-code
- **Symptom:** contradicts the ratified replica doctrine (`docs/SYNC_CONTRACT.md`).
- **Evidence:** `app/api/chat_routes.py:21`, `app/services/ai/conversation_handler.py:9`
  (owned by the concurrent ai/** migration agent — do not touch until it lands),
  `app/config.py:266`.
- **Status:** open (partially blocked on concurrent agent).
- **Owner hint:** docs/sync

## Packaging / environment

### MXL-D-023 — `capabilities/setup` pip installs break in frozen builds
- **Area:** engine / packaging
- **Symptom:** `sys.executable -m pip` is the sidecar binary in compiled builds —
  install paths need a packaged-build strategy, not a code tweak.
- **Evidence:** `app/api/setup_routes.py`, capability probes.
- **Status:** partially fixed (2026-07-11) — Settings → Capabilities (Whisper)
  and light capability installs now use the shared frozen-safe
  `pip --target` installer (`app/services/optional_packages/`) with SSE
  progress, matching image-gen. Remaining: `setup_routes.py` playwright
  sync path and any other `sys.executable -m pip` call sites.
- **Owner hint:** packaging

### MXL-D-024 — Plain `uv sync` strips installed extras from the venv
- **Area:** dev environment / docs
- **Symptom:** `uv sync` removed torch/diffusers/whisper installed via extras; agents
  following Hard Rule 5 silently uninstall the media-gen stack. Use
  `uv sync --all-extras` (or `--inexact`) on dev machines.
- **Evidence:** observed 2026-07-09; CLAUDE.md dev commands say plain `uv sync`.
- **Status:** open (docs fix pending — coordinate with the concurrent pyproject agent).
- **Owner hint:** docs

### MXL-D-025 — matrx-ai circular import breaks GenericOpenAIChat
- **Area:** engine / upstream package
- **Symptom:** importing `GenericOpenAIChat` cold hit a circular import; local LLM
  routing through matrx-ai was disabled.
- **Evidence:** previously root AGENT_TASKS blocked section (deleted 2026-07-12);
  `docs/matrx-ai-generic-openai-port.md`.
- **Status:** fixed for matrx-local (2026-07-10, matrx-ai 0.3.0 migration, Phase 3):
  engine imports `matrx_ai.orchestrator` before any provider import (grep
  `import-order fix` in `app/services/ai/`), verified by
  `tests/smoke/test_ai_client_host.py::test_runtime_model_registration`. The
  UNDERLYING cycle still exists upstream in matrx-ai 0.3.0 for a cold
  `matrx_ai.providers` import — tracked in `.matrx/AGENT_TASKS.md` (lazy import in
  `orchestrator/executor.py`, then delete the three workaround imports here).
- **Owner hint:** ai / packages

## Cross-repo

### MXL-D-026 — Envelope `v` field not mirrored into matrx-frontend
- **Area:** cross-component envelope
- **Symptom:** `app/api/cross_component_envelope.py` and matrx-extend carry `v: 2`;
  canonical `matrx-frontend/lib/types/bridge-envelope.ts` lacks the optional field.
- **Evidence:** `.matrx/AGENT_TASKS.md` Phase 7 entry.
- **Status:** open (change lives in matrx-frontend).
- **Owner hint:** extension bridge / frontend

### MXL-D-027 — Voice E2E unconfirmed on physical hardware
- **Area:** desktop / voice
- **Symptom:** full setup → record → transcript with the current Rust pipeline never
  confirmed on a real device (download, load, mic permission, wake word, streaming,
  session persistence).
- **Evidence:** previously root AGENT_TASKS (deleted 2026-07-12).
- **Status:** needs-hw-verification.
- **Owner hint:** voice

### MXL-D-028 — Supabase instance SMTP broken: public email signup fails platform-wide
- **Area:** cross-repo / Supabase Auth (instance txzxabzwovsujtloxrus)
- **Symptom:** `POST /auth/v1/signup` (supabase-js `auth.signUp`) fails with
  "Error sending confirmation email" and the user creation rolls back (no row in
  `auth.users`). Because confirm-email is enabled, ANY self-serve email signup —
  and likely password-reset / magic-link emails — is dead on every Matrx surface.
- **Evidence:** discovered 2026-07-10 provisioning the matrx-local E2E test
  account (`desktop/e2e/setup/create-test-user.mjs`): two addresses, same 5xx,
  `auth.users` empty afterward. Workaround used: direct SQL insert into
  `auth.users`/`auth.identities` (confirmed email) + password rotation via
  `auth.updateUser`. See `docs/UI_TESTING.md` § Credential rotation.
- **Status:** open — fix SMTP provider in Supabase Auth settings (dashboard;
  not fixable from this repo). Attempted to file in the matrx-feedback tracker;
  submission was permission-blocked, so it is recorded here.
- **Owner hint:** platform / Supabase admin (Arman)

### MXL-D-029 — matrx-ai `configure()` loaded `db/_registry.py` by file path → AI stack dead in EVERY packaged build
- **Area:** aidream/packages/matrx-ai (upstream) + matrx-local engine boot
- **Symptom:** `FileNotFoundError: .../_MEIxxxx/matrx_ai/db/_registry.py` at
  `initialize_matrx_ai()`, killing Phase 1: "AI endpoints will not work",
  `[launcher] ai_engine → ✗ FAILED`, then `tools → ⚠ degraded — 0 tools registered`.
  The logged advice ("check SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY") was a red
  herring — env config was never involved.
- **Root cause:** `matrx_ai.configure()` used
  `importlib.util.spec_from_file_location(Path(matrx_ai.__file__).parent / "db" / "_registry.py")`.
  PyInstaller ships modules inside the PYZ archive — there is no `.py` on disk — so
  the load could never succeed in a frozen sidecar. Dev (`uv run`) runs from source
  and never saw it, which is why it shipped.
- **Evidence:** `matrx_ai/__init__.py:146-155` (0.3.3, pre-fix); user crash log 2026-07-11.
- **Status:** fixed. Upstream: plain `from matrx_ai.db._registry import configure_db`
  (`db/__init__.py` is lazy/PEP-562, so the file-path hack it existed to avoid is
  obsolete). matrx-local is immunized independently of the installed matrx-ai version
  by pre-importing the module in `app/services/ai/engine.py` (configure() skips its
  file-path load when the module is already in `sys.modules`), and the specs +
  `scripts/build-sidecar.sh` now `collect-submodules matrx_ai` so lazily-imported
  submodules are actually bundled.
- **Owner hint:** publish the fixed matrx-ai, then raise the floor in `pyproject.toml`

### MXL-D-030 — Vaulted media 404'd forever: `/media-library/file/{id}` didn't know the vault existed
- **Area:** engine / media-library + media-vault, desktop / media-gen
- **Symptom:** 41 red `404 Unknown media item` errors per session against items that
  were in the Private Vault the whole time; thumbnails re-requested every dead id on
  every mount, with no negative cache, forever.
- **Root cause:** moving an item into the vault deletes the plaintext file, but job
  history still holds the same item id, and `/media-library/file/{id}` only ever read
  the plaintext library — so it answered "Unknown media item" about an item it held.
- **Evidence:** `app/api/media_library_routes.py:74` (pre-fix), `desktop/src/hooks/use-media-gen.ts`
  thumbnail effect; verified: all four ids from the user's log return `has_item() == True`.
- **Status:** fixed. `/media-library/file/{id}` is now THE read path for any media id:
  plaintext → 200, vaulted+unlocked → 200 (decrypted in memory), vaulted+locked → 423,
  unknown → 404. Frontend keeps a negative cache (`locked` retries after unlock via
  `VAULT_UNLOCKED_EVENT`; `gone` never retries). Pinned by `tests/smoke/test_media_vault.py`.
- **Owner hint:** media-gen

### MXL-D-031 — Download failures dead-ended the user: gated model, missing key, uninstalled packages all became a truncated red 401
- **Area:** engine / downloads, desktop / downloads UI
- **Symptom:** FLUX.1-schnell 401 "Cannot access gated repo"; Civitai 401; and
  "huggingface_hub is not importable — run the in-app installer (POST /image-gen/install)"
  (an HTTP verb, in end-user copy) — all rendered as one `truncate`d 48-unit red string
  with a Retry button that reproduced the same failure.
- **Root causes (four, all fixed):**
  1. FLUX.1-schnell was missing `requires_hf_token=True` (it is the ONLY `gated: auto`
     repo in the catalog — audited against the Hub), so no pre-check ever fired.
  2. `main.py` started the download manager BEFORE `load_user_keys_into_env()`, so a
     resumed HF download could read a token that wasn't injected yet and go out
     unauthenticated.
  3. `read_hf_token()` had no key_manager-cache fallback (Civitai already had one), so
     HF depended entirely on the deprecated `os.environ` injection shim.
  4. No failure taxonomy: the engine could not distinguish "no token" from "dead token"
     from "license not accepted" — all three are a bare 401.
- **Status:** fixed. New `app/services/downloads/failures.py` — a failure the user can
  fix carries a `DownloadResolution` (title, plain-English message, one action), the
  engine classifies HF 401/403 via a `whoami` probe, and the UI renders
  `DownloadActionDialog` — an explanation plus a working button. Genuine errors (500,
  network) pass through untouched and keep Retry. Also fixed the adjacent parity failure:
  `civitai` was in the engine's `VALID_PROVIDERS` but missing from `api-key-patterns.ts`,
  so the bulk `.env` import ignored the very key the error told users to set.
- **Owner hint:** downloads

### MXL-D-032 — Screen Recording reported "denied" for a permission the user was never offered
- **Area:** engine / permissions, desktop / Setup Wizard + Permissions modal
- **Symptom:** Setup Wizard: "Not granted: Screen Recording … Screen Recording=denied",
  while the Permissions modal said "Not Requested" for the same permission on the same
  screen. "Review & Grant" opened System Settings — where the engine was not listed, so
  there was nothing to switch on. A true dead end.
- **Root cause:** `CGPreflightScreenCaptureAccess()` returns false for BOTH "never asked"
  and "denied"; `checker.py` collapsed both into `DENIED`. Nothing ever called
  `CGRequestScreenCaptureAccess()`, which is the ONLY thing that registers a process in
  System Settings → Screen Recording. Screen capture runs in the Python engine
  (`screencapture`, `app/tools/tools/system.py`), so the engine is the process that needs
  the grant — the frontend was reporting the Tauri window's status, a different principal.
- **Evidence:** `checker.py:1151` (pre-fix), `setup_routes.py:395`, `use-permissions.ts:339`.
- **Status:** fixed. Status is now honest (`not_determined` until we have actually asked;
  `denied` only after an ask came back false, tracked by `~/.matrx/screen_recording_requested`),
  `POST /devices/permissions/request/screen-recording` performs the real request, the UI
  offers a real "Grant Access" button, and the frontend reads screen-recording status from
  the engine so the wizard and the modal can no longer contradict each other.
- **Owner hint:** permissions

---

## Untriaged legacy backlog (from deleted root `AGENT_TASKS.md`, 2026-07-12)

Not Arman-approved. Not full defect entries yet. Bugs that already had `MXL-D-*`
IDs above were **not** duplicated here. Weekly cleanup should promote, reject, or
expand these into proper entries / agent tasks.

### Gaps / features (were “Important Missing Features”)
- Multimodal image input UI for local LLM — backend ready; missing attach/paste UI, base64 content arrays, mmproj download in DownloadManager.
- Gemma 4 E2B/E4B native audio via llama.cpp — blocked on experimental libmtmd upstream; video input also WIP upstream.
- Chat: “Local” tab routing to llama-server — `Chat.tsx` has no local mode; users must use the LLM page.
- Image gen: FLUX.1 Dev token gate — **likely obsolete** (FLUX.1-dev removed from catalog 2026-07-09); confirm and drop or reframe.
- Voice: no partial/streaming transcription results — Whisper waits for full chunks.
- Voice: English-only hardcoded — expose language picker wired to settings.
- Voice: transcription sessions not synced to cloud — `localStorage` only.
- QA: Launch at login + tray minimize E2E on signed macOS/Windows builds.
- QA: `headless_scraping` setting reaches Playwright `headless` in live scrape paths.

### Tech debt
- Split `Voice.tsx` (~2,800 lines) into `desktop/src/components/voice/*.tsx`.
- Zustand (or equivalent) for shared app state — `App.tsx` prop-drills; defer until active bugs clear.
- API key extras — rotation timestamps; optional OS keychain.
- Configurations consistency gaps — see `docs/official/settings-catalog.md` §6 (theme live apply, `chatMaxConversations`, dual audio-device storage, wake keyword cloud vs Rust).
- Dark mode full-app visual pass (modals, dropdowns, third-party wrappers).

### Wishlist / future
- ComfyUI sidecar evaluation; cloud AI relay; cloud-assigned scrape job queue.
- Welcome cards → agent IDs + Settings favorites (product design first).
- Proxy full E2E with `MAIN_SERVER` URL (see `.matrx/ARMAN_TASKS.md`).
- Wake word: sherpa-onnx KWS when stable Rust bindings exist.
- Wake-on-LAN / smart home APIs; reverse tunnel; real app icon before public launch.

### Upstream / blocked (was root “Blocked”)
- matrx-ai still assumes server-side paths in places — shipping confidence needs client-only/local paths verified. Much of this moved under matrx-ai 0.3.0 + `.matrx/AGENT_TASKS.md` upstream work; keep only what remains after triage.
