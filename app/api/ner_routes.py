"""Local named-entity extraction routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.common.route_errors import safe_route
from app.services.action_needed.registry import get_action_needed_registry
from app.services.ner.service import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    DEFAULT_THRESHOLD,
    NerError,
    NerExtraction,
    get_ner_service,
)

router = APIRouter(prefix="/ner", tags=["ner"])


class NerStatusResponse(BaseModel):
    active_model_id: str
    loaded_model_id: str | None
    model_loaded: bool
    model_downloaded: bool
    needs_download: bool
    is_downloading: bool
    download_model_id: str | None
    model_dir: str
    default_model_id: str
    deps: dict[str, Any]
    last_error: str | None


class NerDownloadRequest(BaseModel):
    model_id: str | None = None
    force: bool = False


class NerDownloadResponse(BaseModel):
    success: bool
    already_downloaded: bool = False
    model_id: str
    repo_id: str | None = None
    local_path: str


class NerExtractRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2_000_000)
    labels: list[str] | dict[str, str] = Field(..., description="Entity labels or label descriptions.")
    model_id: str | None = None
    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0.0, le=1.0)
    max_chars: int = Field(default=DEFAULT_MAX_CHARS, ge=500, le=100_000)
    overlap_chars: int = Field(default=DEFAULT_OVERLAP_CHARS, ge=0, le=20_000)

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text is empty after stripping whitespace")
        return value

    @field_validator("labels")
    @classmethod
    def _validate_labels(cls, value: list[str] | dict[str, str]) -> list[str] | dict[str, str]:
        if isinstance(value, list):
            cleaned = [str(label).strip() for label in value if str(label).strip()]
            if not cleaned:
                raise ValueError("labels must contain at least one non-empty label")
            return cleaned
        cleaned_dict = {
            str(k).strip(): str(v).strip()
            for k, v in value.items()
            if str(k).strip()
        }
        if not cleaned_dict:
            raise ValueError("labels must contain at least one label")
        return cleaned_dict


class NerBatchExtractRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=128)
    labels: list[str] | dict[str, str]
    model_id: str | None = None
    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0.0, le=1.0)
    max_chars: int = Field(default=DEFAULT_MAX_CHARS, ge=500, le=100_000)
    overlap_chars: int = Field(default=DEFAULT_OVERLAP_CHARS, ge=0, le=20_000)


class NerEntityResponse(BaseModel):
    text: str
    label: str
    start: int
    end: int
    score: float | None = None


class NerExtractResponse(BaseModel):
    entities: list[NerEntityResponse]
    model_id: str
    repo_id: str
    elapsed_seconds: float
    chunk_count: int


class NerBatchExtractResponse(BaseModel):
    results: list[NerExtractResponse]


async def _raise_ner(exc: NerError, operation_key: str) -> None:
    status_map = {
        "empty_text": 400,
        "empty_labels": 400,
        "invalid_chunk_size": 400,
        "invalid_overlap": 400,
        "invalid_threshold": 400,
        "unknown_model": 404,
        "in_progress": 409,
        "model_not_downloaded": 409,
        "missing_dependency": 503,
        "download_failed": 502,
        "hf_token_missing": 409,
        "hf_token_invalid": 409,
        "hf_gate_not_accepted": 409,
        "hf_gate_pending": 409,
        "load_failed": 500,
        "batch_too_large": 413,
    }
    await get_action_needed_registry().reconcile_operation(
        operation_key, exc.action_needed
    )
    raise HTTPException(
        status_code=status_map.get(exc.code, 500),
        detail={
            "detail": exc.message,
            "code": exc.code,
            "action_needed": (
                exc.action_needed.model_dump(mode="json", exclude_none=True)
                if exc.action_needed
                else None
            ),
        },
    ) from exc


def _to_response(result: NerExtraction) -> NerExtractResponse:
    return NerExtractResponse(
        entities=[NerEntityResponse(**span.__dict__) for span in result.entities],
        model_id=result.model_id,
        repo_id=result.repo_id,
        elapsed_seconds=result.elapsed_seconds,
        chunk_count=result.chunk_count,
    )


@router.get("/status", response_model=NerStatusResponse)
async def ner_status() -> NerStatusResponse:
    return NerStatusResponse(**get_ner_service().get_status())


@router.get("/models")
async def ner_models() -> list[dict[str, Any]]:
    return get_ner_service().list_models()


@router.post("/download", response_model=NerDownloadResponse)
@safe_route("ner_download")
async def ner_download(req: NerDownloadRequest) -> NerDownloadResponse:
    operation_key = f"ner.download:{req.model_id or 'default'}"
    try:
        result = await get_ner_service().download_model(req.model_id, force=req.force)
    except NerError as exc:
        await _raise_ner(exc, operation_key)
    await get_action_needed_registry().reconcile_operation(operation_key, None)
    return NerDownloadResponse(**result)


@router.post("/extract", response_model=NerExtractResponse)
@safe_route("ner_extract")
async def ner_extract(req: NerExtractRequest) -> NerExtractResponse:
    operation_key = f"ner.extract:{req.model_id or 'default'}"
    try:
        result = await get_ner_service().extract(
            text=req.text,
            labels=req.labels,
            model_id=req.model_id,
            threshold=req.threshold,
            max_chars=req.max_chars,
            overlap_chars=req.overlap_chars,
        )
    except NerError as exc:
        await _raise_ner(exc, operation_key)
    await get_action_needed_registry().reconcile_operation(operation_key, None)
    return _to_response(result)


@router.post("/extract/batch", response_model=NerBatchExtractResponse)
@safe_route("ner_extract_batch")
async def ner_extract_batch(req: NerBatchExtractRequest) -> NerBatchExtractResponse:
    operation_key = f"ner.extract-batch:{req.model_id or 'default'}"
    try:
        results = await get_ner_service().extract_batch(
            texts=req.texts,
            labels=req.labels,
            model_id=req.model_id,
            threshold=req.threshold,
            max_chars=req.max_chars,
            overlap_chars=req.overlap_chars,
        )
    except NerError as exc:
        await _raise_ner(exc, operation_key)
    await get_action_needed_registry().reconcile_operation(operation_key, None)
    return NerBatchExtractResponse(results=[_to_response(r) for r in results])
