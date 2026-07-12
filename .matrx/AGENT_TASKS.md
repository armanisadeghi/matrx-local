# Agent Tasks — Matrx Local

> **The ONLY Arman-approved worklist.** If you are doing related work and see a
> matching open task here, TAKE IT and do it. Do not add tasks without Arman —
> unapproved discoveries go in `FOUND_DEFECTS.md` (holding area).
>
> - Tasks carry: created date, priority (P0–P3 where set), and an analysis stamp
>   (`Analyzed <date> — verified in code` or `code analysis pending`).
> - Finished a task? Condense it to ONE line under Completed (what + date +
>   defect ID/commit). Full detail lives in git history.
> - Blocked on something only Arman can do? File it in `.matrx/ARMAN_TASKS.md`
>   and ask him in chat.
> - Hygiene passes (dedupe, promotion, compression) run via the `task-hygiene`
>   skill. Rules: `AGENT_INSTRUCTIONS.md`. Inbox: `TASKS_FROM_USER.md`.
>
> Scan order: **Needs Clarification** → **Blocked** → **Active** → **Completed**.
>
> Root `AGENT_TASKS.md` was deleted 2026-07-12. Unique leftover backlog →
> `FOUND_DEFECTS.md` § "Untriaged legacy backlog".

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

Also filed for Arman in `.matrx/ARMAN_TASKS.md`.

---

## Blocked

_(none)_

## Active

- [ ] **img2img + LoRA follow-ups (added 2026-07-10, image-gen img2img/LoRA
  build)** — shipped: `init_image_b64`/`strength`/`loras` on
  /image-gen/generate + /image-gen/jobs, `supports_img2img`/`img2img_strength`/
  `lora_family` in the model catalog, /image-gen/loras endpoints (list +
  DownloadManager-routed download + delete), curated 5-entry LoRA catalog
  (all verified against live HF metadata 2026-07-10), smoke suite
  `tests/smoke/test_media_gen_img2img_lora.py` (22 tests). Remaining:
  - [ ] Live GPU verification per family: run a real img2img generation on
    each downloaded model (sdxl-turbo, FLUX.2-klein, Z-Image, FLUX.1-schnell,
    Qwen-Image). Smoke tests cover routing/contracts with stub pipelines
    only; `AutoPipelineForImage2Image.from_pipe` task-class resolution was
    verified statically against diffusers 0.39.0/0.37.1 but not executed
    with real weights.
  - [ ] Live LoRA verification: download `latent-consistency/lcm-lora-sdxl`
    and generate on sdxl-turbo; confirm adapter effect + clean unload. Note:
    catalog FLUX LoRAs are trained on FLUX.1-dev — compatibility with our
    FLUX.1-schnell is family-plausible but unproven.
  - [ ] Desktop UI (out of scope for the Python-only build): img2img
    drop-zone + strength slider + LoRA picker in the Image tab, wired to the
    new request fields and /image-gen/loras.
  - [ ] DELETE /image-gen/loras/{id} while its download is still active is
    not blocked (the manager will fail the entry loudly, but a 409 with a
    clear message would be nicer).
  - [ ] flux2-klein img2img is unified reference-image editing (no strength
    knob) — surface an "edit mode" label in the UI so users don't expect
    SD-style partial-denoise behavior.

- [ ] **Custom image models (HF + Civitai) follow-ups (added 2026-07-11,
  custom-model registry build)** — shipped: custom-model registry
  (`~/.matrx/image-models/custom-models.json`, atomic writes) merged into
  GET /image-gen/models (`custom`/`source`/`format` fields); POST
  /image-gen/custom-models/inspect (HF repo/URL + Civitai model/version
  URL/id, network-free resolution of family/format/size, warnings +
  registerable verdict); POST /image-gen/custom-models (server-side
  validation — unknown family + unsupported single_file combos refused at
  registration — + DownloadManager enqueue); DELETE
  /image-gen/custom-models/{id}; Civitai direct-download path in
  DownloadManager (Bearer auth from stored key, non-retryable 401/403 with
  "Civitai API key required — Settings → API Keys" message,
  `.download-complete` marker, `dest_filename` override); `civitai` provider
  in the API-key store (PUT/DELETE /settings/api-keys/civitai, masked list,
  `read_civitai_key()`); single-file loading via
  `<FamilyPipeline>.from_single_file` (SD/SDXL/FLUX/Z-Image — verified
  FromSingleFileMixin on diffusers 0.37.1; Qwen + Flux2Klein have none and
  are refused at registration); POST /image-gen/loras/download now accepts
  HF URLs and `{civitai: ...}` (type must be LORA — checkpoints are
  redirected to Add Model and vice versa), LoRA store entries carry
  `source`. Smoke suite `tests/smoke/test_media_gen_custom_models.py`
  (18 tests, network-free). Remaining:
  - [ ] Live end-to-end verification: register a real small Civitai SDXL
    checkpoint + a Civitai LoRA with a real API key, download, load,
    generate (from_single_file dtype behavior on MPS is family-plausible
    but unproven for community checkpoints; the constant-image guard will
    scream if a dtype bug ships black images).
  - [ ] Flux single-file checkpoints: from_single_file fetches text
    encoders/VAE from the gated FLUX.1 HF repos at FIRST LOAD (surfaced as
    an inspect warning + requires_hf_token, and load passes
    token=read_hf_token()) — but that first load is a multi-GB fetch
    outside the DownloadManager (no progress UI). Consider pre-fetching
    components through the DownloadManager at registration instead.
  - [ ] Desktop UI: "Add custom model" flow (ref input → inspect card with
    warnings → confirm), Civitai key field in Settings → API Keys, custom
    badge + delete button on model cards, LoRA-from-Civitai input.
  - [ ] Civitai baseModel map only covers common bases (SD1.x, SDXL/Pony/
    Illustrious/NoobAI, Flux.1 D/S/Krea, Flux.2 Klein, Qwen, Z-Image);
    unmapped bases refuse honestly. Extend as diffusers gains loaders
    (SD3.x is mappable once we ship an SD3 pipeline family).
  - [ ] Hardware estimates for custom models are size-heuristic
    (single_file: size+1.5 GB VRAM; diffusers: 0.65x+1.5 GB; floors 6/10;
    RAM=VRAM+4 — documented in custom_models.py). Consider refining with
    actual tensor-dtype inspection from safetensors headers (HTTP range
    request on the first 8 bytes + header JSON, still download-free).

- [ ] **Heartbeat never bumps `app_instances.tunnel_updated_at` (discovered
  2026-07-10, Phase 7 remote-control audit)** — Analyzed 2026-07-10 — verified
  in code. `app/services/cloud_sync/settings_sync.py::heartbeat` re-asserts
  `tunnel_url` / `tunnel_ws_url` / `tunnel_active` every 5 min (correct) but
  omits `tunnel_updated_at`, so that column only moves on explicit
  start/stop (`instance_manager.update_tunnel_url`) and goes stale while a
  tunnel is live (observed: stuck at 2026-05-10 on this Mac). Nothing gates
  on it today (frontend resolve + aidream `_is_online` use `last_seen` +
  `tunnel_active`), but it misleads humans reading the row. Fix: include
  `tunnel_updated_at = now` in the heartbeat payload whenever the
  tunnel_payload dict is included. NOT fixed in Phase 7 because
  `settings_sync*` was owned by a concurrent agent. (= MXL-D-010)

- [ ] **Silent no-op PATCHes to `app_instances` (discovered 2026-07-10)** —
  Analyzed 2026-07-10 — verified in code. Both `settings_sync.heartbeat` and
  `instance_manager.update_tunnel_url`
  PATCH with `user_id=eq.&instance_id=eq.` filters; PostgREST returns 2xx
  even when ZERO rows match (row deleted / RLS-hidden), so an orphaned
  instance "heartbeats successfully" forever. The orphan detector only
  fires via `list_instances`. Fix: request `Prefer: return=representation`
  (or `count=exact`) and treat 0 affected rows as an orphan signal.
  (= MXL-D-003)

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
  row it can't see) — needs a SECURITY DEFINER claim RPC instead. (= MXL-D-011)

- [ ] **Mirror the envelope `v` field into matrx-frontend (discovered
  2026-07-10, Phase 7)** — `app/api/cross_component_envelope.py` and
  matrx-extend `src/lib/messaging/cross-component-envelope.ts` now carry a
  `v: 2` version field (optional, defaults to 2). The canonical schema
  `matrx-frontend/lib/types/bridge-envelope.ts` needs the same optional
  field to stay byte-parity (v1/v2 publishers parse unchanged). Change lives
  in matrx-frontend — file/execute there. (= MXL-D-026)

- [ ] **Folder delete is destructive and untombstoned (discovered 2026-07-10,
  Phase 6 sync-contract audit)** — Analyzed 2026-07-10 — verified in code.
  `DELETE /documents/folders/{folder_id}`
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
  tests/parity/test_sync_contract.py. (= MXL-D-002)

- [ ] **Stale "SQLite is the single source of truth" claims outside the sync
  subsystems (discovered 2026-07-10)** — Contradict the ratified replica
  doctrine (docs/SYNC_CONTRACT.md): `app/api/chat_routes.py:21`,
  `app/services/ai/conversation_handler.py:9`, and the notes comment at
  `app/config.py:266` ("local is source of truth, Supabase is sync target").
  Reword to replica terms (local = working copy / first-access replica;
  cloud = durable truth; conversations currently local-only per contract gap
  #1). The parity test `test_no_sqlite_source_of_truth_claims_in_sync_modules`
  guards the three sync directories only; these three files were outside
  Phase 6 scope. (= MXL-D-022)

- [ ] **Circular import: app.config ↔ app.common (discovered 2026-07-10)** —
  Analyzed 2026-07-10 — verified in code. When `app.config` is the FIRST app
  module a process imports, its own import
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
  when fixing. (= MXL-D-009)

- [ ] **Media Vault follow-ups** — (1) Desktop frontend for the vault is being
  built in parallel against the /media-vault contract documented in
  `app/api/media_vault_routes.py` — verify against the shipped UI. (2) Extend
  the vault beyond media-library items (envelope already carries
  content_type/file_name/sha256 for arbitrary files; needs an ingest route for
  non-library files). (3) Consider surfacing auto-lock remaining time and a
  configurable timeout. Added 2026-07-09.

- [ ] **`num_images_per_prompt` > 1 generates N images but only images[0] is
  returned/persisted** — exposed in /image-gen/params advanced for
  completeness; the service should either persist all N to the media library
  or reject >1 loudly. Discovered 2026-07-09. (= MXL-D-004)

### Bug-hunt wave 1 — still-deferred items (2026-06-11 full-system audit)
The audit fixed ~150 verified bugs (commits 9ca5652..ab1f3c8); fixed deferrals
are condensed under Completed. Still open:

- [ ] **Rust: audio capture is f32-only** — `build_input_stream` fails on
  I16/U16-default devices (common on Linux/ALSA); match `sample_format()`
  and convert. (`transcription/audio_capture.rs:69-102`) STAYS DEFERRED
  (needs hardware). (= MXL-D-007)
- [ ] **Rust: floating overlay mixes physical/logical px** on scaled
  secondary monitors (needs-hw-verification). (= MXL-D-008)
  The `sidecar_status` hardcoded-port half was FIXED 2026-07-10 (commit
  5ac3748e4) — now reads the discovery file.
- [ ] **use-chat-tts**: chunks are serialized but `speakText` resolves at
  synthesis end, not playback drain — a small gap between sentences can
  remain; gapless append-mode scheduling is the follow-up. (MXL-D-019)
- [~] **capabilities/setup pip installs break in frozen builds**
  (`sys.executable -m pip` is the sidecar binary) — partially fixed 2026-07-11:
  Settings → Capabilities (Whisper) now uses frozen-safe `--target` + SSE
  (`app/services/optional_packages/`, `app/services/capabilities/installer.py`).
  Remaining: `setup_routes.py` and other raw `sys.executable -m pip` sites.
  (MXL-D-023)

### Active (added 2026-07-10, matrx-ai 0.3.0 migration — Phase 3)

- [ ] **/ai-surface follow-up: agent authored system prompt is not available
  locally** — the local agent cache (SQLite `agents`, from aidream `GET
  /api/agents`) carries settings/variables but NOT the agent's authored
  messages; aidream exposes NO non-admin REST endpoint returning them
  (`/agent-service/agents/{id}` is admin-only and still omits messages). A
  NEW conversation started on the local runtime therefore runs with the
  agent's model+tools only (loud WARNING log + a stream `warning` event
  `agent_prompt_unavailable_locally`); continue-mode turns are unaffected
  (persisted conversation state is the source of truth). Fix belongs in
  aidream: a user-scoped full-definition endpoint (or sync the messages
  column into `GET /api/agents`); then extend sync_engine + `_load_local_agent`.
- [ ] **/ai-surface follow-up: no local /ai/cancel/{request_id}** — aidream's
  cancel router isn't mirrored; the FE cancels via fetch-abort, and
  matrx-connect's detach-on-disconnect keeps the local turn running to
  completion (burning local compute). Wire emitter.cancel via a small
  request registry when needed.
- [ ] **Upstream (matrx-ai 0.3.0): client-host writes reach the
  WriteCoordinator + Turn-Boundary Inbox reads cxm** — the client-host
  contract says the coordinator is "always None in a client host", but
  `persistence/queue_helpers.get_coordinator()` only short-circuits when no
  RequestLane exists — and matrx-connect's `create_streaming_response`
  ALWAYS opens a lane, so `_ensure_cx_registered()` → cxm →
  `DBNotConfiguredError` mid-request. Same class:
  `tools/dynamic_drain.drain_pending_injections` reads cx_pending_injection
  with no store-first dispatch (crashed every tool-loop turn boundary).
  Worked around host-side in
  `engine.install_client_host_coordinator_guard()` (wraps get_coordinator +
  the two drain fns to no-op when a conversation_store is configured).
  Upstream fix: store-first checks at both choke points; then delete the
  guard.
- [ ] **Upstream (aidream/packages/matrx-ai): providers ↔ orchestrator circular
  import still present in 0.3.0** — a COLD `import matrx_ai.providers...`
  dies (`providers/__init__` → `unified_client` → `orchestrator/__init__` →
  `executor` → `from matrx_ai.providers import UnifiedAIClient` on the
  partially-initialized package). matrx-local works around it by importing
  `matrx_ai.orchestrator` first (engine.py, local_llm_registry.py, the
  client-host smoke test — grep `import-order fix`). Fix belongs in matrx-ai
  (lazy import in orchestrator/executor.py); then delete the three
  workaround imports here.
- [ ] **Upstream (matrx-ai 0.3.0): pricing/cost lookup ignores the host
  ModelCatalog** — `config/usage_config.py::warm_pricing_lookup` goes through
  `ai_catalog_manager.ensure_loaded()` → ORM `get_model("AiEndpoint")` →
  `DBNotConfiguredError` in a client host, so token costs are never
  calculated locally (loud ERROR per request in the log). The host catalog
  already serves `pricing` on the model dict — matrx-ai should consult it.
  Cosmetic-but-noisy; surfaced by `tests/smoke/test_ai_client_host.py` runs.
- [ ] **Publish matrx-* 0.3.0-era packages to PyPI (OPERATOR)** — matrx-ai
  0.3.0, matrx-connect 0.1.1, matrx-graph 0.1.0, matrx-runtime 0.0.1,
  matrx-utils 1.1.0, matrx-orm 3.1.0 exist only on aidream main. Until
  tag-pushed, `uv lock`/`uv sync`/CI cannot resolve matrx-local's pins; dev
  venv runs hand-installed wheels (`uv build` in aidream + `uv pip install`)
  and uv.lock is intentionally stale (still pins matrx-ai 0.1.26). After
  publishing: `uv lock && uv sync --extra all` and drop the `--no-sync`
  workaround note in tests/conftest.py if desired.
- [ ] **Image-gen packages dir shadows venv deps (root-caused 2026-07-10)** —
  `~/.matrx/image-gen-packages` is PREPENDED to sys.path
  (`inject_image_gen_path`), and its protobuf 7.34.1 shadowed the venv's
  protobuf 6 → xai-sdk import crash → AI engine init FAILED at boot.
  Mitigated by an early `import google.protobuf` guard in
  app/services/ai/engine.py + `[tool.uv] constraint-dependencies =
  ["protobuf<7"]`. Real fix: the frozen build's runtime_hook injects before
  any app import — audit which shared deps the image-gen dir can shadow and
  either append (not prepend) or vendor-isolate them.

## Completed

_(one line each, newest first; full detail in git history)_

- [x] Docs now mandate `uv sync --all-extras` (CLAUDE.md dev commands + Hard Rule 5, README quick start) — fixes MXL-D-024 (2026-07-12)
- [x] Screen Recording permission honesty: real `CGRequestScreenCaptureAccess` flow, `not_determined` vs `denied`, engine-side status — fixes MXL-D-032 (2026-07-11)
- [x] Download-failure taxonomy (`downloads/failures.py` + `DownloadActionDialog`): gated repo / missing-dead token / license distinguished; civitai added to `api-key-patterns.ts` — fixes MXL-D-031 (2026-07-11)
- [x] `/media-library/file/{id}` is now vault-aware (plaintext/unlocked/locked-423/404) + FE negative cache — fixes MXL-D-030 (2026-07-11)
- [x] matrx-ai `configure()` file-path load of `db/_registry.py` killed the AI stack in every packaged build — upstream import fix + engine pre-import immunization + `collect-submodules matrx_ai` in specs — fixes MXL-D-029 (2026-07-11; publish floor bump tracked in ARMAN_TASKS)
- [x] Frozen-safe Whisper/capability installs (`pip --target` + SSE via `app/services/optional_packages/`) — partial fix for MXL-D-023 (2026-07-11)
- [x] Media-gen queue-busy corruption → engine generation gate + queue-first UX + `priority:"next"` + queue-row lightbox + prompt caps to 10k — `tests/smoke/test_media_gen_queue.py` (2026-07-09)
- [x] Media-gen mid-flight cancellation + failure hardening: shared cancel hook, POST /image-gen/cancel, DELETE jobs cancels RUNNING image+video jobs, OOM-friendly errors, VRAM release — `tests/smoke/test_media_gen_cancel.py` (2026-07-09; closes the "no mid-flight cancel" task)
- [x] Media-gen: per-model Load spinner (`loadingImageModelId`/`loadingVideoModelId`) — one model no longer spins all cards (2026-07-09)
- [x] Media-gen backend: `extra_params` passthrough + `/params/{model_id}`, persistent media library `~/.matrx/media/generated/` + `/media-library/*`, image job queue `/image-gen/jobs`, concrete seeds (2026-07-09)
- [x] Private Media Vault backend: AES-256-GCM per-file, scrypt user slot + RSA-4096 escrow, verify-before-delete moves, 423 when locked, `scripts/vault-recover.py` — `tests/smoke/test_media_vault.py` (2026-07-09)
- [x] Custom LLM context length resolution (`resolveContextLength`: per-model → explicit → catalog → Settings → 8192; dropdowns to 262k; persisted for auto-start) (2026-07-09)
- [x] Local LLM catalog expansion (Qwen3.5, Devstral, GLM, Phi-4-reasoning, Gemma 4 12B/3n) + mmproj auto-download/attach (2026-07-09)
- [x] pytest engine fixture no longer kills live dev engines: `MATRX_SKIP_ORPHAN_SCAN`, isolated `MATRX_HOME_DIR`, PID-scoped teardown (2026-07-10)
- [x] LlmServerState mutex no longer held across 120s health wait — fixes MXL-D-005 (2026-07-10, de04a3dbf)
- [x] vulkaninfo VRAM parse now order-independent per heap — fixes MXL-D-006 (2026-07-10, 269b8e90f; runtime confirm on a real AMD/Intel box still wanted)
- [x] 12 orphaned tool panels deleted; KeyValueField stable row ids — fixes MXL-D-015 (2026-07-10, 256e8f7c1)
- [x] Dead stub modules `app/services/{transcription,audio,files}`, `tts/player.py` deleted — fixes MXL-D-021 (2026-07-10, 2847904bd)
- [x] extension_auth reject-log quiet-gap re-WARN + state capped at 256 keys — fixes MXL-D-020 (2026-07-10, 637828ff7)
- [x] Rust download manager wrote nothing to disk (dm_enqueue path) — per-chunk `.part` write + fsync + atomic rename, `dest_dir` required — fixes MXL-D-001 (2026-07-10, a158612a7; remainder of the download audit = MXL-D-012/013/014)
- [x] /ai-surface: `/chat/ai` rebuilt host-side (`app/api/ai_routes.py`, byte-compatible with aidream `/ai`; error envelope; mounted at `/ai` + `/chat/ai`) — `tests/smoke/test_ai_surface.py` (2026-07-10, Phase 5; contract gaps tracked in Active)

- [FEAT] matrx-local adopts matrx-ai 0.3.0 client-host seams (Phase 3) — 2026-07-10.
  `matrx_ai.initialize(client_mode=True)` + `configure(...)` seams in engine.py; `SQLiteConversationStore`, `SqliteModelCatalog`, `SqliteKeyResolver`; sync_engine handles reshaped `GET /api/ai-models`. 310 tests passed incl. new `tests/smoke/test_ai_client_host.py`; sanity boot: 108 tools, 58 catalog models.

- [FEAT] Tool registry unification, matrx-local side (Phase 4) — 2026-07-10.
  New `app/tools/catalog.py` (108 entries, auto-built from dispatcher; `LOCAL_TOOL_MANIFEST` deleted); 18 orphan tools now dispatched; tool_sync → drift reporter + `emit-changeset` (`.matrx/tool_registry_changeset.sql`, 59 rows for operator). In passing: fixed PEP-563 `eval_str` bug in tool_schemas introspection.

- [BUG] Lifecycle + silent-failure hardening (adversarial-investigation wave) — 2026-07-09.
  Playwright orphan reclaim; live sibling engines protected; TTS 409 fast-fail; tools/hardware registries truthful; scraper retry-queue backoff + degraded key. Smoke 118 passed.

- [FEAT] Media-generation overhaul (backend) — 2026-07-09. Image catalog refresh (FLUX.2-klein default; FLUX.1-dev/HunyuanDiT removed), weights via universal DownloadManager, NEW video gen `app/services/video_gen/` + `/video-gen/*` (Wan 2.2/2.1, LTX-Video), hardware gate in `media_gen/hardware.py`.

- [BUG] /extension/* auth log noise: reason-specific degraded-mode messages, JWKS failure dedupe, first-miss WARN then DEBUG + 60s rate summary (`app/api/extension_auth.py`) — 2026-05-08
- [BUG] /devices/permissions took 21s per hit (15 OS probes serialized on cfprefsd): 30s TTL cache + single-flight + `force_refresh`, FE timeout 35s, error logs capture message/stack — 2026-05-08
- [BUG] FE race: `invokeToolWs` before WS upgrade — added `isWsConnected()`/`waitForWs()`, invoke waits up to 3s (`api.ts`, `Ports.tsx`) — 2026-05-08
- [BUG] llama-server dyld crash in bundled macOS app: added `bundle.resources` for dylibs to `tauri.macos.conf.json` (mirrors Windows pattern) — 2026-05-08
- [ENH] Gemma 4 unblocked: llama.cpp b8519→b9076, catalog entries refreshed with HF-verified sizes — 2026-05-08
- [ENH] Boot self-check: startup verifies /extension/* routes + JWT posture + tunnel/metrics/discovery; GET/POST /extension/boot-check + Bridge Test panel — 2026-05-07 (3f911668)
- [ENH] /extension/tunnel/status runtime introspection + preferred-mode logic (MATRX_PREFER_TUNNEL) — 2026-05-07 (82fdce6a)
- [ENH] /extension/* metrics (count/errors/latencies) + GET /extension/metrics + panel — 2026-05-07 (648a17e1)
- [ENH] /extension/* JWT validation via Supabase JWKS + HS256 fallback (`app/api/extension_auth.py`) — 2026-05-07 (61a26f7b)
