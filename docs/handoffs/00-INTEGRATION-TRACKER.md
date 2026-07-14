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
| W1 | matrx-ai client-host completion | `aidream/docs/handoffs/matrx-ai-client-host-completion.md` | ✅ Code DONE + wound down 2026-07-14; 0.4.0 on PyPI (wheel verified to include the review fixes); live route exercised (115 rows, 67 w/ required lists); handoff groomed w/ honest verification ledger | ① Coordinator: push aidream `main` (W1 commits local-only there; ahead ~19/behind 2 — rebase+push, coordinate first) ② live boot verification (blocked on W2's dirty worktree) ③ delete W2's re-grown `install_client_host_queue_guard()` when W2 lands |
| W2 | Canonical local DB mirror + chat sync | `docs/handoffs/canonical-local-db-mirror.md` | 🔄 Chat cutover SHIPPED (mirror infra, V10 migration, outbox+pull, RLS drill); **session still live** (dirty: chat_sync/, local_db/, chat_routes) | Airplane-mode drill with a signed-in session; workbench/ai mirror cutovers; fix `test_ai_client_host` "no such table: conversations" |
| W3 | File sync (full + pointer modes) | `docs/handoffs/file-sync-system.md` | 🔄 Built end-to-end (engine, index, hydration into tools, Configurations UI, 21 tests); groomed handoff says deploy matrx-files 0.1.4 + live drill + first-run prompt remain | matrx-files 0.1.4 deployed server-side; live two-mode drill; first-run prompt |
| W4 | OpenAI-compatible local endpoint | `docs/handoffs/local-openai-endpoint.md` | ✅ Code shipped; focused `/v1` smoke 18/18; live engine/tunnel SDK drill not yet exercised | Second-device OpenAI SDK drill through tunnel with real Supabase JWT + running local llama-server |
| W5 | GLiNER local NER | `docs/handoffs/gliner-local-ner.md` | ✅ Code + cloud registry DONE (`0f4d1c729`, `927b75a17`); handoff groomed | Real GLiNER2-base drill: install NER runtime, download model, run `/ner/extract` + tool invocation |
| W6 | matrx-extend health schema | (matrx-extend repo) | ✅ DONE, verified by coordinator (`status` literal kept; optional `health` field added) | — |
| W7 | **Actions / delegation / tool bundles alignment** | `docs/handoffs/actions-delegation-bundles.md` | 📋 Handoff WRITTEN (research done: delegation engine already recognizes matrx-local; blocker is the missing `ui.ui_surface` row + no capability/loader + flat 115-tool registry) | Dispatch to an agent (or Arman review first) — step 1 (seed surface row) unblocks everything |

## Standing gates & environment facts

- aidream `main` is ahead 18 / behind 2 with other live sessions — coordinate
  before pushing main there; the v0.4.0 tag was pushed standalone.
- matrx-local `main` local-only commits pile up fast with multiple agents —
  push after each coherent workstream lands (protect the work).
- FLUX.1-schnell: gated on HF **per-account**; the app's gentle-card flow is
  the correct per-user UX. CDN-mirroring Apache-2.0 weights is the zero-
  friction path — candidate follow-up under W3/downloads.
- Old pre-actionable-failure download rows still show raw red error strings
  in status logs — purge/re-render legacy rows (small task, unassigned).

## Collisions

- W5 handoff work touched only `docs/handoffs/gliner-local-ner.md` and this tracker row. Current working tree contains unrelated active W2/W3-style edits (`app/services/file_sync/`, local DB mirror/schema files, cloud/settings files, desktop files); do not sweep those dirty files into W5 commits.
- W1 wind-down touched only `aidream/docs/handoffs/matrx-ai-client-host-completion.md` + this tracker row. W1's remaining engine-side deletion (`install_client_host_queue_guard()` in `app/services/ai/engine.py`) lives inside W2's DIRTY worktree — do NOT delete it from outside; it is W2's to remove when their session lands (it is redundant-but-harmless with matrx-ai 0.4.0).
- W4 touched `app/main.py` only for `/v1` request-body log redaction. The current checkout also has unrelated NER router edits in `app/main.py`; keep those hunks with W5/NER and do not conflate them with W4 handoff commits.

## Coordination rules (for every workstream agent)

1. When you stop (done OR blocked), groom YOUR handoff doc (progress log +
   remaining work) and update YOUR ROW here — status + next gate, one line.
2. Never touch another workstream's dirty files; note collisions here under
   a `## Collisions` heading instead.
3. Verified means EXERCISED: a booted engine, a real request, a live DB row —
   never just a green typecheck.
4. Anything only Arman can do goes to `.matrx/ARMAN_TASKS.md` — and check the
   key-store rule above before asking anything about tokens.
