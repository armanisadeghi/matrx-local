"""File sync REST routes.

Mounted at /file-sync in app/main.py.

GET  /file-sync/status                 — engine status (mode, counts, cursor, cycle)
POST /file-sync/sync                   — run one sync cycle now
POST /file-sync/hydrate                — fetch real bytes for a pointer file
GET  /file-sync/conflicts              — open conflicts
POST /file-sync/conflicts/{id}/resolve — keep_local | keep_remote
POST /file-sync/mode                   — change file_sync_mode (off|pointers|full)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.common.system_logger import get_logger
from app.services.file_sync import get_file_sync_engine
from app.services.file_sync.engine import MODES

logger = get_logger()
router = APIRouter(tags=["file-sync"])


class HydrateRequest(BaseModel):
    path: str  # rel path under the Files root, or a cloud file id


class ResolveRequest(BaseModel):
    resolution: str  # keep_local | keep_remote


class ModeRequest(BaseModel):
    mode: str  # off | pointers | full


@router.get("/file-sync/status")
async def file_sync_status() -> dict:
    return await get_file_sync_engine().get_status()


@router.post("/file-sync/sync")
async def file_sync_now() -> dict:
    engine = get_file_sync_engine()
    if not engine.is_configured and not await engine._configure_from_token():
        raise HTTPException(409, "not signed in — file sync needs a valid user token")
    if engine.mode == "off":
        raise HTTPException(409, "file_sync_mode is 'off' — enable pointers or full first")
    try:
        return await engine.sync_cycle()
    except Exception as exc:
        logger.error("[file_sync] manual sync failed: %s", exc, exc_info=True)
        raise HTTPException(502, f"sync failed: {exc}")


@router.post("/file-sync/hydrate")
async def file_sync_hydrate(body: HydrateRequest) -> dict:
    engine = get_file_sync_engine()
    try:
        path = await engine.hydrate(body.path)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        logger.error("[file_sync] hydrate %s failed: %s", body.path, exc, exc_info=True)
        raise HTTPException(502, f"hydration failed: {exc}")
    return {"path": str(path)}


@router.get("/file-sync/conflicts")
async def file_sync_conflicts() -> dict:
    return {"conflicts": await get_file_sync_engine().list_conflicts()}


@router.post("/file-sync/conflicts/{file_id}/resolve")
async def file_sync_resolve(file_id: str, body: ResolveRequest) -> dict:
    try:
        return await get_file_sync_engine().resolve_conflict(file_id, body.resolution)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/file-sync/mode")
async def file_sync_mode(body: ModeRequest) -> dict:
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    from app.services.cloud_sync.settings_sync import get_settings_sync

    sync = get_settings_sync()
    sync.set("file_sync_mode", body.mode)  # persists locally; cloud sync rides the normal loop
    engine = get_file_sync_engine()
    if body.mode == "off":
        await engine.stop_watcher()
    return {"mode": body.mode}
