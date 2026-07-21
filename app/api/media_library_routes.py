"""Media Library API routes (prefix /media-library).

Serves the persistent library of generated media written by the image and
video generation services (app/services/media_gen/library.py):

  GET    /media-library/items?media_type=image|video&limit=&offset=
                                        — newest-first sidecar metadata
  GET    /media-library/thumb/{item_id} — small JPEG for grids (self-healing:
                                          generates + caches on miss)
  GET    /media-library/file/{item_id}  — full media bytes (lightbox / download);
                                          resolves VAULTED ids too (423 when the
                                          vault is locked) — see get_media_file
  DELETE /media-library/items/{item_id} — delete file + sidecar (+ thumb)

Auth comes from the same Bearer-token AuthMiddleware that gates the
/image-gen and /video-gen surfaces — no extra dependency here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.common.route_errors import safe_route
from app.services.media_gen import library
from app.services.media_gen import thumbs as media_thumbs
from app.services.media_vault.service import (
    VaultError,
    VaultLockedError,
    get_vault_service,
)

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
    init_image_file: str | None = None
    """Name of the stored img2img source image, when this item was generated
    from one. Non-null means GET /media-library/items/{id}/init-image serves
    those bytes — which is what lets "Remix" restore the input image, not just
    the settings. Null for text-to-image items (and for vaulted ones, whose
    plaintext init image is destroyed along with the plaintext result)."""


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


@router.get(
    "/revision-branch/{root_item_id}",
    response_model=MediaLibraryListResponse,
)
@safe_route("media_library_revision_branch")
async def list_revision_branch(root_item_id: str) -> MediaLibraryListResponse:
    """All images in a revision branch, root-first (oldest → newest)."""
    items = library.list_revision_branch(root_item_id)
    return MediaLibraryListResponse(
        items=[MediaLibraryItem(**item) for item in items],
        total=len(items),
    )


@router.get("/thumb/{item_id}")
async def get_media_thumb(item_id: str) -> Response:
    """Small JPEG for gallery / filmstrip / queue tiles.

    Self-healing: if ``<id>.thumb.jpg`` is missing, this request generates it
    from the full media, writes it beside the original, and returns it. The
    client shows a placeholder until this succeeds — there is no separate
    backfill. Vaulted + unlocked items get an in-memory JPEG (never written
    as plaintext). Vaulted + locked → 423.
    """
    meta = library.get_item(item_id)
    if meta is not None:
        path = Path(meta["file_path"])
        if not path.exists():
            raise HTTPException(
                status_code=410, detail="Media file no longer exists on disk"
            )
        try:
            thumb_path = media_thumbs.ensure_thumb(meta)
        except Exception as exc:  # noqa: BLE001 — surface generation failure
            raise HTTPException(
                status_code=500,
                detail=f"Could not build thumbnail: {exc}",
            ) from exc
        return FileResponse(
            str(thumb_path),
            media_type="image/jpeg",
            filename=thumb_path.name,
        )

    vault = get_vault_service()
    if not vault.has_item(item_id):
        raise HTTPException(status_code=404, detail=f"Unknown media item: {item_id}")
    try:
        data, content_type = vault.read_file(item_id)
    except VaultLockedError as exc:
        raise HTTPException(
            status_code=423,
            detail="Item is in the locked Private Vault — unlock it to view.",
        ) from exc
    except VaultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Videos in the vault: we only have encrypted mp4 bytes in memory; extracting
    # a poster without a temp file is out of scope. Images are the common case.
    if not content_type.startswith("image/"):
        raise HTTPException(
            status_code=404,
            detail="No thumbnail for vaulted video — open the full file instead.",
        )
    try:
        jpeg = media_thumbs.thumb_bytes_for_vault_image(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not build thumbnail: {exc}",
        ) from exc
    return Response(content=jpeg, media_type="image/jpeg")


@router.get("/file/{item_id}")
async def get_media_file(item_id: str) -> Response:
    """The media bytes with the correct content-type — point <img>/<video>
    tags here (append ?token= for auth when headers aren't available).

    THE canonical read path for any generated media id, wherever it now lives.
    Moving an item into the Private Vault deletes the plaintext file, but every
    historical reference to it (job history, thumbnails, share links) still
    holds the same id — so a vaulted id resolves here too:

      plaintext library hit  → 200 file bytes
      vaulted + unlocked     → 200 decrypted bytes (in memory, never on disk)
      vaulted + locked       → 423 Locked (the vault contract's locked code)
      neither                → 404

    The 423 is the whole point: before this, a vaulted id 404'd as "Unknown
    media item", which is a lie — the item exists and is one unlock away.
    """
    meta = library.get_item(item_id)
    if meta is not None:
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

    vault = get_vault_service()
    if not vault.has_item(item_id):
        raise HTTPException(status_code=404, detail=f"Unknown media item: {item_id}")
    try:
        data, content_type = vault.read_file(item_id)
    except VaultLockedError as exc:
        raise HTTPException(
            status_code=423,
            detail="Item is in the locked Private Vault — unlock it to view.",
        ) from exc
    except VaultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type=content_type)


@router.get("/items/{item_id}/init-image")
async def get_media_init_image(item_id: str) -> Response:
    """The img2img SOURCE image this item was generated from — what makes
    "Remix" able to restore the input, not just the settings.

    Same resolution contract as ``get_media_file``, so a vaulted id keeps
    working instead of lying about not existing:

      plaintext library hit  → 200 the source image
      vaulted + unlocked     → 200 decrypted bytes (in memory, never on disk)
      vaulted + locked       → 423 Locked
      no source image        → 404 (a text-to-image item, or one generated
                               before source images were stored)
    """
    path = library.get_init_image_path(item_id)
    if path is not None:
        return FileResponse(str(path), media_type="image/png", filename=path.name)

    vault = get_vault_service()
    if not vault.has_init_image(item_id):
        raise HTTPException(
            status_code=404,
            detail=f"No stored input image for media item: {item_id}",
        )
    try:
        data = vault.read_init_image(item_id)
    except VaultLockedError as exc:
        raise HTTPException(
            status_code=423,
            detail="Item is in the locked Private Vault — unlock it to view.",
        ) from exc
    except VaultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type="image/png")


@router.delete("/items/{item_id}")
@safe_route("media_library_delete")
async def delete_media_item(item_id: str) -> dict:
    """Delete an item's media file AND its sidecar. Loud 404 for unknown ids."""
    if not library.delete_item(item_id):
        raise HTTPException(status_code=404, detail=f"Unknown media item: {item_id}")
    return {"deleted": True, "id": item_id}
