"""Universal download manager.

Manages an intelligent concurrent download queue backed by the local SQLite
database.  All large file downloads (LLM models, Whisper models, image-gen
weights, TTS voices, future file-sync items) funnel through this service so:

- Progress is tracked in one place and streamed to the UI via SSE.
- The queue survives app restarts / crashes (status 'active' → reset to
  'queued' on startup so incomplete downloads are automatically retried).
- Downloads continue even when the Tauri window is closed (the Python engine
  runs as a background sidecar).
- Each download emits fine-grained byte-level progress events (real percent,
  speed, ETA) using httpx's streaming API.
- Up to MAX_CONCURRENT downloads run in parallel; the concurrency limit is
  raised dynamically when measured bandwidth allows it.
- Priority is enforced: higher-priority downloads always start before
  lower-priority ones when a slot is free.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Deque, Literal, Optional, Tuple

import httpx

from pathlib import Path

from app.common.system_logger import get_logger
from app.services.downloads import failures
from app.services.downloads.errors import NonRetryableDownloadError
from app.services.downloads.failures import ActionableDownloadError
from app.services.local_db.database import get_db

logger = get_logger()

DownloadStatus = Literal["queued", "active", "completed", "failed", "cancelled"]
DownloadCategory = Literal["llm", "whisper", "image_gen", "video_gen", "tts", "ner", "file_sync"]

# Maximum number of simultaneous downloads.
MAX_CONCURRENT = 3

# How often (in bytes) we flush a progress event to SSE subscribers.
# At typical LLM download speeds (10–100 MB/s) this is ~10–100 ms between
# events — frequent enough for smooth UI without flooding the SSE connection.
_PROGRESS_CHUNK_BYTES = 512 * 1024  # 512 KB

# Rolling-window speed calculation: keep the last N byte-count samples.
_SPEED_WINDOW_SIZE = 10

# State-log interval (seconds).
_LOG_INTERVAL_S = 15.0

# Bandwidth probe: if measured speed < this fraction of peak, open another slot.
_BANDWIDTH_UTILISATION_THRESHOLD = 0.8

# Bandwidth probe cooldown — only expand slots this often.
_SLOT_EXPAND_COOLDOWN_S = 10.0

# Chunk size for the primary download slot (64 KB), and for secondary slots
# (32 KB) to reduce buffer competition with the primary download.
_PRIMARY_CHUNK_BYTES = 65536
_SECONDARY_CHUNK_BYTES = 32768


@dataclass
class DownloadEntry:
    id: str
    category: str
    filename: str
    display_name: str
    urls: list[str]
    total_bytes: int = 0
    bytes_done: int = 0
    status: str = "queued"
    error_msg: Optional[str] = None
    priority: int = 0
    part_current: int = 1
    part_total: int = 1
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    # Transient: rolling-window speed samples — (monotonic_time, bytes_done)
    _speed_samples: Deque[Tuple[float, int]] = field(default_factory=deque, repr=False, compare=False)

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(100.0, self.bytes_done / self.total_bytes * 100)

    def current_speed_bps(self) -> float:
        """Rolling-window speed in bytes/sec. Returns 0.0 if not enough samples."""
        samples = self._speed_samples
        if len(samples) < 2:
            return 0.0
        dt = samples[-1][0] - samples[0][0]
        if dt <= 0:
            return 0.0
        db = samples[-1][1] - samples[0][1]
        return max(0.0, db / dt)

    def record_sample(self, bytes_done: int) -> None:
        """Record a speed sample at the current monotonic time."""
        now = time.monotonic()
        self._speed_samples.append((now, bytes_done))
        while len(self._speed_samples) > _SPEED_WINDOW_SIZE:
            self._speed_samples.popleft()

    def set_resolution(self, resolution: Optional[dict[str, Any]]) -> None:
        """Attach (or clear) the user-facing fix for a failed download.

        Stored inside `metadata` because that column already round-trips as JSON
        through persistence and hydration — a failure that survives a restart is
        the whole point (the user quits, reopens, and must still be told what to
        do). Read it back off `to_dict()["resolution"]`, never off metadata.
        """
        meta = dict(self.metadata or {})
        if resolution is None:
            meta.pop("resolution", None)
        else:
            meta["resolution"] = resolution
        self.metadata = meta or None

    @property
    def resolution(self) -> Optional[dict[str, Any]]:
        res = (self.metadata or {}).get("resolution")
        return res if isinstance(res, dict) else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_speed_samples", None)
        d["percent"] = self.percent
        d["speed_bps"] = self.current_speed_bps()
        remaining = self.total_bytes - self.bytes_done
        spd = self.current_speed_bps()
        d["eta_seconds"] = (remaining / spd) if (spd > 0 and remaining > 0) else None
        # Hoisted to the top level: the UI's contract is entry.resolution, not
        # a scavenger hunt through metadata.
        d["resolution"] = self.resolution
        return d


@dataclass
class ProgressEvent:
    """Emitted on every meaningful progress tick and status change."""
    id: str
    category: str
    filename: str
    display_name: str
    status: str
    bytes_done: int
    total_bytes: int
    percent: float
    part_current: int
    part_total: int
    speed_bps: float = 0.0
    eta_seconds: Optional[float] = None
    error_msg: Optional[str] = None
    resolution: Optional[dict[str, Any]] = None
    """Set on a user-fixable failure — see app/services/downloads/failures.py.
    The UI renders it as an explain-and-ask dialog; None means a real error."""
    updated_at: str = ""
    bandwidth_bps: float = 0.0

    def to_sse(self) -> str:
        d = asdict(self)
        if not d.get("updated_at"):
            d["updated_at"] = _now()
        payload = json.dumps(d)
        return f"data: {payload}\n\n"


# ------------------------------------------------------------------
# Hugging Face snapshot file filtering
# ------------------------------------------------------------------
#
# A raw HF repo ships FAR more than a diffusers-format load needs: .bin AND
# .safetensors duplicates, fp32 AND fp16 variants, onnx/openvino/flax exports,
# root-level single-file checkpoint re-packagings, and docs/media assets.
# Downloading everything multiplied real download sizes by up to 9x
# (Lightricks/LTX-Video: 254 GB raw vs ~28 GB needed). This filter is the
# single source of truth — it feeds BOTH the total_bytes computation and the
# per-file download loop so they can never disagree.

# File suffixes never needed by `from_pretrained(..., use_safetensors=True)`.
_HF_IGNORE_SUFFIXES: tuple[str, ...] = (
    ".bin", ".onnx", ".onnx_data", ".msgpack", ".h5", ".pt", ".ckpt", ".md",
    # docs/media assets (model cards, sample outputs) — never loaded.
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4",
)
_HF_IGNORE_BASENAMES: tuple[str, ...] = (".gitattributes",)
# Directory names whose entire subtree is skipped (alternate-framework exports
# and documentation assets).
_HF_IGNORE_DIRS: frozenset[str] = frozenset(
    {"flax", "openvino", "onnx", "assets", "examples", "media",
     "vae_decoder", "vae_encoder"}  # vae_*: onnx-only VAE exports (sdxl-turbo)
)


def filter_hf_repo_files(
    files: list[Tuple[str, int]],
    *,
    load_variant: Optional[str] = None,
) -> list[Tuple[str, int]]:
    """Filter an HF repo file listing down to what a diffusers-format load needs.

    Rules:
      1. Global ignore set: alternate weight formats (.bin/.onnx/.msgpack/.h5/
         .pt/.ckpt), docs (.md, .gitattributes, images), and whole
         flax/openvino/onnx/assets/examples/media subfolders.
      2. Root-level ``*.safetensors`` files are single-file checkpoint
         re-packagings (e.g. ``sd_xl_turbo_1.0.safetensors``, the many
         ``ltxv-*.safetensors`` in Lightricks/LTX-Video) — a diffusers-format
         ``from_pretrained(local_dir)`` needs ``model_index.json`` + component
         SUBFOLDERS only, never these duplicates.
      3. When ``load_variant`` is set (e.g. "fp16"): keep variant weight files
         (``*.fp16.safetensors``) and skip a non-variant weight file whenever
         its variant sibling exists in the repo; files without a variant
         sibling are kept as-is. Loading must pass ``variant=load_variant`` to
         ``from_pretrained`` so it reads exactly what was downloaded.
    """
    names = {fn for fn, _ in files}
    kept: list[Tuple[str, int]] = []
    for fn, size in files:
        low = fn.lower()
        parts = low.split("/")
        base = parts[-1]
        if base in _HF_IGNORE_BASENAMES:
            continue
        if low.endswith(_HF_IGNORE_SUFFIXES):
            continue
        if any(d in _HF_IGNORE_DIRS for d in parts[:-1]):
            continue
        if "/" not in fn and low.endswith(".safetensors"):
            continue  # root-level single-file checkpoint duplicate
        if load_variant and low.endswith(".safetensors"):
            token = f".{load_variant.lower()}."
            if token not in base:
                # Skip when the variant sibling exists (plain or sharded form).
                stem = fn[: -len(".safetensors")]
                sibling_plain = f"{stem}.{load_variant}.safetensors"
                # Sharded: "X-00001-of-00005.safetensors" → "X.fp16-00001-of-00005.safetensors"
                sibling_shard = None
                dash = stem.rfind("-0")
                if dash > 0 and "-of-" in stem[dash:]:
                    sibling_shard = (
                        f"{stem[:dash]}.{load_variant}{stem[dash:]}.safetensors"
                    )
                if sibling_plain in names or (sibling_shard and sibling_shard in names):
                    continue
        kept.append((fn, size))
    return kept


async def _classify_hf_auth_failure(
    exc: BaseException, repo_id: str, token: Optional[str]
) -> BaseException:
    """Map a Hugging Face failure to the thing the user must actually do.

    Distinguishing "your token is dead" from "your token is fine but you haven't
    accepted this model's license" is the whole job here — both arrive as a bare
    401, and telling a user to re-enter a key that is already correct is the dead
    end we're removing. The answer comes from the shared key validator (whoami),
    which is the single canonical "is this key real?" path for every provider.

    Only a definitive `invalid` condemns the token. An `unknown` verdict (HF is
    down, the network is out) sends the user to the license page instead —
    harmless, and the far likelier cause.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status not in (401, 403):
        return exc
    if not token:
        return failures.hf_token_missing(repo_id)

    from app.services.ai.key_validation import validate_key  # noqa: PLC0415

    result = await validate_key("huggingface", token)
    if result.verdict == "invalid":
        return failures.hf_token_invalid(repo_id)
    if result.verdict != "valid":
        logger.warning(
            "[downloads] HF token check inconclusive (%s) — assuming the token "
            "is valid and the model gate is unaccepted", result.message,
        )
    # Token is fine → the gate is the problem. HF distinguishes "you never
    # asked" from "you asked and the authors haven't approved yet" only in the
    # error text ("awaiting a review" / "pending"). Different asks: accept the
    # license vs. just wait.
    exc_text = str(exc).lower()
    if "awaiting" in exc_text or "pending" in exc_text:
        return failures.hf_gate_pending(repo_id)
    return failures.hf_gate_not_accepted(repo_id)


def _is_hf_url(url: str) -> bool:
    """True when ``url`` points at the Hugging Face Hub itself (where the
    stored token must be attached) — NOT their signed CDN hosts, which embed
    auth in the URL and must never receive the bearer token."""
    try:
        host = httpx.URL(url).host or ""
    except Exception:  # noqa: BLE001 — malformed URL: just don't attach auth
        return False
    return host in ("huggingface.co", "hf.co", "www.huggingface.co")


def _hf_repo_from_url(url: str) -> Optional[str]:
    """``https://huggingface.co/<org>/<name>/resolve/main/x.gguf`` → ``org/name``."""
    try:
        parts = [p for p in (httpx.URL(url).path or "").split("/") if p]
    except Exception:  # noqa: BLE001
        return None
    if len(parts) >= 2 and parts[0] != "api":
        return f"{parts[0]}/{parts[1]}"
    return None


# SSE subscriber queue type
_Subscriber = asyncio.Queue[str]


class DownloadManager:
    """Concurrent priority-aware download queue manager."""

    def __init__(self) -> None:
        self._entries: dict[str, DownloadEntry] = {}
        # Priority-sorted list of pending IDs.
        # Sort key stored alongside: (neg_priority, created_at, dl_id)
        self._pending: list[Tuple[int, str, str]] = []  # (neg_priority, created_at, id)
        self._pending_ids: set[str] = set()

        self._active_ids: set[str] = set()
        # Start with 1 permit so only 1 download runs initially.
        # _maybe_expand_slots() releases additional permits (up to MAX_CONCURRENT)
        # when bandwidth headroom is detected, growing concurrency incrementally.
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)
        self._active_slots = 1  # current effective concurrency (grows with bandwidth probe)

        self._subscribers: list[_Subscriber] = []
        self._cancel_flags: dict[str, asyncio.Event] = {}
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._log_task: Optional[asyncio.Task[None]] = None
        self._started = False

        # Bandwidth tracking
        self._peak_speed_bps: float = 0.0
        self._last_slot_expand_time: float = 0.0
        self._bandwidth_bps: float = 0.0  # aggregate of all active speeds

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background worker and re-queue any incomplete downloads."""
        if self._started:
            return
        self._started = True
        await self._load_history()
        await self._resume_incomplete()
        self._worker_task = asyncio.create_task(self._worker(), name="download-manager-worker")
        self._log_task = asyncio.create_task(self._periodic_log(), name="download-manager-log")
        logger.info("[downloads] Manager started — MAX_CONCURRENT=%d", MAX_CONCURRENT)

    async def stop(self) -> None:
        """Gracefully stop the worker (called during app shutdown)."""
        for task in (self._worker_task, self._log_task):
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        logger.info("[downloads] Manager stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        *,
        category: str,
        filename: str,
        display_name: str,
        urls: list[str],
        metadata: Optional[dict[str, Any]] = None,
        priority: int = 0,
        download_id: Optional[str] = None,
    ) -> DownloadEntry:
        """Add a download to the priority queue (idempotent by filename+category).

        ``metadata.dest_dir`` is REQUIRED: it is where the downloaded bytes are
        written. The manager previously had no destination concept at all — it
        streamed the bytes, counted them, discarded them, and reported
        "completed". Failing loudly here is the only honest behavior.
        """
        dest_dir = (metadata or {}).get("dest_dir")
        if not dest_dir:
            raise ValueError(
                "engine-side downloads require metadata.dest_dir — "
                "without it the downloaded bytes have nowhere to go"
            )

        # Idempotency: skip if already queued/active/completed for this file
        for entry in self._entries.values():
            if entry.filename == filename and entry.category == category:
                if entry.status in ("queued", "active"):
                    logger.debug("[downloads] Already queued/active: %s", filename)
                    return entry
                if entry.status == "completed":
                    # Completed is only terminal while the file still exists —
                    # a deleted file must be re-downloadable.
                    if (entry.metadata or {}).get("hf_repo_id") or (metadata or {}).get("hf_repo_id"):
                        # HF snapshots land as many files; the marker is the
                        # single source of truth for "still on disk".
                        from app.services.media_gen.paths import DOWNLOAD_COMPLETE_MARKER
                        existing_dest = Path(
                            (entry.metadata or {}).get("dest_dir", dest_dir)
                        ) / DOWNLOAD_COMPLETE_MARKER
                    else:
                        _md = entry.metadata or {}
                        existing_dest = Path(_md.get("dest_dir", dest_dir)) / (
                            _md.get("dest_filename") or entry.filename
                        )
                    if existing_dest.exists():
                        logger.debug("[downloads] Already completed: %s", filename)
                        return entry
                    logger.info(
                        "[downloads] %s marked completed but file is gone — re-downloading",
                        filename,
                    )
                    entry.status = "queued"
                    entry.bytes_done = 0
                    entry.completed_at = None
                    entry.metadata = {**(entry.metadata or {}), "dest_dir": dest_dir}
                    entry.set_resolution(None)  # re-queued: last failure's ask is stale
                    entry.error_msg = None
                    entry.updated_at = _now()
                    self._cancel_flags[entry.id] = asyncio.Event()
                    await self._persist(entry)
                    # CRITICAL: without re-inserting into the pending queue the
                    # worker never dispatches this re-queued entry and the
                    # download hangs in "queued" forever until restart. Mirror
                    # the fresh-entry path.
                    self._insert_pending(entry.id, entry.priority, entry.created_at)
                    return entry

        dl_id = download_id or str(uuid.uuid4())
        now = _now()
        entry = DownloadEntry(
            id=dl_id,
            category=category,
            filename=filename,
            display_name=display_name,
            urls=urls,
            status="queued",
            priority=priority,
            created_at=now,
            updated_at=now,
            metadata=metadata,
        )

        await self._persist(entry)
        self._entries[dl_id] = entry
        self._cancel_flags[dl_id] = asyncio.Event()

        await self._broadcast(ProgressEvent(
            id=dl_id,
            category=category,
            filename=filename,
            display_name=display_name,
            status="queued",
            bytes_done=0,
            total_bytes=0,
            percent=0.0,
            part_current=1,
            part_total=len(urls) or 1,
            updated_at=now,
        ))

        self._insert_pending(dl_id, priority, now)
        logger.info("[downloads] Enqueued: %s (%s) priority=%d", filename, category, priority)
        return entry

    async def cancel(self, download_id: str) -> bool:
        """Request cancellation of a queued or active download."""
        entry = self._entries.get(download_id)
        if not entry:
            return False
        if entry.status not in ("queued", "active"):
            return False

        flag = self._cancel_flags.get(download_id)
        if flag:
            flag.set()

        # Remove from pending if not yet started
        self._remove_pending(download_id)

        entry.status = "cancelled"
        entry.updated_at = _now()
        await self._persist(entry)

        now = _now()
        await self._broadcast(ProgressEvent(
            id=download_id,
            category=entry.category,
            filename=entry.filename,
            display_name=entry.display_name,
            status="cancelled",
            bytes_done=entry.bytes_done,
            total_bytes=entry.total_bytes,
            percent=entry.percent,
            part_current=entry.part_current,
            part_total=entry.part_total,
            updated_at=now,
        ))
        logger.info("[downloads] Cancelled: %s (id=%s)", entry.filename, download_id)
        return True

    def get_all(self, status: Optional[str] = None, category: Optional[str] = None) -> list[DownloadEntry]:
        entries = list(self._entries.values())
        if status:
            entries = [e for e in entries if e.status == status]
        if category:
            entries = [e for e in entries if e.category == category]
        entries.sort(key=lambda e: (
            0 if e.status == "active" else
            1 if e.status == "queued" else
            2 if e.status == "failed" else
            3,
            e.created_at,
        ))
        return entries

    def subscribe(self) -> _Subscriber:
        """Open a new SSE subscription queue."""
        q: _Subscriber = asyncio.Queue(maxsize=500)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: _Subscriber) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def sse_stream(self) -> AsyncIterator[str]:
        """Async generator of SSE-formatted strings for GET /downloads/stream."""
        q = self.subscribe()
        try:
            # Send a snapshot of current state immediately on connect
            for entry in self.get_all():
                spd = entry.current_speed_bps()
                remaining = entry.total_bytes - entry.bytes_done
                eta = (remaining / spd) if (spd > 0 and remaining > 0) else None
                yield ProgressEvent(
                    id=entry.id,
                    category=entry.category,
                    filename=entry.filename,
                    display_name=entry.display_name,
                    status=entry.status,
                    bytes_done=entry.bytes_done,
                    total_bytes=entry.total_bytes,
                    percent=entry.percent,
                    part_current=entry.part_current,
                    part_total=entry.part_total,
                    speed_bps=spd,
                    eta_seconds=eta,
                    error_msg=entry.error_msg,
                    updated_at=entry.updated_at or _now(),
                    bandwidth_bps=self._bandwidth_bps,
                ).to_sse()

            # Yield keep-alive pings every 20s to prevent proxy/browser timeouts
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self.unsubscribe(q)

    # ------------------------------------------------------------------
    # Internal: priority-pending helpers
    # ------------------------------------------------------------------

    def _insert_pending(self, dl_id: str, priority: int, created_at: str) -> None:
        """Insert into the sorted pending list. Higher priority = lower sort key."""
        if dl_id in self._pending_ids:
            return
        key = (-priority, created_at, dl_id)
        bisect.insort(self._pending, key)
        self._pending_ids.add(dl_id)

    def _pop_next_pending(self) -> Optional[str]:
        """Pop the highest-priority pending ID."""
        if not self._pending:
            return None
        key = self._pending.pop(0)
        dl_id = key[2]
        self._pending_ids.discard(dl_id)
        return dl_id

    def _remove_pending(self, dl_id: str) -> None:
        """Remove a specific ID from the pending list (e.g., on cancel)."""
        if dl_id not in self._pending_ids:
            return
        self._pending = [(np, ca, i) for (np, ca, i) in self._pending if i != dl_id]
        self._pending_ids.discard(dl_id)

    # ------------------------------------------------------------------
    # Internal: worker
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        """Dispatcher loop: assigns pending downloads to concurrent slots."""
        while True:
            try:
                # Wait until a slot is free
                await self._semaphore.acquire()
            except asyncio.CancelledError:
                break

            dl_id = self._pop_next_pending()
            if dl_id is None:
                # No work; release slot and wait briefly before checking again
                self._semaphore.release()
                await asyncio.sleep(0.2)
                continue

            entry = self._entries.get(dl_id)
            if not entry or entry.status in ("cancelled", "completed"):
                self._semaphore.release()
                continue

            cancel_flag = self._cancel_flags.get(dl_id, asyncio.Event())
            if cancel_flag.is_set():
                self._semaphore.release()
                continue

            # Determine whether this is the primary (first) slot or a secondary slot
            is_primary = len(self._active_ids) == 0
            chunk_size = _PRIMARY_CHUNK_BYTES if is_primary else _SECONDARY_CHUNK_BYTES

            self._active_ids.add(dl_id)
            entry.status = "active"
            entry.updated_at = _now()
            await self._persist(entry)

            await self._broadcast(ProgressEvent(
                id=dl_id,
                category=entry.category,
                filename=entry.filename,
                display_name=entry.display_name,
                status="active",
                bytes_done=entry.bytes_done,
                total_bytes=entry.total_bytes,
                percent=entry.percent,
                part_current=entry.part_current,
                part_total=entry.part_total,
                updated_at=entry.updated_at,
                bandwidth_bps=self._bandwidth_bps,
            ))

            # Launch download as a separate task so the worker can keep dispatching
            asyncio.create_task(
                self._run_download_slot(entry, cancel_flag, chunk_size),
                name=f"download-{dl_id[:8]}",
            )

    async def _run_download_slot(
        self,
        entry: DownloadEntry,
        cancel_flag: asyncio.Event,
        chunk_size: int,
    ) -> None:
        """Execute a single download and release the semaphore slot when done."""
        dl_id = entry.id
        try:
            await self._download(entry, cancel_flag, chunk_size)
        except Exception as exc:
            if entry.status not in ("cancelled", "completed"):
                entry.status = "failed"
                entry.error_msg = str(exc)
                # A user-fixable failure carries the fix. Stash it on the entry
                # (it persists + rides the SSE event) so the UI can ASK the user
                # for the one thing it needs instead of showing them a 401.
                resolution = (
                    exc.resolution.to_dict()
                    if isinstance(exc, ActionableDownloadError)
                    else None
                )
                entry.set_resolution(resolution)
                entry.updated_at = _now()
                await self._persist(entry)
                await self._broadcast(ProgressEvent(
                    id=dl_id,
                    category=entry.category,
                    filename=entry.filename,
                    display_name=entry.display_name,
                    status="failed",
                    bytes_done=entry.bytes_done,
                    total_bytes=entry.total_bytes,
                    percent=entry.percent,
                    part_current=entry.part_current,
                    part_total=entry.part_total,
                    error_msg=str(exc),
                    resolution=resolution,
                    updated_at=entry.updated_at,
                    bandwidth_bps=self._bandwidth_bps,
                ))
                # A failure the user is expected to resolve is not an error at
                # all — it is a STATE ("token missing", "license not accepted").
                # Log it at INFO with the [action-needed] marker, no stack
                # trace, so it never reads like a crash in the log or the UI.
                if resolution is not None:
                    logger.info(
                        "[downloads] [action-needed] %s (id=%s code=%s) — %s",
                        entry.filename, dl_id, resolution["code"],
                        resolution["message"],
                    )
                else:
                    logger.error(
                        "[downloads] FAILED: %s (id=%s category=%s bytes_done=%d total_bytes=%d part=%d/%d) — %s",
                        entry.filename, dl_id, entry.category,
                        entry.bytes_done, entry.total_bytes,
                        entry.part_current, entry.part_total,
                        exc,
                        exc_info=True,
                    )
        finally:
            self._active_ids.discard(dl_id)
            self._update_bandwidth()
            self._semaphore.release()
            await self._maybe_expand_slots()

    def _update_bandwidth(self) -> None:
        """Recalculate aggregate bandwidth from all currently active entries."""
        total = sum(
            self._entries[i].current_speed_bps()
            for i in self._active_ids
            if i in self._entries
        )
        self._bandwidth_bps = total
        if total > self._peak_speed_bps:
            self._peak_speed_bps = total

    async def _maybe_expand_slots(self) -> None:
        """Expand concurrency by 1 if bandwidth headroom allows it.

        Adds an extra semaphore permit so the dispatcher loop can launch one
        additional concurrent download beyond the initial MAX_CONCURRENT.
        """
        if self._active_slots >= MAX_CONCURRENT:
            return
        if not self._pending:
            return
        now = time.monotonic()
        if now - self._last_slot_expand_time < _SLOT_EXPAND_COOLDOWN_S:
            return
        if self._peak_speed_bps <= 0:
            return

        # Only expand if active bandwidth is below threshold of peak
        if self._bandwidth_bps < _BANDWIDTH_UTILISATION_THRESHOLD * self._peak_speed_bps:
            self._active_slots += 1
            self._last_slot_expand_time = now
            # Release an extra semaphore permit so a new slot can actually be used.
            self._semaphore.release()
            logger.info(
                "[downloads] Expanding concurrency to %d slots "
                "(bandwidth_bps=%.0f peak_bps=%.0f)",
                self._active_slots, self._bandwidth_bps, self._peak_speed_bps,
            )

    async def _download(
        self,
        entry: DownloadEntry,
        cancel_flag: asyncio.Event,
        chunk_size: int,
    ) -> None:
        """Download all URL parts for a single entry with streaming progress."""
        # Hugging Face snapshot downloads (image/video model weights) take a
        # dedicated path: file list + sizes come from the HF API and bytes are
        # fetched with hf_hub_download (resumable), not raw URL streaming.
        if (entry.metadata or {}).get("hf_repo_id"):
            await self._download_hf_snapshot(entry, cancel_flag)
            return

        urls = entry.urls
        if not urls:
            raise ValueError("No URLs provided for download")

        entry.part_total = len(urls)
        total_known = entry.total_bytes

        # Phase 1: HEAD requests to determine total size (best-effort)
        if total_known <= 0:
            total_known = await _probe_total_bytes(urls)
            entry.total_bytes = total_known
            await self._persist_progress(entry)

        bytes_before_this_part = 0
        max_retries = 3

        # Civitai direct downloads authenticate with the stored API key.
        # httpx drops the Authorization header on cross-origin redirects
        # (civitai.com → their signed CDN URL), which is exactly right.
        # The key is never logged.
        request_headers: dict[str, str] = {}
        if (entry.metadata or {}).get("civitai_download"):
            from app.services.media_gen.paths import read_civitai_key  # noqa: PLC0415
            civitai_key = read_civitai_key()
            if civitai_key:
                request_headers["Authorization"] = f"Bearer {civitai_key}"
        elif any(_is_hf_url(u) for u in urls):
            # Hugging Face direct-URL downloads (GGUF weights via resolve/main,
            # wake-word models, …) must carry the stored token exactly like the
            # snapshot path does — resolved AT REQUEST TIME from the app key
            # store (read_hf_token: env-injected app key → key-manager cache →
            # hub cache). Without this, gated GGUF repos 401 with HF's
            # "please log in" page even though the user's token is configured.
            # httpx drops the Authorization header on the cross-origin redirect
            # to HF's signed CDN, which is exactly right.
            from app.services.media_gen.paths import read_hf_token  # noqa: PLC0415
            hf_token = read_hf_token()
            if hf_token:
                request_headers["Authorization"] = f"Bearer {hf_token}"

        async with httpx.AsyncClient(
            follow_redirects=True,
            headers=request_headers,
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0),
        ) as client:
            for part_idx, url in enumerate(urls):
                entry.part_current = part_idx + 1
                entry.updated_at = _now()

                last_error: Optional[Exception] = None
                for attempt in range(max_retries):
                    if cancel_flag.is_set():
                        entry.status = "cancelled"
                        entry.updated_at = _now()
                        await self._persist(entry)
                        return

                    if attempt > 0:
                        wait = 2 ** attempt
                        logger.warning(
                            "[downloads] Retry %d/%d for %s part %d in %ds",
                            attempt + 1, max_retries, entry.filename, part_idx + 1, wait,
                        )
                        await asyncio.sleep(wait)

                    try:
                        part_bytes = await self._download_part(
                            client=client,
                            url=url,
                            entry=entry,
                            bytes_before=bytes_before_this_part,
                            cancel_flag=cancel_flag,
                            chunk_size=chunk_size,
                            dest=self._part_dest(entry, url, part_idx),
                        )
                        bytes_before_this_part += part_bytes
                        last_error = None
                        break
                    except asyncio.CancelledError:
                        raise
                    except NonRetryableDownloadError:
                        # e.g. Civitai 401/403 — retrying cannot fix a missing
                        # or invalid API key; surface the message immediately.
                        raise
                    except Exception as exc:
                        last_error = exc
                        logger.warning(
                            "[downloads] Attempt %d/%d failed for %s part %d: %s",
                            attempt + 1, max_retries, entry.filename, part_idx + 1, exc,
                        )

                if last_error is not None:
                    raise RuntimeError(
                        f"Download failed after {max_retries} attempts. Last error: {last_error}"
                    ) from last_error

        # Custom image models / Civitai LoRAs downloaded via direct URL use
        # the same completion marker the HF snapshot path writes — the
        # image/LoRA services treat weights as installed ONLY when it exists.
        if (entry.metadata or {}).get("write_complete_marker"):
            from app.services.media_gen.paths import DOWNLOAD_COMPLETE_MARKER  # noqa: PLC0415
            marker_dir = Path((entry.metadata or {})["dest_dir"])
            marker_dir.mkdir(parents=True, exist_ok=True)
            (marker_dir / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")

        entry.status = "completed"
        entry.bytes_done = entry.total_bytes if entry.total_bytes > 0 else bytes_before_this_part
        entry.completed_at = _now()
        entry.updated_at = _now()
        await self._persist(entry)

        await self._broadcast(ProgressEvent(
            id=entry.id,
            category=entry.category,
            filename=entry.filename,
            display_name=entry.display_name,
            status="completed",
            bytes_done=entry.bytes_done,
            total_bytes=entry.total_bytes,
            percent=100.0,
            part_current=entry.part_total,
            part_total=entry.part_total,
            updated_at=entry.completed_at,
            bandwidth_bps=self._bandwidth_bps,
        ))
        logger.info(
            "[downloads] Completed: %s (id=%s bytes=%d)",
            entry.filename, entry.id, entry.bytes_done,
        )

    def _part_dest(self, entry: DownloadEntry, url: str, part_idx: int) -> Path:
        """Resolve where a part's bytes land on disk.

        Multi-part downloads (e.g. split GGUF) are distinct files — use each
        URL's basename; single-part downloads use the entry filename.
        """
        md = entry.metadata or {}
        dest_dir = Path(md.get("dest_dir", "."))
        if entry.part_total > 1:
            name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or (
                f"{entry.filename}.part{part_idx + 1}"
            )
        else:
            # dest_filename decouples the on-disk name from the (unique)
            # entry filename: e.g. a Civitai LoRA entry is keyed by its
            # store id, but the weight file must keep its real name.
            name = md.get("dest_filename") or entry.filename
        return dest_dir / name

    async def _download_part(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        entry: DownloadEntry,
        bytes_before: int,
        cancel_flag: asyncio.Event,
        chunk_size: int,
        dest: Path,
    ) -> int:
        """Stream a single URL to ``dest`` (tmp → rename), updating progress.
        Returns the number of bytes downloaded for this part."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        part_bytes_done = 0
        last_emit_bytes = 0
        try:
            with open(tmp, "wb") as fh:
                async with client.stream("GET", url) as response:
                    if (
                        response.status_code in (401, 403)
                        and (entry.metadata or {}).get("civitai_download")
                    ):
                        # Attribute the refusal correctly — three distinct
                        # user-fixable causes, three distinct asks. A blanket
                        # "key required or invalid" told users with a working
                        # key to re-enter it forever.
                        key_present = bool(client.headers.get("authorization"))
                        if not key_present:
                            raise failures.civitai_key_required()
                        if response.status_code == 401:
                            raise failures.civitai_key_rejected()
                        raise failures.civitai_access_restricted(
                            (entry.metadata or {}).get("model_page_url")
                        )
                    if response.status_code in (401, 403) and _is_hf_url(url):
                        # Same attribution contract as the snapshot path: a
                        # gated-repo refusal is a request to the user (accept
                        # the license / add or fix the token), never a raw 401.
                        from app.services.media_gen.paths import read_hf_token  # noqa: PLC0415
                        await response.aread()
                        exc = httpx.HTTPStatusError(
                            f"HTTP {response.status_code} from Hugging Face",
                            request=response.request,
                            response=response,
                        )
                        repo_id = _hf_repo_from_url(url) or entry.display_name
                        raise await _classify_hf_auth_failure(
                            exc, repo_id, read_hf_token()
                        )
                    response.raise_for_status()

                    part_total = int(response.headers.get("content-length", 0))
                    if part_total and entry.total_bytes <= 0:
                        entry.total_bytes = part_total
                        await self._persist_progress(entry)

                    async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                        if cancel_flag.is_set():
                            raise asyncio.CancelledError()

                        fh.write(chunk)
                        part_bytes_done += len(chunk)
                        entry.bytes_done = bytes_before + part_bytes_done
                        entry.updated_at = _now()

                        entry.record_sample(entry.bytes_done)
                        speed_bps = entry.current_speed_bps()

                        # Update aggregate bandwidth
                        self._update_bandwidth()

                        # Emit only once per _PROGRESS_CHUNK_BYTES to avoid flooding
                        if entry.bytes_done - last_emit_bytes >= _PROGRESS_CHUNK_BYTES:
                            last_emit_bytes = entry.bytes_done
                            await self._persist_progress(entry)

                            remaining = entry.total_bytes - entry.bytes_done
                            eta: Optional[float] = None
                            if speed_bps > 0 and remaining > 0:
                                eta = remaining / speed_bps

                            await self._broadcast(ProgressEvent(
                                id=entry.id,
                                category=entry.category,
                                filename=entry.filename,
                                display_name=entry.display_name,
                                status="active",
                                bytes_done=entry.bytes_done,
                                total_bytes=entry.total_bytes,
                                percent=entry.percent,
                                part_current=entry.part_current,
                                part_total=entry.part_total,
                                speed_bps=speed_bps,
                                eta_seconds=eta,
                                updated_at=entry.updated_at,
                                bandwidth_bps=self._bandwidth_bps,
                            ))
        except BaseException:
            # Failed/cancelled part: drop the partial temp file. The retry
            # loop in _download restarts the part from scratch.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        tmp.replace(dest)
        return part_bytes_done

    # ------------------------------------------------------------------
    # Internal: Hugging Face snapshot downloads (model weights)
    # ------------------------------------------------------------------

    async def _download_hf_snapshot(
        self,
        entry: DownloadEntry,
        cancel_flag: asyncio.Event,
    ) -> None:
        """Download a full HF model repo into ``metadata.dest_dir`` with progress.

        Strategy (spec: "snapshot_download doesn't natively emit byte progress"):
          1. ``HfApi.repo_info(files_metadata=True)`` gives the exact file list
             and per-file byte sizes → ``total_bytes`` is known up front.
          2. A worker thread runs a per-file ``hf_hub_download`` loop into
             ``local_dir=dest_dir`` — each file is individually resumable and
             checksum-verified by huggingface_hub.
          3. This coroutine polls the on-disk directory size once a second and
             feeds real byte progress (percent, speed, ETA) into the normal
             broadcast/persist pipeline, so /downloads/stream shows it exactly
             like any other download.
          4. On success a ``.download-complete`` marker is written into the
             model dir — the image/video services treat a model as downloaded
             ONLY when that marker exists.

        Failures are loud: the underlying HF error propagates to the caller,
        which marks the entry ``failed`` with the error message.
        """
        from app.services.media_gen.paths import DOWNLOAD_COMPLETE_MARKER, read_hf_token

        md = entry.metadata or {}
        repo_id: str = md["hf_repo_id"]
        dest_dir = Path(md["dest_dir"])

        try:
            from huggingface_hub import HfApi, hf_hub_download  # noqa: PLC0415
        except ImportError as exc:
            raise failures.ai_packages_missing() from exc

        # Single source of truth for the token — env (app-stored key injected
        # by key_manager, or a user-set HF_TOKEN) with a fallback to
        # huggingface_hub's own cached token store. Passing the resolved token
        # explicitly to HfApi/hf_hub_download avoids the "You are sending
        # unauthenticated requests to the HF Hub" warning whenever a token
        # exists anywhere the hub can see it.
        token = read_hf_token()
        loop = asyncio.get_running_loop()

        # Phase 1: exact file list + sizes from the HF API — FILTERED down to
        # what the diffusers-format load actually needs. The same filtered list
        # feeds total_bytes AND the download loop below (must stay consistent).
        api = HfApi(token=token)
        try:
            info = await loop.run_in_executor(
                None, lambda: api.repo_info(repo_id, files_metadata=True)
            )
        except Exception as exc:
            # repo_info is the FIRST authenticated call, so this is where a
            # gated/auth problem surfaces — turn it into a specific request to
            # the user here, once, instead of letting a raw 401 leak out of the
            # per-file loop below.
            raise await _classify_hf_auth_failure(exc, repo_id, token) from exc
        all_files: list[tuple[str, int]] = [
            (s.rfilename, s.size or 0) for s in (info.siblings or [])
        ]
        if not all_files:
            raise RuntimeError(f"Hugging Face repo {repo_id} lists no files")

        load_variant = md.get("load_variant") or None
        allow_files = md.get("hf_allow_files") or None
        if allow_files:
            # Explicit allowlist (LoRA downloads: exactly the safetensors
            # weight file) — bypasses the diffusers-layout filter, which would
            # otherwise drop root-level *.safetensors as "checkpoint dups".
            allow_set = {str(f) for f in allow_files}
            files = [(fn, size) for fn, size in all_files if fn in allow_set]
            missing = allow_set - {fn for fn, _ in files}
            if missing:
                raise RuntimeError(
                    f"Hugging Face repo {repo_id} does not contain the "
                    f"requested file(s): {', '.join(sorted(missing))}"
                )
        else:
            files = filter_hf_repo_files(all_files, load_variant=load_variant)
        if not files:
            raise RuntimeError(
                f"Hugging Face repo {repo_id}: file filter removed every file "
                f"(variant={load_variant!r}) — the repo layout does not match "
                "the expected diffusers format"
            )
        skipped_bytes = sum(s for _, s in all_files) - sum(s for _, s in files)
        logger.info(
            "[downloads] HF snapshot %s: %d/%d files after filtering "
            "(variant=%s, skipping %.2f GB of alternate formats/duplicates)",
            repo_id, len(files), len(all_files), load_variant or "-",
            skipped_bytes / 1e9,
        )

        entry.total_bytes = sum(size for _, size in files)
        entry.part_total = len(files)
        await self._persist_progress(entry)

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Phase 2: per-file download loop in a worker thread.
        done_evt = threading.Event()
        errors: list[BaseException] = []
        files_done = {"count": 0}

        def _worker() -> None:
            try:
                for fname, _size in files:
                    if cancel_flag.is_set():
                        return
                    hf_hub_download(
                        repo_id=repo_id,
                        filename=fname,
                        local_dir=str(dest_dir),
                        token=token,
                    )
                    files_done["count"] += 1
            except BaseException as exc:  # noqa: BLE001 — surfaced to caller
                errors.append(exc)
            finally:
                done_evt.set()

        worker = threading.Thread(
            target=_worker, daemon=True, name=f"hf-snapshot-{entry.id[:8]}"
        )
        worker.start()

        # Phase 3: poll on-disk bytes and emit progress until the worker exits.
        # Once cancellation is requested (or the entry is otherwise terminal),
        # stop broadcasting hard-coded "active" events — otherwise the poll loop
        # visually reverts the UI back to "downloading" after cancel() already
        # set the entry to cancelled. The between-files check in the worker
        # thread still lets the in-flight file finish; this just stops emitting.
        while not done_evt.is_set():
            if cancel_flag.is_set() or entry.status in ("cancelled", "failed"):
                break
            await asyncio.sleep(1.0)
            if cancel_flag.is_set() or entry.status in ("cancelled", "failed"):
                break
            bytes_now = await loop.run_in_executor(None, _dir_size_bytes, dest_dir)
            entry.bytes_done = (
                min(bytes_now, entry.total_bytes) if entry.total_bytes else bytes_now
            )
            entry.part_current = min(files_done["count"] + 1, entry.part_total)
            entry.updated_at = _now()
            entry.record_sample(entry.bytes_done)
            self._update_bandwidth()
            await self._persist_progress(entry)

            speed_bps = entry.current_speed_bps()
            remaining = entry.total_bytes - entry.bytes_done
            await self._broadcast(ProgressEvent(
                id=entry.id,
                category=entry.category,
                filename=entry.filename,
                display_name=entry.display_name,
                status="active",
                bytes_done=entry.bytes_done,
                total_bytes=entry.total_bytes,
                percent=entry.percent,
                part_current=entry.part_current,
                part_total=entry.part_total,
                speed_bps=speed_bps,
                eta_seconds=(remaining / speed_bps) if (speed_bps > 0 and remaining > 0) else None,
                updated_at=entry.updated_at,
                bandwidth_bps=self._bandwidth_bps,
            ))

        if cancel_flag.is_set():
            # cancel() already broadcast the 'cancelled' event; just record state.
            entry.status = "cancelled"
            entry.updated_at = _now()
            await self._persist(entry)
            return

        if errors:
            # A per-file 401/403 means the same three things a repo_info 401
            # means (dead token / unaccepted gate / no token) — classify it the
            # same way rather than surfacing the raw HF text. Non-auth errors
            # come back unchanged and keep their real message.
            classified = await _classify_hf_auth_failure(errors[0], repo_id, token)
            if isinstance(classified, ActionableDownloadError):
                raise classified from errors[0]
            raise RuntimeError(
                f"Hugging Face download failed for {repo_id}: {errors[0]}"
            ) from errors[0]

        # Phase 4: mark complete — this marker is what makes the model "downloaded".
        (dest_dir / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")

        entry.status = "completed"
        entry.bytes_done = entry.total_bytes
        entry.part_current = entry.part_total
        entry.completed_at = _now()
        entry.updated_at = entry.completed_at
        await self._persist(entry)
        await self._broadcast(ProgressEvent(
            id=entry.id,
            category=entry.category,
            filename=entry.filename,
            display_name=entry.display_name,
            status="completed",
            bytes_done=entry.bytes_done,
            total_bytes=entry.total_bytes,
            percent=100.0,
            part_current=entry.part_total,
            part_total=entry.part_total,
            updated_at=entry.completed_at,
            bandwidth_bps=self._bandwidth_bps,
        ))
        logger.info(
            "[downloads] Completed HF snapshot: %s → %s (%d files, %d bytes)",
            repo_id, dest_dir, len(files), entry.total_bytes,
        )

    # ------------------------------------------------------------------
    # Internal: periodic logging
    # ------------------------------------------------------------------

    async def _periodic_log(self) -> None:
        """Log full queue state every _LOG_INTERVAL_S seconds."""
        while True:
            try:
                await asyncio.sleep(_LOG_INTERVAL_S)
                await self._emit_state_log()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[downloads] Periodic log error: %s", exc)

    async def _emit_state_log(self) -> None:
        """Emit a detailed structured snapshot of the download queue to the log."""
        active_entries = [
            self._entries[i] for i in self._active_ids if i in self._entries
        ]
        queued_entries = [
            e for e in self._entries.values() if e.status == "queued"
        ]
        failed_entries = [
            e for e in self._entries.values() if e.status == "failed"
        ]
        completed_entries = [
            e for e in self._entries.values() if e.status == "completed"
        ]
        cancelled_entries = [
            e for e in self._entries.values() if e.status == "cancelled"
        ]

        active_info = [
            {
                "id": e.id,
                "filename": e.filename,
                "category": e.category,
                "percent": round(e.percent, 1),
                "speed_bps": round(e.current_speed_bps()),
                "bytes_done": e.bytes_done,
                "total_bytes": e.total_bytes,
                "eta_seconds": round(
                    (e.total_bytes - e.bytes_done) / e.current_speed_bps()
                    if e.current_speed_bps() > 0 and e.total_bytes > e.bytes_done else 0
                ),
            }
            for e in active_entries
        ]
        queued_info = [
            {"id": e.id, "filename": e.filename, "priority": e.priority}
            for e in sorted(queued_entries, key=lambda x: (-x.priority, x.created_at))
        ]
        # Failures split by kind: rows carrying a resolution are waiting on the
        # USER (a state, logged as [action-needed] with the code, never the raw
        # error text); rows without one are genuine errors and keep their text.
        action_needed_info = [
            {"id": e.id, "filename": e.filename,
             "code": (e.resolution or {}).get("code")}
            for e in failed_entries if e.resolution is not None
        ]
        failed_info = [
            {"id": e.id, "filename": e.filename, "error_msg": e.error_msg}
            for e in failed_entries if e.resolution is None
        ]

        logger.info(
            "[downloads] STATE | active=%d queued=%d completed=%d fails=%d cancelled=%d "
            "bandwidth_bps=%.0f peak_bps=%.0f active_slots=%d max_concurrent=%d | "
            "active=%s queued=%s fails=%s action_needed=%s",
            len(active_entries),
            len(queued_entries),
            len(completed_entries),
            len(failed_entries),
            len(cancelled_entries),
            self._bandwidth_bps,
            self._peak_speed_bps,
            self._active_slots,
            MAX_CONCURRENT,
            json.dumps(active_info),
            json.dumps(queued_info),
            json.dumps(failed_info),
            json.dumps(action_needed_info),
        )

    # ------------------------------------------------------------------
    # Internal: persistence
    # ------------------------------------------------------------------

    async def _persist(self, entry: DownloadEntry) -> None:
        """Write or update the full row for an entry."""
        try:
            db = get_db()
            await db.execute(
                """
                INSERT INTO downloads
                    (id, category, filename, display_name, urls,
                     total_bytes, bytes_done, status, error_msg,
                     priority, part_current, part_total,
                     created_at, updated_at, completed_at, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status       = excluded.status,
                    bytes_done   = excluded.bytes_done,
                    total_bytes  = excluded.total_bytes,
                    error_msg    = excluded.error_msg,
                    part_current = excluded.part_current,
                    part_total   = excluded.part_total,
                    updated_at   = excluded.updated_at,
                    completed_at = excluded.completed_at,
                    metadata     = excluded.metadata
                """,
                (
                    entry.id,
                    entry.category,
                    entry.filename,
                    entry.display_name,
                    json.dumps(entry.urls),
                    entry.total_bytes,
                    entry.bytes_done,
                    entry.status,
                    entry.error_msg,
                    entry.priority,
                    entry.part_current,
                    entry.part_total,
                    entry.created_at or _now(),
                    entry.updated_at or _now(),
                    entry.completed_at,
                    json.dumps(entry.metadata) if entry.metadata else None,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.warning(
                "[downloads] DB persist FAILED for %s (id=%s): %s",
                entry.filename, entry.id, exc, exc_info=True,
            )

    async def _persist_progress(self, entry: DownloadEntry) -> None:
        """Lightweight progress-only update (only writes byte counters + status)."""
        try:
            db = get_db()
            await db.execute(
                "UPDATE downloads SET bytes_done=?, total_bytes=?, part_current=?, updated_at=? WHERE id=?",
                (entry.bytes_done, entry.total_bytes, entry.part_current, _now(), entry.id),
            )
            await db.commit()
        except Exception as exc:
            logger.warning(
                "[downloads] DB progress update FAILED for %s (id=%s): %s",
                entry.filename, entry.id, exc, exc_info=True,
            )

    async def _resume_incomplete(self) -> None:
        """On startup, reset 'active' rows to 'queued' and re-add them to the priority queue."""
        try:
            db = get_db()
            rows = await db.fetchall(
                "SELECT * FROM downloads WHERE status IN ('queued', 'active') ORDER BY priority DESC, created_at ASC"
            )
            for row in rows:
                dl_id = row["id"]
                urls = json.loads(row["urls"] or "[]")
                metadata_raw = row["metadata"]
                metadata = json.loads(metadata_raw) if metadata_raw else None
                entry = DownloadEntry(
                    id=dl_id,
                    category=row["category"],
                    filename=row["filename"],
                    display_name=row["display_name"],
                    urls=urls,
                    total_bytes=row["total_bytes"],
                    bytes_done=0,  # Restart from beginning (no range-request resume yet)
                    status="queued",
                    error_msg=None,
                    priority=row["priority"],
                    part_current=1,
                    part_total=row["part_total"],
                    created_at=row["created_at"],
                    updated_at=_now(),
                    metadata=metadata,
                )
                # Reset to queued in DB
                await db.execute(
                    "UPDATE downloads SET status='queued', bytes_done=0, part_current=1, updated_at=? WHERE id=?",
                    (_now(), dl_id),
                )
                self._entries[dl_id] = entry
                self._cancel_flags[dl_id] = asyncio.Event()
                self._insert_pending(dl_id, row["priority"], row["created_at"])
                logger.info("[downloads] Resuming incomplete download: %s (priority=%d)", entry.filename, row["priority"])

            await db.commit()
        except Exception as exc:
            logger.warning("[downloads] Failed to resume incomplete downloads: %s", exc, exc_info=True)

    async def _load_history(self) -> None:
        """Load completed/failed/cancelled history rows for the UI (last 50).

        Failed rows written before the actionable-failure taxonomy existed
        carry only a raw ``error_msg`` and replayed as red errors on every
        start. Re-triage them here: a recognizable user-fixable message gets a
        ``resolution`` attached (persisted), so old rows render through the
        same prompt UI as new failures.
        """
        # Key presence read ONCE, from the same request-time sources the
        # download paths use — attribution must never claim "add your token"
        # to a user whose token is configured.
        from app.services.media_gen.paths import read_civitai_key, read_hf_token  # noqa: PLC0415
        hf_token_present = read_hf_token() is not None
        civitai_key_present = read_civitai_key() is not None
        try:
            db = get_db()
            rows = await db.fetchall(
                "SELECT * FROM downloads WHERE status IN ('completed','failed','cancelled') ORDER BY updated_at DESC LIMIT 50"
            )
            for row in rows:
                if row["id"] in self._entries:
                    continue
                urls = json.loads(row["urls"] or "[]")
                metadata_raw = row["metadata"]
                metadata = json.loads(metadata_raw) if metadata_raw else None
                entry = DownloadEntry(
                    id=row["id"],
                    category=row["category"],
                    filename=row["filename"],
                    display_name=row["display_name"],
                    urls=urls,
                    total_bytes=row["total_bytes"],
                    bytes_done=row["bytes_done"],
                    status=row["status"],
                    error_msg=row["error_msg"],
                    priority=row["priority"],
                    part_current=row["part_current"],
                    part_total=row["part_total"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    completed_at=row["completed_at"],
                    metadata=metadata,
                )
                if entry.status == "failed" and entry.resolution is None:
                    triaged = failures.retriage_stale_failure(
                        entry.error_msg,
                        entry.metadata,
                        hf_token_present=hf_token_present,
                        civitai_key_present=civitai_key_present,
                    )
                    if triaged is not None:
                        entry.set_resolution(triaged.to_dict())
                        await self._persist(entry)
                        logger.info(
                            "[downloads] [action-needed] re-triaged stale failure "
                            "%s (id=%s) → %s",
                            entry.filename, entry.id, triaged.code,
                        )
                self._entries[row["id"]] = entry
        except Exception as exc:
            logger.warning("[downloads] Failed to load history: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Internal: SSE broadcast
    # ------------------------------------------------------------------

    async def _broadcast(self, event: ProgressEvent) -> None:
        msg = event.to_sse()
        dead: list[_Subscriber] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Subscriber queue full — drop oldest message and retry
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    dead.append(q)
        for q in dead:
            self.unsubscribe(q)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _dir_size_bytes(root: Path) -> int:
    """Recursive on-disk size of ``root`` (includes hf .incomplete files,
    which is exactly what we want for live download progress)."""
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


async def _probe_total_bytes(urls: list[str]) -> int:
    """HEAD each URL and sum the content-length values to get total expected bytes."""
    total = 0
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            for url in urls:
                try:
                    resp = await client.head(url)
                    cl = int(resp.headers.get("content-length", 0))
                    total += cl
                except Exception:
                    pass
    except Exception:
        pass
    return total


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_instance: Optional[DownloadManager] = None


def get_download_manager() -> DownloadManager:
    global _instance
    if _instance is None:
        _instance = DownloadManager()
    return _instance
