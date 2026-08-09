# Scraper — the local execution lane

There is ONE scraper engine: the `matrx-scraper` package (CLAUDE.md Hard Rule 1).
This directory is not an engine — it is the lane that runs that engine from the
user's own machine and residential IP (`use_proxy=False`, always), plus the
plumbing that moves results between this machine and the server.

| File | Role |
|---|---|
| `engine.py` | Runs the package's orchestrator locally |
| `scrape_store.py` | The dual write: local SQLite, then cloud |
| `remote_client.py` | HTTP client for `scraper.app.matrxserver.com` |
| `retry_queue.py` | Polls the server for URLs it wants us to scrape |
| `auth_helper.py` | The signed-in user's JWT for background calls |

---

## The dual write, and what "unsynced" means

Every successful scrape is written twice: local SQLite first (must succeed, is
the user's copy forever), then pushed to the server so the web app and every
other device sees it. `cloud_sync_status` tracks the second half.

**A push that has not happened is not a push that failed.** This distinction is
the entire design of `scrape_store`, and it exists because it was missing:

> Observed 2026-08-09 on a real machine — `GET /scrapes/sync-status` returned
> `{total: 8, synced: 1, pending: 1, failed: 6}`. Three causes, one shared
> defect. `save_content` never sent the `page_name` the server's
> `ContentSaveRequest` marks **required**, so every push since the 2026-04-29
> `/api/v1` → `/api/scraper` migration answered 422 and the dual write was
> silently dead for three months. Alongside it, a single 502 gateway blip and
> three pushes made while signed out had each burned all five retries and
> parked good rows in terminal `failed` — for conditions that resolve
> themselves. Nothing told the user any of it.

So there are exactly three outcomes for a push, and `classify_push_error`
decides which:

| Outcome | When | Status | Retry budget |
|---|---|---|---|
| `pushed` | 2xx | `synced` | — |
| `deferred` | no signed-in user, 401/403, 408/425/429, any 5xx, any `httpx.TransportError` | stays `pending` + `cloud_sync_blocked_reason` | **untouched** |
| `failed` | any other 4xx, or an exception we cannot name | `failed`, attempts +1 | spent |

**Only a genuine rejection may spend the retry budget.** A user who was signed
out for a week, or whose network was down, must not return to a permanently
failed backlog. An *unrecognised* exception counts as a rejection on purpose —
the budget exists to stop a pathologically broken row from retrying forever,
and an error nobody has named is exactly that until someone names it.

### Recovery

`reset_pending_failed()` revives failed rows still under `_MAX_AUTO_RETRIES`;
that runs on every engine start. `reset_pending_failed(include_terminal=True)`
also revives rows that exhausted the budget **and zeroes their counter** — the
recovery path for the case a counter cannot represent: the rejections were
real, but the client that provoked them has since been fixed. It is reachable
only from an explicit trigger (`POST /scrapes/sync`, or `sync_after_sign_in`),
never from the background loop.

`POST /auth/token` calls `sync_after_sign_in()`, because signing in is
literally the blocker clearing for every auth-deferred row.

---

## States, not errors — what the user sees

`get_sync_summary()` reduces the counts to ONE state, a plain-language message,
and the action that clears it. `GET /scrapes/sync-status` returns it verbatim
and `ScrapeSyncBanner` on the Scraping page renders it.

| `state` | `action` | Meaning |
|---|---|---|
| `synced` | `none` | Nothing outstanding — the banner renders nothing |
| `signed_out` | `sign_in` | The engine has no usable JWT. One click pushes the live Supabase token back to the engine, which drains the backlog |
| `offline` | `none` | Cloud unreachable; fixes itself |
| `rejected` | `retry` | The only true failure — retry still offered |
| `queued` | `none` | Waiting on the next background pass |

`cloud_sync_error` holds a raw `httpx` string. It is a diagnostic **for us** and
must never reach the UI — the state and message are what the user reads.

Ordering is by what the user can act on: a signed-out user is told to sign in
even if the cloud is also flaky, because signing in is the step they own.

---

## Payload contracts with the server

The server's request models live in `matrx_scraper.api.ext_router` — the same
package this repo depends on, so they are **importable here and can be the
judge in a test**. `tests/unit/test_scrape_cloud_sync.py` validates the real
outgoing `save_content` body against the server's own `ContentSaveRequest`.

Do this for every endpoint you add. A hand-rolled fake accepts whatever you
send it, which is precisely how a missing required field shipped and 422'd for
three months without a single failing test. Known outstanding mismatch of the
same class: **MXL-D-076** (`/queue/submit` silently discards the content the
desktop sends).

---

## Result shape

Consumers read the package's `ScrapeResult` — `success` / `failure_reason`,
never a `status` string or an `error` field (CLAUDE.md Hard Rule 2). Anything
persisted or pushed goes through `scrape_store.content_from_result`, the ONE
place a result becomes a content dict; `STORED_FIELDS` names real
`ScrapeResult` fields and crashes at call time if one is renamed away.
