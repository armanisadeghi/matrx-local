"""Retry queue background service.

The remote scraper server automatically enqueues URLs it failed to scrape
(Cloudflare blocks, IP bans, etc.) for the desktop app to retry using the
user's residential IP.

This module runs a background asyncio task that:
  1. Polls GET /api/scraper/queue/pending every 30s
  2. Claims items (10-min TTL)
  3. Scrapes each URL locally via the ScraperEngine
  4. On success → POST /api/scraper/queue/submit  (content stored in server DB)
     On success → also save_content() directly (belt-and-suspenders)
  5. On failure → POST /api/scraper/queue/fail    (promotes to Chrome ext tier)

All remote calls forward the active user's Supabase JWT so writes are
attributed to the real user. If no user is logged in, the cycle is a
no-op until they sign in.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.scraper.auth_helper import get_active_user_token
from app.services.scraper.remote_client import (
    RemoteScraperOrganizationError,
    get_remote_scraper,
)

logger = logging.getLogger(__name__)

# Stable client ID for this machine (generates once per engine run)
_CLIENT_ID = str(uuid.uuid4())

_POLL_INTERVAL = 30          # seconds between polls (healthy cadence)
_MAX_BACKOFF   = 600         # 10 min — cap for exponential backoff on failure
_DEGRADE_AFTER = 3           # consecutive remote failures before DEGRADED
_BATCH_LIMIT   = 5           # max items to claim per poll
_CLAIM_TIMEOUT = 9 * 60      # 9 min — safely inside the server's 10-min TTL
_TERMINAL_RECHECK = 60       # seconds between "has the signed-in user changed?" checks

# Registry service name. Deliberately NOT "scraper" — local scraping keeps
# working when the REMOTE retry-queue endpoint (scraper.app.matrxserver.com) is
# unreachable, so flipping the local "scraper" service to DEGRADED would itself
# be an untruthful registry state. This tracks the remote retry pipeline only.
_REGISTRY_NAME = "scraper_retry_queue"

# Status codes that are a CONTRACT refusal rather than an outage. A 4xx is the
# server saying "this request is wrong / not allowed", which no amount of
# client-side retry can out-wait — the exact SR-04 trap: 8 `→ degraded` marks
# and a poll every 240s for 22+ hours against an endpoint that answered 400
# every single time. The three exceptions are genuinely retryable 4xx codes.
_RETRYABLE_4XX = frozenset({408, 425, 429})


@dataclass(frozen=True)
class PollFailure:
    """Why a poll cycle failed, and whether waiting can ever fix it."""

    summary: str
    terminal: bool = False
    remedy: str = ""
    http_status: int | None = None
    server_code: str = ""


def _server_words(exc: httpx.HTTPStatusError) -> tuple[str, str]:
    """(code, remedy) as the server itself stated them.

    matrx-connect hoists its refusal detail to the response root:
    ``{error, code, message, user_message, ...}``. Reading the server's own
    sentence is the difference between "remote retry queue unreachable" and
    "send the X-Organization-Id header naming the organization you are acting
    in" — the second one is actionable, and it was always on the wire.
    """
    try:
        body = exc.response.json()
    except Exception:  # noqa: BLE001 — a non-JSON body simply has no words
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    detail = body.get("detail")
    if isinstance(detail, dict):  # pre-hoist shape, e.g. a raw HTTPException
        body = {**detail, **{k: v for k, v in body.items() if k != "detail"}}
    code = body.get("code") or body.get("error") or ""
    remedy = body.get("user_message") or body.get("message") or ""
    return (str(code) if code else ""), (str(remedy) if remedy else "")


def _classify(exc: Exception, what: str) -> PollFailure:
    """Turn a remote-call exception into a transient-or-terminal verdict."""
    if isinstance(exc, RemoteScraperOrganizationError):
        return PollFailure(
            summary=f"{what}: {exc}",
            terminal=True,
            remedy=exc.remedy,
            server_code="organization_unresolved",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if 400 <= status < 500 and status not in _RETRYABLE_4XX:
            code, remedy = _server_words(exc)
            return PollFailure(
                summary=f"{what}: HTTP {status}"
                + (f" {code}" if code else "")
                + f" from {exc.request.url}",
                terminal=True,
                remedy=remedy
                or "The scraper server refused this request permanently and sent no "
                "reason with it; this needs a developer, not a retry.",
                http_status=status,
                server_code=code,
            )
    return PollFailure(summary=f"{what}: {exc}")


_task: asyncio.Task[None] | None = None
_running = False
_stats: dict[str, int] = {"polled": 0, "claimed": 0, "submitted": 0, "failed": 0}


def get_stats() -> dict[str, Any]:
    return {**_stats, "running": _running, "client_id": _CLIENT_ID}


async def _acting_user_id() -> str | None:
    """The signed-in user this poller is acting for, or None when signed out.

    Identity, not the token: a token refresh every hour is the same caller, so
    a terminal refusal must not re-arm on it.
    """
    token = await get_active_user_token()
    if not token:
        return None
    from app.services.aidream.organization import jwt_user_id

    return jwt_user_id(token)


async def _scrape_locally(url: str) -> dict[str, Any] | None:
    """Try to scrape a URL using the local ScraperEngine.

    Returns a content dict suitable for save_content() / queue/submit,
    or None if scraping failed.
    """
    try:
        from matrx_scraper.scrape_options import ScrapeOptions

        # Import here to avoid circular imports; engine is already running
        from app.services.scraper.engine import LocalScrapeOptions, get_scraper_engine
        from app.services.scraper.scrape_store import content_from_result

        engine = get_scraper_engine()
        if not engine.is_ready:
            logger.debug("RetryQueue: engine not ready, skipping local scrape of %s", url)
            return None

        # No cache: the server queued this URL precisely because ITS attempt
        # failed, so answering from our own recent copy would defeat the point
        # of the hand-off.
        page = await engine.scrape_one(
            url,
            LocalScrapeOptions(
                fields=ScrapeOptions(
                    get_text_data=True, get_links=True, get_overview=True
                ),
                use_cache=False,
            ),
        )

        if not page.success:
            logger.debug(
                "RetryQueue: local scrape of %s failed: %s (status=%s firewall=%s)",
                url, page.failure_reason, page.status_code, page.firewall,
            )
            return None

        return content_from_result(page)
    except Exception as exc:
        logger.error(
            "RetryQueue: local scrape of %s raised unexpected error: %s",
            url, exc, exc_info=True,
        )
        return None


async def _poll_once() -> PollFailure | None:
    """Single poll cycle: fetch → claim → scrape → submit/fail.

    Returns ``None`` when the cycle completed (or was a legitimate no-op:
    unconfigured, no signed-in user, no pending items) and a ``PollFailure``
    when a REMOTE call failed — the loop uses that to drive exponential
    backoff and the DEGRADED registry state. Per-item submit/report failures
    are NOT treated as cycle-level failures (they don't indicate the remote
    queue endpoint is down).
    """
    client = get_remote_scraper()
    if not client.is_configured:
        return None

    # Fetch the active user's JWT once per cycle. Without it the server
    # can't attribute writes to a real user (and once auth is enforced
    # server-side, every queue call would 401). When no user is signed
    # in, this poll cycle is a no-op until they sign in.
    auth_token = await get_active_user_token()
    if not auth_token:
        logger.debug("RetryQueue: no active user token; skipping cycle")
        return None

    try:
        resp = await client.get_pending(
            tier="desktop", limit=_BATCH_LIMIT, auth_token=auth_token,
        )
        items: list[dict[str, Any]] = resp.get("items", [])
    except Exception as exc:
        # Logged at DEBUG here; the loop escalates to a single WARNING +
        # DEGRADED after sustained failures so we don't flood the log with an
        # ERROR every 30s when the remote server is down.
        logger.debug("RetryQueue: get_pending failed: %s", exc)
        return _classify(exc, "get_pending failed")

    if not items:
        return None

    _stats["polled"] += len(items)
    ids = [item["id"] for item in items]

    try:
        await client.claim_items(
            item_ids=ids,
            client_id=_CLIENT_ID,
            client_type="desktop",
            auth_token=auth_token,
        )
        _stats["claimed"] += len(ids)
    except Exception as exc:
        logger.debug("RetryQueue: claim_items failed: %s", exc)
        return _classify(exc, "claim_items failed")

    for item in items:
        item_id: str = item["id"]
        url: str = item.get("target_url", "")
        if not url:
            continue

        logger.info("RetryQueue: retrying %s locally", url)
        content = await _scrape_locally(url)

        if content and (content.get("text_data") or content.get("ai_research_content")):
            char_count = len(content.get("text_data", "") + content.get("ai_research_content", ""))
            try:
                await client.submit_result(
                    queue_item_id=item_id,
                    url=url,
                    content=content,
                    content_type="html",
                    char_count=char_count,
                    auth_token=auth_token,
                )
                _stats["submitted"] += 1
                logger.info("RetryQueue: submitted %s (chars=%d)", url, char_count)

                # Belt-and-suspenders: also save directly
                try:
                    await client.save_content(
                        url=url,
                        content=content,
                        content_type="html",
                        char_count=char_count,
                        auth_token=auth_token,
                    )
                except Exception as exc:
                    logger.warning("RetryQueue: save_content backup failed for %s: %s", url, exc)

            except Exception as exc:
                logger.warning("RetryQueue: submit_result failed for %s: %s", url, exc)
                _stats["failed"] += 1
        else:
            reason = f"local scrape returned no content for {url}"
            logger.info("RetryQueue: local scrape failed for %s, promoting to extension", url)
            try:
                await client.report_failure(
                    queue_item_id=item_id,
                    error=reason,
                    promote_to_extension=True,
                    auth_token=auth_token,
                )
                _stats["failed"] += 1
            except Exception as exc:
                logger.warning("RetryQueue: report_failure failed: %s", exc)


async def _loop() -> None:
    global _running
    _running = True
    logger.info("RetryQueue: background poller started (interval=%ds)", _POLL_INTERVAL)

    from app.launcher import get_registry
    registry = get_registry()
    # Baseline state so the retry pipeline shows up in /admin/status.
    registry.ready(_REGISTRY_NAME)

    delay = _POLL_INTERVAL
    consecutive_failures = 0
    degraded = False
    # 🚨 A PERMANENT REFUSAL IS A STATE, NOT A RETRY SCHEDULE (SR-04).
    # When the server answers 4xx, waiting changes nothing: this poller sat in
    # DEGRADED for 22+ hours re-asking an endpoint that had already given its
    # verdict, and the one thing it never did was say what the verdict was. So
    # a terminal refusal parks the service in FAILED with the server's own
    # remedy attached, and stops polling. It re-arms for a DIFFERENT signed-in
    # user (the refusal is about this caller's request, so a new caller is a
    # new question) — never on a timer.
    terminal_for_user: str | None = None

    try:
        while True:
            if terminal_for_user is not None:
                if await _acting_user_id() == terminal_for_user:
                    await asyncio.sleep(_TERMINAL_RECHECK)
                    continue
                logger.info(
                    "RetryQueue: signed-in user changed — re-arming after the "
                    "permanent refusal recorded for the previous user"
                )
                terminal_for_user = None
                registry.ready(_REGISTRY_NAME)
                consecutive_failures = 0
                degraded = False
                delay = _POLL_INTERVAL

            try:
                failure = await _poll_once()
            except Exception as exc:
                logger.debug("RetryQueue: unexpected error in poll cycle: %s", exc)
                failure = _classify(exc, "unexpected error in poll cycle")

            if failure and failure.terminal:
                terminal_for_user = await _acting_user_id()
                logger.error(
                    "RetryQueue: the scraper server refused this permanently — "
                    "%s. WHAT TO DO: %s. Polling stopped (it cannot succeed by "
                    "repeating); it resumes if a different user signs in.",
                    failure.summary, failure.remedy,
                )
                registry.failed(
                    _REGISTRY_NAME,
                    f"remote retry queue refused permanently: {failure.summary}",
                    remedy=failure.remedy,
                    http_status=failure.http_status,
                    server_code=failure.server_code,
                    terminal=True,
                    retrying=False,
                )
                degraded = False
                consecutive_failures = 0
                continue

            if failure:
                consecutive_failures += 1
                # Exponential backoff: 30s → 60 → 120 … capped at 10 min so a
                # sustained remote outage stops hammering the server (and the
                # log) every 30s.
                delay = min(_POLL_INTERVAL * (2 ** consecutive_failures), _MAX_BACKOFF)
                if consecutive_failures == _DEGRADE_AFTER:
                    logger.warning(
                        "RetryQueue: remote queue unreachable %d times in a row "
                        "(%s) — backing off to %ds and marking degraded",
                        consecutive_failures, failure.summary, delay,
                    )
                    registry.degraded(
                        _REGISTRY_NAME,
                        reason=f"remote retry queue unreachable: {failure.summary}",
                    )
                    degraded = True
            else:
                if degraded:
                    logger.info("RetryQueue: remote queue reachable again — recovered")
                    registry.ready(_REGISTRY_NAME)
                    degraded = False
                consecutive_failures = 0
                delay = _POLL_INTERVAL

            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        logger.info("RetryQueue: poller stopped")
    finally:
        _running = False


def start() -> None:
    """Start the background retry queue poller. Call once on engine startup."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="retry-queue-poller")


def stop() -> None:
    """Cancel the background poller. Call on engine shutdown.

    Prefer ``stop_async`` from async contexts — fire-and-forget cancellation
    can leave a pending task when the loop closes ("Task was destroyed but it
    is pending") and skips the task's finally blocks.
    """
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None


async def stop_async(timeout: float = 3.0) -> None:
    """Cancel the background poller and await its completion."""
    global _task
    task = _task
    _task = None
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
