"""Image generation service.

Uses Hugging Face diffusers for local image generation. This is an OPTIONAL
feature — diffusers and torch are installed on demand by installer.py into
~/.matrx/image-gen-packages/ (NOT bundled). All imports are lazy (inside
methods) so that the module can be imported and the route registered even when
the packages are missing. The endpoints return clear "not available" errors
when deps are absent.

Model weights are NOT downloaded inside from_pretrained (that was the shipped
bug: silent multi-GB downloads with no progress). Instead:

  1. POST /image-gen/download enqueues the repo into the universal
     DownloadManager (category "image_gen"), which fetches it into
     ~/.matrx/image-models/<sanitized-id>/ with live byte progress on
     /downloads/stream and writes a .download-complete marker.
  2. load_model() loads ONLY from that local dir with local_files_only=True
     and refuses (needs_download=True) when the marker is absent.

Supported pipeline types (see models.py):
  - "flux2-klein"           → Flux2KleinPipeline
  - "z-image"               → ZImagePipeline
  - "qwen-image"            → QwenImagePipeline
  - "flux"                  → FluxPipeline
  - "stable-diffusion-xl"   → StableDiffusionXLPipeline
  - "stable-diffusion"      → StableDiffusionPipeline
"""

from __future__ import annotations

import asyncio
import base64
import gc
import io
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.common.system_logger import get_logger
from app.services.image_gen.models import IMAGE_GEN_MODELS, ImageGenModel
from app.services.media_gen.cancellation import (
    GenerationCancelled,
    friendly_generation_error,
    install_cancel_hook,
    release_generation_memory,
)
from app.services.media_gen.hardware import (
    check_model_hardware,
    select_device,
)
from app.services.media_gen.paths import (
    image_models_dir,
    is_model_downloaded,
    model_dir,
    read_hf_token,
    sanitize_model_id,
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
        f"persists, use 'Update AI packages' in the Image tab."
    )

# Runtime minimum: below this the "packages outdated" banner fires and forces a
# reinstall. Verified that every pipeline class in the current catalog
# (Flux2Klein / ZImage / QwenImage) already exists and imports in diffusers
# 0.37.1, so the runtime floor is 0.37 — a higher floor would force a pointless
# multi-GB re-install for users who already have a working 0.37.x. FRESH installs
# still pin >=0.39 (installer.py / pyproject [image-gen]) to get the newest fixes.
MIN_DIFFUSERS_VERSION = (0, 37, 0)


# ── availability check ────────────────────────────────────────────────────────

def _check_deps() -> tuple[bool, str]:
    """Return (available, reason). Fast — no heavy imports."""
    missing = []
    for pkg in ("torch", "diffusers", "transformers", "accelerate"):
        try:
            __import__(pkg)
        except Exception as exc:  # noqa: BLE001 — a corrupt install (e.g. OSError
            # on a dylib load) must NOT crash `import app.main`; surface it as a
            # normal "unavailable" reason instead.
            # Loud recovery: a plain "No module named '<pkg>'" is the benign
            # not-installed case (stays quiet). But a DIFFERENT missing module
            # while importing an installed package — e.g. "No module named
            # 'timeit'" during `import torch` — is a frozen-build bug (PyInstaller
            # omitted a stdlib module the package lazily imports). That, and any
            # non-ImportError (ABI mismatch, dylib load failure), gets a loud
            # traceback so it isn't hidden behind "packages not installed".
            benign_missing = (
                isinstance(exc, ModuleNotFoundError)
                and exc.name in (pkg, pkg.split(".", 1)[0])
            )
            if not benign_missing:
                logger.warning(
                    "[image_gen] optional package %r is present but failed to import "
                    "(likely a frozen-build gap — a missing stdlib module it lazily "
                    "imports): %s", pkg, exc, exc_info=True,
                )
            missing.append(f"{pkg} ({exc})" if not isinstance(exc, ImportError) else pkg)
    if missing:
        return False, (
            f"Image generation requires optional packages: {', '.join(missing)}. "
            "Install them from the app (POST /image-gen/install)."
        )
    return True, ""


DEPS_AVAILABLE, DEPS_REASON = _check_deps()


def get_installed_diffusers_version() -> str | None:
    """Installed diffusers version string, or None when not installed."""
    try:
        import diffusers  # noqa: PLC0415
        return str(diffusers.__version__)
    except ImportError:
        return None


def _parse_version(v: str) -> tuple[int, int, int]:
    parts: list[int] = []
    for token in v.split(".")[:3]:
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def are_packages_outdated() -> bool:
    """True when diffusers is installed but older than MIN_DIFFUSERS_VERSION."""
    v = get_installed_diffusers_version()
    if v is None:
        return False
    return _parse_version(v) < MIN_DIFFUSERS_VERSION


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
    item_id: str | None = None
    """Media-library item id — every successful generation is persisted to
    ~/.matrx/media/generated/images/ (see app/services/media_gen/library.py)."""
    file_path: str | None = None
    """Absolute path of the persisted PNG."""
    seed: int | None = None
    """The CONCRETE seed used. When the request omitted a seed the service
    generates one server-side (never lets diffusers pick invisibly) so every
    image is reproducible."""
    cancelled: bool = False
    """True when the generation was aborted by a cancel request (POST
    /image-gen/cancel or DELETE /image-gen/jobs/{id} on a running job).
    Always paired with success=False and error="Cancelled"."""


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
        self._load_error: str | None = None
        self._device: str | None = None
        # Active generations, keyed by an opaque token. Each entry:
        #   {"kind": "oneshot"|"job", "job_id": str|None,
        #    "event": threading.Event, "cancel_requested": bool,
        #    "supports_step_cancel": bool|None}
        # Registered in generate() BEFORE the (potentially minutes-long) model
        # load, so a cancel always has something to grab — the exact
        # "spinning forever with no way to stop it" scenario.
        self._active_gens: dict[str, dict[str, Any]] = {}
        self._gen_counter = 0

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
        with self._lock:
            gens = [dict(g) for g in self._active_gens.values()]
        return {
            "available": self.available,
            "unavailable_reason": DEPS_REASON if not self.available else None,
            "loaded_model_id": self._loaded_model_id,
            "is_loading": self._is_loading,
            "load_progress": self._load_progress,
            "load_error": self._load_error,
            "is_generating": bool(gens),
            "cancel_requested": any(g["cancel_requested"] for g in gens),
            "packages_version": get_installed_diffusers_version(),
            "packages_outdated": are_packages_outdated(),
            "device": self._device or select_device(),
        }

    # ── cancellation ──────────────────────────────────────────────────────────

    def _register_generation(
        self, kind: str, job_id: str | None, event: "threading.Event"
    ) -> str:
        with self._lock:
            self._gen_counter += 1
            token = f"gen-{self._gen_counter}"
            self._active_gens[token] = {
                "kind": kind,
                "job_id": job_id,
                "event": event,
                "cancel_requested": False,
                "supports_step_cancel": None,
            }
            return token

    def _unregister_generation(self, token: str) -> None:
        with self._lock:
            self._active_gens.pop(token, None)

    def request_cancel(self) -> dict:
        """Cancel the currently running generation(s) — one-shot or job.

        Returns {"cancelled": True, "was": "oneshot"|"job", "job_id": ...,
        "mid_flight": bool} or {"cancelled": False, "reason":
        "nothing_running"}. ``mid_flight=False`` means the running pipeline
        supports no per-step callback (non-catalog pipeline) — the cancel is
        recorded and takes effect when the pipeline call finishes, never
        silently dropped.
        """
        with self._lock:
            gens = list(self._active_gens.values())
            if not gens:
                return {"cancelled": False, "reason": "nothing_running"}
            for g in gens:
                g["cancel_requested"] = True
                g["event"].set()
            primary = gens[0]
            return {
                "cancelled": True,
                "was": primary["kind"],
                "job_id": primary["job_id"],
                # None = hook not installed yet (still loading / pre-pipe) —
                # the cancel event is checked before AND after pipe() too, so
                # treat it as cancellable.
                "mid_flight": primary["supports_step_cancel"] is not False,
            }

    def request_cancel_job(self, job_id: str) -> dict:
        """Cancel a specific RUNNING job's generation. Returns {"found": bool,
        "mid_flight": bool} — found=False when that job has no in-flight
        generation (already finishing, or not started yet)."""
        with self._lock:
            for g in self._active_gens.values():
                if g["job_id"] == job_id:
                    g["cancel_requested"] = True
                    g["event"].set()
                    return {
                        "found": True,
                        "mid_flight": g["supports_step_cancel"] is not False,
                    }
        return {"found": False, "mid_flight": False}

    def list_models(self) -> list[ImageGenModel]:
        return IMAGE_GEN_MODELS

    def get_model(self, model_id: str) -> ImageGenModel | None:
        return next((m for m in IMAGE_GEN_MODELS if m.model_id == model_id), None)

    def is_downloaded(self, model_id: str) -> bool:
        return is_model_downloaded(image_models_dir(), model_id)

    def model_hardware_check(self, model: ImageGenModel) -> tuple[bool, str | None]:
        return check_model_hardware(
            min_vram_gb=model.vram_gb, min_ram_gb=model.ram_gb
        )

    async def start_download(self, model_id: str) -> dict:
        """Queue the model's weights into the universal DownloadManager.

        Returns {"queued": True, "download_id": ...} — progress streams over
        /downloads/stream (category "image_gen"). Idempotent.
        """
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
        dest = model_dir(image_models_dir(), model_id)
        entry = await get_download_manager().enqueue(
            category="image_gen",
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

    async def load_model(self, model_id: str) -> dict:
        """Load a model pipeline into memory. No-op if already loaded.

        NEVER triggers a network download: when the local weights are absent
        this returns {"success": False, "needs_download": True}. The route
        maps that to a 409 so the UI can offer the Download button.
        Runs in a background thread to avoid blocking the event loop.
        """
        if not self.available:
            return {"success": False, "error": self.unavailable_reason}

        model = self.get_model(model_id)
        if model is None:
            return {"success": False, "error": f"Unknown model: {model_id}"}

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
        extra_params: dict[str, Any] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        job_id: str | None = None,
    ) -> GenerationResult:
        """Generate an image. Loads the model if not already loaded.

        ``extra_params`` are arbitrary pipeline kwargs merged LAST over the
        computed call kwargs — the user's values override every default, but
        may never override protected params (loud ValueError → success=False).

        A null ``seed`` becomes a concrete server-generated one — the used
        seed is always reported back (result + media-library sidecar).

        ``progress_callback(step, total_steps)`` fires per denoising step
        when the pipeline supports callback_on_step_end (job queue progress).

        ``job_id`` marks this generation as belonging to a queue job (for
        cancellation bookkeeping); None means a one-shot request.

        Cancellable at any point via request_cancel()/request_cancel_job():
        during the model load (checked right after), before the pipeline
        call, at every denoising step, and after the pipeline call.
        Cancelled generations return success=False, error="Cancelled",
        cancelled=True — promptly, never a hang."""
        if not self.available:
            return GenerationResult(success=False, error=self.unavailable_reason)

        model = self.get_model(model_id)
        if model is None:
            return GenerationResult(success=False, error=f"Unknown model: {model_id}")

        cancel_event = threading.Event()
        token = self._register_generation(
            "job" if job_id else "oneshot", job_id, cancel_event
        )
        try:
            # Load if needed
            if self._loaded_model_id != model_id or self._pipeline is None:
                load_result = await self.load_model(model_id)
                if not load_result.get("success"):
                    return GenerationResult(
                        success=False,
                        error=load_result.get("error", "Failed to load model"),
                    )
            # A cancel that arrived during the (minutes-long) model load lands
            # here — the load itself is not abortable, the generation is.
            if cancel_event.is_set():
                logger.info("[image_gen] Generation cancelled during model load")
                return GenerationResult(
                    success=False, error="Cancelled", cancelled=True,
                    model_id=model_id,
                )

            # Resolve defaults from model catalog
            resolved_steps = steps if steps is not None else model.recommended_steps
            resolved_guidance = guidance if guidance is not None else model.recommended_guidance
            resolved_width = width if width is not None else model.default_width
            resolved_height = height if height is not None else model.default_height
            used_seed = seed if seed is not None else random.randint(0, 2**32 - 1)

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
                used_seed,
                model,
                extra_params or {},
                progress_callback,
                cancel_event,
                token,
            )
        finally:
            self._unregister_generation(token)

    # ── sync internals (run in thread pool) ──────────────────────────────────

    def _load_model_sync(self, model: "ImageGenModel") -> dict:
        with self._lock:
            if self._loaded_model_id == model.model_id and self._pipeline is not None:
                return {"success": True, "model_id": model.model_id, "already_loaded": True}

            self._is_loading = True
            self._load_progress = 0.0
            self._load_error = None
            logger.info("[image_gen] Loading model: %s", model.model_id)

            try:
                # Unload existing pipeline first
                self._unload_sync_locked()

                import torch  # noqa: PLC0415
                from diffusers import (  # noqa: PLC0415
                    DiffusionPipeline,
                    FluxPipeline,
                    StableDiffusionPipeline,
                    StableDiffusionXLPipeline,
                )

                # Device + dtype — PER PIPELINE FAMILY, not one-size-fits-all.
                #
                # SD / SDXL family MUST load in torch.float16, never bfloat16:
                # diffusers' built-in black-image protection for the SDXL VAE
                # (``needs_upcasting = vae.dtype == torch.float16 and
                # vae.config.force_upcast``) only engages for fp16. Loading
                # sdxl-turbo in bf16 on MPS left the VAE decoding in bf16,
                # which overflowed to NaN and shipped SOLID BLACK 843-byte
                # PNGs in live testing (July 2026). fp16 + the automatic fp32
                # VAE upcast is the canonical SDXL configuration everywhere.
                #
                # The flow-matching models (flux/flux2-klein/z-image/qwen) are
                # trained and documented in bf16 — fp16 overflows THEIR
                # transformers — so they stay bf16. fp8 is NOT supported on
                # Metal; never attempt it. CPU stays fp32.
                sd_family = model.pipeline_type in (
                    "stable-diffusion-xl", "stable-diffusion"
                )
                if torch.cuda.is_available():
                    device = "cuda"
                    dtype = torch.float16 if sd_family else torch.bfloat16
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    device = "mps"
                    dtype = torch.float16 if sd_family else torch.bfloat16
                else:
                    device = "cpu"
                    dtype = torch.float32  # CPU doesn't support half precision well

                local_path = str(model_dir(image_models_dir(), model.model_id))
                common_kwargs: dict = {
                    "torch_dtype": dtype,
                    "use_safetensors": True,
                    "local_files_only": True,
                }
                if model.load_variant:
                    # The download filter fetched ONLY the variant weight files
                    # (e.g. *.fp16.safetensors) — loading must ask for the same
                    # variant or from_pretrained won't find the weights.
                    common_kwargs["variant"] = model.load_variant

                self._load_progress = 10.0

                if model.pipeline_type == "flux2-klein":
                    from diffusers import Flux2KleinPipeline  # noqa: PLC0415
                    pipe = Flux2KleinPipeline.from_pretrained(local_path, **common_kwargs)
                elif model.pipeline_type == "z-image":
                    from diffusers import ZImagePipeline  # noqa: PLC0415
                    # low_cpu_mem_usage=False per the Z-Image model card.
                    pipe = ZImagePipeline.from_pretrained(
                        local_path, low_cpu_mem_usage=False, **common_kwargs
                    )
                elif model.pipeline_type == "qwen-image":
                    from diffusers import QwenImagePipeline  # noqa: PLC0415
                    pipe = QwenImagePipeline.from_pretrained(local_path, **common_kwargs)
                elif model.pipeline_type == "flux":
                    pipe = FluxPipeline.from_pretrained(local_path, **common_kwargs)
                elif model.pipeline_type == "stable-diffusion-xl":
                    pipe = StableDiffusionXLPipeline.from_pretrained(
                        local_path, **common_kwargs
                    )
                elif model.pipeline_type == "stable-diffusion":
                    pipe = StableDiffusionPipeline.from_pretrained(
                        local_path, **common_kwargs
                    )
                else:
                    pipe = DiffusionPipeline.from_pretrained(local_path, **common_kwargs)

                self._load_progress = 80.0

                pipe.to(device)

                # Do NOT enable attention slicing. It is a pre-torch-2 memory
                # hack that is unnecessary with SDPA, and on MPS + fp16 with a
                # CFG batch it overflows to NaN — verified live (July 2026):
                # sdxl-turbo with enable_attention_slicing() produced solid
                # black images (std 0.0); the identical call without slicing
                # produced a correct image (std 105.9).

                self._pipeline = pipe
                self._loaded_model_id = model.model_id
                self._device = device
                self._load_progress = 100.0

                logger.info(
                    "[image_gen] Model loaded: %s on %s dtype=%s",
                    model.model_id, device, dtype,
                )
                return {"success": True, "model_id": model.model_id, "device": device}

            except Exception as exc:
                logger.error("[image_gen] Failed to load model %s: %s", model.model_id, exc, exc_info=True)
                self._pipeline = None
                self._loaded_model_id = None
                error = friendly_load_error(exc) or str(exc)
                # Surfaced in GET /image-gen/status as load_error so a
                # reconnecting UI sees WHY the model is not loaded.
                self._load_error = error
                return {"success": False, "error": error}
            finally:
                # is_loading may NEVER stay true after a load attempt —
                # a stuck true here is the "spins forever" bug.
                self._is_loading = False

    def _unload_sync(self) -> None:
        with self._lock:
            self._unload_sync_locked()

    def _unload_sync_locked(self) -> None:
        """Must be called with self._lock held."""
        if self._pipeline is None:
            return
        # Clear the references FIRST — even if the cache cleanup below fails,
        # the service must never keep reporting a model as loaded.
        self._pipeline = None
        self._loaded_model_id = None
        try:
            import torch  # noqa: PLC0415
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif (
                hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
                and hasattr(torch, "mps")
            ):
                torch.mps.empty_cache()
        except Exception as exc:
            logger.warning("[image_gen] Error freeing memory on unload: %s", exc)

    def _generate_sync(
        self,
        prompt: str,
        negative_prompt: str,
        steps: int,
        guidance: float,
        width: int,
        height: int,
        seed: int,
        model: ImageGenModel,
        extra_params: dict[str, Any],
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
        gen_token: str | None = None,
    ) -> GenerationResult:
        with self._lock:
            if self._pipeline is None:
                return GenerationResult(success=False, error="No model loaded")
            pipe = self._pipeline
        if cancel_event is None:
            cancel_event = threading.Event()

        try:
            import torch  # noqa: PLC0415

            # seed is always concrete here (generate() manufactures one when
            # the caller omitted it) — every image is reproducible.
            device = self._device or "cpu"
            generator = torch.Generator(device=device).manual_seed(seed)

            call_kwargs: dict = {
                "prompt": prompt,
                "num_inference_steps": steps,
                "width": width,
                "height": height,
            }

            call_kwargs["generator"] = generator

            # Guidance / negative prompt only for applicable models.
            if model.pipeline_type == "qwen-image":
                # QwenImagePipeline uses true_cfg_scale (verified on the model
                # card) and needs a negative prompt for CFG to engage.
                if guidance > 0.0:
                    call_kwargs["true_cfg_scale"] = guidance
                call_kwargs["negative_prompt"] = negative_prompt or " "
            else:
                if model.supports_negative_prompt and negative_prompt:
                    call_kwargs["negative_prompt"] = negative_prompt
                # ALWAYS pass guidance_scale — guidance 0.0 is a meaningful
                # value ("no CFG"), not "use the default". Omitting it made
                # diffusers fall back to its own default (5.0 for SDXL), which
                # silently ran sdxl-turbo with CFG enabled: wrong results for
                # a turbo model, double the UNet compute, and the trigger for
                # the MPS black-image path (July 2026).
                call_kwargs["guidance_scale"] = guidance

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
            # AFTER the merge so nothing can displace it (the callback kwargs
            # are also in PROTECTED_PARAMS). All catalog pipelines support the
            # modern callback; "none" (generic DiffusionPipeline path only)
            # means cancel lands when the pipeline finishes — reported via
            # mid_flight=false on the cancel endpoints.
            hook_mode = install_cancel_hook(
                pipe,
                call_kwargs,
                cancel_event=cancel_event,
                total_steps=steps,
                progress_callback=progress_callback,
            )
            if gen_token is not None:
                with self._lock:
                    gen = self._active_gens.get(gen_token)
                    if gen is not None:
                        gen["supports_step_cancel"] = hook_mode != "none"

            if cancel_event.is_set():
                raise GenerationCancelled("Cancelled before the pipeline started")

            t0 = time.monotonic()
            try:
                output = pipe(**call_kwargs)
            except (TypeError, ValueError) as exc:
                if extra_params:
                    # Loud recovery: name the user's parameters instead of
                    # silently stripping them or surfacing a bare traceback.
                    raise RuntimeError(
                        "The pipeline rejected the generation parameters "
                        f"(extra_params: {', '.join(sorted(extra_params))}) — {exc}"
                    ) from exc
                raise
            elapsed = time.monotonic() - t0

            # A cancel that landed on the final step (or against a pipeline
            # with no per-step callback) arrives here — a cancelled request
            # returns cancelled, never a surprise late result.
            if cancel_event.is_set():
                raise GenerationCancelled("Cancelled at pipeline completion")

            image = output.images[0]

            # Loud recovery: a numerically-collapsed generation (fp16/bf16
            # overflow → NaN in the VAE decode) comes out of diffusers'
            # numpy_to_pil as a perfectly constant image (usually solid
            # black). A real generation is never constant. Raise instead of
            # silently returning a black rectangle — this exact failure
            # shipped 843-byte black PNGs in live testing (July 2026).
            import numpy as np  # noqa: PLC0415
            arr = np.asarray(image)
            if arr.size == 0 or int(arr.max()) == int(arr.min()):
                raise RuntimeError(
                    "Generation produced a constant "
                    f"(value={int(arr.min()) if arr.size else 'empty'}) image — "
                    "numerical overflow (NaN) in the diffusion/VAE pipeline. "
                    f"model={model.model_id} device={self._device} — this is a "
                    "dtype bug, not a prompt problem; report it."
                )

            # Encode to base64 PNG
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            png_bytes = buf.getvalue()
            b64 = base64.b64encode(png_bytes).decode("utf-8")

            # Persist into the media library. A save failure is a real
            # failure (disk full / permissions) — it propagates to the
            # except below and returns success=False with the reason; the
            # library is part of the generation contract, not best-effort.
            from app.services.media_gen.library import (  # noqa: PLC0415
                save_generated_image,
            )
            record_params = {
                k: v for k, v in call_kwargs.items()
                if k not in (
                    "generator", "callback_on_step_end", "callback", "callback_steps",
                )
            }
            item = save_generated_image(
                png_bytes,
                model_id=model.model_id,
                prompt=prompt,
                negative_prompt=negative_prompt,
                params=record_params,
                seed=seed,
                width=image.width,
                height=image.height,
                elapsed_seconds=elapsed,
            )

            return GenerationResult(
                success=True,
                image_b64=b64,
                width=image.width,
                height=image.height,
                model_id=model.model_id,
                elapsed_seconds=elapsed,
                item_id=item["id"],
                file_path=item["file_path"],
                seed=seed,
            )

        except GenerationCancelled as exc:
            # User-requested abort — clean terminal state, never a traceback.
            logger.info("[image_gen] Generation cancelled: %s", exc)
            release_generation_memory("image_gen")
            return GenerationResult(
                success=False, error="Cancelled", cancelled=True,
                model_id=model.model_id, seed=seed,
            )
        except Exception as exc:
            # Outermost failure boundary: EVERYTHING (OOM, NaN/black-image
            # guard, callback-raised errors, save failures) becomes a terminal
            # success=False with a clear message — never an escaped exception,
            # never a hang. Full traceback goes to the log.
            logger.error("[image_gen] Generation failed: %s", exc, exc_info=True)
            release_generation_memory("image_gen")
            return GenerationResult(
                success=False, error=friendly_generation_error(exc),
                model_id=model.model_id, seed=seed,
            )


# ── singleton ─────────────────────────────────────────────────────────────────

_service: ImageGenService | None = None


def get_image_gen_service() -> ImageGenService:
    global _service
    if _service is None:
        _service = ImageGenService()
    return _service
