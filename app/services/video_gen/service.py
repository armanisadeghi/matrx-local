"""Video generation service.

Diffusers-based local text-to-video / image-to-video. Uses the SAME on-demand
package install as image generation (~/.matrx/image-gen-packages/ — see
app/services/image_gen/installer.py); there is no separate video installer.
All torch/diffusers imports are lazy so this module imports cleanly when the
optional packages are absent.

Key properties:

- Hard hardware gate: video is available only on Apple Silicon >= 16GB unified
  or CUDA >= 8GB VRAM (app.services.media_gen.hardware). CPU-only: never.
- Model weights come from ~/.matrx/video-models/<id>/ via the universal
  DownloadManager (category "video_gen"); load_model uses
  local_files_only=True and refuses with needs_download when absent.
- Generation runs in a dedicated worker thread. Diffusers'
  callback_on_step_end updates the job record every step, so the UI can poll
  GET /video-gen/jobs/{id} for a real progress bar.
- ONE job at a time (VideoJobStore enforces it).
- mp4 encoding via imageio-ffmpeg (core dependency — ships in the sidecar).
- MPS runs bf16 — fp8 is not supported on Metal, never attempt it.
"""

from __future__ import annotations

import asyncio
import base64
import io
import random
import threading
import time
from datetime import datetime
from typing import Any

from app.common.system_logger import get_logger
from app.launcher import get_registry
from app.services.media_gen.cancellation import (
    GenerationCancelled,
    friendly_generation_error,
    install_cancel_hook,
    release_generation_memory,
)
from app.services.media_gen.hardware import (
    check_model_hardware,
    get_compute_profile,
)
from app.services.media_gen.paths import (
    generated_videos_dir,
    is_model_downloaded,
    model_dir,
    read_hf_token,
    sanitize_model_id,
    video_models_dir,
)
from app.services.video_gen.jobs import VideoJob, get_video_job_store
from app.services.video_gen.models import (
    VideoGenModel,
    get_video_gen_model,
    get_video_gen_models,
)

logger = get_logger()


def _missing_module_from_chain(exc: BaseException) -> str | None:
    """Walk an exception's cause/context chain and return the name of the first
    missing module if the failure is (or was caused by) an ImportError.

    diffusers/transformers/torchvision lazily import stdlib modules the frozen
    engine's own import graph never references, so PyInstaller omits them and the
    package raises ModuleNotFoundError at load time. We surface the module name
    so the user gets an actionable message instead of a raw multi-line traceback.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ModuleNotFoundError) and cur.name:
            return cur.name
        if isinstance(cur, ImportError):
            return getattr(cur, "name", None) or "a required component"
        cur = cur.__cause__ or cur.__context__
    return None


def friendly_load_error(exc: BaseException) -> str | None:
    """Map an ImportError/ModuleNotFoundError load failure to an actionable,
    single-line message for the UI. Returns None for non-import failures so the
    caller keeps its existing (already reasonable) error text. The full
    traceback is always logged separately — this only shapes the API field.
    """
    module = _missing_module_from_chain(exc)
    if module is None:
        return None
    return (
        f"The AI engine build is missing a component required by this model "
        f"({module}). Update the app to the latest version; if the problem "
        f"persists, use 'Update AI packages' in the Video tab."
    )


def _check_deps() -> tuple[bool, str]:
    """Return (available, reason). Fast — no heavy imports."""
    missing = []
    for pkg in ("torch", "diffusers", "transformers", "accelerate"):
        try:
            __import__(pkg)
        except Exception as exc:  # noqa: BLE001 — a corrupt install (e.g. OSError
            # on a dylib load) must NOT crash `import app.main`; surface it as a
            # normal "unavailable" reason instead.
            # Loud recovery (mirrors image_gen._check_deps): a plain
            # "No module named '<pkg>'" is the benign not-installed case (quiet);
            # a different missing module while importing an installed package —
            # e.g. "No module named 'timeit'" during `import torch` — is a
            # frozen-build gap (PyInstaller omitted a stdlib module the package
            # lazily imports) and gets a loud traceback.
            benign_missing = (
                isinstance(exc, ModuleNotFoundError)
                and exc.name in (pkg, pkg.split(".", 1)[0])
            )
            if pkg == "torch" and benign_missing:
                # The remaining AI packages depend on torch. On a clean
                # first install, importing bundled accelerate/transformers
                # after this point only creates false frozen-gap tracebacks.
                missing.append(pkg)
                break
            if not benign_missing:
                logger.warning(
                    "[video_gen] optional package %r is present but failed to import "
                    "(likely a frozen-build gap — a missing stdlib module it lazily "
                    "imports): %s", pkg, exc, exc_info=True,
                )
            missing.append(f"{pkg} ({exc})" if not isinstance(exc, ImportError) else pkg)
    if missing:
        return False, (
            f"Video generation requires the AI packages: {', '.join(missing)}. "
            "Install them from the app (POST /image-gen/install — video shares "
            "the same install)."
        )
    return True, ""


DEPS_AVAILABLE, DEPS_REASON = _check_deps()


def _round_to_multiple(value: int, multiple: int) -> int:
    """Nearest positive multiple of ``multiple`` (never below ``multiple``)."""
    if value <= 0:
        return multiple
    return max(multiple, round(value / multiple) * multiple)


def _snap_generation_grid(
    model: VideoGenModel, width: int, height: int, num_frames: int
) -> tuple[int, int, int]:
    """Snap requested dims/frames to the model family's valid generation grid.

    LTX requires width/height divisible by 32 and num_frames ≡ 1 (mod 8); Wan
    wants width/height divisible by 16. Passing off-grid values makes diffusers
    either raise or silently pad — snapping (round to nearest valid) keeps the
    request honest and the stored job record accurate.
    """
    if model.pipeline_type == "ltx":
        w = _round_to_multiple(width, 32)
        h = _round_to_multiple(height, 32)
        # nearest k*8 + 1, with a floor of 9 frames.
        nf = max(9, round((num_frames - 1) / 8) * 8 + 1)
    else:  # wan
        w = _round_to_multiple(width, 16)
        h = _round_to_multiple(height, 16)
        nf = num_frames
    return w, h, nf


class VideoGenService:
    """Singleton wrapping one loaded video pipeline + the job runner."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._load_lock = asyncio.Lock()
        self._pipeline: Any = None
        self._i2v_pipeline: Any = None  # derived via from_pipe, shares weights
        self._loaded_model_id: str | None = None
        self._is_loading = False
        self._load_progress: float = 0.0
        self._load_error: str | None = None
        self._load_started_at: float | None = None
        self._device: str | None = None
        self._worker: threading.Thread | None = None
        # (job_id, cancel_event) for the job currently in the worker thread,
        # plus whether its pipeline supports per-step abort (None = unknown,
        # hook not installed yet). Guarded by self._lock.
        self._active_cancel: tuple[str, threading.Event] | None = None
        self._active_supports_step_cancel: bool | None = None

    # ── availability / status ────────────────────────────────────────────────

    @property
    def packages_installed(self) -> bool:
        return DEPS_AVAILABLE

    @property
    def loaded_model_id(self) -> str | None:
        return self._loaded_model_id

    def get_status(self) -> dict:
        compute = get_compute_profile()
        available = DEPS_AVAILABLE and compute.video_supported
        if not compute.video_supported:
            reason: str | None = compute.video_unsupported_reason
        elif not DEPS_AVAILABLE:
            reason = DEPS_REASON
        else:
            reason = None
        store = get_video_job_store()
        active_job_id = store.active_job_id
        active_job = store.get(active_job_id) if active_job_id else None
        return {
            "available": available,
            "unavailable_reason": reason,
            "packages_installed": DEPS_AVAILABLE,
            "hardware_supported": compute.video_supported,
            "hardware_reason": compute.video_unsupported_reason,
            "loaded_model_id": self._loaded_model_id,
            "is_loading": self._is_loading,
            "load_progress": self._load_progress,
            "load_error": self._load_error,
            "load_started_at": self._load_started_at,
            "load_age_seconds": (
                time.time() - self._load_started_at if self._is_loading and self._load_started_at else None
            ),
            "device": self._device or compute.device,
            "active_job_id": active_job_id,
            "cancel_requested": bool(active_job and active_job.cancel_requested),
        }

    def request_cancel_job(self, job_id: str) -> dict:
        """Cancel the running job's generation mid-flight. Returns
        {"found": bool, "mid_flight": bool} — found=False when that job has
        no in-flight generation (already finishing or not yet started)."""
        with self._lock:
            if self._active_cancel is not None and self._active_cancel[0] == job_id:
                self._active_cancel[1].set()
                return {
                    "found": True,
                    "mid_flight": self._active_supports_step_cancel is not False,
                }
        return {"found": False, "mid_flight": False}

    def list_models(self) -> list[VideoGenModel]:
        return get_video_gen_models()

    def get_model(self, model_id: str) -> VideoGenModel | None:
        return get_video_gen_model(model_id)

    def is_downloaded(self, model_id: str) -> bool:
        return is_model_downloaded(video_models_dir(), model_id)

    def model_hardware_check(self, model: VideoGenModel) -> tuple[bool, str | None]:
        compute = get_compute_profile()
        if not compute.video_supported:
            return False, compute.video_unsupported_reason
        return check_model_hardware(
            min_vram_gb=model.vram_gb, min_ram_gb=model.ram_gb, compute=compute
        )

    # ── download ─────────────────────────────────────────────────────────────

    async def start_download(self, model_id: str) -> dict:
        """Queue model weights into the DownloadManager (category video_gen)."""
        model = self.get_model(model_id)
        if model is None:
            return {"queued": False, "error": f"Unknown model: {model_id}"}
        if self.is_downloaded(model_id):
            return {"queued": False, "already_downloaded": True}

        # Gated (private) models need an HF token — pre-check so the user gets a
        # clean 400-style message instead of a deep 401 in the download worker.
        if model.requires_hf_token and read_hf_token() is None:
            return {
                "queued": False,
                "error": (
                    f"{model.name} is a gated model. Add your Hugging Face read "
                    "token under Settings → API Keys → Hugging Face, then start "
                    "the download again."
                ),
                "needs_hf_token": True,
            }

        from app.services.downloads.manager import get_download_manager  # noqa: PLC0415
        dest = model_dir(video_models_dir(), model_id)
        entry = await get_download_manager().enqueue(
            category="video_gen",
            filename=sanitize_model_id(model_id),
            display_name=model.name,
            urls=[f"hf://{model_id}"],  # marker only — the HF path ignores URLs
            metadata={
                "dest_dir": str(dest),
                "hf_repo_id": model_id,
                # Persisted for future matching (download filename IS the
                # sanitized model id, but this makes the link explicit/robust).
                "model_id": model_id,
                # Drives the HF snapshot file filter — only variant weight
                # files are fetched; load_model passes variant= to match.
                "load_variant": model.load_variant,
            },
            priority=1,
        )
        return {"queued": True, "download_id": entry.id}

    # ── load / unload ────────────────────────────────────────────────────────

    def _job_active(self) -> bool:
        """True while a video job is queued/running.

        Loading or unloading a pipeline mid-generation is unsafe: _run_job
        releases self._lock before the multi-minute pipe() call, so a concurrent
        load would put two pipelines in memory (OOM) and a concurrent unload
        would ``del`` the pipeline / empty the CUDA cache under a live
        generation. Both guard on this.
        """
        store = get_video_job_store()
        active_id = store.active_job_id
        if not active_id:
            return False
        job = store.get(active_id)
        return bool(job and job.status in ("queued", "running"))

    async def load_model(self, model_id: str) -> dict:
        status = self.get_status()
        if not status["available"]:
            return {"success": False, "error": status["unavailable_reason"]}

        model = self.get_model(model_id)
        if model is None:
            return {"success": False, "error": f"Unknown model: {model_id}"}

        if self._job_active():
            return {
                "success": False,
                "error": "a video generation job is running",
                "job_active": True,
            }

        if not self.is_downloaded(model_id):
            return {
                "success": False,
                "error": "model not downloaded",
                "needs_download": True,
            }

        hw_ok, hw_reason = self.model_hardware_check(model)
        if not hw_ok:
            return {
                "success": False,
                "error": f"Hardware requirements not met: {hw_reason}",
                "hardware_blocked": True,
            }

        async with self._load_lock:
            if self._loaded_model_id == model_id and self._pipeline is not None:
                return {"success": True, "model_id": model_id, "already_loaded": True}
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._load_model_sync, model)

    async def unload_model(self) -> dict:
        if self._job_active():
            return {
                "success": False,
                "error": "a video generation job is running",
                "job_active": True,
            }
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._unload_sync)
        return {"success": True}

    def _load_model_sync(self, model: VideoGenModel) -> dict:
        registry = get_registry()
        with self._lock:
            if self._loaded_model_id == model.model_id and self._pipeline is not None:
                return {"success": True, "model_id": model.model_id, "already_loaded": True}

            self._is_loading = True
            self._load_started_at = time.time()
            self._load_progress = 0.0
            self._load_error = None
            registry.starting("video-gen-model", model=model.model_id)
            logger.info("[video_gen] Loading model: %s", model.model_id)

            try:
                self._unload_sync_locked()

                import torch  # noqa: PLC0415

                if torch.cuda.is_available():
                    device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    device = "mps"
                else:
                    raise RuntimeError(
                        "No supported compute device (CUDA or Apple Silicon MPS) "
                        "— video generation never runs on CPU."
                    )
                # bf16 on both CUDA and MPS. fp8 is NOT supported on Metal.
                #
                # Dtype safety review (after the image-gen black-image bug,
                # July 2026 — SDXL VAE NaN'd when loaded bf16 because
                # diffusers' force_upcast protection is fp16-only):
                #  - Wan: the VAE is explicitly loaded fp32 below per the Wan
                #    model cards, so the overflow-prone decode is already in
                #    full precision; transformer bf16 is the documented config.
                #  - LTX: the LTX model card and diffusers docs run the WHOLE
                #    pipeline (AutoencoderKLLTXVideo included) in bf16 — the
                #    VAE was trained for bf16 and has no fp16-only upcast gate,
                #    so bf16 is correct here, NOT the SDXL failure mode.
                dtype = torch.bfloat16

                local_path = str(model_dir(video_models_dir(), model.model_id))
                self._load_progress = 10.0

                # When the catalog sets load_variant, the download filter
                # fetched ONLY that variant's weight files — loading must ask
                # for the same variant to find them.
                variant_kwargs: dict = (
                    {"variant": model.load_variant} if model.load_variant else {}
                )

                if model.pipeline_type == "wan":
                    from diffusers import AutoencoderKLWan, WanPipeline  # noqa: PLC0415
                    # Wan model cards: VAE in fp32, transformer in bf16.
                    vae = AutoencoderKLWan.from_pretrained(
                        local_path,
                        subfolder="vae",
                        torch_dtype=torch.float32,
                        local_files_only=True,
                        **variant_kwargs,
                    )
                    self._load_progress = 40.0
                    pipe = WanPipeline.from_pretrained(
                        local_path,
                        vae=vae,
                        torch_dtype=dtype,
                        local_files_only=True,
                        **variant_kwargs,
                    )
                elif model.pipeline_type == "ltx":
                    from diffusers import LTXPipeline  # noqa: PLC0415
                    pipe = LTXPipeline.from_pretrained(
                        local_path,
                        torch_dtype=dtype,
                        local_files_only=True,
                        **variant_kwargs,
                    )
                else:
                    raise RuntimeError(f"Unknown pipeline type: {model.pipeline_type}")

                self._load_progress = 80.0
                pipe.to(device)

                self._pipeline = pipe
                self._i2v_pipeline = None
                self._loaded_model_id = model.model_id
                self._device = device
                self._load_progress = 100.0
                registry.ready("video-gen-model", model=model.model_id, device=device)
                logger.info(
                    "[video_gen] Model loaded: %s on %s dtype=%s",
                    model.model_id, device, dtype,
                )
                return {"success": True, "model_id": model.model_id, "device": device}

            except Exception as exc:
                registry.failed("video-gen-model", exc, model=model.model_id)
                self._pipeline = None
                self._i2v_pipeline = None
                self._loaded_model_id = None
                error = friendly_load_error(exc) or str(exc)
                # Surfaced in GET /video-gen/status as load_error so a
                # reconnecting UI sees WHY the model is not loaded.
                self._load_error = error
                return {"success": False, "error": error}
            finally:
                # is_loading may NEVER stay true after a load attempt.
                self._is_loading = False
                self._load_started_at = None

    def _unload_sync(self) -> None:
        with self._lock:
            self._unload_sync_locked()

    def _unload_sync_locked(self) -> None:
        if self._pipeline is None:
            return
        registry = get_registry()
        registry.stopping("video-gen-model")
        # Clear the references FIRST — even if the cache cleanup below fails,
        # the service must never keep reporting a model as loaded.
        self._pipeline = None
        self._i2v_pipeline = None
        self._loaded_model_id = None
        release_generation_memory("video_gen")
        registry.stopped("video-gen-model")

    # ── generation (job-based) ───────────────────────────────────────────────

    def start_generation(
        self,
        *,
        prompt: str,
        model_id: str,
        negative_prompt: str = "",
        width: int | None = None,
        height: int | None = None,
        num_frames: int | None = None,
        fps: int | None = None,
        steps: int | None = None,
        guidance: float | None = None,
        seed: int | None = None,
        image_base64: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> VideoJob:
        """Create a job and kick off the worker thread. Caller must have
        validated availability + downloaded/loaded state (the route does).

        ``extra_params`` are arbitrary pipeline kwargs merged LAST over the
        computed call kwargs in the worker — user values override every
        default, but may never override ``prompt``.

        Raises RuntimeError when a job is already running, ValueError for a
        bad request (unknown model, protected extra_params key, …).
        """
        model = self.get_model(model_id)
        if model is None:
            raise ValueError(f"Unknown model: {model_id}")
        if image_base64 and not model.supports_image_to_video:
            raise ValueError(f"{model.name} does not support image-to-video")
        # Fail the request NOW (400) instead of inside the worker thread.
        from app.services.media_gen.params import PROTECTED_PARAMS  # noqa: PLC0415
        blocked = PROTECTED_PARAMS & set(extra_params or {})
        if blocked:
            raise ValueError(
                f"extra_params may not override protected parameter(s): "
                f"{', '.join(sorted(blocked))}. Use the top-level request field instead."
            )

        req_w = width or model.default_width
        req_h = height or model.default_height
        req_frames = num_frames or model.default_num_frames
        snap_w, snap_h, snap_frames = _snap_generation_grid(
            model, req_w, req_h, req_frames
        )
        if (snap_w, snap_h, snap_frames) != (req_w, req_h, req_frames):
            logger.info(
                "[video_gen] Snapped %s params to valid grid: "
                "%dx%d x%d → %dx%d x%d frames",
                model.pipeline_type, req_w, req_h, req_frames,
                snap_w, snap_h, snap_frames,
            )

        # A null seed becomes a CONCRETE server-generated seed before the job
        # record is created — the used seed is always reported back (job +
        # media-library sidecar) so any result can be reproduced. Never let
        # diffusers pick one invisibly.
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        store = get_video_job_store()
        job = store.create(
            prompt=prompt,
            model_id=model_id,
            width=snap_w,
            height=snap_h,
            num_frames=snap_frames,
            fps=fps or model.default_fps,
            seed=seed,
        )

        resolved_steps = steps if steps is not None else model.recommended_steps
        resolved_guidance = guidance if guidance is not None else model.recommended_guidance

        worker = threading.Thread(
            target=self._run_job,
            kwargs={
                "job_id": job.job_id,
                "model": model,
                "negative_prompt": negative_prompt,
                "steps": resolved_steps,
                "guidance": resolved_guidance,
                "image_base64": image_base64,
                "extra_params": dict(extra_params or {}),
            },
            daemon=True,
            name=f"video-gen-{job.job_id[:8]}",
        )
        self._worker = worker
        worker.start()
        return job

    def _get_pipeline_for_mode(self, model: VideoGenModel, i2v: bool) -> Any:
        """Return the pipeline for the requested mode, deriving an I2V variant
        from the loaded T2V pipeline via from_pipe (shares weights — no second
        copy of the model in memory)."""
        if not i2v:
            return self._pipeline
        if self._i2v_pipeline is not None:
            return self._i2v_pipeline
        if model.pipeline_type == "wan":
            from diffusers import WanImageToVideoPipeline  # noqa: PLC0415
            self._i2v_pipeline = WanImageToVideoPipeline.from_pipe(self._pipeline)
        elif model.pipeline_type == "ltx":
            from diffusers import LTXImageToVideoPipeline  # noqa: PLC0415
            self._i2v_pipeline = LTXImageToVideoPipeline.from_pipe(self._pipeline)
        else:
            raise RuntimeError(f"Unknown pipeline type: {model.pipeline_type}")
        return self._i2v_pipeline

    def _run_job(
        self,
        *,
        job_id: str,
        model: VideoGenModel,
        negative_prompt: str,
        steps: int,
        guidance: float,
        image_base64: str | None,
        extra_params: dict[str, Any],
    ) -> None:
        """Worker thread: full generation → encode → job record updates.

        Every exit is a terminal job state: completed, failed (clear message,
        full traceback in the log) or cancelled (mid-flight abort via the
        per-step cancel event) — the job can never spin forever.
        """
        store = get_video_job_store()
        job = store.get(job_id)
        assert job is not None

        cancel_event = threading.Event()
        with self._lock:
            self._active_cancel = (job_id, cancel_event)
            self._active_supports_step_cancel = None

        try:
            import torch  # noqa: PLC0415

            with self._lock:
                if self._pipeline is None or self._loaded_model_id != model.model_id:
                    raise RuntimeError(
                        f"Model {model.model_id} is not loaded. "
                        "Call POST /video-gen/load first."
                    )
                pipe = self._get_pipeline_for_mode(model, i2v=bool(image_base64))

            if not store.mark_running(job_id, total_steps=steps):
                # Cancelled (DELETE /video-gen/jobs/{id}) between create and
                # worker start — never resurrect it.
                logger.info(
                    "[video_gen] Job %s: cancelled before start — skipping",
                    job_id[:8],
                )
                return

            call_kwargs: dict = {
                "prompt": job.prompt,
                "height": job.height,
                "width": job.width,
                "num_frames": job.num_frames,
                "num_inference_steps": steps,
            }
            # ALWAYS pass guidance_scale — 0.0 means "no CFG", not "use the
            # diffusers default". (Same omission bug as image-gen: skipping it
            # made SDXL fall back to CFG 5.0; Wan/LTX have nonzero defaults
            # too.)
            call_kwargs["guidance_scale"] = guidance
            if model.supports_negative_prompt and negative_prompt:
                call_kwargs["negative_prompt"] = negative_prompt
            if job.seed is not None:
                call_kwargs["generator"] = torch.Generator(
                    device=self._device or "cpu"
                ).manual_seed(job.seed)

            if image_base64:
                from PIL import Image  # noqa: PLC0415
                img = Image.open(io.BytesIO(base64.b64decode(image_base64)))
                img = img.convert("RGB").resize((job.width, job.height))
                call_kwargs["image"] = img

            # User-supplied extra_params merge LAST (they override every
            # computed default except the protected params) and are
            # validated against the pipeline signature BEFORE burning compute.
            from app.services.media_gen.params import (  # noqa: PLC0415
                merge_extra_params,
                validate_pipeline_kwargs,
            )
            validate_pipeline_kwargs(pipe, extra_params.keys())
            call_kwargs = merge_extra_params(call_kwargs, extra_params)

            # Per-step hook: job progress + mid-flight cancellation. Installed
            # AFTER the merge so nothing can displace it (callback kwargs are
            # also in PROTECTED_PARAMS). Wan/LTX (t2v + i2v) all support the
            # modern callback on diffusers >= 0.37.
            hook_mode = install_cancel_hook(
                pipe,
                call_kwargs,
                cancel_event=cancel_event,
                total_steps=steps,
                progress_callback=lambda step, total: store.update_progress(
                    job_id, current_step=step, total_steps=total
                ),
            )
            with self._lock:
                self._active_supports_step_cancel = hook_mode != "none"

            if cancel_event.is_set():
                raise GenerationCancelled("Cancelled before the pipeline started")

            t0 = time.monotonic()
            logger.info(
                "[video_gen] Job %s: generating %dx%d x%d frames, %d steps (%s)",
                job_id[:8], job.width, job.height, job.num_frames, steps,
                "i2v" if image_base64 else "t2v",
            )
            try:
                output = pipe(**call_kwargs)
            except (TypeError, ValueError) as exc:
                if extra_params:
                    # Loud recovery: name the user's parameters instead of a
                    # bare traceback or silent stripping.
                    raise RuntimeError(
                        "The pipeline rejected the generation parameters "
                        f"(extra_params: {', '.join(sorted(extra_params))}) — {exc}"
                    ) from exc
                raise
            # A cancel that landed on the final step (or against a pipeline
            # with no per-step callback) arrives here — never a surprise
            # late completion after the user asked to stop.
            if cancel_event.is_set():
                raise GenerationCancelled("Cancelled at pipeline completion")
            frames = output.frames[0]
            gen_seconds = time.monotonic() - t0
            logger.info(
                "[video_gen] Job %s: %d frames generated in %.1fs — encoding mp4",
                job_id[:8], len(frames), gen_seconds,
            )

            out_path = self._encode_mp4(frames, fps=job.fps, job_id=job_id)

            # Persist into the media library: MOVE the mp4 into
            # ~/.matrx/media/generated/videos/ + write the JSON sidecar. The
            # job's output_path is updated to the library location so
            # GET /video-gen/jobs/{id}/result keeps serving the file. A save
            # failure fails the job loudly — the library is part of the
            # contract, not best-effort.
            from app.services.media_gen.library import (  # noqa: PLC0415
                save_generated_video,
            )
            record_params = {
                k: v for k, v in call_kwargs.items()
                if k not in (
                    "generator", "image",
                    "callback_on_step_end", "callback", "callback_steps",
                )
            }
            item = save_generated_video(
                out_path,
                model_id=model.model_id,
                prompt=job.prompt,
                negative_prompt=negative_prompt,
                params=record_params,
                seed=job.seed,
                width=job.width,
                height=job.height,
                num_frames=job.num_frames,
                fps=job.fps,
                elapsed_seconds=time.monotonic() - t0,
            )
            store.mark_completed(
                job_id, output_path=item["file_path"], item_id=item["id"]
            )
            logger.info(
                "[video_gen] Job %s: completed → %s (library item %s)",
                job_id[:8], item["file_path"], item["id"],
            )

        except GenerationCancelled as exc:
            # User-requested abort — clean terminal state, never a traceback.
            logger.info("[video_gen] Job %s cancelled: %s", job_id[:8], exc)
            release_generation_memory("video_gen")
            store.mark_cancelled(job_id)
        except Exception as exc:
            # Outermost failure boundary for the worker thread: EVERYTHING
            # (OOM, encode failures, callback-raised errors, save failures)
            # becomes a terminal failed job with a clear message — the thread
            # never dies leaving the job stuck "running".
            logger.error("[video_gen] Job %s FAILED: %s", job_id[:8], exc, exc_info=True)
            release_generation_memory("video_gen")
            store.mark_failed(job_id, friendly_generation_error(exc))
        finally:
            with self._lock:
                self._active_cancel = None
                self._active_supports_step_cancel = None

    def _encode_mp4(self, frames: list[Any], *, fps: int, job_id: str):
        """Encode frames to H.264 mp4 with imageio-ffmpeg (core dep)."""
        import numpy as np  # noqa: PLC0415
        import imageio_ffmpeg  # noqa: PLC0415

        norm: list[Any] = []
        for f in frames:
            arr = np.asarray(f)
            if arr.dtype != np.uint8:
                arr = (np.clip(arr, 0.0, 1.0) * 255).round().astype(np.uint8)
            norm.append(np.ascontiguousarray(arr))

        h, w = norm[0].shape[:2]
        out_dir = generated_videos_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"video_{stamp}_{job_id[:8]}.mp4"

        writer = imageio_ffmpeg.write_frames(
            str(out_path),
            (w, h),
            fps=fps,
            codec="libx264",
            pix_fmt_in="rgb24",
            quality=8,
            macro_block_size=1,  # keep exact model output dims (already /16)
        )
        writer.send(None)  # seed the generator
        try:
            for arr in norm:
                writer.send(arr.tobytes())
        finally:
            writer.close()
        return out_path


# ── singleton ─────────────────────────────────────────────────────────────────

_service: VideoGenService | None = None


def get_video_gen_service() -> VideoGenService:
    global _service
    if _service is None:
        _service = VideoGenService()
    return _service
