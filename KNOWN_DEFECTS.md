# KNOWN_DEFECTS.md — Matrx Local

Platform-standard defect ledger. **Find-it-own-it:** every defect you spot gets an
entry here the moment you spot it — no silent handoffs. When you fix one, flip its
status to `fixed` with the commit; never delete entries (history is the point).

Format: one defect per entry — id, area, symptom, evidence (file:line), status
(`open` / `fixed` / `needs-hw-verification` / `blocked-external`), owner hint.

Related trackers: `.matrx/AGENT_TASKS.md` (active worklist), `.arman/ARMAN_TASKS.md`
(manual tasks), root `AGENT_TASKS.md` (read-only history).

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
- **Evidence:** root `AGENT_TASKS.md` (active bugs); `desktop/src` ChatPanel.
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
- **Status:** open (needs design).
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
- **Evidence:** root `AGENT_TASKS.md` (blocked section); `docs/matrx-ai-generic-openai-port.md`.
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
- **Evidence:** root `AGENT_TASKS.md` (active bugs).
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
