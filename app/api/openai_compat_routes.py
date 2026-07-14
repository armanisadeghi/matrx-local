"""OpenAI-compatible /v1 surface over local desktop models.

This router intentionally lives behind the engine's normal AuthMiddleware:
loopback callers need a Bearer token, and tunnel callers need a verified
Supabase JWT for this instance owner. Do not add these routes to _PUBLIC_PATHS.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.common.system_logger import get_logger
from app.services.ai.local_llm_registry import resolve_local_llm_model

logger = get_logger()

router = APIRouter(prefix="/v1", tags=["openai-compatible"])

_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _openai_error(
    message: str,
    *,
    status_code: int,
    code: str,
    param: str | None = None,
    error_type: str = "invalid_request_error",
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": param,
                "code": code,
            }
        },
    )


def _response_headers(headers: httpx.Headers) -> dict[str, str]:
    """Copy safe upstream response headers."""
    out: dict[str, str] = {}
    for key, value in headers.items():
        lk = key.lower()
        if lk in _HOP_BY_HOP_HEADERS or lk in {"content-length", "content-encoding"}:
            continue
        out[key] = value
    return out


def _client_headers(request: Request) -> dict[str, str]:
    """Forward useful client negotiation headers, never the Supabase JWT."""
    headers: dict[str, str] = {"accept-encoding": "identity"}
    for key in ("accept", "user-agent"):
        value = request.headers.get(key)
        if value:
            headers[key] = value
    return headers


def _new_upstream_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)


def _normalize_chat_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    requested_model = payload.get("model")
    if not isinstance(requested_model, str) or not requested_model.strip():
        return payload, None

    target = resolve_local_llm_model(requested_model.strip())
    if target is None:
        return payload, None

    forwarded = dict(payload)
    forwarded["model"] = target["model_name"]
    return forwarded, target


async def _stream_upstream(
    client: httpx.AsyncClient,
    upstream: httpx.Response,
) -> AsyncIterator[bytes]:
    try:
        async for chunk in upstream.aiter_raw():
            if chunk:
                yield chunk
    finally:
        await upstream.aclose()
        await client.aclose()


@router.get("/models")
async def list_models() -> dict[str, Any]:
    target = resolve_local_llm_model()
    data: list[dict[str, Any]] = []
    if target is not None:
        data.append(
            {
                "id": target["canonical_model_name"],
                "object": "model",
                "created": 0,
                "owned_by": "local",
                "root": target["model_name"],
                "parent": None,
            }
        )
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(request: Request) -> Response:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return _openai_error(
            "Request body must be valid JSON.",
            status_code=400,
            code="invalid_json",
        )

    if not isinstance(payload, dict):
        return _openai_error(
            "Request body must be a JSON object.",
            status_code=400,
            code="invalid_request",
        )

    requested_model = payload.get("model")
    if not isinstance(requested_model, str) or not requested_model.strip():
        return _openai_error(
            "Missing required parameter: 'model'.",
            status_code=400,
            code="missing_required_parameter",
            param="model",
        )

    forwarded, target = _normalize_chat_payload(payload)
    if target is None:
        active = resolve_local_llm_model()
        if active is None:
            return _openai_error(
                "No local model is currently running on this machine.",
                status_code=503,
                code="local_model_not_available",
                param="model",
            )
        return _openai_error(
            f"The model '{requested_model}' is not served by this machine.",
            status_code=404,
            code="model_not_found",
            param="model",
        )

    url = f"{target['base_url']}/chat/completions"
    stream = bool(payload.get("stream"))
    started = time.monotonic()

    if stream:
        client = _new_upstream_client()
        try:
            upstream_request = client.build_request(
                "POST",
                url,
                json=forwarded,
                headers=_client_headers(request),
            )
            upstream = await client.send(upstream_request, stream=True)
        except httpx.TimeoutException:
            await client.aclose()
            return _openai_error(
                "Timed out connecting to the local model server.",
                status_code=504,
                code="local_model_timeout",
            )
        except httpx.TransportError as exc:
            await client.aclose()
            logger.warning("[openai_compat] local chat stream proxy failed: %s", exc)
            return _openai_error(
                "Could not connect to the local model server.",
                status_code=502,
                code="local_model_unreachable",
            )

        if upstream.status_code >= 400:
            body = await upstream.aread()
            headers = _response_headers(upstream.headers)
            status = upstream.status_code
            await upstream.aclose()
            await client.aclose()
            return Response(
                content=body,
                status_code=status,
                media_type=headers.pop("content-type", None),
                headers=headers,
            )

        logger.info(
            "[openai_compat] streaming chat proxied to %s in %.3fs",
            target["canonical_model_name"],
            time.monotonic() - started,
        )
        return StreamingResponse(
            _stream_upstream(client, upstream),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers=_response_headers(upstream.headers),
        )

    try:
        async with _new_upstream_client() as client:
            upstream = await client.post(
                url,
                json=forwarded,
                headers=_client_headers(request),
            )
    except httpx.TimeoutException:
        return _openai_error(
            "Timed out connecting to the local model server.",
            status_code=504,
            code="local_model_timeout",
        )
    except httpx.TransportError as exc:
        logger.warning("[openai_compat] local chat proxy failed: %s", exc)
        return _openai_error(
            "Could not connect to the local model server.",
            status_code=502,
            code="local_model_unreachable",
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        headers=_response_headers(upstream.headers),
    )
