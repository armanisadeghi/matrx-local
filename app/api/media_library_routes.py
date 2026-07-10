"""Media Library API routes (prefix /media-library).

Serves the persistent library of generated media written by the image and
video generation services (app/services/media_gen/library.py):

  GET    /media-library/items?media_type=image|video&limit=&offset=
                                        — newest-first sidecar metadata
  GET    /media-library/file/{item_id}  — the media bytes (use in <img>/<video>)
  DELETE /media-library/items/{item_id} — delete file + sidecar

Auth comes from the same Bearer-token AuthMiddleware that gates the
/image-gen and /video-gen surfaces — no extra dependency here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.common.route_errors import safe_route
from app.services.media_gen import library

router = APIRouter(prefix="/media-library", tags=["media-library"])


class MediaLibraryItem(BaseModel):
    id: str
    media_type: Literal["image", "video"]
    model_id: str
    prompt: str
    negative_prompt: str = ""
    params: dict[str, Any]
    """The FULL resolved kwargs actually passed to the pipeline (extra_params
    included; non-serializable values recorded as their repr)."""
    seed: int | None = None
    width: int = 0
    height: int = 0
    num_frames: int | None = None
    fps: int | None = None
    elapsed_seconds: float = 0.0
    created_at: str
    """UTC ISO timestamp."""
    file_name: str
    file_size_bytes: int
    file_path: str
    """Absolute path on this machine."""


class MediaLibraryListResponse(BaseModel):
    items: list[MediaLibraryItem]
    total: int
    """Total matching items (before limit/offset) — for pagination."""


@router.get("/items", response_model=MediaLibraryListResponse)
@safe_route("media_library_list")
async def list_media_items(
    media_type: Literal["image", "video"] | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> MediaLibraryListResponse:
    """Newest-first generated media with full generation metadata."""
    items, total = library.list_items(media_type, limit=limit, offset=offset)
    return MediaLibraryListResponse(
        items=[MediaLibraryItem(**item) for item in items],
        total=total,
    )


@router.get("/file/{item_id}")
async def get_media_file(item_id: str) -> FileResponse:
    """The media bytes with the correct content-type — point <img>/<video>
    tags here (append ?token= for auth when headers aren't available)."""
    meta = library.get_item(item_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown media item: {item_id}")
    path = Path(meta["file_path"])
    if not path.exists():
        raise HTTPException(
            status_code=410, detail="Media file no longer exists on disk"
        )
    return FileResponse(
        str(path),
        media_type=library.content_type_for(meta["media_type"]),
        filename=path.name,
    )


@router.delete("/items/{item_id}")
@safe_route("media_library_delete")
async def delete_media_item(item_id: str) -> dict:
    """Delete an item's media file AND its sidecar. Loud 404 for unknown ids."""
    if not library.delete_item(item_id):
        raise HTTPException(status_code=404, detail=f"Unknown media item: {item_id}")
    return {"deleted": True, "id": item_id}
