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

    ~/.matrx/generated/videos/           — video job scratch dir + jobs.json history
    ~/.matrx/media/generated/images/     — media library: <uuid>.png + <uuid>.json
    ~/.matrx/media/generated/videos/     — media library: <uuid>.mp4 + <uuid>.json

Every successful generation is persisted into the media library
(app/services/media_gen/library.py): the media file plus a JSON sidecar with
the full resolved generation parameters. /media-library/* serves it.
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


def generated_images_dir() -> Path:
    """Image job history dir (jobs.json) — mirrors generated_videos_dir()."""
    return MATRX_HOME_DIR / "generated" / "images"


def generated_media_dir() -> Path:
    """Root of the persistent media library (images/ and videos/ subdirs)."""
    return MATRX_HOME_DIR / "media" / "generated"


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

    Resolution order (highest → lowest precedence):
      1. ``HF_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN`` env vars. The key_manager
         injects the app-stored token (SQLite ``huggingface`` API key) into
         these at startup AND on save, and an explicitly user-set env var
         beats everything else — this is the primary source of truth.
      2. huggingface_hub's own token store via ``get_token()`` — this picks up
         a token written to the standard cache locations
         (``~/.cache/huggingface/token`` / ``~/.huggingface/token``) by a prior
         ``huggingface-cli login`` or by any hub call that cached credentials.
         This is what restores a token the user "already had" but that only
         ever lived in the HF cache, never in the app's own store.

    Gated (private) model downloads pre-check this so the user gets a clean
    "set your HF token" message instead of a deep 401 RuntimeError buried in
    the download worker. Because huggingface_hub itself falls back to the same
    ``get_token()`` store, checking it here keeps the pre-check consistent with
    what the actual download will resolve.
    """
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        val = os.environ.get(var)
        if val and val.strip():
            return val.strip()

    # Lazy import — huggingface_hub is only present once the AI packages are
    # installed (in-app installer). Never let its absence break this call.
    try:
        from huggingface_hub import get_token  # noqa: PLC0415

        cached = get_token()
    except Exception:  # noqa: BLE001 — import missing or hub internal error
        cached = None
    if cached and cached.strip():
        return cached.strip()

    return None
