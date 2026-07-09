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
import inspect
import io
import threading
import time
from datetime import datetime
from typing import Any

from app.common.system_logger import get_logger
from app.launcher import get_registry
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
from app.services.video_gen.models import VIDEO_GEN_MODELS, VideoGenModel

logger = get_logger()


def _check_deps() -> tuple[bool, str]:
    """Return (available, reason). Fast — no heavy imports."""
    missing = []
    for pkg in ("torch", "diffusers", "transformers", "accelerate"):
        try:
            __import__(pkg)
        except Exception as exc:  # noqa: BLE001 — a corrupt install (e.g. OSError
            # on a dylib load) must NOT crash `import app.main`; surface it as a
            # normal "unavailable" reason instead.
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
        self._device: str | None = None
        self._worker: threading.Thread | None = None

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
        return {
            "available": available,
            "unavailable_reason": reason,
            "packages_installed": DEPS_AVAILABLE,
            "hardware_supported": compute.video_supported,
            "hardware_reason": compute.video_unsupported_reason,
            "loaded_model_id": self._loaded_model_id,
            "is_loading": self._is_loading,
            "load_progress": self._load_progress,
            "device": self._device or compute.device,
            "active_job_id": get_video_job_store().active_job_id,
        }

    def list_models(self) -> list[VideoGenModel]:
        return VIDEO_GEN_MODELS

    def get_model(self, model_id: str) -> VideoGenModel | None:
        return next((m for m in VIDEO_GEN_MODELS if m.model_id == model_id), None)

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
                    f"{model.name} is a gated model — set your Hugging Face token "
                    "in Settings before downloading it."
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
            self._load_progress = 0.0
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
                return {"success": False, "error": str(exc)}
            finally:
                self._is_loading = False

    def _unload_sync(self) -> None:
        with self._lock:
            self._unload_sync_locked()

    def _unload_sync_locked(self) -> None:
        if self._pipeline is None:
            return
        registry = get_registry()
        registry.stopping("video-gen-model")
        try:
            import torch  # noqa: PLC0415
            del self._pipeline
            self._pipeline = None
            self._i2v_pipeline = None
            self._loaded_model_id = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception as exc:
            logger.warning("[video_gen] Error unloading pipeline: %s", exc)
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
    ) -> VideoJob:
        """Create a job and kick off the worker thread. Caller must have
        validated availability + downloaded/loaded state (the route does).

        Raises RuntimeError when a job is already running.
        """
        model = self.get_model(model_id)
        if model is None:
            raise ValueError(f"Unknown model: {model_id}")
        if image_base64 and not model.supports_image_to_video:
            raise ValueError(f"{model.name} does not support image-to-video")

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
    ) -> None:
        """Worker thread: full generation → encode → job record updates."""
        store = get_video_job_store()
        job = store.get(job_id)
        assert job is not None

        try:
            import torch  # noqa: PLC0415

            with self._lock:
                if self._pipeline is None or self._loaded_model_id != model.model_id:
                    raise RuntimeError(
                        f"Model {model.model_id} is not loaded. "
                        "Call POST /video-gen/load first."
                    )
                pipe = self._get_pipeline_for_mode(model, i2v=bool(image_base64))

            store.mark_running(job_id, total_steps=steps)

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

            # Step callback — updates the job record every denoising step.
            def _on_step_end(p: Any, step: int, timestep: Any, cb_kwargs: dict) -> dict:
                store.update_progress(job_id, current_step=step + 1, total_steps=steps)
                return cb_kwargs

            sig_params = inspect.signature(pipe.__call__).parameters
            if "callback_on_step_end" in sig_params:
                call_kwargs["callback_on_step_end"] = _on_step_end
            else:
                logger.warning(
                    "[video_gen] %s pipeline has no callback_on_step_end — "
                    "progress will jump 0→100", model.pipeline_type,
                )

            t0 = time.monotonic()
            logger.info(
                "[video_gen] Job %s: generating %dx%d x%d frames, %d steps (%s)",
                job_id[:8], job.width, job.height, job.num_frames, steps,
                "i2v" if image_base64 else "t2v",
            )
            output = pipe(**call_kwargs)
            frames = output.frames[0]
            gen_seconds = time.monotonic() - t0
            logger.info(
                "[video_gen] Job %s: %d frames generated in %.1fs — encoding mp4",
                job_id[:8], len(frames), gen_seconds,
            )

            out_path = self._encode_mp4(frames, fps=job.fps, job_id=job_id)
            store.mark_completed(job_id, output_path=str(out_path))
            logger.info("[video_gen] Job %s: completed → %s", job_id[:8], out_path)

        except Exception as exc:
            logger.error("[video_gen] Job %s FAILED: %s", job_id[:8], exc, exc_info=True)
            store.mark_failed(job_id, str(exc))

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
