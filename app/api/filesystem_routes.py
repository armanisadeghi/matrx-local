"""Structured local filesystem discovery API."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.services.filesystem import get_filesystem_service

router = APIRouter(prefix="/filesystem", tags=["filesystem"])


class PriorityRoot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    label: str | None = None


class PriorityRootsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roots: list[PriorityRoot] = Field(default_factory=list, max_length=100)


class IndexingSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_enabled: bool = True
    semantic_enabled: bool = False
    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5", min_length=1, max_length=200)
    max_content_bytes: int = Field(default=512 * 1024 * 1024, ge=16 * 1024 * 1024, le=20 * 1024 * 1024 * 1024)
    max_embedding_entries: int = Field(default=10_000, ge=100, le=50_000)


@router.get("/places")
async def get_places() -> dict[str, Any]:
    service = get_filesystem_service()
    return {"kind": "filesystem.places", "namespace": "host", "places": await service.places()}


@router.put("/priority-roots")
async def update_priority_roots(request: PriorityRootsRequest) -> dict[str, Any]:
    service = get_filesystem_service()
    places = await service.set_priority_roots([root.model_dump(exclude_none=True) for root in request.roots])
    return {"kind": "filesystem.places", "namespace": "host", "places": places}


@router.put("/indexing-settings")
async def update_indexing_settings(request: IndexingSettingsRequest) -> dict[str, object]:
    return await get_filesystem_service().set_indexing_settings(**request.model_dump())


@router.get("/indexing-settings")
async def get_indexing_settings() -> dict[str, object]:
    return await get_filesystem_service().indexing_settings()


@router.get("/list")
async def list_directory(
    path: str,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    show_hidden: bool = False,
) -> dict[str, object]:
    try:
        result = await get_filesystem_service().list_directory(
            path, cursor=cursor, limit=limit, show_hidden=show_hidden
        )
    except (NotADirectoryError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()


@router.get("/find")
async def find_paths(
    query: str = Query(min_length=1),
    root: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    try:
        result = await get_filesystem_service().find(
            query, root=root, cursor=cursor, limit=limit
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()


@router.get("/status")
async def filesystem_status() -> dict[str, object]:
    return await get_filesystem_service().status()


@router.get("/search-content")
async def search_content(
    query: str = Query(min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    service = get_filesystem_service()
    if not service.index.fts_available:
        raise HTTPException(status_code=503, detail="SQLite FTS5 is unavailable")
    results = await asyncio.to_thread(service.index.search_content, query, limit)
    return {"kind": "filesystem.content-search", "namespace": "host", "query": query, "results": results}


@router.get("/search-semantic")
async def search_semantic(
    query: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    try:
        return await get_filesystem_service().semantic_find(query, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
