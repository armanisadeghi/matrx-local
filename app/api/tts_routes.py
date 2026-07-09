"""Text-to-speech API routes (Kokoro-82M via ONNX Runtime).

Error contract
--------------
Non-streaming routes raise ``HTTPException`` with the following codes:
  * 400 — bad input (empty text, etc.)
  * 404 — voice not found
  * 409 — download already in progress
  * 422 — synthesis-time validation (e.g. unknown lang/voice)
  * 500 — internal/Kokoro failure
  * 503 — model not yet downloaded

The streaming endpoint (``/synthesize-stream``) does **not** use HTTP status
codes for mid-stream errors — it always returns 200 and sends an in-stream
``error`` frame followed by the terminating ``end`` frame. See
``services/tts/models.py`` for the v2 wire format.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.common.route_errors import safe_route
from app.services.tts.models import (
    MAX_VOICE_IMPORT_BYTES,
    SAMPLE_RATE,
    STREAM_PROTOCOL_VERSION,
)
from app.services.tts.service import TtsError, get_tts_service

router = APIRouter(prefix="/tts", tags=["tts"])


# ── Request / Response schemas ────────────────────────────────────────────────

class TtsStatusResponse(BaseModel):
    model_downloaded: bool
    model_loaded: bool
    is_downloading: bool
    download_progress: float
    model_dir: str
    voice_count: int
    needs_download: bool = False
    auto_download: bool = False


class TtsVoiceInfo(BaseModel):
    voice_id: str
    name: str
    gender: str
    language: str
    lang_code: str
    quality_grade: str
    traits: list[str] = []
    is_custom: bool = False
    is_default: bool = False
    blend_recipe: list[dict] = []


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50_000)
    voice_id: str = Field(default="af_heart")
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    lang: str | None = Field(default=None, description="Override espeak language code (e.g. 'en-us')")
    streaming_threshold: int | None = Field(
        default=None,
        ge=1,
        le=50_000,
        description="Optional override of the short/long streaming cutoff (chars). "
                    "When omitted, the server's tts_streaming_threshold setting is used.",
    )

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("text is empty after stripping whitespace")
        return stripped


class PreviewVoiceRequest(BaseModel):
    voice_id: str


class DownloadResponse(BaseModel):
    success: bool
    already_downloaded: bool = False
    error: str | None = None
    error_code: str | None = None


# ── Voice blending schemas ─────────────────────────────────────────────────────

class BlendComponent(BaseModel):
    voice_id: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class BlendPreviewRequest(BaseModel):
    components: list[BlendComponent] = Field(..., min_length=1)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    lang: str = Field(default="en-us")


class SaveBlendRequest(BaseModel):
    voice_id: str = Field(..., min_length=1, max_length=64,
                          description="Filesystem-safe ID (letters, digits, _ -)")
    name: str = Field(..., min_length=1, max_length=80)
    components: list[BlendComponent] = Field(..., min_length=1)
    gender: str = Field(default="female")
    lang_code: str = Field(default="a")


class RenameVoiceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class ActionResponse(BaseModel):
    success: bool
    voice_id: str = ""
    error: str | None = None
    error_code: str | None = None


# ── Internal helpers ──────────────────────────────────────────────────────────


def _raise_for_result(result, default_status: int = 500) -> None:
    """Translate a service ``error_code`` to an HTTP status code."""
    code = (result.error_code if hasattr(result, "error_code") else result.get("error_code")) or ""
    msg = (result.error if hasattr(result, "error") else result.get("error")) or "Unknown error"
    status_map = {
        "empty_text": 400,
        "invalid_id": 400,
        "bad_format": 400,
        "bad_shape": 400,
        "voice_not_found": 404,
        "in_progress": 409,
        "model_not_downloaded": 409,
        "invalid_blend": 422,
        "model_missing": 503,
    }
    status = status_map.get(code, default_status)
    raise HTTPException(status_code=status, detail={"detail": msg, "code": code})


def _raise_if_not_downloaded(svc) -> None:
    """Fail fast with a structured 409 when the Kokoro model is not on disk.

    This is what prevents a synth request from silently triggering a 330 MB
    download and timing out the client. When ``tts_auto_download_model`` is
    enabled, the download is kicked off in the background here (never blocks).
    """
    if svc.model_downloaded:
        return
    svc.maybe_start_background_download()
    st = svc.get_status()
    raise HTTPException(
        status_code=409,
        detail={
            "detail": "tts model not downloaded",
            "code": "model_not_downloaded",
            "needs_download": True,
            "downloading": bool(st.get("is_downloading")),
        },
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/status", response_model=TtsStatusResponse)
async def tts_status() -> TtsStatusResponse:
    svc = get_tts_service()
    return TtsStatusResponse(**svc.get_status())


@router.get("/voices", response_model=list[TtsVoiceInfo])
async def list_voices() -> list[TtsVoiceInfo]:
    svc = get_tts_service()
    return [TtsVoiceInfo(**v) for v in svc.list_voices()]


@router.post("/download-model", response_model=DownloadResponse)
async def download_model() -> DownloadResponse:
    svc = get_tts_service()
    result = await svc.download_model()
    if not result.get("success") and result.get("error_code") == "in_progress":
        raise HTTPException(
            status_code=409,
            detail={"detail": result.get("error", "Download in progress"),
                    "code": "in_progress"},
        )
    return DownloadResponse(**result)


@router.post("/synthesize")
@safe_route("tts_synthesize")
async def synthesize(req: SynthesizeRequest) -> Response:
    """Generate speech and return audio/wav bytes."""
    svc = get_tts_service()
    _raise_if_not_downloaded(svc)
    result = await svc.synthesize(
        text=req.text, voice_id=req.voice_id, speed=req.speed, lang=req.lang,
    )
    if not result.success or result.audio_bytes is None:
        _raise_for_result(result)

    return Response(
        content=result.audio_bytes,
        media_type="audio/wav",
        headers={
            "X-TTS-Duration": str(result.duration_seconds),
            "X-TTS-Voice": result.voice_id,
            "X-TTS-Elapsed": str(result.elapsed_seconds),
            "X-TTS-Sample-Rate": str(result.sample_rate),
        },
    )


@router.post("/synthesize-stream")
async def synthesize_stream(req: SynthesizeRequest, request: Request) -> StreamingResponse:
    """Stream speech as v2 framed bytes.

    Wire format (each frame):
        1 byte tag  ·  4 bytes BE uint32 length  ·  N bytes payload

    Tags: 0x01 chunk (WAV) · 0x02 end · 0xFF error (UTF-8 JSON).

    Errors are surfaced as in-stream error frames (HTTP 200 with the response
    body containing the error frame). The client must rely on receiving an
    explicit ``end`` frame to detect a clean finish — anything else is a
    truncation error.

    The server polls ``request.is_disconnected()`` between chunks and stops
    yielding promptly when the client aborts.
    """
    svc = get_tts_service()
    # Pre-check before opening the stream so the client gets a clean 409 instead
    # of waiting on a 330 MB download behind the streaming socket (30s timeout).
    _raise_if_not_downloaded(svc)

    async def _gen() -> AsyncIterator[bytes]:
        async for frame in svc.synthesize_stream(
            text=req.text,
            voice_id=req.voice_id,
            speed=req.speed,
            lang=req.lang,
            is_disconnected=request.is_disconnected,
            streaming_threshold=req.streaming_threshold,
        ):
            yield frame

    return StreamingResponse(
        _gen(),
        media_type="application/octet-stream",
        headers={
            "X-TTS-Voice": req.voice_id,
            "X-TTS-Stream-Protocol": str(STREAM_PROTOCOL_VERSION),
            "X-TTS-Sample-Rate": str(SAMPLE_RATE),
            "Cache-Control": "no-cache",
        },
    )


@router.post("/preview-voice")
@safe_route("tts_preview_voice")
async def preview_voice(req: PreviewVoiceRequest) -> Response:
    """Generate a short preview clip for a given voice."""
    svc = get_tts_service()
    _raise_if_not_downloaded(svc)
    result = await svc.preview_voice(req.voice_id)
    if not result.success or result.audio_bytes is None:
        _raise_for_result(result)

    return Response(
        content=result.audio_bytes,
        media_type="audio/wav",
        headers={
            "X-TTS-Duration": str(result.duration_seconds),
            "X-TTS-Voice": result.voice_id,
        },
    )


@router.delete("/unload")
async def unload() -> dict:
    svc = get_tts_service()
    return await svc.unload()


# ── Voice blending endpoints ───────────────────────────────────────────────────


@router.post("/blend/preview")
async def blend_preview(req: BlendPreviewRequest) -> Response:
    """Blend voices and return a preview WAV without saving."""
    svc = get_tts_service()
    components = [c.model_dump() for c in req.components]
    result = await svc.blend_and_preview(components, speed=req.speed, lang=req.lang)
    if not result.success or result.audio_bytes is None:
        _raise_for_result(result)
    return Response(
        content=result.audio_bytes,
        media_type="audio/wav",
        headers={
            "X-TTS-Duration": str(result.duration_seconds),
            "X-TTS-Voice": "blend-preview",
        },
    )


@router.post("/blend/save", response_model=ActionResponse)
async def blend_save(req: SaveBlendRequest) -> ActionResponse:
    svc = get_tts_service()
    components = [c.model_dump() for c in req.components]
    result = await svc.save_blended_voice(
        voice_id=req.voice_id,
        name=req.name,
        components=components,
        gender=req.gender,
        lang_code=req.lang_code,
    )
    if not result.get("success"):
        _raise_for_result(result)
    return ActionResponse(**result)


# ── Custom voice management ────────────────────────────────────────────────────


@router.get("/custom-voices", response_model=list[TtsVoiceInfo])
async def list_custom_voices() -> list[TtsVoiceInfo]:
    svc = get_tts_service()
    return [TtsVoiceInfo(**v) for v in svc._load_custom_voices()]


@router.patch("/custom-voices/{voice_id}", response_model=ActionResponse)
async def rename_custom_voice(voice_id: str, req: RenameVoiceRequest) -> ActionResponse:
    svc = get_tts_service()
    result = await svc.rename_custom_voice(voice_id, req.name)
    if not result.get("success"):
        _raise_for_result(result)
    return ActionResponse(**result)


@router.delete("/custom-voices/{voice_id}", response_model=ActionResponse)
async def delete_custom_voice(voice_id: str) -> ActionResponse:
    svc = get_tts_service()
    result = await svc.delete_custom_voice(voice_id)
    if not result.get("success"):
        _raise_for_result(result)
    return ActionResponse(**result)


@router.post("/custom-voices/import", response_model=ActionResponse)
async def import_voice_file(
    request: Request,
    file: UploadFile = File(...),
    voice_id: str = Form(...),
    name: str = Form(...),
    gender: str = Form(default="female"),
    lang_code: str = Form(default="a"),
) -> ActionResponse:
    """Import a custom voice from an uploaded .npy or .bin file.

    Bounded by ``MAX_VOICE_IMPORT_BYTES`` (5 MB). Real Kokoro embeddings are
    ~520 KB; anything beyond a few MB is suspicious.
    """
    # Cheap content-length check so we don't even start reading huge bodies.
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_VOICE_IMPORT_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={"detail": f"Upload exceeds {MAX_VOICE_IMPORT_BYTES} bytes",
                            "code": "too_large"},
                )
        except ValueError:
            pass

    # Read with a hard cap; if we hit the limit we abort.
    content = await file.read(MAX_VOICE_IMPORT_BYTES + 1)
    if len(content) > MAX_VOICE_IMPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"detail": f"Upload exceeds {MAX_VOICE_IMPORT_BYTES} bytes",
                    "code": "too_large"},
        )

    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    svc = get_tts_service()
    result = await svc.import_voice_file(
        voice_id=voice_id,
        name=name,
        data=content,
        file_ext=ext,
        gender=gender,
        lang_code=lang_code,
    )
    if not result.get("success"):
        _raise_for_result(result)
    return ActionResponse(**result)
