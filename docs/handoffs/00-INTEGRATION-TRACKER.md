---
status: active
updated: 2026-07-14
repos: [matrx-local, aidream, matrx-extend]
owner-context: MASTER TRACKER for the 2026-07 full-integration push — every workstream, its status, and its next gate. Managed by the coordinating agent; workstream agents UPDATE THEIR ROW + progress log when they stop.
---

# Integration push — master tracker

**The goal (Arman, verbatim):** "It's our portal to the user's computer for
everything — private, confidential data such as chat they don't want leaving
their computer, private files, private data, disk access, desktop access,
local models, local codebase." Local-first; cloud is fallback. No red errors
for user-fixable states — gentle prompts with a button. User secrets live in
the app's key store (Settings → API Keys), NEVER `.env`.

**Non-goals right now:** end-user releases, SMTP, RLS hardening — there are
no external users yet. Everything aims at: make the system actually work.

## Workstreams

| # | Workstream | Handoff doc | Status 2026-07-14 | Next gate |
|---|---|---|---|---|
| W0 | Wave-0 revival (deps, broadcast, notes auto-sync, boot order, llm race, health, file tools) | — (done; see AGENT_TASKS Completed) | ✅ DONE, pushed `93bb5d57d..18d827e12` | — |
| W1 | matrx-ai client-host completion | `aidream/docs/handoffs/matrx-ai-client-host-completion.md` | ✅ LIVE BOOT DRILL RUN 2026-07-14: `loaded 115 tools from the server registry ✓` + real `/ai/chat` turn ran `local_system_info` end-to-end (Darwin/arm64), zero DBNotConfiguredError on the request path. Drill-blocker FIXED: host executors now register BEFORE the server registry load (`app/services/ai/engine.py`) — the old order left all 115 tools pre-flight-dropped as unrunnable. One residue: matrx-ai's delegated-call expiry sweep still logs DBNotConfiguredError on client hosts (filed in aidream FOUND_DEFECTS 2026-07-14) | ① matrx-ai upstream: store-guard the expiry sweep (last DBNotConfiguredError source) ② Coordinator: push aidream `main` (W1+drill doc commits local-only) ③ delete W2's re-grown `install_client_host_queue_guard()` when W2 lands |
| W2 | Canonical local DB mirror + chat sync | `docs/handoffs/canonical-local-db-mirror.md` | ✅ Chat system DONE + EXERCISED LIVE (bidirectional round-trip verified: 23k rows pulled, 7 local convs in cloud; adversarial round fixed; aidream 0167/0168/0169 applied live; MXL-D-046 fixed; all W2 files committed). MXL-D-052 FIXED 2026-07-13: local store wrote `source='model'` for AI/tool turns (canonical = `'user'`; `source` is origin, `role` is authorship) so every assistant message 400'd on push — 3 write sites fixed + V13 repair migration; live drill pushed all 7 previously-rejected rows (7/7 sent, verified in cloud), 2 test pins | Per-user mirror partitioning (Arman-approved) — see handoff Remaining #1 |
| W3 | File sync (full + pointer modes) | `docs/handoffs/file-sync-system.md` | ✅ Code DONE + wound down 2026-07-14: 37-agent adversarial review, 30 confirmed findings fixed (`56340714e`), 35+14 tests green, E2E DRILL PASSED (isolated engine + locally-run service vs LIVE cloud: bootstrap/hydration/upload/tombstone round-trip); honest verification ledger in the handoff | Arman: deploy matrx-files 0.1.4 — coordinator CONFIRMED the tag push is permission-blocked for all agents (2026-07-14), so the two terminal commands in ARMAN_TASKS are genuinely yours; then prod-engine drill + first-run prompt |
| W4 | OpenAI-compatible local endpoint | `docs/handoffs/local-openai-endpoint.md` | ✅ LOOPBACK SDK DRILL PASSED 2026-07-14 (stock openai SDK + real Supabase JWT vs booted dev engine): models ✓, real Kokoro speech ✓, real Whisper transcription ✓ (TTS wav round-tripped verbatim), real fastembed embeddings ✓, auth envelope ✓ (401 no-token; loopback presence posture by design). Chat = clean 503 (no llama-server on machine). Found: SDK default `encoding_format=base64` 400s on `/v1/embeddings` (handoff item 3a) | Tunnel drill (live engine was DOWN 2026-07-14 — no `~/.matrx/local.json`) + real llama-server chat streaming + wrong-owner JWT 403 |
| W5 | GLiNER local NER | `docs/handoffs/gliner-local-ner.md` | ✅ Code + cloud registry DONE (`0f4d1c729`, `927b75a17`); handoff groomed | Real GLiNER2-base drill: install NER runtime, download model, run `/ner/extract` + tool invocation |
| W6 | matrx-extend health schema | (matrx-extend repo) | ✅ DONE, verified by coordinator (`status` literal kept; optional `health` field added) | — |
| W7 | **Actions / delegation / tool bundles alignment** | `docs/handoffs/actions-delegation-bundles.md` | 🟡 Steps 1-4 DONE + EXERCISED 2026-07-14: surface seeded (aidream 0170), 115→19 mega-tools LIVE + drift-clean (matrx-local `b8c936098`/`e87a21114` pushed), desktop-native capability + load_desktop_tools in matrx-ai (aidream local `f99caf82b`, NOT released), 9 bundles + surface defaults live (aidream 0171). Steps 5-6 (suspend/resume client half, acceptance drill) NOT started | ① matrx-ai release >0.4.0 with desktop-native (then server deploy + matrx-local floor bump; observe drift gate green on boot) ② next agent: step 5 per handoff (open design question: how a headless desktop learns of a suspended call) |
| W8 | **Errors→Prompts UX (downloads)** | — (done; see AGENT_TASKS Completed, MXL-D-047 line) | ✅ DONE + EXERCISED LIVE 2026-07-13: every HF call attaches the app-stored token at request time (raw GGUF path, model_repo analysis, NER; snapshot path already did); HF/Civitai auth refusals → `DownloadResolution` states with exact attribution (token-present gated 401 → license/pending, NEVER "re-enter your token"); actionable failures log INFO `[action-needed]` (no red ERROR, engine + client), STATE log splits `action_needed` from `fails`; stale pre-taxonomy rows re-triaged in `_load_history` (verified on Arman's real store: FLUX→`hf_gate_not_accepted`, Z-Image→`ai_packages_missing`, Civitai blanket copy→key flows); UI: first-class "Needs your action" prompt cards atop the Downloads panel (action button + "Check again & retry"), DownloadActionDialog annihilated; FLUX.1-schnell re-trigger downloaded AUTHENTICATED with his stored token; 20 unit tests (`tests/unit/test_hf_token_and_failure_states.py`); commits `98de4438d`, `2d0ba7db8`, `42802d1d0` | Remaining: MXL-D-051 (--live source engine ignores SIGTERM//admin/shutdown even idle, SIGINT works — reproduced live, filed); optional: extend the same state-not-error doctrine to non-download surfaces (`/image-gen/download` pre-check 400 is plain text, not a resolution) |
| W8 | **Errors→Prompts UX (notes access)** | — (done; commits `bfb14e5d2`, `d3ab1a8d2`, `c780f41aa`, `d039e8067`) | ✅ DONE + EXERCISED on a source-run engine 2026-07-13: the owner's "notes never tells me to grant access" bug fixed end-to-end. Engine: `notes_access_guard` carries kind (`permission`/`missing_dir`) + platform-appropriate reason; new `GET /notes/access` + `POST /notes/access/recheck` (active probe; `create_dir` = Create-folder action); found+fixed live: unguarded `.exists()` stat in `list_conflicts` made `GET /notes/sync/status` a raw 500 while degraded (same latent stat in `list_folders`/`load_local_mappings`). UI: `NotesAccessPrompt` full-page state on Documents — plain-English FDA explanation, System Settings deep-link via the existing permissions system, "Check again" + 10 s auto-poll that dismisses itself and reloads on grant, cross-platform reasons, no restart needed. Verified: chmod-000 drill (degrade → all notes endpoints 200, ONE warn, registry degraded → restore → recheck flips registry back to ready); 12 pytest pins (`tests/unit/test_notes_access_state.py`) + 3 vitest render pins; desktop tsc clean | In-app visual pass on Arman's machine (real FDA revoke/grant in System Settings) — agent automation stops at the sign-in gate |

## Standing gates & environment facts

- aidream `main` is ahead 18 / behind 2 with other live sessions — coordinate
  before pushing main there; the v0.4.0 tag was pushed standalone.
- matrx-local `main` local-only commits pile up fast with multiple agents —
  push after each coherent workstream lands (protect the work).
- FLUX.1-schnell: gated on HF **per-account**; the app's gentle-card flow is
  the correct per-user UX. CDN-mirroring Apache-2.0 weights is the zero-
  friction path — candidate follow-up under W3/downloads.
- ~~Old pre-actionable-failure download rows show raw red error strings~~ —
  DONE (W8, 2026-07-13): stale rows are re-triaged onto the resolution
  taxonomy at startup and render as prompts.

## Collisions

- W3 wind-down committed only file-sync paths (`app/services/file_sync/`,
  `app/api/file_sync_routes.py`, its `app/main.py` Phase-2e hunk, mirror
  snapshot/generator, V11 in `local_db/schema.py`, desktop files, tests,
  handoff+tracker+ARMAN_TASKS). The working tree still holds W2's live edits
  (`chat_sync/`, `local_db/`, `chat_routes`, `settings_sync.py` proxy-port
  change) — untouched. W3's aidream commits (`40893249f`, `fd4053cc7`, + two
  doc commits) sit on aidream local main behind W1's unpushed wave; the
  standalone tag `matrx-files/v0.1.4` avoids needing an aidream main push to
  deploy.
- W5 handoff work touched only `docs/handoffs/gliner-local-ner.md` and this tracker row. Current working tree contains unrelated active W2/W3-style edits (`app/services/file_sync/`, local DB mirror/schema files, cloud/settings files, desktop files); do not sweep those dirty files into W5 commits.
- W1 wind-down touched only `aidream/docs/handoffs/matrx-ai-client-host-completion.md` + this tracker row. W1's remaining engine-side deletion (`install_client_host_queue_guard()` in `app/services/ai/engine.py`) lives inside W2's DIRTY worktree — do NOT delete it from outside; it is W2's to remove when their session lands (it is redundant-but-harmless with matrx-ai 0.4.0).
- W7 (2026-07-14) committed only its own paths: matrx-local `app/tools/*` (+actions.py), `app/services/ai/local_tool_bridge.py`, one line in `app/api/chat_routes.py`, tool tests/snapshot, handoff+tracker; aidream LOCAL commits `a9ac784dc`/`f99caf82b`/`fc46f94fb` (migrations 0170/0171 + packages/matrx-ai desktop-native) — aidream main still not pushed (coordinate before pushing; the matrx-ai module needs a release, not just a push). W7 did NOT touch the concurrent media-gen or downloads/notes files dirty in the tree.
- W4 touched `app/main.py` only for `/v1` request-body log redaction. The current checkout also has unrelated NER router edits in `app/main.py`; keep those hunks with W5/NER and do not conflate them with W4 handoff commits.

- W1/W4 verification drills (2026-07-14) committed ONLY `app/services/ai/engine.py`
  (phase-order fix), `FOUND_DEFECTS.md` (MXL-D-052), this tracker, and the two
  handoff docs (W4 here, W1 in aidream). The worktree's in-flight prompt-matrix /
  cloud-sync / desktop edits belong to other live sessions — untouched. NOTE for
  those sessions: during the drills another agent's engine was running on dev port
  22240 with a FRESH temp home, and my first dev engine received an external
  SIGTERM (suspect: the pytest fixture engine-killer noted in memory) — shared dev
  world is contested right now; bind-order means your engine may not be on 22240.

## Coordination rules (for every workstream agent)

1. When you stop (done OR blocked), groom YOUR handoff doc (progress log +
   remaining work) and update YOUR ROW here — status + next gate, one line.
2. Never touch another workstream's dirty files; note collisions here under
   a `## Collisions` heading instead.
3. Verified means EXERCISED: a booted engine, a real request, a live DB row —
   never just a green typecheck.
4. Anything only Arman can do goes to `.matrx/ARMAN_TASKS.md` — and check the
   key-store rule above before asking anything about tokens.
