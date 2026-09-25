"""The desktop's ONE door to a local-model Mandate (online and offline).

  GET /local-mandates/{mandate_key}
      200: {mandate_key, resolution, definition, resolved_at, stale, stale_reason}
           ``resolution`` is AIDream's ``GET /api/mandates/{key}/resolution``
           payload VERBATIM; ``definition`` is the Holder's execution
           definition, the same object ``GET /api/agents/{id}/execution-definition``
           returns. ``stale`` is true when the network was unavailable and this
           is the last answer the platform gave this person in this
           organization — ``stale_reason`` says why.
      401: never signed in on this device
      4xx: AIDream's own refusal of this mandate for this caller, passed through
      503: never resolved on this device and AIDream unreachable

Service + rationale: app/services/ai/local_mandates.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from app.services.ai.local_mandates import get_local_mandate_resolver

router = APIRouter(prefix="/local-mandates", tags=["mandates"])


@router.get("/{mandate_key}")
async def resolve_local_mandate(mandate_key: str, response: Response) -> dict[str, Any]:
    payload = await get_local_mandate_resolver().resolve(mandate_key)
    response.headers["X-Matrx-Mandate-Stale"] = "true" if payload["stale"] else "false"
    return payload
