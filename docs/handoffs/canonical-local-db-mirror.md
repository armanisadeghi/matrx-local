---
status: active
updated: 2026-07-13
repos: [matrx-local, aidream]
owner-context: local SQLite as a structural mirror of the canonical cloud DB; chat-system sync (conversations/messages/media/entities)
---

# Canonical local DB mirror + chat sync — handoff

## Arman's vision (verbatim intent)

"We're supposed to have a cloud based database, which is what all of our
systems use … sync the database with a local database so that the user would
have an identical copy of all of their data that are relevant, including
system data that is not secretive, such as list of AI models … make sure the
system works as well as possible even when they don't have Internet access."

On notes: "Notes is now on our canonical system, which is the EXACT system
that the entire Matrx Local database must be built on so that our local db is
a perfect match of our real db."

On chat: "The 'Chat' system is what we use, but you have to make sure you
don't think it's just chat.conversation — that's the system of about 10+
tables that store chats, including text generation, image, video, audio,
entities."

The docs and schemas already in this repo are NOT the spec — the cloud DB is.
The repo drifted 3–4 months behind the platform; do not trust local docs or
local table shapes over the live Supabase schema (project `txzxabzwovsujtloxrus`).

## Progress log — 2026-07-13 (chat cutover SHIPPED)

- **Mirror infrastructure built**: `schema_mirror/snapshot.json` (checked-in
  introspection of live chat/workbench/ai — and now files — schemas) →
  `scripts/generate_mirror_schema.py` → generated
  `app/services/local_db/mirror_schema.py`. Per-schema SQLite files
  (`~/.matrx/mirror/chat.db`) ATTACHed under the schema name, so local SQL is
  literally `chat.conversation`. Loud drift detection (`--check` +
  runtime ALTER/ERROR in `mirror.py`). The file-sync workstream already
  extended it to `files.files`/`files.folders`.
- **Chat cutover done**: `SQLiteConversationStore` + repos write canonical
  `chat.*` rows; migration V10 copied bespoke data, seeded the outbox with
  all local history, and DROPPED `conversations`/`messages`/`user_requests`/
  `tool_call_logs`.
- **Bidirectional sync live**: `app/services/chat_sync/` — outbox push
  (batched upserts, parent-first, poison isolation, cloud echo-back) +
  incremental keyset pull (per-table `sync_meta` checkpoints, row LWW,
  pending-outbox protection, tombstones only). Managed service `chat_sync`
  (main.py phase 2d); `GET /chat/mirror/status`, `POST /chat/mirror/sync`.
- **RLS verified** (role-simulated drill, all 22 chat tables read+write with
  a plain user JWT): one failure found and fixed — `chat.conversation_value`
  had RLS policies but ZERO table grants (aidream migration
  `0167_conversation_value_grants.sql`, applied live + verified).
- Contract updated: docs/SYNC_CONTRACT.md (gap #1 CLOSED, subsystem #4).
  Tests: `tests/characterization/test_chat_mirror_characterization.py`.
- **Remaining** (see ARMAN_TASKS/questions): live end-to-end airplane-mode
  drill with a signed-in desktop session (stored JWT+refresh were stale on
  this machine); workbench/ai mirror cutovers; media/artifact byte handling
  rides the file-sync handoff; matrx-ai 0.4.0 floor is unpublished so
  `uv sync` fails repo-wide (pre-existing, not this workstream).

## Where things stand (verified live 2026-07-13, pre-cutover)

- Cloud is organized into namespaced schemas. The chat system is 22 tables in
  the `chat` schema: `conversation` (39 cols), `message` (26), `media`,
  `tool_call` (48), `tool_trace`, `user_request` (29), `request`,
  `request_snapshot`, `artifact` (26), `code_edit`, `code_message_file`,
  `agent_memory`, `agent_plan`, `agent_run`, `agent_run_stage`, `agent_task`,
  `conversation_value`, `observational_memory`, `observational_memory_event`,
  `pending_injection`, `user_todo`, `user_usage_summary`. Notes are
  `workbench.notes` / `workbench.note_folders`; models are `ai.model_*`.
- Local SQLite (`~/.matrx/matrx.db`, schema in
  `app/services/local_db/schema.py`) is a BESPOKE shape, not a mirror:
  `conversations` (9 cols) / `messages` (10 cols) vs the canonical 39/26-col
  chat tables; local aux notes tables mirror a graveyarded cloud design.
- What syncs today: models/agents/tools pull-cache
  (`app/services/local_db/sync_engine.py`), settings + instance heartbeat
  (`app/services/cloud_sync/`), notes bidirectional incl. the new
  engine-owned auto-sync loop (2026-07-13,
  `app/services/documents/sync_engine.py start_background_sync`).
- What does NOT sync at all: the entire chat system. Local conversations
  write `server_conversation_id=None` (`app/services/ai/conversation_handler.py:92`);
  the `sync_queue` outbox table has ZERO writers. matrx-ai 0.3.x's
  `SqliteConversationStore` seam is the local write path.

## Priority work queue

### 1. Schema strategy decision → then the mirror
Design the local store as a per-user structural mirror of the canonical
schemas (start with `chat.*`, `workbench.*`, `ai.model_*`): same table names,
same column names, SQLite-compatible types, RLS replaced by "this user's rows
only". Generate the local DDL FROM the live cloud schema (introspection
script, not hand-written) so drift is mechanically detectable. Keep a thin
compatibility layer so existing repos/endpoints keep working during cutover;
then DELETE the bespoke tables (no dual systems — annihilate what's replaced).

### 2. Chat-system sync (both directions)
- Outbound: local turns (conversation/message/tool_call/user_request rows the
  matrx-ai store writes) push to `chat.*` via PostgREST with the user JWT —
  same pattern as notes sync (`documents/supabase_client.py` uses
  Accept-Profile headers per schema). A local turn must appear in the web
  app's history.
- Inbound: pull the user's cloud conversations incrementally (updated_at /
  sync-version checkpoint like `get_notes_since`) so the desktop shows chats
  started on web/mobile — including media/artifact references (pointers, not
  bytes; bytes ride the file-sync handoff).
- Conflict story: chat rows are append-mostly; last-write-wins per column is
  acceptable EXCEPT never lose a message row. Deletions are tombstones.

### 3. Wire it into the existing machinery
One sync spine, not a fourth subsystem: reuse the engine-owned background
loop pattern (`documents/sync_engine.py start_background_sync`) and the
`sync_meta` checkpoint table. Status must surface in `/notes/sync/status`-style
endpoints and the Sync UI. Loud failures — a 404/permission error from the
cloud is an ERROR with the table name, never a silent skip.

### 4. RLS verification before shipping
Every `chat.*` and `workbench.*` table this touches must be verified
RLS-readable/writable with a plain user JWT from the desktop (no service
role — this app never has one). Anything that fails is an aidream-side
policy fix, filed in aidream.

## Contracts

- Supabase project `txzxabzwovsujtloxrus`; user JWT auth only.
- matrx-ai conversation store Protocol (`matrx_ai/client_host/store.py`) is
  the local write interface — do not bypass it.
- Cloud schema changes (if any are needed) follow the shared migration
  ledger rules in CLAUDE.md — applied live + verified in the same session.

## Verification

Airplane-mode drill: full chat + notes read/write offline → reconnect →
everything converges, and a conversation started on the desktop is visible in
the web app (and vice versa) within one sync interval. Pin with
characterization tests against a seeded local mirror.
