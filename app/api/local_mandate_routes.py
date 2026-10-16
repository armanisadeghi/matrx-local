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

  POST /local-mandates/{mandate_key}/fill   body: {"variables": {name: value}}
      200: {mandate_key, resolution, messages, resolved_at, stale, stale_reason}
           ``messages`` are the Holder's authored messages ({role, content}) FILLED for the
           first turn by matrx-ai's one pass (THE VARIABLE RULE): declared names only, an
           empty declared value is "", the ``{{!name}}`` escape, sections, declared prompt
           blocks. The caller keeps them for its conversation; a later turn never refills.
      422/503: a declared prompt block could not be resolved (``detail.blocks`` names it)

Service + rationale: app/services/ai/local_mandates.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.services.ai.local_mandates import get_local_mandate_resolver

router = APIRouter(prefix="/local-mandates", tags=["mandates"])


@router.get("/{mandate_key}")
async def resolve_local_mandate(mandate_key: str, response: Response) -> dict[str, Any]:
    payload = await get_local_mandate_resolver().resolve(mandate_key)
    response.headers["X-Matrx-Mandate-Stale"] = "true" if payload["stale"] else "false"
    return payload


class LocalMandateFillRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


@router.post("/{mandate_key}/fill")
async def fill_local_mandate(
    mandate_key: str, body: LocalMandateFillRequest, response: Response
) -> dict[str, Any]:
    payload = await get_local_mandate_resolver().fill(mandate_key, body.variables)
    response.headers["X-Matrx-Mandate-Stale"] = "true" if payload["stale"] else "false"
    return payload
