# Media artifacts

## Authority

Screenshot tool outputs use the generated Content IR tool-output contract. The
local artifact table is a persistence sidecar, not a second semantic schema.
Absolute local paths must never enter Content IR, chat rows, delegation
payloads, or cloud metadata.

## Execution lanes

- **Local origin:** atomically commit bytes to durable local storage, insert a
  `sync_pending` artifact row, and return immediately. Local models and the
  desktop UI consume those local bytes even with no network, auth session, or
  cloud database.
- **Cloud-delegated origin:** upload directly with the delegated user's JWT and
  return only after a cloud `file_id` exists. If publication fails, retain a
  local recovery copy but fail the delegated tool result; an inaccessible
  local-only reference is never reported to the cloud as success.

The artifact publisher is always on while the engine is running. It is
independent of the optional Files replica setting because conversation media
must become portable when connectivity returns.

## Invariants

1. `artifact_id` is generated once and remains stable after publication.
2. Raw base64 may exist transiently at a model-provider boundary only. It is
   never durable tool output.
3. Cloud URLs are service-issued; clients never construct storage URLs.
4. A cloud-ready artifact carries both `file_id` and `media_ref.file_id`.
5. Failed publication is retryable and never prevents offline local use.
