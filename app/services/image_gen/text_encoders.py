"""Persistent optional text-encoder assets for image-generation models.

The catalog declares alternatives inside each ``image_gen_model`` payload.
This module owns their local lifecycle:

    ~/.matrx/image-models/text-encoders/<encoder-id>/
        text-encoder.json
        <catalog-declared files>
        .download-complete

Downloads always use the universal DownloadManager (category
``image_gen_text_encoder``), are revision-pinned when the catalog supplies a
commit, and become installed only after every declared file plus the final
marker exists.  The stock model encoder is implicit and never copied here.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.services.image_gen.models import AlternativeTextEncoder, ImageGenModel
from app.services.media_gen.paths import (
    DOWNLOAD_COMPLETE_MARKER,
    image_text_encoders_dir,
    read_hf_token,
)

logger = get_logger()

_ENCODER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_META_NAME = "text-encoder.json"


def is_valid_encoder_id(encoder_id: str) -> bool:
    return bool(_ENCODER_ID_RE.fullmatch(encoder_id)) and ".." not in encoder_id


def encoder_dir(encoder_id: str) -> Path:
    if not is_valid_encoder_id(encoder_id):
        raise ValueError(f"Invalid text encoder id: {encoder_id!r}")
    return image_text_encoders_dir() / encoder_id


def get_model_encoder(
    model: ImageGenModel, encoder_id: str | None
) -> AlternativeTextEncoder | None:
    if encoder_id is None:
        return None
    return next((e for e in model.text_encoders if e.encoder_id == encoder_id), None)


def _read_meta(encoder_id: str) -> dict[str, Any] | None:
    if not is_valid_encoder_id(encoder_id):
        return None
    path = encoder_dir(encoder_id) / _META_NAME
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("metadata root is not an object")
        return value
    except Exception as exc:  # noqa: BLE001 — one corrupt asset never blanks the catalog
        logger.error("[image_gen] Corrupt text encoder metadata %s: %s", path, exc)
        return None


def installed_encoder(spec: AlternativeTextEncoder) -> dict[str, Any] | None:
    """Return installation metadata when this exact catalog revision is ready."""
    meta = _read_meta(spec.encoder_id)
    if meta is None:
        return None
    if meta.get("repo_id") != spec.repo_id or meta.get("revision") != spec.revision:
        return None
    root = encoder_dir(spec.encoder_id)
    if not (root / DOWNLOAD_COMPLETE_MARKER).exists():
        return None
    if any(not (root / filename).is_file() for filename in spec.files):
        return None
    return {**meta, "dir": str(root), "installed": True}


def is_encoder_installed(spec: AlternativeTextEncoder) -> bool:
    return installed_encoder(spec) is not None


def encoder_api_info(spec: AlternativeTextEncoder) -> dict[str, Any]:
    return {**asdict(spec), "installed": is_encoder_installed(spec)}


def _write_pending_meta(spec: AlternativeTextEncoder) -> None:
    root = encoder_dir(spec.encoder_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / DOWNLOAD_COMPLETE_MARKER).unlink(missing_ok=True)
    payload = {
        **asdict(spec),
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / _META_NAME).write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def start_encoder_download(
    model: ImageGenModel, encoder_id: str
) -> dict[str, Any]:
    """Queue one model-compatible encoder. Idempotent and persistent."""
    spec = get_model_encoder(model, encoder_id)
    if spec is None:
        return {
            "queued": False,
            "error": f"Text encoder '{encoder_id}' is not offered for {model.name}.",
        }
    if is_encoder_installed(spec):
        return {"queued": False, "already_installed": True, "encoder_id": encoder_id}
    if spec.requires_hf_token and read_hf_token() is None:
        return {
            "queued": False,
            "needs_hf_token": True,
            "error": (
                f"{spec.name} is gated on Hugging Face. Accept its repository "
                "terms, then add your Hugging Face read token under Settings → "
                "API Keys → Hugging Face."
            ),
        }

    from app.services.downloads.manager import get_download_manager  # noqa: PLC0415

    _write_pending_meta(spec)
    root = encoder_dir(spec.encoder_id)
    entry = await get_download_manager().enqueue(
        category="image_gen_text_encoder",
        filename=spec.encoder_id,
        display_name=f"Text encoder: {spec.name}",
        urls=[f"hf://{spec.repo_id}"],
        metadata={
            "dest_dir": str(root),
            "hf_repo_id": spec.repo_id,
            "hf_revision": spec.revision,
            "hf_allow_files": list(spec.files),
            "text_encoder_id": spec.encoder_id,
            "model_id": model.model_id,
        },
        priority=1,
    )
    return {
        "queued": True,
        "download_id": entry.id,
        "encoder_id": spec.encoder_id,
    }


def load_encoder_components(
    spec: AlternativeTextEncoder, *, dtype: Any
) -> tuple[Any, Any]:
    """Load a complete Transformers/GGUF encoder and tokenizer from local disk.

    ``state_dict`` alternatives patch the stock pipeline encoder instead and
    are handled by ``ImageGenService`` after pipeline construction.
    """
    installed = installed_encoder(spec)
    if installed is None:
        raise RuntimeError(
            f"Text encoder '{spec.encoder_id}' is not downloaded. Select it in "
            "Alternative text encoders and wait for the download to finish."
        )
    if spec.format == "state_dict":
        raise ValueError("state_dict encoders patch the stock component in place")

    from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415

    root = Path(installed["dir"])
    if spec.format == "transformers":
        component_dir = root / spec.subfolder if spec.subfolder else root
        tokenizer = AutoTokenizer.from_pretrained(
            str(component_dir), local_files_only=True
        )
        encoder = AutoModelForCausalLM.from_pretrained(
            str(component_dir),
            local_files_only=True,
            torch_dtype=dtype,
        )
        return encoder, tokenizer

    if spec.format == "gguf":
        if not spec.weight_name:
            raise RuntimeError(f"GGUF encoder '{spec.encoder_id}' has no weight_name")
        tokenizer = AutoTokenizer.from_pretrained(
            str(root), gguf_file=spec.weight_name, local_files_only=True
        )
        encoder = AutoModelForCausalLM.from_pretrained(
            str(root),
            gguf_file=spec.weight_name,
            local_files_only=True,
            torch_dtype=dtype,
        )
        return encoder, tokenizer

    raise RuntimeError(
        f"Unsupported text encoder format '{spec.format}' for {spec.encoder_id}"
    )
