"""Scrape persistence layer — dual-write to local SQLite AND remote server.

Every successful scrape is written here. The rule is simple:
  1. Write to local SQLite first (always succeeds, survives forever).
  2. Push to remote server in the background (fire-and-forget; failures are
     queued and retried on the next engine startup and periodically).

Nothing is ever truly deleted unless the user explicitly confirms twice.
Soft-delete marks is_deleted=1; hard delete is a separate admin action.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# How many GENUINE server rejections before we stop retrying automatically.
# Only a rejection that will read the same on the next attempt spends this
# budget — see `classify_push_error`.
_MAX_AUTO_RETRIES = 5

# Background cloud-push retry interval (seconds)
_SYNC_INTERVAL = 120

# Blocked reasons — a push that cannot happen YET. Neither is a failure: the
# row stays queued, keeps its full retry budget, and syncs the moment the
# blocker clears.
BLOCKED_AUTH = "auth"        # nobody signed in, or the server refused the JWT
BLOCKED_OFFLINE = "offline"  # network down, or the server is unreachable/5xx

# Blocked reason → the state the user is actually in, and what fixes it. The
# frontend renders these; it never renders `cloud_sync_error`, which is a raw
# httpx string meant for us.
BLOCKED_STATES: dict[str, dict[str, str]] = {
    BLOCKED_AUTH: {
        "state": "signed_out",
        "message": (
            "Sign in to sync your scrapes to the cloud. They are saved on this "
            "computer and will upload automatically once you do."
        ),
        "action": "sign_in",
    },
    BLOCKED_OFFLINE: {
        "state": "offline",
        "message": (
            "The cloud is unreachable right now. Your scrapes are saved on this "
            "computer and will upload automatically when the connection is back."
        ),
        "action": "none",
    },
}

# HTTP statuses that say "ask again later", not "this payload is wrong".
_RETRY_AFTER_STATUSES = frozenset({408, 425, 429})

# Distinguishes "the caller did not resolve a token" from "the caller resolved
# one and there is none" — the latter must not trigger a second lookup.
_UNSET_TOKEN: Any = object()


def classify_push_error(exc: BaseException) -> str | None:
    """Return a blocked reason for a deferrable push failure, else ``None``.

    ``None`` means the server genuinely REJECTED this row and will reject it
    again — the only thing that may spend the retry budget.

    The distinction is the whole point: an unauthenticated or offline push is
    a STATE that resolves itself, and burning a retry on it strands a perfectly
    good row in terminal 'failed' for a reason that fixed itself minutes later.
    An unrecognized exception counts as a rejection on purpose — the budget
    exists to stop a pathologically broken row from retrying forever, and an
    error we cannot name is exactly that until someone names it.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return BLOCKED_AUTH
        if status in _RETRY_AFTER_STATUSES or status >= 500:
            return BLOCKED_OFFLINE
        return None
    # ConnectError, ReadTimeout, ConnectTimeout, ProxyError, … all derive from
    # TransportError — no reachable server means no verdict on the payload.
    if isinstance(exc, httpx.TransportError):
        return BLOCKED_OFFLINE
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _page_name_for_url(url: str) -> str:
    """The engine's own page key, so a local row matches the server's row.

    `unique_page_name` is the cache/dedupe identity the whole platform uses;
    deriving our own would make the same page two different pages depending on
    which side scraped it.
    """
    try:
        from matrx_scraper.utils.url import get_url_info

        return get_url_info(url).unique_page_name
    except Exception:
        import hashlib
        return hashlib.sha256(url.encode()).hexdigest()[:40]


def _domain_for_url(url: str) -> str:
    try:
        from matrx_scraper.utils.url import get_url_info

        return get_url_info(url).full_domain
    except Exception:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc or url
        except Exception:
            return ""


# What a stored/pushed scrape carries.
#
# These are field names on the package's `ScrapeResult` — NOT a parallel schema
# invented here. Serialisation goes through the package's own `to_dict()`, so
# there is exactly one definition of what each field means and one place a
# rename can happen; `_assert_known_fields` below turns a drifted name into an
# import-time crash instead of a column that silently stores null forever.
#
# We store a SUBSET rather than the whole result on purpose: `ScrapeResult` also
# carries organized_data, tables, code_blocks, per-header markdown and the full
# element inventory. Those are large, they are re-derivable from the page, and
# writing them into every user's SQLite (and pushing them to the server on every
# scrape) is real cost for content nothing reads today. Add a field here the day
# something reads it.
STORED_FIELDS: tuple[str, ...] = (
    "text_data",
    "ai_research_content",
    "title",
    "overview",
    "links",
    "main_image",
    "hashes",
    "cms",
    "firewall",
    "status_code",
    "scraped_at",
)


def _assert_known_fields() -> None:
    from dataclasses import fields as dataclass_fields

    from matrx_scraper.orchestrator import ScrapeResult

    known = {f.name for f in dataclass_fields(ScrapeResult)}
    unknown = [name for name in STORED_FIELDS if name not in known]
    if unknown:
        raise RuntimeError(
            f"scrape_store.STORED_FIELDS names fields that no longer exist on "
            f"matrx_scraper ScrapeResult: {unknown}. Fix the names — do not "
            f"drop them silently."
        )


def content_from_result(result: Any) -> dict[str, Any]:
    """Turn a matrx_scraper `ScrapeResult` into the stored/pushed content dict."""
    _assert_known_fields()
    payload = result.to_dict()
    content = {name: payload.get(name) for name in STORED_FIELDS}
    # Both the local reader and the server's content schema key on these two,
    # so they are always present as strings. Non-HTML scrapes (PDF, image,
    # JSON) carry their extraction in `raw_text`.
    content["text_data"] = content.get("text_data") or result.raw_text or ""
    content["ai_research_content"] = (
        content.get("ai_research_content") or content["text_data"]
    )
    return content


# ---------------------------------------------------------------------------
# Local SQLite operations
# ---------------------------------------------------------------------------

async def save_locally(
    url: str,
    content: dict[str, Any],
    content_type: str = "html",
    user_id: str = "",
) -> str:
    """Write a scrape result to local SQLite. Returns the new row ID.

    This is the primary write path — it must always succeed.
    cloud_sync_status starts as 'pending' until the background push confirms.
    """
    from app.services.local_db.database import get_db

    page_name = _page_name_for_url(url)
    domain = _domain_for_url(url)
    char_count = len(content.get("text_data", "") + content.get("ai_research_content", ""))
    row_id = str(uuid.uuid4())
    now = _now_iso()

    db = get_db()
    try:
        # Mark any existing active rows for this page_name as superseded
        # (soft-keep them, but flag that they're not the latest)
        # We do this by leaving them in place — the latest scraped_at wins on read.

        await db.execute(
            """
            INSERT INTO scrape_pages
                (id, url, page_name, domain, content, char_count, content_type,
                 scraped_at, cloud_sync_status, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                row_id, url, page_name, domain,
                json.dumps(content, default=str),
                char_count, content_type, now, user_id,
            ),
        )
        await db.commit()
        logger.info(
            "[scrape_store] Saved locally: %s (id=%s, chars=%d)", url, row_id, char_count
        )
        return row_id
    except Exception as exc:
        logger.error("[scrape_store] LOCAL WRITE FAILED for %s: %s", url, exc, exc_info=True)
        raise


async def mark_cloud_synced(row_id: str) -> None:
    from app.services.local_db.database import get_db
    db = get_db()
    await db.execute(
        """
        UPDATE scrape_pages
        SET cloud_sync_status = 'synced', cloud_sync_at = ?, cloud_sync_error = NULL,
            cloud_sync_blocked_reason = NULL
        WHERE id = ?
        """,
        (_now_iso(), row_id),
    )
    await db.commit()


async def mark_cloud_failed(row_id: str, error: str) -> None:
    """Record a GENUINE server rejection. This is the only path that spends
    the retry budget — call it only for errors `classify_push_error` returns
    ``None`` for."""
    from app.services.local_db.database import get_db
    db = get_db()
    await db.execute(
        """
        UPDATE scrape_pages
        SET cloud_sync_status = 'failed',
            cloud_sync_error = ?,
            cloud_sync_blocked_reason = NULL,
            cloud_sync_attempts = cloud_sync_attempts + 1
        WHERE id = ?
        """,
        (error[:500], row_id),
    )
    await db.commit()


async def mark_cloud_deferred(row_id: str, reason: str, detail: str) -> None:
    """Record that the push cannot happen YET.

    The row stays 'pending' — it is still queued, which is the truth — and
    `cloud_sync_attempts` is deliberately NOT incremented. A user who was
    signed out for a week must not come back to a permanently failed backlog.
    """
    from app.services.local_db.database import get_db
    db = get_db()
    # `IS NOT` is SQLite's null-safe comparison: re-recording an unchanged
    # blocker writes nothing. A signed-out machine with a large backlog
    # re-derives the same verdict every two minutes forever, and that must not
    # be a write per row per pass.
    await db.execute(
        """
        UPDATE scrape_pages
        SET cloud_sync_status = 'pending',
            cloud_sync_blocked_reason = ?,
            cloud_sync_error = ?
        WHERE id = ?
          AND (cloud_sync_status IS NOT 'pending'
               OR cloud_sync_blocked_reason IS NOT ?
               OR cloud_sync_error IS NOT ?)
        """,
        (reason, detail[:500], row_id, reason, detail[:500]),
    )
    await db.commit()


async def clear_blocked_reason(reason: str) -> int:
    """Clear one blocked reason across the backlog — the blocker is gone.

    Called when the condition demonstrably changed (e.g. the user just signed
    in), so the next push pass reports a fresh verdict instead of showing a
    stale "you are signed out" banner to someone who is signed in.
    """
    from app.services.local_db.database import get_db
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE scrape_pages
        SET cloud_sync_blocked_reason = NULL, cloud_sync_error = NULL
        WHERE cloud_sync_blocked_reason = ? AND is_deleted = 0
        """,
        (reason,),
    )
    await db.commit()
    return cursor.rowcount or 0  # type: ignore[union-attr]


async def reset_pending_failed(include_terminal: bool = False) -> int:
    """Reset failed rows back to pending so they get re-tried.

    By default only rows still under `_MAX_AUTO_RETRIES` come back, so a row
    the server keeps rejecting does not retry forever on every startup.

    ``include_terminal=True`` also revives rows that exhausted the budget AND
    zeroes their counter. That is the recovery path for the case the counter
    cannot represent: the rejections were real, but the client that provoked
    them has since been fixed. It is only reachable from an explicit trigger —
    a user pressing retry, or signing in — never from the background loop.
    """
    from app.services.local_db.database import get_db
    db = get_db()
    if include_terminal:
        cursor = await db.execute(
            """
            UPDATE scrape_pages
            SET cloud_sync_status = 'pending',
                cloud_sync_error = NULL,
                cloud_sync_blocked_reason = NULL,
                cloud_sync_attempts = 0
            WHERE cloud_sync_status = 'failed'
              AND is_deleted = 0
            """,
        )
    else:
        cursor = await db.execute(
            """
            UPDATE scrape_pages
            SET cloud_sync_status = 'pending',
                cloud_sync_error = NULL,
                cloud_sync_blocked_reason = NULL
            WHERE cloud_sync_status = 'failed'
              AND cloud_sync_attempts < ?
              AND is_deleted = 0
            """,
            (_MAX_AUTO_RETRIES,),
        )
    await db.commit()
    return cursor.rowcount or 0  # type: ignore[union-attr]


async def get_pending_sync(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch rows that need to be pushed to the cloud."""
    from app.services.local_db.database import get_db
    db = get_db()
    rows = await db.fetchall(
        """
        SELECT id, url, page_name, content, content_type, char_count
        FROM scrape_pages
        WHERE cloud_sync_status = 'pending' AND is_deleted = 0
        ORDER BY scraped_at ASC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in rows]


async def list_scrapes(
    user_id: str = "",
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    from app.services.local_db.database import get_db
    db = get_db()
    deleted_filter = "" if include_deleted else "AND is_deleted = 0"
    user_filter = "AND user_id = ?" if user_id else ""
    params: tuple[Any, ...] = (limit, offset)
    if user_id:
        params = (user_id, limit, offset)
    rows = await db.fetchall(
        f"""
        SELECT id, url, page_name, domain, char_count, content_type,
               scraped_at, cloud_sync_status, cloud_sync_at, cloud_sync_error,
               cloud_sync_attempts, is_deleted, deleted_at, user_id
        FROM scrape_pages
        WHERE 1=1 {deleted_filter} {user_filter}
        ORDER BY scraped_at DESC
        LIMIT ? OFFSET ?
        """,
        params,
    )
    return [dict(r) for r in rows]


async def get_scrape(row_id: str) -> Optional[dict[str, Any]]:
    from app.services.local_db.database import get_db
    db = get_db()
    row = await db.fetchone(
        "SELECT * FROM scrape_pages WHERE id = ?", (row_id,)
    )
    if not row:
        return None
    data = dict(row)
    try:
        data["content"] = json.loads(data["content"])
    except Exception:
        pass
    return data


async def soft_delete(row_id: str) -> bool:
    """Mark a scrape as deleted (recoverable). First of two confirmations."""
    from app.services.local_db.database import get_db
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE scrape_pages
        SET is_deleted = 1, deleted_at = ?
        WHERE id = ? AND is_deleted = 0
        """,
        (_now_iso(), row_id),
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0  # type: ignore[union-attr]


async def hard_delete(row_id: str) -> bool:
    """Permanently remove a scrape. Only called after explicit second confirmation."""
    from app.services.local_db.database import get_db
    db = get_db()
    cursor = await db.execute(
        "DELETE FROM scrape_pages WHERE id = ? AND is_deleted = 1",
        (row_id,),
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0  # type: ignore[union-attr]


async def restore(row_id: str) -> bool:
    from app.services.local_db.database import get_db
    db = get_db()
    cursor = await db.execute(
        "UPDATE scrape_pages SET is_deleted = 0, deleted_at = NULL WHERE id = ? AND is_deleted = 1",
        (row_id,),
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0  # type: ignore[union-attr]


_EMPTY_SUMMARY: dict[str, Any] = {
    "total": 0, "synced": 0, "pending": 0, "failed": 0, "deleted": 0,
    "blocked_auth": 0, "blocked_offline": 0,
}


async def get_sync_summary() -> dict[str, Any]:
    """Counts plus the ONE state the user is in and what clears it."""
    from app.services.local_db.database import get_db
    db = get_db()
    row = await db.fetchone(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN cloud_sync_status = 'synced'  AND is_deleted = 0 THEN 1 ELSE 0 END), 0) AS synced,
            COALESCE(SUM(CASE WHEN cloud_sync_status = 'pending' AND is_deleted = 0 THEN 1 ELSE 0 END), 0) AS pending,
            COALESCE(SUM(CASE WHEN cloud_sync_status = 'failed'  AND is_deleted = 0 THEN 1 ELSE 0 END), 0) AS failed,
            COALESCE(SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END), 0) AS deleted,
            COALESCE(SUM(CASE WHEN cloud_sync_blocked_reason = 'auth'    AND is_deleted = 0 THEN 1 ELSE 0 END), 0) AS blocked_auth,
            COALESCE(SUM(CASE WHEN cloud_sync_blocked_reason = 'offline' AND is_deleted = 0 THEN 1 ELSE 0 END), 0) AS blocked_offline
        FROM scrape_pages
        """,
    )
    summary = dict(_EMPTY_SUMMARY) if not row else {k: (v or 0) for k, v in dict(row).items()}
    summary.update(_summary_state(summary))
    return summary


def _summary_state(summary: dict[str, Any]) -> dict[str, Any]:
    """Reduce the counts to one state, its message, and the action that fixes it.

    Ordered by what the user can actually do about it: a signed-out user is
    told to sign in even if the cloud is also flaky, because signing in is the
    step they own.
    """
    pending = summary.get("pending") or 0
    failed = summary.get("failed") or 0
    unsynced = pending + failed

    if not unsynced:
        return {
            "healthy": True, "unsynced": 0, "state": "synced",
            "message": "All scrapes are synced to the cloud.", "action": "none",
        }

    base = {"healthy": False, "unsynced": unsynced}
    if summary.get("blocked_auth"):
        return {**base, **BLOCKED_STATES[BLOCKED_AUTH]}
    if summary.get("blocked_offline"):
        return {**base, **BLOCKED_STATES[BLOCKED_OFFLINE]}
    if failed:
        return {
            **base, "state": "rejected", "action": "retry",
            "message": (
                f"The cloud would not accept {failed} scrape(s). They are safe on "
                "this computer. Retrying is harmless — if it keeps happening, the "
                "server rejected the content itself."
            ),
        }
    return {
        **base, "state": "queued", "action": "none",
        "message": f"{pending} scrape(s) are queued to upload. This happens automatically.",
    }


# ---------------------------------------------------------------------------
# Cloud push helper
# ---------------------------------------------------------------------------

async def _push_one_to_cloud(
    row: dict[str, Any], auth_token: str | None = _UNSET_TOKEN
) -> str:
    """Push a single pending row. Returns 'pushed' | 'deferred' | 'failed'.

    `auth_token` is resolved once per batch by the caller; the sentinel default
    keeps single-row callers (the fire-and-forget push in `save_scrape`)
    working without each of them repeating the lookup.
    """
    from app.services.scraper.auth_helper import get_active_user_token
    from app.services.scraper.remote_client import get_remote_scraper

    row_id: str = row["id"]
    url: str = row["url"]

    if auth_token is _UNSET_TOKEN:
        auth_token = await get_active_user_token()

    # Without an active user JWT the server can't attribute the save to a
    # real user — and would answer 401 anyway. That is a STATE, not a
    # failure: record why the row is stuck (so the UI can offer sign-in) and
    # leave the retry budget untouched.
    if not auth_token:
        await mark_cloud_deferred(
            row_id, BLOCKED_AUTH, "No signed-in user — sign in to sync this scrape."
        )
        logger.debug(
            "[scrape_store] Cloud sync deferred (no active user token): %s id=%s",
            url, row_id,
        )
        return "deferred"

    try:
        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        char_count: int = row.get("char_count") or 0
        content_type: str = row.get("content_type", "html")

        client = get_remote_scraper()
        await client.save_content(
            url=url,
            # Required by the server; derived the same way `save_locally`
            # derives the stored column, so both sides name the same page.
            page_name=row.get("page_name") or _page_name_for_url(url),
            content=content,
            content_type=content_type,
            char_count=char_count if char_count > 0 else None,
            auth_token=auth_token,
        )
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        reason = classify_push_error(exc)
        if reason is not None:
            await mark_cloud_deferred(row_id, reason, error_msg)
            logger.info(
                "[scrape_store] Cloud sync deferred (%s, retry budget untouched): %s → %s",
                reason, url, error_msg,
            )
            return "deferred"
        await mark_cloud_failed(row_id, error_msg)
        logger.warning("[scrape_store] Cloud sync REJECTED: %s → %s", url, error_msg)
        return "failed"

    await mark_cloud_synced(row_id)
    logger.info("[scrape_store] Cloud sync OK: %s (id=%s)", url, row_id)
    return "pushed"


async def push_pending_to_cloud(limit: int = 20) -> dict[str, int]:
    """Push all pending rows to the cloud. Returns {pushed, deferred, failed}.

    `deferred` counts rows that could not be attempted yet (signed out, cloud
    unreachable) — they are still queued and cost nothing.
    """
    pending = await get_pending_sync(limit=limit)
    if not pending:
        return {"pushed": 0, "deferred": 0, "failed": 0}

    # Resolved once for the batch: it decrypts through the OS keychain, and
    # doing that per row turned a 100-row backlog into 100 keychain hits every
    # two minutes.
    from app.services.scraper.auth_helper import get_active_user_token

    auth_token = await get_active_user_token()

    counts = {"pushed": 0, "deferred": 0, "failed": 0}
    for row in pending:
        try:
            outcome = await _push_one_to_cloud(row, auth_token)
        except Exception as exc:
            # `_push_one_to_cloud` handles push errors itself; reaching here
            # means the local DB write failed, which is never the row's fault.
            logger.warning(
                "[scrape_store] Cloud push bookkeeping failed for id=%s: %s",
                row.get("id"), exc, exc_info=True,
            )
            outcome = "deferred"
        counts[outcome] += 1

    logger.info(
        "[scrape_store] Cloud push batch: pushed=%d deferred=%d failed=%d",
        counts["pushed"], counts["deferred"], counts["failed"],
    )
    return counts


async def sync_after_sign_in() -> dict[str, int]:
    """Sync the backlog now that a user is signed in.

    Sign-in clears the exact blocker that stranded these rows, so this revives
    even rows that ran out of retries — including everything failed by the
    unauthenticated pushes of previous sessions.
    """
    revived = await reset_pending_failed(include_terminal=True)
    cleared = await clear_blocked_reason(BLOCKED_AUTH)
    if revived or cleared:
        logger.info(
            "[scrape_store] Sign-in sync: revived %d failed row(s), unblocked %d",
            revived, cleared,
        )
    result = await push_pending_to_cloud(limit=100)
    return {"revived": revived, "unblocked": cleared, **result}


# ---------------------------------------------------------------------------
# Dual-write entry point — called after every successful local scrape
# ---------------------------------------------------------------------------

async def save_scrape(
    url: str,
    content: dict[str, Any],
    content_type: str = "html",
    user_id: str = "",
) -> str:
    """Dual-write a scrape: local SQLite (blocking) + cloud (background fire-and-forget).

    Returns the local row ID immediately. Cloud push happens asynchronously;
    the cloud_sync_status column tracks whether it succeeded.
    """
    # 1. Local write — must succeed
    row_id = await save_locally(url, content, content_type, user_id)

    # 2. Cloud push — fire and forget; failure is tracked in DB for later retry
    async def _push() -> None:
        row = {"id": row_id, "url": url, "content": content,
               "content_type": content_type,
               "char_count": len(content.get("text_data", "") + content.get("ai_research_content", ""))}
        await _push_one_to_cloud(row)

    asyncio.create_task(_push())

    return row_id


# ---------------------------------------------------------------------------
# Background sync loop — runs on engine startup, retries pending/failed rows
# ---------------------------------------------------------------------------

_sync_task: asyncio.Task[None] | None = None
_sync_running = False


async def _sync_loop() -> None:
    global _sync_running
    _sync_running = True
    logger.info("[scrape_store] Background sync loop started (interval=%ds)", _SYNC_INTERVAL)
    try:
        # On startup, reset failed rows back to pending (below retry cap)
        reset_count = await reset_pending_failed()
        if reset_count:
            logger.info(
                "[scrape_store] Reset %d failed scrape(s) → pending for retry", reset_count
            )

        # First pass — push anything that didn't get synced before last shutdown
        await push_pending_to_cloud(limit=50)
        summary = await get_sync_summary()
        if not summary["healthy"]:
            logger.warning(
                "[scrape_store] Startup sync: %d scrape(s) unsynced — state=%s (%s)",
                summary["unsynced"], summary["state"], summary["message"],
            )

        while True:
            await asyncio.sleep(_SYNC_INTERVAL)
            await push_pending_to_cloud(limit=20)
    except asyncio.CancelledError:
        logger.info("[scrape_store] Background sync loop stopped")
    finally:
        _sync_running = False


def start_sync() -> None:
    """Start the background cloud-push loop. Call once at engine startup."""
    global _sync_task
    if _sync_task is not None and not _sync_task.done():
        return
    _sync_task = asyncio.create_task(_sync_loop(), name="scrape-store-sync")


def stop_sync() -> None:
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        _sync_task = None


async def stop_sync_async(timeout: float = 3.0) -> None:
    """Cancel the background sync task and await its completion."""
    global _sync_task
    task = _sync_task
    _sync_task = None
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
