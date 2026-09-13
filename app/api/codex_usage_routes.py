"""Authenticated local Codex usage bridge."""

from __future__ import annotations

import datetime as dt
from fastapi import APIRouter, HTTPException, Query

from app.services.codex_usage import CollectionBusyError, snapshot_service
from app.services.codex_usage.allowance import allowance_service

router = APIRouter(prefix="/codex-usage", tags=["codex-usage"])


def _parse(value: str, name: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            422, detail=f"{name} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise HTTPException(422, detail=f"{name} must include a timezone")
    return parsed


@router.get("/allowance")
async def get_codex_usage_allowance(refresh: bool = Query(False, description="Bypass the short-lived local allowance cache")) -> dict:
    """Read a cached account allowance without collecting usage or starting a model."""
    return await allowance_service.read(refresh)


@router.get("")
async def get_codex_usage(
    start: str = Query(..., description="Inclusive ISO-8601 timestamp"),
    end: str = Query(..., description="Exclusive ISO-8601 timestamp"),
    refresh: bool = Query(False, description="Collect a fresh bounded snapshot"),
    grouping: str = Query("model", pattern="^(model|model_effort)$"),
) -> dict:
    """Collect local-only, sanitized aggregate usage for an authenticated caller."""
    try:
        snapshot = await snapshot_service.read(
            _parse(start, "start"), _parse(end, "end"), refresh
        )
        return snapshot | {"requested_grouping": grouping}
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except CollectionBusyError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
