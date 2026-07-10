# Agent Tasks

Active worklist managed by agents. **See `AGENT_INSTRUCTIONS.md` for rules** — especially around task format, condensation, and when to ask the user.

> Quick scan order for arriving agents:
> 1. **Needs Clarification** below (questions waiting on the user)
> 2. **Blocked** (waiting on external)
> 3. **Active** (`ready` and `in-progress`)
> 4. **Completed** (recent context, condensed)

---

## Needs Clarification

### TASK-001: Local GLiNER NER subsystem (URGENT)
- **Status:** needs-clarification
- **Created:** 2026-07-09
- **Priority:** URGENT — cloud NER API volume is a major cost driver
- **Source:** User: NER is critical; API costs are killing us at volume; need
  local GLiNER including small through largest; must run through Arman first

**Goal**
Ship a first-class local NER stack in the Python sidecar so high-volume entity
extraction stops hitting paid cloud APIs. Support small always-on models and
optional large/XXL downloads on powerful machines.

**Why**
NER is a core product path. Cloud API cost scales with volume. GLiNER is the
right local runtime (encoder NER) — not llama-server / GGUF.

**Plan doc**
[`docs/GLINER_NER_INTEGRATION_PLAN.md`](../docs/GLINER_NER_INTEGRATION_PLAN.md)

**Subtasks** (do not start coding until Arman approves)
- [ ] Arman reviews plan + answers decision checklist in the doc
- [ ] P1: NER service + `/ner/extract[/batch]` + small/medium download + `local_extract_entities`
- [ ] P2: queue/batching, DownloadManager `"ner"`, registry, capability probe
- [ ] P3: GLiNER2 + PII presets + large/XXL gating + optional desktop UI
- [ ] P4 (optional): ONNX/ORT fast path for default small model

**Notes**
**BLOCKED ON ARMAN — do not implement until reviewed.** Exact questions in the
plan doc "Decisions needed from Arman" section. Key constraint already decided
by research: Python sidecar subsystem, NOT LLM catalog / llama-server.

Also filed for Arman in `.arman/ARMAN_TASKS.md`.

---

## Blocked

_(none)_

## Active

- [ ] **Heartbeat never bumps `app_instances.tunnel_updated_at` (discovered
  2026-07-10, Phase 7 remote-control audit)** —
  `app/services/cloud_sync/settings_sync.py::heartbeat` re-asserts
  `tunnel_url` / `tunnel_ws_url` / `tunnel_active` every 5 min (correct) but
  omits `tunnel_updated_at`, so that column only moves on explicit
  start/stop (`instance_manager.update_tunnel_url`) and goes stale while a
  tunnel is live (observed: stuck at 2026-05-10 on this Mac). Nothing gates
  on it today (frontend resolve + aidream `_is_online` use `last_seen` +
  `tunnel_active`), but it misleads humans reading the row. Fix: include
  `tunnel_updated_at = now` in the heartbeat payload whenever the
  tunnel_payload dict is included. NOT fixed in Phase 7 because
  `settings_sync*` was owned by a concurrent agent.

- [ ] **Silent no-op PATCHes to `app_instances` (discovered 2026-07-10)** —
  Both `settings_sync.heartbeat` and `instance_manager.update_tunnel_url`
  PATCH with `user_id=eq.&instance_id=eq.` filters; PostgREST returns 2xx
  even when ZERO rows match (row deleted / RLS-hidden), so an orphaned
  instance "heartbeats successfully" forever. The orphan detector only
  fires via `list_instances`. Fix: request `Prefer: return=representation`
  (or `count=exact`) and treat 0 affected rows as an orphan signal.

- [ ] **`app_instances` cross-user duplicate rows — DB cleanup + product
  decision (discovered 2026-07-10)** — `instance_id`
  `inst_940d435f8faf313e7261230f7f204db0` (linux, DESKTOP-9HE9A6L) exists
  under TWO user_ids (`4cf62e4e-…` and `34ed4fc3-…`). This is NOT a
  registration bug: the code upserts on the real unique constraint
  `(user_id, instance_id)` (`settings_sync.register_instance`,
  `on_conflict=user_id,instance_id`), and cross-user rows are invisible to
  the client under RLS (`auth.uid() = user_id`) — the desktop app cannot
  detect or delete the other user's row. Cleanup (admin, one-off):
  `delete from app_instances where id = 'b7900a03-5fed-480a-8f02-29636f5a0606';`
  (the stale 34ed4fc3 copy, last_seen 2026-03-03). Decision for Arman: if
  one-machine-one-row is desired, a `unique(instance_id)` constraint would
  BREAK second-account registration under current RLS (upsert can't adopt a
  row it can't see) — needs a SECURITY DEFINER claim RPC instead.

- [ ] **Mirror the envelope `v` field into matrx-frontend (discovered
  2026-07-10, Phase 7)** — `app/api/cross_component_envelope.py` and
  matrx-extend `src/lib/messaging/cross-component-envelope.ts` now carry a
  `v: 2` version field (optional, defaults to 2). The canonical schema
  `matrx-frontend/lib/types/bridge-envelope.ts` needs the same optional
  field to stay byte-parity (v1/v2 publishers parse unchanged).

- [ ] **Folder delete is destructive and untombstoned (discovered 2026-07-10,
  Phase 6 sync-contract audit)** — `DELETE /documents/folders/{folder_id}`
  (`app/api/document_routes.py::delete_folder`) hard-deletes the local folder
  directory (all note files) and fire-and-forgets ONE cloud
  `delete_folder(folder_id)` call. The contained notes get NO SQLite
  tombstones (`notes.is_deleted` stays 0 / rows keep `file_path`), so if the
  cloud call fails (offline), the next `full_sync` sees "local file gone, no
  tombstone" and RESURRECTS every note in the folder from the cloud. Fix
  (route file was outside Phase 6 scope): on folder delete, soft-delete each
  contained note's SQLite row (repo.soft_delete) and fire-and-forget
  per-note `soft_delete_note` calls, so the existing full_sync
  tombstone-propagation branch handles the offline case. Contract reference:
  docs/SYNC_CONTRACT.md gap #2; enforcement pattern in
  tests/parity/test_sync_contract.py.

- [ ] **Stale "SQLite is the single source of truth" claims outside the sync
  subsystems (discovered 2026-07-10)** — Contradict the ratified replica
  doctrine (docs/SYNC_CONTRACT.md): `app/api/chat_routes.py:21`,
  `app/services/ai/conversation_handler.py:9`, and the notes comment at
  `app/config.py:266` ("local is source of truth, Supabase is sync target").
  Reword to replica terms (local = working copy / first-access replica;
  cloud = durable truth; conversations currently local-only per contract gap
  #1). The parity test `test_no_sqlite_source_of_truth_claims_in_sync_modules`
  guards the three sync directories only; these three files were outside
  Phase 6 scope.

- [ ] **Circular import: app.config ↔ app.common (discovered 2026-07-10)** —
  When `app.config` is the FIRST app module a process imports, its own import
  fails: `app/config.py:5` imports `app.common.platform_ctx`, which triggers
  `app/common/__init__.py` → `fancy_prints` → `from app.config import
  LOG_VCPRINT` while `app.config` is partially initialized → ImportError.
  Repro: fresh interpreter, `from app.services.documents.file_manager import
  content_hash` (file_manager's first app import is app.config). The app
  currently works only because normal entry points happen to import
  `app.common` first. Fix: make `app/common/__init__.py` lazy or move
  `LOG_VCPRINT` out of app.config (fancy_prints could read the env itself).
  Characterization tests work around it with an explicit `import app.common`
  first (tests/characterization/test_documents_sync_characterization.py and
  test_local_db_sync_characterization.py) — remove those workaround imports
  when fixing.

- [x] **Media-gen: Generate while queue busy corrupted everything → generation
  gate + queue-first UX + queue-row lightbox + prompt caps (2026-07-09)** —
  Root cause: `POST /image-gen/generate` called `ImageGenService.generate`
  directly while the job runner was mid-generation → two concurrent `pipe()`
  calls on the same (non-reentrant) diffusers pipeline, and a differing
  `model_id` triggered `load_model → _unload_sync_locked()` which unloaded the
  pipeline out from under the running job. Fixes: (1) engine `asyncio.Lock`
  generation gate in `ImageGenService.generate` (one generation ever; cancel
  registration scoped INSIDE the gate so a cancel only hits the owner, never a
  waiter); (2) `POST /image-gen/jobs` accepts `priority:"next"` (front of
  pending queue, persisted, `ImageJobResponse.priority`); (3) frontend
  queue-first: Generate while anything is queued/running/in-flight enqueues as
  next + violet `QueueNotice` + "Up next" badge (all 6 image surfaces; video
  already busy-safe — Cancel-state button + "One video at a time" note);
  (4) completed queue rows/chips/cards open in MediaLightbox on click
  (shared `useImageJobLightbox` in `shared.tsx`, fetch-then-open, prev/next
  across the panel's completed jobs, stopPropagation on X/seed controls);
  (5) prompt textareas `resize-y` everywhere + honest per-family
  `PromptCapacityHint`; API prompt caps raised 1000→10000 (image), 2000→10000
  (video), workflow subject 500→2000 — no truncation anywhere on our side.
  Tests: `tests/smoke/test_media_gen_queue.py` (gate overlap, cancel-frees-
  waiter, A/C/B priority ordering, persistence, HTTP priority + long-prompt
  round-trip). Follow-ups (minor): the workflow queue-first path materializes
  the preset template client-side (duplicate of the server's `{subject}`
  substitution — consider a jobs-side `preset_id` field later); a one-shot
  WAITING on the gate (raw API users only; UI can't reach it) has no cancel
  handle until it starts.

- [x] **Media-gen: download one model → all downloaded cards spin (2026-07-09)** —
  Shared `anyLoading`/`busy` (`imageModelLoading || status.is_loading`) drove
  the Load spinner on every downloaded model card. Fixed: track
  `loadingImageModelId` / `loadingVideoModelId` in `use-media-gen`; cards spin
  only when `loading*ModelId === m.model_id`, siblings only disable. Also
  tightened `findModelDownload` to exact sanitized filename (no substring).
  Files: `use-media-gen.ts`, `shared.tsx`, `ImageGenSection`/`VideoGenSection`,
  variants Workspace/Studio/Focus/Gallery.

- [x] **Media-gen backend: params exposure + media library + image job queue
  (2026-07-09)** — (1) `extra_params` passthrough on image/video generate
  (merged LAST, `prompt` protected, unknown kwargs fail loudly naming the
  parameter) + `GET /image-gen/params/{model_id}` and
  `GET /video-gen/params/{model_id}` (complete effective defaults, common +
  advanced). (2) Persistent media library `~/.matrx/media/generated/{images,videos}/`
  (`<uuid>.png|.mp4` + JSON sidecar with full resolved pipeline kwargs) +
  `/media-library/items|file/{id}` API; image generate auto-saves, video jobs
  move the mp4 into the library on completion. (3) Image job QUEUE
  (`/image-gen/jobs` POST/GET/GET-id/DELETE) — FIFO, one at a time, per-step
  progress, history in `~/.matrx/generated/images/jobs.json`. (4) Seeds are
  always concrete: a null seed becomes a server-generated one, reported in
  responses/jobs/sidecars for reproducibility. Files:
  `app/services/media_gen/{params,library,paths}.py`,
  `app/services/image_gen/{service,jobs}.py`,
  `app/services/video_gen/{service,jobs}.py`,
  `app/api/{image_gen_routes,video_gen_routes,media_library_routes}.py`,
  `app/main.py`, `tests/smoke/test_media_gen_params_library.py`.

- [x] **Private Media Vault backend (2026-07-09)** — Encrypted, password-locked
  store for library media at `~/.matrx/media/vault/` (vault.json + opaque
  `blobs/<id>.bin` / `<id>.meta.bin`). AES-256-GCM per-file (96-bit nonces,
  AAD-bound to item id), VMK wrapped in a scrypt user slot AND a mandatory
  RSA-4096-OAEP escrow slot (public key embedded in
  `app/services/media_vault/escrow.py`; creation fails loudly if escrow wrap
  fails). Move/restore are verify-before-delete (full decrypt round-trip +
  SHA-256 before the source is removed). 15-min lazy auto-lock; locked access
  = HTTP 423 everywhere; decrypted bytes served from memory only. Recovery
  backdoor: `scripts/vault-recover.py` (escrow private key → decrypt-all
  and/or rewrap a fresh user slot). `cryptography` now an explicit core dep
  (was transitive-only). Files: `app/services/media_vault/{crypto,escrow,
  service}.py`, `app/api/media_vault_routes.py`, `app/main.py`,
  `scripts/vault-recover.py`, `tests/smoke/test_media_vault.py` (7 passing).

- [ ] **Media Vault follow-ups** — (1) Desktop frontend for the vault is being
  built in parallel against the /media-vault contract documented in
  `app/api/media_vault_routes.py` — verify against the shipped UI. (2) Extend
  the vault beyond media-library items (envelope already carries
  content_type/file_name/sha256 for arbitrary files; needs an ingest route for
  non-library files). (3) Consider surfacing auto-lock remaining time and a
  configurable timeout. Added 2026-07-09.

- [ ] **`uv sync` (no flags) strips installed extras from the venv** — running
  plain `uv sync` removed torch/diffusers/whisper/matrx-scheduler that were
  installed via extras; restored with `uv sync --all-extras`. Docs/CLAUDE.md
  say `uv sync` — agents doing Hard-Rule-5 dep additions will silently
  uninstall the media-gen stack. Should document `uv sync --all-extras` (or
  `--inexact`) as the required form on dev machines. Discovered 2026-07-09.

- [ ] **No mid-flight cancel for image OR video generation jobs** — DELETE
  /image-gen/jobs/{id} cancels queued jobs and removes finished records but
  returns 409 for a running job; video has no cancel at all. Aborting an
  in-flight diffusers pipe() needs a cooperative interrupt via
  callback_on_step_end (raise → mark cancelled) — same mechanism would work
  for both. Discovered 2026-07-09 while building the image job queue.

- [ ] **`num_images_per_prompt` > 1 generates N images but only images[0] is
  returned/persisted** — exposed in /image-gen/params advanced for
  completeness; the service should either persist all N to the media library
  or reject >1 loudly. Discovered 2026-07-09.

- [x] **Custom LLM context hard-capped at 4096 (2026-07-09)** — Models-tab
  Play for custom GGUFs passed hardcoded `4096`; Settings `llmDefaultContextLength`
  was never wired into `startServer`; Server/Inference dropdowns topped out at
  32k; auto-start always used 8192. Fixed: shared `resolveContextLength`
  (per-model override → explicit → catalog → Settings → 8192), per-model
  override UI on custom model expand, dropdowns through 262k, persist
  `last_context_length` in `llm.json` for auto-start. Files:
  `desktop/src/lib/llm/contextLength.ts`, `use-llm.ts`, `LocalModels.tsx`,
  `Configurations.tsx`, `llm/config.rs`, `llm/commands.rs`, `lib.rs`.

- [x] **Local LLM catalog expanded (2026-07-09)** — added Qwen3.5-2B/4B/9B
  (vision+mmproj), Devstral Small 2 24B, Qwen3-Coder-30B-A3B, GLM-4.7-Flash,
  Qwen3-Next-80B-A3B, Qwen3-Coder-Next. Auto-recommend now prefers Qwen3.5
  defaults. Also wired mmproj auto-download + auto-attach on server start
  (fixes Gemma 4 / Qwen3.5 vision). Files: `model_selector.rs`, `commands.rs`,
  `types.ts`.
- [x] **Gemma complete + compact GLM/distills (2026-07-09)** — added Gemma 4
  12B Unified (+ mmproj), Gemma 3n E2B; GLM-Z1-9B; upgraded Low2 → R1-0528
  Qwen3-8B and Mid2 → Phi-4-reasoning-plus; added Phi-4-mini-reasoning.
  Full NER plan: `docs/GLINER_NER_INTEGRATION_PLAN.md` + TASK-001 (Arman gate).

- [x] **pytest engine fixture kills live dev engines (fixed 2026-07-10)** —
  `tests/conftest.py` spawned a real engine whose `preflight.clean_orphans()`
  swept ALL engine-pattern processes, SIGKILLing live dev engines (confirmed
  twice 2026-07-09); it also clobbered the real `~/.matrx/local.json`, and
  its 40s boot wait exceeded the 30s pytest timeout. Fixed: run.py honors
  `MATRX_SKIP_ORPHAN_SCAN=1` (loud skip of clean_orphans, set by the
  fixture); fixture isolates `MATRX_HOME_DIR` to a session tmp dir (model
  dirs symlinked read-through); teardown is PID-scoped to the spawned tree
  only (never patterns/ports); boot wait 60s with
  `timeout_func_only = true` in tests/pytest.ini so fixture setup is
  excluded from the 30s per-test timer. Verified: full smoke+parity green
  with a live packaged engine on 22140 untouched.

### Bug-hunt wave 1 — deferred items (2026-06-11 full-system audit)
A comprehensive audit fixed ~150 verified bugs (see commits 9ca5652..ab1f3c8).
These were found, verified, and deliberately deferred:

- [x] **Rust: LlmServerState mutex held across 120s health wait** — FIXED
  2026-07-10 (commit de04a3dbf, Phase 8): new `server::start_server` locks
  only around stop + install; static one-start-at-a-time guard; mid-start
  stop kills by PID and trips the phase-3 ownership check. KNOWN_DEFECTS
  MXL-D-005.
- [ ] **Rust: audio capture is f32-only** — `build_input_stream` fails on
  I16/U16-default devices (common on Linux/ALSA); match `sample_format()`
  and convert. (`transcription/audio_capture.rs:69-102`) STAYS DEFERRED
  (needs hardware). KNOWN_DEFECTS MXL-D-007.
- [x] **Rust: vulkaninfo VRAM parse order inverted** — FIXED 2026-07-10
  (commit 269b8e90f, Phase 8): verified `size =` really does precede
  DEVICE_LOCAL in real heap blocks; parser now order-independent per heap,
  validated with a standalone harness. Runtime confirmation on a real
  AMD/Intel box still wanted (cfg'd out on macOS). KNOWN_DEFECTS MXL-D-006.
- [ ] **Rust: floating overlay mixes physical/logical px** on scaled
  secondary monitors (needs-hw-verification, KNOWN_DEFECTS MXL-D-008).
  The `sidecar_status` hardcoded-port half was FIXED 2026-07-10 (commit
  5ac3748e4) — now reads the discovery file.
- [x] **Unmounted tool panels carry latent bugs** — RESOLVED 2026-07-10
  (commit 256e8f7c1, Phase 8): the 12 unreachable panels were DELETED
  (Tools.tsx routes only Monitoring/Generic; import graph verified zero
  importers; git history preserves them for any future re-wire).
  KeyValueField (live via ToolForm) got a real fix: stable row ids replace
  index keys, editing happens on an id-keyed row array. KNOWN_DEFECTS
  MXL-D-015.
- [x] **Engine: dead stub files** — DELETED 2026-07-10 (commit 2847904bd,
  Phase 8): app/services/{transcription,audio,files}/ and tts/player.py
  removed after repo-wide reference check incl. PyInstaller specs.
  KNOWN_DEFECTS MXL-D-021.
- [ ] **use-chat-tts**: chunks are serialized but `speakText` resolves at
  synthesis end, not playback drain — a small gap between sentences can
  remain; gapless append-mode scheduling is the follow-up. (MXL-D-019)
- [x] **extension_auth reject-log window** — FIXED 2026-07-10 (commit
  637828ff7, Phase 8): quiet-gap re-WARN wired to the previously dead
  constant; state dict capped at 256 keys, longest-idle eviction.
  KNOWN_DEFECTS MXL-D-020.
- [ ] **capabilities/setup pip installs break in frozen builds**
  (`sys.executable -m pip` is the sidecar binary) — needs a packaged-build
  strategy, not a code tweak. (MXL-D-023)

- [x] **Download-chunk-discard P0 re-verified & closed (2026-07-10, Phase 8)**
  — Root AGENT_TASKS.md P0 claimed BOTH managers discard bytes. Current
  code: Python was already fixed (manager.py:814 tmp open, :827 write
  before count, :873 tmp.replace(dest)); Rust internal path
  (dm_enqueue → download_part) was STILL discarding — fixed in commit
  a158612a7 (per-chunk write → .part tmp → fsync → atomic rename;
  metadata.dest_dir now required, loud failure otherwise). KNOWN_DEFECTS
  MXL-D-001; remaining audit phases tracked as MXL-D-012/013/014.


_(none)_

---

## Completed

- [FEAT] Tool registry unification, matrx-local side (Phase 4) — 2026-07-10.
  **One code-side truth:** new `app/tools/catalog.py` (108 entries) auto-built from dispatcher `TOOL_HANDLERS` + `tool_schemas` introspection; each entry carries canonical cloud name (`local_*` — the `tool.definition` name; 62 legacy names pinned verbatim, 49 of them live in Supabase bound to executor `matrx-local`; 46 generated `local_<snake_case>`, zero collisions verified against `tool.definition`), dispatcher name, input_schema (arg_model > introspection), category, tags, platform gating, timeout. `LOCAL_TOOL_MANIFEST` DELETED (no shims); consumers repointed: `local_tool_bridge` (registers all 108 under cloud names), engine Phase-A½ backfill, `GET /chat/local-tools`, local-DB `sync_tools` (62→108 rows).
  **Orphans wired:** all 18 historical orphan `tool_` functions now dispatched (mail/messages/contacts/calendar/photos/location/speech — macOS runtime-gated + declared `platforms=("darwin",)` — plus BrowserClose, which is NOT a duplicate of BrowserTabs-close). Dispatcher 90→108, orphans 0.
  **tool_sync rewritten:** cloud-canon drift reporter (`status`/`diff`, LOUD + non-blocking, modeled on matrx-extend `check-tool-db-drift.ts`; exit 1 on drift as signal, 0 when unreachable) + `emit-changeset` writing reviewable SQL (`tool.definition`+`tool.binding` inserts, executor `matrx-local`) — the desktop NEVER writes the tool tables directly. Read path: `GET /ai-tools/app/matrx-local/all` (deployed server still 404s it → embedded 49-name fallback baseline, names-only mode).
  **Bug fixed in passing:** PEP-563 string annotations made `tool_schemas` introspection type every param `"string"` and made dispatcher `_coerce_tool_input` a silent no-op — both now use `inspect.signature(..., eval_str=True)`.
  **Operator deliverable:** `.matrx/tool_registry_changeset.sql` — 59 NEW definition+binding rows (0 CHANGED / 0 collisions / 0 removed) to apply via Supabase MCP after review. Tests: exact-set parity (`tests/parity/test_tool_count.py` replaced floor-79), characterization snapshot regenerated (dispatcher=catalog=source=108).

- [FEAT] Media-gen mid-flight cancellation + failure hardening (backend) — 2026-07-09.
  **Cancellation:** new shared `app/services/media_gen/cancellation.py` (`GenerationCancelled`, `install_cancel_hook`, `release_generation_memory`, `friendly_generation_error`). Every generation gets a per-run cancel event checked in the diffusers per-step callback (all 10 catalog pipeline classes support `callback_on_step_end` on diffusers ≥0.37; legacy `callback` fallback kept for generic pipelines; "none" → cancel lands at pipeline completion, reported via `mid_flight=false`). New `POST /image-gen/cancel`; `DELETE /image-gen/jobs/{id}` and new `DELETE /video-gen/jobs/{id}` now cancel RUNNING jobs mid-flight (`outcome:"cancelling"`, `cancel_requested` flips immediately, status → `cancelled` with partial progress preserved). `GenerateResponse.cancelled`, status endpoints gained `is_generating`/`cancel_requested`/`load_error`. Image cancel also aborts a generation stuck in the auto-load phase (registered before load).
  **Failure capture:** OOM → friendly message (name+text detection, no torch import); `load_error` surfaced in both statuses with `is_loading` guaranteed cleared in `finally`; image job worker task gets a done-callback so a dead drain loop screams instead of dying silently; unload paths clear pipeline refs BEFORE cache cleanup (a failed torch import can no longer leave a phantom "loaded" model); VRAM released (`gc` + cuda/mps `empty_cache`) after every cancel/failure. `PROTECTED_PARAMS` now blocks `callback*` kwargs (cancel hook is engine-owned). Video `mark_running` returns False for a job cancelled before start (never resurrected).
  Tests: `tests/smoke/test_media_gen_cancel.py` (17 tests, stub pipeline, no real model) + existing media/image/imports smoke suites all green. NOTE for UI: a cancel during model LOAD cannot abort the load itself — the weights finish loading, then the generation returns cancelled immediately; video cancels can take up to one denoising step (tens of seconds on MPS) to land — poll `cancel_requested` for "Cancelling…".

- [BUG] Lifecycle + silent-failure hardening (adversarial-investigation wave) — 2026-07-09.
  **Playwright leak (L1):** leaked `chrome-headless-shell` + Playwright driver trees now reclaimed by `app/preflight.py` (`playwright_driver`/`playwright_browser` ManagedService entries, `orphan_only` + scoped to our `PLAYWRIGHT_BROWSERS_PATH` via `env_markers`/`safety_keyword`, `kill_tree`) AND by the in-session force-exit path (`run.py::_kill_child_subprocesses` → `scraper/engine.py::terminate_playwright_tree(driver_pid)`, driver PID captured at `browser_pool.start()`).
  **Sibling-engine kill (L2):** `engine` service now `protect_live_owner=True` — a live, healthy engine (discovery-file owner or scan-range `/health` responder) and its ancestor chain are never killed; packaged app + dev `uv run run.py` coexist. Documented in the preflight docstring.
  **Port-fallback (L3):** `assign_engine_port()` probes the incumbent on 22140 and loudly names its PID (live Matrx → "second instance"; foreign → "foreign process").
  **TTS registry (S1):** load/download now transition `registry.starting/ready/degraded/failed` ("tts") in `service.ensure_loaded` + auto-download path.
  **Registry truthfulness (S2):** `main.py` Phase 2 marks `tools` DEGRADED when 0 tools registered (`load_tools_and_register()` now returns the count); `hardware_routes.run_initial_detection` imports the real `fire_and_forget` (fixed `NameError`) and marks `hardware` DEGRADED on detection failure.
  **TTS first-use UX (S3):** `synthesize`/`synthesize_stream` fast-fail with 409 `{detail, code:"model_not_downloaded", needs_download, downloading}` instead of a silent 330 MB blocking download; background auto-download honored via `tts_auto_download_model`; `/tts/status` gains `needs_download`/`auto_download`.
  **TTS threshold (S4):** `SynthesizeRequest.streaming_threshold` (optional int) plumbed to `synthesize_stream`; server default from `tts_streaming_threshold` setting, fallback 280.
  **Scraper retry-queue (S5):** `app/services/scraper/retry_queue.py` now uses exponential backoff (30s→cap 10min) + `registry.degraded("scraper_retry_queue", …)` on sustained remote failure, cleared on recovery; per-cycle remote errors demoted to DEBUG (killed the ~every-30s log flood). NOTE: used a dedicated `scraper_retry_queue` key rather than `"scraper"` so a remote-endpoint outage doesn't falsely mark local scraping degraded.
  **Log levels (S6/S7):** `notify.py` broadcast + plyer failures and `tts` download-progress-emit failure raised from DEBUG → WARNING.
  Verified: `import app.main`, `pytest tests/smoke -x -q` (118 passed/1 skipped), TTS 409 TestClient probes, read-only preflight decision-logic check (live 22140 engine + ancestors protected; live playwright driver/browsers excluded; aidream `run.py` excluded).
- [FEAT] Media-generation overhaul (backend) — 2026-07-09. Image gen: catalog refreshed (FLUX.2-klein-4B default, Z-Image-Turbo, Qwen-Image; FLUX.1-dev + HunyuanDiT removed); weights now download through the universal DownloadManager (per-file `hf_hub_download` + dir-size polling → real progress on `/downloads/stream`) into `~/.matrx/image-models/<id>/` with a `.download-complete` marker; new `POST /image-gen/download`; `/load` is local-only (`local_files_only=True`, 409 `needs_download` when absent); `/status` gained `packages_version`/`packages_outdated`/`device`; installer pins bumped (torch>=2.6, diffusers>=0.39, transformers>=4.51, accelerate>=1.0) with an in-place upgrade path (`needs_upgrade()` — `POST /install` re-runs pip when diffusers < 0.39). Video gen (new): `app/services/video_gen/` + `/video-gen/*` (status/models/download/load/unload/generate→202 job_id, jobs list/detail, mp4 result) — Wan 2.2 TI2V-5B default, Wan 2.1 1.3B, LTX-Video; one job at a time; diffusers step-callback progress; mp4 via imageio-ffmpeg; history in `~/.matrx/generated/videos/jobs.json`; hard hardware gate (Apple Silicon ≥16GB unified or CUDA ≥8GB, never CPU) in shared `app/services/media_gen/hardware.py`. `DownloadCategory` gained `"video_gen"`. Legacy AGENT_TASKS items (download progress / VRAM gating / `~/.matrx/image-models` / video engine) closed.
- [BUG] /extension/* auth log noise + misleading "JWT validation DISABLED" warning. The deployed binary fired (a) "JWT validation DISABLED — neither SUPABASE_JWT_SECRET nor SUPABASE_URL is configured" *while* SUPABASE_URL was actually configured (incoming tokens were HS256 but engine had no secret, so the bypass-to-degraded path was triggered with the wrong message), and (b) per-request "JWKS validation failed: kid …" + "missing Bearer token" WARNINGs every 2s during /extension/rpc polling, completely drowning the engine log. Split `_log_degraded_mode_once` into reason-specific variants (`no_paths` vs `hs256_no_secret`), added `_debug_log_jwks_failure` that suppresses repeats by `(kid, error_type)`, and added `_log_rejection` that logs the first miss as WARNING then demotes to DEBUG with a periodic 60s rate-summary at INFO. `app/api/extension_auth.py`. 2026-05-08
- [BUG] /devices/permissions endpoint took 21s on every page hit (cold ``check_all_permissions`` runs 15 OS probes including macOS ``system_profiler SP{Audio,Camera,Bluetooth,AirPort}DataType``, which serialise on the private cfprefsd IPC). Frontend abort timeout was 15s so the call never completed → `console.error("Failed to load permissions:", err)` → `Failed to load permissions: {}` in the captured log (Error props are non-enumerable, JSON.stringify drops them). Added a 30s TTL in-process cache with single-flight refresh in `app/api/permissions_routes.py`, exposed `force_refresh=true` query param, invalidated the cache from the Windows grant route, bumped the frontend timeout to 35s, surfaced `forceRefresh` on `engine.getDevicePermissions()`, and routed permissions/ports error logs through `logWarn` so they capture `.message` and `.stack` instead of `{}`. 2026-05-08
- [BUG] Frontend race on page mount: `engineStatus` flips to `"connected"` the moment REST is reachable, but `connectWebSocket()` runs slightly later in `use-engine.ts` (it waits for the Supabase session token). Pages that called `engine.invokeToolWs(...)` inside an `engineStatus === "connected"` `useEffect` would throw `WebSocket not connected` on first render. Added `engine.isWsConnected()` + `engine.waitForWs(timeoutMs)` and made `invokeToolWs` wait up to 3s for the upgrade before failing; `Ports.tsx` skips the tick if WS still isn't ready. `desktop/src/lib/api.ts`, `desktop/src/pages/Ports.tsx`. 2026-05-08
- [BUG] llama-server crash on launch (macOS bundled app): `dyld: Library not loaded: @rpath/libllama-common.0.dylib`. `download-llama-server.sh` was downloading the dylibs into `desktop/src-tauri/binaries/` and `install_name_tool` was rewriting the rpath to `@executable_path/../Resources/binaries`, but `tauri.macos.conf.json` had no `bundle.resources` entry to copy them into the bundle — so `Contents/Resources/binaries/` did not exist on installed apps and every dylib lookup failed. Mirrored the existing Windows pattern by adding `"resources": { "binaries/*.dylib": "binaries/" }` to `tauri.macos.conf.json`. Next signed/notarized build will ship the dylibs alongside `llama-server` and local LLM inference will work again. 2026-05-08
- [ENH] Gemma 4 unblocked: bumped llama.cpp b8519→b9076, refreshed all 4 catalog entries with HF-verified byte sizes (E2B/E4B/26B-A4B/31B). 2026-05-08
- [ENH] Boot self-check: every engine startup verifies /extension/* routes registered + JWT posture + tunnel/metrics/discovery, logs summary, GET/POST /extension/boot-check endpoints + Bridge Test panel. 2026-05-07 (3f911668)
- [ENH] /extension/tunnel/status: runtime introspection of Cloudflare tunnel state with preferred-mode logic (MATRX_PREFER_TUNNEL); Bridge Test sub-panel surfaces it with re-pair hint when engine prefers tunnel. 2026-05-07 (82fdce6a)
- [ENH] /extension/* observability: in-memory metrics (count, errors, last-N latencies) per command via app/api/extension_metrics.py + GET /extension/metrics + Metrics panel in Bridge Test. 2026-05-07 (648a17e1)
- [ENH] /extension/* JWT validation: real Supabase JWKS (preferred) + HS256 (fallback) signature + expiry check via new app/api/extension_auth.py. Loopback fallback preserved when SUPABASE_JWT_SECRET and SUPABASE_URL both unset (loud one-time WARNING). 2026-05-07 (61a26f7b)
