"""Download manager REST + SSE routes.

Mounted at /downloads in app/main.py.

GET  /downloads           — list all downloads (optional ?status= ?category=)
POST /downloads           — enqueue a new download
GET  /downloads/{id}      — get a single download entry
DELETE /downloads/{id}    — cancel a download
POST /downloads/{id}/dismiss — permanently remove a terminal record
GET  /downloads/stream    — SSE stream of DownloadProgressEvents
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.common.system_logger import get_logger
from app.common.route_errors import safe_route
from app.services.downloads.manager import get_download_manager

logger = get_logger()
router = APIRouter(tags=["downloads"])


class EnqueueRequest(BaseModel):
    category: str
    filename: str
    display_name: str
    urls: list[str]
    priority: int = 0
    metadata: Optional[dict] = None
    download_id: Optional[str] = None


@router.get("/downloads/stream")
async def download_stream():
    """Server-Sent Events stream for live download progress."""
    manager = get_download_manager()

    async def generate():
        async for chunk in manager.sse_stream():
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/downloads")
async def list_downloads(status: Optional[str] = None, category: Optional[str] = None):
    """List all downloads, optionally filtered by status or category."""
    manager = get_download_manager()
    entries = manager.get_all(status=status, category=category)
    return [e.to_dict() for e in entries]


@router.post("/downloads")
@safe_route("downloads_enqueue")
async def enqueue_download(req: EnqueueRequest):
    """Enqueue a new download (or return existing if already queued/active)."""
    manager = get_download_manager()
    try:
        entry = await manager.enqueue(
            category=req.category,
            filename=req.filename,
            display_name=req.display_name,
            urls=req.urls,
            priority=req.priority,
            metadata=req.metadata,
            download_id=req.download_id,
        )
    except ValueError as exc:
        # Missing dest_dir etc. — caller error, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return entry.to_dict()


@router.get("/downloads/{download_id}")
async def get_download(download_id: str):
    """Get a single download entry by ID."""
    manager = get_download_manager()
    all_entries = manager.get_all()
    for entry in all_entries:
        if entry.id == download_id:
            return entry.to_dict()
    raise HTTPException(status_code=404, detail="Download not found")


@router.post("/downloads/{download_id}/dismiss")
@safe_route("downloads_dismiss")
async def dismiss_download(download_id: str):
    """Permanently remove a terminal (failed/cancelled/completed) record.

    This is how the user addresses a stale failure or an obsolete
    action-needed prompt and makes it go away — the record is deleted from
    memory and SQLite, so it can never be "restored" on a later boot.
    """
    manager = get_download_manager()
    try:
        removed = await manager.dismiss(download_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Download not found")
    return {"status": "removed", "id": download_id}


@router.delete("/downloads/{download_id}")
@safe_route("downloads_cancel")
async def cancel_download(download_id: str):
    """Cancel a queued or active download."""
    manager = get_download_manager()
    cancelled = await manager.cancel(download_id)
    if not cancelled:
        raise HTTPException(
            status_code=404,
            detail="Download not found or not cancellable",
        )
    return {"status": "cancelled", "id": download_id}
