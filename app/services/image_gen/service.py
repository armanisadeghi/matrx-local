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

Image-to-image: when a request carries init image bytes, the loaded pipeline
is wrapped per generation via AutoPipelineForImage2Image.from_pipe (component
sharing — no re-load, near-zero extra memory; see _to_img2img). The init
image is aspect-fill resized + center-cropped to the requested dims
(prepare_init_image). The generation gate, cancel hooks, and library
persistence cover img2img identically to text-to-image.

LoRAs: applied per generation INSIDE the gate (_apply_loras) and ALWAYS
unloaded in a finally — the pipeline is stateless across generations. Requires
``peft`` in the managed image-gen packages (installer.py IMAGE_GEN_PACKAGES) —
diffusers' ``load_lora_weights`` / ``set_adapters`` gate on USE_PEFT_BACKEND.
LoRA weights are installed by the DownloadManager into
~/.matrx/image-models/loras/ (app/services/image_gen/loras.py).
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
from app.services.image_gen.models import (
    ImageGenModel,
    get_image_gen_model,
    get_image_gen_models,
)
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
# managed-package upgrade. Diffusers 0.37.x cannot load valid AI-Toolkit/Civitai
# Z-Image LoRAs that omit per-layer ``.alpha`` tensors: its converter blindly
# pops those optional keys (MXL-D-054). 0.39 includes the compatible converter
# and Z-Image key-normalization fixes, so keeping 0.37 installed makes every
# such adapter fail even though its download is complete and valid.
MIN_DIFFUSERS_VERSION = (0, 39, 0)


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
            benign_missing = isinstance(exc, ModuleNotFoundError) and exc.name in (
                pkg,
                pkg.split(".", 1)[0],
            )
            if pkg == "torch" and benign_missing:
                # torch is the root runtime dependency. Packages such as
                # accelerate are deliberately bundled for lightweight shared
                # utilities but cannot import without torch; probing them now
                # produces misleading frozen-gap tracebacks on a clean first
                # install. The managed installer installs/rechecks the entire
                # stack together, so one root reason is both quieter and more
                # accurate.
                missing.append(pkg)
                break
            if not benign_missing:
                logger.warning(
                    "[image_gen] optional package %r is present but failed to import "
                    "(likely a frozen-build gap — a missing stdlib module it lazily "
                    "imports): %s",
                    pkg,
                    exc,
                    exc_info=True,
                )
            missing.append(
                f"{pkg} ({exc})" if not isinstance(exc, ImportError) else pkg
            )
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


# ── image-to-image helpers ────────────────────────────────────────────────────


def prepare_init_image(image_bytes: bytes, width: int, height: int) -> Any:
    """Decode + fit an init image to the requested dims.

    Aspect-fill + center-crop (documented contract): the image is scaled so it
    COVERS width x height (no letterboxing, no distortion), then the overflow
    is cropped symmetrically around the center. Returns an RGB PIL image of
    exactly (width, height). Raises ValueError on undecodable bytes.
    """
    import io as _io  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    try:
        img = Image.open(_io.BytesIO(image_bytes))
        img.load()
    except Exception as exc:
        raise ValueError(f"init image could not be decoded: {exc}") from exc
    img = img.convert("RGB")
    scale = max(width / img.width, height / img.height)
    new_w = max(width, round(img.width * scale))
    new_h = max(height, round(img.height * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def _to_img2img(pipe: Any, model: "ImageGenModel") -> Any:
    """Wrap the loaded text-to-image pipeline as its family's img2img pipeline.

    Uses ``AutoPipelineForImage2Image.from_pipe`` — COMPONENT SHARING: the new
    pipeline object reuses the exact loaded modules (transformer/unet, VAE,
    text encoders), so there is no re-download, no re-load, and near-zero
    extra memory. Verified per catalog family against diffusers 0.39.0
    (task-class resolution): SD → StableDiffusionImg2ImgPipeline, SDXL →
    StableDiffusionXLImg2ImgPipeline, flux → FluxImg2ImgPipeline, qwen-image →
    QwenImageImg2ImgPipeline, z-image → ZImageImg2ImgPipeline, flux2-klein →
    Flux2KleinPipeline (the unified pipeline IS its own img2img). Fallback:
    ``DiffusionPipeline.from_pipe`` on the mapped class via the same mapping —
    both failing is a loud error, never a silent text-to-image run.
    """
    from diffusers import AutoPipelineForImage2Image  # noqa: PLC0415

    try:
        return AutoPipelineForImage2Image.from_pipe(pipe)
    except Exception as exc:  # noqa: BLE001 — fall back, then fail loudly
        logger.warning(
            "[image_gen] AutoPipelineForImage2Image.from_pipe failed for %s "
            "(%s) — trying explicit task-class from_pipe",
            model.pipeline_type,
            exc,
        )
        try:
            from diffusers.pipelines.auto_pipeline import (  # noqa: PLC0415
                AUTO_IMAGE2IMAGE_PIPELINES_MAPPING,
                _get_task_class,
            )

            cls = _get_task_class(
                AUTO_IMAGE2IMAGE_PIPELINES_MAPPING, type(pipe).__name__
            )
            return cls.from_pipe(pipe)
        except Exception as exc2:
            raise RuntimeError(
                f"Image-to-image is unavailable for {model.model_id}: the "
                f"img2img pipeline could not be constructed from the loaded "
                f"components ({exc2})"
            ) from exc2


def _ensure_peft_for_loras() -> None:
    """LoRA load/activate/unload in diffusers requires the PEFT backend."""
    try:
        import peft  # noqa: F401, PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "LoRA styles require the PEFT package, which is not installed in "
            "your image-generation environment. Open Image Generation and run "
            "'Update AI packages' (POST /image-gen/install) — the installer "
            "will add PEFT automatically."
        ) from exc


def _apply_loras(
    call_pipe: Any, lora_specs: list[dict[str, Any]], model: "ImageGenModel"
) -> list[dict[str, Any]]:
    """Load + activate the requested LoRAs on ``call_pipe``. Runs INSIDE the
    generation gate; the caller MUST pair it with unload_lora_weights() in a
    finally (stateless per-generation — the pipeline is always left clean).

    Returns the applied-lora records for the sidecar/job. Raises RuntimeError
    naming the offending LoRA on any failure (missing install, family
    mismatch, diffusers load error) — the generation aborts loudly.
    """
    from app.services.image_gen.loras import (  # noqa: PLC0415
        check_lora_model_compat,
        get_installed_lora,
    )

    _ensure_peft_for_loras()

    names: list[str] = []
    scales: list[float] = []
    applied: list[dict[str, Any]] = []
    for i, spec in enumerate(lora_specs):
        lora_id = str(spec["id"])
        scale = float(spec.get("scale", 1.0))
        meta = get_installed_lora(lora_id)
        if meta is None or not meta.get("installed"):
            raise RuntimeError(
                f"LoRA '{lora_id}' is not installed — download it first via "
                "POST /image-gen/loras/download (GET /image-gen/loras lists "
                "installed ids)."
            )
        try:
            check_lora_model_compat(model.lora_family, meta)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        adapter = f"matrx_lora_{i}"
        try:
            call_pipe.load_lora_weights(
                meta["dir"], weight_name=meta["weight_name"], adapter_name=adapter
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load LoRA '{lora_id}' ({meta['repo_id']}, "
                f"{meta['weight_name']}): {exc}"
            ) from exc
        names.append(adapter)
        scales.append(scale)
        applied.append(
            {
                "id": lora_id,
                "repo_id": meta["repo_id"],
                "weight_name": meta["weight_name"],
                "scale": scale,
            }
        )
    call_pipe.set_adapters(names, adapter_weights=scales)
    logger.info(
        "[image_gen] Applied %d LoRA(s): %s",
        len(applied),
        ", ".join(f"{a['id']}@{a['scale']}" for a in applied),
    )
    return applied


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
        # THE generation gate: exactly one generation (load + pipe()) may run
        # at a time, ever. Both the one-shot path (POST /image-gen/generate)
        # and the job runner acquire it around the whole critical section. A
        # diffusers pipeline is NOT re-entrant — two concurrent pipe() calls
        # on the same object corrupt each other's scheduler state, and a
        # concurrent load_model() can unload the pipeline mid-generation.
        # This was the shipped "clicking Generate breaks all queued items and
        # the current one" bug (July 2026). Cancel registration lives INSIDE
        # the gate so a cancel always targets the generation that owns the
        # pipeline — a waiter parked on the gate is untouched and proceeds
        # after the cancelled run releases it.
        self._gen_gate = asyncio.Lock()
        self._pipeline: Any = None
        self._loaded_model_id: str | None = None
        self._loaded_text_encoder_id: str | None = None
        self._is_loading = False
        self._load_progress: float = 0.0
        self._load_error: str | None = None
        self._load_started_at: float | None = None
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
        # Never allow the known-broken 0.37.x Z-Image LoRA converter to run.
        # Startup migrates existing installs before optional packages are
        # imported; this remains a defense-in-depth gate if migration failed.
        return DEPS_AVAILABLE and not are_packages_outdated()

    @property
    def unavailable_reason(self) -> str:
        if are_packages_outdated():
            return (
                "A required AI runtime update is pending or failed. Image generation "
                "is paused until Diffusers 0.39.0 is installed; restart or reconnect "
                "to retry the automatic migration."
            )
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
            "unavailable_reason": self.unavailable_reason
            if not self.available
            else None,
            "loaded_model_id": self._loaded_model_id,
            "loaded_text_encoder_id": self._loaded_text_encoder_id,
            "is_loading": self._is_loading,
            "load_progress": self._load_progress,
            "load_error": self._load_error,
            "load_started_at": self._load_started_at,
            "load_age_seconds": (
                time.time() - self._load_started_at
                if self._is_loading and self._load_started_at
                else None
            ),
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
                "started_at": time.time(),
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
        return get_image_gen_models()

    def get_model(self, model_id: str) -> ImageGenModel | None:
        m = get_image_gen_model(model_id)
        if m is not None:
            return m
        # Custom registry fallback — user-registered models behave exactly
        # like catalog models from here on (load/generate/img2img/LoRA flags
        # all come off the bridged ImageGenModel).
        from app.services.image_gen.custom_models import get_custom_model  # noqa: PLC0415

        return get_custom_model(model_id)

    def is_downloaded(self, model_id: str) -> bool:
        return is_model_downloaded(image_models_dir(), model_id)

    def model_hardware_check(self, model: ImageGenModel) -> tuple[bool, str | None]:
        return check_model_hardware(min_vram_gb=model.vram_gb, min_ram_gb=model.ram_gb)

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

        if model.custom:
            # Custom models carry their own download spec (HF snapshot or
            # Civitai direct URL) in the registry entry.
            from app.services.image_gen.custom_models import (  # noqa: PLC0415
                enqueue_custom_download,
                get_custom_entry,
            )

            custom_entry = get_custom_entry(model_id)
            if custom_entry is None:
                return {"queued": False, "error": f"Unknown custom model: {model_id}"}
            return await enqueue_custom_download(custom_entry)

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

    async def load_model(
        self, model_id: str, text_encoder_id: str | None = None
    ) -> dict:
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

        encoder = None
        if text_encoder_id is not None:
            from app.services.image_gen.text_encoders import (  # noqa: PLC0415
                get_model_encoder,
                is_encoder_installed,
            )

            encoder = get_model_encoder(model, text_encoder_id)
            if encoder is None:
                return {
                    "success": False,
                    "error": f"Unknown text encoder '{text_encoder_id}' for {model.name}.",
                }
            if not is_encoder_installed(encoder):
                return {
                    "success": False,
                    "error": f"Text encoder '{text_encoder_id}' is not downloaded.",
                    "needs_text_encoder_download": True,
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
            if (
                self._loaded_model_id == model_id
                and self._loaded_text_encoder_id == text_encoder_id
                and self._pipeline is not None
            ):
                return {"success": True, "model_id": model_id, "already_loaded": True}

            loop = asyncio.get_running_loop()
            self._event_loop = loop  # Captured before entering the thread pool
            return await loop.run_in_executor(
                None, self._load_model_sync, model, encoder
            )

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
        init_image_bytes: bytes | None = None,
        strength: float | None = None,
        revision_parent_item_id: str | None = None,
        revision_root_item_id: str | None = None,
        loras: list[dict[str, Any]] | None = None,
        text_encoder_id: str | None = None,
    ) -> GenerationResult:
        """Generate an image. Loads the model if not already loaded.

        ``init_image_bytes`` (decoded PNG/JPEG bytes) switches the run to
        IMAGE-TO-IMAGE: the loaded pipeline is wrapped via
        ``AutoPipelineForImage2Image.from_pipe`` (component sharing — no
        re-load) and the init image is aspect-fill resized + center-cropped
        to the requested dims. ``strength`` (0..1, default 0.6) is only valid
        with an init image and only for families whose img2img accepts it
        (all except flux2-klein). ``loras`` is a list of
        ``{"id": <installed-lora-id>, "scale": float}`` applied inside the
        generation gate and ALWAYS unloaded afterwards (stateless).

        ``extra_params`` are arbitrary pipeline kwargs merged LAST over the
        computed call kwargs — the user's values override every default, but
        may never override protected params (loud ValueError → success=False).

        A null ``seed`` becomes a concrete server-generated one — the used
        seed is always reported back (result + media-library sidecar).

        ``progress_callback(step, total_steps)`` fires per denoising step
        when the pipeline supports callback_on_step_end (job queue progress).

        ``job_id`` marks this generation as belonging to a queue job (for
        cancellation bookkeeping); None means a one-shot request.

        Serialized by the generation gate: when another generation (job or
        one-shot) is in flight, this call WAITS its turn instead of running
        concurrently against the same pipeline. Cancelling the running
        generation never cancels a waiter — the waiter simply runs next.

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

        # img2img request-shape guards (the routes 400 these first; this is
        # the defense-in-depth for direct service callers / the job runner).
        if strength is not None and init_image_bytes is None:
            return GenerationResult(
                success=False,
                error="strength only applies to image-to-image — provide "
                "init_image_b64 (an input image) or omit strength.",
            )
        if init_image_bytes is not None and not model.supports_img2img:
            return GenerationResult(
                success=False,
                error=f"{model.name} does not support image-to-image "
                "(no img2img pipeline exists for this family).",
            )
        if strength is not None and not model.img2img_strength:
            return GenerationResult(
                success=False,
                error=f"{model.name} performs reference-image editing without "
                "a strength control — omit strength for this model.",
            )
        if revision_root_item_id is not None and revision_parent_item_id is None:
            return GenerationResult(
                success=False,
                error="revision_root_item_id requires revision_parent_item_id.",
            )
        if revision_parent_item_id is not None and init_image_bytes is None:
            return GenerationResult(
                success=False,
                error="revision requires an input image containing the parent result.",
            )
        if revision_parent_item_id is not None and model.pipeline_type not in {
            "z-image",
            "flux",
            "flux2-klein",
        }:
            return GenerationResult(
                success=False,
                error=f"{model.name} is not enabled for iterative revision — "
                "use Z-Image or FLUX.",
            )

        if text_encoder_id is not None:
            from app.services.image_gen.text_encoders import (  # noqa: PLC0415
                get_model_encoder,
                is_encoder_installed,
            )

            encoder = get_model_encoder(model, text_encoder_id)
            if encoder is None:
                return GenerationResult(
                    success=False,
                    error=f"Unknown text encoder '{text_encoder_id}' for {model.name}.",
                )
            if not is_encoder_installed(encoder):
                return GenerationResult(
                    success=False,
                    error=f"Text encoder '{text_encoder_id}' is not downloaded.",
                )

        if self._gen_gate.locked():
            logger.info(
                "[image_gen] Generation request (%s) waiting for the in-flight "
                "generation to finish — the gate serializes all pipeline use",
                f"job {job_id[:8]}" if job_id else "one-shot",
            )
        async with self._gen_gate:
            # Registered INSIDE the gate (and before the potentially
            # minutes-long model load) so a cancel always has exactly the
            # generation that owns the pipeline to grab — never a waiter.
            cancel_event = threading.Event()
            token = self._register_generation(
                "job" if job_id else "oneshot", job_id, cancel_event
            )
            try:
                # Load if needed
                if (
                    self._loaded_model_id != model_id
                    or self._loaded_text_encoder_id != text_encoder_id
                    or self._pipeline is None
                ):
                    load_result = await self.load_model(model_id, text_encoder_id)
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
                        success=False,
                        error="Cancelled",
                        cancelled=True,
                        model_id=model_id,
                    )

                # Resolve defaults from model catalog
                resolved_steps = steps if steps is not None else model.recommended_steps
                resolved_guidance = (
                    guidance if guidance is not None else model.recommended_guidance
                )
                resolved_width = width if width is not None else model.default_width
                resolved_height = height if height is not None else model.default_height
                used_seed = seed if seed is not None else random.randint(0, 2**32 - 1)

                import functools  # noqa: PLC0415

                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None,
                    functools.partial(
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
                        init_image_bytes=init_image_bytes,
                        strength=strength,
                        revision_parent_item_id=revision_parent_item_id,
                        revision_root_item_id=revision_root_item_id,
                        loras=list(loras or []),
                        text_encoder_id=text_encoder_id,
                    ),
                )
            finally:
                self._unregister_generation(token)

    # ── sync internals (run in thread pool) ──────────────────────────────────

    def _load_model_sync(
        self, model: "ImageGenModel", text_encoder: Any = None
    ) -> dict:
        with self._lock:
            text_encoder_id = (
                text_encoder.encoder_id if text_encoder is not None else None
            )
            if (
                self._loaded_model_id == model.model_id
                and self._loaded_text_encoder_id == text_encoder_id
                and self._pipeline is not None
            ):
                return {
                    "success": True,
                    "model_id": model.model_id,
                    "already_loaded": True,
                }

            self._is_loading = True
            self._load_started_at = time.time()
            self._load_progress = 0.0
            self._load_error = None
            logger.info(
                "[image_gen] Loading model: %s (text_encoder=%s)",
                model.model_id,
                text_encoder_id or "standard",
            )

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
                    "stable-diffusion-xl",
                    "stable-diffusion",
                )
                if torch.cuda.is_available():
                    device = "cuda"
                    dtype = torch.float16 if sd_family else torch.bfloat16
                elif (
                    hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
                ):
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

                if model.format == "single_file":
                    # Custom single-file checkpoints (Civitai / HF root
                    # .safetensors). Registration already rejected any
                    # family without from_single_file support (verified
                    # against diffusers 0.37.1, the runtime floor: SD, SDXL,
                    # FLUX, Z-Image inherit FromSingleFileMixin; Qwen and
                    # Flux2Klein do NOT — see custom_models.py).
                    #
                    # NOTE on local_files_only: from_single_file resolves the
                    # pipeline's CONFIG (a few KB of json) from the reference
                    # HF repo unless already cached, so we deliberately do
                    # NOT pass local_files_only here. For flux checkpoints
                    # (transformer-only files) it also fetches the text
                    # encoders/VAE on first load — surfaced as a warning +
                    # requires_hf_token at inspect/registration time, never
                    # silently sprung on the user without notice.
                    if not model.weight_name:
                        raise RuntimeError(
                            f"Custom model {model.model_id} has no weight_name "
                            "in its registry entry — re-register it."
                        )
                    weight_path = str(
                        model_dir(image_models_dir(), model.model_id)
                        / model.weight_name
                    )
                    single_file_classes: dict[str, Any] = {
                        "stable-diffusion": StableDiffusionPipeline,
                        "stable-diffusion-xl": StableDiffusionXLPipeline,
                        "flux": FluxPipeline,
                    }
                    if model.pipeline_type == "z-image":
                        from diffusers import ZImagePipeline  # noqa: PLC0415

                        sf_cls: Any = ZImagePipeline
                    else:
                        sf_cls = single_file_classes.get(model.pipeline_type)
                    if sf_cls is None:
                        # Defense in depth — registration must have refused.
                        raise RuntimeError(
                            f"Pipeline family '{model.pipeline_type}' has no "
                            "from_single_file loader — this model should have "
                            "been rejected at registration."
                        )
                    pipe = sf_cls.from_single_file(
                        weight_path,
                        torch_dtype=dtype,
                        token=read_hf_token(),
                    )
                elif model.pipeline_type == "flux2-klein":
                    from diffusers import Flux2KleinPipeline  # noqa: PLC0415

                    encoder_kwargs: dict[str, Any] = {}
                    if text_encoder is not None and text_encoder.format != "state_dict":
                        from app.services.image_gen.text_encoders import (  # noqa: PLC0415
                            load_encoder_components,
                        )

                        alt_encoder, alt_tokenizer = load_encoder_components(
                            text_encoder, dtype=dtype
                        )
                        encoder_kwargs = {
                            "text_encoder": alt_encoder,
                            "tokenizer": alt_tokenizer,
                        }
                    pipe = Flux2KleinPipeline.from_pretrained(
                        local_path, **common_kwargs, **encoder_kwargs
                    )
                    if text_encoder is not None and text_encoder.format == "state_dict":
                        from app.services.image_gen.text_encoders import (  # noqa: PLC0415
                            encoder_dir,
                        )

                        if not text_encoder.weight_name:
                            raise RuntimeError(
                                f"State-dict encoder '{text_encoder.encoder_id}' has no weight_name"
                            )
                        state = torch.load(
                            encoder_dir(text_encoder.encoder_id)
                            / text_encoder.weight_name,
                            map_location="cpu",
                            weights_only=True,
                        )
                        pipe.text_encoder.load_state_dict(state, strict=True)
                        del state
                elif model.pipeline_type == "z-image":
                    from diffusers import ZImagePipeline  # noqa: PLC0415

                    # low_cpu_mem_usage=False per the Z-Image model card.
                    pipe = ZImagePipeline.from_pretrained(
                        local_path, low_cpu_mem_usage=False, **common_kwargs
                    )
                elif model.pipeline_type == "qwen-image":
                    from diffusers import QwenImagePipeline  # noqa: PLC0415

                    pipe = QwenImagePipeline.from_pretrained(
                        local_path, **common_kwargs
                    )
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
                    pipe = DiffusionPipeline.from_pretrained(
                        local_path, **common_kwargs
                    )

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
                self._loaded_text_encoder_id = text_encoder_id
                self._device = device
                self._load_progress = 100.0

                logger.info(
                    "[image_gen] Model loaded: %s on %s dtype=%s text_encoder=%s",
                    model.model_id,
                    device,
                    dtype,
                    text_encoder_id or "standard",
                )
                return {"success": True, "model_id": model.model_id, "device": device}

            except Exception as exc:
                logger.error(
                    "[image_gen] Failed to load model %s: %s",
                    model.model_id,
                    exc,
                    exc_info=True,
                )
                self._pipeline = None
                self._loaded_model_id = None
                self._loaded_text_encoder_id = None
                error = friendly_load_error(exc) or str(exc)
                # Surfaced in GET /image-gen/status as load_error so a
                # reconnecting UI sees WHY the model is not loaded.
                self._load_error = error
                return {"success": False, "error": error}
            finally:
                # is_loading may NEVER stay true after a load attempt —
                # a stuck true here is the "spins forever" bug.
                self._is_loading = False
                self._load_started_at = None

    def _unload_sync(self) -> None:
        with self._lock:
            self._unload_sync_locked()

    def _unload_sync_locked(self) -> None:
        """Must be called with self._lock held."""
        if self._pipeline is None:
            self._loaded_model_id = None
            self._loaded_text_encoder_id = None
            return
        # Clear the references FIRST — even if the cache cleanup below fails,
        # the service must never keep reporting a model as loaded.
        self._pipeline = None
        self._loaded_model_id = None
        self._loaded_text_encoder_id = None
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
        *,
        init_image_bytes: bytes | None = None,
        strength: float | None = None,
        revision_parent_item_id: str | None = None,
        revision_root_item_id: str | None = None,
        loras: list[dict[str, Any]] | None = None,
        text_encoder_id: str | None = None,
    ) -> GenerationResult:
        with self._lock:
            if self._pipeline is None:
                return GenerationResult(success=False, error="No model loaded")
            pipe = self._pipeline
        if cancel_event is None:
            cancel_event = threading.Event()
        lora_specs = list(loras or [])

        try:
            import torch  # noqa: PLC0415

            # seed is always concrete here (generate() manufactures one when
            # the caller omitted it) — every image is reproducible.
            device = self._device or "cpu"
            generator = torch.Generator(device=device).manual_seed(seed)

            # ── img2img: wrap the SAME loaded components (no re-load) ────────
            call_pipe = pipe
            init_image_sha256: str | None = None
            resolved_strength: float | None = None
            if init_image_bytes is not None:
                import hashlib  # noqa: PLC0415

                init_image_sha256 = hashlib.sha256(init_image_bytes).hexdigest()
                call_pipe = _to_img2img(pipe, model)
                init_image = prepare_init_image(init_image_bytes, width, height)
                if model.img2img_strength:
                    resolved_strength = strength if strength is not None else 0.6
                    # img2img runs ~steps*strength denoising steps — zero
                    # effective steps is a guaranteed pipeline failure; say
                    # so BEFORE burning a model load.
                    if int(steps * resolved_strength) < 1:
                        raise RuntimeError(
                            f"steps ({steps}) x strength ({resolved_strength}) "
                            "rounds to zero denoising steps — raise steps or "
                            "strength so steps*strength >= 1 (e.g. steps=2 at "
                            "strength 0.6)."
                        )

            call_kwargs: dict = {
                "prompt": prompt,
                "num_inference_steps": steps,
                "width": width,
                "height": height,
            }
            if init_image_bytes is not None:
                call_kwargs["image"] = init_image
                if resolved_strength is not None:
                    call_kwargs["strength"] = resolved_strength
                # SD/SDXL img2img pipelines take no width/height — the output
                # size IS the (pre-resized) init image size, which we already
                # fitted to the requested dims above.
                try:
                    import inspect as _inspect  # noqa: PLC0415

                    i2i_params = _inspect.signature(call_pipe.__call__).parameters
                    for dim_key in ("width", "height"):
                        if dim_key not in i2i_params:
                            call_kwargs.pop(dim_key, None)
                except (TypeError, ValueError):
                    pass

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

            validate_pipeline_kwargs(call_pipe, extra_params.keys())
            call_kwargs = merge_extra_params(call_kwargs, extra_params)

            # Per-step hook: job progress + mid-flight cancellation. Installed
            # AFTER the merge so nothing can displace it (the callback kwargs
            # are also in PROTECTED_PARAMS). All catalog pipelines support the
            # modern callback; "none" (generic DiffusionPipeline path only)
            # means cancel lands when the pipeline finishes — reported via
            # mid_flight=false on the cancel endpoints.
            hook_mode = install_cancel_hook(
                call_pipe,
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

            # LoRAs load + activate INSIDE the gate and are ALWAYS unloaded in
            # the finally below — every generation starts and ends with a
            # clean, adapter-free pipeline (stateless per-generation). A
            # failed load aborts the generation loudly, naming the LoRA.
            applied_loras: list[dict[str, Any]] = []
            t0 = time.monotonic()
            try:
                if lora_specs:
                    applied_loras = _apply_loras(call_pipe, lora_specs, model)
                try:
                    output = call_pipe(**call_kwargs)
                except (TypeError, ValueError) as exc:
                    if extra_params:
                        # Loud recovery: name the user's parameters instead of
                        # silently stripping them or surfacing a bare traceback.
                        raise RuntimeError(
                            "The pipeline rejected the generation parameters "
                            f"(extra_params: {', '.join(sorted(extra_params))}) — {exc}"
                        ) from exc
                    raise
            finally:
                if lora_specs:
                    try:
                        call_pipe.unload_lora_weights()
                    except Exception as unload_exc:  # noqa: BLE001 — must not mask the real failure
                        logger.error(
                            "[image_gen] Failed to unload LoRA weights after "
                            "generation — the pipeline may retain adapters "
                            "until the model is reloaded: %s",
                            unload_exc,
                            exc_info=True,
                        )
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
                k: v
                for k, v in call_kwargs.items()
                if k
                not in (
                    # "image" (the init PIL image) is deliberately NOT
                    # persisted — the sidecar notes init_image_sha256 instead.
                    "generator",
                    "callback_on_step_end",
                    "callback",
                    "callback_steps",
                    "image",
                )
            }
            record_params["has_init_image"] = init_image_bytes is not None
            record_params["pipeline_type"] = model.pipeline_type
            if init_image_sha256 is not None:
                record_params["init_image_sha256"] = init_image_sha256
            if revision_parent_item_id is not None:
                record_params["revision_parent_item_id"] = revision_parent_item_id
                record_params["revision_root_item_id"] = (
                    revision_root_item_id or revision_parent_item_id
                )
            if applied_loras:
                record_params["loras"] = applied_loras
            if text_encoder_id is not None:
                record_params["text_encoder_id"] = text_encoder_id
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
                # Stored beside the result so Remix restores the actual input
                # image, not just a note that there was one.
                init_image_bytes=init_image_bytes,
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
                success=False,
                error="Cancelled",
                cancelled=True,
                model_id=model.model_id,
                seed=seed,
            )
        except Exception as exc:
            # Outermost failure boundary: EVERYTHING (OOM, NaN/black-image
            # guard, callback-raised errors, save failures) becomes a terminal
            # success=False with a clear message — never an escaped exception,
            # never a hang. Full traceback goes to the log.
            logger.error("[image_gen] Generation failed: %s", exc, exc_info=True)
            release_generation_memory("image_gen")
            return GenerationResult(
                success=False,
                error=friendly_generation_error(exc),
                model_id=model.model_id,
                seed=seed,
            )


# ── singleton ─────────────────────────────────────────────────────────────────

_service: ImageGenService | None = None


def get_image_gen_service() -> ImageGenService:
    global _service
    if _service is None:
        _service = ImageGenService()
    return _service
