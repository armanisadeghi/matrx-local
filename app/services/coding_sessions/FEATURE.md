# Coding Session Bridge — Matrx Local edge

System of record:
`/Users/armanisadeghi/code/common-docs/systems/coding/coding-session-bridge/FEATURE.md`.

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

- Rows are strictly ordered inside a V25 **delivery lane**: canonical
  `(provider, provider_project_key, provider_session_id)` across every action
  and subordinate stream. For the rare sessionless request, action plus source
  replaces the session identity.
  The oldest eligible lane head is attempted first, but a deferred lane does
  not block another provider, session, or project. Every action/stream for the
  same real session deliberately shares a lane, so `SessionMetadata` cannot
  pass any transcript batch that creates its binding.
- Success means aidream returned 2xx **and** a valid BridgeResponse v1 receipt
  for the same provider/action with event-mirror fidelity, canonical session
  and conversation UUIDs, zero conflicts, and exactly one accepted or duplicate
  entry. A generic/stale/proxy 2xx is retryable and never deletes the row.
- The stored envelope bytes are checked against their committed SHA-256 and
  revalidated as adapter envelope v1 before every upload. Deterministic local
  corruption is preserved in quarantine immediately so mutated bytes are never
  sent and an impossible retry cannot wedge the lane.
- Network/auth/server failure keeps the exact envelope and records a bounded
  retry delay. If aidream accepted it but the response was lost, restart
  replays the same envelope and server idempotency resolves the duplicate.
- Missing/expired auth and the dev world's disabled cloud participation retain
  every row. The first upstream HTTP 401 pauses the publisher globally instead
  of spraying one rejected credential across every lane; a newly verified
  session is the explicit resume signal. `POST /auth/token` verifies the token
  against the configured Supabase Auth project before persisting it, so a token
  issued by another project cannot silently enter the engine.
- The task is registered as `coding_session_bridge` in the existing launcher
  registry and stops before SQLite closes. It owns no subprocess.

### 🚨 THE POISON-ROW RULE — ordered delivery plus infinite retry means STOP

Per-lane ordering is deliberate and correct. Retrying until success is deliberate
and correct. **Together they mean one permanently-rejected envelope halts every
later row in that lane, forever, without quarantine.** Found live 2026-08-17: a
single row had failed **2,520 times since 2026-08-13** with HTTP 409
`entry_mutated` and had blocked **3,709 rows for four days** — a second silent
capture outage sitting underneath the first one.

`entry_mutated` is definitionally terminal: the server already holds that
stable event id with DIFFERENT bytes, so attempt 2,521 fails exactly like
attempt 1. A terminal rejection now moves the row into
`coding_session_bridge_quarantine` and the queue advances.

- **Terminal immediately:** an error code in `_TERMINAL_ERROR_CODES`.
- **Terminal only after `_QUARANTINE_AFTER_ATTEMPTS`:** a bare 400/409/422. A
  proxy or a mid-deploy server emits those transiently, and dropping a row
  early would be real data loss.
- **Never terminal:** offline, or any cloud 5xx. The server has refused nothing.
- **Local-integrity terminal:** persisted SHA, JSON, or envelope-schema failure.
  These conditions cannot self-heal and are quarantined without an upload.
- **The envelope is PRESERVED, never deleted** — zero data loss is this
  outbox's contract, and a refused row is exactly what a human needs to see.
- **Quarantine logs at ERROR and `status()` reports a `quarantined` count.** A
  non-zero count means real events are permanently absent from the platform.

### 🚨 THE POST-DELIVERY RULE — retiring a DELIVERED row is not bookkeeping

Once aidream has accepted an envelope, deleting its local row is the ONLY thing
that stops the publisher sending it again. That write therefore runs on the same
durable boundary the hook ingress uses — a private short connection with
`BEGIN IMMEDIATE`, `synchronous=FULL`, and `_DURABLE_WRITE_BUSY_TIMEOUT_MS` —
never on the application's shared connection.

Found live 2026-08-19 on **v1.4.35**, which had already fixed the v1.4.34 crash
(an ack-write exception raising out of the tick). The write was still issued on
the shared connection, so under a codex hook burst it lost with
`database is locked`; the ack failed, the delete failed, the deferral failed,
and the loop `continue`d and re-selected the same row. Outbox row 72184 was
re-POSTed to aidream **48 times**, and because `sync_pending` holds
`_sync_lock` for the whole tick — up to `_MAX_BATCH` uploads of one delivered
row — every coding-session route queued behind it: `/coding-session/status`
median **1,040 s**, `/coding-session/hooks` median **330 s**, while `/health`
stayed at 0 ms. A wedge in this loop is an engine-wide latency event, not a
quiet backlog.

- **`_retire_delivered_row` is ack + delete in ONE private transaction.** If it
  fails, a delete-only private transaction is attempted, so an acknowledgement
  problem can never keep a delivered envelope queued.
- **A delivered row is never uploaded twice.** If the delete still cannot land,
  the id goes into the in-memory `_delivered_undeleted` set, the tick `break`s
  (it does not `continue`), and later ticks retry only the delete. Selection
  skips those ids outright.
- **`attempts=0` on a pending row is normal, not evidence of starvation.** The
  publisher only ever touches lane HEADS, so of 22,126 rows exactly 219 were
  ever eligible. Read a pileup as one stalled tick, not a starved lane.

- **An UNEXPECTED exception defers its row; it never stops the publisher.**
  Three separate incidents have now taken the whole tick down — v1.4.34's ack
  write, v1.4.35's delete, and (2026-08-19, v1.4.36) a raw
  `ssl.SSLError: SSLV3_ALERT_BAD_RECORD_MAC` that `AIDreamClient` did not
  classify, so it sailed past `except (AIDreamOfflineError, AIDreamError)`.
  Each time the row recorded no attempt, so it earned no backoff either, and
  every lane sat at `attempts=0` with nothing in the outbox explaining the
  stall. `sync_pending` now catches `Exception` per row, screams, records the
  failure, and lets the next lane through. `AIDreamClient` classifies
  `ssl.SSLError`/`OSError` at its request boundary as `AIDreamOfflineError` —
  at that boundary an `OSError` means exactly one thing, the server was not
  reached.

- **EVERY outbox mutation runs on the durable boundary.** Enqueue, retire,
  defer, record-failure, and quarantine all go through
  `_durable_writes` / the private `BEGIN IMMEDIATE` connection — never the
  shared one. A queue-state write that loses the shared connection's race does
  not merely fail: it fails INSIDE an exception handler and takes the publisher
  with it. That is precisely how v1.4.37 died — `_record_failure` raised
  `database is locked` out of `except AIDreamOfflineError`. Bookkeeping about a
  failure must never become a worse failure, so those calls are individually
  guarded too. Quarantine's copy-then-delete is ONE transaction; a crash
  between them would lose the envelope, the one thing this outbox promises
  never happens.

Regression: `tests/unit/test_coding_session_delivered_row_wedge.py`.

### Provider-neutral delivery status

`GET /coding-session/status` is the one desktop-facing status facade for every
bridge provider (`claude_code`, `codex`, `cursor`, and `vscode`). It reports:

- whether the publisher task is active and cloud participation is enabled;
- pending and quarantined totals grouped by provider, action, and source;
- pending payload bytes and distinct queued-session counts by provider;
- a row for every supported provider even when all of its counts are zero;
- backend-owned capability flags for event mirror, historical import, title
  sync, local runtime, native resume, and VS Code participant conversations,
  plus provider limitations the UI can explain without hardcoded assumptions;
- the safe queue head (provider/action/source, attempts, next retry, and a
  redacted operational error); and
- the last durable local enqueue and last validated cloud acknowledgement,
  globally and per provider/action/source; and
- a publisher-wide safe blocker plus grouped, redacted quarantine reasons.

Migration V24 stores those enqueue/acknowledgement summaries in the bounded
`coding_session_bridge_delivery_activity` aggregate. It has one row per
provider/action/source and never stores an envelope, provider session ID,
project path, transcript, server response body, or raw error. An
acknowledgement is written only after the existing strict BridgeResponse v1
validation passes, in the same transaction that removes the durable outbox
row. “Queued” therefore means local-only; “acknowledged” means aidream proved
the receipt.

The acknowledgement write is **best-effort relative to the delete**: once the
upstream POST has been validated, the envelope is DELIVERED, and a local
write failure (e.g. `database is locked` under hook-ingress contention) must
never keep the delivered row at the head of its lane. Pre-fix (2026-08-19),
an ack-write failure raised out of the publisher tick *after* a successful
upload; the row was never deleted, the lane wedged re-sending the same
envelope forever, and the outbox grew to 17k rows / 579 MB with every row at
`attempts=0`. Now an ack failure logs loudly and the delete still runs; a
delete failure defers the row with backoff instead of crashing the tick
(server-side bridge idempotency makes the eventual re-send a duplicate, not
corruption). Claude history status derives the same truth for the
`claude_local_jsonl` source and includes its quarantine count instead of
silently dropping it.

V25 replaces the retired global FIFO scheduler with those session-stream lanes.
`head_blocker` is the oldest lane head whose retry time is still in the future;
an ordinary ready row is no longer mislabeled as a blocker.

Migration V26 adds `coding_session_bridge_queue_metadata`, a payload-free side
index written in the same durable transaction as every enqueue, retirement, and
quarantine move. Status groups that small index and never JSON-parses the raw
outbox. It also stores local-only enqueue provenance (`explicit_history`,
`capture_recovery`, `local_runtime`, or `live_hook`), so history retry/discard
actions cannot affect automatic recovery or runtime mirrors.

The same migration changes `claude_session_metadata_sent` from an enqueue
ledger into an acknowledgement ledger. Legacy rows are cleared once, because
they cannot prove cloud delivery; one idempotent reconciliation repairs them.
The publisher writes the digest only in the validated acknowledgement +
outbox-delete transaction. A repeated reconciliation before that reports the
metadata as already queued, never synchronized.

Migration V28 adds `item_count` to the payload-free queue index. The overview
therefore names and counts durable **envelopes** separately from the logical
provider events they carry. `GET /coding-session/delivery/envelopes` paginates
safe evidence (dimensions, opaque session correlation, size, item count,
attempts, retry timing, and redacted failure) without exposing transcript or
command content. One exact envelope can be retried. Discard is deliberately a
two-step operation: the first request returns its exact impact and
`confirmation_required`; only `confirm=true` permanently removes the local
copy. A quarantined retry validates the preserved bytes and schema before
atomically returning that envelope to its original ordered lane.

### Provider readiness is evidence, never a connection guess

`GET /coding-session/providers/readiness` joins safe local probes for Claude
Code, Codex, Cursor, and VS Code with the delivery-status aggregate. It keeps
product installation/version/running state, adapter detection/configuration,
authorization and hook-trust knowledge, adapter-owned pending/poison/in-flight
spools, last local enqueue, and last cloud acknowledgement as separate fields.
The connection state remains `unverified`: even a running product or a recent
acknowledgement proves only that exact observation, never a current live
connection. Unknown installation and process states are nullable instead of
being reported as false. Paths, process arguments, usernames, provider session
IDs, and spool filenames never leave the facade.

Actions are guided instructions. Claude and Codex can point to their supported
host setup/test/trust flows; unpublished Cursor and VS Code artifacts are never
silently installed from local source checkouts.

## Automatic capture reconciliation — backfilling what the hooks lost

`capture_reconciler.py` closes the hole a non-blocking hook leaves. Every Claude
Code hook rides the plugin's already-connected MCP session, an MCP hook can
never initiate OAuth, and Claude treats hook failure as **non-blocking** — so a
dropped connection stops mirroring permanently and silently (23.5 hours lost on
2026-08-16; a live diff on 2026-08-17 then found **160 of the 200 most recent
local sessions had never reached the platform at all**).

**This is not a second transport.** The reconciler runs the diff a human
otherwise runs by hand: `GET /coding-sessions/sessions` for what the platform
holds, a stat-only inventory of every bounded local session, and the existing
`import_selected` → the existing outbox for whatever is missing. Only the
bounded selected candidates are content-hashed before enqueue, while every
byte-hashing, account-identity, and conflict rule stays the importer's.

The cloud side of that diff is never a capped sample. `identity_client.py`
walks the schema-v2 opaque keyset cursor until AI Dream reports
`has_more=false`, `complete=true`, and the accumulated rows equal the frozen
`total_count`. Capture reconciliation aborts without reading the local
inventory when any page is malformed, changes snapshot totals, loops a cursor,
or cannot prove completeness; a partial allowlist must never make a known
session look missing.

- 🚨 **THE ERA RULE — the consent boundary.** Only sessions whose local activity
  falls at or after the owner's **earliest existing binding** are backfilled:
  the era in which they had already opted into continuous mirroring and it
  simply failed. An owner with **no bindings at all gets nothing** — a
  first-time user's whole history is never swept up — and pre-install sessions
  stay local forever. Never widen this without Arman's ruling.
- **Both identity forms are matched.** A hook binding stores Claude's RAW
  session UUID; a native import stores the `claude-sdk:<digest>:<b64>`
  composite. Checking only one form re-uploads everything on every pass.
- **Bounded per pass:** at most `MAX_SELECTED_SESSIONS`, under
  `BATCH_BYTE_BUDGET` (half the importer's cap), oldest-first so a backlog
  drains deterministically. The byte budget exists so one huge transcript
  cannot fail a batch and burn the retry budget of nine innocent sessions.
- **Bounded per session:** `claude_capture_backfill` counts failed attempts;
  success resets the count. After `MAX_ATTEMPTS` a broken session is left alone
  with its error preserved. A changed stat fence resets the count — more writing
  is a new input, not the same failure repeating. Status aggregates every
  exhausted failure instead of filtering only the newest 50 ledger rows.
- **A backfill logs at WARNING, not INFO.** It firing means the hook path lost
  data, which is a defect upstream, not routine housekeeping.
- Routes: `GET /coding-session/claude/capture/status`,
  `POST /coding-session/claude/capture/reconcile?dry_run=true`. Task:
  `claude_capture_reconciler` in the launcher registry.

## Raw-ledger boundary

This outbox is not the cloud raw ledger and not part of generic chat mirror
sync. `chat.coding_session` and `chat.coding_session_entry` remain owner-only
in the one platform Postgres. `scripts/generate_mirror_schema.py` explicitly
excludes both tables, and `app/services/chat_sync/engine.py` independently
refuses to pull or push them even if a malformed generated mirror includes
one. Generic schema regeneration can therefore never copy raw commands,
paths, file contents, or secrets into the desktop chat replica.

## Explicit Claude history sync

`POST /coding-session/claude/history/review` is the only discovery door. It runs only after the
user presses **Review local history**, persists an immutable local inventory, reports exact
new/content-changed/details-changed/missing/unchanged counts, and uploads nothing. `GET
/coding-session/claude/history/scans/{scan_id}` searches, filters, sorts, and pages that stable
inventory instead of hiding older sessions. A review uses file identity/size/mtime fences and does
not reread the transcript corpus. `POST /coding-session/claude/history/prepare` content-hashes only
the at-most-ten selected rows and refuses if their review fence changed. `POST
/coding-session/claude/history/import` accepts those exact prepared revisions and atomically commits
every generated
`append_native` batch **and its `SessionMetadata` observation in one transaction**
to the existing V19 outbox before returning 202. A metadata failure rolls back the
transcript batches, so a 500 can never conceal a partial commit. `GET
/coding-session/claude/history/status` reports the outbox and last explicit enqueue. If delivery is
blocked, the page exposes `DELETE /coding-session/claude/history/pending` as **Discard queued
copies**; it removes only explicit Claude-history batches and never source files, hook observations,
or already-delivered cloud history. **Retry now** clears only history-batch backoff/error and wakes
the ordered publisher; neither repair silently drops an event.

- **The source is Claude's documented local JSONL.** Main transcripts come from
  `${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<project>/<session>.jsonl`; subordinate JSONL files
  become independent `subagent:<id>` streams.
- **Review is privacy-minimal.** It never returns transcript text or a raw directory. It exposes a
  project display basename plus an opaque SHA-256 project key. Preparation hashes the exact bytes of
  every selected main/subagent stream. The import reopens bounded regular files without following
  symlinks, re-hashes the bytes actually read, and rejects a replacement or mutation before the
  outbox changes. The import preserves exact raw JSON only after explicit selection; the full parsed
  provider record remains owner-only (JSON whitespace/key-order are not claimed as durable
  semantics).
- **Account identity is provenance, not authorization.** `claude auth status --json` is reduced to
  the canonical v2 `provider_account_key`: a deterministic SHA-256 of the fixed public namespace
  `matrx:coding-session:claude-code-account:v2` plus apiProvider/authMethod/orgId/lowercased email,
  so the SAME Claude account produces the SAME key on every machine (the retired v1 key was HMAC'd
  with a per-installation secret and falsely conflicted across machines). It is an opaque
  correlation ID, deliberately not a secret. The wire metadata also carries
  `provider_account_key_version=2`, `provider_account_fingerprint` (the key's first 12 hex chars),
  and a cloud-safe `provider_account_label` — a masked email such as `a***n@t***.com`, else
  `org:<orgId[:8]>`; `orgName` is never used because it commonly embeds the raw email. The direct
  loopback review/runtime responses additionally return the full locally observed account identity
  so the person can identify which of their accounts is active. That display field never enters an
  outbox envelope or cloud metadata; organization names, credentials, and tokens never enter either.
  An unknown account may be enriched once; a known same-version key can never change for the same
  provider session ID, and a v2 key upgrades a legacy v1 binding exactly once on re-import. This
  identity describes the active login at import time; Claude's local transcript does not
  independently prove which historical account created a file. Claude and AI Matrx identities are
  rechecked after the bounded read and before enqueue.
- **[`claude_probe.py`](claude_probe.py) is the single CLI/account probe.** A packaged desktop GUI
  has a deliberately sparse system `PATH`, while Claude's current native installer puts the
  launcher at `~/.local/bin/claude`. Resolution therefore checks executable `PATH` entries plus the
  official per-user location, the retired `~/.claude/local/claude` location, and known
  Homebrew/system/platform locations. History import and the local runtime consume this same
  resolver. Auth/version subprocesses are bounded; a timeout terminates and reaps its child, and
  the probe preserves distinct not-found, execution-failure, timeout, signed-out, and
  stable-identity-unavailable states. Developer-owned Anthropic keys and gateway overrides are
  blanked for the probe so they cannot shadow the user's subscription login; version failure is
  diagnostic only and cannot turn a successfully verified account into a false sign-out.
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

## Claude's own labels — the same title, kept in sync BOTH ways

🚨 **Arman's ruling (2026-08-16): "Those labels cannot be different. They must be exactly the same,
and they must remain in sync. So if it's changed in Claude Code, we are able to update it in our
system."** and *"The Claude Code title is what we should use for our label. And when our
conversations go to Claude Code, or if I update this, then the Claude Code value should be updated
to match."* The Claude Code desktop app stores the EXACT label it shows in its sidebar in one small
JSON record per session at
`<app support>/Claude/claude-code-sessions/<accountUuid>/<orgUuid>/local_<id>.json`. Every record
carries `cliSessionId` — the UUID naming `~/.claude/projects/<cwd-slug>/<cliSessionId>.jsonl` —
which is the identity the bridge already binds, so the join is exact.

One explicit pass (`POST /coding-session/claude/labels/sync`) reconciles both directions.
Cross-repo contract: `/Users/armanisadeghi/code/common-docs/systems/coding/coding-session-bridge/FEATURE.md`
§ "The session label".

- **[`claude_session_index.py`](claude_session_index.py) is the only reader.** It reduces each
  record to display labels — `title`, `titleSource`, workspace name (the last segment of `cwd`),
  `branch`, `worktreeName`, `isArchived` — and never emits a raw path. A desktop sync script unions
  these index files across accounts, so the same `cliSessionId` appears once per account folder
  (five, on this machine); the freshest record wins. Bounds: 50,000 files, 8 MiB per file. Override
  the root with `CLAUDE_DESKTOP_SESSIONS_DIR`.
- 🚨 **Freshness is `lastActivityAt` THEN the record file's mtime, and the mtime half is
  load-bearing.** Observed against the real app 2026-08-16: renaming a session in Claude Code
  rewrites ONLY the active account's copy and does NOT bump `lastActivityAt`, so every copy ties and
  a first-read tie-break selects a stale sibling's old title purely by directory sort order. Never
  drop back to a single-key comparison.
- **[`title_sync.py`](title_sync.py) is the reconciler for both directions.** It asks aidream
  `GET /coding-sessions/sessions?provider=claude_code` for the owner's bindings and matches each
  `provider_session_id` (raw UUID for event-mirror bindings, the decoded
  `claude-sdk:<digest>:<b64 id>` composite for imports) to an index record. Inbound: one
  `SessionMetadata` observation per session whose Claude labels changed. Outbound: see below. `GET
  /coding-session/claude/labels/status` reports index availability and the last pass;
  `?dry_run=true` reports without enqueueing and without opening a single Claude file.
- **The allowlist must be complete before either direction runs.** The shared
  `identity_client.py` consumes every schema-v2 keyset page and accepts the
  inventory only when its final count matches the server-frozen `total_count`
  and `complete=true`. A legacy, truncated, changing, or malformed response
  blocks the pass; title sync never mutates Claude's files from partial cloud
  knowledge.
- **The local index must also be complete.** The reader reports actual
  truncation separately from its 50,000-file bound. Apply, verify, and retry
  fail closed if any candidate was truncated or unreadable; preview/status
  remain available so the user can inspect the blocker. A record must also
  prove writable before the writer will touch it.
- **Every compared provider detail comes back from AI Dream.** Identity rows
  expose the last acknowledged Claude project display name, git branch,
  worktree name, archive state, pin state/rank, and category alongside title
  evidence. The local comparison reads those `claude_*` fields; it never
  guesses cloud state from a local sent ledger. Missing values remain explicit
  `null`, so verification can distinguish cleared metadata from absent proof.
- **Session details sync is preview → apply → acknowledge → verify.** Every pass
  has a durable operation id and one payload-free comparison row per bound
  session across title, project, branch, worktree, archive, pin/rank, and
  category. A preview records chosen direction/action/reason and changes no
  files or queue state. Apply records detected and enqueued separately;
  `claude_session_metadata_sent` remains the acknowledgement proof. Verification
  refetches the complete AI Matrx identity inventory and rereads Claude's index
  before it can call a row `verified`. Missing server fields are reported as
  unobserved, never invented or treated as equal.
- **A write-down begins with a durable intent.** The intent is committed before
  any Claude index file is opened for mutation. Every copy outcome is then
  recorded before enqueue, including partial writes/refusals, and the
  convergence observation carries its receipt id. A write followed by enqueue
  failure remains an explicit retry obligation; the pushed-title ledger advances
  only after the envelope is durable. On restart, abandoned operations close as
  failed and orphaned intents gain an inspectable `recovery_required` row. A
  failed/partial intent is individually retryable with fresh byte/mtime fences;
  retrying an already verified intent is a no-op. The UI pages this evidence
  with an opaque compound keyset cursor through
  `/coding-session/claude/labels/operations/{operation_id}` and proves final
  state through the operation's `/verify` endpoint.
- **Pins + categories ride the same observation (2026-08-21).** The desktop app keeps pins and
  custom sidebar groups in its localStorage LevelDB, which the machine's session-sync agent
  extracts into the canonical ledger (`~/.claude/claude-code-sidebar-state.json`; see that
  machine's `~/.claude/CLAUDE-CODE-SESSION-SYNC.md`). `claude_session_index.read_session_index`
  joins the ledger by record filename (`CLAUDE_SIDEBAR_LEDGER` overrides the path) and the
  payload gains `is_pinned` / `pinned_rank` / `category`; the server mirrors pinned onto
  `chat.conversation.is_favorite` and category into the conversation's bridge metadata. A
  session the ledger has never observed sends neither field.
- **The server list is the allowlist for BOTH directions.** Labels of local sessions AI Matrx never
  mirrored never leave this machine, and a session AI Matrx does not own is never written to on
  disk. This is the whole privacy boundary of the feature — never widen it to "every local session".
- **Idempotent by ledger.** `claude_session_metadata_sent` (V20) records the exact payload digest
  last cloud-acknowledged per bound session; `claude_session_title_pushed` (V21) records the exact title last
  written down. An unchanged label costs zero network work and zero writes into another
  application's files. The explicit import writes the V20 ledger too, so an import and a pull pass
  never duplicate each other.
- **Metadata plane, not the ledger.** A `SessionMetadata` observation updates an EXISTING binding's
  labels only — it never mints a binding or a conversation and never appends a transcript entry, so
  it works against event-mirror and native bindings alike. An unmirrored session comes back
  `accepted=0` with no session identity, which the outbox treats as a durable settle rather than
  retrying forever.

### The return direction — AI Matrx → Claude Code

🚨 **THE CLIENT LAW APPLIES HARDEST HERE: the ladder is a SERVER answer.** aidream computes
`title_source` (`user > provider > first_prompt > placeholder`) with `titles.resolve_title_source`
and returns it, plus `conversation_title` and `claude_title`, on every identity row. This repo
decides nothing about precedence — it pushes down when, and only when, the server says `user`.
Re-deriving the ladder here would be the exact defect
`/Users/armanisadeghi/code/common-docs/policies/clients-consume-never-reimplement.md` names.

- **[`claude_label_writer.py`](claude_label_writer.py) is the only writer**, and every rule in it is
  a restriction, derived by observing the real app on 2026-08-16 rather than assumed:
  - **Two fields, never a third.** Only `title` and `titleSource` are assigned, with `titleSource`
    inserted directly after `title` exactly as Claude Code's own rename does. `titleSource` is
    `user`, because an AI Matrx rename IS the human naming their session — the same value Claude
    records for a title its auto-titler must not replace.
  - **Byte-fidelity or refuse.** Claude's writers use three JSON serializations across its history
    (all 4,282 records on this machine match one exactly). The parsed record is re-serialized with
    each candidate and compared to the original bytes; the match is reused for the write. A format
    this module has never seen is left untouched. Never "just write it out" — that would silently
    reformat someone else's file.
  - **Backed up once per file** into `<MATRX_HOME_DIR>/backups/claude-session-index/`, holding the
    bytes from before AI Matrx ever touched that record. A later write never overwrites the
    snapshot.
  - **Fenced.** Size, mtime_ns and content SHA-256 are re-checked immediately before an atomic
    temp+fsync+rename. A record Claude Code moved underneath us is refused (`concurrent_modification`),
    never clobbered — its value is newer and the inbound leg will pull it up.
  - **Every account copy** of the session is written, so no stale sibling can win the next read.
- **The push is reported back.** After a successful write the reconciler enqueues a
  `SessionMetadata` observation carrying that title plus `title_origin="ai_matrx_user"`. The server
  then records the tier as `user` AND converges `applied_title` on the shared label. **Do not drop
  that second envelope** — without it `applied_title` goes stale and a later rename in Claude Code
  could never win, because the ladder would read our pushed title as a user title it must not
  overwrite.
- **Conflict rule: last-writer-wins, Claude Code wins a tie.** Both sides agree only while
  `applied_title` equals the shown label. If Claude's index title no longer equals the server's
  `claude_title`, Claude Code was renamed too and the return leg stands down for that pass. If
  Claude Code overwrites a title we wrote, nothing is lost — the V21 ledger recognises our own value
  and stands down, and the next inbound pass pulls Claude's value back.
- **Latency is honest.** Claude Code loads this index at startup and serves it from memory — a disk
  write is NOT picked up live (verified 2026-08-16: the app kept reporting its cached title after
  the file changed). The card says the title appears "the next time Claude Code reloads". Never
  promise instant.
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
- `tests/unit/test_claude_label_writeback.py`: the RETURN direction — byte-for-byte writes through
  each of Claude's three serializations, refusal of an unseen format / a concurrent rewrite / a
  record that now names another session, the once-per-file backup, all account copies written, the
  mtime tie-break, only `title_source="user"` travelling down, an unowned session never written,
  Claude Code winning a two-sided rename, our own overwritten push not being fought over, and
  dry-run opening no file at all.
- `aidream/scripts/_verify_claude_label_return_sync.py`: the live two-way proof against the real
  platform database and the real Claude index on this machine, restoring every byte it touches.
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
- `tests/unit/test_claude_probe.py`: packaged macOS `PATH` discovery of the official
  `~/.local/bin/claude` launcher, executable validation, legacy-location compatibility, safe
  account reduction, distinct probe failure states, bounded diagnostics, and timeout
  termination/reaping.

## The LOCAL Claude Code runtime — start/resume/cancel/stream on this machine

`local_runtime.py` is the desktop half of the managed provider-runtime story
(the hosted half is aidream's sandbox-gated `claude_managed_runtime.py`). It
drives the official Claude Agent SDK against **the user's OWN installed
`claude` and subscription login** — `cli_path` is always the user's binary, no
`CLAUDE_CONFIG_DIR` override, and `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`/
`ANTHROPIC_BASE_URL` (+ Bedrock/Vertex switches) are BLANKED in the child env
because the CLI silently prefers an inherited key over the claude.ai login
(observed live). A runtime session is therefore a first-class Claude Code
session: it writes the real `~/.claude/projects/<slug>/<id>.jsonl`, shows up in
Claude Code's own UI, and stays locally `--resume`-able.

- **Persistence is the EXISTING import pipeline — deliberately not a second
  transport and not the SDK `session_store` seam.** After each completed turn
  and at settle, the runtime runs a targeted
  `ClaudeHistoryImporter.import_selected` pass for this one session. Exact
  JSONL bytes become `append_native` batches in the durable outbox → production
  `chat.coding_session` as fidelity=native, origin=matrx_local, with account
  identity and Claude's own label riding along. A pass that conflicts because
  Claude wrote mid-hash simply defers to the next turn boundary (the capture
  reconciler closes any residual gap). Live tokens stream over the local SSE
  seam; the cloud ledger advances at turn boundaries — that is the honest v1
  granularity and the UI says so.
- **`setting_sources=["project","local"]`** — the repo's CLAUDE.md loads;
  user-level settings are excluded ON PURPOSE so the AI Matrx plugin's hooks
  cannot fire inside a runtime session and mint a second (event_mirror, raw
  UUID) binding beside the native ledger.
- **The workspace allowlist is the safety boundary.** Roots from setting
  `claude_runtime_workspace_roots` (default `~/code`); each folder must be
  approved once (`claude_runtime_approved_folders`, persisted). Approval is
  loopback-only — a physical-presence decision made in the desktop app, never
  remotely.
- **Resume is capability-gated truth.** `resumable()` answers only from
  Claude's own local store (transcript file present + workspace still exists).
  SEND is RESUME: a follow-up turn is a native resume with a new prompt.
- **The cloud→local relay is the EXISTING per-user Supabase Broadcast bridge
  channel** (`matrx-local-bridge:<user_id>` → `cross_component_router` →
  `invoke_command`): `coding_runtime.{capabilities,start,status,cancel,
  resumable}` in `app/api/coding_runtime_handlers.py`, the same registry that
  already carries the ~80-tool dispatcher. No new service, database, or
  inbound port. The browser client is matrx-frontend
  `features/ai-work/lib/matrxLocalRuntime.ts`.
- Loopback HTTP routes for the desktop UI: `/coding-session/runtime/*`
  (capabilities, approvals, start, status, cancel, resumable, SSE `/events`).
  Desktop surface: `AgentRuntimeCard` on the Claude Code history page.
- Provider execution and AI Matrx mirroring are separate settlement outcomes.
  A final mirror exception can never suppress `runtime_finished`; the terminal
  event and status expose both results. Every runtime event has a monotonic
  sequence and UTC emission time. SSE accepts `after_sequence`; if that cursor
  predates the bounded replay buffer it emits `stream_gap` with the available
  range so the consumer can refresh status instead of silently missing output.
- Engine shutdown interrupts active runs (Hard Rule 0, wired in `app/main.py`).

Verification: `tests/unit/test_local_claude_runtime.py` (allowlist/approval
gates, resume truth, a REAL importer→outbox mirror pass) and
`scripts/_verify_local_claude_runtime_e2e.py` — the production proof run
2026-08-17: START/STREAM → native binding + canonical conversation in
production (`fidelity=native`, marker text projected), NATIVE RESUME appending
to the same binding (entries 10→17, history marker repeated), CANCEL settling
as `cancelled`, outbox drained to zero with validated receipts.
