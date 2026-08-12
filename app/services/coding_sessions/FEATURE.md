# Coding Session Bridge — Matrx Local edge

System of record:
`/Users/armanisadeghi/code/common-docs/systems/coding-session-bridge/FEATURE.md`.

Cross-repo product plan:
`/Users/armanisadeghi/code/common-docs/projects/ai-work-hub/PLAN.md` — read it before building
conversation browsing, provider launch, saved requests, skills, associations, or automation for
this integration.

## What this edge owns

`POST /coding-session/hooks` is the frozen provider-neutral command-hook
ingress. It accepts the strict adapter envelope v1 with
`action="observe_hook"` for Claude Code, Codex, Cursor, or VS Code. The route
is available without a bearer only on the engine's direct loopback socket and
requires an exact IPv4/IPv6 loopback peer in addition to rejecting every
Cloudflare/tunnel-marked request with 403. A command hook never
receives or stores the user's Supabase JWT.

The success boundary is an independently durable local SQLite commit. The
route uses a short connection to the same `matrx.db`, `BEGIN IMMEDIATE`, and
`synchronous=FULL`; this prevents an unrelated coroutine on the application's
shared connection from committing or rolling back the hook transaction and
fsyncs the WAL before the response exists. A successful request returns HTTP
202 with:

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
Provider-supplied stable event IDs deduplicate a still-pending exact envelope
replay; reusing one with any changed envelope field is a 409. Events without a
real provider ID are never given a fabricated stable identity.

## Ordered cloud publication

The engine-owned `CodingSessionBridgeOutbox` task reads the persisted active
user session through `TokenRepo`, obtains the existing app-configured
`AIDreamClient`, and sends the exact stored JSON to authenticated
`POST /api/coding-sessions/bridge`. There is no second token, HTTP client,
daemon, service, or database.

- Rows are attempted strictly by SQLite ID. A deferred or failed head row
  blocks later rows; the bridge never reorders a provider event stream.
- Success means aidream returned 2xx **and** a valid BridgeResponse v1 receipt
  for the same provider/action with event-mirror fidelity, canonical session
  and conversation UUIDs, zero conflicts, and exactly one accepted or duplicate
  entry. A generic/stale/proxy 2xx is retryable and never deletes the row.
- The stored envelope bytes are checked against their committed SHA-256 and
  revalidated as adapter envelope v1 before every upload. Corruption blocks the
  ordered queue loudly instead of sending mutated raw data.
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

## Explicit Claude history sync

`GET /coding-session/claude/history/preview` is the only discovery door. It runs only after the
user presses **Review local history**, reports file/session/byte/project counts plus bounded session
summaries, and uploads nothing. `POST /coding-session/claude/history/import` accepts at most ten
session IDs with their exact preview revisions and atomically commits every generated
`append_native` batch to the existing V19 outbox before returning 202. `GET
/coding-session/claude/history/status` reports the outbox and last explicit enqueue.

- **The source is Claude's documented local JSONL.** Main transcripts come from
  `${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<project>/<session>.jsonl`; subordinate JSONL files
  become independent `subagent:<id>` streams.
- **Preview is privacy-minimal.** It never returns transcript text or a raw directory. It exposes a
  project display basename plus an opaque SHA-256 project key. Its selection revision hashes the
  exact bytes of every main/subagent stream, not file timestamps. The import reopens bounded regular
  files without following symlinks, re-hashes the bytes actually read, and rejects a replacement or
  mutation before the outbox changes. The import preserves exact raw JSON only after explicit
  selection; the full parsed provider record remains owner-only (JSON whitespace/key-order are not
  claimed as durable semantics).
- **Account identity is provenance, not authorization.** `claude auth status --json` is reduced to
  a local SHA-256 `provider_account_key`; email, organization name, credentials, and tokens never
  enter preview, outbox, or cloud metadata. An unknown account may be enriched once; a known key
  can never change for the same provider session ID. This key describes the active login at import;
  Claude's local transcript does not independently prove which historical account created a file.
  Claude and AI Matrx identities are rechecked after the bounded read and before enqueue.
- **Reconciliation is additive and idempotent.** Entry UUID plus exact canonical payload hash is
  replay-safe. A changed preview revision, account switch, transcript UUID reuse, or source mutation
  rejects before any new outbox row. Selection identity is session UUID plus opaque project key;
  duplicate UUIDs across project directories never collapse locally, and the server rejects a
  provider-session UUID already bound to another project. Transcript truncation/rotation never
  deletes cloud history.
- **Partial files stay partial.** Corrupt/oversized lines are omitted with their original line-based
  sequences retained, so the cloud checkpoint reports a gap instead of pretending the import is
  complete. Subagents and compaction/system records remain exact raw entries even when no safe
  normalized projection exists.
- **Exact ledger is not native restore.** The copied JSONL supports a faithful private AI Matrx
  transcript. Native Claude continuation is only the local `claude --resume <session-id>` command
  while the original file, workspace, and current Claude login exist. The importer does not copy
  file-history snapshots, permissions, credentials, or filesystem state and always reports
  `native_restore_available=false`.
- **Pin/archive truth is unsupported.** Local Claude exposes names, project grouping, branch, and
  fork/session lineage in the documented transcript/session picker. No stable local pin/archive
  source exists, so the product does not infer those states. Web-managed archive is a separate
  Claude web capability.

Hard bounds: 10,000 discovered sessions; 200 preview rows; ten selected sessions; 64 MiB per
explicit import; 2 MiB per JSONL line; 100 entries and 4 MiB per outbox envelope; 2,048 UTF-8 bytes
for the allowlisted `source_metadata` object.

## Deliberate hook boundary

Only independently observed hook events enter `/hooks`; selected Claude JSONL uses the explicit
history routes above. Managed native provider sessions use the same envelope models, but history
sync adds no Claude SDK dependency and does not advertise native resume/fork. `models.py` is the exact wire twin of the unreleased
`matrx_ai.coding_sessions.BridgeRequest`; replace the twin with the package
model once that package version is published and consumed here.

## Verification

- `tests/unit/test_coding_session_bridge.py`: strict envelope validation,
  migration, FULL-sync transaction isolation, durable ack, exact stable
  replay/mutation, missing-ID behavior, offline/restart replay, ordered failure
  recovery, persisted-byte integrity, and strict upstream receipt validation.
- `tests/unit/test_coding_session_mirror_policy.py`: future-schema generation
  plus independent runtime pull and push refusals for both raw tables.
- `tests/smoke/test_coding_session_bridge.py`: real engine loopback 202,
  tunnel 403, hooks-only gate, and unknown-field rejection.
- `python scripts/generate_mirror_schema.py --check`: generated mirror parity.
- `tests/unit/test_claude_history_import.py`: explicit preview, privacy, subagent/compaction
  preservation, corrupt-line gaps, atomic batching, replay, account switch, content-bound revision
  drift (including preserved size/mtime), bounded giant lines, symlink refusal, duplicate project
  UUID separation, and duplicate entry UUID refusal.
