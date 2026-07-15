"""Video generation model catalog.

Curated diffusers-format text-to-video / image-to-video models that can run
locally. Pipeline classes verified against the installed diffusers package and
the HF model cards (July 2026):

- ``wan``  → WanPipeline (T2V) / WanImageToVideoPipeline (I2V), with
             AutoencoderKLWan loaded fp32 per the Wan model cards.
- ``ltx``  → LTXPipeline (T2V) / LTXImageToVideoPipeline (I2V).

Hardware gating (spec hard rule): video generation as a whole requires Apple
Silicon with >= 16 GB unified memory OR a CUDA GPU with >= 8 GB VRAM — enforced
in the service via app.services.media_gen.hardware. Per-model minimums below
gate individual models on top of that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


VideoPipelineType = Literal["wan", "ltx"]


@dataclass(frozen=True)
class VideoGenModel:
    """A single video generation model entry."""

    model_id: str
    """Hugging Face repo ID (diffusers format)."""

    name: str
    provider: str
    pipeline_type: VideoPipelineType

    vram_gb: float
    """Minimum VRAM (CUDA) in GB."""

    ram_gb: float
    """Minimum system RAM / unified memory in GB."""

    description: str
    quality_rating: int
    speed_rating: int

    recommended_steps: int
    recommended_guidance: float

    default_width: int
    default_height: int
    default_num_frames: int
    default_fps: int

    supports_image_to_video: bool
    """Whether the model accepts a source image (I2V)."""

    supports_negative_prompt: bool
    model_card_url: str
    license_name: str

    max_num_frames: int
    """Maximum num_frames the model supports (per the model card)."""

    download_size_gb: float = 0.0
    """Approximate FILTERED download size in GB (what the DownloadManager's HF
    file filter actually fetches — not the raw repo size)."""

    load_variant: str | None = None
    """diffusers weight-variant name (e.g. 'fp16') — when set, only variant
    weight files are downloaded and ``variant=<value>`` is passed to
    ``from_pretrained``. None for all current video models (their repos publish
    a single weight format)."""

    requires_hf_token: bool = False
    tags: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Catalog — COMPILED FALLBACK DATA
#
# The live catalog is the remote `catalog_entries` table (kind
# `video_gen_model`) served through app/services/catalogs — read it via the
# get_*() accessors below, NEVER by importing this list directly. It stays
# only as the explicit defaults tier (first boot / total network failure).
# ─────────────────────────────────────────────────────────────────────────────

VIDEO_GEN_MODELS: list[VideoGenModel] = [
    # ── Wan 2.2 TI2V-5B — DEFAULT. Text+Image→Video in one model ─────────────
    # Verified: WanPipeline + AutoencoderKLWan (vae fp32, pipeline bf16),
    # 720P (1280x704), num_frames=121, steps=50, guidance 5.0, fps 24.
    VideoGenModel(
        model_id="Wan-AI/Wan2.2-TI2V-5B-Diffusers",
        name="Wan 2.2 TI2V 5B",
        provider="Wan-AI (Alibaba)",
        pipeline_type="wan",
        vram_gb=12.0,
        ram_gb=32.0,
        description="Best default: 5B unified text-to-video AND image-to-video at 720P/24fps. Apache 2.0.",
        quality_rating=5,
        speed_rating=3,
        recommended_steps=50,
        recommended_guidance=5.0,
        default_width=1280,
        default_height=704,
        default_num_frames=121,
        default_fps=24,
        supports_image_to_video=True,
        supports_negative_prompt=True,
        model_card_url="https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers",
        license_name="Apache 2.0",
        max_num_frames=121,
        # Verified filtered size (HfApi, July 2026): 34.20 GB — single weight
        # format; only docs/assets are filtered.
        download_size_gb=34.3,
        tags=["default", "t2v", "i2v", "720p", "apache-2.0"],
    ),
    # ── Wan 2.1 T2V 1.3B — light option for smaller machines ─────────────────
    # Verified: WanPipeline + AutoencoderKLWan, 480P (832x480),
    # num_frames=81, guidance 5.0, fps 15.
    VideoGenModel(
        model_id="Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        name="Wan 2.1 T2V 1.3B",
        provider="Wan-AI (Alibaba)",
        pipeline_type="wan",
        vram_gb=8.0,
        ram_gb=16.0,
        description="Light 1.3B text-to-video at 480P — runs on 8GB GPUs and 16GB Macs. Apache 2.0.",
        quality_rating=3,
        speed_rating=4,
        recommended_steps=30,
        recommended_guidance=5.0,
        default_width=832,
        default_height=480,
        default_num_frames=81,
        default_fps=15,
        supports_image_to_video=False,
        supports_negative_prompt=True,
        model_card_url="https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
        license_name="Apache 2.0",
        max_num_frames=81,
        # Verified filtered size (HfApi, July 2026): 28.93 GB — the umt5-xxl
        # text encoder alone is ~22.7 GB; single weight format.
        download_size_gb=29.0,
        tags=["light", "t2v", "480p", "apache-2.0"],
    ),
    # ── LTX-Video — fastest generation, DiT distilled ─────────────────────────
    # Verified: LTXPipeline / LTXImageToVideoPipeline, bf16, dims divisible
    # by 32, num_frames divisible by 8 + 1, fps 24.
    VideoGenModel(
        model_id="Lightricks/LTX-Video",
        name="LTX-Video",
        provider="Lightricks",
        pipeline_type="ltx",
        vram_gb=8.0,
        ram_gb=16.0,
        description="Fastest local video model — real-time-class DiT. LTX Open Weights license (free under $10M revenue).",
        quality_rating=3,
        speed_rating=5,
        recommended_steps=30,
        recommended_guidance=3.0,
        default_width=704,
        default_height=480,
        default_num_frames=121,
        default_fps=24,
        supports_image_to_video=True,
        supports_negative_prompt=True,
        model_card_url="https://huggingface.co/Lightricks/LTX-Video",
        license_name="LTX Open Weights",
        max_num_frames=257,
        # Verified filtered size (HfApi, July 2026): 28.42 GB — the repo is
        # diffusers-format at root (model_index.json + transformer/text_encoder/
        # vae/tokenizer/scheduler subfolders, exactly what LTXPipeline loads);
        # the ~225 GB of root ltxv-*.safetensors checkpoint re-packagings are
        # excluded by the download filter's root-safetensors rule.
        download_size_gb=28.5,
        tags=["fastest", "t2v", "i2v", "ltx"],
    ),
]

DEFAULT_VIDEO_MODEL_ID = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"


# ─────────────────────────────────────────────────────────────────────────────
# Remote-catalog accessors — the canonical read path
# ─────────────────────────────────────────────────────────────────────────────


def get_video_gen_models() -> list[VideoGenModel]:
    """Resolved video-gen model catalog (remote > cache > compiled fallback)."""
    from app.services.catalogs import get_catalog  # noqa: PLC0415 — lazy: avoids import cycle
    from app.services.catalogs.adapt import entries_to_dataclasses  # noqa: PLC0415

    return entries_to_dataclasses(get_catalog("video_gen_model"), VideoGenModel)


def get_video_gen_model(model_id: str) -> VideoGenModel | None:
    """Single resolved catalog model by HF repo id."""
    return next((m for m in get_video_gen_models() if m.model_id == model_id), None)


def get_default_video_model_id() -> str:
    """The catalog's flagged default model id (payload.is_default)."""
    from app.common.system_logger import get_logger  # noqa: PLC0415
    from app.services.catalogs import get_catalog  # noqa: PLC0415

    for entry in get_catalog("video_gen_model"):
        if entry.payload.get("is_default"):
            return str(entry.payload.get("model_id") or entry.key)
    get_logger().warning(
        "[video_gen] no catalog entry flagged is_default — falling back to the "
        "compiled DEFAULT_VIDEO_MODEL_ID (%s)",
        DEFAULT_VIDEO_MODEL_ID,
    )
    return DEFAULT_VIDEO_MODEL_ID
