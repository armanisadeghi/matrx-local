# Coding Sessions — delivery throughput (the last open item)

**Owner:** the "Work" session, put in charge of the whole Claude Code capture /
sync / Coding Sessions effort by Arman on 2026-09-12 (three sessions had been
shipping into the same files with no lead: "Work", "Claude Code Plugins",
"Claude Code sync"). **Rule:** finish the open item below, verify on the
installed app, then delete this file. Never run a release script by hand —
Arman's instruction 2026-09-12: committed and verified on localhost is done.

## What is DONE and verified (do not redo)

| Piece | Where | Proof |
|---|---|---|
| Web: three conversation buckets, full account emails, Claude pins as stars | matrx-frontend `7257690791` (+ favorites RPC `cvx_list_scoped_canonical_favorites`) | Live on aimatrx.com 2026-09-08 |
| Server: bridge + identity list never demand an org header; org self-resolved server-side | aidream `797ec1520`, `ownership.resolve_coding_session_organization_id` | `GET /api/coding-sessions/sessions` → 200 without header, 2026-09-08 |
| Desktop: cloud-judged Coding Sessions screen, honest labels, diagnosis dialogs, pinned column | matrx-local `9c3026d61`, `33756565d`, `aa2c8f76d`, `25eaf78d4` | Installed 1.4.89: cloud.checked=true, 1,482 in cloud, 61 changed, 438 not in cloud |
| Desktop: engine asks the desktop for a fresh session; every silent pause is a blocker | `7cacf502a`, `app/services/session_freshness.py` | Blocker showed on 1.4.87, cleared on 1.4.89 after desktop push |
| Desktop: keychain DEK failures retry with backoff and log their cause; helper budget 45 s | `0778745f2` | In 1.4.88+ |
| Desktop: index warm-up at start, cap 250k, transcript-only rows, title_sync write gate, label reconcile on a timer | `c4e7bfc63`, `25eaf78d4`, `fffbb839e`, `0a1047617` | Unit suites green |
| Tests never read the real Claude home | `0c3baf183` (tests/conftest.py autouse fixture) | 41 tests 5 s (was 10 min + failures) |
| Plugins (other session, holding): shared-checkout git guard | matrx-claude-plugin `5c056f2` (0.2.0-alpha.10), matrx-codex-plugin `d2e6447` (0.2.0-alpha.9) | 316 checks green per that session |
| Sync scripts (other session, now gone): `~/.claude/sync-claude-code-sessions.py` + `CLAUDE-CODE-SESSION-SYNC.md` updated 2026-09-11 23:05 | local files | documented in that .md |

## The open item — publisher throughput + tick observability

Measured on the installed app 1.4.89, 2026-09-12 09:33 PDT: ~193,000 queued
envelopes (Codex hook events, ~1,160 lanes), drained sequentially at ~0.7/s
while Codex hooks enqueue up to ~4/s. Earlier the same morning the publisher
idled for minutes with eligible rows and nothing on the status endpoint said so.

MERGED to main 2026-09-12 (commits b22d175b4, 9ced9caa2, 330758c45, 3f089c2aa; 18 publisher
tests, 81 across the surface, two independent review rounds): bounded concurrency across lanes
(knob `coding_session_delivery_concurrency`, default 8, clamp 1–32, strict
per-lane order kept), per-tick observability (`publisher.ticks` in
`GET /coding-session/status`, a one-line INFO log when a tick sends nothing
with eligible rows, one compact line on the Coding Sessions page).

Live verification still owed by whoever runs after the next release reaches the installed app:
1. `GET /coding-session/status` (loopback bearer) → `publisher.ticks.last_tick_at`
   within the poll interval, `transport_circuit.config.delivery_concurrency` = 8.
2. Outbox count (`select count(*) from coding_session_bridge_outbox`) falls by
   ≥ 4/s while the server is healthy; acknowledgements advance in
   `coding_session_bridge_delivery_activity`.
3. No quarantine growth beyond the known 195 (88 entry_mutated, 78
   provider_account_conflict, 29 http_409).

## Known, not this lane's

- Server `coding_session_bridge_failed` in `ownership.resolve_coding_session_organization_id`
  (1 in 30 min on 2026-09-12) — rare 500, retried by the client; worth a look by
  whoever next touches aidream ownership.
