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

### MXL-D-043 — No dev/live isolation: a dev-run engine hijacks `~/.matrx/local.json` and shares all state with the installed app
- **Area:** run.py / preflight / dev workflow (likely the MXL-D-039 trigger class)
- **Symptom:** any `uv run python run.py` (Arman or ANY coding agent) uses the
  same `~/.matrx` as the installed app: same discovery file, same `matrx.db`,
  same `settings.json`. `run.py:826` writes the discovery file
  **unconditionally** — even when `assign_engine_port` fell back to 22141
  because the live engine owns 22140. From that moment every discovery-file
  consumer (installed app's Rust `read_engine_port_from_discovery`,
  matrx-extend, cloud round trips) routes to the dev engine running
  uncommitted code. When the dev engine exits, the file points at a corpse and
  the "live app" looks broken. Additionally, cross-instance kill paths
  (`kill_orphaned_sidecars` `pkill -f matrx-engine`, `clean_orphans`) can kill
  the other instance's tree. Net effect: dev testing corrupts live behavior,
  live bugs get reported against code that wasn't at fault, and "fixes" get
  validated against the wrong engine.
- **Evidence:** `run.py:826` (unconditional `write_discovery_file(port)`),
  `app/preflight.py:1073-1089` (second instance allowed, binds 22141, then
  still clobbers the file), `desktop/src-tauri/src/lib.rs:164/976/1073`
  (Rust hardcodes `~/.matrx/local.json`, ignores `MATRX_HOME_DIR`). Matches
  MXL-D-039 live observation "possibly another active agent session".
- **Status:** open. Analyzed 2026-07-13 — verified in code.
- **Owner hint:** two-part fix: (1) dev-by-default isolation — when
  `not getattr(sys, "frozen", False)` and no explicit `MATRX_HOME_DIR`, run.py
  defaults home to `~/.matrx-dev` and port to 22150+, loudly; (2) discovery
  guard — `write_discovery_file` must refuse to clobber a file whose recorded
  pid is alive and answers `/health` at its recorded URL when that pid ≠ self.
  Plus a CLAUDE.md hard rule + wrapper script so agents can't run the engine
  un-isolated.

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
