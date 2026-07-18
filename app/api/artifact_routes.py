"""Local media artifact reads and explicit reconciliation controls."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.artifacts import get_artifact_service

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}/content")
async def artifact_content(artifact_id: str) -> FileResponse:
    service = get_artifact_service()
    artifact = await service.get(artifact_id)
    path = await service.local_path(artifact_id)
    if artifact is None or path is None:
        raise HTTPException(status_code=404, detail="Local artifact content not found")
    return FileResponse(
        path,
        media_type=artifact.media_type,
        filename=artifact.file_name,
        content_disposition_type="inline",
    )


@router.get("/{artifact_id}")
async def artifact_status(artifact_id: str) -> dict:
    artifact = await get_artifact_service().get(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact.model_dump(mode="json", exclude_none=True)


@router.post("/sync")
async def sync_artifacts() -> dict[str, int]:
    published = await get_artifact_service().sync_pending()
    return {"published": published}
