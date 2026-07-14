"""Custom image-model registry — user-added checkpoints from HF and Civitai.

Users can register ANY diffusers-format repo or single-file checkpoint from
Hugging Face or Civitai as a first-class image model. The flow is always:

  1. POST /image-gen/custom-models/inspect {ref}
       ``ref`` is an HF repo id, an HF URL, or a Civitai model/version URL/id.
       Resolves metadata WITHOUT downloading weights (HF API file listing +
       model_index.json; Civitai public REST API) and returns a proposed
       registry entry + warnings + a registerable verdict.
  2. POST /image-gen/custom-models {entry}
       Validates the (inspect-confirmed) entry server-side, persists it to the
       registry, and queues the weights through the universal DownloadManager
       — never an inline silent download.
  3. The entry merges into GET /image-gen/models with ``custom: true`` and is
       generated/loaded through the exact same service paths as catalog
       models (ImageGenService.get_model falls back to this registry).

Registry file: ``~/.matrx/image-models/custom-models.json``
  {"version": 1, "models": [<entry>, ...]}   — atomic writes (tmp + replace).

Entry keys: model_id ("custom/<sanitized-ref>"), name, source ("hf"|"civitai"),
source_ref, pipeline_type, family, format ("diffusers"|"single_file"),
weight_name (single_file), files ([{name,size}], diffusers), size_gb,
requires_hf_token, download_url / civitai_model_id / civitai_version_id
(civitai), vram_gb, ram_gb, added_at.

Family policy — HONEST BY DESIGN:
  * A model whose family cannot be proven maps to family "unknown" and is
    REFUSED at registration with the reason. We never pretend.
  * single_file is only allowed for families whose pipeline class inherits
    diffusers' FromSingleFileMixin — verified against diffusers 0.37.1 (the
    runtime floor; also documented for 0.39): StableDiffusionPipeline,
    StableDiffusionXLPipeline, FluxPipeline, ZImagePipeline support it;
    QwenImagePipeline and Flux2KleinPipeline do NOT. Unsupported combos are
    rejected AT REGISTRATION, never at load time.

Hardware heuristic (conservative, size-based — documented contract):
  * single_file checkpoints are ~always fp16/bf16 on disk → resident weight
    memory ≈ file size; VRAM = size_gb + 1.5 GB activation/overhead headroom.
  * diffusers repos mix fp32 shards (loaded at half precision → 0.5x) and
    bf16 weights (1.0x); we assume 0.65x + 1.5 GB overhead.
  * Floors match the smallest catalog model: 6 GB VRAM / 10 GB RAM; RAM =
    VRAM + 4 GB. Rounded to 0.1. Erring HIGH (a too-strict gate) is the
    conservative direction — the gate reuses check_model_hardware exactly
    like catalog models.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.common.system_logger import get_logger
from app.services.image_gen.models import ImageGenModel
from app.services.media_gen.paths import (
    image_models_dir,
    model_dir,
    read_civitai_key,
    read_hf_token,
    sanitize_model_id,
)

logger = get_logger()

REGISTRY_FILENAME = "custom-models.json"
CUSTOM_MODEL_PREFIX = "custom/"

# ── family tables (single source of truth for custom models) ─────────────────
# pipeline_type / dtype / img2img / LoRA flags all derive from the family,
# using the SAME semantics as the curated catalog in models.py.

FAMILY_INFO: dict[str, dict[str, Any]] = {
    "sd15": {
        "pipeline_type": "stable-diffusion",
        "steps": 30,
        "guidance": 7.5,
        "negative_prompt": True,
        "width": 512,
        "height": 512,
        "supports_img2img": True,
        "img2img_strength": True,
    },
    "sdxl": {
        "pipeline_type": "stable-diffusion-xl",
        "steps": 30,
        "guidance": 6.0,
        "negative_prompt": True,
        "width": 1024,
        "height": 1024,
        "supports_img2img": True,
        "img2img_strength": True,
    },
    "flux": {
        "pipeline_type": "flux",
        "steps": 28,
        "guidance": 3.5,
        "negative_prompt": False,
        "width": 1024,
        "height": 1024,
        "supports_img2img": True,
        "img2img_strength": True,
    },
    "flux2": {
        "pipeline_type": "flux2-klein",
        "steps": 4,
        "guidance": 1.0,
        "negative_prompt": False,
        "width": 1024,
        "height": 1024,
        # Flux2KleinPipeline is its own unified generate+edit pipeline with
        # NO strength parameter (see models.py catalog entry).
        "supports_img2img": True,
        "img2img_strength": False,
    },
    "qwen": {
        "pipeline_type": "qwen-image",
        "steps": 50,
        "guidance": 4.0,
        "negative_prompt": True,
        "width": 1024,
        "height": 1024,
        "supports_img2img": True,
        "img2img_strength": True,
    },
    "z-image": {
        "pipeline_type": "z-image",
        "steps": 9,
        "guidance": 0.0,
        "negative_prompt": False,
        "width": 1024,
        "height": 1024,
        "supports_img2img": True,
        "img2img_strength": True,
    },
}

# Families whose pipeline class inherits diffusers FromSingleFileMixin.
# VERIFIED by issubclass() against diffusers 0.37.1 (the runtime floor):
#   StableDiffusionPipeline  → True   StableDiffusionXLPipeline → True
#   FluxPipeline             → True   ZImagePipeline            → True
#   QwenImagePipeline        → False  Flux2KleinPipeline        → False
SINGLE_FILE_FAMILIES: frozenset[str] = frozenset({"sd15", "sdxl", "flux", "z-image"})

# HF diffusers model_index.json "_class_name" → family. Extends the pipeline
# table in service.py — anything not listed is family "unknown" (refused).
PIPELINE_CLASS_TO_FAMILY: dict[str, str] = {
    "StableDiffusionPipeline": "sd15",
    "StableDiffusionXLPipeline": "sdxl",
    "FluxPipeline": "flux",
    "Flux2KleinPipeline": "flux2",
    "QwenImagePipeline": "qwen",
    "ZImagePipeline": "z-image",
}

# Civitai ``baseModel`` strings → family (lowercased lookup). Pony/Illustrious/
# NoobAI are SDXL-architecture fine-tune lineages — their checkpoints and
# LoRAs load with the SDXL pipeline/adapters. Anything unmapped (SD 2.x,
# SD 3.x, video bases, ...) is "unknown" and registration refuses — honestly.
CIVITAI_BASE_MODEL_TO_FAMILY: dict[str, str] = {
    "sd 1.4": "sd15",
    "sd 1.5": "sd15",
    "sd 1.5 lcm": "sd15",
    "sd 1.5 hyper": "sd15",
    "sdxl 0.9": "sdxl",
    "sdxl 1.0": "sdxl",
    "sdxl 1.0 lcm": "sdxl",
    "sdxl turbo": "sdxl",
    "sdxl lightning": "sdxl",
    "sdxl hyper": "sdxl",
    "pony": "sdxl",
    "illustrious": "sdxl",
    "noobai": "sdxl",
    "flux.1 d": "flux",
    "flux.1 s": "flux",
    "flux.1 krea": "flux",
    "flux.2 klein": "flux2",
    "qwen": "qwen",
    "z-image": "z-image",
    "z-image turbo": "z-image",
    # Civitai's live baseModel string for Tongyi Z-Image Turbo / Base
    # (verified via API 2026-07-11 — NOT "Z-Image Turbo", the camel-concat form).
    "zimageturbo": "z-image",
    "zimagebase": "z-image",
}


def map_civitai_base_model(base_model: str | None) -> str:
    """Civitai baseModel → our family; "unknown" when unmapped (never a guess)."""
    if not base_model:
        return "unknown"
    return CIVITAI_BASE_MODEL_TO_FAMILY.get(base_model.strip().lower(), "unknown")


class InspectError(Exception):
    """Raised by ref parsing / inspection with an HTTP-mappable status."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── hardware heuristic ────────────────────────────────────────────────────────


def estimate_hardware(size_gb: float, fmt: str) -> tuple[float, float]:
    """Conservative size-based (vram_gb, ram_gb) estimate — see module docs."""
    if fmt == "single_file":
        vram = size_gb + 1.5
    else:
        vram = size_gb * 0.65 + 1.5
    vram = max(6.0, round(vram, 1))
    ram = max(10.0, round(vram + 4.0, 1))
    return vram, ram


# ── registry persistence (atomic) ─────────────────────────────────────────────


def registry_path() -> Path:
    return image_models_dir() / REGISTRY_FILENAME


def load_registry() -> list[dict[str, Any]]:
    """All registered custom-model entries. Corrupt file → skip-and-scream."""
    path = registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise ValueError("registry root must be {'version': 1, 'models': [...]}")
        return [m for m in models if isinstance(m, dict) and m.get("model_id")]
    except Exception as exc:  # noqa: BLE001 — a corrupt registry must not 500 /models
        logger.error("[image_gen] Corrupt custom-model registry %s: %s", path, exc)
        return []


def save_registry(models: list[dict[str, Any]]) -> None:
    """Atomic write: tmp file + os.replace — never a half-written registry."""
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "models": models}, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)


def list_custom_models() -> list[dict[str, Any]]:
    return load_registry()


def get_custom_entry(model_id: str) -> dict[str, Any] | None:
    return next((m for m in load_registry() if m["model_id"] == model_id), None)


# ── ref parsing ───────────────────────────────────────────────────────────────

_HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")
_CIVITAI_SHORT_RE = re.compile(r"^civitai:(\d+)(?:@(\d+))?$")


def parse_ref(ref: str) -> dict[str, Any]:
    """Classify a user ref → {"kind": "hf", "repo_id": ...} or
    {"kind": "civitai", "model_id": int|None, "version_id": int|None}.

    Accepted: HF repo id (org/name), HF URL, Civitai model/version URL,
    bare Civitai model id (all-digits), "civitai:<model>[@<version>]".
    Raises InspectError(400) for anything unresolvable — with the reason.
    """
    r = (ref or "").strip()
    if not r:
        raise InspectError(400, "ref must not be empty")

    if r.isdigit():
        return {"kind": "civitai", "model_id": int(r), "version_id": None}

    m = _CIVITAI_SHORT_RE.match(r)
    if m:
        return {
            "kind": "civitai",
            "model_id": int(m.group(1)),
            "version_id": int(m.group(2)) if m.group(2) else None,
        }

    # civitai.com = SFW front door; civitai.red = full/NSFW front door;
    # civitai.green redirects to .com. Same DB + path shape on all three —
    # only the host filter differs. API resolution always hits civitai.com.
    _CIVITAI_HOSTS = frozenset({"civitai.com", "civitai.red", "civitai.green"})
    looks_like_url = "://" in r or r.lower().startswith(
        (
            "www.",
            "huggingface.co/",
            "hf.co/",
            "civitai.com/",
            "civitai.red/",
            "civitai.green/",
        )
    )
    if looks_like_url:
        url = r if "://" in r else f"https://{r}"
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().removeprefix("www.")
        path = parsed.path or ""

        if host in _CIVITAI_HOSTS:
            dl = re.search(r"/api/download/models/(\d+)", path)
            if dl:
                return {
                    "kind": "civitai",
                    "model_id": None,
                    "version_id": int(dl.group(1)),
                }
            mv = re.search(r"/(?:api/v1/)?model-versions/(\d+)", path)
            if mv:
                return {
                    "kind": "civitai",
                    "model_id": None,
                    "version_id": int(mv.group(1)),
                }
            mm = re.search(r"/models/(\d+)", path)
            if mm:
                q = parse_qs(parsed.query or "")
                # modelVersionId is the pin — without it multi-base models
                # (ZIT + Flux + SDXL on one page) resolve to the NEWEST
                # version, which is often a different family.
                ver = q.get("modelVersionId", [None])[0]
                return {
                    "kind": "civitai",
                    "model_id": int(mm.group(1)),
                    "version_id": int(ver) if ver and str(ver).isdigit() else None,
                }
            raise InspectError(
                400,
                f"Unrecognized Civitai URL '{ref}' — expected a model page "
                "(civitai.com|civitai.red/models/<id>?modelVersionId=<ver>) "
                "or model-version / download URL.",
            )

        if host in ("huggingface.co", "hf.co"):
            segs = [s for s in path.split("/") if s]
            if len(segs) >= 2 and segs[0] not in ("datasets", "spaces", "api"):
                repo_id = f"{segs[0]}/{segs[1]}"
                if _HF_REPO_RE.match(repo_id):
                    return {"kind": "hf", "repo_id": repo_id}
            raise InspectError(
                400,
                f"Unrecognized Hugging Face URL '{ref}' — expected a model "
                "page (huggingface.co/<org>/<name>).",
            )

        raise InspectError(
            400,
            f"Unsupported host in '{ref}' — only huggingface.co and "
            "civitai.com / civitai.red references are accepted.",
        )

    if _HF_REPO_RE.match(r) and ".." not in r:
        return {"kind": "hf", "repo_id": r}

    raise InspectError(
        400,
        f"Could not interpret '{ref}' as a Hugging Face repo id/URL or a "
        "Civitai model/version URL/id.",
    )


# ── HTTP (mocked in tests — everything network goes through here) ────────────


async def _http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(url, headers=headers or {})
        resp.raise_for_status()
        return resp.json()


def _civitai_headers() -> dict[str, str]:
    key = read_civitai_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _hf_headers() -> dict[str, str]:
    token = read_hf_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _friendly_civitai_http_error(exc: httpx.HTTPStatusError, what: str) -> InspectError:
    code = exc.response.status_code
    if code in (401, 403):
        if read_civitai_key():
            if code == 401:
                return InspectError(
                    401,
                    f"Civitai rejected the saved API key while resolving {what}. "
                    "The key may have been revoked or regenerated — update it "
                    "under Settings → API Keys → Civitai, then try again.",
                )
            return InspectError(
                403,
                f"Your Civitai key is connected, but Civitai says your account "
                f"doesn't have access to {what}. Open the model page on Civitai "
                "to unlock access, then try again.",
            )
        return InspectError(
            401,
            f"Civitai requires an API key to resolve {what}. Add your key "
            "under Settings → API Keys → Civitai, then try again.",
        )
    if code == 404:
        return InspectError(404, f"Civitai has no {what} (404) — check the URL/id.")
    return InspectError(400, f"Civitai request for {what} failed ({code}).")


# ── Civitai resolution ────────────────────────────────────────────────────────


def _pick_civitai_file(files: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the primary safetensors Model file of a version. Loud on failure."""
    model_files = [f for f in files if str(f.get("type", "Model")) == "Model"]
    if not model_files:
        raise InspectError(400, "This Civitai version lists no model file.")

    def is_safetensor(f: dict[str, Any]) -> bool:
        fmt = str(((f.get("metadata") or {}).get("format")) or "").lower()
        name = str(f.get("name") or "").lower()
        return fmt == "safetensor" or name.endswith(".safetensors")

    st = [f for f in model_files if is_safetensor(f)]
    if not st:
        raise InspectError(
            400,
            "This Civitai version has no .safetensors file — only safetensors "
            "checkpoints are supported (pickle .ckpt files are not loaded).",
        )
    primary = next((f for f in st if f.get("primary")), None)
    return primary or st[0]


async def resolve_civitai(parsed: dict[str, Any]) -> dict[str, Any]:
    """Resolve a parsed civitai ref → normalized info dict (no weight download).

    Returns: {model_id, version_id, model_name, version_name, model_type,
    base_model, family, file_name, size_kb, download_url}.
    """
    version_id = parsed.get("version_id")
    model_id = parsed.get("model_id")
    headers = _civitai_headers()

    if version_id is not None:
        try:
            ver = await _http_get_json(
                f"https://civitai.com/api/v1/model-versions/{version_id}",
                headers=headers,
            )
        except httpx.HTTPStatusError as exc:
            raise _friendly_civitai_http_error(exc, f"model version {version_id}")
        model_meta = ver.get("model") or {}
        model_name = str(model_meta.get("name") or f"Civitai {ver.get('modelId')}")
        model_type = str(model_meta.get("type") or "")
        model_id = int(ver.get("modelId") or (model_id or 0))
    else:
        try:
            mod = await _http_get_json(
                f"https://civitai.com/api/v1/models/{model_id}", headers=headers
            )
        except httpx.HTTPStatusError as exc:
            raise _friendly_civitai_http_error(exc, f"model {model_id}")
        model_name = str(mod.get("name") or f"Civitai {model_id}")
        model_type = str(mod.get("type") or "")
        versions = mod.get("modelVersions") or []
        if not versions:
            raise InspectError(400, f"Civitai model {model_id} has no versions.")
        ver = versions[0]
        version_id = int(ver.get("id"))

    base_model = str(ver.get("baseModel") or "")
    file = _pick_civitai_file(list(ver.get("files") or []))
    download_url = str(file.get("downloadUrl") or ver.get("downloadUrl") or "")
    if not download_url:
        raise InspectError(400, "Civitai returned no download URL for this version.")

    return {
        "model_id": int(model_id or 0),
        "version_id": int(version_id),
        "model_name": model_name,
        "version_name": str(ver.get("name") or ""),
        "model_type": model_type,
        "base_model": base_model,
        "family": map_civitai_base_model(base_model),
        "file_name": str(file.get("name") or f"civitai-{version_id}.safetensors"),
        "size_kb": float(file.get("sizeKB") or 0.0),
        "download_url": download_url,
    }


# ── HF resolution ─────────────────────────────────────────────────────────────


def _guess_checkpoint_family(
    repo_id: str, filename: str | None, base_model: str | None, tags: list[str]
) -> str:
    """Family guess for an HF single-file checkpoint repo (no model_index).

    Same honest posture as loras.guess_base_family — anything ambiguous is
    "unknown" (and registration refuses)."""
    haystacks: list[str] = []
    if base_model:
        haystacks.append(base_model.lower())
    haystacks.extend(t.lower() for t in tags)
    haystacks.append(repo_id.lower())
    if filename:
        haystacks.append(filename.lower())
    for text in haystacks:
        if "flux.2" in text or "flux2" in text:
            return "flux2"
        if "flux" in text:
            return "flux"
        if "z-image" in text or "zimage" in text:
            return "z-image"
        if "qwen-image" in text:
            return "qwen"
        if "sdxl" in text or (
            "xl" in text and ("sd" in text or "stable-diffusion" in text)
        ):
            return "sdxl"
        if "pony" in text or "illustrious" in text:
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


async def resolve_hf(repo_id: str) -> dict[str, Any]:
    """Resolve an HF repo → normalized info dict WITHOUT downloading weights.

    diffusers-format repos (model_index.json present): pipeline class →
    family; size = filtered file listing (same filter the DownloadManager
    uses, so the estimate matches what will actually be fetched).
    Single-file repos: the largest root-level .safetensors checkpoint.
    """
    from app.services.downloads.manager import filter_hf_repo_files  # noqa: PLC0415

    headers = _hf_headers()
    try:
        info = await _http_get_json(
            f"https://huggingface.co/api/models/{repo_id}?blobs=true",
            headers=headers,
        )
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            raise InspectError(
                401,
                f"Hugging Face denied access to '{repo_id}' ({code}) — the "
                "repo is gated/private. Add a Hugging Face read token under "
                "Settings → API Keys → Hugging Face.",
            )
        if code == 404:
            raise InspectError(
                404, f"Hugging Face repo '{repo_id}' does not exist (404)."
            )
        raise InspectError(
            400, f"Hugging Face request for '{repo_id}' failed ({code})."
        )

    siblings: list[dict[str, Any]] = list(info.get("siblings") or [])
    files: list[tuple[str, int]] = [
        (str(s.get("rfilename")), int(s.get("size") or 0)) for s in siblings
    ]
    names = {fn for fn, _ in files}
    gated = info.get("gated")
    requires_token = bool(gated) and gated is not False

    card = info.get("cardData") or {}
    raw_base = card.get("base_model")
    base_model = (
        raw_base
        if isinstance(raw_base, str)
        else (
            raw_base[0]
            if isinstance(raw_base, list) and raw_base and isinstance(raw_base[0], str)
            else None
        )
    )
    tags = [str(t) for t in (info.get("tags") or [])]

    if "model_index.json" in names:
        try:
            index = await _http_get_json(
                f"https://huggingface.co/{repo_id}/raw/main/model_index.json",
                headers=headers,
            )
        except httpx.HTTPStatusError as exc:
            raise InspectError(
                400,
                f"Could not read model_index.json from '{repo_id}' "
                f"({exc.response.status_code}).",
            )
        class_name = str((index or {}).get("_class_name") or "")
        family = PIPELINE_CLASS_TO_FAMILY.get(class_name, "unknown")
        kept = filter_hf_repo_files(files, load_variant=None)
        size_gb = round(sum(s for _, s in kept) / 1e9, 2)
        return {
            "format": "diffusers",
            "family": family,
            "pipeline_class": class_name,
            "files": [{"name": fn, "size": s} for fn, s in kept],
            "weight_name": None,
            "size_gb": size_gb,
            "requires_hf_token": requires_token,
            "name": repo_id.split("/", 1)[1],
        }

    root_st = [
        (fn, s)
        for fn, s in files
        if "/" not in fn and fn.lower().endswith(".safetensors")
    ]
    if not root_st:
        raise InspectError(
            400,
            f"'{repo_id}' is neither a diffusers-format repo (no "
            "model_index.json) nor a single-file .safetensors checkpoint — "
            "it cannot be registered as an image model.",
        )
    # Largest root safetensors = the checkpoint (repos sometimes also ship a
    # small VAE/embedding file at root).
    weight_name, weight_size = max(root_st, key=lambda t: t[1])
    family = _guess_checkpoint_family(repo_id, weight_name, base_model, tags)
    return {
        "format": "single_file",
        "family": family,
        "pipeline_class": None,
        "files": None,
        "weight_name": weight_name,
        "size_gb": round(weight_size / 1e9, 2),
        "requires_hf_token": requires_token,
        "name": repo_id.split("/", 1)[1],
        "multiple_root_safetensors": len(root_st) > 1,
    }


# ── inspection (the /inspect endpoint body) ───────────────────────────────────


def registration_refusal(entry: dict[str, Any]) -> str | None:
    """Why this entry may NOT be registered, or None when it is registerable.

    The single gate shared by inspect (verdict) and register (enforcement) —
    an unsupported model is always refused AT REGISTRATION with the reason,
    never discovered at load time.
    """
    family = str(entry.get("family") or "unknown")
    fmt = str(entry.get("format") or "")
    if family not in FAMILY_INFO:
        return (
            f"Model family '{family}' is not supported — the base "
            "architecture could not be mapped to a supported pipeline. "
            f"Supported families: {', '.join(sorted(FAMILY_INFO))}."
        )
    if fmt not in ("diffusers", "single_file"):
        return f"Unknown model format '{fmt}' — expected 'diffusers' or 'single_file'."
    if fmt == "single_file":
        if family not in SINGLE_FILE_FAMILIES:
            return (
                f"Single-file checkpoints are not supported for the "
                f"'{family}' family — diffusers has no from_single_file "
                f"loader for its pipeline. Single-file support: "
                f"{', '.join(sorted(SINGLE_FILE_FAMILIES))}."
            )
        if not entry.get("weight_name"):
            return (
                "single_file entries must carry weight_name (the checkpoint filename)."
            )
    if entry.get("source") == "civitai" and not entry.get("download_url"):
        return "Civitai entries must carry download_url (from inspect)."
    if entry.get("source") == "hf" and not _HF_REPO_RE.match(
        str(entry.get("source_ref") or "")
    ):
        return f"source_ref '{entry.get('source_ref')}' is not a valid Hugging Face repo id."
    return None


async def inspect_ref(ref: str) -> dict[str, Any]:
    """Resolve a ref into {"entry", "warnings", "registerable", "refusal_reason"}.

    Never downloads weights. Raises InspectError for unresolvable refs and
    for Civitai LoRAs pointed at the model endpoint (they belong on
    POST /image-gen/loras/download).
    """
    parsed = parse_ref(ref)
    warnings: list[str] = []

    if parsed["kind"] == "civitai":
        info = await resolve_civitai(parsed)
        if info["model_type"].upper() in ("LORA", "LOCON", "DORA"):
            raise InspectError(
                400,
                f"Civitai model '{info['model_name']}' is a "
                f"{info['model_type']} (a LoRA), not a checkpoint — download "
                "it via POST /image-gen/loras/download "
                '{"civitai": "<same ref>"} instead.',
            )
        if info["model_type"] and info["model_type"] != "Checkpoint":
            raise InspectError(
                400,
                f"Civitai model '{info['model_name']}' has type "
                f"'{info['model_type']}' — only Checkpoint models can be "
                "registered as image models.",
            )
        family = info["family"]
        size_gb = round(info["size_kb"] * 1024 / 1e9, 2)
        requires_hf_token = False
        if family == "flux":
            # A Flux single-file checkpoint carries only the transformer;
            # diffusers' from_single_file fetches the remaining components
            # (text encoders/VAE) from the gated FLUX.1 HF repos at first load.
            requires_hf_token = True
            warnings.append(
                "Flux single-file checkpoints contain only the transformer — "
                "the text encoders and VAE are fetched from the (gated) "
                "FLUX.1 Hugging Face repo at first load, which requires a "
                "Hugging Face token."
            )
        entry: dict[str, Any] = {
            "model_id": f"{CUSTOM_MODEL_PREFIX}civitai-{info['model_id']}-{info['version_id']}",
            "name": (
                f"{info['model_name']} ({info['version_name']})"
                if info["version_name"]
                else info["model_name"]
            ),
            "source": "civitai",
            "source_ref": f"civitai:{info['model_id']}@{info['version_id']}",
            "family": family,
            "pipeline_type": FAMILY_INFO.get(family, {}).get(
                "pipeline_type", "unknown"
            ),
            "format": "single_file",
            "weight_name": info["file_name"],
            "files": None,
            "size_gb": size_gb,
            "requires_hf_token": requires_hf_token,
            "download_url": info["download_url"],
            "civitai_model_id": info["model_id"],
            "civitai_version_id": info["version_id"],
        }
        warnings.insert(
            0,
            (
                "Community model from Civitai — the license and content are not "
                "verified by this app; check the model page before commercial use."
            ),
        )
        if not read_civitai_key():
            warnings.append(
                "Most Civitai downloads require an API key — add one under "
                "Settings → API Keys → Civitai before downloading."
            )
        if family == "unknown":
            warnings.append(
                f"Civitai baseModel '{info['base_model'] or '(none)'}' does "
                "not map to a supported family."
            )
    else:
        repo_id = parsed["repo_id"]
        info = await resolve_hf(repo_id)
        family = info["family"]
        entry = {
            "model_id": f"{CUSTOM_MODEL_PREFIX}{sanitize_model_id(repo_id)}",
            "name": info["name"],
            "source": "hf",
            "source_ref": repo_id,
            "family": family,
            "pipeline_type": FAMILY_INFO.get(family, {}).get(
                "pipeline_type", "unknown"
            ),
            "format": info["format"],
            "weight_name": info["weight_name"],
            "files": info["files"],
            "size_gb": info["size_gb"],
            "requires_hf_token": bool(info["requires_hf_token"]),
            "download_url": None,
            "civitai_model_id": None,
            "civitai_version_id": None,
        }
        warnings.append(
            "Community model — the license is not verified by this app; "
            "check the model card before commercial use."
        )
        if info["format"] == "single_file":
            warnings.append(
                "Family was detected heuristically from the model card/"
                "filename (no model_index.json) — generation fails loudly if "
                "the detection is wrong."
            )
            if info.get("multiple_root_safetensors"):
                warnings.append(
                    "The repo ships multiple root-level .safetensors files — "
                    "the largest one was selected as the checkpoint."
                )
        elif family == "unknown":
            warnings.append(
                f"Pipeline class '{info.get('pipeline_class') or '(none)'}' "
                "is not supported by this app."
            )
        if entry["requires_hf_token"]:
            warnings.append(
                "Gated repo — a Hugging Face read token is required to "
                "download it (Settings → API Keys → Hugging Face)."
            )

    vram_gb, ram_gb = estimate_hardware(float(entry["size_gb"]), str(entry["format"]))
    entry["vram_gb"] = vram_gb
    entry["ram_gb"] = ram_gb
    refusal = registration_refusal(entry)
    return {
        "entry": entry,
        "warnings": warnings,
        "registerable": refusal is None,
        "refusal_reason": refusal,
    }


# ── registration / deletion / catalog bridge ──────────────────────────────────


def register_custom_model(entry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Validate + persist an (inspect-confirmed) entry. Returns (stored, created).

    Derived fields (model_id shape, vram/ram, added_at) are recomputed
    server-side — a tampered client payload can not smuggle an unsupported
    combo past the gate. Raises ValueError with the reason on refusal.
    """
    refusal = registration_refusal(entry)
    if refusal:
        raise ValueError(refusal)

    model_id = str(entry.get("model_id") or "")
    if not model_id.startswith(CUSTOM_MODEL_PREFIX) or len(model_id) <= len(
        CUSTOM_MODEL_PREFIX
    ):
        raise ValueError(
            f"Custom model ids must be namespaced '{CUSTOM_MODEL_PREFIX}<ref>' "
            f"— got '{model_id}'."
        )

    models = load_registry()
    existing = next((m for m in models if m["model_id"] == model_id), None)
    if existing is not None:
        return existing, False

    family = str(entry["family"])
    vram_gb, ram_gb = estimate_hardware(
        float(entry.get("size_gb") or 0.0), str(entry["format"])
    )
    stored: dict[str, Any] = {
        "model_id": model_id,
        "name": str(entry.get("name") or model_id),
        "source": str(entry["source"]),
        "source_ref": str(entry["source_ref"]),
        "family": family,
        "pipeline_type": FAMILY_INFO[family]["pipeline_type"],
        "format": str(entry["format"]),
        "weight_name": entry.get("weight_name"),
        "files": entry.get("files"),
        "size_gb": round(float(entry.get("size_gb") or 0.0), 2),
        "requires_hf_token": bool(entry.get("requires_hf_token")),
        "download_url": entry.get("download_url"),
        "civitai_model_id": entry.get("civitai_model_id"),
        "civitai_version_id": entry.get("civitai_version_id"),
        "vram_gb": vram_gb,
        "ram_gb": ram_gb,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    models.append(stored)
    save_registry(models)
    logger.info(
        "[image_gen] Registered custom model %s (source=%s family=%s format=%s "
        "size=%.2fGB)",
        model_id,
        stored["source"],
        family,
        stored["format"],
        stored["size_gb"],
    )
    return stored, True


def remove_custom_model(model_id: str) -> bool:
    """Remove a registry entry AND its weights on disk. False when unknown."""
    models = load_registry()
    remaining = [m for m in models if m["model_id"] != model_id]
    if len(remaining) == len(models):
        return False
    save_registry(remaining)
    d = model_dir(image_models_dir(), model_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    logger.info("[image_gen] Deleted custom model %s (weights dir %s)", model_id, d)
    return True


def custom_entry_to_model(entry: dict[str, Any]) -> ImageGenModel:
    """Bridge a registry entry into the catalog dataclass — the service and
    routes treat custom models EXACTLY like catalog models from here on."""
    family = str(entry["family"])
    fam = FAMILY_INFO[family]
    source = str(entry.get("source") or "hf")
    if source == "civitai":
        card_url = f"https://civitai.com/models/{entry.get('civitai_model_id')}"
        provider = "Custom (Civitai)"
    else:
        card_url = f"https://huggingface.co/{entry['source_ref']}"
        provider = "Custom (Hugging Face)"
    return ImageGenModel(
        model_id=str(entry["model_id"]),
        name=str(entry.get("name") or entry["model_id"]),
        provider=provider,
        pipeline_type=fam["pipeline_type"],
        vram_gb=float(entry.get("vram_gb") or 6.0),
        ram_gb=float(entry.get("ram_gb") or 10.0),
        description=(
            f"Custom {family} model ({entry.get('format')}) added from "
            f"{'Civitai' if source == 'civitai' else 'Hugging Face'}."
        ),
        quality_rating=0,
        speed_rating=0,
        recommended_steps=int(fam["steps"]),
        recommended_guidance=float(fam["guidance"]),
        supports_negative_prompt=bool(fam["negative_prompt"]),
        model_card_url=card_url,
        default_width=int(fam["width"]),
        default_height=int(fam["height"]),
        download_size_gb=float(entry.get("size_gb") or 0.0),
        requires_hf_token=bool(entry.get("requires_hf_token")),
        supports_img2img=bool(fam["supports_img2img"]),
        img2img_strength=bool(fam["img2img_strength"]),
        lora_family=family,
        tags=["custom", source],
        format=str(entry.get("format") or "diffusers"),
        weight_name=entry.get("weight_name"),
        custom=True,
        source=source,
    )


def get_custom_model(model_id: str) -> ImageGenModel | None:
    entry = get_custom_entry(model_id)
    if entry is None:
        return None
    try:
        return custom_entry_to_model(entry)
    except Exception as exc:  # noqa: BLE001 — a bad registry row must scream, not 500
        logger.error("[image_gen] Corrupt custom-model entry %s: %s", model_id, exc)
        return None


def list_custom_catalog_models() -> list[ImageGenModel]:
    out: list[ImageGenModel] = []
    for entry in load_registry():
        m = get_custom_model(str(entry["model_id"]))
        if m is not None:
            out.append(m)
    return out


# ── download enqueue ──────────────────────────────────────────────────────────


async def enqueue_custom_download(entry: dict[str, Any]) -> dict[str, Any]:
    """Queue a registered custom model's weights through the DownloadManager.

    HF source → the existing HF snapshot path (hf_repo_id; single_file adds
    hf_allow_files). Civitai source → the direct-URL path with
    civitai_download=True (Bearer auth from the stored key; 401/403 surfaces
    the friendly "Civitai API key required" message on the download entry).
    """
    from app.services.downloads.manager import get_download_manager  # noqa: PLC0415

    model_id = str(entry["model_id"])
    dest = model_dir(image_models_dir(), model_id)
    if entry["source"] == "hf":
        metadata: dict[str, Any] = {
            "dest_dir": str(dest),
            "hf_repo_id": entry["source_ref"],
            "model_id": model_id,
            "load_variant": None,
        }
        if entry["format"] == "single_file":
            metadata["hf_allow_files"] = [entry["weight_name"]]
        urls = [f"hf://{entry['source_ref']}"]  # marker only — HF path ignores URLs
    else:
        metadata = {
            "dest_dir": str(dest),
            "model_id": model_id,
            "civitai_download": True,
            "write_complete_marker": True,
            "dest_filename": entry["weight_name"],
        }
        if entry.get("civitai_model_id") is not None:
            metadata["model_page_url"] = (
                f"https://civitai.com/models/{entry['civitai_model_id']}"
            )
        urls = [str(entry["download_url"])]

    dl = await get_download_manager().enqueue(
        category="image_gen",
        filename=sanitize_model_id(model_id),
        display_name=str(entry.get("name") or model_id),
        urls=urls,
        metadata=metadata,
        priority=1,
    )
    return {"queued": True, "download_id": dl.id}
