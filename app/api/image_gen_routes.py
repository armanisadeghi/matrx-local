"""Image generation API routes.

All imports of the optional diffusers/torch packages are behind the service
boundary — this module is safe to import even when those packages are not
installed. Endpoints return HTTP 503 with a clear installation message when
the optional dependencies are absent.

/image-gen/install       — POST: start background install of torch + diffusers
/image-gen/install/status — GET: current install state (polling)
/image-gen/install/stream — GET: SSE progress stream during install

Job-event vocabulary (polled via GET /image-gen/jobs/{job_id}; the only push
stream is /install/stream, whose SSE data lines carry the install progress
dict {phase, percent, message}):
  status       — "queued" → "running" → terminal "completed" | "failed" |
                 "cancelled"
  progress     — 0.0–1.0 denoising progress while running
  cancel_requested — flips true immediately on cancel; the cancel lands at
                 the next denoising step (status → "cancelled")
  error        — human-readable failure text on "failed"

First-class request fields on POST /image-gen/generate AND /image-gen/jobs
(beyond prompt/steps/guidance/width/height/seed/extra_params):
  init_image_b64 — base64 PNG/JPEG (optional data: URL prefix accepted).
                 Switches the run to IMAGE-TO-IMAGE for models whose catalog
                 entry has supports_img2img=true (GET /image-gen/models). The
                 image is aspect-fill resized + center-cropped to the
                 requested width/height. Garbage/undecodable data → 400.
  strength     — 0..1 img2img denoising strength (default 0.6 when an init
                 image is present). Requires init_image_b64 (400 otherwise);
                 rejected for families without a strength knob (flux2-klein).
  loras        — [{id, scale}] of INSTALLED LoRA ids (GET /image-gen/loras).
                 Applied for this generation only and always unloaded after
                 (stateless). Unknown id / not-yet-downloaded / base-family
                 mismatch (e.g. an sdxl LoRA on a flux model) → 400 naming
                 the LoRA.

LoRA management:
  GET    /image-gen/loras            — installed LoRAs + curated catalog
  POST   /image-gen/loras/download   — {repo_id, weight_name?} → routed
                                       through the universal DownloadManager
                                       (category "image_gen_lora"; progress on
                                       /downloads/stream) — NEVER an inline
                                       silent download
  DELETE /image-gen/loras/{lora_id}  — remove an installed LoRA

Error responses additionally carry the aidream envelope {error, message,
details} via EnvelopeRoute — ADDITIVE: every legacy key ("detail",
"needs_download", ...) is preserved unchanged.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

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

from app.api.error_envelope import EnvelopeRoute

router = APIRouter(prefix="/image-gen", tags=["image-gen"], route_class=EnvelopeRoute)


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
    supports_img2img: bool
    """True when init_image_b64 is accepted for this model (an img2img
    pipeline exists for the family in diffusers)."""
    img2img_strength: bool
    """True when the family's img2img accepts the strength knob. False only
    for flux2-klein (reference-image editing, no strength parameter)."""
    lora_family: str
    """LoRA-compatibility family ("sdxl" | "sd15" | "flux" | "flux2" | "qwen"
    | "z-image") — match against a LoRA's base_family before requesting it."""
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
    load_error: str | None = None
    """Why the last model load failed (None when it succeeded / never ran).
    is_loading always returns to false after a failed load — this field
    carries the reason so a reconnecting UI can show it."""
    is_generating: bool = False
    """True while any generation (one-shot or job) is in flight — includes
    the auto-load phase. Lets a reconnecting UI see something is running."""
    cancel_requested: bool = False
    """True once a cancel has been requested for the in-flight generation
    but before it lands (show "Cancelling…")."""
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


class LoraSpec(BaseModel):
    id: str
    """Installed LoRA id (the sanitized repo id, e.g.
    'latent-consistency--lcm-lora-sdxl') — see GET /image-gen/loras."""
    scale: float = Field(1.0, ge=0.0, le=2.0)
    """Adapter weight (typical 0.5–1.0; 1.0 = full effect)."""


class GenerateRequest(BaseModel):
    # 10k chars is an API sanity bound, NOT a model limit. Real limits are per
    # model family and enforced by the model itself (SD/SDXL CLIP truncates at
    # 77 tokens; FLUX/T5 reads ~512 tokens ≈ 2000+ chars; Qwen/Z-Image accept
    # long prompts). The old 1000-char cap was an arbitrary API restriction
    # that blocked legitimate FLUX/Qwen prompts — never reintroduce it.
    prompt: str = Field(..., min_length=1, max_length=10000)
    model_id: str
    negative_prompt: str = ""
    steps: int | None = Field(None, ge=1, le=150)
    guidance: float | None = Field(None, ge=0.0, le=20.0)
    width: int | None = Field(None, ge=64, le=2048, multiple_of=8)
    height: int | None = Field(None, ge=64, le=2048, multiple_of=8)
    seed: int | None = None
    init_image_b64: str | None = None
    """Base64 PNG/JPEG (a ``data:image/...;base64,`` prefix is accepted and
    stripped). Switches to IMAGE-TO-IMAGE — only for models with
    supports_img2img=true. Aspect-fill resized + center-cropped to the
    requested width/height. Undecodable data → 400."""
    strength: float | None = Field(None, ge=0.0, le=1.0)
    """img2img denoising strength (0=keep the input, 1=ignore it). Default
    0.6 when init_image_b64 is present. Requires init_image_b64 — set without
    an input image → 400."""
    loras: list[LoraSpec] = Field(default_factory=list)
    """INSTALLED LoRA ids (+ per-LoRA scale) to apply for THIS generation
    only — always unloaded afterwards. Unknown id or base-family mismatch
    → 400 naming the LoRA. GET /image-gen/loras for installed ids."""
    extra_params: dict[str, Any] = Field(default_factory=dict)
    """Arbitrary diffusers pipeline kwargs, merged LAST over the computed call
    kwargs — your values override every default. ``prompt`` can never be
    overridden here (use the top-level field); an unknown kwarg fails loudly
    with success=false naming the parameter. See GET /image-gen/params/{model_id}
    for the full effective defaults per model."""


class EnqueueImageJobRequest(GenerateRequest):
    priority: Literal["normal", "next"] = "normal"
    """"next" inserts the job at the FRONT of the pending queue — it runs
    immediately after the currently-running generation, before every other
    queued job. Used by the UI's queue-first Generate flow (clicking Generate
    while a generation is running queues as up-next instead of firing a
    concurrent one-shot). "normal" (default) appends FIFO as before."""


class GenerateResponse(BaseModel):
    success: bool
    image_b64: str | None = None
    """Base64-encoded PNG. Embed as: data:image/png;base64,{image_b64}"""
    width: int = 0
    height: int = 0
    model_id: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None
    item_id: str | None = None
    """Media-library id — fetch the file via GET /media-library/file/{item_id}."""
    file_path: str | None = None
    """Absolute path of the persisted PNG on this machine."""
    seed: int | None = None
    """The CONCRETE seed used — server-generated when the request omitted it,
    so every image is reproducible."""
    cancelled: bool = False
    """True when this generation was aborted via POST /image-gen/cancel —
    always paired with success=false and error="Cancelled"."""


class CancelGenerationResponse(BaseModel):
    cancelled: bool
    """True when a running generation was told to stop; false when nothing
    was running (see reason)."""
    was: str | None = None
    """"oneshot" | "job" — what kind of generation the cancel hit."""
    job_id: str | None = None
    """Set when the cancelled generation belongs to a queue job."""
    reason: str | None = None
    """"nothing_running" when cancelled=false."""
    mid_flight: bool = True
    """True when the running pipeline supports per-step abort (every catalog
    model does) — the generation stops at the next denoising step. False only
    for non-catalog pipelines with no step callback: the cancel is recorded
    and the result is discarded when the pipeline call finishes."""


class ModelParamsResponse(BaseModel):
    common: dict[str, Any]
    """Effective defaults for the first-class request fields (steps, guidance,
    width, height, negative_prompt, seed)."""
    advanced: dict[str, Any]
    """Every other kwarg the service passes to the pipeline for this model
    (with its effective default) plus verified-safe diffusers knobs — all
    overridable via extra_params."""
    supports_negative_prompt: bool


class ImageJobEnqueuedResponse(BaseModel):
    job_id: str


class ImageJobResponse(BaseModel):
    job_id: str
    status: str
    """queued | running | completed | failed | cancelled."""
    prompt: str
    model_id: str
    negative_prompt: str = ""
    steps: int | None = None
    guidance: float | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    """Requested seed while queued; the CONCRETE seed used once running."""
    has_init_image: bool = False
    """True for image-to-image jobs (the init image itself is never echoed
    back — init_image_sha256 identifies it)."""
    init_image_sha256: str | None = None
    strength: float | None = None
    loras: list[dict[str, Any]] = []
    """The requested LoRAs: [{"id", "scale"}]."""
    extra_params: dict[str, Any] = {}
    progress: float = 0.0
    """0..1 per denoising step."""
    current_step: int = 0
    total_steps: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None
    item_id: str | None = None
    """Media-library id once completed — GET /media-library/file/{item_id}."""
    file_path: str | None = None
    cancel_requested: bool = False
    """True the instant a cancel lands on this running job; the abort takes
    effect at the next denoising step (status then becomes "cancelled" with
    partial progress preserved). Poll this to show "Cancelling…"."""
    priority: str = "normal"
    """"next" when the job was enqueued at the front of the pending queue
    (queue-first Generate). The UI labels such queued jobs "Up next"."""
    created_at: float = 0.0


def _image_job_response(job) -> ImageJobResponse:
    d = job.to_dict()
    return ImageJobResponse(
        job_id=d["job_id"],
        status=d["status"],
        prompt=d["prompt"],
        model_id=d["model_id"],
        negative_prompt=d["negative_prompt"],
        steps=d["steps"],
        guidance=d["guidance"],
        width=d["width"],
        height=d["height"],
        seed=d["seed"],
        has_init_image=d.get("has_init_image", False),
        init_image_sha256=d.get("init_image_sha256"),
        strength=d.get("strength"),
        loras=d.get("loras", []),
        extra_params=d["extra_params"],
        progress=d["progress"],
        current_step=d["current_step"],
        total_steps=d["total_steps"],
        elapsed_seconds=d["elapsed_seconds"],
        error=d["error"],
        item_id=d["item_id"],
        file_path=d["file_path"],
        cancel_requested=d["cancel_requested"],
        priority=d.get("priority", "normal"),
        created_at=d["created_at"],
    )


class WorkflowGenerateRequest(BaseModel):
    preset_id: str
    subject: str = Field(..., min_length=1, max_length=2000, description="Fills the {subject} placeholder in the prompt template.")
    model_id: str | None = None
    """Override model. Defaults to the preset's suggested model."""
    seed: int | None = None


# ── img2img / LoRA request validation ─────────────────────────────────────────

_MAX_INIT_IMAGE_BYTES = 32 * 1024 * 1024  # 32 MB decoded — an API sanity bound


def _validate_generation_inputs(
    req: GenerateRequest, model
) -> tuple[bytes | None, str | None]:
    """Validate init_image_b64 / strength / loras for a request against a
    catalog model. Returns (decoded_init_bytes, init_image_sha256). Every
    failure is a clear 400 — nothing malformed ever reaches the queue or a
    loaded pipeline."""
    if req.strength is not None and not req.init_image_b64:
        raise HTTPException(
            status_code=400,
            detail="strength only applies to image-to-image — provide "
                   "init_image_b64 (the input image) or omit strength.",
        )

    init_bytes: bytes | None = None
    init_sha: str | None = None
    if req.init_image_b64:
        if not model.supports_img2img:
            raise HTTPException(
                status_code=400,
                detail=f"{model.name} does not support image-to-image — pick "
                       "a model with supports_img2img=true "
                       "(GET /image-gen/models).",
            )
        if req.strength is not None and not model.img2img_strength:
            raise HTTPException(
                status_code=400,
                detail=f"{model.name} performs reference-image editing with "
                       "no strength control — omit strength for this model.",
            )
        b64 = req.init_image_b64
        if b64.startswith("data:"):
            _, _, b64 = b64.partition(",")
        import base64 as _base64  # noqa: PLC0415
        try:
            raw = _base64.b64decode(b64, validate=True)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="init_image_b64 is not valid base64 data — send a "
                       "base64-encoded PNG or JPEG image.",
            )
        if len(raw) > _MAX_INIT_IMAGE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"init image is too large ({len(raw)} bytes decoded; "
                       f"max {_MAX_INIT_IMAGE_BYTES}).",
            )
        import io as _io  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        try:
            img = Image.open(_io.BytesIO(raw))
            img.verify()
            fmt = (img.format or "").upper()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="init_image_b64 does not decode to a readable image — "
                       "send a base64-encoded PNG or JPEG.",
            )
        if fmt not in ("PNG", "JPEG"):
            raise HTTPException(
                status_code=400,
                detail=f"init image format {fmt or 'unknown'} is not "
                       "supported — send PNG or JPEG.",
            )
        import hashlib  # noqa: PLC0415
        init_sha = hashlib.sha256(raw).hexdigest()
        init_bytes = raw

    if req.loras:
        from app.services.image_gen.loras import (  # noqa: PLC0415
            check_lora_model_compat,
            get_installed_lora,
        )
        for spec in req.loras:
            meta = get_installed_lora(spec.id)
            if meta is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown LoRA id '{spec.id}' — GET /image-gen/loras "
                           "lists installed LoRAs; download one via "
                           "POST /image-gen/loras/download.",
                )
            if not meta["installed"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"LoRA '{spec.id}' is not fully downloaded yet — "
                           "wait for its download to complete "
                           "(/downloads/stream, category image_gen_lora).",
                )
            try:
                check_lora_model_compat(model.lora_family, meta)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

    return init_bytes, init_sha


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
            supports_img2img=m.supports_img2img,
            img2img_strength=m.img2img_strength,
            lora_family=m.lora_family,
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
    # Unknown models keep the legacy contract (success=false from the
    # service); img2img/LoRA inputs are validated whenever the model is known.
    model = svc.get_model(req.model_id) if hasattr(svc, "get_model") else None
    init_bytes: bytes | None = None
    if model is not None:
        init_bytes, _init_sha = _validate_generation_inputs(req, model)

    result = await svc.generate(
        prompt=req.prompt,
        model_id=req.model_id,
        negative_prompt=req.negative_prompt,
        steps=req.steps,
        guidance=req.guidance,
        width=req.width,
        height=req.height,
        seed=req.seed,
        extra_params=req.extra_params,
        init_image_bytes=init_bytes,
        strength=req.strength,
        loras=[{"id": s.id, "scale": s.scale} for s in req.loras],
    )
    return GenerateResponse(
        success=result.success,
        image_b64=result.image_b64,
        width=result.width,
        height=result.height,
        model_id=result.model_id,
        elapsed_seconds=result.elapsed_seconds,
        error=result.error,
        item_id=result.item_id,
        file_path=result.file_path,
        seed=result.seed,
        cancelled=result.cancelled,
    )


@router.post("/cancel", response_model=CancelGenerationResponse)
@safe_route("image_gen_cancel")
async def cancel_generation() -> CancelGenerationResponse:
    """Cancel the currently running image generation (one-shot or job).

    The abort lands at the next denoising step: the in-flight POST
    /image-gen/generate returns promptly with success=false,
    error="Cancelled", cancelled=true; a running job's status becomes
    "cancelled" (partial progress preserved). Queued jobs are NOT touched —
    cancel those individually via DELETE /image-gen/jobs/{id}.
    Returns {cancelled: false, reason: "nothing_running"} when idle.
    """
    svc = get_image_gen_service()
    result = svc.request_cancel()
    if result.get("cancelled") and result.get("job_id"):
        from app.services.image_gen.jobs import get_image_job_store  # noqa: PLC0415
        get_image_job_store().request_cancel_running(result["job_id"])
    return CancelGenerationResponse(**result)


# ── Job queue (async generation) ─────────────────────────────────────────────

@router.post("/jobs", response_model=ImageJobEnqueuedResponse, status_code=202)
@safe_route("image_gen_enqueue_job")
async def enqueue_image_job(req: EnqueueImageJobRequest) -> ImageJobEnqueuedResponse:
    """Enqueue a generation job → 202 {job_id} immediately.

    Unlike video, image jobs QUEUE: keep submitting prompts and they run
    strictly one at a time in submission order. ``priority: "next"`` inserts
    the job at the front of the pending queue (right after the running one,
    before all other queued jobs). Poll GET /image-gen/jobs/{id}
    for per-step progress; on completion the job carries the media-library
    ``item_id`` (fetch the PNG via GET /media-library/file/{item_id}) and the
    concrete ``seed`` used. The model must be downloaded (409 otherwise) —
    loading happens automatically when the job runs.
    """
    svc = get_image_gen_service()
    if not svc.available:
        raise HTTPException(
            status_code=503,
            detail=f"Image generation not available: {svc.unavailable_reason}",
        )
    model = svc.get_model(req.model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Unknown model: {req.model_id}")
    # Malformed img2img/LoRA input is a 400 BEFORE the downloaded check —
    # a request that can only fail must never enqueue.
    init_bytes, init_sha = _validate_generation_inputs(req, model)
    if not svc.is_downloaded(req.model_id):
        # Never queue a job that can only fail — the weights must exist first.
        return JSONResponse(  # type: ignore[return-value]
            status_code=409,
            content={"detail": "model not downloaded", "needs_download": True},
        )
    from app.services.media_gen.params import PROTECTED_PARAMS  # noqa: PLC0415
    blocked = PROTECTED_PARAMS & set(req.extra_params)
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=f"extra_params may not override protected parameter(s): "
                   f"{', '.join(sorted(blocked))}. prompt has a top-level "
                   "request field; pipeline callbacks are engine-owned "
                   "(progress + cancellation).",
        )

    from app.services.image_gen.jobs import (  # noqa: PLC0415
        get_image_job_runner,
        get_image_job_store,
    )
    store = get_image_job_store()
    job = store.create(
        prompt=req.prompt,
        model_id=req.model_id,
        negative_prompt=req.negative_prompt,
        steps=req.steps,
        guidance=req.guidance,
        width=req.width,
        height=req.height,
        seed=req.seed,
        has_init_image=init_bytes is not None,
        init_image_sha256=init_sha,
        strength=req.strength,
        loras=[{"id": s.id, "scale": s.scale} for s in req.loras],
        extra_params=dict(req.extra_params),
        priority=req.priority,
    )
    if init_bytes is not None:
        # Bytes live in memory only (jobs.json carries just the sha256).
        store.stash_init_image(job.job_id, init_bytes)
    get_image_job_runner().ensure_running()
    return ImageJobEnqueuedResponse(job_id=job.job_id)


@router.get("/jobs", response_model=list[ImageJobResponse])
async def list_image_jobs(limit: int = 50) -> list[ImageJobResponse]:
    """Recent jobs, newest first (history survives restarts via jobs.json)."""
    from app.services.image_gen.jobs import get_image_job_store  # noqa: PLC0415
    return [_image_job_response(j) for j in get_image_job_store().recent(limit=limit)]


@router.get("/jobs/{job_id}", response_model=ImageJobResponse)
async def get_image_job(job_id: str) -> ImageJobResponse:
    """Job status + per-step progress (poll every ~1-2s while running)."""
    from app.services.image_gen.jobs import get_image_job_store  # noqa: PLC0415
    job = get_image_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _image_job_response(job)


@router.delete("/jobs/{job_id}")
async def cancel_image_job(job_id: str) -> dict:
    """Cancel a job or remove a finished record. Outcomes:

    - queued  → {"outcome": "cancelled"} (record kept, status=cancelled)
    - running → {"outcome": "cancelling", "mid_flight": bool} — the job's
      cancel_requested flips true immediately (show "Cancelling…"); the abort
      lands at the next denoising step and the job becomes status=cancelled
      with partial progress preserved
    - finished → {"outcome": "removed"} (record deleted; the media-library
      item, if any, is NOT deleted)
    - unknown  → 404
    """
    from app.services.image_gen.jobs import get_image_job_store  # noqa: PLC0415
    store = get_image_job_store()
    outcome = store.cancel(job_id)
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="Job not found")
    if outcome == "running":
        store.request_cancel_running(job_id)
        info = get_image_gen_service().request_cancel_job(job_id)
        return {
            "job_id": job_id,
            "outcome": "cancelling",
            "mid_flight": bool(info.get("mid_flight", False)),
        }
    return {"job_id": job_id, "outcome": outcome}


# ── LoRAs ─────────────────────────────────────────────────────────────────────

class LoraInstalledInfo(BaseModel):
    id: str
    """Store id (sanitized repo id) — pass this in GenerateRequest.loras."""
    repo_id: str
    weight_name: str
    base_family: str = "unknown"
    """"sdxl" | "sd15" | "flux" | "unknown" — match against the model's
    lora_family (GET /image-gen/models)."""
    size_bytes: int = 0
    added_at: str | None = None
    installed: bool = True
    """False while the download is still in flight (progress on
    /downloads/stream, category "image_gen_lora")."""


class LoraCatalogInfo(BaseModel):
    repo_id: str
    name: str
    description: str
    weight_name: str
    base_family: str
    license: str
    unverified: bool = False
    """True for catalog entries whose repo/file could not be verified against
    live HF metadata. All current entries were verified 2026-07-10."""
    installed: bool = False


class LorasResponse(BaseModel):
    installed: list[LoraInstalledInfo]
    catalog: list[LoraCatalogInfo]


class LoraDownloadRequest(BaseModel):
    repo_id: str = Field(..., min_length=3, max_length=200)
    """HF repo id (org/name)."""
    weight_name: str | None = None
    """Which .safetensors file to fetch. Optional when the repo contains
    exactly one; required (400 lists the candidates) when it has several."""


class LoraDownloadResponse(BaseModel):
    queued: bool
    download_id: str | None = None
    lora_id: str | None = None
    weight_name: str | None = None
    base_family: str | None = None
    already_installed: bool = False


async def _resolve_lora_weight(
    repo_id: str, weight_name: str | None
) -> tuple[str, str]:
    """Resolve (weight_name, base_family) from live HF repo metadata.

    Module-level on purpose (tests stub it). Raises HTTPException with a
    clear message on every failure path."""
    try:
        from huggingface_hub import HfApi  # noqa: PLC0415
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="huggingface_hub is not installed — run the AI package "
                   "installer first (POST /image-gen/install).",
        )
    from app.services.image_gen.loras import guess_base_family  # noqa: PLC0415
    from app.services.media_gen.paths import read_hf_token  # noqa: PLC0415

    api = HfApi(token=read_hf_token())
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, lambda: api.model_info(repo_id))
    except Exception as exc:  # noqa: BLE001 — network/404/auth all land here
        raise HTTPException(
            status_code=404,
            detail=f"Hugging Face repo '{repo_id}' could not be read: {exc}",
        )
    candidates = [
        s.rfilename for s in (info.siblings or [])
        if s.rfilename.endswith(".safetensors")
    ]
    if weight_name:
        if weight_name not in candidates:
            raise HTTPException(
                status_code=400,
                detail=f"'{weight_name}' is not a .safetensors file in "
                       f"{repo_id}. Available: {', '.join(candidates) or 'none'}",
            )
        chosen = weight_name
    else:
        if not candidates:
            raise HTTPException(
                status_code=400,
                detail=f"{repo_id} contains no .safetensors file — it does "
                       "not look like a LoRA repo.",
            )
        if len(candidates) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"{repo_id} contains multiple .safetensors files — "
                       f"pass weight_name. Candidates: {', '.join(candidates)}",
            )
        chosen = candidates[0]

    base_model = None
    card = getattr(info, "card_data", None) or getattr(info, "cardData", None)
    if card is not None:
        raw_base = card.get("base_model") if hasattr(card, "get") else None
        if isinstance(raw_base, str):
            base_model = raw_base
        elif isinstance(raw_base, list) and raw_base and isinstance(raw_base[0], str):
            base_model = raw_base[0]
    return chosen, guess_base_family(repo_id, chosen, base_model)


@router.get("/loras", response_model=LorasResponse)
async def list_image_loras() -> LorasResponse:
    """Installed LoRAs (pass their ``id`` in GenerateRequest.loras) plus the
    curated catalog (verified well-known LoRAs, one click from
    POST /image-gen/loras/download)."""
    from app.services.image_gen.loras import (  # noqa: PLC0415
        CURATED_LORA_CATALOG,
        list_loras,
        lora_id_for_repo,
    )
    items = list_loras()
    installed_repo_ids = {m["repo_id"] for m in items if m["installed"]}
    return LorasResponse(
        installed=[
            LoraInstalledInfo(
                id=m["id"],
                repo_id=m["repo_id"],
                weight_name=m["weight_name"],
                base_family=str(m.get("base_family") or "unknown"),
                size_bytes=int(m.get("size_bytes") or 0),
                added_at=m.get("added_at"),
                installed=bool(m["installed"]),
            )
            for m in items
        ],
        catalog=[
            LoraCatalogInfo(
                repo_id=e["repo_id"],
                name=e["name"],
                description=e["description"],
                weight_name=e["weight_name"],
                base_family=e["base_family"],
                license=e["license"],
                unverified=bool(e.get("unverified", False)),
                installed=e["repo_id"] in installed_repo_ids
                or lora_id_for_repo(e["repo_id"]) in {m["id"] for m in items if m["installed"]},
            )
            for e in CURATED_LORA_CATALOG
        ],
    )


@router.post("/loras/download", response_model=LoraDownloadResponse)
@safe_route("image_gen_lora_download")
async def download_lora(req: LoraDownloadRequest) -> LoraDownloadResponse:
    """Queue a LoRA download through the universal DownloadManager (category
    "image_gen_lora" — byte progress/resume on /downloads/stream, exactly like
    model weights; NEVER an inline silent download). Only the safetensors
    weight file is fetched. Idempotent."""
    from app.services.downloads.manager import get_download_manager  # noqa: PLC0415
    from app.services.image_gen.loras import (  # noqa: PLC0415
        get_installed_lora,
        lora_dir,
        lora_id_for_repo,
        write_lora_meta,
    )
    lora_id = lora_id_for_repo(req.repo_id)
    existing = get_installed_lora(lora_id)
    if existing and existing["installed"]:
        return LoraDownloadResponse(
            queued=False, already_installed=True, lora_id=lora_id,
            weight_name=existing["weight_name"],
            base_family=str(existing.get("base_family") or "unknown"),
        )

    weight_name, base_family = await _resolve_lora_weight(
        req.repo_id, req.weight_name
    )
    # Metadata sidecar first — the LoRA shows up as pending (installed=false)
    # immediately; the DownloadManager marker flips it to installed.
    write_lora_meta(
        lora_id, repo_id=req.repo_id, weight_name=weight_name,
        base_family=base_family,
    )
    entry = await get_download_manager().enqueue(
        category="image_gen_lora",
        filename=lora_id,
        display_name=f"LoRA: {req.repo_id}",
        urls=[f"hf://{req.repo_id}"],  # marker only — the HF path ignores URLs
        metadata={
            "dest_dir": str(lora_dir(lora_id)),
            "hf_repo_id": req.repo_id,
            # Exactly the weight file — bypasses the diffusers-layout filter
            # (which would drop a root-level .safetensors as a "dup").
            "hf_allow_files": [weight_name],
            "lora_id": lora_id,
        },
        priority=1,
    )
    return LoraDownloadResponse(
        queued=True, download_id=entry.id, lora_id=lora_id,
        weight_name=weight_name, base_family=base_family,
    )


@router.delete("/loras/{lora_id}")
async def delete_image_lora(lora_id: str) -> dict:
    """Delete an installed LoRA (weights + metadata). 404 when unknown."""
    from app.services.image_gen.loras import delete_lora  # noqa: PLC0415
    if not delete_lora(lora_id):
        raise HTTPException(status_code=404, detail=f"Unknown LoRA: {lora_id}")
    return {"deleted": True, "lora_id": lora_id}


@router.get("/params/{model_id:path}", response_model=ModelParamsResponse)
async def get_image_model_params(model_id: str) -> ModelParamsResponse:
    """COMPLETE effective default parameters for a model — everything the
    service would pass to the pipeline, so the UI can expose every setting.

    ``model_id`` contains a slash (HF repo id) — the path converter accepts it
    URL-encoded or raw (/image-gen/params/stabilityai/sdxl-turbo).
    Anything in ``advanced`` can be overridden via ``extra_params`` on
    POST /image-gen/generate.
    """
    from app.services.media_gen.params import image_effective_params  # noqa: PLC0415
    model = get_image_gen_service().get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_id}")
    return ModelParamsResponse(**image_effective_params(model))


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
        item_id=result.item_id,
        file_path=result.file_path,
        seed=result.seed,
        cancelled=result.cancelled,
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
