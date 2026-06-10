"""Image generation service.

Uses Hugging Face diffusers for local image generation. This is an OPTIONAL
feature — diffusers and torch are declared as [image-gen] extras and are NOT
installed by default. All imports are lazy (inside methods) so that the module
can be imported and the route registered even when the packages are missing.
The endpoints return clear "not available" errors when deps are absent.

Supported pipeline types (see models.py):
  - "flux"                  → FluxPipeline
  - "hunyuan"               → HunyuanDiTPipeline
  - "stable-diffusion-xl"   → StableDiffusionXLPipeline
  - "stable-diffusion"      → StableDiffusionPipeline
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.services.image_gen.models import IMAGE_GEN_MODELS, ImageGenModel

logger = get_logger()

# ── availability check ────────────────────────────────────────────────────────

def _check_deps() -> tuple[bool, str]:
    """Return (available, reason). Fast — no heavy imports."""
    missing = []
    for pkg in ("torch", "diffusers", "transformers", "accelerate"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        return False, (
            f"Image generation requires optional packages: {', '.join(missing)}. "
            "Install with: uv sync --extra image-gen"
        )
    return True, ""


DEPS_AVAILABLE, DEPS_REASON = _check_deps()


# ── result type ───────────────────────────────────────────────────────────────

@dataclass
class GenerationResult:
    success: bool
    image_b64: str | None = None
    """Base64-encoded PNG image."""
    width: int = 0
    height: int = 0
    model_id: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None


# ── service class ─────────────────────────────────────────────────────────────

class ImageGenService:
    """Singleton service wrapping a loaded diffusers pipeline.

    A single pipeline is kept in memory at a time (the active model).
    Loading a different model unloads the current one first to free VRAM.
    All generation runs in a background thread pool to avoid blocking the
    FastAPI event loop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Serializes load_model() at the async boundary so two concurrent
        # requests can't both pass the "already loaded?" check and each spawn
        # a full model load (which would double VRAM use / OOM).
        self._load_lock = asyncio.Lock()
        self._pipeline: Any = None
        self._loaded_model_id: str | None = None
        self._is_loading = False
        self._load_progress: float = 0.0

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return DEPS_AVAILABLE

    @property
    def unavailable_reason(self) -> str:
        return DEPS_REASON

    @property
    def loaded_model_id(self) -> str | None:
        return self._loaded_model_id

    @property
    def is_loading(self) -> bool:
        return self._is_loading

    def get_status(self) -> dict:
        return {
            "available": self.available,
            "unavailable_reason": DEPS_REASON if not self.available else None,
            "loaded_model_id": self._loaded_model_id,
            "is_loading": self._is_loading,
            "load_progress": self._load_progress,
        }

    def list_models(self) -> list[ImageGenModel]:
        return IMAGE_GEN_MODELS

    def get_model(self, model_id: str) -> ImageGenModel | None:
        return next((m for m in IMAGE_GEN_MODELS if m.model_id == model_id), None)

    async def load_model(self, model_id: str) -> dict:
        """Load a model pipeline into memory. No-op if already loaded.

        Returns status dict with `success`, `model_id`, `error`.
        Runs in a background thread to avoid blocking the event loop.
        """
        if not self.available:
            return {"success": False, "error": self.unavailable_reason}

        model = self.get_model(model_id)
        if model is None:
            return {"success": False, "error": f"Unknown model: {model_id}"}

        # Hold the async lock across the check-and-load so concurrent callers
        # serialize instead of racing into two parallel model loads.
        async with self._load_lock:
            if self._loaded_model_id == model_id and self._pipeline is not None:
                return {"success": True, "model_id": model_id, "already_loaded": True}

            loop = asyncio.get_running_loop()
            self._event_loop = loop  # Captured before entering the thread pool
            return await loop.run_in_executor(None, self._load_model_sync, model)

    async def unload_model(self) -> dict:
        """Unload the current pipeline and free VRAM."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._unload_sync)
        return {"success": True}

    async def generate(
        self,
        prompt: str,
        model_id: str,
        negative_prompt: str = "",
        steps: int | None = None,
        guidance: float | None = None,
        width: int | None = None,
        height: int | None = None,
        seed: int | None = None,
    ) -> GenerationResult:
        """Generate an image. Loads the model if not already loaded."""
        if not self.available:
            return GenerationResult(success=False, error=self.unavailable_reason)

        model = self.get_model(model_id)
        if model is None:
            return GenerationResult(success=False, error=f"Unknown model: {model_id}")

        # Load if needed
        if self._loaded_model_id != model_id or self._pipeline is None:
            load_result = await self.load_model(model_id)
            if not load_result.get("success"):
                return GenerationResult(
                    success=False, error=load_result.get("error", "Failed to load model")
                )

        # Resolve defaults from model catalog
        resolved_steps = steps if steps is not None else model.recommended_steps
        resolved_guidance = guidance if guidance is not None else model.recommended_guidance
        resolved_width = width if width is not None else model.default_width
        resolved_height = height if height is not None else model.default_height

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._generate_sync,
            prompt,
            negative_prompt if model.supports_negative_prompt else "",
            resolved_steps,
            resolved_guidance,
            resolved_width,
            resolved_height,
            seed,
            model,
        )

    # ── sync internals (run in thread pool) ──────────────────────────────────

    def _report_to_dm(self, dl_id: str, model: "ImageGenModel", status: str, percent: float, error_msg: str | None = None) -> None:
        """Report image-gen model load progress to the universal download manager (best-effort)."""
        from datetime import datetime, timezone

        def _now_str() -> str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        try:
            import asyncio as _asyncio
            from app.services.downloads.manager import get_download_manager
            dm = get_download_manager()

            updated_at = _now_str()

            async def _push() -> None:
                from app.services.downloads.manager import ProgressEvent
                evt = ProgressEvent(
                    id=dl_id,
                    category="image_gen",
                    filename=model.model_id,
                    display_name=model.display_name,
                    status=status,
                    bytes_done=0,
                    total_bytes=0,
                    percent=percent,
                    part_current=1,
                    part_total=1,
                    speed_bps=0.0,
                    eta_seconds=None,
                    error_msg=error_msg,
                    updated_at=updated_at,
                    bandwidth_bps=dm._bandwidth_bps,
                )
                await dm._broadcast(evt)

            # Use the loop captured before entering the thread pool executor.
            # asyncio.get_event_loop() is deprecated from thread pool in Python 3.10+.
            try:
                loop = getattr(self, "_event_loop", None)
                if loop is not None and loop.is_running():
                    _asyncio.run_coroutine_threadsafe(_push(), loop)
            except Exception:
                pass
        except Exception:
            pass

    def _load_model_sync(self, model: "ImageGenModel") -> dict:
        with self._lock:
            if self._loaded_model_id == model.model_id and self._pipeline is not None:
                return {"success": True, "model_id": model.model_id, "already_loaded": True}

            self._is_loading = True
            self._load_progress = 0.0
            dl_id = f"image_gen-{model.model_id.replace('/', '-')}"
            logger.info("[image_gen] Loading model: %s", model.model_id)

            try:
                # Register with download manager
                self._report_to_dm(dl_id, model, "active", 0.0)

                # Unload existing pipeline first
                self._unload_sync_locked()

                import torch  # noqa: PLC0415
                from diffusers import (  # noqa: PLC0415
                    DiffusionPipeline,
                    FluxPipeline,
                    HunyuanDiTPipeline,
                    StableDiffusionPipeline,
                    StableDiffusionXLPipeline,
                )

                hf_token = (
                    os.environ.get("HF_TOKEN")
                    or os.environ.get("HUGGING_FACE_HUB_TOKEN")
                    or None
                )

                # Choose dtype based on hardware
                dtype = torch.float16
                if torch.cuda.is_available():
                    device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    device = "mps"
                    dtype = torch.float16
                else:
                    device = "cpu"
                    dtype = torch.float32  # CPU doesn't support float16 well

                common_kwargs: dict = {
                    "torch_dtype": dtype,
                    "use_safetensors": True,
                }
                if hf_token and model.requires_hf_token:
                    common_kwargs["token"] = hf_token

                self._load_progress = 10.0
                self._report_to_dm(dl_id, model, "active", 10.0)

                if model.pipeline_type == "flux":
                    pipe = FluxPipeline.from_pretrained(
                        model.model_id, **common_kwargs
                    )
                elif model.pipeline_type == "hunyuan":
                    pipe = HunyuanDiTPipeline.from_pretrained(
                        model.model_id, **common_kwargs
                    )
                elif model.pipeline_type in ("stable-diffusion-xl",):
                    pipe = StableDiffusionXLPipeline.from_pretrained(
                        model.model_id, **common_kwargs
                    )
                elif model.pipeline_type == "stable-diffusion":
                    pipe = StableDiffusionPipeline.from_pretrained(
                        model.model_id, **common_kwargs
                    )
                else:
                    pipe = DiffusionPipeline.from_pretrained(
                        model.model_id, **common_kwargs
                    )

                self._load_progress = 80.0
                self._report_to_dm(dl_id, model, "active", 80.0)

                pipe.to(device)

                # Enable memory optimization when available
                try:
                    pipe.enable_attention_slicing()
                except Exception:
                    pass

                self._pipeline = pipe
                self._loaded_model_id = model.model_id
                self._load_progress = 100.0
                self._report_to_dm(dl_id, model, "completed", 100.0)

                logger.info(
                    "[image_gen] Model loaded: %s on %s dtype=%s",
                    model.model_id, device, dtype,
                )
                return {"success": True, "model_id": model.model_id, "device": device}

            except Exception as exc:
                self._report_to_dm(dl_id, model, "failed", self._load_progress, str(exc))
                logger.error("[image_gen] Failed to load model %s: %s", model.model_id, exc, exc_info=True)
                self._pipeline = None
                self._loaded_model_id = None
                return {"success": False, "error": str(exc)}
            finally:
                self._is_loading = False

    def _unload_sync(self) -> None:
        with self._lock:
            self._unload_sync_locked()

    def _unload_sync_locked(self) -> None:
        """Must be called with self._lock held."""
        if self._pipeline is None:
            return
        try:
            import torch  # noqa: PLC0415
            del self._pipeline
            self._pipeline = None
            self._loaded_model_id = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception as exc:
            logger.warning("[image_gen] Error unloading pipeline: %s", exc)

    def _generate_sync(
        self,
        prompt: str,
        negative_prompt: str,
        steps: int,
        guidance: float,
        width: int,
        height: int,
        seed: int | None,
        model: ImageGenModel,
    ) -> GenerationResult:
        with self._lock:
            if self._pipeline is None:
                return GenerationResult(success=False, error="No model loaded")
            pipe = self._pipeline

        try:
            import torch  # noqa: PLC0415

            generator = None
            if seed is not None:
                device = str(
                    next(iter(pipe.components.values())).device
                    if hasattr(pipe, "components")
                    else "cpu"
                )
                generator = torch.Generator(device=device).manual_seed(seed)

            call_kwargs: dict = {
                "prompt": prompt,
                "num_inference_steps": steps,
                "width": width,
                "height": height,
            }

            if generator is not None:
                call_kwargs["generator"] = generator

            # Guidance / negative prompt only for applicable models
            if model.supports_negative_prompt and negative_prompt:
                call_kwargs["negative_prompt"] = negative_prompt
            if guidance > 0.0 and model.supports_negative_prompt:
                call_kwargs["guidance_scale"] = guidance
            elif guidance > 0.0 and model.pipeline_type == "flux":
                call_kwargs["guidance_scale"] = guidance

            t0 = time.monotonic()
            output = pipe(**call_kwargs)
            elapsed = time.monotonic() - t0

            image = output.images[0]

            # Encode to base64 PNG
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            return GenerationResult(
                success=True,
                image_b64=b64,
                width=image.width,
                height=image.height,
                model_id=model.model_id,
                elapsed_seconds=elapsed,
            )

        except Exception as exc:
            logger.error("[image_gen] Generation failed: %s", exc, exc_info=True)
            return GenerationResult(success=False, error=str(exc))


# ── singleton ─────────────────────────────────────────────────────────────────

_service: ImageGenService | None = None


def get_image_gen_service() -> ImageGenService:
    global _service
    if _service is None:
        _service = ImageGenService()
    return _service
