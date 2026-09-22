# Cloud tool-call delegation client (suspend/resume, headless)

**What this is.** The desktop half of the platform's client-tool
suspend/resume protocol. When a cloud agent turn (web app, extension, any
surface) calls a tool bound to executor `matrx-local`, aidream hard-suspends
the turn (`chat.tool_call` row `status='delegated'`) and ends the stream.
THIS service makes the desktop notice, execute, and deliver. An open web tab
observes resolution through aidream's authenticated durable ledger and gets a
short first-claim window on `/resume`; otherwise the desktop drains the
continuation as a headless fallback.

Canonical protocol doc (wire contract, anti-patterns — read it before
touching this code):
`matrx-frontend/features/agents/docs/CLIENT_TOOL_SUSPEND_RESUME.md`.
matrx-extend's client half (`src/lib/tools/dispatch.ts`) is the reference
implementation for a stream-attached client; this service is the
headless/poll variant.

## Coordination participation gate (`MATRX_CLOUD_PARTICIPATION`)

Delegation is user-authenticated and **instance-claimed**:
`GET /ai/user/pending_calls?instance_id=...` atomically leases only delegated
calls whose tool is explicitly bound to executor `matrx-local`, and only when
the row is untargeted or targets that instance. The desktop's catalog lookup is
a second independent ownership check; browser/UI tools must never reach local
dispatch.

Guard: `app.config.CLOUD_PARTICIPATION_ENABLED` (env `MATRX_CLOUD_PARTICIPATION`,
default `1`). When `0`, this engine is **coordination-silent** — Phase 2f
(delegation client) is not started and Phase 7 / `connect_broadcast` do not
attach to the per-user bridge channel, so it never claims cloud-dispatched work.
`run.py`'s dev isolation defaults source-run engines to `0`; the packaged app
leaves it `1`. A dev engine is still fully usable over its own loopback (a dev
frontend on the same machine reaches it directly). Deliberately opting a dev
engine into the shared channel is safe when the conversation targets one
instance; untargeted legacy calls remain first-claim-wins.

Tier 2 instance-scoped targeting is implemented per the wire contract in
`common-docs/systems/clients/client-tool-delegation/FEATURE.md` §2.7 (Desktop pending-call claim) —
the repo-local design doc (`docs/TIER2_DESKTOP_INSTANCE_TARGETING.md`) was deleted 2026-08-20
as superseded (centralization sweep, doc-migration row 33).

## The pipeline

```
GET /ai/user/pending_calls?instance_id=...  (user JWT, every poll interval)
  → aidream atomically leases only matrx-local-bound tools for this instance
  → defense: tool_name resolves in app.tools.catalog.get_by_cloud_name
  → execute via app.tools.dispatcher.dispatch(entry.dispatcher_name, args)
  → POST /ai/conversations/{id}/tool_results  (duration_ms always sent)
  → continuation_needed=true
      → wait briefly for an open web tab's ledger watcher to claim POST /resume
      → desktop POST /resume + drain only when no browser claimed it
  → sweep again (re-entrancy)
```

## How a headless desktop learns of a call — two sources, ONE sweep path

1. **Poll (correctness).** `GET /ai/user/pending_calls` on an interval
   (default 15 s, env `MATRX_DELEGATION_POLL_INTERVAL`). The ledger row is
   durable (30-day server expiry) so nothing is ever lost. Works with zero
   new server surface. The normal request includes this app's `instance_id`.
   An unscoped, read-only diagnostic request may run once on entry to an idle
   state (or once for a new unresolved-tool signature) so status can explain a
   target mismatch; it does not repeat every poll.
2. **Broadcast wake (latency).** The wake event is cleared BEFORE each sweep,
   never after. A sweep executes the tool and drains the continuation stream,
   and the NEXT delegated call of the same turn is created — and its wake
   published — inside that window; clearing afterwards discarded it and cost
   the call a whole poll interval. Measured on the owner's Mac 2026-09-22:
   claim latency 0.6-1.2 s when the wake landed in the wait, 16-82 s when it
   landed in the sweep. The backstop poll also tightens to
   `ACTIVE_POLL_INTERVAL` (2 s) while anything is in flight — a result owed,
   a review parked, a UI claim live, or a call unsettled — and stays at the
   full interval when idle. Guard:
   `tests/unit/test_delegation_latency_and_conflict.py`.
   aidream publishes `kind:"wake"`,
   `action:"tool_call.delegated"` on `matrx-local-bridge:<user_id>` when a
   turn suspends. `app/api/cross_component_router.py::_handle_wake` routes
   it to `get_delegation_engine().request_sweep(...)`. The wake payload is a
   HINT only — execution always re-reads the ledger via the poll endpoint.
   (Server-side publish lives in aidream
   `aidream/services/runtime/conversation.py::_settle_completed` →
   `aidream/api/cross_component/publisher.py::publish_delegation_wake`;
   deploy-gated.)

Everything funnels into `DelegationEngine.sweep_once()` — there is no second
execution path. Do not add one.

## Invariants (each maps to a shipped bug in a sibling client)

- **Never wait on the original stream** — it ended at suspend. Resume is the
  only continuation path.
- **An open web tab owns the continuation stream.** It polls the authenticated
  pending-call ledger while a delegated card is visible, and the desktop gives
  it a short first-claim window. The server's atomic resume claim is still
  authoritative; the desktop remains the no-browser fallback.
- **Retain the executed result until continuation settles.** Re-posting an
  already-resolved result is idempotent and re-evaluates `continuation_needed`.
  This is the recovery path for a lost acknowledgement, failed `/resume`, or
  retryable conflict after the durable tool row has left `pending_calls`.
- **Always read `continuation_needed`** from the tool_results response and
  resume when true. Skipping this strands the conversation.
- **Single-flight resume per `user_request_id`** (10 s suppression +
  bounded `resume_conflict` retry: 700 ms × attempt, max 4). Parallel
  delegated calls can both report `continuation_needed=true`; two racing
  resumes caused the 2026-06-09 incident. `not_resumable` is terminal;
  `outstanding_delegated_calls` retains the result obligation until the last
  sibling lands. Neither conflict is surfaced as a user-facing error.
- **Execute once, deliver until acknowledged.** `_handled` guards
  re-execution in-process and the SQLite `delegation_outbox` migration records
  the boundary before dispatch (mutating tools!). `_undelivered` retries the
  POST each sweep. After a restart, saved results are redelivered; an
  interrupted/ambiguous execution becomes an explicit error instead of being
  run twice. Authentication, permission, and malformed-acknowledgement failures
  retain the durable result for retry; only a validated server acknowledgement
  clears it.
- **Ownership is enforced twice.** Aidream's claim query admits only tools
  bound to executor `matrx-local`; this client independently requires
  `get_by_cloud_name(tool_name)` to resolve before dispatch. Foreign calls
  (browser/UI-first tools) are never claimed or executed here, and this client
  never posts an error for a tool that is not ours.
- **States, not errors.** No/expired JWT → idle with one INFO line per state
  transition (the app refreshes tokens; see MXL-D-046 — `TokenRepo.is_expired`
  decodes the JWT `exp` itself). Unreachable server → one WARN per
  transition, keep polling.
- **A continuation is only offered when NOTHING is outstanding.** A
  continuation is an invitation to POST `/resume`, and aidream refuses a
  resume with HTTP 409 `outstanding_delegated_calls` while any sibling call of
  that `user_request_id` is still delegated. The `user_request_id` does not
  change across a whole tool-using turn, so a continuation already served
  stays true-looking while the NEXT call of the same turn runs.
  `ui_conversation_state` therefore suppresses the continuation (and reports
  `outstanding`) while any call is `queued`/`executing`/`awaiting_user_review`,
  and `_note_call` drops the recorded continuation the moment a call enters
  one of those states. The retained `_undelivered` obligation re-establishes
  it on the next sweep, so nothing is stranded. This is the defect that ended
  the owner's run on 2026-09-22 (conversation 60b6f5e7…, call
  `toolu_01KCH6aM36tYXNk6CBExGmEZ`).
- **The resume body declares `surface: matrx-local/desktop` +
  `desktop-native`** so the continuation keeps this engine as an active
  executor — without it a re-delegated desktop tool is dropped pre-flight.
- **Client-side execution timeouts per mega-tool** (`EXECUTION_TIMEOUTS`,
  Shell 900 s / Web / Media / Audio 600 s / Browser 300 s, default 120 s).
  The server-side `expires_at` (30 days) is an abandonment TTL, not a
  deadline — do not "fix" slow tools by raising it.

## Local UI stream claims (Cloud Chat continuation ownership)

The desktop's own Cloud Chat page is a stream-attached client (like a web
tab), but it reaches this engine over loopback, so it gets a FIRST-CLASS
claim instead of racing the 2.5 s browser grace window:

- `POST /chat/delegation/ui-claim {conversation_id, ttl_seconds}` — the UI
  declares it owns `/resume` for that conversation. Re-claimed on every poll
  (`claim_ui_stream`); response doubles as the conversation state snapshot.
- While a claim is live, `_deliver` still executes + delivers results but
  SKIPS the headless resume (`resume_deferred_to_ui` event). The retained
  result obligation (`_undelivered`) re-evaluates each sweep, so an
  abandoned claim (closed window, crashed UI) self-heals into the normal
  headless resume once the TTL lapses — no new execution path, no new race.
- `GET /chat/delegation/conversation/{id}` — per-call state
  (executing/delivered) + pending continuation `{user_request_id, needed}`
  for the UI poller. `POST /chat/delegation/ui-release` on stream end.
- The UI never sends while one of its own calls is outstanding. The composer
  is held with `Running on this computer: <tools>…` from
  `localToolsRunningReason`, fed by a READ-ONLY poll of
  `GET /chat/delegation/conversation/{id}` (`readDelegationState`) that takes
  no continuation ownership. It fails OPEN: an engine that reports nothing —
  or cannot be reached, and is therefore running nothing — releases the
  composer. `sendMessage` carries the same check as a second lock.
- **The surface never draws a turn that is still running as over.** Whatever
  ends the stream — a refused resume, a dropped connection, a server error
  event, the person pressing Stop, a wait that gave up — the UI first asks
  whether the SERVER turn is still alive (`isTurnStillRunning`: this
  conversation's delegated calls on aidream, a READ that does not lease, plus
  this engine's own outstanding list). While it is, no failure, no "Stopped.",
  no completed reply: the surface says the turn is still running, keeps
  re-reading the conversation every 3 s until it has been quiet for three
  checks, and holds the composer. On 2026-09-22 the owner watched a "failed"
  turn go on delegating work to his Mac for nine minutes. Guards:
  `desktop/src/hooks/use-cloud-chat.honest-turn.test.ts` (no terminal status
  or error may be written without asking) and
  `desktop/src/lib/cloud-chat-delegation.test.ts`.
- A `/resume` refused with 409 `outstanding_delegated_calls` or
  `resume_conflict` is NOT a failed turn: the UI waits for the calls to land
  and posts it again (bounded, `MAX_RESUME_CONFLICT_RETRIES`). aidream files
  both as ordinary refusals; rendering one as a dead conversation is the bug.
- UI half: `desktop/src/hooks/use-cloud-chat.ts` (multi-segment stream loop:
  `tool_delegated` → claim → poll → `POST /resume` with the desktop client
  envelope). Pinned by `test_ui_claim_defers_resume_then_self_heals_on_release`.

## User-review calls — an authorization boundary, not an execution path

Some delegated tools are executed by NOBODY. `google_email_send` is the
category (`app/services/delegation/user_review.py`): it has **no server
executor anywhere in the platform**, and that absence IS the Gmail
authorization boundary — with no server path, an agent-authored
`user_confirmed` cannot exist, let alone authorize anything.

So the desktop must not dispatch it either. `sweep_once` intercepts a
`USER_REVIEW_TOOLS` call BEFORE `_resolve_tool` and **parks** it:

```
pending call (tool bound to matrx-local)
  → user-review tool?  → _park_review()   [no dispatcher, no outbox, no send]
  → GET  /chat/delegation/reviews          (desktop card reads the proposal)
  → user edits + clicks Send in <GmailReviewCard>
      → the CARD posts the reviewed bytes to aidream
        /api/google-workspace/gmail/send-reviewed with the user's own JWT
  → POST /chat/delegation/review-decision  (sent | declined | cancelled | error)
      → build_review_output() → the normal _deliver() path → /resume
```

Rules that must not erode:

- **The engine never sends mail.** It holds a proposal and turns a human
  decision into a tool result. The send lives in the card
  (`desktop/src/lib/google-workspace.ts`), which is where the reviewed bytes
  are — matching matrx-frontend exactly. Never add a "send" capability to the
  engine; a loopback endpoint that sends is an unattended send path.
- **Only the four human outcomes exist.** The route rejects anything else, and
  `normalize_review_arguments` keeps only `to/cc/subject/body` — an agent
  cannot smuggle a confirmation flag into the card.
- **Parking is never an answer.** A parked call is not `_handled`; nothing is
  delivered until a person decides. A review the server stops listing is
  DROPPED (`_prune_reviews`), never auto-answered. `resolve_review` pops the
  entry first, so a double click delivers once (404 the second time).
- **A review has no deadline.** `ui_conversation_state` reports
  `reviews_pending` so the UI's continuation wait
  (`waitForDelegatedContinuation`) extends its deadline instead of handing the
  conversation to the headless resume while a human is still reading.
- **The user's exposure gate still applies** — a disabled `google_email_send`
  is refused with an error result, not parked.

Desktop half: `desktop/src/hooks/use-email-reviews.ts` (poller + decision) and
`desktop/src/components/chat/GmailReviewCard.tsx` (the consent surface,
rendered above the Cloud Chat composer). Pinned by
`tests/unit/test_delegation_user_review.py` and
`tests/smoke/test_delegation_user_review_routes.py`.

Not this path: **attaching** a Google Doc/Sheet to a chat turn never reaches
the engine. The composer sends the registered file ids as the reserved
`context.__google_files` key and the SERVER reads them — see
`docs/CLOUD_CHAT_SURFACE.md` § Google Workspace on this surface.

## User tool exposure gate

Settings key `cloud_tools` (`{"disabled_tools": [<cloud_name>...]}`) —
cloud-authoritative via the app_settings whole-blob sync, editable in
Settings → Cloud & Account → Cloud Agent Tools (and from the web). Read
FRESH every sweep (`get_disabled_cloud_tools`); a disabled tool's delegated
call is answered with an explicit `is_error` result, never executed and
never silently dropped. Pinned by `tests/unit/test_delegation_disabled_tools.py`.

## Lifecycle

Managed service `delegation`, registered in `app/main.py` Phase 2f
(start) and the lifespan teardown (stop) via `app/launcher.py` — same shape
as `chat_sync`. Broadcast wake availability depends on Phase 7's
subscription; the poller is independent of it.

## Tests

`tests/unit/test_delegation_client.py` pins the full round-trip against
`httpx.MockTransport` (no network, no engine boot): sweep→execute (real
dispatcher)→tool_results→resume, error results, foreign-tool skip, dedup,
409 handling, undelivered/result-obligation retry, fatal resume-stream errors,
browser-first grace, and idle-without-credentials.
