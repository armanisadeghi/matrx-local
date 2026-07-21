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

# The source image an img2img / image-to-video generation started from, saved
# beside its result so "Remix" can restore the input, not just the settings.
_INIT_IMAGE_SUFFIX = ".init.png"

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


def _init_image_path(directory: Path, item_id: str) -> Path:
    """Where an item's source (img2img / image-to-video) input image lives."""
    return directory / f"{item_id}{_INIT_IMAGE_SUFFIX}"


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
    init_image_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Write the media file + sidecar atomically enough (media first, sidecar
    last — a crash between the two leaves an orphan media file that list()
    ignores, never a sidecar pointing at nothing).

    ``init_image_bytes`` (the img2img / image-to-video source image) is stored
    ALONGSIDE the result as ``<item_id>.init.png``. Without it, "Remix" — which
    promises to reproduce exactly what made this image — could restore every
    setting except the one input that mattered most.
    """
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

        init_name: str | None = None
        if init_image_bytes:
            # Best-effort ON PURPOSE: the result is already on disk. Storing the
            # source image is a convenience for Remix — it must never turn a
            # successful generation into a failed one (e.g. ENOSPC writing this
            # extra copy). The failure is LOUD in the log and the sidecar simply
            # records no source image, so Remix tells the truth about it.
            try:
                init_path = _init_image_path(directory, item_id)
                init_path.write_bytes(init_image_bytes)
                init_name = init_path.name
            except OSError as exc:
                logger.error(
                    "[media_library] Could not store the input image for %s: %s — "
                    "the generation is kept; Remix will not be able to restore "
                    "its source image",
                    item_id,
                    exc,
                )

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
            "init_image_file": init_name,
        }
        if media_type == "video":
            meta["num_frames"] = num_frames
            meta["fps"] = fps

        sidecar = directory / f"{item_id}.json"
        sidecar.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    logger.info(
        "[media_library] Saved %s item %s (%s, %d bytes)",
        media_type,
        item_id,
        model_id,
        meta["file_size_bytes"],
    )
    meta["file_path"] = str(media_path)
    # Best-effort gallery thumb. Failure is fine — GET /thumb self-heals.
    from app.services.media_gen.thumbs import write_thumb_best_effort  # noqa: PLC0415

    write_thumb_best_effort(meta)
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
    init_image_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Persist a generated PNG + sidecar. Returns the sidecar metadata
    (including ``file_path``). Raises on failure — callers surface it loudly.

    ``init_image_bytes`` is the img2img source image; it is saved beside the
    result so Remix can restore the exact input.
    """
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
        init_image_bytes=init_image_bytes,
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
            path,
            meta.get("file_name"),
        )
        return None
    meta["file_path"] = str(media_path)
    # An init image recorded but no longer on disk must not advertise itself as
    # remixable — the client would get a 404 on a button it was told to show.
    init_name = meta.get("init_image_file")
    if init_name and not (path.parent / str(init_name)).exists():
        meta["init_image_file"] = None
    meta.setdefault("init_image_file", None)
    return meta


def get_init_image_path(item_id: str) -> Path | None:
    """The stored img2img source image for an item, or None when it has none."""
    meta = get_item(item_id)
    if meta is None or not meta.get("init_image_file"):
        return None
    path = Path(meta["file_path"]).parent / str(meta["init_image_file"])
    return path if path.exists() else None


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


def list_revision_branch(root_item_id: str) -> list[dict[str, Any]]:
    """Every image in a linear revision branch, root-first (oldest → newest).

    Branch membership is ``id == root_item_id`` or
    ``params.revision_root_item_id == root_item_id``. Ordering walks the
    ``revision_parent_item_id`` chain from the root; items that cannot be
    linked (orphans) are appended by ``created_at``.
    """
    if not is_valid_item_id(root_item_id):
        return []

    root_meta = get_item(root_item_id)
    if root_meta is None or root_meta.get("media_type") != "image":
        return []

    members: dict[str, dict[str, Any]] = {root_item_id: root_meta}
    directory = media_type_dir("image")
    if directory.is_dir():
        for sidecar in directory.glob("*.json"):
            meta = _load_sidecar(sidecar)
            if meta is None:
                continue
            item_id = meta.get("id")
            if not isinstance(item_id, str) or item_id == root_item_id:
                continue
            params = meta.get("params")
            if not isinstance(params, dict):
                continue
            if params.get("revision_root_item_id") == root_item_id:
                members[item_id] = meta

    ordered: list[dict[str, Any]] = [root_meta]
    current_id = root_item_id
    linked: set[str] = {root_item_id}
    while True:
        child: dict[str, Any] | None = None
        for item_id, meta in members.items():
            if item_id in linked:
                continue
            params = meta.get("params")
            if not isinstance(params, dict):
                continue
            if params.get("revision_parent_item_id") == current_id:
                child = meta
                break
        if child is None:
            break
        child_id = str(child["id"])
        ordered.append(child)
        linked.add(child_id)
        current_id = child_id

    orphans = [meta for item_id, meta in members.items() if item_id not in linked]
    orphans.sort(key=lambda m: str(m.get("created_at", "")))
    ordered.extend(orphans)
    return ordered


def delete_item(item_id: str) -> bool:
    """Delete an item's media file, sidecar AND stored init image. False when
    the id is unknown.

    The init image goes with it — both on a plain delete and on a vault move
    (which deletes the plaintext original after encrypting it). Leaving the
    source image behind after the user deleted or vaulted the result would be a
    plaintext leak of exactly the content they asked us to remove.
    """
    if not is_valid_item_id(item_id):
        return False
    with _lock:
        for t, ext in _MEDIA_EXT.items():
            directory = media_type_dir(t)
            sidecar = directory / f"{item_id}.json"
            media = directory / f"{item_id}{ext}"
            if sidecar.exists() or media.exists():
                from app.services.media_gen.thumbs import (  # noqa: PLC0415
                    delete_thumb_for_media,
                )

                delete_thumb_for_media(media)
                sidecar.unlink(missing_ok=True)
                media.unlink(missing_ok=True)
                _init_image_path(directory, item_id).unlink(missing_ok=True)
                logger.info("[media_library] Deleted item %s (%s)", item_id, t)
                return True
    return False
