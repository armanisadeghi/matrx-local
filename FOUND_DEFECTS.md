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

## AI / local LLM runtime

### MXL-D-058 — Continuation user messages can collide with existing positions and disappear from durable chat history
- **Area:** `app/services/ai/conversation_handler.py`
  `SQLiteConversationStore.reserve_stream_messages()` (lines ~224-303)
- **Symptom:** A continued Cloud Chat turn can run normally while its user
  message is never inserted into `chat.message`. The reservation derives the
  new user position from `len(config.messages) - 1`; when matrx-ai sanitizes
  tool-result/history messages, that length can point at an already occupied
  position. `_insert()` then treats any existing row at that position as
  success without checking its role or content. The following assistant row is
  persisted, leaving durable history with the user's request missing. This can
  make later turns forget what the user asked and makes conversation audits
  incomplete.
- **Evidence:** `conversation_handler.py:238-265` computes positions from the
  incoming list and returns an existing row ID based only on
  `(conversation_id, position)`. In live mirror data inspected 2026-07-18,
  continuation prompts are missing from conversations
  `c63308ad-1607-4a60-9027-dfae7eeb1c0a` and
  `238dd10f-1992-4cc1-b13b-74db35a0f021`; each continuation begins with an
  assistant row even though matching tool-call/trace ledgers prove that a new
  user request ran.
- **Status:** open
- **Analysis stamp:** Analyzed 2026-07-18 — verified in code and live read-only
  mirror data.
- **Owner hint:** Reserve positions from durable conversation state (or a
  transactional position allocator), never sanitized config length. An
  occupied position must be validated for the expected role/content/identity,
  not silently accepted. Add continuation tests with sanitized tool history
  and determine whether missing user rows can be repaired from surviving
  request payloads.

---

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
- **Status:** open — needs-hw-verification (repro needs a real llama-server
  under cold-load / concurrent inference).
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

### MXL-D-048 — Ask-Arman checklist items for user-fixable app config are the wrong pattern
- **Area:** `.matrx/ARMAN_TASKS.md` process / task-hygiene skill, and any
  feature that gates on a missing key/permission
- **Symptom:** Items like "Add your Hugging Face token" or "Grant Full Disk
  Access" were listed in `.matrx/ARMAN_TASKS.md` as things to manually ask
  Arman to go do via a settings page — but the app already has (or should
  have, see MXL-D-046-adjacent "Proactive in-app permission prompts" task in
  `.matrx/AGENT_TASKS.md`) the exact context needed to prompt the user
  gently, in place, the moment a feature needs that config. Turning a
  self-service, in-app, per-user config gap into a person-to-person "ask
  Arman" checklist item is itself the bug — it hides a real UX gap behind a
  manual workaround, and produces stale/wrong asks (see MXL-D-047) when the
  checklist drifts from reality.
- **Rule going forward:** `task-hygiene`/agents must not file "add your X
  key" or "grant Y permission" as an `.matrx/ARMAN_TASKS.md` item UNLESS
  there's a specific reason the app itself cannot prompt for it (e.g. a
  literal external dashboard action like Supabase SMTP). If the app should
  be able to self-serve this, file the missing prompt/UX as an
  `.matrx/AGENT_TASKS.md` bug instead.
- **Reinforced 2026-07-14 (Arman, emphatic):** this covers **OS permissions
  too** (Full Disk Access, **Screen Recording**), not just API keys. The
  distinction is lead-developer-action vs user-action: a user grant is NEVER
  an Arman task. And the required app behavior is stronger than a passive
  banner — an **urgent in-app notification + one-click deep-link to the exact
  grant page**. "Grant Screen Recording" was living in ARMAN_TASKS as exactly
  this anti-pattern; removed 2026-07-14 and folded into the "Proactive in-app
  permission + API-key prompts" task (which now names Screen Recording and the
  notification requirement).
- **Status:** open
- **Analysis stamp:** Unverified — from docs/logs only (process finding, not
  code-verified beyond the two examples in this pass).
- **Owner hint:** fold into the same effort as the "Proactive in-app
  permission prompts" task (`.matrx/AGENT_TASKS.md`) — generalize that
  task's scope from permissions to API keys too, since the failure mode is
  identical (silent degrade / raw error instead of a contextual nudge).

---

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

### MXL-D-016 — Chat: "Agent not found" silent failure after agent pick
- **Area:** desktop / chat
- **Symptom:** agent pick before sync completes silently nulls `activeAgent`;
  next send proceeds agentless with no toast.
- **Evidence:** `desktop/src/components/chat/ChatPanel.tsx:372-384` —
  `all.find(...)` miss → `setActiveAgent(found ?? null)`, no warning path; send
  at `:227` only includes `agentId` when `activeAgent?.id` is truthy.
- **Status:** open. Analyzed 2026-07-12 — verified in code: on miss for a
  non-empty agentId, toast + block/retry against sync completion instead of
  falling through to null.
- **Owner hint:** chat

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

### MXL-D-018 — Health check false-positives on slow engine start
- **Area:** desktop ↔ engine
- **Symptom:** heavy first load flips UI to "disconnected".
- **Evidence:** `desktop/src/lib/api.ts:276-289` (`isHealthy()` → `/tools/list`,
  2s abort); `desktop/src-tauri/src/lib.rs:997-1008` (`check_engine_health`,
  2s client timeout, `Ok(false)` on any error); `discover_engine_port` 1s at
  `:1021-1022`. A cheap static `GET /health` already exists
  (`app/api/routes.py:85-88`, already in `_SILENT_PATHS`).
- **Status:** open. Analyzed 2026-07-12 — verified in code: point both probes
  at `/health` and/or add a cold-start grace timeout.
- **Owner hint:** desktop core

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

### MXL-D-057 — `app.config` ↔ `app.common` import cycle makes import order load-bearing
- **Area:** `app/config.py:5` (imports `app.common.platform_ctx`) ↔
  `app/common/__init__.py` → `fancy_prints.py:4` (imports `app.config`)
- **Symptom:** any module (or test) that imports `app.config`-dependent code
  BEFORE `app.common` crashes with `ImportError: cannot import name
  'LOG_VCPRINT' from partially initialized module 'app.config'`. The engine
  only boots because its entrypoints happen to import `app.common` first.
- **Evidence:** reproduced 2026-07-18 while adding
  `tests/smoke/test_paths_provenance.py` — importing
  `app.services.paths.manager` first fails; the test now carries an explicit
  `import app.common` workaround with a comment.
- **Status:** open
- **Analysis stamp:** Analyzed 2026-07-18 — the cycle only resolves in one
  direction (app.common first). Fix direction: `fancy_prints` should read
  `LOG_VCPRINT` lazily (or from env) instead of importing `app.config` at
  module level, killing the cycle.
- **Owner hint:** whoever next touches `app/common/fancy_prints.py` or
  `app/config.py`; small, self-contained.

## Cross-repo

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
- Dark mode full-app visual pass (modals, dropdowns, third-party wrappers).

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

1. **Fix chat silent agent-null on pick-before-sync** — P1 — replaces
   MXL-D-016 — Analyzed 2026-07-12, verified in code
   (`ChatPanel.tsx:372-384`). A user picks an agent, the pick silently
   evaporates, and the next message runs agentless with zero feedback —
   classic silent-failure the doctrine forbids. Small fix: toast + don't
   null on miss.
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
