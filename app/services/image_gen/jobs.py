"""Image generation job queue.

Mirrors app/services/video_gen/jobs.py, with one deliberate difference: video
REJECTS a second job (one at a time, no queue) while images QUEUE — the user
keeps writing prompts and submitting; jobs run strictly sequentially (one GPU,
one pipeline) in submission order.

  POST   /image-gen/jobs        → enqueue, returns job_id immediately
  GET    /image-gen/jobs        → recent jobs, newest first
  GET    /image-gen/jobs/{id}   → status + per-step progress
  DELETE /image-gen/jobs/{id}   → cancel a QUEUED job, cancel a RUNNING job
                                  MID-FLIGHT (cancel_requested flips true
                                  immediately; the abort lands at the next
                                  denoising step; status → cancelled with
                                  partial progress preserved), or remove a
                                  finished record

State lives in memory for live polling; the last ``_HISTORY_LIMIT`` jobs
persist to ``~/.matrx/generated/images/jobs.json`` so a restart keeps history
(in-flight jobs are marked failed on load — never resurrected as running).

The worker is a single asyncio task on the FastAPI loop: it awaits
``ImageGenService.generate`` (which off-loads the heavy compute to a thread)
one job at a time, so the event loop never blocks and two generations can
never overlap. Completed jobs carry the media-library ``item_id`` — the file
itself is served by GET /media-library/file/{item_id}.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.common.system_logger import get_logger
from app.services.media_gen.paths import generated_images_dir

logger = get_logger()

ImageJobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]

_HISTORY_LIMIT = 100


@dataclass
class ImageJob:
    job_id: str
    prompt: str
    model_id: str
    negative_prompt: str = ""
    steps: int | None = None
    guidance: float | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    """Requested seed; replaced by the CONCRETE seed once the job runs."""
    extra_params: dict[str, Any] = field(default_factory=dict)
    status: ImageJobStatus = "queued"
    progress: float = 0.0
    """0..1 (per denoising step when the pipeline supports the callback)."""
    current_step: int = 0
    total_steps: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None
    item_id: str | None = None
    """Media-library id once completed — GET /media-library/file/{item_id}."""
    file_path: str | None = None
    """Absolute path of the persisted PNG once completed."""
    cancel_requested: bool = False
    """True the instant a cancel lands on a running job — the abort itself
    takes effect at the next denoising step, so the UI shows "Cancelling…"."""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.status == "running" and self.started_at:
            d["elapsed_seconds"] = round(time.time() - self.started_at, 1)
        return d


class ImageJobStore:
    """Thread-safe FIFO job registry with JSON-file history persistence.

    Progress updates arrive from the generation worker thread (diffusers step
    callback); reads and queue management happen on the FastAPI event loop —
    hence the threading.Lock, exactly like the video store.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ImageJob] = {}
        self._order: list[str] = []  # newest last
        self._loaded = False

    # ── persistence ───────────────────────────────────────────────────────────

    def _history_path(self) -> Path:
        return generated_images_dir() / "jobs.json"

    def load(self) -> None:
        """Load persisted history (idempotent; call once at store init)."""
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
                    job = ImageJob(**item)
                    # Never resurrect an in-flight job as queued/running.
                    if job.status in ("queued", "running"):
                        job.status = "failed"
                        job.error = "Engine restarted while the job was in flight"
                    self._jobs[job.job_id] = job
                    self._order.append(job.job_id)
                logger.info("[image_gen] Loaded %d job(s) from history", len(raw))
            except Exception as exc:
                logger.warning("[image_gen] Could not load jobs.json: %s", exc)

    def _persist_locked(self) -> None:
        try:
            path = self._history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            recent = [self._jobs[j].to_dict() for j in self._order[-_HISTORY_LIMIT:]]
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(recent, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            logger.warning("[image_gen] Could not persist jobs.json: %s", exc)

    # ── public API ────────────────────────────────────────────────────────────

    def create(self, **kwargs: Any) -> ImageJob:
        """Enqueue a job (no one-at-a-time limit — this is a real queue)."""
        with self._lock:
            job = ImageJob(job_id=str(uuid.uuid4()), **kwargs)
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._persist_locked()
            return job

    def get(self, job_id: str) -> ImageJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 50) -> list[ImageJob]:
        with self._lock:
            return [self._jobs[j] for j in reversed(self._order[-limit:])]

    def next_queued(self) -> ImageJob | None:
        """Oldest job still queued (FIFO), or None when the queue is drained."""
        with self._lock:
            for job_id in self._order:
                job = self._jobs[job_id]
                if job.status == "queued":
                    return job
            return None

    def queued_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "queued")

    def mark_running(self, job_id: str, *, total_steps: int, seed: int) -> bool:
        """False when the job was cancelled between dequeue and start — the
        worker skips it instead of resurrecting it."""
        with self._lock:
            job = self._jobs[job_id]
            if job.status != "queued":
                return False
            job.status = "running"
            job.started_at = time.time()
            job.total_steps = total_steps
            job.seed = seed  # the concrete seed actually used
            self._persist_locked()
            return True

    def update_progress(self, job_id: str, current_step: int, total_steps: int) -> None:
        """Fires every denoising step from the worker thread — no disk IO."""
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
        self, job_id: str, *, item_id: str, file_path: str, elapsed_seconds: float
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "completed"
            job.progress = 1.0
            job.item_id = item_id
            job.file_path = file_path
            job.finished_at = time.time()
            job.elapsed_seconds = round(elapsed_seconds, 1)
            self._persist_locked()

    def mark_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "failed"
            job.error = error
            job.finished_at = time.time()
            if job.started_at:
                job.elapsed_seconds = round(job.finished_at - job.started_at, 1)
            self._persist_locked()

    def mark_cancelled(self, job_id: str) -> None:
        """Terminal cancelled state for a job whose in-flight generation was
        aborted. Partial progress (current_step/progress) is preserved on the
        record deliberately — the UI shows how far it got."""
        with self._lock:
            job = self._jobs[job_id]
            job.status = "cancelled"
            job.finished_at = time.time()
            if job.started_at:
                job.elapsed_seconds = round(job.finished_at - job.started_at, 1)
            self._persist_locked()

    def request_cancel_running(self, job_id: str) -> bool:
        """Flip cancel_requested on a RUNNING job (the "Cancelling…" flag the
        UI polls). The actual abort is driven by the service's cancel event;
        the terminal status lands via the runner → mark_cancelled."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return False
            job.cancel_requested = True
            self._persist_locked()
            return True

    def cancel(self, job_id: str) -> str:
        """Cancel/remove a job. Returns one of:

        - "cancelled"  — was queued, now marked cancelled (record kept)
        - "removed"    — finished (completed/failed/cancelled) record deleted
                         (the media-library item, if any, is NOT deleted —
                         use DELETE /media-library/items/{item_id} for that)
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
                self._persist_locked()
                return "cancelled"
            if job.status == "running":
                return "running"
            del self._jobs[job_id]
            self._order.remove(job_id)
            self._persist_locked()
            return "removed"


# ── sequential worker ─────────────────────────────────────────────────────────

class ImageJobRunner:
    """Single asyncio worker draining the queue strictly one job at a time."""

    def __init__(self, store: ImageJobStore) -> None:
        self._store = store
        self._task: asyncio.Task | None = None

    def ensure_running(self) -> None:
        """Start (or restart) the drain task. Called after every enqueue, from
        the event loop — no lock needed, task creation is loop-serialized."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(
            self._drain(), name="image-gen-job-worker"
        )
        self._task.add_done_callback(self._on_drain_done)

    @staticmethod
    def _on_drain_done(task: asyncio.Task) -> None:
        """Loud recovery: the drain loop must never die silently. Per-job
        failures are handled inside the loop; anything escaping to here is an
        infrastructure bug (store corruption, cancellation, …). The next
        enqueue restarts the worker either way."""
        if task.cancelled():
            logger.warning("[image_gen] Job worker task was cancelled")
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "[image_gen] Job worker task DIED unexpectedly — queued jobs "
                "will not run until the next enqueue restarts it: %s",
                exc, exc_info=exc,
            )

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _drain(self) -> None:
        from app.services.image_gen.service import get_image_gen_service  # noqa: PLC0415

        svc = get_image_gen_service()
        while True:
            job = self._store.next_queued()
            if job is None:
                return  # queue drained — next enqueue restarts the task
            job_id = job.job_id
            model = svc.get_model(job.model_id)
            total_steps = (
                job.steps if job.steps is not None
                else (model.recommended_steps if model else 0)
            )
            # Concrete seed decided HERE (not inside generate()) so the job
            # record shows the reproducible seed from the moment it runs.
            import random  # noqa: PLC0415
            used_seed = job.seed if job.seed is not None else random.randint(0, 2**32 - 1)
            if not self._store.mark_running(
                job_id, total_steps=total_steps, seed=used_seed
            ):
                continue  # cancelled between dequeue and start
            logger.info(
                "[image_gen] Job %s: starting (%s, %d queued behind it)",
                job_id[:8], job.model_id, self._store.queued_count(),
            )
            try:
                result = await svc.generate(
                    prompt=job.prompt,
                    model_id=job.model_id,
                    negative_prompt=job.negative_prompt,
                    steps=job.steps,
                    guidance=job.guidance,
                    width=job.width,
                    height=job.height,
                    seed=used_seed,
                    extra_params=job.extra_params,
                    progress_callback=lambda step, total: self._store.update_progress(
                        job_id, step, total
                    ),
                    job_id=job_id,
                )
                if result.cancelled:
                    self._store.mark_cancelled(job_id)
                    logger.info(
                        "[image_gen] Job %s: cancelled mid-flight at step %d/%d",
                        job_id[:8], job.current_step, job.total_steps,
                    )
                elif result.success:
                    assert result.item_id is not None and result.file_path is not None
                    self._store.mark_completed(
                        job_id,
                        item_id=result.item_id,
                        file_path=result.file_path,
                        elapsed_seconds=result.elapsed_seconds,
                    )
                    logger.info(
                        "[image_gen] Job %s: completed → item %s",
                        job_id[:8], result.item_id,
                    )
                else:
                    self._store.mark_failed(job_id, result.error or "Generation failed")
                    logger.error(
                        "[image_gen] Job %s FAILED: %s", job_id[:8], result.error
                    )
            except Exception as exc:  # noqa: BLE001 — one bad job must not kill the queue
                logger.error(
                    "[image_gen] Job %s crashed the worker step: %s",
                    job_id[:8], exc, exc_info=True,
                )
                self._store.mark_failed(job_id, str(exc))


# ── singletons ────────────────────────────────────────────────────────────────

_store: ImageJobStore | None = None
_runner: ImageJobRunner | None = None


def get_image_job_store() -> ImageJobStore:
    global _store
    if _store is None:
        _store = ImageJobStore()
        _store.load()
    return _store


def get_image_job_runner() -> ImageJobRunner:
    global _runner
    if _runner is None:
        _runner = ImageJobRunner(get_image_job_store())
    return _runner
