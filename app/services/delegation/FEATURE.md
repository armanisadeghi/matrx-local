# Cloud tool-call delegation client (suspend/resume, headless)

**What this is.** The desktop half of the platform's client-tool
suspend/resume protocol. When a cloud agent turn (web app, extension, any
surface) calls a tool bound to executor `matrx-local`, aidream hard-suspends
the turn (`chat.tool_call` row `status='delegated'`) and ends the stream.
THIS service makes the desktop notice, execute, deliver, and resume — with
no UI involved.

Canonical protocol doc (wire contract, anti-patterns — read it before
touching this code):
`matrx-frontend/features/agents/docs/CLIENT_TOOL_SUSPEND_RESUME.md`.
matrx-extend's client half (`src/lib/tools/dispatch.ts`) is the reference
implementation for a stream-attached client; this service is the
headless/poll variant.

## The pipeline

```
GET /ai/user/pending_calls  (user JWT, every MATRX_DELEGATION_POLL_INTERVAL s)
  → filter: tool_name resolves in app.tools.catalog.get_by_cloud_name
  → execute via app.tools.dispatcher.dispatch(entry.dispatcher_name, args)
  → POST /ai/conversations/{id}/tool_results  (duration_ms always sent)
  → continuation_needed=true → POST /ai/conversations/{id}/resume
  → DRAIN the continuation stream, then sweep again (re-entrancy)
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
- **Always read `continuation_needed`** from the tool_results response and
  resume when true. Skipping this strands the conversation.
- **Single-flight resume per `user_request_id`** (10 s suppression +
  bounded `resume_conflict` retry: 700 ms × attempt, max 4). Parallel
  delegated calls can both report `continuation_needed=true`; two racing
  resumes caused the 2026-06-09 incident. `outstanding_delegated_calls` and
  `not_resumable` 409s are benign — never retried, never surfaced as errors.
- **Execute once, deliver until acknowledged.** `_handled` guards
  re-execution (mutating tools!); `_undelivered` retries the POST each
  sweep. A hard 4xx (server doesn't know the call) is dropped loudly.
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
409 handling, undelivered retry, idle-without-credentials.
