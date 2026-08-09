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

logger = logging.getLogger(__name__)

# How many cloud push failures before we stop retrying automatically
# (user will see a warning; they can manually trigger retry)
_MAX_AUTO_RETRIES = 5

# Background cloud-push retry interval (seconds)
_SYNC_INTERVAL = 120


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
        SET cloud_sync_status = 'synced', cloud_sync_at = ?, cloud_sync_error = NULL
        WHERE id = ?
        """,
        (_now_iso(), row_id),
    )
    await db.commit()


async def mark_cloud_failed(row_id: str, error: str) -> None:
    from app.services.local_db.database import get_db
    db = get_db()
    await db.execute(
        """
        UPDATE scrape_pages
        SET cloud_sync_status = 'failed',
            cloud_sync_error = ?,
            cloud_sync_attempts = cloud_sync_attempts + 1
        WHERE id = ?
        """,
        (error[:500], row_id),
    )
    await db.commit()


async def reset_pending_failed() -> int:
    """On startup: reset failed rows (below retry limit) back to pending so they get re-tried."""
    from app.services.local_db.database import get_db
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE scrape_pages
        SET cloud_sync_status = 'pending', cloud_sync_error = NULL
        WHERE cloud_sync_status = 'failed'
          AND cloud_sync_attempts < ?
          AND is_deleted = 0
        """,
        (_MAX_AUTO_RETRIES,),
    )
    await db.commit()
    return cursor.rowcount  # type: ignore[union-attr]


async def get_pending_sync(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch rows that need to be pushed to the cloud."""
    from app.services.local_db.database import get_db
    db = get_db()
    rows = await db.fetchall(
        """
        SELECT id, url, content, content_type, char_count
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


async def get_sync_summary() -> dict[str, Any]:
    from app.services.local_db.database import get_db
    db = get_db()
    row = await db.fetchone(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN cloud_sync_status = 'synced'  AND is_deleted = 0 THEN 1 ELSE 0 END), 0) AS synced,
            COALESCE(SUM(CASE WHEN cloud_sync_status = 'pending' AND is_deleted = 0 THEN 1 ELSE 0 END), 0) AS pending,
            COALESCE(SUM(CASE WHEN cloud_sync_status = 'failed'  AND is_deleted = 0 THEN 1 ELSE 0 END), 0) AS failed,
            COALESCE(SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END), 0) AS deleted
        FROM scrape_pages
        """,
    )
    if not row:
        return {"total": 0, "synced": 0, "pending": 0, "failed": 0, "deleted": 0}
    return dict(row)


# ---------------------------------------------------------------------------
# Cloud push helper
# ---------------------------------------------------------------------------

async def _push_one_to_cloud(row: dict[str, Any]) -> None:
    """Push a single pending row to the remote scraper server."""
    from app.services.scraper.auth_helper import get_active_user_token
    from app.services.scraper.remote_client import get_remote_scraper

    row_id: str = row["id"]
    url: str = row["url"]
    try:
        # Without an active user JWT the server can't attribute the save
        # to a real user. Defer the row instead of failing it permanently
        # — the next push attempt (or a manual retry after sign-in) will
        # try again.
        auth_token = await get_active_user_token()
        if not auth_token:
            logger.debug(
                "[scrape_store] Cloud sync deferred (no active user token): %s id=%s",
                url, row_id,
            )
            return

        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        char_count: int = row.get("char_count") or 0
        content_type: str = row.get("content_type", "html")

        client = get_remote_scraper()
        await client.save_content(
            url=url,
            content=content,
            content_type=content_type,
            char_count=char_count if char_count > 0 else None,
            auth_token=auth_token,
        )
        await mark_cloud_synced(row_id)
        logger.info("[scrape_store] Cloud sync OK: %s (id=%s)", url, row_id)
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        await mark_cloud_failed(row_id, error_msg)
        logger.warning("[scrape_store] Cloud sync FAILED: %s → %s", url, error_msg)


async def push_pending_to_cloud(limit: int = 20) -> dict[str, int]:
    """Push all pending rows to the cloud. Returns {pushed, failed}."""
    pending = await get_pending_sync(limit=limit)
    if not pending:
        return {"pushed": 0, "failed": 0}

    pushed = 0
    failed = 0
    for row in pending:
        try:
            await _push_one_to_cloud(row)
            pushed += 1
        except Exception:
            failed += 1

    if pushed or failed:
        logger.info(
            "[scrape_store] Cloud push batch: pushed=%d failed=%d", pushed, failed
        )
    return {"pushed": pushed, "failed": failed}


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
        result = await push_pending_to_cloud(limit=50)
        if result["pushed"] or result["failed"]:
            summary = await get_sync_summary()
            if summary.get("failed", 0) > 0:
                logger.warning(
                    "[scrape_store] Startup sync: %d scrape(s) still unsynced after retry "
                    "(cloud may be unreachable — will retry next startup)",
                    summary["failed"],
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
