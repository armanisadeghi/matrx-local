"""Video generation job records.

One generation job runs at a time (video generation saturates the GPU/ANE —
queueing a second would OOM). Job state lives in memory for live polling and
the last ``_HISTORY_LIMIT`` jobs are persisted to
``~/.matrx/generated/videos/jobs.json`` so a restart doesn't lose history.

The job store is deliberately simple: a dict guarded by a threading.Lock —
progress updates come from the generation worker thread (diffusers step
callback), reads come from the FastAPI event loop.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from app.common.system_logger import get_logger
from app.services.media_gen.paths import generated_videos_dir

logger = get_logger()

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

_HISTORY_LIMIT = 50


@dataclass
class VideoJob:
    job_id: str
    prompt: str
    model_id: str
    status: JobStatus = "queued"
    progress: float = 0.0
    """0..1."""
    current_step: int = 0
    total_steps: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None
    output_path: str | None = None
    item_id: str | None = None
    """Media-library item id — completed jobs are persisted to
    ~/.matrx/media/generated/videos/ (see app/services/media_gen/library.py)."""
    width: int = 0
    height: int = 0
    num_frames: int = 0
    fps: int = 0
    seed: int | None = None
    cancel_requested: bool = False
    """True the instant a cancel lands on a running job. The abort takes
    effect at the NEXT denoising step — video steps can take tens of seconds
    each on MPS, so the UI shows "Cancelling…" off this flag until status
    flips to "cancelled" (partial progress preserved on the record)."""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Live elapsed for running jobs
        if self.status == "running" and self.started_at:
            d["elapsed_seconds"] = round(time.time() - self.started_at, 1)
        return d


class VideoJobStore:
    """Thread-safe job registry with JSON-file history persistence."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, VideoJob] = {}
        self._order: list[str] = []  # newest last
        self._active_job_id: str | None = None
        self._loaded = False

    # ── persistence ───────────────────────────────────────────────────────────

    def _history_path(self) -> Path:
        return generated_videos_dir() / "jobs.json"

    def load(self) -> None:
        """Load persisted history (idempotent; call once at service init)."""
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            path = self._history_path()
            if not path.exists():
                return
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for item in raw:
                    job = VideoJob(**item)
                    # A job that was mid-flight when the engine died is failed —
                    # never resurrect it as "running".
                    if job.status in ("queued", "running"):
                        job.status = "failed"
                        job.error = "Engine restarted while the job was in flight"
                    self._jobs[job.job_id] = job
                    self._order.append(job.job_id)
                logger.info("[video_gen] Loaded %d job(s) from history", len(raw))
            except Exception as exc:
                logger.warning("[video_gen] Could not load jobs.json: %s", exc)

    def _persist_locked(self) -> None:
        try:
            path = self._history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            recent = [self._jobs[j].to_dict() for j in self._order[-_HISTORY_LIMIT:]]
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(recent, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            logger.warning("[video_gen] Could not persist jobs.json: %s", exc)

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def active_job_id(self) -> str | None:
        with self._lock:
            return self._active_job_id

    def create(self, **kwargs) -> VideoJob:
        """Create a queued job. Raises RuntimeError when one is already active."""
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs.get(self._active_job_id)
                if active and active.status in ("queued", "running"):
                    raise RuntimeError(
                        f"A video job is already running (job_id={self._active_job_id}). "
                        "Only one generation runs at a time."
                    )
                self._active_job_id = None

            job = VideoJob(job_id=str(uuid.uuid4()), **kwargs)
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._active_job_id = job.job_id
            self._persist_locked()
            return job

    def get(self, job_id: str) -> VideoJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[VideoJob]:
        with self._lock:
            return [self._jobs[j] for j in reversed(self._order[-limit:])]

    def mark_running(self, job_id: str, total_steps: int) -> bool:
        """False when the job was cancelled between create and worker start —
        the worker must skip it instead of resurrecting it."""
        with self._lock:
            job = self._jobs[job_id]
            if job.status != "queued":
                return False
            job.status = "running"
            job.started_at = time.time()
            job.total_steps = total_steps
            self._persist_locked()
            return True

    def update_progress(self, job_id: str, current_step: int, total_steps: int) -> None:
        """Called from the diffusers step callback (worker thread). No disk IO —
        this fires every step and must stay cheap."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.current_step = current_step
            job.total_steps = total_steps
            job.progress = min(1.0, current_step / total_steps) if total_steps else 0.0
            if job.started_at:
                job.elapsed_seconds = round(time.time() - job.started_at, 1)

    def mark_completed(
        self, job_id: str, output_path: str, item_id: str | None = None
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.progress = 1.0
            job.output_path = output_path
            job.item_id = item_id
            job.finished_at = time.time()
            if job.started_at:
                job.elapsed_seconds = round(job.finished_at - job.started_at, 1)
            if self._active_job_id == job_id:
                self._active_job_id = None
            self._persist_locked()

    def mark_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.error = error
            job.finished_at = time.time()
            if job.started_at:
                job.elapsed_seconds = round(job.finished_at - job.started_at, 1)
            if self._active_job_id == job_id:
                self._active_job_id = None
            self._persist_locked()

    def mark_cancelled(self, job_id: str) -> None:
        """Terminal cancelled state after a mid-flight abort. Partial progress
        (current_step/progress) stays on the record — the UI shows how far it
        got. Frees the one-job-at-a-time slot."""
        with self._lock:
            job = self._jobs[job_id]
            job.status = "cancelled"
            job.finished_at = time.time()
            if job.started_at:
                job.elapsed_seconds = round(job.finished_at - job.started_at, 1)
            if self._active_job_id == job_id:
                self._active_job_id = None
            self._persist_locked()

    def request_cancel_running(self, job_id: str) -> bool:
        """Flip cancel_requested on a RUNNING job so the UI can show
        "Cancelling…" immediately. The abort itself is driven by the
        service's cancel event (next denoising step)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return False
            job.cancel_requested = True
            self._persist_locked()
            return True

    def cancel(self, job_id: str) -> str:
        """Cancel/remove a job (mirrors the image store). Returns:

        - "cancelled"  — was queued, now marked cancelled (record kept;
                         frees the one-job-at-a-time slot)
        - "removed"    — finished (completed/failed/cancelled) record deleted
                         (the media-library item / mp4 is NOT deleted)
        - "running"    — in flight; the caller escalates to a mid-flight
                         cancel (service cancel event + request_cancel_running)
        - "not_found"
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return "not_found"
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = time.time()
                if self._active_job_id == job_id:
                    self._active_job_id = None
                self._persist_locked()
                return "cancelled"
            if job.status == "running":
                return "running"
            del self._jobs[job_id]
            self._order.remove(job_id)
            if self._active_job_id == job_id:
                self._active_job_id = None
            self._persist_locked()
            return "removed"


_store: VideoJobStore | None = None


def get_video_job_store() -> VideoJobStore:
    global _store
    if _store is None:
        _store = VideoJobStore()
        _store.load()
    return _store
