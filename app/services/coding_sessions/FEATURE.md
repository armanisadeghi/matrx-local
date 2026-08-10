# Coding Session Bridge — Matrx Local edge

System of record:
`/Users/armanisadeghi/code/common-docs/systems/coding-session-bridge/FEATURE.md`.

## What this edge owns

`POST /coding-session/hooks` is the frozen provider-neutral command-hook
ingress. It accepts the strict adapter envelope v1 with
`action="observe_hook"` for Claude Code, Codex, Cursor, or VS Code. The route
is available without a bearer only on the engine's direct loopback socket and
requires an exact IPv4/IPv6 loopback peer in addition to rejecting every
Cloudflare/tunnel-marked request with 403. A command hook never
receives or stores the user's Supabase JWT.

The success boundary is the local SQLite commit. A successful request returns
HTTP 202 with:

```json
{
  "schema_version": 1,
  "accepted": true,
  "persisted": true,
  "receipt_id": 42,
  "duplicate": false,
  "pending": 1
}
```

The response never claims cloud delivery. `receipt_id` is the durable
`coding_session_bridge_outbox.id` committed before the response is created.
Provider-supplied stable event IDs deduplicate a still-pending exact replay;
reusing one with different content is a 409. Events without a real provider ID
are never given a fabricated stable identity.

## Ordered cloud publication

The engine-owned `CodingSessionBridgeOutbox` task reads the persisted active
user session through `TokenRepo`, obtains the existing app-configured
`AIDreamClient`, and sends the exact stored JSON to authenticated
`POST /api/coding-sessions/bridge`. There is no second token, HTTP client,
daemon, service, or database.

- Rows are attempted strictly by SQLite ID. A deferred or failed head row
  blocks later rows; the bridge never reorders a provider event stream.
- Success means aidream returned 2xx; only then is the local row deleted.
- Network/auth/server failure keeps the exact envelope and records a bounded
  retry delay. If aidream accepted it but the response was lost, restart
  replays the same envelope and server idempotency resolves the duplicate.
- Missing/expired auth and the dev world's disabled cloud participation are
  retryable states, not terminal failures.
- The task is registered as `coding_session_bridge` in the existing launcher
  registry and stops before SQLite closes. It owns no subprocess.

## Raw-ledger boundary

This outbox is not the cloud raw ledger and not part of generic chat mirror
sync. `chat.coding_session` and `chat.coding_session_entry` remain owner-only
in the one platform Postgres. `scripts/generate_mirror_schema.py` explicitly
excludes both tables, and `app/services/chat_sync/engine.py` independently
refuses to pull or push them even if a malformed generated mirror includes
one. Generic schema regeneration can therefore never copy raw commands,
paths, file contents, or secrets into the desktop chat replica.

## Deliberate v1 boundary

Only independently observed hook events enter this route. Managed native
provider sessions will use the same envelope models later, but this slice adds
no Claude, Codex, Cursor, or VS Code SDK dependency and does not advertise
native resume/fork. `models.py` is the exact wire twin of the unreleased
`matrx_ai.coding_sessions.BridgeRequest`; replace the twin with the package
model once that package version is published and consumed here.

## Verification

- `tests/unit/test_coding_session_bridge.py`: strict envelope validation,
  migration, durable ack, stable replay/mutation, missing-ID behavior,
  offline/restart replay, ordered failure recovery, exact upstream path/JWT,
  and future-schema raw-table exclusion.
- `tests/smoke/test_coding_session_bridge.py`: real engine loopback 202,
  tunnel 403, hooks-only gate, and unknown-field rejection.
- `python scripts/generate_mirror_schema.py --check`: generated mirror parity.
