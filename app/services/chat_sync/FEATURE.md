# Chat mirror sync

`ChatSyncEngine` moves locally-authored `chat.*` mirror rows to Supabase and
pulls cloud changes back into SQLite.

## Outbound boundary

- Parents push before children.
- Desktop-authored per-iteration `chat.request` rows push after their
  conversation/user-request parents and before tool-call children.
- `organization_id` is desktop-authored for conversations; actor/version
  columns remain cloud-owned.
- Legacy queued conversations using `visibility=private` are loudly normalized
  to the live enum value `personal`, so an old poison row can recover without a
  local database rewrite.
- Credential-bearing metadata keys are recursively removed immediately before
  network transmission. This protects both new writes and already-queued rows.
- Payload failures remain visible through logs, sync status, the retry queue,
  and the high-severity stream warning emitted by `local_ai_task`.
