"""The desktop's records-mirror surface: status and a manual sync.

Both endpoints are honest about the campaign switch: when the custom record
store is off for the organization (or its doors are not on the client wire yet)
they answer 409 with the remedy, never an empty success and never a silent
no-op.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.common.system_logger import get_logger
from app.services.records_sync import RecordsMirrorUnavailable, get_records_sync_engine

logger = get_logger()

router = APIRouter(prefix="/records", tags=["records-mirror"])


class RecordsSyncRequest(BaseModel):
    organization_id: str
    table_ids: list[str]


@router.get("/mirror/status")
async def records_mirror_status() -> dict[str, Any]:
    """Outbox, checkpoints, conflicts, and the state of the campaign switch."""
    return await get_records_sync_engine().get_status()


@router.post("/mirror/sync")
async def trigger_records_mirror_sync(body: RecordsSyncRequest) -> dict[str, Any]:
    """Run one pull+push cycle of the records mirror right now."""
    engine = get_records_sync_engine()
    configured = await engine.configure_from_persisted_token(
        organization_id=body.organization_id, table_ids=body.table_ids
    )
    if not configured:
        raise HTTPException(status_code=401, detail="No signed-in user — sign in first")
    try:
        summary = await engine.sync_cycle()
    except RecordsMirrorUnavailable as exc:
        logger.error("[records_routes] mirror unavailable: %s", exc)
        raise HTTPException(status_code=409, detail=f"{exc.reason}. {exc.remedy}") from exc
    return {"status": "ok", **summary}
