"""Access-health API — the single HTTP surface for filesystem access state.

Replaces the notes-only /notes/access* endpoints with a resource-scoped,
evidence-based view. See app/services/access_health/FEATURE.md.

GET  /access/health   — cached evidence view (no filesystem I/O)
POST /access/recheck  — actively probe (all or named resources); "Check again"
POST /access/reset    — clear all evidence and re-probe everything; the ONLY
                        sanctioned way for reset features to touch access state
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.access_health import get_access_health

router = APIRouter(prefix="/access", tags=["access"])


class RecheckRequest(BaseModel):
    # None → probe every registered resource.
    resource_ids: list[str] | None = None
    # "Create folder" action for the missing_dir case. On macOS-without-FDA
    # the mkdir itself is denied, so this degrades to the permission
    # evidence — never a crash.
    create_missing: bool = False


@router.get("/health")
async def access_health() -> dict[str, Any]:
    """Current access-health snapshot (in-memory evidence, no probing)."""
    return get_access_health().snapshot()


@router.post("/recheck")
async def access_recheck(req: RecheckRequest | None = None) -> dict[str, Any]:
    """Actively re-probe access ("Check again" button)."""
    service = get_access_health()
    resource_ids = req.resource_ids if req is not None else None
    create_missing = bool(req.create_missing) if req is not None else False
    return await asyncio.to_thread(
        service.recheck, resource_ids, create_missing=create_missing
    )


@router.post("/reset")
async def access_reset() -> dict[str, Any]:
    """Clear all access evidence and re-probe everything."""
    return await asyncio.to_thread(get_access_health().reset)
