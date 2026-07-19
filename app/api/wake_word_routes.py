"""FastAPI routes for the openWakeWord detection engine.

These routes mirror the Tauri IPC commands exposed by the Rust whisper-tiny
wake word engine so the frontend hook can talk to either backend with minimal
branching:

  GET  /wake-word/status           — current state (running, mode, model, etc.)
  POST /wake-word/start            — start the detection loop
  POST /wake-word/stop             — stop it completely
  POST /wake-word/mute             — mute (keep loop alive)
  POST /wake-word/unmute           — resume after mute
  POST /wake-word/dismiss          — 10-second false-trigger cooldown
  POST /wake-word/trigger          — manual trigger (for testing)
  GET  /wake-word/stream           — Server-Sent Events stream of detection events
  GET  /wake-word/models           — list available + downloaded OWW models
  POST /wake-word/models/download  — download a pre-trained model
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.common.system_logger import get_logger
from app.common.process_shutdown import process_shutdown_requested
from app.services.wake_word.service import get_wake_word_service
from app.services.wake_word.models import (
    list_available_models,
    download_model,
    model_exists,
    OWWModelInfo,
)

logger = get_logger()
router = APIRouter(prefix="/wake-word", tags=["wake-word"])


# ── Pydantic models ────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    model_name: str | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    device_name: str | None = None


class ConfigureRequest(BaseModel):
    model_name: str | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class DownloadRequest(BaseModel):
    model_name: str


class WakeWordStatus(BaseModel):
    running: bool
    mode: str
    model_name: str
    threshold: float


class ModelInfoResponse(BaseModel):
    name: str
    filename: str
    downloaded: bool
    size_mb: float
    description: str
    is_built_in: bool
    is_custom: bool


class ModelsResponse(BaseModel):
    models: list[ModelInfoResponse]


# ── Helper ─────────────────────────────────────────────────────────────────────

def _model_to_response(m: OWWModelInfo) -> ModelInfoResponse:
    return ModelInfoResponse(
        name=m.name,
        filename=m.filename,
        downloaded=m.downloaded,
        size_mb=m.size_mb,
        description=m.description,
        is_built_in=m.is_built_in,
        is_custom=m.is_custom,
    )


# ── Status ─────────────────────────────────────────────────────────────────────

@router.get("/status", response_model=WakeWordStatus)
async def get_status() -> WakeWordStatus:
    svc = get_wake_word_service()
    return WakeWordStatus(
        running=svc.running,
        mode=svc.mode,
        model_name=svc.model_name,
        threshold=svc.threshold,
    )


# ── Control ────────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_wake_word(req: StartRequest) -> dict[str, str]:
    """Start the OWW detection loop.  Idempotent if already running."""
    svc = get_wake_word_service()
    try:
        await svc.start(
            model_name=req.model_name,
            threshold=req.threshold,
            device_name=req.device_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "started"}


@router.post("/stop")
async def stop_wake_word() -> dict[str, str]:
    svc = get_wake_word_service()
    await svc.stop()
    return {"status": "stopped"}


@router.post("/mute")
async def mute_wake_word() -> dict[str, str]:
    svc = get_wake_word_service()
    await svc.mute()
    return {"status": "muted"}


@router.post("/unmute")
async def unmute_wake_word() -> dict[str, str]:
    svc = get_wake_word_service()
    await svc.unmute()
    return {"status": "listening"}


@router.post("/dismiss")
async def dismiss_wake_word() -> dict[str, str]:
    svc = get_wake_word_service()
    await svc.dismiss()
    return {"status": "dismissed"}


@router.post("/trigger")
async def trigger_wake_word() -> dict[str, str]:
    """Manually fire a wake-word-detected event (for testing/dev)."""
    svc = get_wake_word_service()
    await svc._emit("wake-word-detected", {"keyword": "MANUAL", "score": 1.0})
    return {"status": "triggered"}


@router.post("/configure")
async def configure_wake_word(req: ConfigureRequest) -> dict[str, str]:
    svc = get_wake_word_service()
    await svc.configure(model_name=req.model_name, threshold=req.threshold)
    return {"status": "configured"}


# ── SSE stream ─────────────────────────────────────────────────────────────────

@router.get("/stream")
async def event_stream() -> StreamingResponse:
    """Server-Sent Events stream that forwards OWW detection events to the frontend.

    Event names and payload shapes exactly match the Tauri events emitted by
    the Rust whisper-tiny wake word engine:

        event: wake-word-detected
        data: {"keyword": "hey_jarvis", "score": 0.95}

        event: wake-word-rms
        data: 0.42

        event: wake-word-mode
        data: "listening"

        event: wake-word-error
        data: "some error message"
    """

    async def _generator():
        svc = get_wake_word_service()
        # Send an initial heartbeat so the browser EventSource knows it's alive
        yield "event: ping\ndata: {}\n\n"
        try:
            loop = asyncio.get_running_loop()
            next_keepalive = loop.time() + 15.0
            while not process_shutdown_requested():
                evt = await svc.next_event(timeout=0.25)
                if evt is None:
                    if loop.time() >= next_keepalive:
                        # Send a keepalive comment to prevent proxy timeouts
                        yield ": keepalive\n\n"
                        next_keepalive = loop.time() + 15.0
                    continue
                event_name = evt["event"]
                data = evt["data"]
                # SSE format: "event: <name>\ndata: <json>\n\n"
                payload = json.dumps(data) if not isinstance(data, str) else json.dumps(data)
                yield f"event: {event_name}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Model management ───────────────────────────────────────────────────────────

@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    models = list_available_models()
    return ModelsResponse(models=[_model_to_response(m) for m in models])


@router.post("/models/download")
async def download_oww_model(req: DownloadRequest) -> ModelInfoResponse:
    """Download a pre-trained model from HuggingFace.  Runs synchronously (small files).

    For progress feedback, use POST /wake-word/models/download-stream (SSE).
    """
    if model_exists(req.model_name):
        # Already present — return info without re-downloading
        models = list_available_models()
        existing = next((m for m in models if m.name == req.model_name), None)
        if existing:
            return _model_to_response(existing)

    try:
        info = await download_model(req.model_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Failed to download OWW model {req.model_name}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return _model_to_response(info)


@router.post("/models/download-stream")
async def download_model_with_progress(req: DownloadRequest) -> StreamingResponse:
    """Download a model and stream progress as SSE events.

    Events:
        progress: {"bytes_done": int, "total_bytes": int, "percent": float}
        complete: {"name": str, "size_mb": float}
        error:    str
    """

    async def _gen():
        download_task: asyncio.Task | None = None
        try:
            progress_events: list[tuple[int, int]] = []

            async def on_progress(done: int, total: int) -> None:
                progress_events.append((done, total))

            # Start download (runs to completion, progress buffered above)
            # We run in a task so we can yield SSE concurrently
            download_task = asyncio.create_task(
                download_model(req.model_name, on_progress=on_progress)
            )

            while not download_task.done() and not process_shutdown_requested():
                await asyncio.sleep(0.1)
                for done, total in progress_events:
                    pct = round(done / total * 100, 1) if total > 0 else 0.0
                    payload = json.dumps({"bytes_done": done, "total_bytes": total, "percent": pct})
                    yield f"event: progress\ndata: {payload}\n\n"
                progress_events.clear()

            if process_shutdown_requested():
                return

            info = await download_task
            yield f"event: complete\ndata: {json.dumps({'name': info.name, 'size_mb': info.size_mb})}\n\n"

        except ValueError as exc:
            yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"
        except Exception as exc:
            logger.exception(f"Download stream error: {exc}")
            yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"
        finally:
            if download_task is not None and not download_task.done():
                download_task.cancel()
                await asyncio.gather(download_task, return_exceptions=True)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
