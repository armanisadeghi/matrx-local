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

## Changelog

- 2026-08-23 — chat mirror writes now carry explicit conversation-scoped
  organization identity for conversations and all six child payload families;
  missing provenance dead-letters before HTTP.
