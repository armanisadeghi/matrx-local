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
/coding-session/claude/history/status` reports the outbox and last explicit enqueue. If delivery is
blocked, the page exposes `DELETE /coding-session/claude/history/pending` as **Discard queued
copies**; it removes only explicit Claude-history batches and never source files, hook observations,
or already-delivered cloud history. **Retry now** clears only history-batch backoff/error and wakes
the ordered publisher; neither repair silently drops an event.

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
  the canonical v2 `provider_account_key`: a deterministic SHA-256 of the fixed public namespace
  `matrx:coding-session:claude-code-account:v2` plus apiProvider/authMethod/orgId/lowercased email,
  so the SAME Claude account produces the SAME key on every machine (the retired v1 key was HMAC'd
  with a per-installation secret and falsely conflicted across machines). It is an opaque
  correlation ID, deliberately not a secret. The wire metadata also carries
  `provider_account_key_version=2`, `provider_account_fingerprint` (the key's first 12 hex chars),
  and a display-safe `provider_account_label` — a masked email such as `a***n@t***.com`, else
  `org:<orgId[:8]>`; `orgName` is never used because it commonly embeds the raw email. The raw
  email, organization name, credentials, and tokens never enter preview, outbox, or cloud metadata.
  An unknown account may be enriched once; a known same-version key can never change for the same
  provider session ID, and a v2 key upgrades a legacy v1 binding exactly once on re-import. This
  identity describes the active login at import time; Claude's local transcript does not
  independently prove which historical account created a file. Claude and AI Matrx identities are
  rechecked after the bounded read and before enqueue.
- **Reconciliation is additive and idempotent.** Entry UUID plus exact canonical payload hash is
  replay-safe. A changed preview revision, account switch, transcript UUID reuse, or source mutation
  rejects before any new outbox row. Selection identity is session UUID plus opaque project key;
  duplicate UUIDs across project directories use the same deterministic namespaced identity as the
  Claude Agent SDK SessionStore and never collapse. The raw Claude UUID remains only in bounded
  source metadata. Transcript truncation/rotation never
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
- **Pinning is unsupported; archive is now READ, never inferred.** Claude Code's desktop app keeps a
  real `isArchived` flag in its own session index (below), so archive state is observed as provider
  truth. There is still no local pin/starred/tags/category source anywhere — the product does not
  infer those and must not invent them.

## Claude's own labels — the same title, kept in sync

🚨 **Arman's ruling (2026-08-16): "Those labels cannot be different. They must be exactly the same,
and they must remain in sync. So if it's changed in Claude Code, we are able to update it in our
system."** The Claude Code desktop app stores the EXACT label it shows in its sidebar in one small
JSON record per session at
`<app support>/Claude/claude-code-sessions/<accountUuid>/<orgUuid>/local_<id>.json`. Every record
carries `cliSessionId` — the UUID naming `~/.claude/projects/<cwd-slug>/<cliSessionId>.jsonl` —
which is the identity the bridge already binds, so the join is exact.

- **[`claude_session_index.py`](claude_session_index.py) is the only reader.** It reduces each
  record to display labels — `title`, `titleSource`, workspace name (the last segment of `cwd`),
  `branch`, `worktreeName`, `isArchived` — and never emits a raw path. A desktop sync script unions
  these index files across accounts, so the same `cliSessionId` appears once per account folder; the
  record with the newest `lastActivityAt` wins. Bounds: 50,000 files, 8 MiB per file. Override the
  root with `CLAUDE_DESKTOP_SESSIONS_DIR`.
- **[`title_sync.py`](title_sync.py) is the pull-sync reconciler.** `POST
  /coding-session/claude/labels/sync` asks aidream `GET /coding-sessions/sessions?provider=claude_code`
  for the owner's bindings, matches each `provider_session_id` (raw UUID for event-mirror bindings,
  the decoded `claude-sdk:<digest>:<b64 id>` composite for imports) to an index record, and enqueues
  one `SessionMetadata` observation per session whose labels changed. `GET
  /coding-session/claude/labels/status` reports index availability and the last pass; `?dry_run=true`
  reports without enqueueing.
- **The server list is the allowlist.** Labels of local sessions AI Matrx never mirrored never leave
  this machine. This is the whole privacy boundary of the feature — never widen it to "every local
  session".
- **Idempotent by ledger.** `claude_session_metadata_sent` (V20) records the exact payload digest
  last enqueued per bound session, so an unchanged label costs zero network work and a rename is
  detected on the next pass. The explicit import writes the same ledger, so an import and a pull
  pass never duplicate each other.
- **Metadata plane, not the ledger.** A `SessionMetadata` observation updates an EXISTING binding's
  labels only — it never mints a binding or a conversation and never appends a transcript entry, so
  it works against event-mirror and native bindings alike. aidream applies title precedence
  `user > provider > first_prompt > placeholder`: a title the user set in AI Matrx always wins.
  An unmirrored session comes back `accepted=0` with no session identity, which the outbox treats as
  a durable settle rather than retrying forever.
- **The import carries the same labels.** `_discover_sources` prefers the index title/branch/
  workspace over anything derived from the JSONL, and `import_selected` queues one label observation
  per session behind the batches that mint the binding. It is deliberately a separate envelope:
  **Discard queued copies** drops only `append_native` rows, and abandoning a byte copy must never be
  collateral damage for a label update.

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
- `tests/unit/test_claude_session_labels.py`: index dedupe by newest activity, no raw path in any
  payload, both bound identity forms resolving to one CLI session UUID, the server list as the
  allowlist (an unmirrored session's label never enters an envelope), idempotent re-sync, a rename in
  Claude Code reaching the next pass, dry-run, loud blocked reasons, the SessionMetadata
  acknowledgement contract (native fidelity accepted, unbound `accepted=0` settles), and the import
  carrying the exact index title.
- `tests/unit/test_claude_history_import.py`: explicit preview, privacy, subagent/compaction
  preservation, corrupt-line gaps, atomic batching, replay, account switch, content-bound revision
  drift (including preserved size/mtime), bounded giant lines, symlink refusal, duplicate project
  UUID separation, duplicate entry UUID refusal, deterministic cross-machine v2 account-key
  derivation, and masked-label safety (only the masked label may carry an `@`).
