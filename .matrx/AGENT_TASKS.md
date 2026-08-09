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

_(none)_

---

## Blocked

_(none)_

## Active

### Log audit 2026-07-14 — scheduled from `docs/LOG_AUDIT_2026-07-14.md`

> Source log export 2026-07-14 12:27:26. Facts + file links only in the audit doc —
> tasks below are scoped investigations. **Needs Arman approval before starting**
> (per AGENT_TASKS rules).

#### AT-L008 — Local engine 404 on `POST /v2/ai/agents/{id}`
- **Status:** filed, not started. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P1
- **Log ID:** L008
- **Scope:** matrx-local + matrx-frontend
- **Source files:** `matrx-local/app/main.py` (mounts), `matrx-local/app/api/ai_routes.py`, `matrx-frontend/lib/api/ai-api-version.ts`
- **Deliverable:** document v2 routing behavior when frontend targets `127.0.0.1:22140` (local engine) vs aidream cloud.

#### AT-L004 — Download FAILED: Tongyi-MAI--Z-Image-Turbo (huggingface_hub not importable)
- **Status:** filed, not started. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P2
- **Log ID:** L004
- **Scope:** matrx-local downloads + image-gen install
- **Source files:** `matrx-local/app/services/downloads/manager.py` (`_download_hf_snapshot`), `matrx-local/app/services/downloads/failures.py`, `matrx-local/app/main.py` (Phase 0a ordering)
- **Deliverable:** verify whether `huggingface_hub` is importable at failure time; correlate with persisted error row `f98e0567-12e3-4c0c-b217-0543eb293b41`.

#### AT-L005 — Download FAILED: black-forest-labs--FLUX.1-schnell (HF 401 gated)
- **Status:** filed, not started. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P2
- **Log ID:** L005
- **Scope:** matrx-local downloads
- **Source files:** `matrx-local/app/services/downloads/manager.py`, `matrx-local/app/services/downloads/failures.py`, `matrx-local/app/services/media_gen/paths.py` (`read_hf_token`)
- **Deliverable:** document HF token presence and gate-acceptance state for `black-forest-labs/FLUX.1-schnell` at failure time; error row `18add2b4-b22a-4d15-a752-033bb1ebae0b`.

#### AT-L006 — Download FAILED: civitai--580857-2674760 (API key)
- **Status:** filed, not started. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P2
- **Log ID:** L006
- **Scope:** matrx-local downloads + settings
- **Source files:** `matrx-local/app/services/downloads/manager.py`, `matrx-local/app/services/downloads/failures.py`, `matrx-local/app/services/media_gen/paths.py` (`read_civitai_key`)
- **Deliverable:** document Civitai key presence at failure time; error row `2f704646-7762-403b-9f28-78629fb645d2`.

#### AT-L007 — Downloads CANCELLED (sdxl-turbo + Z-Image-Turbo)
- **Status:** filed, not started. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P3
- **Log ID:** L007
- **Scope:** matrx-local downloads
- **Source files:** `matrx-local/app/services/downloads/manager.py`, `matrx-local/desktop/src/contexts/DownloadManagerContext.tsx`
- **Deliverable:** determine cancel initiator (user action vs cascade from other failures) for ids `8dff0ba9-…` and `e9efc7a8-…` at 12:01:59.

#### AT-L002 — app_config boot degraded (cache tier)
- **Status:** filed, not started. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P3
- **Log ID:** L002
- **Scope:** matrx-local app_config
- **Source files:** `matrx-local/app/main.py` Phase 00, `matrx-local/app/services/app_config/service.py`, `matrx-local/app/services/app_config/FEATURE.md`
- **Deliverable:** confirm whether registry transitioned to `ready` after background remote fetch; capture `last_error` if fetch failed.

#### AT-L010 — Skill discovery fs/list 404 WARN spam (six convention paths)
- **Status:** filed, not started. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P3
- **Log ID:** L010
- **Scope:** aidream skills ingest + matrx-local sandbox routes
- **Source files:** `aidream/packages/matrx-ai/matrx_ai/skills/ingest.py` (`walk_via_proxy`), `aidream/aidream/services/sandboxes/sandbox_autobind.py`, `matrx-local/app/api/sandbox_routes.py`, `matrx-local/app/main.py` (`_log_requests_dispatch`)
- **Deliverable:** document why probes use `root_path: "/"` and whether 404s are expected absent skill dirs on the user's machine.

---

### TASK: Salvage-audit orphan branch — macOS DMG installer (`add-auto-update-system-Pl7Ak`)
- **Status:** filed, not started. Approved by Arman 2026-07-14. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P2
- **Source:** Arman — an agent left work stranded on a branch instead of merging to
  main, so it never reached us. At some point this work was genuinely good; nobody
  knows its current value. Mine it for what's still worth taking. **Do NOT merge the
  branch and do NOT delete it** — produce an assessment + a cherry-pick/rebuild
  recommendation for Arman.

**The branch**
- Remote: `origin/claude/add-auto-update-system-Pl7Ak` (local-only remote `origin`, GitHub `armanisadeghi/matrx-local`).
- Single commit: `06ac6c50ebec1efd61e7b7a946f2ab00f926aa92` — "feat: add macOS DMG installer with drag-to-Applications experience", authored **2026-03-08** (~4 months stale; `main` has ~281 commits since the merge-base).
- **Name is misleading:** despite "auto-update-system" in the branch name, the actual commit is a **macOS DMG installer** change, not an updater. Assess what's really there, not what the name implies.
- Files touched (238+/5-): `.github/workflows/release.yml`, `desktop/src-tauri/tauri.conf.json`, `docs/installation-and-data-safety.md` (+215 lines, new), `scripts/release.sh`.

**What to do**
1. Read the full diff: `git diff $(git merge-base origin/main origin/claude/add-auto-update-system-Pl7Ak)..origin/claude/add-auto-update-system-Pl7Ak`.
2. Compatibility test: for each touched file, compare the branch's version against
   today's `main`. These are all release/packaging surfaces that have almost
   certainly evolved — determine per-hunk whether the change is (a) already done a
   better way on main, (b) obsolete, or (c) a still-missing capability worth having.
3. Specifically assess the DMG drag-to-Applications installer UX and the
   `installation-and-data-safety.md` doc — is a proper DMG installer experience
   something main still lacks? Cross-check against current `tauri.conf.json` bundle
   config and `scripts/release.sh` / `.github/workflows/release.yml`.
4. **Deliverable:** a short report (append to this task or a `docs/` note) listing
   each salvageable piece, whether it applies cleanly to current main, and a
   recommended action (cherry-pick as-is / rebuild against current release flow /
   discard). Do NOT apply changes without Arman's go-ahead — this is assess-only.
5. After Arman acts on the report, the branch can be deleted (tracked separately).

---

### TASK: Salvage-audit orphan branch — sync-loop crash hardening (`fix-client-python-execution-BpvKX`)
- **Status:** filed, not started. Approved by Arman 2026-07-14. `code analysis pending`.
- **Created:** 2026-07-14
- **Priority:** P2
- **Source:** Arman — same situation as the DMG task above: good work stranded on a
  branch, never merged, now of unknown current value. **Do NOT merge and do NOT
  delete the branch** — assess and recommend.

**The branch**
- Remote: `origin/claude/fix-client-python-execution-BpvKX`.
- Single commit: `c6ab976485f40af06e7da2c428b1220ad7906795` — "fix: remove unused import and harden sync loop against crashes", authored **2026-03-08** (~4 months stale).
- **Name is misleading:** branch says "fix-client-python-execution" but the commit is
  actually a **local-DB sync-loop hardening** change. Assess the real content.
- Files touched (28+/7-): `app/services/local_db/database.py` (1 line),
  `app/services/local_db/sync_engine.py` (crash-hardening around the sync loop).

**What to do**
1. Read the full diff: `git diff $(git merge-base origin/main origin/claude/fix-client-python-execution-BpvKX)..origin/claude/fix-client-python-execution-BpvKX`.
2. This one is higher-value to check carefully — sync reliability is load-bearing and
   the notes/file sync engine has been overhauled since March (see the notes-sync and
   file_sync work). Determine whether the crash-hardening it adds to `sync_engine.py`
   is: (a) already present/superseded on current main, (b) still a real gap, or (c)
   conceptually right but needs re-implementing against the current sync engine.
3. Cross-reference [docs/SYNC_CONTRACT.md](../docs/SYNC_CONTRACT.md) and the current
   `app/services/file_sync/FEATURE.md` / local_db sync code — does the branch's
   defensive pattern still make sense under the current architecture?
4. **Deliverable:** a short report — is there a real robustness fix here worth
   forward-porting? If yes, recommend the exact change against current code (as a new
   fix, not a stale cherry-pick). If already covered, say so and clear the branch for
   deletion. Assess-only; no changes without Arman's go-ahead.

---

### TASK: Agent Browser Testing Surface (CDP-controllable Chromium for agents)
- **Status:** filed, not started — long-term. Approved for backlog by Arman
  2026-07-14. `code analysis pending` (current-state audit done; build not scoped in code).
- **Created:** 2026-07-14
- **Priority:** P2 (long-term; unblocks agents diagnosing localhost UIs)
- **Source:** User — need agents working inside the app to actively test
  websites/UIs, run localhost dev servers, and diagnose problems in a real browser.

**Goal**
Expose the Playwright/Chromium already bundled in `scraper-service/` as a
first-class, agent-driven **CDP browser tool surface** (navigate/click/type/eval/
screenshot/console/network/DOM), backed by a long-lived headed session, so agents
can drive and diagnose sites — especially local `localhost` dev servers.

**Key insights (full detail in plan doc)**
- The current `<iframe>` "Browser" tab is useless because it *embeds* pages as
  child frames → blocked by X-Frame-Options / CSP `frame-ancestors`. Not an
  engine problem.
- We already ship a Chromium-class engine (Tauri webview) AND a real
  CDP-drivable Chromium (Playwright in `scraper-service/browser_pool.py`).
- A native webview panel is the WRONG fix here — humans can browse in it, but
  agents can't introspect/control it. Agents need **CDP**, which Playwright is.
- Real project = "expose the Chromium we already bundle as an agent tool
  surface," NOT "embed a browser."
- Being LOCAL is the whole point: agent runs `pnpm dev` + drives Chromium at
  `localhost` — no tunnel/CORS/deploy.
- Packaging catch: Chromium is **NOT bundled** — `build-sidecar.sh:225`
  auto-installs Playwright browsers at runtime (~150 MB). Bundle-vs-download is
  an open Arman decision.

**Plan doc**
[`docs/AGENT_BROWSER_TESTING_PLAN.md`](../docs/AGENT_BROWSER_TESTING_PLAN.md)

**Blocked-on / decisions (see plan doc "Open questions")**
- Bundle Chromium in installer vs. managed first-run download-as-a-STATE?
- Headed visible window MVP vs. in-app screencast required for v1?
- Origin/port allowlist policy for agent-driven browsing?

---

### TASK-001: Local GLiNER NER subsystem (URGENT)
- **Status:** in-progress — approved by Arman 2026-07-13, assigned to agent (see `app/services/ner/`, `app/api/ner_routes.py`, `app/tools/tools/ner.py`, `tests/*ner*`)
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

**Subtasks**
- [x] Arman reviews plan + answers decision checklist in the doc — approved 2026-07-13
- [ ] P1: NER service + `/ner/extract[/batch]` + small/medium download + `local_extract_entities`
- [ ] P2: queue/batching, DownloadManager `"ner"`, registry, capability probe
- [ ] P3: GLiNER2 + PII presets + large/XXL gating + optional desktop UI
- [ ] P4 (optional): ONNX/ORT fast path for default small model

**Notes**
Unblocked — Arman approved the plan 2026-07-13; build in progress. Key
constraint from research: Python sidecar subsystem, NOT LLM catalog /
llama-server.

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

- [x] **Heartbeat never bumps `app_instances.tunnel_updated_at` (= MXL-D-010)**
  — FIXED 2026-07-17: `settings_sync.heartbeat` now includes
  `tunnel_updated_at = now` whenever the tunnel URL it writes differs from the
  last URL it wrote (tracked in-memory as `_last_tunnel_url_written`), so the
  column means "when this URL appeared" rather than "last heartbeat".

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

- [ ] **/ai-surface follow-up: no local /ai/cancel/{request_id}** — aidream's
  cancel router isn't mirrored; the FE cancels via fetch-abort, and
  matrx-connect's detach-on-disconnect keeps the local turn running to
  completion (burning local compute). Wire emitter.cancel via a small
  request registry when needed.
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
## Completed

- [BUG] Windows frozen-runtime verifier rejected bundled Office templates — 2026-08-09. The v1.4.16 release proved PyInstaller classifies ZIP-based `default.docx` / `default.pptx` payloads as binary (`b`) CArchive entries on Windows rather than data (`x`); the new verifier ignored that extractable payload class and falsely reported both templates absent after the executable built successfully. `archive_data_files()` now accepts both extractable classes while retaining exact-path checks, with a Windows-backslash/binary-entry regression test. v1.4.16 release cancelled before publication; corrected by v1.4.17.
- [BUG] Office codec was broken in every packaged build — found by exercising the PyInstaller sidecar for the first time, 2026-08-09. Built the Linux sidecar from `specs/matrx-engine-x86_64-unknown-linux-gnu.spec` and ran the real codec inside it: **generating a .pptx whose slides carry speaker notes died** with `FileNotFoundError: /tmp/_MEIxxxx/pptx/oxml/../templates/notesMaster.xml`. The template WAS in the archive — `pptx/oxml/__init__.py` opens it through `<its own dir>/../templates/…`, and frozen modules live in the PYZ, so `_MEIPASS/pptx/oxml/` is not a real directory and the OS cannot resolve the `..`. Same shape in `docx/parts/*` (`default-header/footer/styles/settings/comments.xml`) and `pptx/shapes` (OLE icons); all four specs are one-file, so every shipped platform carried it. Fix: `specs/_office_bundle.py` — ONE source of truth for the four specs + the `build-sidecar.sh` fallback that (a) makes a missing Office package or template a FATAL build error instead of the bare `except Exception: pass` all four specs had (the silent skip behind the protobuf/jinja2/huggingface_hub/tqdm outages), and (b) DERIVES the `../`-referencing directories from the installed package sources and ships an inert marker into each so they materialize. Gates: `verify-frozen-runtime.py` now checks the archive's data TOC + those directories and executes the binary (`MATRX_FROZEN_OFFICE_VERIFY=1`) to generate docx/pptx/xlsx from specs, round-trip them, read raw-renderer-authored documents (headings/table/notes/multi-sheet), and confirm `classify_office` routing — on every target, no managed runtime needed; `tests/unit/test_office_bundle.py` (8 tests) pins the wiring. Verified green on the rebuilt Linux sidecar; macOS/Windows are covered by the same gate on their next release build.
- [BUG] Notes duplicate factory, final funnel (push_all) + full cloud cleanup — 2026-08-08 (Arman-directed close-out). The 2026-08-06 fix guarded full_sync/_pull_note/_handle_external_change but NOT push_all: watcher-created `never_synced` SQLite rows (34 Finder-style "label 2.md" copies surfaced by the TCC fix) were pushed as new cloud notes on 2026-08-07 with no content check, and pending pushes could resurrect soft-deleted rows. push_all now fetches the live snapshot once per tick when rows are pending and (a) never mints a cloud note whose exact bytes already exist remotely, (b) propagates a remote tombstone instead of resurrecting when local bytes are unchanged (edits still resurrect); firings scream. 4 new regression tests (13 total in `test_notes_duplication_regression.py`). Cloud fully deduped with Arman's approval: Aug-7 " 2" copies + all 172 residual same-path pairs (byte-identical, kept older row — closes the 2026-08-06 ARMAN_TASKS decision) + 3 stale-hash same-path pairs → 754 live notes, 0 duplicate groups, 0 same-path collisions.
- [BUG] macOS notes/files silent EPERM (Documents folder) root-caused + fixed — 2026-08-06. Engine helper (`Matrx Engine.app`) enumerated `~/Documents/Matrx/{Notes,Files}` → errno 1 on every launch while FDA was granted and create/read/write/replace/delete all worked: macOS's per-folder Files & Folders TCC service denies SILENTLY (no prompt) when the bundle lacks `NSDocumentsFolderUsageDescription`. Added the 5 folder/volume usage keys to BOTH bundles (`specs/matrx-engine-{aarch64,x86_64}-apple-darwin.spec` + `desktop/src-tauri/Info.plist`), so the consent prompt can appear; access-health message now names Files & Folders (evidence-based: FDA granted + POSIX exonerated + path under a TCC folder) instead of blaming "folder permissions/ownership". Tripwire: `tests/unit/test_folder_usage_descriptions.py`; message pins in `tests/smoke/test_access_health.py`.
- [TEST] Notes duplication-factory regression suite — 2026-08-06. `tests/characterization/test_notes_duplication_regression.py` (9 tests) pins every guard from the e02846daf/24a7e659e fixes: contested-pull identical-content skip, reroute+CAS convergence (no `_2_2` chains), full_sync pathless-twin adoption, bound-twin skip, genuine-new-note still pushes, remote-tombstone deletion wins for unchanged bytes / local edit resurrects, watcher duplicate guard. Cloud verified same day: 0 suffix-chain labels, 0 same-label dupes among 892 live notes (176 same-path byte-identical pairs remain from pre-cleanup — cloud-side dedup candidate, needs Arman).
- [DOC] MXL-D-060 fixed (docs/official edit explicitly approved by Arman in the 2026-08-06 docs-hygiene session) — `docs/official/settings-catalog.md` § 3 rewritten to describe the current session-only API-key validation contract: `POST /settings/api-keys/{provider}/validate` \| `/validate-all` return a live verdict that is never persisted (`ApiKeysRepo.record_validation()` no longer exists; schema V17 drops the historical `api_key_validation` blob; the frontend only keeps it in React state) — 2026-08-06.
- [BUG] MXL-D-016 chat silent agent-null on pick-before-sync — verified fixed 2026-08-06 (`ChatPanel.tsx:383-403`: miss now sets `pendingAgentId` + `agentSelectionError` banner and blocks send via `sendBlockedReason`); FOUND_DEFECTS entry closed.
- [BUG] Notes sync duplicate factory — 2026-08-06. Two compounding defects in `app/services/documents/sync_engine.py` mass-duplicated cloud notes (764 corpus clones 2026-07-14, ~2,100 suffix-chained `label_2_2_…` notes 2026-07-29/30): (1) `full_sync` minted a brand-new cloud note for any local file lacking a SQLite identity + cloud path match — a fresh/second SQLite (dev vs live engine, `--fresh` homes) re-minted the entire replica; (2) the contested-path reroute in `_pull_note` allocated `label_2.md` but could never write it back (`set_file_path_if_null` fails on non-null), so every cycle allocated another suffix and each orphan fed defect 1. Fix: content-hash adoption/skip guard in `full_sync` (never mint when identical bytes exist remotely; adopt pathless twins), byte-identical contested pulls no longer materialize, reroutes converge via new `set_file_path_if_matches` CAS, same guard in `_handle_external_change`; guard firings scream. Cloud data deduped (3,491 → 892 live notes, soft-delete, identical-content-only). Tests: characterization + parity suites pass.
- [BUG] Download records were immortal — 2026-07-19. Stale action-needed prompts (Civitai key chip that outlived the fixed key; `hf_gate_not_accepted` card that survived the accepted license AND the running re-download) and months-old failures re-warned on every boot with no way to clear them. `enqueue` now re-queues a failed/cancelled row IN PLACE (one artifact = one row) and purges superseded duplicates; new `POST /downloads/{id}/dismiss` + `DownloadManager.dismiss` permanently deletes terminal records (memory + SQLite + `removed` SSE event); Dismiss buttons on prompt cards + history rows. Doctrine: `app/services/downloads/FEATURE.md`. Tests: `tests/unit/test_download_resume_reconcile.py`.
- [BUG] macOS Quit recorded as a crash — 2026-07-19. Every intentional Quit SIGABRT'd in GGML's Metal atexit static destructor (verified from the .ips report: `terminate:` → `exit` → `__cxa_finalize_ranges` → `ggml_metal_device_free` → `ggml_abort`), so macOS filed "quit unexpectedly" after every clean shutdown. `RunEvent::ExitRequested` now ends true quits with `_exit(0)` after `graceful_shutdown_sync` completes (window state saved explicitly first; update restarts untouched) so no atexit destructor can crash a finished shutdown (`desktop/src-tauri/src/lib.rs`).

- [BUG] MXL-D-070 single-ML-stack overhaul — 2026-07-19. Capability recipes (ner/transcription) no longer install their own torch: `requires_ml_runtime` consumes the managed media runtime slot (installed first when absent), installs resolve against the slot's exact pins via constraints + `--dry-run --report` thin plan, `app/services/optional_packages/guardrails.py` screens/sanitizes every install path (incl. lightweight caps; audio_recording's explicit numpy removed), startup sweep strips legacy torch copies from capability dirs, tripwire tests `tests/unit/test_ml_stack_guardrails.py` encode consumer compat floors (gliner≥0.2.27, numpy<2.5 numba ceiling, transformers<5.7). Compat verified by metadata research + empirical import/forward-pass test against slot pins. Doctrine: `app/services/optional_packages/FEATURE.md`.

- [BUG] MXL-D-062/064/065 fixed: deterministic atomic managed image runtime — 2026-07-19 (`85e22c256`, `v1.3.159`)

- [UI] Canonical app version shown on startup, first run, login, and footer — 2026-07-19 (`desktop/src/lib/app-version.tsx`)

- [BUG] v1.3.145 engine freeze + image-gen outage fixed: shared packages now collect WHOLE into the frozen bundle via one list (`huggingface_hub.dataclasses` was missing, so a partial bundled hf 1.8.0 shadowed the complete managed 1.24.0 and killed EVERY model load on every platform), and pipeline-lifecycle vs generation-bookkeeping locks were split so status/cancel never queue behind a ~50s model load (nine unrelated endpoints were timing out at 30s on a mutex) — 2026-07-19 (`specs/_managed_runtime_bundle.py`, `specs/*.spec`, `scripts/build-sidecar.sh`, `app/services/image_gen/service.py`, `app/services/video_gen/service.py`). Adversarial review then found the SAME lock defect shipped in TTS (five `async def` methods took the model-lifecycle lock on the event loop) and a video cancel-handle race — both fixed in the same change (`app/services/tts/service.py`). The packaged smoke then caught a THIRD, unrelated shipped defect: `list_audio_input_devices` was a plain sync `#[tauri::command]`, so it ran on the main thread — the tao/NSApplication event loop — and blocked forever in CoreAudio `mach_msg`, freezing the UI on mount and leaving the app unquittable (2886/2886 main-thread samples in CoreAudio; reproduced 3/3, and confirmed pre-existing by a control build at clean HEAD). Fixed with `#[tauri::command(async)]` (`desktop/src-tauri/src/transcription/commands.rs`). Filed MXL-D-061..066 for the unbounded filesystem index, the floating managed-runtime pins, the clean-path discovery file, append-order version shadowing, the unlinked shared-bundle list, and the pre-existing keychain smoke-test timeout.

- [UI] Full light/dark audit: pre-paint theme, canonical controls/media, accessible status palette — 2026-07-19 (`desktop/src/index.css`)

- [BUG] MXL-D-018 fixed: packaged startup follows the Rust-owned child and its actual port for a 300s cold-start window, exits early only when the child really dies, and uses the same contract for Settings/recovery restarts — 2026-07-19 (`desktop/src/lib/sidecar.ts`, `desktop/src/hooks/use-engine.ts`)
- [BUG] Dev/live engine isolation fixed (logged under a mistaken 'MXL-D-060' ID — unrelated to the settings-catalog MXL-D-060): web, packaged, and pytest engines use private homes, ports, cloud state, and PID-only cleanup — 2026-07-19 (`scripts/smoke.sh`, `tests/conftest.py`)
- [AT-L001] Deferred local-LLM registration until engine discovery — 2026-07-18 (`desktop/src/hooks/use-llm.ts`)
- [BUG] Fixed Wi-Fi probe deadline and restored-cancellation warning noise — 2026-07-18 (`app/services/permissions/checker.py`, `desktop/src/lib/downloads/logging.ts`)
- [BUG] Startup model transfers now require Retry, restored failures warn as history, and frozen sidecars bundle Jinja chat-template modules — 2026-07-18 (`app/services/downloads/manager.py`, `desktop/src/lib/downloads/logging.ts`, `specs/`)
- [BUG] Packaged macOS startup now isolates Keychain reads in an identity-preserving, timeout-bounded private-pipe helper with cross-process DEK locking — 2026-07-18 (`app/common/keychain_helper.py`)
- [BUG] Managed image-runtime startup now resolves Torch/Torchvision as an ABI-compatible pair, observes background migration failures, and prevents dev-home package symlinks from reading or rewriting the live app runtime by routing them to an in-home fallback without mutating user state — 106 focused media tests + 7 optional-package isolation tests green (2026-07-18)

_(one line each, newest first; full detail in git history)_
- [BUG] MXL-D-057 fixed: `app.config` and `app.common` cold-import independently — 2026-07-18 (`tests/smoke/test_cold_imports.py`)
- [FEAT] Unified proactive grant remediation across permissions, provider keys, licenses, tools, panels, and downloads — 2026-07-18 (`desktop/src/features/action-needed/`)
- [FEAT] Cross-platform filesystem intelligence completed: real-filesystem authority drops cloud-file tools; progressive fair metadata/content indexing spans Windows/Linux/macOS with priority roots, maintenance, quotas, optional semantic search, structured local_file results, shared desktop tree/artifact rendering, Settings controls, and bounded sandbox list/read contracts; live tool catalog applied and drift-clean — 2026-07-18
- [BUG] MXL-D-058 fixed: continued chat turns allocate from durable message positions and reconcile compacted tool history without dropping the new user message; conversation activity now exposes correlated tool calls/traces — 2026-07-18
- [BUG] MXL-D-054 fixed: per-file perl-alarm timeout (120s) on both macOS dylib re-sign loops (`build-sidecar.sh` + release.yml); a codesign hang is now a loud hard failure instead of an eternal silent wedge — 2026-07-18
- [FEAT] Cloud Chat is now a platform Surface: every cloud request declares `client:{surface:"matrx-local/desktop", capabilities:["desktop-native"], state}` so aidream auto-injects the desktop tools (agent-level `auto_tools_disabled`/allow-lists respected); in-view delegated-tool continuation via loopback UI stream claims (engine defers `/resume` while claimed, self-heals on TTL); desktop tool taxonomy consolidated 9→2 categories (`desktop`/`desktop-web`) across actions.py + live DB (aidream migration 0201, applied + ledgered) with stale 0171 bundles deactivated; composer "+" menu (model/temperature/max-tokens overrides, per-conversation tool exclusions, text-file attachments, coming-soon rows); web-controllable `cloud_tools.disabled_tools` exposure setting synced via app_settings and enforced each sweep + Settings → Cloud card; docs in docs/CLOUD_CHAT_SURFACE.md + delegation FEATURE.md; 171 unit tests + tsc green — 2026-07-18
- [BUG] Packaged Cloud Chat restored end-to-end: AIDream admits exact Tauri production origins; desktop removes bodyless-GET preflights, exposes catalog failures, resolves model UUID labels, and preserves Error details; live `chat.tool_call` schema verified and local mirror refreshed, closing AT-L003 — 2026-07-17
- [TASK-002] Restored remote app-config authority for desktop Cloud Chat and compute routing; cached renderer config, removed packaged AIDream env dependency, and verified release checks — 2026-07-17 (`desktop/src/lib/app-config.ts`)
- [FEAT] Multi-window support (VS Code-style, Arman-approved plan 2026-07-15): full peer windows (`peer-N`, File > New Window / Cmd+Shift+N / tray) + lightweight panel windows (`panel-<page>` for chat, cloud-chat, notes, activity, ports, transcription, tts, media-generation, media-gallery) with per-panel provider manifests; Rust `windows.rs` registry + leader election (`window-leader-changed`), last-full-window-only close-to-tray/shutdown, native menus (`menu.rs`, macOS NSApp Window menu), dynamic tray window list, capabilities globs, `tauri-plugin-window-state` (overlay denylisted); frontend `window-role.ts`/`useWindowLeader` gate background tasks, cloud heartbeat, and auto-update polling to the leader; overlay window no longer boots the full app; transcription-sessions cross-window storage sync + per-label sidebar collapse; docs in `desktop/src/panels/FEATURE.md` — 2026-07-15
- [FEAT] Application Recovery unified end-to-end: real awaited page refresh + provider-preserving view remounts + page-local crash boundaries, shared watchdog/history and Recovery Center, native renderer reload, PID-scoped engine restart, ownership-safe app relaunch, Windows stale-sidecar-handle repair, backend ServiceController registry + capability-driven image/video repair and truthful engine-restart escalation, supervised durable image worker, media lifecycle teardown, and scoped preview/backup/confirm settings resets; verified with 76 frontend tests, 29 focused Python media/recovery tests (23 pass/6 platform skips), cargo check, production web smoke, and an isolated real-engine boot/probe/clean-shutdown drill — 2026-07-15
- [BUG] Image-model load forever-spinner fixed: Supabase auth callbacks no longer await engine network work while holding the auth lock (the SIGNED_IN → connectWebSocket → getSession reentry deadlocked all later authenticated calls before `/image-gen/load` was sent); token lookup now has an independent 10s fail-safe so auth failures surface instead of bypassing HTTP timeouts — live stuck-state evidence + 71 frontend unit tests + production web smoke clean — 2026-07-15
- [BUG] AT-L009 + the prompt-less local-agent follow-up fixed end-to-end: matrx-ai now owns `ExecutionAgentSource` + the one canonical full-definition mapper; AIDream exposes authenticated owner/public master+version definitions; Local hash-validates/caches the opaque definition and retries from conversation agent provenance, never picker metadata or empty prompts; frontend gates direct Local routing on `agent_execution_v1` and classifies structured 422s correctly — 2026-07-14
- [FEAT] Upstream matrx-ai 0.4.0 client-host writes reach the WriteCoordinator + Turn-Boundary Inbox reads cxm; host-side coordinator-guard monkeypatch DELETED with the >=0.4.0 floor bump; pinned by `tests/client_host/test_no_db_streaming_with_tool.py` — 2026-07-13
- [BUG] MXL-D-052 fixed (chat_sync, W2): local mirror wrote `source='model'` for AI/tool turns while the canonical vocabulary (aidream `Message.source` default + cloud `cx_message_source_check`) is user/agent_template/system — `source` is row ORIGIN, `role` carries authorship — so every assistant message 400'd on push and dead-lettered; fixed all three write sites (`MessagesRepo.create`, `conversation_handler.persist_completed_request`, V10 cutover CASE) to `'user'`, added V13 repair migration (remaps rows, resurrects dead-letters, re-enqueues with UUID/legacy-conversation guards); verified live: dev-world module-level drill pushed all 7 previously-rejected messages to the cloud (sent=7 failed=0, rows confirmed in live `chat.message` with source='user', roles intact); 2 pins in `tests/characterization/test_chat_mirror_characterization.py` — 2026-07-13
- [BUG] Notes access-degraded UX fixed (Errors→Prompts, tracker W8 notes row): Documents page now shows a first-class `NotesAccessPrompt` (FDA explanation + System Settings deep-link via the permissions system, Check again + 10s auto-poll, Create-folder for missing dir) instead of silently empty lists; engine adds `GET /notes/access` + `POST /notes/access/recheck` (guard gains kind + platform-appropriate reason) and a live-found raw 500 in `GET /notes/sync/status` (unguarded `.exists()` in `list_conflicts`) is fixed; chmod-000 drill verified degrade→recover without restart; 12 pytest + 3 vitest pins — 2026-07-13
- [BUG] MXL-D-047 fixed (Errors→Prompts, tracker W8): every HF call now resolves the stored token AT REQUEST TIME from the app key store (raw-URL GGUF path, model_repo analysis, NER snapshot — the HF-snapshot path already did); HF/Civitai auth refusals classify into `DownloadResolution` states with exact attribution (token-present gated 401 → "accept the license" / "access pending", never "re-enter your token"); actionable failures log INFO `[action-needed]` (engine + client), STATE log splits `action_needed` from `fails`; stale pre-taxonomy failure rows re-triaged onto the taxonomy in `_load_history`; UI renders first-class "Needs your action" prompt cards (action button + "Check again & retry") atop the Downloads panel, DownloadActionDialog annihilated. Verified live (`--live` engine, Arman's real store): stale FLUX row → `hf_gate_not_accepted` with his token present, re-triggered FLUX.1-schnell downloads authenticated; 20 unit tests pin it (`tests/unit/test_hf_token_and_failure_states.py`) — 2026-07-13
- [BUG] MXL-D-046 fixed: `TokenRepo.is_expired` now decodes the JWT `exp` claim (stored `expires_at` carried the ~7-day session expiry while the access token was dead — every sync loop ran "configured" into guaranteed 401s); confirmed live then fixed (`app/services/local_db/repositories.py`) — 2026-07-13
- [BUG] MXL-D-043 fixed: dev/live worlds fully isolated — source-run engines self-isolate (run.py guard: `~/.matrx-dev`, ports 22240-22259, orphan sweep off, salted cloud instance id, model caches symlinked, proxy 22280, diagnostics in dev home, trayless so SIGTERM works — sidesteps MXL-D-042 for dev); discovery-file clobber + non-owner-update guards in preflight (pinned by `tests/characterization/test_discovery_guard.py`); debug Rust reads dev world + never pkills packaged engines (a likely MXL-D-039 trigger); frontend port base via `desktop/src/lib/engine-ports.ts`; `scripts/dev.sh` (+`--fresh`/`--live`); CLAUDE.md Hard Rule 9 + `docs/TESTING_LADDER.md`. Verified live: two dev engines coexisted with the guard holding (2nd bound 22241, discovery owner unchanged), live `~/.matrx` untouched, SIGTERM → clean death 1s; 170 characterization + 265 smoke tests green — 2026-07-13
- [ENH] **Canonical local DB mirror + chat sync (handoff `docs/handoffs/canonical-local-db-mirror.md`)**: generated structural mirror of cloud schemas (`schema_mirror/` + `scripts/generate_mirror_schema.py` → ATTACHed `~/.matrx/mirror/chat.db`), V10 cutover annihilated bespoke conversations/messages/user_requests/tool_call_logs, `SQLiteConversationStore`+repos write canonical `chat.*` + outbox, new `app/services/chat_sync/` engine (push parent-first w/ echo-back, keyset incremental pull, LWW + pending-outbox protection, tombstones), managed service `chat_sync`, `/chat/mirror/status|sync`; RLS drill passed all 22 chat tables after fixing missing grants on `chat.conversation_value` (aidream migration 0167, applied live); SYNC_CONTRACT gap #1 CLOSED; pinned by `tests/characterization/test_chat_mirror_characterization.py` — 2026-07-13
- [BUG] MXL-D-030 fixed: `/health` now reflects launcher registry (ok/degraded/failed_services + service names), still HTTP 200 + probe-cheap (`app/api/routes.py`) — 2026-07-13
- [BUG] Broadcast subscribe gate read removed env var `MATRX_BRIDGE_BROADCAST_ENABLED` — now gates on the `extension_broadcast_enabled` setting; cross-machine fallback revived (`app/main.py` Phase 7, `app/api/token_routes.py`) — 2026-07-13
- [ENH] Engine-owned notes auto-sync loop (managed service `notes_sync`): persisted-JWT config, watcher ensure, 10-min incremental + daily full reconcile; verified live push 33/pull 2 to `workbench.notes` (`app/services/documents/sync_engine.py`) — 2026-07-13
- [BUG] Download manager resumed HF downloads before image-gen sys.path injection → false "AI packages are not installed"; start reordered after injections (`app/main.py` Phase 0a) — 2026-07-13
- [BUG] Auto-started llama-server never registered with the engine (llm-server-ready fired before React listener); on-mount reconciliation added (`desktop/src/hooks/use-llm.ts`) — 2026-07-13
- [BUG] Packaged banner "matrx-ai = NOT INSTALLED" was a copy_metadata omission — matrx-* dist-info bundled in all 4 specs + build fallback — 2026-07-13
- [BUG] Civitai 401/403 conflation: now distinguishes no-key / key-rejected / access-restricted with correct actions (`app/services/downloads/`) — 2026-07-13
- [ENH] File-management tools Move/Copy/Delete(trash-first)/Rename/Mkdir + Edit replace_all; catalog 108→113, cloud `tool.definition`/`tool.binding` updated via tool_sync changeset, drift clean — 2026-07-13
- [ENH] matrx-* floors bumped to the published wave (matrx-ai 0.3.12, matrx-utils 2.0.0, matrx-orm 3.1.9 + explicit connect/graph/runtime pins); publish-gate comments retired — 2026-07-13

- [x] Notes + realtime sync overhaul (freeze, false conflicts, dead sync): paste/typing freeze fixed (deferred memoized markdown preview + 128KB guard + stable use-documents callbacks + memoized NoteList/FolderTree); realtime repointed public→workbench with created_by filter (was receiving ZERO events) + last_device_id echo suppression + on-subscribe catch-up pull; note_hashes now recorded only after a SUCCESSFUL push (offline-edit clobber risk); own-push guard in pull/full_sync kills self-conflicts; push collapsed to one upsert (was 5 round trips, 3 hitting graveyarded tables); all graveyarded-table client calls deleted (versions→local+history.row_versions trigger, devices/mappings/sync_log→local, shares→501+UI removed); engine-owned auto-sync loop (10-min tick + daily reconcile from stored auth token, launcher-registered); watcher auto-starts + loud on crash; identical-side conflicts auto-pruned; mappings dialog now shows local mappings (was always-empty cloud list). SYNC_CONTRACT.md amended; characterization pins updated. Rounds 2-3 (from two adversarial workflow passes, 25 confirmed findings total): updated_at incremental cursor (sync_version is a per-row counter — was blind to most remote edits and ALL deletes), optimistic-concurrency conditional pushes (no more silent cross-device last-writer-wins), pathless web-note import + collision-free allocation + conditional device-stamped write-back, tombstone/path-reuse/clock-skew guards, device_id moved to ~/.matrx (iCloud-shared notes dir = shared identity = silent ping-pong), delete routes under the sync lock, cursor stop-on-failure (missed tombstone would be resurrected cloud-wide), editor draft-guard made real (keystroke echo defeated it). Filed MXL-D-045 (first-import lock hold) (2026-07-13, commits 9f494a5dc..fc531e80c)
- [x] Folder delete now tombstones contained notes in SQLite + per-note cloud soft-deletes + sync-state hash pruning — offline folder deletes no longer resurrect notes — closes MXL-D-002 (2026-07-13)

- [x] Deleted stale untracked `specs/aimatrx-engine-*.spec` leftovers from disk (git already dropped them in 2e42a1134; the disk copies were the Hard Rule 6 edit-the-dead-set trap) — closes MXL-D-031 (2026-07-13)

- [x] Silent-engine-death investigation + fixes (root cause of "image system frozen until app restart"): engine never wedges while alive — the freezes were a live UI over a dead engine. Fixed: loud signal-handler/shutdown logging + run.py logger black hole + uvicorn records into system.log (MXL-D-036), `timeout_graceful_shutdown=5` + 25s teardown join + every exit path logs (MXL-D-037, the old silent 10s `os._exit(0)` was the vanishing act), Rust `lifecycle.log` attributing every engine kill/spawn/exit with reason (MXL-D-039 instrumentation), access.log rotation at 10MB (MXL-D-041, was 560MB), google.protobuf bundling + runtime-dir purge fixing matrx-ai init failing every packaged boot (MXL-D-040), app-wide EngineDownBanner + hook loading→error fixes so a dead engine is announced instead of infinite spinners (MXL-D-038). Verified live: SIGTERM'd isolated engine → full shutdown narrative in system.log, clean exit ~1s; protobuf purge healed this machine (7.34.1 → bundled 6.33.5) (2026-07-13, commits ac73b7b62..)

- [x] Canonical media layer (`desktop/src/components/media/`): ONE `MediaDescriptor` + `MediaThumb`/`MediaItemThumb` + app-wide lightbox/info-dialog/right-click menu + full action set (full screen, info, download, copy image, copy prompt, delete, vault, restore, use-as-input, **Remix**, show-in-folder, reuse-seed) behind `MediaActionsProvider`; deleted the forked Gallery/Library detail dialogs and per-surface blob loaders. Engine now stores the img2img source image beside the result and encrypts it into the vault (so vaulting never destroys it) — that is what makes Remix complete. One app-level library + vault store with cross-store removal/lock events, so delete / move-to-Private / restore / vault-lock update every surface in the same tick. Fixes: info-dialog 10-page horizontal scroll, lightbox exiting after ~3 images, truncated-prompt dead ends, non-canonical video surfaces. New defects filed: MXL-D-034, MXL-D-035 (2026-07-12, v1.3.106)
- [x] API-key validation: shared `app/services/ai/key_validation.py` provider→spec registry (free auth-only endpoints, tri-state valid/invalid/unknown/unsupported) + `POST /settings/api-keys/{provider}/validate` & `/validate-all` + Test / Test All buttons and verdict badges in Settings → API Keys; `downloads/manager.py` HF whoami one-off absorbed into it; `elevenlabs`+`fastino` added to `VALID_PROVIDERS` (were in `PROVIDER_ENV_MAP` only → PUT/bulk 422, `.env`-only) and to `api-key-patterns.ts` (2026-07-12)
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
