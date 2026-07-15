"""app/api/catalog_routes.py — resolved Remote Catalog entries over HTTP.

``GET /catalogs/{kind}`` serves the RESOLVED entries for one catalog kind
(active, per-entry ``min_app_version``-gated, sorted by ``sort_order``) plus
the provenance tier, so every non-Python consumer reads the same resolved
view the engine itself uses:

  - the desktop webview (LLM / Whisper model selectors, system prompts,
    API-key provider patterns) — a new model row in the DB shows up here
    on the next refresh, with NO Tauri rebuild;
  - matrx-extend or any other local client that wants the catalog.

Kind slugs are validated against ``KNOWN_KINDS`` — an unknown kind is a 404,
never an empty 200 (an empty list must always mean "this kind is empty",
not "you typo'd the slug").

Trust model: read-only public metadata (model lists, prompts — the same data
anyone can read anonymously from Supabase), so the per-kind paths are listed
in ``_PUBLIC_PATHS`` (app/api/auth.py), same posture as ``/chat/models``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.services.catalogs import get_catalogs_service, is_valid_kind

router = APIRouter(prefix="/catalogs", tags=["catalogs"])


@router.get("/{kind}")
async def get_catalog_kind(kind: str) -> dict[str, Any]:
    """Resolved catalog entries for one kind (active, version-gated, sorted)."""
    if not is_valid_kind(kind):
        raise HTTPException(status_code=404, detail=f"Unknown catalog kind: {kind}")
    service = get_catalogs_service()
    entries = service.get_catalog(kind)
    resolved = service.resolved
    return {
        "kind": kind,
        "tier": resolved.tier,
        "fetched_at": resolved.fetched_at.isoformat() if resolved.fetched_at else None,
        "entries": [e.model_dump(mode="json") for e in entries],
    }
