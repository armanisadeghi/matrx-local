"""LoRA store + curated catalog for image generation.

Layout (see app/services/media_gen/paths.py):

    ~/.matrx/image-models/loras/<sanitized-repo-id>/
        <weight_name>.safetensors     — the LoRA weights
        lora.json                     — {repo_id, weight_name, base_family,
                                         size_bytes, added_at}
        .download-complete            — written by the DownloadManager; a LoRA
                                        is "installed" ONLY when this exists

Downloads MUST route through the universal DownloadManager (category
"image_gen_lora", ``metadata.hf_allow_files`` filters the snapshot to exactly
the weight file) — silent in-request downloads are this project's original
sin and are never reintroduced here.

Family compatibility: every installed LoRA carries a ``base_family`` guess
("sdxl" | "sd15" | "flux" | "unknown"), derived from the repo's declared
``base_model`` when available and from repo-id/filename heuristics otherwise.
A KNOWN family that differs from the target model's ``lora_family`` fails
loudly before any weights load; "unknown" is attempted and any diffusers
error is surfaced cleanly.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.services.media_gen.paths import (
    DOWNLOAD_COMPLETE_MARKER,
    image_loras_dir,
    sanitize_model_id,
)

logger = get_logger()

# Dir names are sanitized repo ids: org--name (plus dots/dashes/underscores).
# Validated before ANY filesystem lookup so a crafted id can never traverse
# outside the loras dir.
_LORA_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_valid_lora_id(lora_id: str) -> bool:
    return bool(_LORA_ID_RE.match(lora_id)) and ".." not in lora_id


def lora_id_for_repo(repo_id: str) -> str:
    return sanitize_model_id(repo_id)


def lora_dir(lora_id: str) -> Path:
    return image_loras_dir() / lora_id


# ── base-family detection ─────────────────────────────────────────────────────

def guess_base_family(
    repo_id: str,
    weight_name: str | None = None,
    declared_base_model: str | None = None,
) -> str:
    """Best-effort LoRA base-family guess: "sdxl" | "sd15" | "flux" | "unknown".

    Precedence: the repo's declared ``base_model`` (HF cardData — reliable),
    then repo-id/filename token heuristics. Honest by design — anything
    ambiguous is "unknown" (attempted at load; diffusers' own error surfaces).
    """
    haystacks: list[str] = []
    if declared_base_model:
        haystacks.append(declared_base_model.lower())
    haystacks.append(repo_id.lower())
    if weight_name:
        haystacks.append(weight_name.lower())

    for text in haystacks:
        if "flux" in text:
            return "flux"
        if "xl" in text and ("sd" in text or "stable-diffusion" in text):
            return "sdxl"
        if "sdxl" in text:
            return "sdxl"
        if any(t in text for t in ("sd15", "sd-1-5", "sd1.5", "sd_1_5",
                                   "stable-diffusion-v1-5",
                                   "stable-diffusion-1-5", "sdv1-5")):
            return "sd15"
    return "unknown"


def check_lora_model_compat(model_lora_family: str, lora_meta: dict[str, Any]) -> None:
    """Raise ValueError naming the mismatch when a KNOWN LoRA family differs
    from the model's family. "unknown" always passes (attempt + surface)."""
    fam = str(lora_meta.get("base_family") or "unknown")
    if fam != "unknown" and fam != model_lora_family:
        raise ValueError(
            f"LoRA '{lora_meta.get('id') or lora_meta.get('repo_id')}' is a "
            f"{fam} LoRA but the selected model is a {model_lora_family} "
            f"model — LoRA weights are architecture-specific and cannot be "
            f"applied across families. Pick a {model_lora_family} LoRA or "
            f"switch models."
        )


# ── metadata sidecar ──────────────────────────────────────────────────────────

def write_lora_meta(
    lora_id: str,
    *,
    repo_id: str,
    weight_name: str,
    base_family: str,
) -> None:
    d = lora_dir(lora_id)
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "repo_id": repo_id,
        "weight_name": weight_name,
        "base_family": base_family,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    (d / "lora.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def get_installed_lora(lora_id: str) -> dict[str, Any] | None:
    """Metadata for one INSTALLED LoRA (lora.json + completion marker + weight
    file present), or None. The returned dict adds id / dir / size_bytes /
    installed."""
    if not is_valid_lora_id(lora_id):
        return None
    d = lora_dir(lora_id)
    meta_path = d / "lora.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or "repo_id" not in meta or "weight_name" not in meta:
            raise ValueError("lora.json missing required fields")
    except Exception as exc:  # noqa: BLE001 — skip-and-scream, never crash a listing
        logger.error("[image_gen] Corrupt lora.json in %s: %s", d, exc)
        return None
    weight = d / str(meta["weight_name"])
    installed = (d / DOWNLOAD_COMPLETE_MARKER).exists() and weight.exists()
    meta["id"] = lora_id
    meta["dir"] = str(d)
    meta["size_bytes"] = weight.stat().st_size if weight.exists() else 0
    meta["installed"] = installed
    return meta


def list_loras(*, include_pending: bool = True) -> list[dict[str, Any]]:
    """All LoRAs with a lora.json under the store dir, newest first.

    ``include_pending=False`` filters to fully-installed ones (marker+weight).
    """
    base = image_loras_dir()
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        meta = get_installed_lora(d.name)
        if meta is None:
            continue
        if not include_pending and not meta["installed"]:
            continue
        out.append(meta)
    out.sort(key=lambda m: str(m.get("added_at", "")), reverse=True)
    return out


def delete_lora(lora_id: str) -> bool:
    """Delete a LoRA dir (weights + metadata). False when unknown/invalid."""
    if not is_valid_lora_id(lora_id):
        return False
    d = lora_dir(lora_id)
    if not d.is_dir():
        return False
    shutil.rmtree(d)
    logger.info("[image_gen] Deleted LoRA %s", lora_id)
    return True


# ── curated catalog ───────────────────────────────────────────────────────────
# Every entry below was verified to exist on the HF Hub via the live API on
# 2026-07-10 (repo present, exact weight filename listed in siblings,
# license/base_model read from cardData) — hence unverified=False throughout.
# If you add an entry you could NOT verify against live HF metadata, set
# unverified=True instead of guessing.

CURATED_LORA_CATALOG: list[dict[str, Any]] = [
    {
        "repo_id": "latent-consistency/lcm-lora-sdxl",
        "name": "LCM LoRA (SDXL)",
        "description": (
            "Latent Consistency Model acceleration LoRA — 4-8 step SDXL "
            "generations with guidance 1.0-2.0."
        ),
        "weight_name": "pytorch_lora_weights.safetensors",
        "base_family": "sdxl",
        "license": "openrail++",
        "unverified": False,
    },
    {
        "repo_id": "nerijs/pixel-art-xl",
        "name": "Pixel Art XL",
        "description": "Crisp pixel-art style for SDXL. Trigger phrase: 'pixel art'.",
        "weight_name": "pixel-art-xl.safetensors",
        "base_family": "sdxl",
        "license": "creativeml-openrail-m",
        "unverified": False,
    },
    {
        "repo_id": "CiroN2022/toy-face",
        "name": "Toy Face (SDXL)",
        "description": "Toy/figurine face style for SDXL. Trigger phrase: 'toy_face'.",
        "weight_name": "toy_face_sdxl.safetensors",
        "base_family": "sdxl",
        "license": "other (see model card)",
        "unverified": False,
    },
    {
        "repo_id": "XLabs-AI/flux-RealismLora",
        "name": "FLUX Realism LoRA",
        "description": (
            "Photorealism enhancement LoRA trained on FLUX.1-dev (FLUX.1 "
            "family — inherits the FLUX.1-dev non-commercial license)."
        ),
        "weight_name": "lora.safetensors",
        "base_family": "flux",
        "license": "flux-1-dev-non-commercial-license",
        "unverified": False,
    },
    {
        "repo_id": "alimama-creative/FLUX.1-Turbo-Alpha",
        "name": "FLUX.1 Turbo Alpha",
        "description": (
            "8-step distillation/acceleration LoRA trained on FLUX.1-dev "
            "(FLUX.1 family — inherits the FLUX.1-dev non-commercial license)."
        ),
        "weight_name": "diffusion_pytorch_model.safetensors",
        "base_family": "flux",
        "license": "flux-1-dev-non-commercial-license",
        "unverified": False,
    },
]
