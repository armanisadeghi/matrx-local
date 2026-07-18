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

Delegation is **USER-scoped, not instance-scoped**: `GET /ai/user/pending_calls`
returns every delegated call for the account and the ledger has no executor
column, so a call is claimed by whichever `matrx-local` engine recognizes the
tool name. Two engines on one account (the installed app + a source-run dev
engine) therefore **race for the same call** — double-execution and 409 resume
races that hand back false pass/fail signals during live testing.

Guard: `app.config.CLOUD_PARTICIPATION_ENABLED` (env `MATRX_CLOUD_PARTICIPATION`,
default `1`). When `0`, this engine is **coordination-silent** — Phase 2f
(delegation client) is not started and Phase 7 / `connect_broadcast` do not
attach to the per-user bridge channel, so it never claims cloud-dispatched work.
`run.py`'s dev isolation defaults source-run engines to `0`; the packaged app
leaves it `1`. A dev engine is still fully usable over its own loopback (a dev
frontend on the same machine reaches it directly). Deliberately opting a dev
engine INTO the shared channel (`MATRX_CLOUD_PARTICIPATION=1`) currently makes
it race the installed app — instance-scoped targeting (so the frontend can pick
which desktop drives) is the planned Tier 2 follow-up.

Tier 2 (instance-scoped targeting so a dev desktop and the installed app never
claim each other's work — reusing `cx_conversation.app_instance_id` from the
compute-target picker): design in
[docs/TIER2_DESKTOP_INSTANCE_TARGETING.md](../../../docs/TIER2_DESKTOP_INSTANCE_TARGETING.md).
The client already sends `?instance_id=` on the poll (forward-compatible).

## The pipeline

```
GET /ai/user/pending_calls  (user JWT, every MATRX_DELEGATION_POLL_INTERVAL s)
  → filter: tool_name resolves in app.tools.catalog.get_by_cloud_name
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
   new server surface.
2. **Broadcast wake (latency).** aidream publishes `kind:"wake"`,
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
  run twice. A hard 4xx (server doesn't know the call) is dropped loudly.
- **Ownership by tool name.** `chat.tool_call` has NO executor column; a
  pending call is ours iff `get_by_cloud_name(tool_name)` resolves. Foreign
  calls (browser/ui-first tools) are silently left for their owner — never
  post an error for a tool that isn't ours.
- **States, not errors.** No/expired JWT → idle with one INFO line per state
  transition (the app refreshes tokens; see MXL-D-046 — `TokenRepo.is_expired`
  decodes the JWT `exp` itself). Unreachable server → one WARN per
  transition, keep polling.
- **The resume body declares `surface: matrx-local/desktop` +
  `desktop-native`** so the continuation keeps this engine as an active
  executor — without it a re-delegated desktop tool is dropped pre-flight.
- **Client-side execution timeouts per mega-tool** (`EXECUTION_TIMEOUTS`,
  Shell 900 s / Web / Media / Audio 600 s / Browser 300 s, default 120 s).
  The server-side `expires_at` (30 days) is an abandonment TTL, not a
  deadline — do not "fix" slow tools by raising it.

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
