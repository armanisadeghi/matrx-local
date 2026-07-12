"""Image generation model catalog.

Curated list of the best open-source image generation models that can run
locally. All models use the diffusers library with a Hugging Face repo ID.

Each entry is self-describing — the router and UI derive all necessary
information from this catalog without hardcoded magic strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PipelineType = Literal[
    "flux2-klein",          # Flux2KleinPipeline  (present since diffusers 0.37.1; fresh installs pin >=0.39)
    "z-image",              # ZImagePipeline
    "qwen-image",           # QwenImagePipeline
    "flux",                 # FluxPipeline        (FLUX.1 family)
    "stable-diffusion-xl",  # StableDiffusionXLPipeline
    "stable-diffusion",     # StableDiffusionPipeline (legacy, no catalog entry)
]


@dataclass(frozen=True)
class ImageGenModel:
    """A single image generation model entry."""

    model_id: str
    """Hugging Face repo ID (e.g. 'black-forest-labs/FLUX.1-schnell')."""

    name: str
    """Human-readable display name."""

    provider: str
    """Organization / company name."""

    pipeline_type: PipelineType
    """The diffusers pipeline class family to use."""

    vram_gb: float
    """Minimum VRAM needed in GB (FP16). Used for compatibility checks."""

    ram_gb: float
    """Minimum system RAM needed in GB."""

    description: str
    """One-sentence description shown in the UI."""

    quality_rating: int
    """0–5 quality rating (same scale as LLM ratings)."""

    speed_rating: int
    """0–5 speed rating. 5 = fastest."""

    recommended_steps: int
    """Default inference steps for this model."""

    recommended_guidance: float
    """Default CFG guidance scale. 0.0 = not applicable (flow models)."""

    supports_negative_prompt: bool
    """Whether the model meaningfully uses negative prompts."""

    model_card_url: str
    """Link to the HuggingFace model card."""

    default_width: int = 1024
    default_height: int = 1024

    download_size_gb: float = 0.0
    """Approximate full-repo download size in GB (shown in the UI before the
    exact byte total is known; the DownloadManager reports exact bytes)."""

    load_variant: str | None = None
    """diffusers weight-variant name (e.g. 'fp16') — when set, ONLY the
    ``*.<variant>.safetensors`` weight files are downloaded (their non-variant
    duplicates are skipped) and ``variant=<value>`` is passed to
    ``from_pretrained`` at load time so loading matches the download. Only set
    this when the repo actually publishes variant files (check the HF file
    tree); requesting a nonexistent variant makes from_pretrained fail."""

    requires_hf_token: bool = False
    """Whether a HF token is needed to download this model."""

    supports_img2img: bool = False
    """Whether the family has an image-to-image pipeline in diffusers (>=0.37,
    verified against 0.39.0, July 2026). When true, POST /image-gen/generate
    and /image-gen/jobs accept ``init_image_b64`` (+ ``strength``); the service
    wraps the loaded pipeline via ``AutoPipelineForImage2Image.from_pipe``
    (component sharing — no re-load, near-zero extra memory)."""

    img2img_strength: bool = True
    """Whether the family's img2img call accepts a ``strength`` knob. False for
    flux2-klein: ``Flux2KleinPipeline`` is its own unified generate+edit
    pipeline (AutoPipelineForImage2Image maps flux2-klein → Flux2KleinPipeline)
    that conditions on reference image(s) with NO strength parameter —
    sending strength for it fails loudly instead of being silently dropped.
    Only meaningful when ``supports_img2img`` is true."""

    lora_family: str = "unknown"
    """LoRA-compatibility family of the base model ("sdxl" | "sd15" | "flux" |
    "flux2" | "qwen" | "z-image"). A LoRA whose detected base_family is known
    and differs from this fails loudly BEFORE any weights load."""

    tags: list[str] = field(default_factory=list)

    # ── custom-model fields (user-registered models; catalog entries keep the
    #    defaults — see app/services/image_gen/custom_models.py) ──────────────
    format: str = "diffusers"
    """"diffusers" (model_index.json + component subfolders, loaded via
    from_pretrained) or "single_file" (one .safetensors checkpoint, loaded via
    <FamilyPipeline>.from_single_file). Catalog models are always diffusers."""

    weight_name: str | None = None
    """single_file only: the checkpoint filename inside the model dir."""

    custom: bool = False
    """True for user-registered models from the custom-model registry
    (~/.matrx/image-models/custom-models.json). Custom models are deletable
    via DELETE /image-gen/custom-models/{model_id}."""

    source: str = "catalog"
    """"catalog" | "hf" | "civitai" — where the model definition came from."""


# ─────────────────────────────────────────────────────────────────────────────
# Catalog
# ─────────────────────────────────────────────────────────────────────────────

IMAGE_GEN_MODELS: list[ImageGenModel] = [
    # ── FLUX.2 klein 4B — DEFAULT. Few-step, fast, Apache 2.0 ─────────────────
    # Verified against the HF model card (July 2026): Flux2KleinPipeline,
    # torch.bfloat16, 4 steps, guidance_scale=1.0, 1024x1024.
    ImageGenModel(
        model_id="black-forest-labs/FLUX.2-klein-4B",
        name="FLUX.2 Klein 4B",
        provider="Black Forest Labs",
        pipeline_type="flux2-klein",
        vram_gb=13.0,
        ram_gb=16.0,
        description="Best default: state-of-the-art quality in 4 steps, unifies generation and editing. Apache 2.0.",
        quality_rating=5,
        speed_rating=5,
        recommended_steps=4,
        recommended_guidance=1.0,
        supports_negative_prompt=False,
        model_card_url="https://huggingface.co/black-forest-labs/FLUX.2-klein-4B",
        default_width=1024,
        default_height=1024,
        # Verified filtered size (HfApi, July 2026): 15.98 GB — transformer
        # 7.75 + text_encoder 8.05 + vae 0.17 (root single-file dup excluded).
        download_size_gb=16.0,
        # img2img: AutoPipelineForImage2Image maps flux2-klein →
        # Flux2KleinPipeline itself (verified, diffusers 0.39.0): the SAME
        # unified pipeline accepts a reference `image` for editing but has NO
        # `strength` parameter — hence img2img_strength=False.
        supports_img2img=True,
        img2img_strength=False,
        lora_family="flux2",
        tags=["default", "fast", "high-quality", "apache-2.0"],
    ),
    # ── Z-Image Turbo — 6B photorealism + text rendering, 8 steps ─────────────
    # Verified: ZImagePipeline, torch.bfloat16, num_inference_steps=9
    # (8 DiT passes), guidance_scale=0.0.
    ImageGenModel(
        model_id="Tongyi-MAI/Z-Image-Turbo",
        name="Z-Image Turbo",
        provider="Tongyi-MAI (Alibaba)",
        pipeline_type="z-image",
        vram_gb=13.0,
        ram_gb=16.0,
        description="6B turbo model with exceptional photorealism and in-image text rendering. 8-step generation. Apache 2.0.",
        quality_rating=5,
        speed_rating=4,
        recommended_steps=9,
        recommended_guidance=0.0,
        supports_negative_prompt=False,
        model_card_url="https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
        default_width=1024,
        default_height=1024,
        # Verified filtered size (HfApi, July 2026): 32.85 GB — the repo ships
        # a single (fp32-shard) weight format, so no variant can shrink it.
        download_size_gb=32.9,
        # ZImageImg2ImgPipeline exists in diffusers 0.37.1 AND 0.39.0
        # (verified) with image + strength.
        supports_img2img=True,
        lora_family="z-image",
        tags=["photorealism", "text-rendering", "apache-2.0"],
    ),
    # ── Qwen-Image — 20B flagship, heavy: gate on 48GB unified / 24GB VRAM ────
    # Verified: QwenImagePipeline (DiffusionPipeline resolves to it),
    # torch.bfloat16, 50 steps, true_cfg_scale=4.0 (needs a negative prompt).
    ImageGenModel(
        model_id="Qwen/Qwen-Image",
        name="Qwen-Image",
        provider="Qwen (Alibaba)",
        pipeline_type="qwen-image",
        vram_gb=24.0,
        ram_gb=48.0,
        description="20B flagship with the best complex-text rendering and precise editing. Heavy — needs 48GB+ unified memory or a 24GB+ GPU. Apache 2.0.",
        quality_rating=5,
        speed_rating=2,
        recommended_steps=50,
        recommended_guidance=4.0,
        supports_negative_prompt=True,
        model_card_url="https://huggingface.co/Qwen/Qwen-Image",
        default_width=1024,
        default_height=1024,
        # Verified filtered size (HfApi, July 2026): 57.70 GB — single weight
        # format (no duplicates to filter beyond docs).
        download_size_gb=57.8,
        # QwenImageImg2ImgPipeline exists in diffusers 0.37.1 AND 0.39.0
        # (verified) with image + strength + true_cfg_scale.
        supports_img2img=True,
        lora_family="qwen",
        tags=["flagship", "text-rendering", "heavy", "apache-2.0"],
    ),
    # ── FLUX.1-schnell — legacy fast option, kept for compatibility ───────────
    ImageGenModel(
        model_id="black-forest-labs/FLUX.1-schnell",
        name="FLUX.1 Schnell",
        provider="Black Forest Labs",
        pipeline_type="flux",
        vram_gb=8.0,
        ram_gb=16.0,
        description="Previous-generation fast model. 4-step generation. Apache 2.0.",
        quality_rating=4,
        speed_rating=5,
        recommended_steps=4,
        recommended_guidance=0.0,
        supports_negative_prompt=False,
        model_card_url="https://huggingface.co/black-forest-labs/FLUX.1-schnell",
        default_width=1024,
        default_height=1024,
        # Verified filtered size (HfApi, July 2026): 33.73 GB — root
        # flux1-schnell.safetensors + ae.safetensors dups (24 GB) excluded.
        download_size_gb=33.8,
        # FluxImg2ImgPipeline (verified 0.37.1 + 0.39.0): image + strength.
        supports_img2img=True,
        lora_family="flux",
        # Apache 2.0 BUT the HF repo is gated ("gated": "auto", verified against
        # the Hub July 2026): downloads 401 unless the account has a token AND
        # has accepted the repo terms. The only gated repo in this catalog —
        # audited against huggingface.co/api/models/* — and missing this flag is
        # what let a 33 GB download start and die on an unauthenticated 401.
        requires_hf_token=True,
        tags=["fast", "legacy", "apache-2.0"],
    ),
    # ── SDXL-Turbo — instant previews on modest hardware ──────────────────────
    ImageGenModel(
        model_id="stabilityai/sdxl-turbo",
        name="SDXL Turbo",
        provider="Stability AI",
        pipeline_type="stable-diffusion-xl",
        vram_gb=6.0,
        ram_gb=10.0,
        description="1-step generation for instant image previews. Excellent for fast iteration.",
        quality_rating=2,
        speed_rating=5,
        recommended_steps=1,
        recommended_guidance=0.0,
        supports_negative_prompt=False,
        model_card_url="https://huggingface.co/stabilityai/sdxl-turbo",
        default_width=512,
        default_height=512,
        # Verified filtered size (HfApi, July 2026): 6.94 GB with the fp16
        # variant filter (raw repo is 55.5 GB of fp32+fp16+onnx duplicates).
        download_size_gb=7.0,
        # The repo ships fp32 AND fp16 weights (plus onnx exports and a
        # single-file checkpoint). fp16 halves the download and matches the
        # bf16/fp16 dtype we load with anyway.
        load_variant="fp16",
        # StableDiffusionXLImg2ImgPipeline (verified 0.37.1 + 0.39.0). NOTE:
        # SD/SDXL img2img take no width/height — the service pre-resizes the
        # init image to the requested dims (aspect-fill + center-crop), and
        # img2img runs ~steps*strength denoising steps, so steps*strength must
        # be >= 1 (the service enforces this loudly; for this 1-step model
        # use steps=2 with the default strength 0.6).
        supports_img2img=True,
        lora_family="sdxl",
        tags=["fast", "preview", "1-step"],
    ),
]

DEFAULT_IMAGE_MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"

# ── Workflow presets ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkflowPreset:
    """A preconfigured generation workflow with a fixed prompt template."""

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
    tags: list[str] = field(default_factory=list)


WORKFLOW_PRESETS: list[WorkflowPreset] = [
    WorkflowPreset(
        preset_id="photorealistic-portrait",
        name="Photorealistic Portrait",
        description="Professional headshot or portrait photo quality",
        prompt_template=(
            "professional portrait photo of {subject}, sharp focus, studio lighting, "
            "8k uhd, high detail, bokeh background"
        ),
        negative_prompt="cartoon, illustration, painting, blurry, low quality, deformed",
        suggested_model_id="black-forest-labs/FLUX.2-klein-4B",
        steps=4,
        guidance=1.0,
        width=1024,
        height=1024,
        tags=["portrait", "photo"],
    ),
    WorkflowPreset(
        preset_id="product-shot",
        name="Product Photography",
        description="Clean product shot on white or studio background",
        prompt_template=(
            "product photography of {subject}, clean white background, "
            "professional lighting, sharp details, commercial photo"
        ),
        negative_prompt="cluttered, dark, blurry, low quality",
        suggested_model_id="black-forest-labs/FLUX.2-klein-4B",
        steps=4,
        guidance=1.0,
        width=1024,
        height=1024,
        tags=["product", "commercial"],
    ),
    WorkflowPreset(
        preset_id="concept-art",
        name="Concept Art / Illustration",
        description="Digital art and concept illustration style",
        prompt_template=(
            "concept art of {subject}, digital painting, detailed, vibrant colors, "
            "trending on artstation, professional illustration"
        ),
        negative_prompt="photo, realistic, blurry, low quality",
        suggested_model_id="black-forest-labs/FLUX.2-klein-4B",
        steps=4,
        guidance=1.0,
        width=1024,
        height=1024,
        tags=["art", "illustration"],
    ),
    WorkflowPreset(
        preset_id="ui-mockup",
        name="UI / App Mockup",
        description="Clean app interface or website mockup screenshot",
        prompt_template=(
            "clean modern {subject} UI design, flat design, minimal, "
            "professional app interface, light theme, high resolution screenshot"
        ),
        negative_prompt="cluttered, low quality, dark, outdated",
        suggested_model_id="black-forest-labs/FLUX.2-klein-4B",
        steps=4,
        guidance=1.0,
        width=1280,
        height=960,
        tags=["ui", "design"],
    ),
    WorkflowPreset(
        preset_id="logo-icon",
        name="Logo / Icon",
        description="Simple icon or logo on transparent-style background",
        prompt_template=(
            "minimalist logo for {subject}, vector style, clean lines, "
            "simple icon, white background, professional brand identity"
        ),
        negative_prompt="complex, cluttered, photo, realistic, dark background",
        suggested_model_id="black-forest-labs/FLUX.2-klein-4B",
        steps=4,
        guidance=1.0,
        width=1024,
        height=1024,
        tags=["logo", "icon", "branding"],
    ),
    WorkflowPreset(
        preset_id="landscape",
        name="Landscape / Scene",
        description="Wide-format scenic or environmental image",
        prompt_template=(
            "{subject}, wide angle landscape photography, golden hour lighting, "
            "high dynamic range, ultra detailed, 8k"
        ),
        negative_prompt="people, text, watermark, low quality",
        suggested_model_id="black-forest-labs/FLUX.2-klein-4B",
        steps=4,
        guidance=1.0,
        width=1344,
        height=768,
        tags=["landscape", "scenic"],
    ),
]
