"""LoRA store + curated catalog for image generation.

Layout (see app/services/media_gen/paths.py):

    ~/.matrx/image-models/loras/<sanitized-repo-id>/
        <weight_name>.safetensors     — the LoRA weights
        lora.json                     — {repo_id, weight_name, base_family,
                                         name?, source, added_at, ...}
        .download-complete            — written by the DownloadManager; a LoRA
                                        is "installed" ONLY when this exists

Downloads MUST route through the universal DownloadManager (category
"image_gen_lora", ``metadata.hf_allow_files`` filters the snapshot to exactly
the weight file) — silent in-request downloads are this project's original
sin and are never reintroduced here.

Family compatibility: every installed LoRA carries a ``base_family`` guess
("sdxl" | "sd15" | "flux" | "z-image" | "unknown"), derived from the repo's
declared ``base_model`` when available and from repo-id/filename heuristics
otherwise. A KNOWN family that differs from the target model's ``lora_family``
fails loudly before any weights load; "unknown" is attempted and any
diffusers error is surfaced cleanly.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    """Best-effort LoRA base-family guess:
    "sdxl" | "sd15" | "flux" | "flux2" | "z-image" | "unknown".

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
        if any(token in text for token in ("flux.2", "flux2", "flux-2", "flux_2")):
            return "flux2"
        if "flux" in text:
            return "flux"
        # Civitai camel form ("ZImageTurbo") and dashed ("z-image-turbo")
        if "z-image" in text or "zimage" in text or "z_image" in text:
            return "z-image"
        if "xl" in text and ("sd" in text or "stable-diffusion" in text):
            return "sdxl"
        if "sdxl" in text:
            return "sdxl"
        if any(
            t in text
            for t in (
                "sd15",
                "sd-1-5",
                "sd1.5",
                "sd_1_5",
                "stable-diffusion-v1-5",
                "stable-diffusion-1-5",
                "sdv1-5",
            )
        ):
            return "sd15"
    return "unknown"


def resolve_lora_display_name(
    meta: dict[str, Any],
    *,
    catalog_by_repo: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """Human label for an installed LoRA.

    Precedence: ``name`` in the on-disk sidecar (Civitai downloads write the
    model page title here), then a matching curated-catalog entry by
    ``repo_id``, else None (the UI falls back to ``id``).
    """
    raw = meta.get("name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    repo = meta.get("repo_id")
    if catalog_by_repo and isinstance(repo, str):
        cat = catalog_by_repo.get(repo) or {}
        cat_name = cat.get("name")
        if isinstance(cat_name, str) and cat_name.strip():
            return cat_name.strip()
    return None


def backfill_lora_family_from_catalog(
    meta: dict[str, Any],
    *,
    catalog_by_repo: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Repair an installed LoRA whose sidecar predates reliable family data.

    Version-pinned catalog entries are authoritative for their exact artifact.
    When an older Civitai install was recorded as ``unknown`` because its
    ``baseModel`` label carried an unmapped size suffix, persist the catalog's
    known family atomically.  Known sidecar families are never overwritten:
    disagreement there is evidence to investigate, not something to hide.
    """
    current = str(meta.get("base_family") or "unknown")
    repo_id = meta.get("repo_id")
    if current != "unknown" or not isinstance(repo_id, str):
        return meta
    catalog_entry = catalog_by_repo.get(repo_id)
    catalog_family = (
        str(catalog_entry.get("base_family") or "unknown")
        if catalog_entry
        else "unknown"
    )
    if catalog_family == "unknown":
        return meta

    lora_id = str(meta.get("id") or "")
    if not is_valid_lora_id(lora_id):
        return meta
    meta_path = lora_dir(lora_id) / "lora.json"
    tmp: Path | None = None
    try:
        persisted = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(persisted, dict):
            raise ValueError("lora.json root is not an object")
        persisted["base_family"] = catalog_family
        tmp = meta_path.with_name(f"{meta_path.name}.{uuid4().hex}.tmp")
        tmp.write_text(json.dumps(persisted, indent=2), encoding="utf-8")
        tmp.replace(meta_path)
    except Exception as exc:  # noqa: BLE001 — listing must remain fault-isolated
        logger.error(
            "[image_gen] Could not backfill LoRA family for %s from catalog: %s",
            lora_id,
            exc,
        )
        return meta
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    meta["base_family"] = catalog_family
    logger.info(
        "[image_gen] Backfilled LoRA %s family from catalog: %s",
        lora_id,
        catalog_family,
    )
    return meta


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
    source: str = "hf",
    extra: dict[str, Any] | None = None,
) -> None:
    """``source`` is "hf" (HF repo download) or "civitai" (direct download);
    for Civitai LoRAs ``repo_id`` carries the canonical
    ``civitai:<modelId>@<versionId>`` ref and ``extra`` records the numeric
    ids (civitai_model_id / civitai_version_id)."""
    d = lora_dir(lora_id)
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "repo_id": repo_id,
        "weight_name": weight_name,
        "base_family": base_family,
        "source": source,
        "added_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
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
        if (
            not isinstance(meta, dict)
            or "repo_id" not in meta
            or "weight_name" not in meta
        ):
            raise ValueError("lora.json missing required fields")
    except Exception as exc:  # noqa: BLE001 — skip-and-scream, never crash a listing
        logger.error("[image_gen] Corrupt lora.json in %s: %s", d, exc)
        return None
    weight = d / str(meta["weight_name"])
    installed = (d / DOWNLOAD_COMPLETE_MARKER).exists() and weight.exists()
    meta["id"] = lora_id
    meta["dir"] = str(d)
    meta.setdefault("source", "hf")  # pre-Civitai installs predate the field
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


# ── curated catalog — COMPILED FALLBACK DATA ─────────────────────────────────
# The live catalog is the remote `catalog_entries` table (kind `lora`) served
# through app/services/catalogs — read it via get_curated_lora_catalog()
# below, NEVER by importing this list directly. It stays only as the explicit
# defaults tier (first boot / total network failure).
# HF entries: verified against the Hub API (repo present, exact weight
# filename in siblings, license/base_model from cardData).
# Civitai entries: verified against GET /api/v1/models/<id> (type=LORA,
# baseModel=ZImageTurbo, primary .safetensors file). ``repo_id`` is the
# canonical short ref ``civitai:<modelId>@<versionId>`` — the version pin is
# REQUIRED because multi-base Civitai pages resolve to the newest version
# (often a different family) when modelVersionId is omitted.
# Set unverified=True only when you could NOT verify against live metadata.

CURATED_LORA_CATALOG: list[dict[str, Any]] = [
    # ── Z-Image Turbo (most-used catalog model) — Civitai, verified 2026-07-11
    {
        "repo_id": "civitai:2268008@2617751",
        "name": "Realistic Snapshot (ZIT v5)",
        "description": (
            "Organic photorealism for Z-Image Turbo — skin pores, sensor "
            "noise, candid lighting. Strength ~0.6–0.7. Civitai baseModel "
            "ZImageTurbo."
        ),
        "weight_name": "RealisticSnapshot-Zimage-Turbov5.safetensors",
        "base_family": "z-image",
        "license": "civitai (see model page)",
        "source": "civitai",
        "unverified": False,
    },
    {
        "repo_id": "civitai:2395852@2812128",
        "name": "Radiant Realism Pro (ZIT v2)",
        "description": (
            "Beauty / skin-texture / natural skin-tone LoRA for Z-Image "
            "Turbo portraits. Steps 8–10, CFG 1."
        ),
        "weight_name": "Z-Image Turbo Radiant v2.0.safetensors",
        "base_family": "z-image",
        "license": "civitai (see model page)",
        "source": "civitai",
        "unverified": False,
    },
    {
        "repo_id": "civitai:2283998@2570552",
        "name": "ZepiCRealism (Turbo)",
        "description": (
            "Natural detail realism LoRA for Z-Image Turbo. Strength 1.0 "
            "(or 0.5–1.0); reduces the default Asian prior toward a more "
            "neutral look."
        ),
        "weight_name": "ZepiCRealism-Turbo.safetensors",
        "base_family": "z-image",
        "license": "civitai (see model page)",
        "source": "civitai",
        "unverified": False,
    },
    {
        "repo_id": "civitai:580857@2674760",
        "name": "Realistic Skin Texture (ZTurbo v4.5)",
        "description": (
            "Detailed photorealistic skin-texture style for Z-Image Turbo. "
            "Multi-base page — this pin is the ZImageTurbo v4.5 version."
        ),
        "weight_name": "skin texture Photorealistic style v4.5.safetensors",
        "base_family": "z-image",
        "license": "civitai (see model page)",
        "source": "civitai",
        "unverified": False,
    },
    {
        "repo_id": "civitai:1379962@2457938",
        "name": "Amateur Instagramification (Z-turbo)",
        "description": (
            "Amateur / Instagram candid aesthetic for Z-Image Turbo. "
            "Multi-base page — this pin is the ZImageTurbo version."
        ),
        "weight_name": "zimage-igbaddie.safetensors",
        "base_family": "z-image",
        "license": "civitai (see model page)",
        "source": "civitai",
        "unverified": False,
    },
    {
        "repo_id": "civitai:2234266@2515203",
        "name": "[ZIT] Detail Slider",
        "description": ("Detail-enhancement slider LoRA trained for Z-Image Turbo."),
        "weight_name": "Z-Detail-Slider.safetensors",
        "base_family": "z-image",
        "license": "civitai (see model page)",
        "source": "civitai",
        "unverified": False,
    },
    {
        "repo_id": "civitai:432586@2454927",
        "name": "Cinematic Shot (ZIT)",
        "description": "Cinematic framing / lighting style for Z-Image Turbo.",
        "weight_name": "zy_CinematicShot_zit.safetensors",
        "base_family": "z-image",
        "license": "civitai (see model page)",
        "source": "civitai",
        "unverified": False,
    },
    {
        "repo_id": "civitai:689192@2558476",
        "name": "Aesthetic Amateur Photo (ZIT)",
        "description": ("Amateur-photo aesthetic LoRA for Z-Image Turbo."),
        "weight_name": "aesthetic_exp1.safetensors",
        "base_family": "z-image",
        "license": "civitai (see model page)",
        "source": "civitai",
        "unverified": False,
    },
    # ── SDXL / FLUX (HF Hub, verified 2026-07-10) ────────────────────────────
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
        "source": "hf",
        "unverified": False,
    },
    {
        "repo_id": "nerijs/pixel-art-xl",
        "name": "Pixel Art XL",
        "description": "Crisp pixel-art style for SDXL. Trigger phrase: 'pixel art'.",
        "weight_name": "pixel-art-xl.safetensors",
        "base_family": "sdxl",
        "license": "creativeml-openrail-m",
        "source": "hf",
        "unverified": False,
    },
    {
        "repo_id": "CiroN2022/toy-face",
        "name": "Toy Face (SDXL)",
        "description": "Toy/figurine face style for SDXL. Trigger phrase: 'toy_face'.",
        "weight_name": "toy_face_sdxl.safetensors",
        "base_family": "sdxl",
        "license": "other (see model card)",
        "source": "hf",
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
        "source": "hf",
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
        "source": "hf",
        "unverified": False,
    },
]

# Required keys on every catalog entry (must match LoraCatalogInfo's non-default
# fields in app/api/image_gen_routes.py). A hand-added entry that omits one used
# to raise KeyError inside the /image-gen/loras response builder and 500 the
# whole endpoint — blanking installed styles too. The endpoint now skips bad
# entries, and this import-time check makes an authoring mistake loud at startup
# instead of silent until a user opens "Get more".
_REQUIRED_CATALOG_KEYS = (
    "repo_id",
    "name",
    "description",
    "weight_name",
    "base_family",
    "license",
)


def validate_catalog() -> list[str]:
    """Return a list of problems in CURATED_LORA_CATALOG (empty when clean).

    Never raises — a malformed curated entry must degrade to "that one style is
    missing", never crash image-gen or the engine.
    """
    problems: list[str] = []
    seen_repo_ids: set[str] = set()
    for i, e in enumerate(CURATED_LORA_CATALOG):
        # isinstance FIRST — a non-dict entry (a bare string / tuple / stray
        # None from an editing slip) is the exact authoring mistake this guard
        # exists to catch, and calling .get() on it would raise AttributeError
        # straight out of module import and take down the whole image-gen
        # surface. Never touch the entry before proving it's a dict.
        if not isinstance(e, dict):
            problems.append(f"entry {i} is not a dict")
            continue
        label = e.get("repo_id") or e.get("name") or f"index {i}"
        missing = [k for k in _REQUIRED_CATALOG_KEYS if not e.get(k)]
        if missing:
            problems.append(f"'{label}' missing required key(s): {', '.join(missing)}")
        rid = e.get("repo_id")
        if rid:
            if rid in seen_repo_ids:
                problems.append(f"duplicate repo_id '{rid}'")
            seen_repo_ids.add(rid)
    return problems


try:
    _catalog_problems = validate_catalog()
except Exception as _exc:  # noqa: BLE001 — a validator that crashes import is worse than a bad entry
    logger.error("[image_gen] validate_catalog() crashed at import: %s", _exc)
    _catalog_problems = []
if _catalog_problems:
    logger.error(
        "[image_gen] CURATED_LORA_CATALOG has %d malformed entr%s — they will be "
        "SKIPPED in GET /image-gen/loras (the panel stays up): %s",
        len(_catalog_problems),
        "y" if len(_catalog_problems) == 1 else "ies",
        "; ".join(_catalog_problems),
    )


def get_curated_lora_catalog() -> list[dict[str, Any]]:
    """Resolved curated LoRA catalog (remote > cache > compiled fallback).

    Entries are plain dicts (payload mirrors of the legacy
    ``CURATED_LORA_CATALOG`` items) — the compiled list above stays only as
    the defaults tier via app/services/catalogs/compiled.py.
    """
    from app.services.catalogs import get_catalog  # noqa: PLC0415 — lazy: avoids import cycle

    return [dict(e.payload) for e in get_catalog("lora")]
