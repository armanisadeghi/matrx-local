"""Generation-parameter plumbing shared by image and video generation.

Two jobs live here:

1. ``merge_extra_params`` / ``validate_pipeline_kwargs`` — the extra_params
   passthrough. API callers may send ``extra_params: {...}`` with ANY kwarg the
   underlying diffusers pipeline accepts. User values are merged LAST so they
   override every computed default — with one exception: ``prompt`` is
   protected. The prompt is the request's first-class field and the value
   stored in the media library; letting extra_params silently replace it would
   make the stored record lie about what was generated. Attempting to override
   it raises a loud ValueError instead.

2. ``image_effective_params`` / ``video_effective_params`` — the COMPLETE
   effective default parameter set for a model, split into ``common`` (the
   first-class request fields) and ``advanced`` (every other kwarg the service
   actually passes to the pipeline for that family, plus a small set of
   verified-safe diffusers knobs with their diffusers defaults). These back
   ``GET /image-gen/params/{model_id}`` and ``GET /video-gen/params/{model_id}``
   so the UI can show — and let the user change — every setting; nothing is
   silently defaulted.

   The ``advanced`` maps are derived from the real call paths in
   ``ImageGenService._generate_sync`` and ``VideoGenService._run_job`` — if you
   change what those pass to ``pipe(**call_kwargs)``, update the maps here in
   the same change.
"""

from __future__ import annotations

import inspect
from typing import Any, Iterable, Mapping

from app.services.image_gen.models import ImageGenModel
from app.services.video_gen.models import VideoGenModel

# Keys extra_params may never override. ``prompt`` is the request's
# authoritative field (and the media-library record of what was generated).
PROTECTED_PARAMS: frozenset[str] = frozenset({"prompt"})


def merge_extra_params(
    call_kwargs: Mapping[str, Any], extra_params: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Merge user-supplied extra_params over the computed call kwargs.

    extra_params wins on every key EXCEPT the protected ones (``prompt``) —
    overriding those raises ValueError (loud recovery, never silent stripping).
    Returns a new dict; inputs are not mutated.
    """
    merged = dict(call_kwargs)
    if not extra_params:
        return merged
    blocked = PROTECTED_PARAMS & set(extra_params)
    if blocked:
        raise ValueError(
            f"extra_params may not override protected parameter(s): "
            f"{', '.join(sorted(blocked))}. Use the top-level request field instead."
        )
    merged.update(extra_params)
    return merged


def validate_pipeline_kwargs(pipe: Any, extra_keys: Iterable[str]) -> None:
    """Fail fast — BEFORE a multi-second/minute generation — when extra_params
    contains a kwarg the loaded pipeline's __call__ does not accept.

    Raises ValueError naming every offending parameter. No-op when the
    pipeline signature takes **kwargs (nothing to reject statically).
    """
    keys = list(extra_keys)
    if not keys:
        return
    try:
        sig = inspect.signature(pipe.__call__)
    except (TypeError, ValueError):
        return  # unusual callable — let the pipeline itself raise
    params = sig.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return
    unknown = sorted(set(keys) - set(params))
    if unknown:
        raise ValueError(
            f"{type(pipe).__name__} does not accept parameter(s): "
            f"{', '.join(unknown)}. Remove them from extra_params "
            f"(GET the /params endpoint for this model to see valid knobs)."
        )


# ── effective default parameters per model ───────────────────────────────────

def image_effective_params(model: ImageGenModel) -> dict[str, Any]:
    """Complete effective defaults for an image model.

    ``common`` mirrors the first-class GenerateRequest fields; ``advanced``
    is what _generate_sync would actually pass to pipe() beyond those, plus
    verified-safe diffusers knobs (overridable via extra_params).
    """
    g = model.recommended_guidance
    common: dict[str, Any] = {
        "steps": model.recommended_steps,
        "guidance": g,
        "width": model.default_width,
        "height": model.default_height,
        "negative_prompt": "",
        "seed": None,
    }

    # num_images_per_prompt exists on every diffusers text-to-image pipeline.
    # NOTE: the service returns only images[0]; values > 1 spend compute on
    # images that are not returned — exposed for completeness, not recommended.
    advanced: dict[str, Any] = {"num_images_per_prompt": 1}

    if model.pipeline_type == "qwen-image":
        # Qwen uses true_cfg_scale (not guidance_scale) and always receives a
        # negative_prompt (" " when empty) so CFG engages — see _generate_sync.
        advanced["true_cfg_scale"] = g
    else:
        # ALWAYS passed — 0.0 means "no CFG", not "use the diffusers default".
        advanced["guidance_scale"] = g

    if model.pipeline_type == "flux":
        # FluxPipeline T5 sequence length (diffusers default 512).
        advanced["max_sequence_length"] = 512
    elif model.pipeline_type in ("stable-diffusion", "stable-diffusion-xl"):
        # SD/SDXL DDIM-family knobs with their diffusers defaults.
        advanced["eta"] = 0.0
        advanced["guidance_rescale"] = 0.0
        advanced["clip_skip"] = None

    return {
        "common": common,
        "advanced": advanced,
        "supports_negative_prompt": model.supports_negative_prompt,
    }


def video_effective_params(model: VideoGenModel) -> dict[str, Any]:
    """Complete effective defaults for a video model (see image variant)."""
    g = model.recommended_guidance
    common: dict[str, Any] = {
        "steps": model.recommended_steps,
        "guidance": g,
        "width": model.default_width,
        "height": model.default_height,
        "num_frames": model.default_num_frames,
        "fps": model.default_fps,
        "negative_prompt": "",
        "seed": None,
    }
    advanced: dict[str, Any] = {
        # ALWAYS passed by _run_job (0.0 = "no CFG", never "pipeline default").
        "guidance_scale": g,
        # Present on both WanPipeline and LTXPipeline; service uses frames[0],
        # so values > 1 waste compute — exposed for completeness.
        "num_videos_per_prompt": 1,
    }
    return {
        "common": common,
        "advanced": advanced,
        "supports_negative_prompt": model.supports_negative_prompt,
    }
