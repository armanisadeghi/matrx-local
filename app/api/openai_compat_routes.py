"""OpenAI-compatible /v1 surface over local desktop models.

This router intentionally lives behind the engine's normal AuthMiddleware:
loopback callers need a Bearer token, and tunnel callers need a verified
Supabase JWT for this instance owner. Do not add these routes to _PUBLIC_PATHS.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import tempfile
import time
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.common.system_logger import get_logger
from app.services.ai.local_llm_registry import resolve_local_llm_model

logger = get_logger()

router = APIRouter(prefix="/v1", tags=["openai-compatible"])

_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)
_NON_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
_MAX_SPEECH_INPUT_CHARS = 50_000
_MAX_TRANSCRIPTION_BYTES = 25 * 1024 * 1024
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
    return httpx.AsyncClient(timeout=_STREAM_TIMEOUT, follow_redirects=False)


def _new_non_stream_upstream_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_NON_STREAM_TIMEOUT, follow_redirects=False)


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


@lru_cache(maxsize=4)
def _load_embedding_model(model_name: str):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=model_name)


async def _embed_texts(texts: tuple[str, ...], model_name: str) -> list[list[float]]:
    def _run() -> list[list[float]]:
        model = _load_embedding_model(model_name)
        vectors = model.embed(list(texts))
        return [vector.astype(float).tolist() for vector in vectors]

    return await asyncio.to_thread(_run)


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
    if importlib.util.find_spec("fastembed") is not None:
        data.append(
            {
                "id": f"local/{_DEFAULT_EMBEDDING_MODEL}",
                "object": "model",
                "created": 0,
                "owned_by": "local",
                "root": _DEFAULT_EMBEDDING_MODEL,
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
        async with _new_non_stream_upstream_client() as client:
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


def _get_tts_service():
    from app.services.tts.service import get_tts_service

    return get_tts_service()


async def _transcribe_audio_file(
    file_path: str,
    *,
    model: str,
    language: str | None,
):
    from app.tools.session import ToolSession
    from app.tools.tools.audio import tool_transcribe_audio

    return await tool_transcribe_audio(
        ToolSession(working_dir=os.path.dirname(file_path)),
        file_path=file_path,
        model=model,
        language=language,
    )


@router.post("/audio/speech")
async def audio_speech(request: Request) -> Response:
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

    text = payload.get("input")
    if not isinstance(text, str) or not text.strip():
        return _openai_error(
            "Missing required parameter: 'input'.",
            status_code=400,
            code="missing_required_parameter",
            param="input",
        )
    text = text.strip()
    if len(text) > _MAX_SPEECH_INPUT_CHARS:
        return _openai_error(
            f"Parameter 'input' exceeds {_MAX_SPEECH_INPUT_CHARS} characters.",
            status_code=413,
            code="input_too_large",
            param="input",
        )

    response_format = str(payload.get("response_format") or "wav").lower()
    if response_format != "wav":
        return _openai_error(
            "Only response_format='wav' is currently supported by the local TTS engine.",
            status_code=400,
            code="unsupported_response_format",
            param="response_format",
        )

    voice = str(payload.get("voice") or "af_heart")
    speed_raw = payload.get("speed", 1.0)
    try:
        speed = float(speed_raw)
    except (TypeError, ValueError):
        return _openai_error(
            "Parameter 'speed' must be a number.",
            status_code=400,
            code="invalid_type",
            param="speed",
        )
    if speed < 0.5 or speed > 2.0:
        return _openai_error(
            "Parameter 'speed' must be between 0.5 and 2.0.",
            status_code=400,
            code="invalid_value",
            param="speed",
        )

    svc = _get_tts_service()
    if not svc.model_downloaded:
        try:
            svc.maybe_start_background_download()
        except Exception:
            logger.debug("[openai_compat] TTS background download check failed", exc_info=True)
        return _openai_error(
            "The local TTS model is not downloaded on this machine.",
            status_code=409,
            code="model_not_downloaded",
            param="model",
        )

    result = await svc.synthesize(
        text=text,
        voice_id=voice,
        speed=speed,
        lang=payload.get("lang") if isinstance(payload.get("lang"), str) else None,
    )
    if not result.success or result.audio_bytes is None:
        code = result.error_code or "tts_synthesis_failed"
        status = {
            "voice_not_found": 404,
            "invalid_id": 400,
            "empty_text": 400,
            "model_not_downloaded": 409,
            "model_missing": 503,
        }.get(code, 500)
        return _openai_error(
            result.error or "Local TTS synthesis failed.",
            status_code=status,
            code=code,
            param="voice" if code == "voice_not_found" else None,
        )

    return Response(
        content=result.audio_bytes,
        media_type="audio/wav",
        headers={
            "X-TTS-Voice": result.voice_id,
            "X-TTS-Duration": str(result.duration_seconds),
            "X-TTS-Sample-Rate": str(result.sample_rate),
        },
    )


@router.post("/audio/transcriptions")
async def audio_transcriptions(
    file: UploadFile = File(...),
    model: str = Form(default="base"),
    language: str | None = Form(default=None),
    response_format: str = Form(default="json"),
) -> Response:
    normalized_format = (response_format or "json").lower()
    if normalized_format not in {"json", "text", "verbose_json"}:
        return _openai_error(
            "Only response_format values 'json', 'text', and 'verbose_json' are currently supported.",
            status_code=400,
            code="unsupported_response_format",
            param="response_format",
        )

    local_model = "base" if model == "whisper-1" else model
    suffix = os.path.splitext(file.filename or "")[1] or ".audio"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            total = 0
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_TRANSCRIPTION_BYTES:
                    return _openai_error(
                        f"Audio upload exceeds {_MAX_TRANSCRIPTION_BYTES} bytes.",
                        status_code=413,
                        code="file_too_large",
                        param="file",
                    )
                tmp.write(chunk)

        result = await _transcribe_audio_file(
            tmp_path,
            model=local_model,
            language=language,
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.debug("[openai_compat] could not delete transcription temp file", exc_info=True)

    from app.tools.types import ToolResultType

    if result.type == ToolResultType.ERROR:
        metadata = result.metadata or {}
        code = (
            "missing_dependency"
            if metadata.get("fix_capability_id") == "transcription"
            else "transcription_failed"
        )
        status = 503 if code == "missing_dependency" else 500
        return _openai_error(
            result.output or "Local transcription failed.",
            status_code=status,
            code=code,
        )

    metadata = result.metadata or {}
    text = str(metadata.get("text") or "").strip()
    if normalized_format == "text":
        return Response(content=text, media_type="text/plain")

    body: dict[str, Any] = {"text": text}
    if normalized_format == "verbose_json":
        body.update(
            {
                "task": "transcribe",
                "language": metadata.get("language"),
                "duration": None,
                "segments": metadata.get("segments", []),
            }
        )
    return JSONResponse(content=body)


@router.post("/embeddings")
async def embeddings(request: Request) -> JSONResponse:
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

    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        texts = (raw_input,)
    elif isinstance(raw_input, list) and all(isinstance(item, str) for item in raw_input):
        texts = tuple(raw_input)
    else:
        return _openai_error(
            "Parameter 'input' must be a string or an array of strings.",
            status_code=400,
            code="invalid_type",
            param="input",
        )

    if not texts or any(not text for text in texts):
        return _openai_error(
            "Parameter 'input' must not be empty.",
            status_code=400,
            code="invalid_value",
            param="input",
        )

    encoding_format = str(payload.get("encoding_format") or "float").lower()
    if encoding_format != "float":
        return _openai_error(
            "Only encoding_format='float' is currently supported.",
            status_code=400,
            code="unsupported_encoding_format",
            param="encoding_format",
        )

    model_name = str(payload.get("model") or _DEFAULT_EMBEDDING_MODEL)
    if model_name.startswith("local/"):
        model_name = model_name.removeprefix("local/")

    try:
        vectors = await _embed_texts(texts, model_name)
    except ImportError:
        return _openai_error(
            "Local embeddings are not installed. Install the optional 'embeddings' extra to enable /v1/embeddings.",
            status_code=503,
            code="missing_dependency",
            param="model",
        )
    except ValueError as exc:
        return _openai_error(
            str(exc),
            status_code=404,
            code="model_not_found",
            param="model",
        )
    except Exception as exc:
        logger.warning("[openai_compat] local embeddings failed: %s", exc)
        return _openai_error(
            "Local embedding generation failed.",
            status_code=500,
            code="embedding_failed",
        )

    return JSONResponse(
        content={
            "object": "list",
            "data": [
                {"object": "embedding", "index": idx, "embedding": vector}
                for idx, vector in enumerate(vectors)
            ],
            "model": model_name,
            "usage": {
                "prompt_tokens": sum(len(text.split()) for text in texts),
                "total_tokens": sum(len(text.split()) for text in texts),
            },
        }
    )
