# FOUND_DEFECTS.md — Matrx Local

> **Holding area — NOT an approved worklist.** Spot something while doing other
> work? File it here with evidence, then keep going. Do **not** treat open
> entries as assigned tasks.
>
> **Who acts on what**
> - **Working agent (opportunistic):** If you hit an open entry and can fix it
>   cleanly in scope, fix it — then **DELETE the entry** and add a one-line
>   completed item to `.matrx/AGENT_TASKS.md` (what + date + defect ID + commit).
>   Fixed entries never sit here; detail lives in git history.
> - **Cleanup / promotion agent (`/task-hygiene`):** Does **not** silently
>   implement open entries. Proposes ≤3 promotions into `.matrx/AGENT_TASKS.md`
>   at a time for Arman's approval.
> - **Any agent re-encountering an open entry:** Remind Arman — get approval to
>   fix **now**, or promote it to an agent task. Do not keep rediscovering in silence.
>
> Check **## Rejected** (bottom) before filing — don't re-file a won't-fix.
> Defects belonging to another repo go in THAT repo's own FOUND_DEFECTS.md.
> Related: `.matrx/AGENT_TASKS.md` (approved work), `.matrx/ARMAN_TASKS.md` (ask Arman).

Format: one defect per entry — id (`MXL-D-###`), area, symptom, evidence
(file:line), status (`open` / `needs-hw-verification` / `blocked-external`),
analysis stamp (`Unverified — from docs/logs only` or `Analyzed <date> —
verified in code: …`), owner hint. Date everything YYYY-MM-DD.

_Last hygiene pass: 2026-07-12 — 13 entries deleted as duplicates of open
`.matrx/AGENT_TASKS.md` tasks (MXL-D-002/003/004/007/008/009/010/011/019/022/
023/024/026 — the tasks carry the full evidence and keep the IDs inline)._

---

## Notes sync / data safety

### MXL-D-074 — Notes sync mass-deleted 2,600+ cloud notes across four waves; local trigger unidentified
- **Area:** `app/services/documents/sync_engine.py` (tombstone propagation:
  `full_sync` local-file-gone branch, `_handle_external_delete` watcher path),
  `app/api/document_routes.py` (`_delete_folder_locked`, `DELETE /notes/{id}`)
- **Symptom:** Cloud `workbench.notes` for `arman@armansadeghi.com`
  (4cf62e4e-…) received sequential device-stamped soft-deletes in four waves:
  2026-08-06 19:54–19:55 (464), 2026-08-07 21:47–22:32 (2,135), 2026-08-08
  08:07–08:12 (176) — devices `921d9676-75d` (primary) and `5bc17b18-13e`.
  Verified in `history.row_versions` / `workbench.notes` on 2026-08-08. The
  user's "recent notes" list became months-old notes; deletions were only
  noticed by accident. All rows are SOFT-deleted (recoverable); restore
  decision is with Arman (SQL in the incident PR description).
- **Status:** open, NARROWED (2026-08-08 second pass). Content-verified in
  the DB: **every wave-deleted note has a live byte-identical copy — zero
  unique content was lost**; the waves were the intentional duplicate-factory
  cleanup (times correlate exactly with the dup-investigation commits
  `e02846d`/`24a7e65` Aug 6 19:56Z and `34e2b31`/`9c8c95b` Aug 8 08:09–08:13Z;
  PR #6 → v1.4.14). The factory itself was still minting `_2` copies on Aug 7
  22:16–22:39Z (pre-fix engine live); two leftover identical dups were removed
  Aug 8. **Mitigation shipped:** mass-delete circuit breaker (PR #7 —
  `allow_cloud_delete`, budget max(25, 10% of live corpus)/24h, persisted trip
  state, explicit `POST /documents/sync/delete-breaker/reset`). **Remaining
  (on-machine only):** confirm the deletion driver in `~/.matrx/logs`, check
  which device id was a DEV engine (possible MXL-D-043-class breach), verify
  the installed app is ≥ v1.4.14, and factory-dead verification — full recipe
  in [docs/handoffs/notes-sync-on-machine-verification.md](docs/handoffs/notes-sync-on-machine-verification.md).
- **Owner hint:** an agent on Arman's machine, per the handoff doc.

---

## Updater / packaged runtime

### MXL-D-067 — Background updater replaces the live PyInstaller sidecar and corrupts all later lazy imports
- **Area:** `desktop/src/hooks/use-auto-update.ts:159-180`,
  `desktop/src-tauri/src/lib.rs:1470-1512`, packaged `Matrx Engine` one-file
  executable
- **Symptom:** The leader window's "silent pre-download" passes `install=true`,
  so Tauri's `download_and_install()` replaces `/Applications/AI Matrx.app`
  while its Python sidecar is still running. PyInstaller reopens its own
  executable for every lazy PYZ import. The running process retains the old
  archive TOC/offsets but reads those offsets from the newly installed
  executable, producing repeated `zlib.error: incorrect header check` across
  AI, chat sync, delegation, scraper, tools, schedulers, and finally Uvicorn
  lifespan startup. The engine exits and the whole local surface is offline.
- **Evidence:** 2026-07-19 production timeline: lifecycle log spawned sidecar
  PID 54982 at 08:34:05; filesystem metadata records the installed app bundle
  being replaced at 08:34:34-35; first PYZ error at 08:34:41; engine startup
  exited at 08:34:47. The current on-disk v1.3.148 archive was independently
  validated: all 12,277 PYZ entries decompress successfully, and the same
  binary starts after restart. This excludes a bad build or missing hidden
  import and proves live replacement as the trigger.
- **Status:** open — production-critical.
- **Analysis stamp:** Analyzed 2026-07-19 — verified against production logs,
  installed-file birth/change timestamps, the PyInstaller 6.18 loader source,
  and a complete archive extraction pass.
- **Owner hint:** Download may happen in the background, installation may not.
  Stop/drain the sidecar before replacing the bundle, then install and relaunch
  as one ownership-safe transaction. Add a packaged test that keeps making
  lazy imports during update installation and proves no old process can read
  the replaced executable.

### MXL-D-068 — Documents access is a macOS MAC/TCC denial but diagnostics falsely exonerate privacy controls
- **Area:** `app/services/access_health/probes.py:183-285`,
  `app/services/access_health/service.py:445-455`, `desktop/src-tauri/Info.plist`
- **Symptom:** The signed engine can create/write/read/delete its own probe
  inside `~/Documents/Matrx`, but `os.scandir()` of existing Notes, Files, and
  Code content returns EPERM. The access-health code sees an unrelated FDA
  probe succeed and tells the user this is ownership/volume state, even though
  EPERM is the macOS sandbox/MAC class and Files & Folders is distinct from
  Full Disk Access. Notes sync, file replica, direct list calls, and indexing
  all remain degraded. The shipped main/helper Info.plists omit
  `NSDocumentsFolderUsageDescription`; the canonical root is also inside the
  iCloud Drive File Provider Documents domain and no durable user-selected
  security-scoped grant is used.
- **Evidence:** POSIX ownership/mode/ACL permit access and the same shell can
  enumerate all three directories. Live `/access/health` shows only enumerate
  denied (errno 1), while create/write/read-own/replace/delete all succeed.
  Installed helper is Developer-ID signed and can read the FDA-protected
  Messages DB, so the FDA probe is genuinely granted but does not explain the
  protected Documents read denial.
- **Status:** open.
- **Analysis stamp:** Analyzed 2026-07-19 — verified against the installed app,
  live engine capability probes, codesign/Info.plist output, and filesystem
  metadata.
- **Owner hint:** Add the Documents usage description to the responsible app,
  obtain/persist an explicit user-selected directory grant (security-scoped
  bookmark), keep helper responsibility/signing identity stable, and stop
  treating an FDA success as proof that every other MAC privilege is granted.

### MXL-D-069 — Permission recovery can lose the filesystem crawler permanently on SQLite contention
- **Area:** `app/services/filesystem/service.py:727-798`,
  `app/services/filesystem/index.py:653-691`
- **Symptom:** A normal EPERM from `os.scandir()` enters `_crawl_loop`'s error
  path, but `fail_directory()` runs after `_maintenance_lock` has been
  released. Another filesystem worker can acquire the lock and write first;
  after the 10-second SQLite busy timeout, the recovery write raises
  `sqlite3.OperationalError: database is locked`. That exception escapes the
  OSError handler and terminates `filesystem-crawl`. `_background_task_done()`
  logs it but only restarts enrichment, never crawl, so indexing silently stops
  until the whole engine restarts.
- **Evidence:** Exact production trace at 2026-07-19 08:55:31: initial EPERM
  scanning `~/Documents/Matrx/Code`, followed inside `fail_directory()` by
  `database is locked`, followed by `Filesystem background task
  filesystem-crawl exited unexpectedly`. Code inspection confirms the recovery
  write is outside the maintenance lock and crawl has no supervisor restart.
- **Status:** open.
- **Analysis stamp:** Analyzed 2026-07-19 — production trace and code path agree.
- **Owner hint:** Fence claim failure/release under the same maintenance lock,
  handle SQLite contention inside the crawl loop, and supervise/restart crawl
  just as enrichment is supervised.

### MXL-D-071 — fastembed puts a second tokenizers/huggingface_hub/numpy set in the engine env with no isolation contract
- **Area:** `pyproject.toml` `[project.optional-dependencies] embeddings =
  ["fastembed>=0.7.0"]`; imported in-process at
  `app/api/openai_compat_routes.py:114` and
  `app/services/filesystem/service.py` (580/670/1010).
- **Symptom:** fastembed is the only in-engine ML consumer with no install
  contract: no capability installer, no `--target` dir, no guardrail
  screening — it must arrive via `uv sync --extra embeddings` (dev) or a
  bundle, and it drags onnxruntime + tokenizers + huggingface_hub + numpy
  into the engine environment. If its resolved tokenizers/hf-hub versions
  ever diverge from the managed runtime slot's pins (tokenizers 0.22.2,
  hf-hub 1.8.0), engine-env copies win over the slot by sys.path order and
  the slot's transformers 5.3 runs against a tokenizers it wasn't locked
  with — the same class of mismatch as the resolved MXL-D-070, one layer up.
- **Status:** open.
- **Analysis stamp:** Analyzed 2026-07-19 during the single-ML-stack
  guardrail build (see `app/services/optional_packages/FEATURE.md`); shipped
  frozen builds may not bundle fastembed at all — first step is determining
  where it actually ships.
- **Owner hint:** Either give embeddings a real capability recipe under the
  guardrail system (screened, sanitized, slot-constrained) or pin fastembed's
  ML-family transitives to the slot's exact versions in
  `pyproject.toml` `constraint-dependencies` and assert alignment in
  `tests/unit/test_ml_stack_guardrails.py`.

---

## AI / local LLM runtime

### MXL-D-055 — `get_local_llm_status()` is a status *getter* with a destructive side-effect; can self-deregister a healthy/cold llama-server
- **Area:** `app/services/ai/local_llm_registry.py` `get_local_llm_status()` (lines ~226-255); caller `app/api/chat_routes.py` `/local-llm/connect` (~519-521)
- **Symptom:** `get_local_llm_status()` does a live `_probe_llama_server()`
  (single GET `/v1/models`, `timeout=1.0`, no retry) and on ANY failure calls
  `clear_local_llm()` — nulling `_local_llm_port`/`_local_llm_model` and
  unregistering the runtime model + UnifiedClient instance. Two consequences:
  (1) A function named/typed as a status read mutates global registration
  state — surprising side-effect. (2) `/local-llm/connect` calls
  `set_local_llm(...)` then immediately `get_local_llm_status()`; if the
  just-started llama-server can't answer `/v1/models` within 1.0s (cold model
  load — exactly when connect fires), the status call deregisters what was
  just registered and returns `available: false`. A concurrent status poll
  during active inference (llama-server serves one request at a time, so
  `/v1/models` can stall > 1.0s) can also deregister a healthy, in-use server
  mid-session, and the next agent turn loses local routing.
- **Evidence:** `local_llm_registry.py:232-242` (probe-and-`clear_local_llm()`
  inside the getter), `_probe_llama_server` `timeout=1.0` at
  `local_llm_registry.py:61-73`, single-failure clear with no debounce/retry;
  `chat_routes.py:519-521` set→status self-undo. Introduced by commit
  `51ce67f0e` "Add local target routing for cloud chat".
- **Status:** resolved 2026-07-18 — `get_local_llm_status()` is now a
  side-effect-free snapshot. A failed probe reports `reachable:false` while
  preserving the registered port, model, runtime-catalog entry, and unified
  client; only the explicit desktop lifecycle disconnect clears them. Focused
  registration/probe regression passes.
- **Analysis stamp:** Analyzed 2026-07-15 — verified in code via adversarial
  review of the test-fix for `test_runtime_model_registration`. The
  self-healing intent is right ("loud recovery"), but a single 1.0s probe with
  no grace and a destructive getter is too aggressive.
- **Owner hint:** Split the read from the repair: make `get_local_llm_status()`
  side-effect-free (report `reachable: false` without clearing), and move
  clear-on-unreachable to an explicit reconciler with a grace window / N-retry
  (or only clear after K consecutive failures). Don't let a status poll or the
  connect handshake tear down a cold-but-valid server.

---

## API keys / credentials

### MXL-D-073 — Live `api_key_provider` catalog is missing the `brave` row, so Brave keys are silently unrecognised
- **Area:** `public.catalog_entries` kind `api_key_provider`, app `matrx-local`
  (live rows) vs `app/services/catalogs/compiled_data.py`
  (`COMPILED_API_KEY_PROVIDER_ENTRIES`) / `desktop/src/lib/api-key-patterns.ts`
- **Symptom:** The compiled fallback has **13** rows including `brave`; the live
  catalog serves **12** and has no `brave` row. Because the live catalog
  *replaces* the compiled list once fetched (`refreshApiKeyPatterns()` /
  `get_catalog("api_key_provider")`), the shipped app silently ignores
  `BRAVE_API_KEY` / `BRAVE_SEARCH_API_KEY` in a pasted .env — the exact bug the
  in-file comment at `desktop/src/lib/api-key-patterns.ts:126-128` records as
  having already happened once with Civitai. It also stops the Credential Vault
  tier from matching a Brave key stored under an alias (Arman's own vault has
  one, saved as `BRAVE_SEARCH_API_KEY`).
- **Evidence:** 2026-07-26, live cached catalog (176 entries,
  `fetched_at=2026-07-27T03:52:37Z`): `get_catalog("api_key_provider")` → keys
  `openai, anthropic, google, huggingface, civitai, groq, together, xai,
  cerebras, elevenlabs, fastino, global-strip-lists` — **no `brave`**.
  `COMPILED_API_KEY_PROVIDER_ENTRIES` has 13 including `brave`.
  `tests/characterization/test_catalogs_offline.py:47` asserts 13 against the
  COMPILED set, so no existing test can see this drift.
- **Status:** blocked-external (needs a Supabase write; the MCP identity in the
  discovering session was unauthenticated)
- **Analysis stamp:** Analyzed 2026-07-26 — verified against the live catalog
  cache and the compiled fallback in the same session.
- **Owner hint:** Insert the missing `brave` row into `catalog_entries`
  (`app='matrx-local'`, `kind='api_key_provider'`, key `brave`, payload copied
  from `COMPILED_API_KEY_PROVIDER_ENTRIES`). Then add a LIVE drift check —
  `tests/parity/test_catalogs_live.py` should compare live keys against compiled
  keys per kind and fail on a missing row: "the DB replaces the fallback" makes
  a *smaller* live set a silent feature regression, not a harmless difference.

### MXL-D-066 — `test_api_key_set_and_delete_roundtrip` times out on macOS (pre-existing)
- **Area:** `tests/smoke/test_api_keys.py::test_api_key_set_and_delete_roundtrip`
- **Symptom:** `httpx.ReadTimeout` after ~25s on `PUT /settings/api-keys/{provider}`.
  Reproduced 3/3.
- **Evidence:** **Confirmed pre-existing** — reproduced identically in a clean
  worktree at HEAD `0efd2fb7d` with no working-tree changes, 2026-07-19. Key
  writes go through the macOS keychain helper (`app/common/keychain_helper.py`),
  which spawns a signed subprocess; a locked keychain or a suppressed prompt
  blocks it past the client timeout.
- **Status:** open
- **Analysis stamp:** Analyzed 2026-07-19 — control-tested at clean HEAD, so it
  is NOT caused by the v1.3.145 outage fixes.
- **Owner hint:** Either bound the keychain write with a timeout that surfaces a
  clear error, or make the smoke test skip when the keychain is unavailable.
  A test that hangs on an interactive OS prompt cannot run in CI.

### MXL-D-061 — Filesystem index grows unbounded (3.5 GB on one live machine)
- **Area:** `app/services/filesystem/` — the indexer and its scope policy
- **Symptom:** `~/.matrx/filesystem-index.sqlite3` reached **3.5 GB** (plus a
  5 MB WAL, still being written) on Arman's machine — an effectively
  whole-machine index. No bounding policy, no cap, no user-visible disclosure
  of the disk cost. This ships to every user.
- **Evidence:** `du -sh ~/.matrx/filesystem-index.sqlite3` → 3.5G, 2026-07-19,
  live home. Grew during the ~60 filesystem-indexer commits of 2026-07-18.
- **Status:** open
- **Analysis stamp:** Analyzed 2026-07-19 — size measured directly on the live
  home. NOT implicated in the v1.3.145 engine freeze (the engine was idle at
  0% CPU in the diagnostic snapshot); this is a standalone disk-consumption
  defect.
- **Owner hint:** Needs a bounding policy (scope allowlist, size cap, or
  eviction) plus a settings surface showing the index size with a rebuild/clear
  action. Decide the default scope with Arman before implementing.

### MXL-D-063 — Stale discovery file when the server thread exits on its own
- **Area:** `run.py` `_wait_forever()` (~line 675)
- **Symptom:** `remove_discovery_file()` runs in the signal handler and in the
  `not teardown_clean` branch, but NOT on the clean path taken when
  `_shutdown_event` is set by the server thread exiting by itself (no signal).
  That path leaves `~/.matrx/local.json` behind pointing at a dead port, so
  every client routes to nothing.
- **Evidence:** `run.py:675-690` — removal sits only inside `if not
  teardown_clean:`; the covering calls are `run.py:528, 606, 802, 982`.
- **Status:** open
- **Analysis stamp:** Analyzed 2026-07-19 — narrow exposure (SIGTERM/SIGINT is
  the normal Tauri path and IS covered). A fix placing `remove_discovery_file()`
  in `_wait_forever` was written and then REVERTED: it violates the deliberate
  no-blocking-I/O-around-the-completion-barrier invariant
  (`tests/unit/test_run_shutdown_barrier.py`), and `run.py:770-785` documents
  that the call does blocking I/O (flock'd writes, local.json reads) that can
  wedge shutdown forever.
- **Owner hint:** Fix belongs in the lifespan teardown (where the engine already
  releases its own resources, before the barrier), NOT on the main thread's
  post-barrier path. Do not "just add the call back" to `_wait_forever`.

### MXL-D-051 — Live-position source engine ignores SIGTERM and /admin/shutdown (SIGINT works)
- **Area:** `run.py` / `app/main.py` signal handling under `MATRX_LIVE_ENGINE=1`
- **Symptom:** A `./scripts/dev.sh --live` source engine ignored
  `POST /admin/shutdown` (200 accepted, "self-signal SIGTERM after response
  flushes") and multiple direct `kill -TERM` — first observed during an
  active HF snapshot download, but ALSO reproduced fully idle afterwards, so
  the download is not the trigger. No signal-handler log line ever appeared
  despite the loud-shutdown instrumentation. `kill -INT` shut it down
  immediately. A plain dev-mode engine (`./scripts/dev.sh`, no --live) died
  cleanly from SIGTERM in ~6s the same day.
- **Evidence:** Observed live 2026-07-13/14 (engine v1.3.110, source run,
  live position): 1x /admin/shutdown + 2x SIGTERM ignored mid-download; 1x
  /admin/shutdown + 1x SIGTERM ignored while idle; SIGINT → clean exit
  (left one orphaned quick-tunnel cloudflared child, killed manually).
- **Status:** open
- **Analysis stamp:** Analyzed 2026-07-14 — reproduced live twice; not yet
  root-caused in code. Note the memory/task note "trayless so SIGTERM works"
  (MXL-D-042/043 work) — the live-position path may reintroduce whatever
  swallows SIGTERM.
- **Owner hint:** Hard Rule 0 territory ("app won't quit / ended
  unexpectedly"). Compare signal-handler installation between dev and
  MATRX_LIVE_ENGINE=1 paths in run.py; also verify the engine's SIGTERM
  handler vs uvicorn's, and that /admin/shutdown's self-signal actually
  sends a signal the process has a handler for.

## Downloads (audit 2026-05-04 remainder — see docs/DOWNLOAD_SYSTEM_AUDIT_AND_PLAN.md)

### MXL-D-012 — Download progress freezes / 0% on chunked-encoding sources
- **Area:** both download managers
- **Symptom:** no Content-Length ⇒ percent pinned at 0% for the whole transfer;
  no heartbeat between 60s idle timeouts, so stalls look like hangs. (The Rust
  buffer-accumulation half was removed 2026-07-10 — writes are per-chunk now.)
- **Evidence:** Rust `desktop/src-tauri/src/downloads/manager.rs:1154`
  (`content_length().unwrap_or(0)`), percent fallthrough to 0.0 at `:1225-1232`,
  60s idle timeout at `:1175-1177`; Python `app/services/downloads/manager.py:950`
  (`content-length` default 0), `percent` returns 0.0 whenever `total_bytes <= 0`
  (`:96-99`). Audit doc Phase 2.
- **Status:** open. Analyzed 2026-07-12 — verified in code: fix is an
  indeterminate/bytes-only progress signal (heartbeat on bytes received) when
  total is unknown, not a percent that requires a total.
- **Owner hint:** downloads

### MXL-D-013 — Five downloads bypass the unified manager; dual Rust/Python managers
- **Area:** downloads architecture
- **Symptom:** `transcription/downloader.rs`, `wake_word/models.py`,
  `setup_routes.py:_download_transcription_model`, `setup_routes.py:_download_cloudflared`,
  `app/tools/tools/transfer.py` all self-download, invisible to the global panel; the
  Rust and Python managers keep separate SQLite stores.
- **Evidence:** audit doc Phases 3–4.
- **Status:** open. Unverified — from docs/logs only.
- **Owner hint:** downloads

### MXL-D-014 — Queue panel lacks pending/aggregate state; slots never expand in practice
- **Area:** desktop / downloads UI
- **Symptom:** no active-vs-pending split, aggregate bandwidth, stage badges, or stall
  detection; `should_expand_slots` never fires on source-rate-limited downloads.
- **Evidence:** `DownloadManagerModal.tsx`; `manager.rs::should_expand_slots`; audit
  doc Phases 5–6.
- **Status:** open. Unverified — from docs/logs only.
- **Owner hint:** downloads

## UI / UX correctness

### MXL-D-017 — `transcription_auto_init` setting has no effect
- **Area:** desktop / transcription
- **Symptom:** toggle exists and syncs to cloud, but nothing reads it — auto-init
  runs unconditionally.
- **Evidence:** setting defined/synced at `desktop/src/lib/settings.ts:70,181,643-646,795`,
  toggle at `desktop/src/pages/Configurations.tsx:1296-1297`; ZERO consumers —
  grep of `desktop/src-tauri/src` and `desktop/src` finds no code gating on it.
- **Status:** open. Analyzed 2026-07-12 — verified in code: write-only plumbing;
  fix by gating the transcription auto-init path on the flag.
- **Owner hint:** transcription

### MXL-D-029 — `tools` service degraded in packaged v1.3.105: "0 tools registered"
- **Area:** engine / packaged sidecar
- **Symptom:** live `/admin/status` on the shipped v1.3.105 build reports
  `tools: degraded — 0 tools registered — AI tool calls will fail`.
- **Evidence:** `curl /admin/status` against the running packaged engine
  (2026-07-12); also in `~/.matrx/diagnostics/2026-07-12T14-52-43_ai_engine.json`.
- **Status:** open. **Probably a cascade** of the `replicate` metadata crash
  (fixed 2026-07-12 in `specs/matrx-engine-*.spec` — `ai_engine` failed at
  import, so nothing registered tools). **Must be re-verified against a build
  made AFTER that fix** before spending time on it: run
  `./scripts/smoke.sh packaged` and read the `/admin/status` section. If tools
  is still degraded, it is an independent bug.
- **Owner hint:** engine core

### MXL-D-034 — Duplicated `formatBytes` / `CopyButton` / shell-open across the app

- **Area:** frontend, code health
- **Symptom:** The media canonicalization (v1.3.106) established one
  `formatBytes`/`formatDate` (`components/media/types.ts`), one `CopyButton`
  (`components/media/MediaInfoDialog.tsx`) and one shell-open (`MediaActions
  Provider.showInFolder`). The rest of the app still carries forks that predate
  it and will drift.
- **Evidence:** `formatBytes` — `lib/utils.ts:8`, `components/DownloadProgress.tsx:126`,
  `components/UpdateBanner.tsx:207`, `components/UpdateDialog.tsx:29`,
  `components/downloads/DownloadManagerModal.tsx:40`,
  `components/tools/panels/MonitoringPanel.tsx:52`, `pages/LocalModels.tsx:152`,
  inline in `pages/Settings.tsx:2716`. `CopyButton` — `components/tools/ToolInfoPanel.tsx:38`,
  `components/chat/ChatMessages.tsx:25`, `pages/LocalModels.tsx:206`.
  Direct `import("@tauri-apps/plugin-shell")` — `pages/LocalModels.tsx:167`,
  `pages/Devices.tsx:193`, `components/downloads/DownloadActionDialog.tsx:57`,
  `components/SetupWizard.tsx:609`, `components/PermissionsModal.tsx:449`,
  `hooks/use-permissions.ts:438`, `hooks/use-auth.ts:219`.
- **Status:** open
- **Analysis:** Analyzed 2026-07-12 — verified in code by an adversarial review
  of the media refactor. Out of scope for that change (none of these render
  media); filing rather than expanding the blast radius of a release.
- **Owner hint:** whoever next touches downloads/settings — collapse onto the
  canonical helpers as you go.

### MXL-D-035 — Captured device VIDEO is not canonical media (photos are)

- **Area:** frontend, media
- **Symptom:** In `pages/Devices.tsx` a captured photo/screenshot routes through
  the canonical `MediaThumb` (full screen, info, right-click menu, download),
  but the video branch of the same conditional is still a raw `<video>` plus a
  hand-rolled `<a download>`. Asymmetric: the same capture flow gives you the
  full action set for a still and almost nothing for a clip.
- **Evidence:** `desktop/src/pages/Devices.tsx` — image branch uses `MediaThumb`;
  video branch renders `<video controls src={mediaSrc}>` with its own download
  link (both occurrences, photo and screen-recording).
- **Status:** open
- **Analysis:** Analyzed 2026-07-12 — verified in code. A device capture has no
  engine item id, so it would get the no-itemId capability set (full screen,
  info, download, copy prompt) — still strictly better than today.
- **Owner hint:** small; mirror what the image branch already does.

### MXL-D-072 — Cloud chat options render an invalid nested button

- **Area:** frontend / cloud chat composer
- **Symptom:** React reports a hydration-invalid `<button>` inside `<button>`
  every time the app renders the cloud-chat options control. This can produce
  inconsistent focus/click behavior and pollutes browser error logs.
- **Evidence:** `desktop/src/components/chat/PlusMenu.tsx:262-282` renders a
  native `<button>` as the child of `PopoverTrigger` without `asChild`, so the
  Radix trigger creates another button around it. Reproduced in the browser
  console during Media Generation visual verification on 2026-07-21.
- **Status:** open.
- **Analysis stamp:** Analyzed 2026-07-21 — verified in code and browser logs.
- **Owner hint:** Add `asChild` to `PopoverTrigger`; verify keyboard focus and
  popover open/close behavior in cloud chat.

## Engine lifecycle / loud-failure doctrine (root-caused 2026-07-13 from Arman's "image system freezes" report)

> Investigation summary: the packaged engine NEVER wedges while alive (every
> logged request in every steady session completes in ms, image-gen and
> media-library included). Arman's "everything shows a forever loader until I
> kill the app" state is a LIVE UI over a DEAD/half-dead engine. Log signature
> of every occurrence (system.log, ~12 times Jul 11–12): `WebSocket closed
> normally (1012)` (= uvicorn began shutdown) → ≤1 more heartbeat → total
> silence. FIXED 2026-07-13: MXL-D-036 (silent shutdown), MXL-D-037 (infinite
> drain), MXL-D-040 (protobuf/matrx-ai), MXL-D-041 (access.log) — one-line
> records in .matrx/AGENT_TASKS.md § Completed. MXL-D-038 fixed 2026-07-13 too (EngineDownBanner +
> media-library init-fetch/retry + sentinel normalization). Still open
> below: MXL-D-039 trigger hunt, MXL-D-042.

### MXL-D-039 — Something SIGTERMs the engine mid-session without any Rust graceful path running (trigger still unidentified)
- **Area:** desktop Rust / lifecycle
- **Symptom:** ~12× on Jul 11–12 the engine received a shutdown signal while
  the user was actively working (e.g. Jul 12 21:15:55, seconds after browsing
  the media library) — with NO `/admin/shutdown` POST (so
  `graceful_shutdown_sync` didn't run), no watchdog "parent gone" warning,
  no new engine spawned after. Candidate senders (all direct-SIGTERM paths):
  `stop_sidecar` command (`sigterm_then_kill` with no admin POST),
  `kill_orphaned_sidecars` `pkill -f matrx-engine` from a second app-instance
  launch, the detached shutdown safety net, or the app process dying without
  a crash report. One real app crash was captured the same evening
  (`aimatrx-desktop-2026-07-12-193925.ips`, GGML atexit SIGABRT during
  AEQuit — the known race, still occurring on 1.3.105).
- **Evidence:** system.log freeze signatures above; DiagnosticReports;
  `lib.rs:74` (pkill), stop_sidecar → sigterm_then_kill, safety net.
- **Status:** open — INSTRUMENTATION SHIPPED 2026-07-13: every Rust kill/
  spawn/exit path now writes an attributed line to
  `<log dir>/lifecycle.log` (new `lifecycle_log.rs`), and the Python side
  logs every received signal (MXL-D-036 fix). On the NEXT mid-session death,
  lifecycle.log + system.log together name the killer. Trigger itself still
  unidentified — close this once one more occurrence is attributed.
- **Owner hint:** wait for one occurrence, read
  `~/Library/Logs/MatrxLocal/lifecycle.log`.
- **Live observations 2026-07-13 ~00:49 (during the fix session):** (1) after
  `osascript quit`, the v1.3.107 engine SURVIVED the app quit for ≥30s (the
  orphan bug, pre-fix build); (2) the app was RELAUNCHED by an unidentified
  actor 8s after quitting (parent=launchd, no LaunchAgent, no login item —
  possibly another active agent session on this machine, the Chrome
  extension, or the updater). An app launch at 00:43 with no user action was
  also observed. A phantom relauncher would explain mid-session engine churn;
  the new lifecycle.log will attribute it on the next occurrence.
- **Update 2026-07-13 (MXL-D-043 fix):** one candidate trigger class is now
  eliminated — dev sessions. Pre-fix, ANY `uv run python run.py` or
  `pnpm tauri:dev` shared `~/.matrx` with the installed app; debug-Rust
  `kill_orphaned_sidecars` pkilled packaged engine names (never its own child
  in dev), and dev engines clobbered the live discovery file. Dev world is now
  fully isolated (`~/.matrx-dev`, ports 22240+, sweeps off — CLAUDE.md Hard
  Rule 9). If mid-session deaths STOP recurring after this date, this was the
  trigger; if one recurs, lifecycle.log still attributes it.
- **Live observation 2026-07-14:** `./scripts/smoke.sh packaged` built and
  launched v1.3.114 cleanly, but graceful app termination left both of the
  newly spawned owned children alive: Rust-owned llama-server PID 12101 and
  engine-owned quick-tunnel cloudflared PID 13025. The engine itself exited.
  Harness evidence: `.smoke/runs/20260714-213904/summary.md`. This reproduces
  the orphan side of the lifecycle defect in a fresh packaged build; neither
  process was part of the pre-launch snapshot.

### MXL-D-042 — Standalone (non-sidecar) engine on macOS ignores SIGTERM while the pystray tray is active
- **Area:** run.py / standalone mode
- **Symptom:** with the pystray icon running, the main thread lives inside the
  AppKit run loop, so Python-level signal handlers never execute — `kill -TERM`
  is silently ignored and the engine only dies to SIGKILL. Sidecar mode
  (TAURI_SIDECAR=1, `_wait_forever()` path) is unaffected — verified live
  2026-07-13: sidecar SIGTERM → full loud teardown in ~1s; standalone SIGTERM
  → nothing, process alive 45s later.
- **Evidence:** live test 2026-07-13 on this machine (engine pid 98729,
  standalone, SIGTERM never reached `_handle_exit`); `run.py setup_tray()` →
  `icon.run()` blocks the main thread in ObjC.
- **Status:** open. Analyzed 2026-07-13 — reproduced live.
- **Owner hint:** run `icon.run_detached()` or move the tray to a helper
  thread and keep the main thread in `_shutdown_event.wait()`; dev-only
  severity (end users always run sidecar mode).
- **Update 2026-07-13 (MXL-D-043 fix):** severity further reduced — DEV
  engines now skip the tray entirely (`run.py setup_tray` dev guard) and die
  cleanly on SIGTERM (verified live: 1s teardown). The bug now only affects
  an intentional `MATRX_LIVE_ENGINE=1` standalone run with a tray — rare.

### MXL-D-045 — Notes bulk sync holds the engine-wide lock for the whole reconcile; the first pathless import (~800 web notes) can stall saves/deletes for minutes

- **Symptom:** while `full_sync` runs, every debounced save (`push_note`), delete
  route, and realtime pull queues behind `sync_engine._sync_lock`. Normally the
  reconcile is seconds, but the FIRST full_sync after the 2026-07-13 overhaul
  imports the ~814 pathless web-authored `workbench.notes` rows (one `_pull_note`
  + one conditional file_path write-back each) and can hold the lock for minutes.
  Typing during that window shows "Saving..." until the import finishes; the
  saves are queued, not lost.
- **Evidence:** `app/services/documents/sync_engine.py::full_sync` (single lock
  scope around the whole reconcile incl. the pathless-import pass added
  2026-07-13); every mutating entry point takes the same lock by design
  (SYNC_CONTRACT — the lock IS the consistency mechanism for `.sync/state.json`).
- **Proposed fix:** chunk the pathless-import pass — release and reacquire the
  lock between batches of ~25 notes (state is persisted per `_pull_note`, so
  interleaved saves are safe), or run the import as its own follow-up phase
  after `full_sync` returns. One-time pain either way; fix is about first-run UX.

### MXL-D-053 — Stale heavy build artifacts in desktop/src-tauri may ship dead weight
- **Area:** build / installer size
- **Symptom:** two large stale artifacts sit in the tree and could be swept
  into the bundle: (1) `desktop/src-tauri/binaries/llama-b8377-bin-macos-x64.tar.gz`
  (~93 MB) — a leftover archive at llama version **b8377** while the shipped
  dylibs are **b9076**; (2) `desktop/src-tauri/sidecar/aimatrx-engine-aarch64-apple-darwin`
  (~215 MB) — an old-named flat sidecar binary next to the current
  `Matrx Engine.app`. Active externalBin is `sidecar/matrx-engine` / the `.app`
  (`tauri.conf.json:44`), so the flat binary looks orphaned.
- **Evidence:** file sizes on disk 2026-07-14 (CDN-assets audit,
  `docs/CDN_ASSETS_PLAN.md` § 3).
- **Status:** open. Unverified — needs a real `./scripts/smoke.sh packaged`
  build to confirm whether either is swept into the installer (a `resources`
  glob catching the tarball, or the flat binary being picked up) before
  deleting. If unused, `git rm` both.
- **Owner hint:** whoever does the CDN installer-slimming work — fold into that.

### MXL-D-056 — `scheduler` extra claimed "bundled into release builds" but the sidecar build never syncs it
- **Area:** `pyproject.toml:85` (scheduler-extra comment) vs
  `scripts/build-sidecar.sh:148` (venv sync) and `build-sidecar.sh:412`
  (`--hidden-import matrx_scheduler`)
- **Symptom:** `pyproject.toml` states the `scheduler` extra "is bundled into
  release builds — see scripts/build-sidecar.sh," but `build-sidecar.sh` syncs
  only `--extra transcription`, never `--extra scheduler`. So `matrx-scheduler`
  is absent from the packaged venv and `--hidden-import matrx_scheduler` is a
  warning-level miss in the shipped binary. Currently inert — the Phase 8
  scheduler host is gated off by default (`MATRX_LOCAL_SCHEDULER_ENABLED`) and
  imported lazily — but the moment that host is enabled in a release build it
  would `ModuleNotFoundError` at runtime (packaged-only, exactly the class
  Hard Rule 5/6 exist to prevent), and the doc is misleading today.
- **Evidence:** Surfaced 2026-07-15 by adversarial review of the matrx-* wave
  bump (an aside, not caused by the bump). `build-sidecar.sh:148` syncs
  `--extra transcription` only; no `--extra scheduler` anywhere in the build
  scripts; `matrx-scheduler` has no metadata in the synced venv.
- **Status:** open
- **Analysis stamp:** Analyzed 2026-07-15 — verified in code; runtime impact
  latent (host gated off), doc claim is wrong now.
- **Owner hint:** Either add `--extra scheduler` to the release sync (if the
  host is meant to ship) or correct the `pyproject.toml:85` comment to say it
  is NOT bundled and must be enabled+synced deliberately. Pick one so doc and
  build agree.

## Testing infrastructure

### MXL-D-076 — `Scrape` tool's `get_links` / `get_overview` flags are inert; every scrape always returns the full payload
- **Area:** `app/services/scraper/engine.py:372` (`ScraperEngine.scrape_one`),
  `app/tools/tools/network.py:415` (`tool_scrape` building `LocalScrapeOptions`)
- **Symptom:** `tool_scrape` carefully constructs
  `LocalScrapeOptions(fields=ScrapeOptions(get_links=…, get_overview=…))` and
  `scrape_one` then **ignores `opts.fields` entirely** — it passes only
  `use_proxy` / `request_type` / `cache` / `domain_config` / `browser_pool` to the
  package's `scrape()`, which takes no field options and always computes
  everything. Field selection in the package is a POST-HOC dict filter
  (`apply_field_flags`), and the tool never calls it — only
  `ScraperEngine.result_dict` does. So `get_links=False, get_overview=False` still
  returns `metadata.links` (all 8 buckets) and `metadata.overview`.
- **Evidence:** verified 2026-08-09 by mutation. Forcing
  `get_links=False, get_overview=False` in `tool_scrape` and re-running
  `tests/smoke/test_scraper.py` left both bucket/overview assertions **green** —
  the payload was unchanged. `grep -n apply_field_flags app/` shows its only call
  site is `ScraperEngine.result_dict`, which `tool_scrape` does not use.
- **Impact:** low/correctness-only today — the extra fields are computed anyway by
  the package, so this wastes response bytes rather than breaking a caller. It
  matters because the flags are advertised in the tool schema: a cloud agent that
  sets `get_links=false` to keep a context window small does not get what it asked
  for, and `output_mode="research"` (which routes through the same dead flags to
  mean "prose only") silently does not trim either.
- **Fix:** either route the tool's result through `apply_field_flags` before
  building the metadata dict, or drop the flags from the tool schema and stop
  pretending. One of the two — the current state advertises a knob that does
  nothing.
- **Status:** open — found while adding local-lane smoke coverage
  (`tests/smoke/test_scraper.py`). Those two tests deliberately pin the ORGANIZER's
  output contract rather than the flags, and their docstrings name this defect, so
  fixing it will not silently invalidate them.
- **Owner hint:** whoever next touches `tool_scrape` or `output_mode="research"`.

### MXL-D-077 — `test_wake_word_start_without_device` can exceed the 15s client read timeout under full-suite load
- **Area:** `tests/smoke/test_wake_word.py:91`
- **Symptom:** `POST /wake-word/start` opens a REAL microphone stream; on a busy
  machine macOS device acquisition occasionally takes longer than the session
  client's 15s timeout, and the test dies with `httpx.ReadTimeout: timed out`
  rather than the 200-or-500 it is written to accept.
- **Evidence:** observed once on 2026-08-09 in a full `tests/smoke` run; the same
  file passed in isolation and the full suite passed on the next 3 consecutive
  runs. Same class as MXL-D-044 (load/ordering-sensitive smoke flake), not a
  product bug — the endpoint's own contract already allows a graceful failure.
- **Fix:** give this one test a longer explicit timeout (it is the only smoke test
  that waits on real audio hardware), or treat `ReadTimeout` as an accepted
  outcome alongside 500 with a message naming the device as the cause.
- **Status:** open — low priority, test-only.

## Cross-repo

### MXL-D-059 — ✅ chat_sync BLIND-UPSERTS server-owned cloud rows (already corrupted production data)
- **Area:** chat_sync / cross-repo (shared Supabase `chat.*`)
- **Symptom:** `_push_table` publishes locally-rebuilt rows to the shared cloud DB with an
  **unconditional** PostgREST upsert — no compare-and-swap, no predicate — so the desktop
  overwrites server-authored truth with whatever its local rebuild lost or never had.
  **This is not theoretical: it corrupted live data on 2026-07-18.** A desktop push wrote a
  `chat.message.content` whose `tool_call` block had lost its `call_id` (matrx-ai ≤0.4.18
  `parse_content` dropped it), producing `tool_use.id=""` → Anthropic 400 on **every**
  subsequent turn of that cloud conversation. Repaired server-side via aidream migrations
  0207 (table-level tool-graph trigger) + 0208 (data repair); 3 rows across 3 conversations.
- **Evidence:**
  - `app/services/chat_sync/client.py:150-166` — `Prefer: resolution=merge-duplicates`, no predicate.
  - `app/services/chat_sync/engine.py:53` — `_STRIP_COLUMNS` discards `version`, so the
    optimistic-concurrency column that would make a guarded write possible is deliberately
    removed before the request goes out.
  - `app/services/ai/conversation_handler.py:1127` — `{k: v for k, v in raw.items() if v not in (None, [], {})}`
    drops `arguments:{}` / `content:[]`; `:1120-1124` `__dict__` fallback loses private attrs.
  - `:490-508` — the pulled cloud row is matched and its content REPLACED by the twice-normalized
    local rebuild, then enqueued for push (the closed lossy loop).
  - Contrast `app/services/documents/sync_engine.py:329-366`, which does this CORRECTLY
    (`expected_content_hash` CAS + conflict preservation) and whose own comment calls the
    unconditional upsert a "SYNC_CONTRACT violation."
- **Former highest-severity clobber paths (beyond the fixed content case):**
  1. `conversation.cache_state` / `metadata` / `config` — hardcoded `'{}'` at
     `conversation_handler.py:137-140` + `repositories.py:376-378`; destroys server prompt-cache state.
  2. `user_request` usage/cost — hardcoded zeros at `conversation_handler.py:196-199`, never
     repaired (`:605-618`); silently zeroes server-side token/cost aggregates.
  3. `message.metadata` — replaced wholesale with `{}` at `:492`.
  4. `conversation.message_count` — recomputed at `:622-628` from a locally PARTIAL replica
     (pull caps at 20 pages × 500).
  5. `PUT /messages/{id}` (`data_routes.py:268-276` → `repositories.py:473-474`) flattens a
     structured message to ONE text block, destroying all tool_call blocks, then pushes it.
  6. `_PUSH_ORDER` is an ordering hint, **not an allowlist** (`engine.py:236,249-251`) — any
     `chat.*` table enqueued anywhere starts unconditionally upserting with zero review.
  7. LWW guard compares timestamps **lexicographically as TEXT** (`engine.py:571-574`) — breaks on
     fractional-second width (Postgres trims trailing zeros, local `_now()` never does — this one
     fires constantly), mixed UTC offsets, and the space-form `datetime('now')` stamps written by
     `artifacts/service.py:370,403,470,483`. `_parse_ts` (`engine.py:73-83`) exists and is unused.
- **Status:** fixed 2026-07-18. Desktop writes now use batched
  `resolution=ignore-duplicates` inserts and `(id, version)` conditional PATCHes; every table has
  an explicit desktop-authored column allowlist, and cloud/tool-result ledger fields are excluded.
  A CAS mismatch or conflicting pull stores both local and remote copies in a durable
  `sync_queue.action='conflict'` row instead of overwriting either side. Characterization tests
  pin projection, CAS mismatch, two-copy preservation, parent ordering, echo, and poison-row
  isolation. Live Supabase introspection confirmed `platform._touch_row` increments `version` on
  updates for every writable mirrored table except `chat.media`, which is now insert-only from
  desktop when an existing row differs.
- **Immediate mitigation already live (server side):** aidream migration 0207 installs
  `chat.message_tool_graph_write_guard`, which REJECTS any UPDATE changing a message's `tool_call`
  id multiset. Desktop pushes that would corrupt the tool graph now fail loudly with
  `tool_call_graph_change_forbidden`, retry, then dead-letter — cloud truth is protected, but the
  desktop mirror stays lossy and the other columns above are still unguarded.
- **Follow-up:** expose conflict resolution in the desktop chat UI. The data is safe and visible
  in `/chat/mirror/status`, but users still need `keep_local` / `keep_cloud` controls.

### MXL-D-027 — Voice E2E unconfirmed on physical hardware
- **Area:** desktop / voice
- **Symptom:** full setup → record → transcript with the current Rust pipeline never
  confirmed on a real device (download, load, mic permission, wake word, streaming,
  session persistence).
- **Evidence:** previously root AGENT_TASKS (deleted 2026-07-12).
- **Status:** needs-hw-verification. Unverified — from docs/logs only.
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
- **Status:** blocked-external — only fixable in the Supabase Auth dashboard.
  Prepped ask filed 2026-07-12 at the TOP of `.matrx/ARMAN_TASKS.md`.
- **Owner hint:** platform / Supabase admin (Arman)

### MXL-D-049 — Chat mirror has no reconcile pass; a swallowed outbox enqueue is a permanent silent gap
- **Area:** `app/services/chat_sync/`, `app/services/local_db/outbox.py`
- **Symptom:** `enqueue_change` deliberately never raises (a broken outbox
  must not fail the user-facing write), but unlike notes (daily `full_sync`)
  the chat mirror has NO periodic reconcile that re-derives missing outbox
  entries from mirror state. A swallowed enqueue = that row never pushes,
  with one ERROR log as the only trace. Same gap means locally-committed
  writes torn from their enqueue by a crash (single shared aiosqlite
  connection, `commit=False` batching) are never repaired.
- **Evidence:** `outbox.py::enqueue_change` docstring (updated 2026-07-13);
  adversarial review 2026-07-13 confirmed no reconcile path exists.
- **Status:** open. Candidate: nightly sweep comparing mirror rows
  (`updated_at > last successful push watermark`) against outbox +
  cloud-echo state; or per-row `dirty` column maintained in the same
  statement as the write.
- **Owner hint:** chat_sync

### MXL-D-050 — Sync loops' commits on the shared aiosqlite connection can tear other writers' transactions
- **Area:** `app/services/local_db/database.py` (singleton connection),
  amplified by `app/services/chat_sync/engine.py` per-page/per-queue-row commits
- **Symptom:** every subsystem shares ONE aiosqlite connection; any
  `commit()` commits whatever statements other coroutines have executed so
  far. Multi-statement write flows using `commit=False` batching
  (mirror write + outbox enqueue) are not actually atomic — a sync-loop
  commit interleaved between them persists the first half; a crash in that
  window leaves a mirror change with no outbox entry (see MXL-D-049).
  Pre-existing architecture-wide hazard; chat_sync multiplies commit
  frequency by orders of magnitude.
- **Evidence:** adversarial review 2026-07-13; `database.py` singleton;
  engine commits in `_apply_cloud_echo`/pull pages/queue ops.
- **Status:** open. Candidate: dedicated aiosqlite connection for the sync
  engines, or wrap write+enqueue pairs in explicit BEGIN IMMEDIATE.
- **Owner hint:** local_db / sync spine


### MXL-D-044 — Flaky full-suite ordering: test_runner_drains_a_whole_batch_unattended

- **Found:** 2026-07-13 during Wave-0 verification run
- **Symptom:** `tests/smoke/test_media_gen_batch.py::test_runner_drains_a_whole_batch_unattended`
  fails in a FULL `pytest tests/smoke/` run (a deliberately-injected
  "synthetic crash inside generate" from a sibling test leaks into its
  batch drain), but passes in isolation and passes 17/17 when the file
  runs alone. Order-dependent state bleed through the shared engine
  fixture — same hazard class as the media-gen pytest-fixture
  engine-killer noted in the 2026-07 media-gen overhaul.
- **Evidence:** full-suite run 2026-07-13: `1 failed, 94 passed`; solo
  rerun: `1 passed`; whole-file rerun: `17 passed`.
- **Status:** open — needs the batch-runner fixture to isolate/reset the
  job queue (or the synthetic-crash test to clean up its poisoned job)
  so suite order can't couple them.
- **Owner hint:** media-gen tests

---

## Untriaged legacy backlog (from deleted root `AGENT_TASKS.md`, 2026-07-12)

Not Arman-approved. Not full defect entries yet. Bugs that already had `MXL-D-*`
IDs above were **not** duplicated here. Weekly cleanup should promote, reject, or
expand these into proper entries / agent tasks.

### Gaps / features (were “Important Missing Features”)
- Multimodal image input UI for local LLM — backend ready; missing attach/paste UI, base64 content arrays, mmproj download in DownloadManager.
- Gemma 4 E2B/E4B native audio via llama.cpp — blocked on experimental libmtmd upstream; video input also WIP upstream.
- Chat: “Local” tab routing to llama-server — `Chat.tsx` has no local mode; users must use the LLM page.
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

### Wishlist / future
- ComfyUI sidecar evaluation; cloud AI relay; cloud-assigned scrape job queue.
- Welcome cards → agent IDs + Settings favorites (product design first).
- Proxy full E2E with `MAIN_SERVER` URL (see `.matrx/ARMAN_TASKS.md`).
- Wake word: sherpa-onnx KWS when stable Rust bindings exist.
- Wake-on-LAN / smart home APIs; reverse tunnel; real app icon before public launch.

### Upstream / blocked (was root “Blocked”)
- matrx-ai still assumes server-side paths in places — shipping confidence needs client-only/local paths verified. Much of this moved under matrx-ai 0.3.0 + `.matrx/AGENT_TASKS.md` upstream work; keep only what remains after triage.

_(deleted 2026-07-12: "Image gen: FLUX.1 Dev token gate" — verified obsolete;
FLUX.1-dev is no longer in the model catalog, only two LoRA descriptions
mention it as training lineage.)_

---

## Pending Arman review

_(promotion proposals prepared by non-interactive `/task-hygiene` runs land here — ≤3 per batch)_

**Batch 2026-07-12 — proposed promotions to `.matrx/AGENT_TASKS.md`:**

1. ~~**Fix chat silent agent-null on pick-before-sync**~~ — DONE (verified
   2026-08-06): `ChatPanel.tsx:383-403` now sets `pendingAgentId` +
   `agentSelectionError` banner and blocks send on a miss. MXL-D-016 closed.
2. **Point engine health probes at `/health` + cold-start grace** — P1 —
   replaces MXL-D-018 — Analyzed 2026-07-12, verified in code. Both TS and
   Rust probes hit heavyweight `/tools/list` with 2s timeouts; the cheap
   `/health` endpoint already exists (`app/api/routes.py:85-88`). Kills the
   false "disconnected" UI on slow first boots; near-trivial diff.
3. **Indeterminate download progress when Content-Length is absent** — P2 —
   replaces MXL-D-012 — Analyzed 2026-07-12, verified in code (both
   managers pin percent at 0%). Chunked downloads look frozen/hung to
   users; fix is a bytes-received heartbeat + indeterminate UI state.

---

## Rejected

_One line each: `- MXL-D-### — <short reason> — <date> — delete when: <condition>`.
Check here before filing. Cleanup deletes lines whose condition has occurred._
