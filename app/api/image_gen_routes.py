"""Image generation API routes.

All imports of the optional diffusers/torch packages are behind the service
boundary — this module is safe to import even when those packages are not
installed. Endpoints return HTTP 503 with a clear installation message when
the optional dependencies are absent.

/image-gen/install       — POST: start background install of torch + diffusers
/image-gen/install/status — GET: current install state (polling)
/image-gen/install/stream — GET: SSE progress stream during install
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.services.image_gen.service import get_image_gen_service
from app.services.image_gen.models import IMAGE_GEN_MODELS, WORKFLOW_PRESETS
from app.services.image_gen.installer import (
    start_install,
    get_active_progress,
    is_image_gen_installed,
    get_image_gen_packages_dir,
    needs_upgrade,
)
from app.common.route_errors import safe_route

router = APIRouter(prefix="/image-gen", tags=["image-gen"])


# ── Response / Request schemas ────────────────────────────────────────────────

class ImageGenModelInfo(BaseModel):
    model_id: str
    name: str
    provider: str
    pipeline_type: str
    vram_gb: float
    ram_gb: float
    description: str
    quality_rating: int
    speed_rating: int
    recommended_steps: int
    recommended_guidance: float
    supports_negative_prompt: bool
    model_card_url: str
    default_width: int
    default_height: int
    requires_hf_token: bool
    tags: list[str]
    download_size_gb: float
    is_downloaded: bool
    hardware_ok: bool
    hardware_reason: str | None = None


class WorkflowPresetInfo(BaseModel):
    preset_id: str
    name: str
    description: str
    prompt_template: str
    negative_prompt: str
    suggested_model_id: str
    steps: int
    guidance: float
    width: int
    height: int
    tags: list[str]


class ImageGenStatusResponse(BaseModel):
    available: bool
    unavailable_reason: str | None
    loaded_model_id: str | None
    is_loading: bool
    load_progress: float
    packages_version: str | None = None
    """Installed diffusers version (None when packages are not installed)."""
    packages_outdated: bool = False
    """True when diffusers < the catalog minimum — the UI shows an
    "Update AI packages" banner that re-runs POST /image-gen/install."""
    device: str = "cpu"
    """"mps" | "cuda" | "cpu"."""


class DownloadModelRequest(BaseModel):
    model_id: str


class DownloadModelResponse(BaseModel):
    queued: bool
    download_id: str | None = None
    already_downloaded: bool = False


class LoadModelRequest(BaseModel):
    model_id: str


class LoadModelResponse(BaseModel):
    success: bool
    model_id: str | None = None
    device: str | None = None
    already_loaded: bool = False
    error: str | None = None


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)
    model_id: str
    negative_prompt: str = ""
    steps: int | None = Field(None, ge=1, le=150)
    guidance: float | None = Field(None, ge=0.0, le=20.0)
    width: int | None = Field(None, ge=64, le=2048, multiple_of=8)
    height: int | None = Field(None, ge=64, le=2048, multiple_of=8)
    seed: int | None = None


class GenerateResponse(BaseModel):
    success: bool
    image_b64: str | None = None
    """Base64-encoded PNG. Embed as: data:image/png;base64,{image_b64}"""
    width: int = 0
    height: int = 0
    model_id: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None


class WorkflowGenerateRequest(BaseModel):
    preset_id: str
    subject: str = Field(..., min_length=1, max_length=500, description="Fills the {subject} placeholder in the prompt template.")
    model_id: str | None = None
    """Override model. Defaults to the preset's suggested model."""
    seed: int | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status", response_model=ImageGenStatusResponse)
async def image_gen_status() -> ImageGenStatusResponse:
    """Get the current image generation service status."""
    svc = get_image_gen_service()
    status = svc.get_status()
    return ImageGenStatusResponse(**status)


@router.get("/models", response_model=list[ImageGenModelInfo])
async def list_image_gen_models() -> list[ImageGenModelInfo]:
    """List all available image generation models with download + hardware state."""
    svc = get_image_gen_service()
    out: list[ImageGenModelInfo] = []
    for m in IMAGE_GEN_MODELS:
        hw_ok, hw_reason = svc.model_hardware_check(m)
        out.append(ImageGenModelInfo(
            model_id=m.model_id,
            name=m.name,
            provider=m.provider,
            pipeline_type=m.pipeline_type,
            vram_gb=m.vram_gb,
            ram_gb=m.ram_gb,
            description=m.description,
            quality_rating=m.quality_rating,
            speed_rating=m.speed_rating,
            recommended_steps=m.recommended_steps,
            recommended_guidance=m.recommended_guidance,
            supports_negative_prompt=m.supports_negative_prompt,
            model_card_url=m.model_card_url,
            default_width=m.default_width,
            default_height=m.default_height,
            requires_hf_token=m.requires_hf_token,
            tags=list(m.tags),
            download_size_gb=m.download_size_gb,
            is_downloaded=svc.is_downloaded(m.model_id),
            hardware_ok=hw_ok,
            hardware_reason=hw_reason,
        ))
    return out


@router.post("/download", response_model=DownloadModelResponse)
@safe_route("image_gen_download")
async def download_model(req: DownloadModelRequest) -> DownloadModelResponse:
    """Queue the model weights into the universal DownloadManager.

    Progress streams over /downloads/stream (category "image_gen").
    Downloads are resumable and land in ~/.matrx/image-models/<id>/.
    """
    svc = get_image_gen_service()
    result = await svc.start_download(req.model_id)
    if result.get("needs_hf_token"):
        raise HTTPException(status_code=400, detail=result["error"])
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return DownloadModelResponse(
        queued=bool(result.get("queued")),
        download_id=result.get("download_id"),
        already_downloaded=bool(result.get("already_downloaded")),
    )


@router.get("/presets", response_model=list[WorkflowPresetInfo])
async def list_workflow_presets() -> list[WorkflowPresetInfo]:
    """List all workflow presets for one-click generation."""
    return [
        WorkflowPresetInfo(
            preset_id=p.preset_id,
            name=p.name,
            description=p.description,
            prompt_template=p.prompt_template,
            negative_prompt=p.negative_prompt,
            suggested_model_id=p.suggested_model_id,
            steps=p.steps,
            guidance=p.guidance,
            width=p.width,
            height=p.height,
            tags=list(p.tags),
        )
        for p in WORKFLOW_PRESETS
    ]


@router.post("/load", response_model=LoadModelResponse)
@safe_route("image_gen_load")
async def load_model(req: LoadModelRequest) -> LoadModelResponse | JSONResponse:
    """Load a downloaded model into memory. NEVER downloads.

    409 with {"detail": "model not downloaded", "needs_download": true} when
    the weights are not on disk yet — call POST /image-gen/download first.
    """
    svc = get_image_gen_service()
    if not svc.available:
        raise HTTPException(
            status_code=503,
            detail=f"Image generation not available: {svc.unavailable_reason}",
        )
    result = await svc.load_model(req.model_id)
    if result.get("needs_download"):
        # Contract: flat body {"detail": "...", "needs_download": true} — a
        # dict HTTPException detail would nest it under another "detail".
        return JSONResponse(
            status_code=409,
            content={"detail": "model not downloaded", "needs_download": True},
        )
    if result.get("hardware_blocked"):
        raise HTTPException(status_code=409, detail=result.get("error"))
    result.pop("needs_download", None)
    result.pop("hardware_blocked", None)
    return LoadModelResponse(**result)


@router.post("/unload")
async def unload_model() -> dict:
    """Unload the current model and free VRAM."""
    svc = get_image_gen_service()
    return await svc.unload_model()


@router.post("/generate", response_model=GenerateResponse)
@safe_route("image_gen_generate")
async def generate_image(req: GenerateRequest) -> GenerateResponse:
    """Generate an image from a text prompt.

    The model is loaded automatically if not already in memory.
    Returns a base64-encoded PNG in `image_b64`.
    """
    svc = get_image_gen_service()
    if not svc.available:
        raise HTTPException(
            status_code=503,
            detail=f"Image generation not available: {svc.unavailable_reason}",
        )

    result = await svc.generate(
        prompt=req.prompt,
        model_id=req.model_id,
        negative_prompt=req.negative_prompt,
        steps=req.steps,
        guidance=req.guidance,
        width=req.width,
        height=req.height,
        seed=req.seed,
    )
    return GenerateResponse(
        success=result.success,
        image_b64=result.image_b64,
        width=result.width,
        height=result.height,
        model_id=result.model_id,
        elapsed_seconds=result.elapsed_seconds,
        error=result.error,
    )


@router.post("/generate-workflow", response_model=GenerateResponse)
async def generate_from_workflow(req: WorkflowGenerateRequest) -> GenerateResponse:
    """Generate using a preset workflow. The {subject} placeholder is filled with `subject`."""
    svc = get_image_gen_service()
    if not svc.available:
        raise HTTPException(
            status_code=503,
            detail=f"Image generation not available: {svc.unavailable_reason}",
        )

    preset = next((p for p in WORKFLOW_PRESETS if p.preset_id == req.preset_id), None)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"Workflow preset not found: {req.preset_id}")

    prompt = preset.prompt_template.replace("{subject}", req.subject)
    model_id = req.model_id or preset.suggested_model_id

    result = await svc.generate(
        prompt=prompt,
        model_id=model_id,
        negative_prompt=preset.negative_prompt,
        steps=preset.steps,
        guidance=preset.guidance,
        width=preset.width,
        height=preset.height,
        seed=req.seed,
    )
    return GenerateResponse(
        success=result.success,
        image_b64=result.image_b64,
        width=result.width,
        height=result.height,
        model_id=result.model_id,
        elapsed_seconds=result.elapsed_seconds,
        error=result.error,
    )


# ── On-demand package installer ────────────────────────────────────────────────

class InstallStatusResponse(BaseModel):
    status: str
    """idle | running | complete | error"""
    stage: str = ""
    percent: float = 0.0
    message: str = ""
    error: str | None = None
    already_installed: bool = False
    install_dir: str = ""
    log_lines: list[str] = []
    """All accumulated pip output lines — returned on every poll so the UI can
    reconstruct the full log after a reconnect or tab switch."""


def _make_status(
    *,
    status: str,
    stage: str = "",
    percent: float = 0.0,
    message: str = "",
    error: str | None = None,
    already_installed: bool = False,
    progress=None,
) -> InstallStatusResponse:
    log_lines: list[str] = []
    if progress is not None:
        with progress._lock:
            log_lines = list(progress.log_lines)
    return InstallStatusResponse(
        status=status,
        stage=stage,
        percent=percent,
        message=message,
        error=error,
        already_installed=already_installed,
        install_dir=str(get_image_gen_packages_dir()),
        log_lines=log_lines,
    )


@router.post("/install", response_model=InstallStatusResponse)
async def install_image_gen() -> InstallStatusResponse:
    """Start the background installation of torch + diffusers.

    Safe to call multiple times — returns current state if already running or done.

    UPGRADE PATH: when packages are installed but diffusers is older than the
    catalog minimum (needs_upgrade()), this re-runs pip with the upgraded pins
    instead of short-circuiting — same SSE progress stream as a fresh install.
    """
    if is_image_gen_installed() and not needs_upgrade():
        from app.services.image_gen.installer import inject_image_gen_path
        inject_image_gen_path()
        from app.services.image_gen import service as _svc
        _svc.DEPS_AVAILABLE, _svc.DEPS_REASON = _svc._check_deps()
        from app.services.video_gen import service as _vsvc
        _vsvc.DEPS_AVAILABLE, _vsvc.DEPS_REASON = _vsvc._check_deps()
        return _make_status(
            status="complete", stage="done", percent=100.0,
            message="Image generation is already installed.",
            already_installed=True,
        )

    existing = get_active_progress()
    if existing and existing.status == "running":
        return _make_status(
            status=existing.status, stage=existing.stage,
            percent=existing.percent, message=existing.message,
            progress=existing,
        )

    progress = await start_install()
    return _make_status(
        status=progress.status, stage=progress.stage,
        percent=progress.percent, message="Installation started.",
        progress=progress,
    )


@router.get("/install/status", response_model=InstallStatusResponse)
async def get_install_status() -> InstallStatusResponse:
    """Poll current installation status — includes all accumulated log lines.

    Call this when reconnecting after a tab switch to restore the full log.
    """
    # Prefer a live run over the marker — during an in-place UPGRADE the
    # marker briefly disappears/reappears; the running progress is the truth.
    active = get_active_progress()
    if is_image_gen_installed() and not (active and active.status == "running"):
        return _make_status(
            status="complete", stage="done", percent=100.0,
            message="Image generation packages are installed.",
            already_installed=True,
        )

    progress = get_active_progress()
    if progress is None:
        return _make_status(
            status="idle", stage="", percent=0.0,
            message="No installation in progress.",
        )

    return _make_status(
        status=progress.status, stage=progress.stage,
        percent=progress.percent, message=progress.message,
        error=progress.error, progress=progress,
    )


@router.get("/install/stream")
async def stream_install_progress() -> StreamingResponse:
    """SSE stream of installation progress events.

    Connect before or after calling POST /install.  Terminates once the
    install completes or errors, or after 30 minutes (safety timeout for
    large downloads on slow connections).
    """
    async def event_stream():
        loop = asyncio.get_running_loop()

        yield f"data: {json.dumps({'status': 'connected', 'percent': 0})}\n\n"

        _active = get_active_progress()
        if is_image_gen_installed() and not (_active and _active.status == "running"):
            yield f"data: {json.dumps({'status': 'complete', 'percent': 100, 'message': 'Already installed'})}\n\n"
            return

        # Wait up to 15 s for the caller to kick off POST /install
        deadline = loop.time() + 15.0
        while get_active_progress() is None:
            if loop.time() > deadline:
                yield f"data: {json.dumps({'status': 'error', 'message': 'No install started. Call POST /image-gen/install first.'})}\n\n"
                return
            await asyncio.sleep(0.3)

        progress = get_active_progress()
        assert progress is not None

        # Keep-alive between events: pip's silent dependency-resolution phase
        # can outlast proxy/browser idle timeouts. (The old "heartbeat task"
        # here was declared but never started — dead code.) SSE comment lines
        # (": ...") are ignored by EventSource parsers, so they're safe to
        # interleave with real events.
        # Pump events through a local queue so the keepalive timeout never
        # cancels the generator's __anext__ (which would corrupt it).
        queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        async def _pump() -> None:
            try:
                async for ev in progress.events():
                    await queue.put(ev)
                    if ev.get("status") in ("complete", "error"):
                        break
            except Exception as pump_exc:
                await queue.put({"status": "error", "message": str(pump_exc)})
            finally:
                await queue.put(_SENTINEL)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is _SENTINEL:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            pump_task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
