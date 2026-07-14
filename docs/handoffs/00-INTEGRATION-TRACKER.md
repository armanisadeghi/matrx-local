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
| W1 | matrx-ai client-host completion | `aidream/docs/handoffs/matrx-ai-client-host-completion.md` | ✅ Code DONE; **0.4.0 PUBLISHED to PyPI 2026-07-14** (tag pushed by coordinator, workflow green); matrx-local lock refreshed, AI smoke 6/6 | Live boot verification: log shows server-registry tools (not backfill); one `/ai/chat` turn invoking a local tool; delete the re-grown `install_client_host_queue_guard()` when W2 lands |
| W2 | Canonical local DB mirror + chat sync | `docs/handoffs/canonical-local-db-mirror.md` | 🔄 Chat cutover SHIPPED (mirror infra, V10 migration, outbox+pull, RLS drill); **session still live** (dirty: chat_sync/, local_db/, chat_routes) | Airplane-mode drill with a signed-in session; workbench/ai mirror cutovers; fix `test_ai_client_host` "no such table: conversations" |
| W3 | File sync (full + pointer modes) | `docs/handoffs/file-sync-system.md` | 🔄 Built end-to-end (engine, index, hydration into tools, Configurations UI, 21 tests); groomed handoff says deploy matrx-files 0.1.4 + live drill + first-run prompt remain | matrx-files 0.1.4 deployed server-side; live two-mode drill; first-run prompt |
| W4 | OpenAI-compatible local endpoint | `docs/handoffs/local-openai-endpoint.md` | ✅ Chat + speech + transcription + embeddings `/v1/*` shipped with auth hardening | Live verification from a second device via tunnel with a stock OpenAI SDK |
| W5 | GLiNER local NER | `docs/GLINER_NER_INTEGRATION_PLAN.md` (TASK-001) | ✅ Service + local tools shipped (`0f4d1c729`, `927b75a17`) | Tool-count pins + cloud registry changeset for the new NER tools (verify `tool_sync status` clean) |
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

## Coordination rules (for every workstream agent)

1. When you stop (done OR blocked), groom YOUR handoff doc (progress log +
   remaining work) and update YOUR ROW here — status + next gate, one line.
2. Never touch another workstream's dirty files; note collisions here under
   a `## Collisions` heading instead.
3. Verified means EXERCISED: a booted engine, a real request, a live DB row —
   never just a green typecheck.
4. Anything only Arman can do goes to `.matrx/ARMAN_TASKS.md` — and check the
   key-store rule above before asking anything about tokens.
