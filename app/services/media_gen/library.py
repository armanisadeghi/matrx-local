"""Persistent library of generated media (images + videos).

Layout (see paths.generated_media_dir):

    ~/.matrx/media/generated/images/<uuid>.png  + <uuid>.json
    ~/.matrx/media/generated/videos/<uuid>.mp4  + <uuid>.json

Each item is a media file plus a JSON sidecar carrying the FULL generation
record: id, media_type, model_id, prompt, negative_prompt, params (the exact
resolved kwargs passed to the diffusers pipeline, extra_params included),
seed, dimensions, num_frames/fps (video), elapsed_seconds, created_at (UTC
ISO), and file size. The /media-library API serves this library; the image
and video services write into it automatically after every successful
generation.

Filesystem-as-database on purpose: items are independent file pairs, listing
is a sidecar scan (fine at the scale of local generation), and deleting an
item is deleting two files. No index to corrupt.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from app.common.system_logger import get_logger
from app.services.media_gen.paths import generated_media_dir

logger = get_logger()

MediaType = Literal["image", "video"]

_MEDIA_EXT: dict[str, str] = {"image": ".png", "video": ".mp4"}
_MEDIA_CONTENT_TYPE: dict[str, str] = {"image": "image/png", "video": "video/mp4"}

# uuid4 shape — item ids are validated before ANY filesystem lookup so a
# crafted id can never traverse outside the library dirs.
_ITEM_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# Serializes concurrent writes/deletes (image generations run in a thread
# pool; the video worker is its own thread).
_lock = threading.Lock()


def media_type_dir(media_type: str) -> Path:
    if media_type not in _MEDIA_EXT:
        raise ValueError(f"Unknown media_type: {media_type!r}")
    return generated_media_dir() / f"{media_type}s"


def content_type_for(media_type: str) -> str:
    return _MEDIA_CONTENT_TYPE[media_type]


def is_valid_item_id(item_id: str) -> bool:
    return bool(_ITEM_ID_RE.match(item_id))


def json_sanitize(value: Any) -> Any:
    """Recursively convert a value into something json.dumps accepts.

    Non-serializable leaves (torch.Generator, PIL images, callbacks, …) become
    their repr string — the sidecar must record every param that was passed,
    even the ones that only make sense in-process.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): json_sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_sanitize(v) for v in value]
    return repr(value)


def _write_item(
    *,
    media_type: MediaType,
    media_bytes: bytes | None,
    src_file: Path | None,
    model_id: str,
    prompt: str,
    negative_prompt: str,
    params: Mapping[str, Any],
    seed: int | None,
    width: int,
    height: int,
    elapsed_seconds: float,
    num_frames: int | None = None,
    fps: int | None = None,
) -> dict[str, Any]:
    """Write the media file + sidecar atomically enough (media first, sidecar
    last — a crash between the two leaves an orphan media file that list()
    ignores, never a sidecar pointing at nothing)."""
    item_id = str(uuid.uuid4())
    ext = _MEDIA_EXT[media_type]
    directory = media_type_dir(media_type)

    with _lock:
        directory.mkdir(parents=True, exist_ok=True)
        media_path = directory / f"{item_id}{ext}"
        if media_bytes is not None:
            media_path.write_bytes(media_bytes)
        else:
            assert src_file is not None
            # Move into the library (same volume under ~/.matrx → atomic rename).
            shutil.move(str(src_file), str(media_path))

        meta: dict[str, Any] = {
            "id": item_id,
            "media_type": media_type,
            "model_id": model_id,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "params": json_sanitize(dict(params)),
            "seed": seed,
            "width": width,
            "height": height,
            "elapsed_seconds": round(float(elapsed_seconds), 2),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file_name": media_path.name,
            "file_size_bytes": media_path.stat().st_size,
        }
        if media_type == "video":
            meta["num_frames"] = num_frames
            meta["fps"] = fps

        sidecar = directory / f"{item_id}.json"
        sidecar.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    logger.info(
        "[media_library] Saved %s item %s (%s, %d bytes)",
        media_type, item_id, model_id, meta["file_size_bytes"],
    )
    meta["file_path"] = str(media_path)
    return meta


def save_generated_image(
    png_bytes: bytes,
    *,
    model_id: str,
    prompt: str,
    negative_prompt: str,
    params: Mapping[str, Any],
    seed: int | None,
    width: int,
    height: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Persist a generated PNG + sidecar. Returns the sidecar metadata
    (including ``file_path``). Raises on failure — callers surface it loudly."""
    return _write_item(
        media_type="image",
        media_bytes=png_bytes,
        src_file=None,
        model_id=model_id,
        prompt=prompt,
        negative_prompt=negative_prompt,
        params=params,
        seed=seed,
        width=width,
        height=height,
        elapsed_seconds=elapsed_seconds,
    )


def save_generated_video(
    src_file: Path,
    *,
    model_id: str,
    prompt: str,
    negative_prompt: str,
    params: Mapping[str, Any],
    seed: int | None,
    width: int,
    height: int,
    num_frames: int,
    fps: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """MOVE an encoded mp4 into the library + write its sidecar. Returns the
    sidecar metadata (including the new ``file_path``)."""
    return _write_item(
        media_type="video",
        media_bytes=None,
        src_file=src_file,
        model_id=model_id,
        prompt=prompt,
        negative_prompt=negative_prompt,
        params=params,
        seed=seed,
        width=width,
        height=height,
        num_frames=num_frames,
        fps=fps,
        elapsed_seconds=elapsed_seconds,
    )


def _load_sidecar(path: Path) -> dict[str, Any] | None:
    """Load one sidecar; a corrupt file is logged loudly and skipped so one
    bad item can't brick the whole library listing."""
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or "id" not in meta or "media_type" not in meta:
            raise ValueError("sidecar missing required fields")
    except Exception as exc:  # noqa: BLE001 — skip-and-scream, never crash the list
        logger.error("[media_library] Corrupt sidecar %s: %s", path, exc)
        return None
    media_path = path.parent / str(meta.get("file_name", ""))
    if not meta.get("file_name") or not media_path.exists():
        logger.error(
            "[media_library] Sidecar %s points at missing media file %r — skipping",
            path, meta.get("file_name"),
        )
        return None
    meta["file_path"] = str(media_path)
    return meta


def list_items(
    media_type: str | None = None, *, limit: int = 50, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """Newest-first sidecar metadata. Returns (page, total_matching)."""
    if media_type is not None and media_type not in _MEDIA_EXT:
        raise ValueError(f"Unknown media_type: {media_type!r}")
    types = [media_type] if media_type else list(_MEDIA_EXT)

    items: list[dict[str, Any]] = []
    for t in types:
        directory = media_type_dir(t)
        if not directory.is_dir():
            continue
        for sidecar in directory.glob("*.json"):
            meta = _load_sidecar(sidecar)
            if meta is not None:
                items.append(meta)

    items.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
    total = len(items)
    return items[offset : offset + limit], total


def get_item(item_id: str) -> dict[str, Any] | None:
    """Sidecar metadata (+file_path) for one item, or None when unknown."""
    if not is_valid_item_id(item_id):
        return None
    for t in _MEDIA_EXT:
        sidecar = media_type_dir(t) / f"{item_id}.json"
        if sidecar.exists():
            return _load_sidecar(sidecar)
    return None


def delete_item(item_id: str) -> bool:
    """Delete an item's media file + sidecar. False when the id is unknown."""
    if not is_valid_item_id(item_id):
        return False
    with _lock:
        for t, ext in _MEDIA_EXT.items():
            directory = media_type_dir(t)
            sidecar = directory / f"{item_id}.json"
            media = directory / f"{item_id}{ext}"
            if sidecar.exists() or media.exists():
                sidecar.unlink(missing_ok=True)
                media.unlink(missing_ok=True)
                logger.info("[media_library] Deleted item %s (%s)", item_id, t)
                return True
    return False
