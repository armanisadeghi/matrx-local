"""Scrape persistence API routes.

Endpoints for listing, retrieving, deleting, and checking cloud-sync status
of locally stored scrape results.

Deletion is protected by a two-step process:
  1. DELETE /scrapes/{id}                       → soft delete (is_deleted=1, recoverable)
  2. DELETE /scrapes/{id}?confirmed=true         → hard delete (permanent, unrecoverable)

The UI must call step 1 first and present a second warning before calling step 2.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Any

from app.common.system_logger import get_logger

router = APIRouter(prefix="/scrapes", tags=["scrapes"])
logger = get_logger()


# ---------------------------------------------------------------------------
# GET /scrapes — list all locally stored scrapes
# ---------------------------------------------------------------------------

@router.get("")
async def list_scrapes(
    user_id: str = Query(default="", description="Filter by user ID"),
    include_deleted: bool = Query(default=False, description="Include soft-deleted rows"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    from app.services.scraper.scrape_store import list_scrapes as _list
    rows = await _list(
        user_id=user_id,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )
    return {"scrapes": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# GET /scrapes/sync-status — cloud sync health summary
# ---------------------------------------------------------------------------

@router.get("/sync-status")
async def sync_status() -> dict[str, Any]:
    """Returns a summary of local scrape storage vs cloud sync state.

    If `failed` > 0 the cloud sync has errors — the UI should surface a warning
    to the user explaining which scrapes are not yet in the cloud.
    """
    from app.services.scraper.scrape_store import get_sync_summary

    summary = await get_sync_summary()
    # The keys exist with value None on an empty table (SUM over zero rows)
    # — .get's default doesn't apply, so guard with `or 0`.
    has_sync_error = (summary.get("failed") or 0) > 0 or (summary.get("pending") or 0) > 0

    return {
        **summary,
        "healthy": not has_sync_error,
        "message": (
            "All scrapes are synced to the cloud."
            if not has_sync_error
            else (
                f"{summary.get('failed', 0)} scrape(s) failed to sync and "
                f"{summary.get('pending', 0)} scrape(s) are queued. "
                "The engine will retry automatically. If this persists, check your "
                "internet connection or the scraper server status."
            )
        ),
    }


# ---------------------------------------------------------------------------
# POST /scrapes/sync — manually trigger a cloud-push pass
# ---------------------------------------------------------------------------

@router.post("/sync")
async def trigger_sync() -> dict[str, Any]:
    """Manually trigger a cloud-push pass for all pending/failed scrapes."""
    from app.services.scraper.scrape_store import push_pending_to_cloud, reset_pending_failed

    reset_count = await reset_pending_failed()
    result = await push_pending_to_cloud(limit=100)
    return {
        "reset_to_pending": reset_count,
        "pushed": result["pushed"],
        "failed": result["failed"],
    }


# ---------------------------------------------------------------------------
# GET /scrapes/{id} — retrieve a single scrape with full content
# ---------------------------------------------------------------------------

@router.get("/{scrape_id}")
async def get_scrape(scrape_id: str) -> dict[str, Any]:
    from app.services.scraper.scrape_store import get_scrape as _get
    row = await _get(scrape_id)
    if not row:
        raise HTTPException(status_code=404, detail="Scrape not found")
    return row


# ---------------------------------------------------------------------------
# DELETE /scrapes/{id} — two-step delete with protection
# ---------------------------------------------------------------------------

@router.delete("/{scrape_id}")
async def delete_scrape(
    scrape_id: str,
    confirmed: bool = Query(
        default=False,
        description=(
            "Set to true to permanently delete. "
            "Without this flag the scrape is only soft-deleted and can be restored. "
            "WARNING: confirmed=true is irreversible."
        ),
    ),
) -> dict[str, Any]:
    """Delete a scrape with two-step protection.

    - Without `?confirmed=true`: soft delete (marks is_deleted=1, fully recoverable).
    - With `?confirmed=true`: permanent hard delete (the row is gone forever).

    The client MUST call without confirmed first, show the user a clear warning
    that the data will be deleted forever, then call again with confirmed=true
    only after the user explicitly acknowledges the second warning.
    """
    from app.services.scraper.scrape_store import soft_delete, hard_delete, get_scrape as _get

    row = await _get(scrape_id)
    if not row:
        raise HTTPException(status_code=404, detail="Scrape not found")

    if not confirmed:
        # Step 1: soft delete
        if row.get("is_deleted"):
            return {
                "status": "already_soft_deleted",
                "message": (
                    "This scrape is already marked for deletion. "
                    "To permanently remove it, call DELETE again with ?confirmed=true. "
                    "WARNING: This cannot be undone."
                ),
                "scrape_id": scrape_id,
                "url": row.get("url"),
            }

        ok = await soft_delete(scrape_id)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to soft-delete scrape")

        return {
            "status": "soft_deleted",
            "message": (
                "Scrape marked for deletion. It is still recoverable. "
                "To permanently delete it, call DELETE again with ?confirmed=true. "
                "WARNING: Permanent deletion cannot be undone."
            ),
            "scrape_id": scrape_id,
            "url": row.get("url"),
        }

    else:
        # Step 2: hard delete — only allowed if already soft-deleted
        if not row.get("is_deleted"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot permanently delete a scrape that has not been soft-deleted first. "
                    "Call DELETE without ?confirmed=true to soft-delete first."
                ),
            )

        ok = await hard_delete(scrape_id)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to permanently delete scrape")

        logger.warning(
            "[scrape_routes] HARD DELETE: scrape_id=%s url=%s",
            scrape_id, row.get("url"),
        )
        return {
            "status": "deleted",
            "message": "Scrape permanently deleted. This cannot be undone.",
            "scrape_id": scrape_id,
            "url": row.get("url"),
        }


# ---------------------------------------------------------------------------
# POST /scrapes/{id}/restore — undo a soft delete
# ---------------------------------------------------------------------------

@router.post("/{scrape_id}/restore")
async def restore_scrape(scrape_id: str) -> dict[str, Any]:
    """Restore a soft-deleted scrape."""
    from app.services.scraper.scrape_store import restore as _restore, get_scrape as _get

    row = await _get(scrape_id)
    if not row:
        raise HTTPException(status_code=404, detail="Scrape not found")
    if not row.get("is_deleted"):
        return {"status": "not_deleted", "message": "Scrape is not deleted.", "scrape_id": scrape_id}

    ok = await _restore(scrape_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to restore scrape")

    return {"status": "restored", "scrape_id": scrape_id, "url": row.get("url")}
