"""On-disk gallery thumbnails for the media library.

Self-healing contract
---------------------
There is no separate backfill job. A thumb either exists beside its media
file (``<uuid>.thumb.jpg``) or it does not. Every read path goes through
``ensure_thumb`` / ``thumb_bytes_for_item``:

  - cache hit  → return the JPEG
  - cache miss → load the full media, resize, write ``.thumb.jpg``, return it

If a thumb is deleted, corrupt, or never written (crash between save and
thumb generation, older libraries), the next request regenerates it. That
is intentional — the gallery never depends on a one-shot migration.

Layout (alongside the media + sidecar)::

    ~/.matrx/media/generated/images/<uuid>.png
    ~/.matrx/media/generated/images/<uuid>.json
    ~/.matrx/media/generated/images/<uuid>.thumb.jpg   ← this module

Videos use the same ``.thumb.jpg`` name (first-frame poster).

Vaulted items have no plaintext files on disk. For those, we generate a
JPEG in memory from the decrypted bytes and do NOT write a plaintext
thumb beside the vault — that would defeat encryption.
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Longest edge for gallery / filmstrip / queue tiles. Lightbox still uses
# the full original via /media-library/file/{id}.
THUMB_MAX_EDGE = 320
THUMB_JPEG_QUALITY = 85
THUMB_SUFFIX = ".thumb.jpg"

# Per-item locks so two concurrent GETs for a missing thumb don't both
# decode the full PNG and race the write.
_item_locks: dict[str, threading.Lock] = {}
_item_locks_guard = threading.Lock()


def _lock_for(item_id: str) -> threading.Lock:
    with _item_locks_guard:
        lock = _item_locks.get(item_id)
        if lock is None:
            lock = threading.Lock()
            _item_locks[item_id] = lock
        return lock


def thumb_path_for_media(media_path: Path) -> Path:
    """``…/<uuid>.png`` → ``…/<uuid>.thumb.jpg`` (same for ``.mp4``)."""
    return media_path.with_suffix("").with_name(media_path.stem + THUMB_SUFFIX)


def _pil_thumb_jpeg(image_bytes: bytes) -> bytes:
    from PIL import Image  # noqa: PLC0415 — pillow is a core dep

    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        img.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=THUMB_JPEG_QUALITY, optimize=True)
        return out.getvalue()


def _video_poster_jpeg(video_path: Path) -> bytes:
    """First decodable frame → JPEG. Raises on failure (caller logs)."""
    import cv2  # noqa: PLC0415 — opencv is a core dep
    from PIL import Image  # noqa: PLC0415

    cap = cv2.VideoCapture(str(video_path))
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"could not decode a frame from {video_path.name}")
    # OpenCV is BGR.
    rgb = frame[:, :, ::-1]
    img = Image.fromarray(rgb)
    img.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=THUMB_JPEG_QUALITY, optimize=True)
    return out.getvalue()


def generate_thumb_bytes(
    *, media_type: str, media_path: Path | None = None, image_bytes: bytes | None = None
) -> bytes:
    """Build JPEG thumb bytes from a file on disk or in-memory image bytes."""
    if media_type == "image":
        raw = image_bytes if image_bytes is not None else None
        if raw is None:
            if media_path is None:
                raise ValueError("image thumb needs media_path or image_bytes")
            raw = media_path.read_bytes()
        return _pil_thumb_jpeg(raw)
    if media_type == "video":
        if media_path is None:
            raise ValueError("video thumb needs media_path")
        return _video_poster_jpeg(media_path)
    raise ValueError(f"Unknown media_type for thumb: {media_type!r}")


def ensure_thumb(meta: dict[str, Any]) -> Path:
    """Return the on-disk thumb path, generating it if missing/empty.

    ``meta`` is a library sidecar dict (must include ``id``, ``media_type``,
    ``file_path``). Raises on generation failure — callers turn that into
    an HTTP error so the UI keeps its placeholder and can retry later.
    """
    item_id = str(meta["id"])
    media_path = Path(meta["file_path"])
    dest = thumb_path_for_media(media_path)

    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    with _lock_for(item_id):
        # Re-check under the lock — another request may have won the race.
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        jpeg = generate_thumb_bytes(
            media_type=str(meta["media_type"]),
            media_path=media_path,
        )
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(jpeg)
        tmp.replace(dest)
        logger.info(
            "[media_thumbs] wrote %s (%d bytes) for %s",
            dest.name,
            len(jpeg),
            item_id,
        )
        return dest


def write_thumb_best_effort(meta: dict[str, Any]) -> None:
    """Generate a thumb right after a successful save. Never raises.

    Failures are loud in the log; the next GET /thumb will self-heal.
    """
    try:
        ensure_thumb(meta)
    except Exception as exc:  # noqa: BLE001 — save must not fail on thumb
        logger.error(
            "[media_thumbs] Could not write thumb for %s after save: %s — "
            "gallery will self-heal on next thumb request",
            meta.get("id"),
            exc,
        )


def delete_thumb_for_media(media_path: Path) -> None:
    """Remove the sibling thumb when the media file is deleted/vaulted."""
    thumb = thumb_path_for_media(media_path)
    thumb.unlink(missing_ok=True)
    # Leftover temp from a crashed write.
    thumb.with_suffix(thumb.suffix + ".tmp").unlink(missing_ok=True)


def thumb_bytes_for_vault_image(image_bytes: bytes) -> bytes:
    """In-memory thumb for an unlocked vault image (never written plaintext)."""
    return _pil_thumb_jpeg(image_bytes)
