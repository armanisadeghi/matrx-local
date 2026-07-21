"""On-disk prompt-matrix library + templates API.

The desktop UI reads/writes reusable option pools and named templates here.
Files live under ``~/.matrx/prompt-matrix/`` (see ``services/prompt_matrix``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.common.route_errors import safe_route
from app.services.prompt_matrix import get_prompt_matrix_store

router = APIRouter(prefix="/prompt-matrix", tags=["prompt-matrix"])


class LibraryPut(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)


class ListsPut(BaseModel):
    lists: list[dict[str, Any]] = Field(default_factory=list)


class TemplatesPut(BaseModel):
    templates: list[dict[str, Any]] = Field(default_factory=list)


class PromptsPut(BaseModel):
    prompts: list[dict[str, Any]] = Field(default_factory=list)


class VariationBatchesPut(BaseModel):
    batches: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/paths")
@safe_route("prompt_matrix_paths")
async def prompt_matrix_paths() -> dict[str, str]:
    return get_prompt_matrix_store().paths()


@router.get("/library")
@safe_route("prompt_matrix_get_library")
async def get_library() -> dict[str, Any]:
    return get_prompt_matrix_store().load_library()


@router.put("/library")
@safe_route("prompt_matrix_put_library")
async def put_library(body: LibraryPut) -> dict[str, Any]:
    try:
        return get_prompt_matrix_store().save_library(body.entries)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/lists")
@safe_route("prompt_matrix_get_lists")
async def get_lists() -> dict[str, Any]:
    return get_prompt_matrix_store().load_lists()


@router.put("/lists")
@safe_route("prompt_matrix_put_lists")
async def put_lists(body: ListsPut) -> dict[str, Any]:
    try:
        return get_prompt_matrix_store().save_lists(body.lists)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/templates")
@safe_route("prompt_matrix_get_templates")
async def get_templates() -> dict[str, Any]:
    return get_prompt_matrix_store().load_templates()


@router.put("/templates")
@safe_route("prompt_matrix_put_templates")
async def put_templates(body: TemplatesPut) -> dict[str, Any]:
    try:
        return get_prompt_matrix_store().save_templates(body.templates)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/prompts")
@safe_route("prompt_matrix_get_prompts")
async def get_prompts() -> dict[str, Any]:
    return get_prompt_matrix_store().load_prompts()


@router.put("/prompts")
@safe_route("prompt_matrix_put_prompts")
async def put_prompts(body: PromptsPut) -> dict[str, Any]:
    try:
        return get_prompt_matrix_store().save_prompts(body.prompts)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/variation-batches")
@safe_route("prompt_matrix_get_variation_batches")
async def get_variation_batches() -> dict[str, Any]:
    return get_prompt_matrix_store().load_variation_batches()


@router.put("/variation-batches")
@safe_route("prompt_matrix_put_variation_batches")
async def put_variation_batches(body: VariationBatchesPut) -> dict[str, Any]:
    try:
        return get_prompt_matrix_store().save_variation_batches(body.batches)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
