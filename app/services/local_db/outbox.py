"""Outbox writer for the canonical mirror — the sync_queue finally has writers.

Every LOCAL-ORIGIN mutation of a mirrored table enqueues one row here; the
chat sync engine drains the queue by pushing the row's CURRENT state to the
cloud (upsert with tombstone semantics — ``deleted_at`` rides along, so a
single ``upsert`` action covers create, update, and soft-delete).

Remote-origin writes (rows applied by the pull side of the sync engine) must
NEVER enqueue — that would echo cloud rows back at the cloud. Pull code
writes raw SQL directly and does not go through these helpers.

Dedupe: one pending queue row per (entity_type, entity_id). The push reads
the live row at drain time, so collapsing repeat enqueues loses nothing.
"""

from __future__ import annotations

from app.common.system_logger import get_logger
from app.services.local_db.database import get_db, LocalDatabase

logger = get_logger()


async def enqueue_change(
    schema: str,
    table: str,
    row_id: str,
    db: LocalDatabase | None = None,
    commit: bool = True,
) -> None:
    """Record that a mirrored row changed locally and needs a cloud push.

    Loud on failure but never raises — a broken outbox must not take down
    the user-facing write that triggered it (the row is still saved locally).
    NOTE: there is no reconcile pass for the chat mirror yet, so a swallowed
    enqueue failure means that specific change never reaches the cloud (the
    ERROR log below is the only trace). A mirror-vs-outbox reconcile sweep
    is tracked in FOUND_DEFECTS (MXL-D-049).
    """
    entity_type = f"{schema}.{table}"
    try:
        _db = db or get_db()
        await _db.execute(
            "DELETE FROM sync_queue WHERE entity_type = ? AND entity_id = ?",
            (entity_type, row_id),
        )
        await _db.execute(
            "INSERT INTO sync_queue (entity_type, entity_id, action, payload) "
            "VALUES (?, ?, 'upsert', '{}')",
            (entity_type, row_id),
        )
        if commit:
            await _db.commit()
    except Exception:
        logger.error(
            "[outbox] FAILED to enqueue %s %s — this local change will not reach "
            "the cloud until the next full reconcile",
            entity_type,
            row_id,
            exc_info=True,
        )
