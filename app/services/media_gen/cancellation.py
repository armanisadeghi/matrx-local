"""Mid-flight cancellation + terminal-failure plumbing shared by image and
video generation.

The problem this solves: a diffusers generation is a long, synchronous
``pipe(**kwargs)`` call in a worker thread. Without a per-step hook there is
NO way to stop it — the user watches a spinner forever and force-kills the
app. The fix:

1. Every generation gets a fresh ``threading.Event`` (the cancel event).
2. ``install_cancel_hook`` wires a per-denoising-step callback into the
   pipeline call kwargs. On every step it (a) raises
   :class:`GenerationCancelled` when the event is set — which aborts the
   pipeline immediately — and (b) forwards progress to the job store.
3. The service catches ``GenerationCancelled`` (NEVER the generic handler),
   releases VRAM/intermediates via ``release_generation_memory``, and records
   a clean *cancelled* terminal state — no stack trace, no hang.

Callback support per pipeline family (verified against diffusers 0.39.0,
July 2026 — the runtime floor is 0.37 where the same holds):

    Flux2KleinPipeline / ZImagePipeline / QwenImagePipeline / FluxPipeline /
    StableDiffusionPipeline / StableDiffusionXLPipeline (image) and
    WanPipeline / WanImageToVideoPipeline / LTXPipeline /
    LTXImageToVideoPipeline (video) ALL accept ``callback_on_step_end``.

The legacy ``callback``/``callback_steps`` path (pre-0.34 SD pipelines) is
kept as a fallback for non-catalog pipelines loaded through the generic
``DiffusionPipeline`` branch. When a pipeline supports neither, the helper
returns ``"none"`` and logs loudly — the caller reports the limitation in the
API response (cancel then lands when the pipeline call finishes, never
silently).
"""

from __future__ import annotations

import gc
import inspect
import threading
from typing import Any, Callable

from app.common.system_logger import get_logger

logger = get_logger()


class GenerationCancelled(Exception):
    """Raised inside the per-step pipeline callback to abort a generation.

    Deliberately NOT a subclass of RuntimeError so the generic failure
    handlers can never confuse a user-requested cancel with a real error.
    """


def install_cancel_hook(
    pipe: Any,
    call_kwargs: dict[str, Any],
    *,
    cancel_event: threading.Event,
    total_steps: int,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    """Wire the engine-owned per-step callback into ``call_kwargs``.

    Returns the mechanism used:
      - ``"modern"`` — ``callback_on_step_end`` (every catalog pipeline)
      - ``"legacy"`` — pre-0.34 ``callback`` + ``callback_steps``
      - ``"none"``   — pipeline supports neither; mid-flight abort is
        impossible and the caller must surface that in its API response.

    Call this AFTER merging user extra_params. The callback kwargs are in
    ``PROTECTED_PARAMS`` so a user can never displace the cancel hook, but we
    still defensively drop any that slipped in (loudly).
    """
    for key in ("callback_on_step_end", "callback", "callback_steps"):
        if key in call_kwargs:
            logger.warning(
                "[media_gen] Dropping caller-supplied %r — the per-step "
                "callback is engine-owned (cancellation depends on it)", key,
            )
            call_kwargs.pop(key)

    try:
        sig_params = inspect.signature(pipe.__call__).parameters
    except (TypeError, ValueError):
        sig_params = {}

    def _check_and_report(step: int) -> None:
        if cancel_event.is_set():
            raise GenerationCancelled(
                f"Generation cancelled at step {step}/{total_steps}"
            )
        if progress_callback is not None:
            progress_callback(step, total_steps)

    if "callback_on_step_end" in sig_params:
        def _on_step_end(
            _pipe: Any, step: int, _timestep: Any, cb_kwargs: dict
        ) -> dict:
            _check_and_report(step + 1)
            return cb_kwargs

        call_kwargs["callback_on_step_end"] = _on_step_end
        return "modern"

    if "callback" in sig_params:
        def _legacy_callback(step: int, _timestep: Any, _latents: Any) -> None:
            _check_and_report(step + 1)

        call_kwargs["callback"] = _legacy_callback
        if "callback_steps" in sig_params:
            call_kwargs["callback_steps"] = 1
        return "legacy"

    logger.warning(
        "[media_gen] %s supports neither callback_on_step_end nor callback — "
        "mid-flight cancellation is unavailable for this pipeline; a cancel "
        "request takes effect when the pipeline call finishes",
        type(pipe).__name__,
    )
    return "none"


def release_generation_memory(tag: str) -> None:
    """Free VRAM/intermediates after an aborted or failed generation.

    Best-effort by design and never raises — it runs inside failure paths.
    """
    try:
        gc.collect()
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
            and hasattr(torch, "mps")
        ):
            torch.mps.empty_cache()
    except Exception as exc:  # noqa: BLE001 — cleanup must not mask the real failure
        logger.warning("[%s] Could not release GPU memory after abort: %s", tag, exc)


def friendly_generation_error(exc: BaseException) -> str:
    """Terminal, human-readable error text for a failed generation.

    Maps out-of-memory failures (torch.cuda.OutOfMemoryError, and the MPS
    RuntimeError whose text contains "out of memory") to an actionable
    message. Detection is by class NAME + message so this module never
    imports torch. Everything else keeps its own message — the full
    traceback is always in the log.
    """
    text = str(exc)
    if type(exc).__name__ == "OutOfMemoryError" or "out of memory" in text.lower():
        return (
            "Out of memory during generation — close other GPU-heavy apps or "
            "try a smaller width/height, fewer steps/frames, or a smaller model."
        )
    return text or type(exc).__name__
