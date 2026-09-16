# Chat mirror sync

`ChatSyncEngine` moves locally-authored `chat.*` mirror rows to Supabase and
pulls cloud changes back into SQLite.

## Outbound boundary

- Parents push before children.
- Desktop-authored per-iteration `chat.request` rows push after their
  conversation/user-request parents and before tool-call children.
- Every outbound organization-scoped row carries `organization_id` before
  HTTP. Conversations preserve the organization supplied by the initiating
  request; `user_request`, `message`, `request`, `tool_call`, `media`, and
  `artifact` copy it from the authoritatively loaded conversation row.
- A missing conversation parent or missing/invalid conversation organization
  is immediately and loudly dead-lettered. The database is never asked to
  infer tenant identity from the actor or parent.
- Actor/version columns remain cloud-owned.
- Legacy queued conversations using `visibility=private` are loudly normalized
  to the live enum value `personal`, so an old poison row can recover without a
  local database rewrite.
- Credential-bearing metadata keys are recursively removed immediately before
  network transmission. This protects both new writes and already-queued rows.
- Payload failures remain visible through logs, sync status, the retry queue,
  and the high-severity stream warning emitted by `local_ai_task`.

## Additive-column recovery

Existing populated chat mirrors can predate additive cloud columns.  Structural
reconciliation records a JSON marker in `chat._mirror_meta` before it adds
those columns; a separate keyset cursor makes recovery resumable without
changing the normal pull cursor.  Fresh empty tables receive no marker.

The one-time SR-10 bootstrap marks the historically dropped fields:
`agent_plan.deleted_at`, `agent_task.deleted_at`, `code_edit.deleted_at`,
`code_message_file.deleted_at`, `observational_memory_event.deleted_at`,
`pending_injection.deleted_at`, `request_snapshot.deleted_at`, `pinned_at`,
`pin_reason`, `agent_definition_version`, `workflow_definition_version`,
`tool_trace.deleted_at`, and `user_todo.deleted_at`. Its revision key prevents
a completed bootstrap from being scheduled again. Owner-only `coding_session`
ledgers are not mirror tables and are excluded.

On normal bounded pull cycles, marked rows hydrate only the decoded marked
columns.  An explicit cloud `NULL` is a value and is applied.  A response that
omits a marked field leaves the marker pending, reports the schema mismatch,
and restarts from the beginning on the next cycle.  A queued local mutation
blocks the SQL update atomically and restarts recovery after the queue drains;
a locally newer row is left to normal sync and does not keep recovery pending.
The marker clears only after a complete pass with no deferred row or missing
field.

## Changelog

- 2026-08-23 — chat mirror writes now carry explicit conversation-scoped
  organization identity for conversations and all six child payload families;
  missing provenance dead-letters before HTTP.
- 2026-09-16 — existing populated chat mirrors bootstrap a resumable SR-10
  additive-column hydration pass.  It preserves queued/local-newer work and
  only changes the marked cloud columns.
