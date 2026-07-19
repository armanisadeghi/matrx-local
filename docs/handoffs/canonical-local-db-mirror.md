---
status: active
updated: 2026-07-13
repos: [matrx-local, aidream]
owner-context: local SQLite as a structural mirror of the canonical cloud DB; chat-system sync (conversations/messages/media/entities)
---

# Canonical local DB mirror + chat sync — handoff

## Arman's vision (verbatim intent)

"We're supposed to have a cloud based database … sync the database with a
local database so that the user would have an identical copy of all of their
data that are relevant, including system data that is not secretive … make
sure the system works as well as possible even when they don't have Internet
access." The cloud DB is the spec — never local docs or local table shapes
(project `txzxabzwovsujtloxrus`).

## Done (2026-07-13 — chat system SHIPPED and exercised live)

- **Mirror infrastructure**: checked-in cloud introspection snapshot
  (`schema_mirror/snapshot.json`) → generator
  (`scripts/generate_mirror_schema.py`, `--check` = drift gate) → generated
  DDL (`app/services/local_db/mirror_schema.py`). Per-schema SQLite files
  `~/.matrx/mirror/<schema>.db` ATTACHed under the schema name
  (`app/services/local_db/mirror.py`) so local SQL is literally
  `chat.conversation`; additive drift auto-heals, incompatible drift screams.
  The file-sync workstream already reuses it (`files.files`/`files.folders`).
- **Chat cutover**: `SQLiteConversationStore`
  (`app/services/ai/conversation_handler.py`) + `ConversationsRepo`/
  `MessagesRepo` (`app/services/local_db/repositories.py`) write canonical
  `chat.*` rows; migrations V10 (copy + guarded outbox seed) and V12 (drop)
  in `app/services/local_db/schema.py` annihilated the bespoke
  conversations/messages/user_requests/tool_call_logs tables. Local ids ARE
  the canonical cloud ids; legacy `server_conversation_id` preserved in
  `conversation.metadata.legacy_server_conversation_id`.
- **Bidirectional sync engine**: `app/services/chat_sync/engine.py` —
  pull-BEFORE-push cycles; incremental keyset pull per table
  (`(updated_at|created_at, pk)`, checkpoints in `sync_meta` keyed
  `chat.<table>`, cursor JSON in `last_hash`); outbox drain as batched
  insert-ignore plus version-conditional PATCHes (parents first, `client.py`
  with `Content-Profile: chat`); explicit desktop-authoring column allowlists;
  durable two-copy conflicts instead of stale overwrites; atomic in-SQL
  pull/echo guards; dead-letter lane (`sync_queue.action='dead'`) for
  non-UUID ids, other-user rows, and permanent 4xx after 5 attempts;
  cloud-stamped echoes written back without re-enqueue. Managed service
  `chat_sync` (main.py Phase 2d + shutdown); `GET /chat/mirror/status`,
  `POST /chat/mirror/sync`.
- **RLS verified for all 22 chat tables** (role-simulated rolled-back
  transaction drill, plain user claims). Cloud fixes shipped in aidream,
  applied live + verified: `0167` (chat.conversation_value had policies but
  ZERO grants), `0168`/`0169` (updated_at + `_touch_row` for the 8 chat
  tables that couldn't propagate updates/tombstones — Arman-approved).
- **MXL-D-046 fixed**: `TokenRepo.is_expired` decodes the JWT `exp` claim
  (stored `expires_at` carried the session expiry while the access token was
  dead — all sync loops 401'd while claiming configured).
- **Adversarial review round** (3 independent reviewers): all confirmed
  findings fixed — see commit `d41f77399` for the full list (cloud-clobber
  ordering, echo TOCTOU, V10 crash-atomicity, web-conversation history
  duplication, tool-call created_at clobber, poison-queue starvation,
  conversation delete not tombstoning messages, migration rollback).
- **Docs/tests**: SYNC_CONTRACT.md rewritten (gap #1 CLOSED, subsystem #4,
  canonical-mirror doctrine); 8-test characterization suite
  (`tests/characterization/test_chat_mirror_characterization.py`) + smoke
  repoint; 257 characterization/parity tests green.

### Verification — what was actually EXERCISED (not just unit-tested)

- **Live end-to-end sync drill (2026-07-13, Arman-green-lit)**: real engine
  code against the REAL `~/.matrx/matrx.db` and REAL cloud with the user's
  JWT. First cycle pulled 23,327 rows / 51 pages in ~130s (mirror now holds
  5,864 conversations + 19,253 messages = the full cloud history); second
  cycle converged to 0 (idempotent); all 22 tables `success`; outbox fully
  drained, 0 dead letters. Outbound verified in the cloud: all 7
  desktop-local conversations exist in `chat.conversation` with
  `source_app='matrx_local'`.
- Real engine boot (`run.py`) on a copied live DB: V10/V12 migration,
  mirror attach, `chat_sync` service start, loud 401 idling — all correct.
- RLS write drill: INSERT/UPDATE/tombstone on conversation/user_request/
  message/tool_call as `authenticated` with the user's uid (rolled back).
- Unit/characterization only (NOT exercised end-to-end): dead-letter lane,
  mid-push-edit echo protection, poison-row isolation, LWW conflict paths.

- **MXL-D-052 fixed (2026-07-13): AI/tool messages now actually reach the
  cloud.** The store wrote `source='model'` for every non-user role, but the
  canonical vocabulary (aidream `db/models/chat.py` `Message.source`
  default `'user'` + live `cx_message_source_check`) is
  user/agent_template/system — `source` records the row's ORIGIN, `role`
  carries authorship (cloud has 11.6k role=assistant/source=user rows). So
  every assistant/tool push 400'd and dead-lettered: synced conversations
  held only the user's turns. Fixed all three write sites to `'user'`
  (`MessagesRepo.create`, `conversation_handler.persist_completed_request`,
  the V10 cutover CASE) and added migration V13 (remap existing rows,
  resurrect dead-letters, re-enqueue with the V10 UUID/legacy-conversation
  guards). Exercised live: dev-world module-level drill — V13 remapped the
  7 poisoned rows, push sent 7/7 (0 failed), rows verified in live
  `chat.message` with source='user' and roles intact; outbox empty. Pinned
  by 2 tests in `tests/characterization/test_chat_mirror_characterization.py`.

## Remaining work (ordered)

1. **Per-user mirror partitioning (Arman-approved: "per user! yes")** —
   move mirror files to `~/.matrx/mirror/<user_id>/<schema>.db` (attach at
   token-config time, re-attach on account switch), partition `sync_queue`
   by user or wipe it on switch, and make `ConversationsRepo.list_all`
   user-scoped. Until then the dead-letter guard in
   `engine.py::_push_table` prevents cross-account pushes but B still SEES
   A's rows locally. This is the next gate for multi-account safety.
2. **UI airplane-mode drill** — the data layer round-trips (verified above),
   but nobody has exercised the desktop Chat UI offline→reconnect, and the
   desktop chat UI still persists to localStorage rather than reading the
   mirror (`desktop/src/hooks/use-chat.ts`). Decide whether the UI should
   read `/data/conversations` (mirror) — probably yes as part of making
   web-started chats visible on desktop.
3. **workbench.* cutover** — notes currently run on bespoke tables + their
   own (working, recently stabilized) sync engine. Arman: "I trust your
   judgement" → recommendation: let notes soak 1–2 weeks, then re-platform
   onto the mirror using the chat pattern. Snapshot already contains the
   schema.
4. **ai.* cutover** — replace the aidream pull-cache (`ai_models` bespoke
   table) with mirror pulls of `ai.model_public`/`model_config` (views —
   generator needs `include_views: True` for ai, or materialize the base
   tables). Low risk, do before workbench.
5. **Chat reconcile backstop (MXL-D-049)** — nightly sweep that re-derives
   missing outbox entries from mirror state (a swallowed enqueue or
   commit-tear currently means that row never pushes; ERROR log is the only
   trace).
6. **Shared-connection commit tearing (MXL-D-050)** — give the sync engines
   their own aiosqlite connection or wrap write+enqueue pairs in
   BEGIN IMMEDIATE. Pre-existing hazard, amplified by chat_sync commit
   frequency.
7. **Realtime channel** — chat sync is interval-based (300s,
   `MATRX_CHAT_SYNC_INTERVAL`). Notes use Supabase Realtime; chat should
   eventually subscribe to `chat.*` changes for sub-second convergence.
8. **Frontend `expires_at` writer** — MXL-D-046 is fixed engine-side (JWT
   exp decoded), but the React app still writes the session expiry into
   `auth_tokens.expires_at` (`POST /auth/token` payload); fix the writer so
   the column stops lying.
9. **Known accepted limits (documented in SYNC_CONTRACT.md, revisit if they
   bite)**: field-clears (set-to-NULL) never propagate (push omits nulls);
   `chat.media` has no version column and is therefore insert-only from the
   desktop; durable `sync_queue.action='conflict'` rows preserve both copies
   but still need a user-facing resolver; message content is
   flattened to text parts locally (`_normalize_message`) — rich parts
   (images etc.) survive only as JSON-dumped text; `chat.request` rows are
   not produced by a client host (user_request↔conversation link rides
   `user_request.metadata.conversation_id`).

### Workarounds to delete later

- `_conv_to_compat`/`_msg_to_compat` compat shapes in `repositories.py`:
  delete when the desktop UI reads canonical rows directly (Remaining #2).
- `metadata.legacy_server_conversation_id`: delete after confirming no
  consumer resolves old aidream server-conversation ids (it exists only so
  the historical mapping isn't destroyed).
- The V10 UUID-shape SQL heuristics duplicate `engine._looks_like_uuid`:
  fine to leave (migration code is frozen), noted for awareness.

## Contracts

- Supabase project `txzxabzwovsujtloxrus`; user JWT auth only, never a
  service role. PostgREST profile headers select the schema.
- matrx-ai conversation store Protocol (`matrx_ai/client_host/store.py`) is
  the local write interface — do not bypass it.
- Cloud schema changes follow the shared migration ledger (applied live +
  verified in the same session). This workstream shipped aidream
  0167/0168/0169 that way.
- Snapshot refresh procedure: `schema_mirror/README.md`.
