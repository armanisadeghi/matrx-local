"""Video generation API routes (prefix /video-gen).

Mirrors /image-gen semantics; auth comes from the same Bearer-token
AuthMiddleware that gates every non-public route. All torch/diffusers imports
stay behind the service boundary — this module imports cleanly with no
optional packages installed.

Contract (media-gen spec, July 2026 — the frontend is built against this):

  GET  /video-gen/status                 — availability + hardware + job state
  GET  /video-gen/models                 — catalog with download/hardware state
  POST /video-gen/download {model_id}    — queue weights via DownloadManager
  POST /video-gen/load {model_id}        — load from local dir (409 if absent)
  POST /video-gen/unload
  POST /video-gen/generate {...}         — 202 {job_id}; ONE job at a time (409)
  GET  /video-gen/jobs                   — recent jobs
  GET  /video-gen/jobs/{job_id}          — job status/progress
  GET  /video-gen/jobs/{job_id}/result   — the mp4 (FileResponse)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.common.route_errors import safe_route
from app.services.video_gen.jobs import get_video_job_store
from app.services.video_gen.models import VIDEO_GEN_MODELS
from app.services.video_gen.service import get_video_gen_service

router = APIRouter(prefix="/video-gen", tags=["video-gen"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class VideoGenStatusResponse(BaseModel):
    available: bool
    unavailable_reason: str | None
    packages_installed: bool
    hardware_supported: bool
    hardware_reason: str | None
    loaded_model_id: str | None
    is_loading: bool
    load_progress: float
    device: str
    active_job_id: str | None


class VideoGenModelInfo(BaseModel):
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
    default_width: int
    default_height: int
    default_num_frames: int
    default_fps: int
    max_num_frames: int
    supports_image_to_video: bool
    supports_negative_prompt: bool
    model_card_url: str
    license_name: str
    requires_hf_token: bool
    tags: list[str]
    download_size_gb: float
    is_downloaded: bool
    hardware_ok: bool
    hardware_reason: str | None = None


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


class GenerateVideoRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    negative_prompt: str = ""
    model_id: str | None = None
    """Defaults to the currently loaded model."""
    width: int | None = Field(None, ge=64, le=1920, multiple_of=16)
    height: int | None = Field(None, ge=64, le=1920, multiple_of=16)
    num_frames: int | None = Field(None, ge=9, le=257)
    fps: int | None = Field(None, ge=4, le=60)
    steps: int | None = Field(None, ge=1, le=100)
    guidance: float | None = Field(None, ge=0.0, le=20.0)
    seed: int | None = None
    image_base64: str | None = None
    """Optional source image (base64, no data: prefix) for image-to-video."""


class GenerateVideoResponse(BaseModel):
    job_id: str


class VideoJobResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    current_step: int
    total_steps: int
    elapsed_seconds: float
    error: str | None
    prompt: str
    model_id: str
    width: int = 0
    height: int = 0
    num_frames: int = 0
    fps: int = 0
    seed: int | None = None
    created_at: float = 0.0


def _job_response(job) -> VideoJobResponse:
    d = job.to_dict()
    return VideoJobResponse(
        job_id=d["job_id"],
        status=d["status"],
        progress=d["progress"],
        current_step=d["current_step"],
        total_steps=d["total_steps"],
        elapsed_seconds=d["elapsed_seconds"],
        error=d["error"],
        prompt=d["prompt"],
        model_id=d["model_id"],
        width=d["width"],
        height=d["height"],
        num_frames=d["num_frames"],
        fps=d["fps"],
        seed=d["seed"],
        created_at=d["created_at"],
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status", response_model=VideoGenStatusResponse)
async def video_gen_status() -> VideoGenStatusResponse:
    """Availability (packages AND hardware), loaded model, active job."""
    return VideoGenStatusResponse(**get_video_gen_service().get_status())


@router.get("/models", response_model=list[VideoGenModelInfo])
async def list_video_gen_models() -> list[VideoGenModelInfo]:
    """List all video models with download + per-model hardware state."""
    svc = get_video_gen_service()
    out: list[VideoGenModelInfo] = []
    for m in VIDEO_GEN_MODELS:
        hw_ok, hw_reason = svc.model_hardware_check(m)
        out.append(VideoGenModelInfo(
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
            default_width=m.default_width,
            default_height=m.default_height,
            default_num_frames=m.default_num_frames,
            default_fps=m.default_fps,
            max_num_frames=m.max_num_frames,
            supports_image_to_video=m.supports_image_to_video,
            supports_negative_prompt=m.supports_negative_prompt,
            model_card_url=m.model_card_url,
            license_name=m.license_name,
            requires_hf_token=m.requires_hf_token,
            tags=list(m.tags),
            download_size_gb=m.download_size_gb,
            is_downloaded=svc.is_downloaded(m.model_id),
            hardware_ok=hw_ok,
            hardware_reason=hw_reason,
        ))
    return out


@router.post("/download", response_model=DownloadModelResponse)
@safe_route("video_gen_download")
async def download_video_model(req: DownloadModelRequest) -> DownloadModelResponse:
    """Queue the model weights into the universal DownloadManager.

    Progress streams over /downloads/stream (category "video_gen").
    Weights land in ~/.matrx/video-models/<id>/ and are resumable.
    """
    result = await get_video_gen_service().start_download(req.model_id)
    if result.get("needs_hf_token"):
        raise HTTPException(status_code=400, detail=result["error"])
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return DownloadModelResponse(
        queued=bool(result.get("queued")),
        download_id=result.get("download_id"),
        already_downloaded=bool(result.get("already_downloaded")),
    )


@router.post("/load", response_model=LoadModelResponse)
@safe_route("video_gen_load")
async def load_video_model(req: LoadModelRequest) -> LoadModelResponse | JSONResponse:
    """Load a downloaded model into memory. NEVER downloads (409 when absent)."""
    svc = get_video_gen_service()
    status = svc.get_status()
    if not status["available"]:
        raise HTTPException(
            status_code=503,
            detail=f"Video generation not available: {status['unavailable_reason']}",
        )
    result = await svc.load_model(req.model_id)
    if result.get("needs_download"):
        return JSONResponse(
            status_code=409,
            content={"detail": "model not downloaded", "needs_download": True},
        )
    if result.get("job_active"):
        return JSONResponse(
            status_code=409,
            content={"detail": "a video generation job is running", "job_active": True},
        )
    if result.get("hardware_blocked"):
        raise HTTPException(status_code=409, detail=result.get("error"))
    result.pop("needs_download", None)
    result.pop("hardware_blocked", None)
    result.pop("job_active", None)
    return LoadModelResponse(**result)


@router.post("/unload", response_model=None)
async def unload_video_model() -> dict | JSONResponse:
    """Unload the current pipeline and free VRAM. 409 while a job is running."""
    result = await get_video_gen_service().unload_model()
    if result.get("job_active"):
        return JSONResponse(
            status_code=409,
            content={"detail": "a video generation job is running", "job_active": True},
        )
    return result


@router.post("/generate", response_model=GenerateVideoResponse, status_code=202)
@safe_route("video_gen_generate")
async def generate_video(req: GenerateVideoRequest) -> GenerateVideoResponse | JSONResponse:
    """Start an async video generation job → 202 {job_id}.

    Poll GET /video-gen/jobs/{job_id} for progress; fetch the mp4 from
    GET /video-gen/jobs/{job_id}/result when completed.
    409 when a job is already running (one at a time) or the model needs
    downloading/loading first. 503 when packages/hardware are unavailable.
    """
    svc = get_video_gen_service()
    status = svc.get_status()
    if not status["available"]:
        raise HTTPException(
            status_code=503,
            detail=f"Video generation not available: {status['unavailable_reason']}",
        )

    model_id = req.model_id or svc.loaded_model_id
    if not model_id:
        raise HTTPException(
            status_code=409,
            detail="No model loaded and no model_id given — load a model first.",
        )
    model = svc.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_id}")

    # The model must already be loaded — generation never triggers a silent
    # multi-GB download or a surprise multi-minute load.
    if svc.loaded_model_id != model_id:
        if not svc.is_downloaded(model_id):
            return JSONResponse(
                status_code=409,
                content={"detail": "model not downloaded", "needs_download": True},
            )
        raise HTTPException(
            status_code=409,
            detail=f"Model {model_id} is not loaded — call POST /video-gen/load first.",
        )

    try:
        job = svc.start_generation(
            prompt=req.prompt,
            model_id=model_id,
            negative_prompt=req.negative_prompt,
            width=req.width,
            height=req.height,
            num_frames=req.num_frames,
            fps=req.fps,
            steps=req.steps,
            guidance=req.guidance,
            seed=req.seed,
            image_base64=req.image_base64,
        )
    except RuntimeError as exc:
        # One-job-at-a-time guard
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return GenerateVideoResponse(job_id=job.job_id)


@router.get("/jobs", response_model=list[VideoJobResponse])
async def list_video_jobs(limit: int = 20) -> list[VideoJobResponse]:
    """Recent jobs, newest first (history survives restarts via jobs.json)."""
    return [_job_response(j) for j in get_video_job_store().recent(limit=limit)]


@router.get("/jobs/{job_id}", response_model=VideoJobResponse)
async def get_video_job(job_id: str) -> VideoJobResponse:
    """Job status + step progress (poll every ~2s while running)."""
    job = get_video_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_response(job)


@router.get("/jobs/{job_id}/result")
async def get_video_job_result(job_id: str) -> FileResponse:
    """The finished mp4 for a completed job."""
    job = get_video_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed" or not job.output_path:
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job.status} — no result available",
        )
    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(
            status_code=410,
            detail="Result file no longer exists on disk",
        )
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=path.name,
    )
