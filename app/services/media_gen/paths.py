"""Filesystem layout for locally-downloaded media models and generated outputs.

Model weights are downloaded into dedicated per-model directories under
``~/.matrx`` (NOT the default Hugging Face cache) so the app fully owns them,
can report accurate "is downloaded" state, and can delete a model cleanly:

    ~/.matrx/image-models/<sanitized-repo-id>/
    ~/.matrx/video-models/<sanitized-repo-id>/

A download is considered complete only when the marker file
``DOWNLOAD_COMPLETE_MARKER`` exists in the model directory. The universal
DownloadManager writes this marker after a Hugging Face snapshot finishes, and
the services read it to decide whether ``from_pretrained(local_files_only=True)``
may be attempted. This makes a half-finished / interrupted download report as
"not downloaded" instead of silently loading a corrupt cache — the exact class
of bug this overhaul exists to kill.

Generated outputs:

    ~/.matrx/generated/videos/    — encoded .mp4 files + jobs.json history

Images are returned inline as base64 and are not persisted here.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.config import MATRX_HOME_DIR

# Marker written into a model dir once its weights are fully downloaded.
# Shared with app/services/downloads/manager.py (Hugging Face snapshot path).
DOWNLOAD_COMPLETE_MARKER = ".download-complete"


def image_models_dir() -> Path:
    return MATRX_HOME_DIR / "image-models"


def video_models_dir() -> Path:
    return MATRX_HOME_DIR / "video-models"


def generated_videos_dir() -> Path:
    return MATRX_HOME_DIR / "generated" / "videos"


def sanitize_model_id(model_id: str) -> str:
    """Turn a HF repo id (``org/name``) into a safe single-segment dir name.

    ``black-forest-labs/FLUX.2-klein-4B`` → ``black-forest-labs--FLUX.2-klein-4B``.
    """
    return model_id.replace("/", "--").replace("\\", "--")


def model_dir(base: Path, model_id: str) -> Path:
    return base / sanitize_model_id(model_id)


def is_model_downloaded(base: Path, model_id: str) -> bool:
    """True only when the model dir exists AND carries the completion marker."""
    d = model_dir(base, model_id)
    return (d / DOWNLOAD_COMPLETE_MARKER).exists()


def mark_model_downloaded(base: Path, model_id: str) -> None:
    d = model_dir(base, model_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")


def read_hf_token() -> str | None:
    """The configured Hugging Face token, or None.

    Reads the same env vars the key_manager injects stored keys into and that
    the DownloadManager's HF snapshot path consumes — the single source of truth
    for "is an HF token configured". Gated (private) model downloads pre-check
    this so the user gets a clean "set your HF token" message instead of a deep
    401 RuntimeError buried in the download worker.
    """
    return (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or None
    )
