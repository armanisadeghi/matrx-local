"""Private Media Vault API routes (prefix /media-vault).

Password-locked, escrow-recoverable encrypted store for media-library items
(app/services/media_vault/service.py). Auth comes from the same Bearer-token
AuthMiddleware that gates /media-library — no extra dependency here.

Contract (the desktop frontend is built against this exactly):

  GET    /media-vault/status            — {exists, unlocked, item_count|null,
                                           auto_lock_seconds}
  POST   /media-vault/create            — {password} (min 8 chars; 409 if the
                                           vault exists; unlocks on success)
  POST   /media-vault/unlock            — {password} (403 on wrong password,
                                           404 when no vault exists)
  POST   /media-vault/lock              — always succeeds
  GET    /media-vault/items             — decrypted metadata list
  GET    /media-vault/file/{item_id}    — decrypted bytes, correct
                                           content-type (in-memory Response —
                                           plaintext never touches disk)
  POST   /media-vault/move              — {item_ids: [...]} →
                                           {results: [{item_id, ok, error?}]}
  POST   /media-vault/restore           — same shape as move, reverse direction
  DELETE /media-vault/items/{item_id}   — permanent delete (404 unknown)
  POST   /media-vault/change-password   — {current_password, new_password}
                                           (403 wrong current password)

Every locked-vault access is HTTP 423 (Locked) — consistently, on all routes
that need the key.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.common.route_errors import safe_route
from app.services.media_vault.service import (
    VaultError,
    VaultExistsError,
    VaultLockedError,
    VaultMissingError,
    WrongPasswordError,
    get_vault_service,
)

router = APIRouter(prefix="/media-vault", tags=["media-vault"])


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class ItemIdsRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1)


class VaultOpResult(BaseModel):
    item_id: str
    ok: bool
    error: str | None = None


class VaultOpResponse(BaseModel):
    results: list[VaultOpResult]


class VaultStatusResponse(BaseModel):
    exists: bool
    unlocked: bool
    item_count: int | None = None
    """Number of vaulted items — only when unlocked, else null."""
    auto_lock_seconds: int


def _translate(exc: VaultError) -> HTTPException:
    """Map service errors to the HTTP contract. 423 = locked, everywhere."""
    if isinstance(exc, VaultLockedError):
        return HTTPException(status_code=423, detail=str(exc))
    if isinstance(exc, VaultMissingError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, VaultExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/status", response_model=VaultStatusResponse)
@safe_route("media_vault_status")
async def vault_status() -> VaultStatusResponse:
    return VaultStatusResponse(**get_vault_service().status())


@router.post("/create")
@safe_route("media_vault_create")
async def vault_create(req: PasswordRequest) -> dict[str, Any]:
    try:
        get_vault_service().create(req.password)
    except VaultError as exc:
        raise _translate(exc) from exc
    return {"created": True, "unlocked": True}


@router.post("/unlock")
@safe_route("media_vault_unlock")
async def vault_unlock(req: PasswordRequest) -> dict[str, Any]:
    try:
        get_vault_service().unlock(req.password)
    except WrongPasswordError as exc:
        raise HTTPException(status_code=403, detail="Wrong password") from exc
    except VaultError as exc:
        raise _translate(exc) from exc
    return {"unlocked": True}


@router.post("/lock")
@safe_route("media_vault_lock")
async def vault_lock() -> dict[str, Any]:
    get_vault_service().lock()
    return {"locked": True}


@router.get("/items")
@safe_route("media_vault_items")
async def vault_items() -> dict[str, Any]:
    try:
        items = get_vault_service().list_items()
    except VaultError as exc:
        raise _translate(exc) from exc
    return {"items": items, "total": len(items)}


@router.get("/file/{item_id}")
async def vault_file(item_id: str) -> Response:
    """Decrypted bytes served from memory — plaintext must never touch disk,
    so this is a Response, never a FileResponse."""
    try:
        data, content_type = get_vault_service().read_file(item_id)
    except WrongPasswordError as exc:  # defensive — read_file shouldn't raise it
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except VaultLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except VaultMissingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VaultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type=content_type)


@router.post("/move", response_model=VaultOpResponse)
@safe_route("media_vault_move")
async def vault_move(req: ItemIdsRequest) -> VaultOpResponse:
    """Move library items INTO the vault (encrypt-verify-then-delete)."""
    try:
        results = get_vault_service().move_from_library(req.item_ids)
    except VaultError as exc:
        raise _translate(exc) from exc
    return VaultOpResponse(results=[VaultOpResult(**r) for r in results])


@router.post("/restore", response_model=VaultOpResponse)
@safe_route("media_vault_restore")
async def vault_restore(req: ItemIdsRequest) -> VaultOpResponse:
    """Restore vaulted items back into the media library."""
    try:
        results = get_vault_service().restore_to_library(req.item_ids)
    except VaultError as exc:
        raise _translate(exc) from exc
    return VaultOpResponse(results=[VaultOpResult(**r) for r in results])


@router.delete("/items/{item_id}")
@safe_route("media_vault_delete")
async def vault_delete(item_id: str) -> dict[str, Any]:
    try:
        deleted = get_vault_service().delete_item(item_id)
    except VaultError as exc:
        raise _translate(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Unknown vault item: {item_id}")
    return {"deleted": True, "id": item_id}


@router.post("/change-password")
@safe_route("media_vault_change_password")
async def vault_change_password(req: ChangePasswordRequest) -> dict[str, Any]:
    try:
        get_vault_service().change_password(req.current_password, req.new_password)
    except WrongPasswordError as exc:
        raise HTTPException(status_code=403, detail="Wrong password") from exc
    except VaultError as exc:
        raise _translate(exc) from exc
    return {"changed": True}
