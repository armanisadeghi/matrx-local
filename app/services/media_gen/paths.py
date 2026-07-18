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
                                           (+ <uuid>.thumb.jpg, self-healing)
    ~/.matrx/media/generated/videos/     — media library: <uuid>.mp4 + <uuid>.json
                                           (+ <uuid>.thumb.jpg poster)

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


def image_loras_dir() -> Path:
    """Installed image LoRAs: ~/.matrx/image-models/loras/<sanitized-repo-id>/.

    Safe alongside model dirs: sanitized HF repo ids always contain ``--`` (an
    org/name slash), so a model dir can never be literally named ``loras``.
    Each LoRA dir holds the safetensors weight file, a ``lora.json`` metadata
    sidecar, and the same ``.download-complete`` marker models use.
    """
    return image_models_dir() / "loras"


def image_text_encoders_dir() -> Path:
    """Optional image text encoders, installed once and reused permanently.

    Layout: ``~/.matrx/image-models/text-encoders/<encoder-id>/``.  Keeping
    these outside model directories avoids duplicating a compatible encoder
    if multiple catalog models intentionally reference it later.
    """
    return image_models_dir() / "text-encoders"


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


def read_civitai_key() -> str | None:
    """The configured Civitai API key, or None.

    Resolution order (mirrors ``read_hf_token``):
      1. ``CIVITAI_API_KEY`` / ``CIVITAI_API_TOKEN`` env vars — the key_manager
         injects the app-stored key (SQLite ``civitai`` API key, saved via
         PUT /settings/api-keys/civitai) into these at startup AND on save.
      2. The key_manager's in-memory resolver cache — survives the planned
         removal of the deprecated env-mutation shim.

    Civitai requires an API key for most model downloads; the DownloadManager
    sends this as ``Authorization: Bearer <key>`` to civitai.com only. The key
    value is never logged.
    """
    for var in ("CIVITAI_API_KEY", "CIVITAI_API_TOKEN"):
        val = os.environ.get(var)
        if val and val.strip():
            return val.strip()
    try:
        from app.services.ai.key_manager import get_cached_user_keys  # noqa: PLC0415

        cached = get_cached_user_keys().get("civitai")
    except Exception:  # noqa: BLE001 — cache unavailable must never break callers
        cached = None
    if cached and cached.strip():
        return cached.strip()
    return None


def read_hf_token() -> str | None:
    """The configured Hugging Face token, or None.

    Resolution order (highest → lowest precedence):
      1. ``HF_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN`` env vars. The key_manager
         injects the app-stored token (SQLite ``huggingface`` API key) into
         these at startup AND on save, and an explicitly user-set env var
         beats everything else — this is the primary source of truth.
      2. The key_manager's in-memory cache of the app's stored keys — the SAME
         store the env injection above comes from, read directly. Without this,
         HF depends entirely on ``os.environ`` mutation (key_manager's
         deprecated ``_inject`` shim, slated for removal), so it would silently
         lose the user's token the day that shim goes — and it already loses it
         to a startup race, when a download resumes before the injection runs.
         Civitai already reads its key this way; HF now matches.
      3. huggingface_hub's own token store via ``get_token()`` — this picks up
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

    try:
        from app.services.ai.key_manager import get_cached_user_keys  # noqa: PLC0415

        stored = get_cached_user_keys().get("huggingface")
    except Exception:  # noqa: BLE001 — cache unavailable must never break callers
        stored = None
    if stored and stored.strip():
        return stored.strip()

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
