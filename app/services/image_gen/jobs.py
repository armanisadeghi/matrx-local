"""Image generation job queue.

Jobs run strictly one at a time (one GPU, one pipeline) in queue order. The
queue is the unit of unattended work: you can enqueue 200 jobs from a prompt
matrix, close the window, restart the machine, and come back to a finished
run — so everything here is built around surviving that.

  POST   /image-gen/jobs           → enqueue one, returns job_id
  POST   /image-gen/jobs/batch     → enqueue N as ONE batch (one request)
  GET    /image-gen/jobs           → recent jobs, newest first
  GET    /image-gen/jobs/{id}      → status + per-step progress
  DELETE /image-gen/jobs/{id}      → cancel queued / cancel running mid-flight /
                                     remove a finished record
  POST   /image-gen/jobs/{id}/retry → requeue a failed or cancelled job
  POST   /image-gen/queue/reorder  → reorder the PENDING jobs
  POST   /image-gen/queue/pause    → stop after the running job finishes
  POST   /image-gen/queue/resume
  DELETE /image-gen/batches/{id}   → cancel every unfinished job of a batch

Three properties make an overnight run actually survivable:

1. RESTART RESUMES. Queued jobs stay queued across an engine restart and a
   job caught mid-flight goes back to queued (its attempt is counted). The
   old behaviour — marking every queued/running job "failed" on load — meant
   one 2am restart silently killed the whole remaining queue.

2. FAILURES RETRY. A transient CUDA OOM or a hiccuping disk costs a retry
   with exponential backoff, not an image. Failures that CANNOT succeed on a
   retry (unknown model, LoRA family mismatch, bad pipeline kwarg) are
   detected and fail immediately — retrying those three times just wastes
   three GPU-minutes to reach the same conclusion.

3. INIT IMAGES ARE DURABLE. img2img bytes are content-addressed on disk
   (sha256), so a restart doesn't strand an img2img job with no input. They
   are reference-counted and swept when the last job that needs them is done.

State persists to ``~/.matrx/generated/images/jobs.json`` after every
transition (atomic tmp+replace). Only TERMINAL jobs are ever evicted by the
history cap — a pending job is never dropped because the history filled up.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any, Literal

from app.common.system_logger import get_logger
from app.services.media_gen.paths import generated_images_dir

logger = get_logger()

ImageJobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
ImageJobPriority = Literal["normal", "next"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

# Terminal records kept in history. Raised from 100 because a single prompt
# matrix can be several hundred jobs — a batch that evicted its own earliest
# results before the user ever saw them was a real hazard at the old cap.
_HISTORY_LIMIT = 1000

# Retry policy. Attempt 1 is the original run; attempts 2..N are retries.
DEFAULT_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 5.0
_RETRY_MAX_DELAY_S = 120.0

# A failure containing any of these can never succeed on a retry — the input
# itself is wrong. Retrying would burn the same GPU minutes to reach the same
# error, three times, while the rest of the queue waits behind it.
_NON_RETRYABLE_MARKERS: tuple[str, ...] = (
    "unknown model",
    "not downloaded",
    "unknown lora",
    "base-family mismatch",
    "incompatible with",
    "unexpected keyword argument",
    "protected parameter",
    "invalid",
)


def _is_retryable(error: str) -> bool:
    lowered = error.lower()
    return not any(marker in lowered for marker in _NON_RETRYABLE_MARKERS)


def _retry_delay(attempt: int) -> float:
    """Exponential backoff: 5s, 10s, 20s … capped. Attempt is 1-based."""
    return min(_RETRY_BASE_DELAY_S * (2 ** max(0, attempt - 1)), _RETRY_MAX_DELAY_S)


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
    has_init_image: bool = False
    """True for image-to-image jobs. The bytes are content-addressed on disk
    (see ImageJobStore._init_image_path) so a restart can still run the job."""
    init_image_sha256: str | None = None
    strength: float | None = None
    """img2img denoising strength (0..1); None for text-to-image jobs."""
    loras: list[dict[str, Any]] = field(default_factory=list)
    """Requested LoRAs: [{"id": <installed-lora-id>, "scale": float}]."""
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
    priority: ImageJobPriority = "normal"
    """"next" = inserted at the FRONT of the pending queue (right after the
    running job). The flag persists so the UI can label the job "Up next"."""

    # ── batch (prompt-matrix) grouping ────────────────────────────────────────
    batch_id: str | None = None
    """Set when the job was enqueued as part of a batch — the unit the user
    actually thinks in ("cancel that 120-image sweep"), not 120 loose rows."""
    batch_index: int = 0
    """Position within the batch (0-based) — also its enqueue order."""
    batch_size: int = 0
    """Total jobs in the batch, so the UI can say "17 of 120" without a scan."""
    batch_label: str | None = None
    """Human name of the batch, e.g. "Portrait sweep"."""
    variables: dict[str, str] = field(default_factory=dict)
    """The matrix variables this job realizes, e.g. {"subject": "cat",
    "style": "noir"} — what makes a queued job legible and a result reusable."""
    combo_label: str | None = None
    """Rendered variable summary, e.g. `subject=cat · style=noir`."""

    # ── retry ─────────────────────────────────────────────────────────────────
    attempts: int = 0
    """Runs started so far. 0 while first queued; 1 during the first run."""
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    next_attempt_at: float | None = None
    """Unix time before which a retrying job must not start (backoff)."""
    last_error: str | None = None
    """Error from the most recent failed attempt, kept while it retries."""

    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    # Monotonic terminal-transition sequence.  Timestamps alone are not a
    # sufficient ordering contract: two jobs can finish in the same clock tick
    # (particularly in tests or after a fast failure).  This makes the
    # completed-history order exact and durable across restarts.
    finished_sequence: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.status == "running" and self.started_at:
            d["elapsed_seconds"] = round(time.time() - self.started_at, 1)
        d["retrying"] = self.status == "queued" and self.attempts > 0
        d["retry_in_seconds"] = (
            max(0.0, round(self.next_attempt_at - time.time(), 1))
            if self.status == "queued" and self.next_attempt_at
            else 0.0
        )
        return d


@dataclass
class BatchSummary:
    """Roll-up of one batch — what the queue UI shows as a single row."""

    batch_id: str
    label: str | None
    total: int
    queued: int
    running: int
    completed: int
    failed: int
    cancelled: int
    created_at: float

    @property
    def finished(self) -> bool:
        return self.queued == 0 and self.running == 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["finished"] = self.finished
        d["done"] = self.completed + self.failed + self.cancelled
        return d


class ImageJobStore:
    """Thread-safe job registry with JSON-file persistence.

    Progress updates arrive from the generation worker thread (the diffusers
    step callback); reads and queue management happen on the FastAPI event
    loop — hence the threading.Lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, ImageJob] = {}
        self._order: list[str] = []  # queue order; newest last
        self._loaded = False
        self._paused = False
        self._batch_labels: dict[str, str] = {}
        self._batch_created: dict[str, float] = {}
        self._next_finished_sequence = 0
        # Hot cache of init-image bytes. The durable copy is on disk, keyed by
        # sha256 — this only saves a re-read for jobs enqueued this session.
        self._init_cache: dict[str, bytes] = {}

    # ── persistence ───────────────────────────────────────────────────────────

    def _history_path(self) -> Path:
        return generated_images_dir() / "jobs.json"

    def _init_image_dir(self) -> Path:
        return generated_images_dir() / "init-images"

    def _init_image_path(self, sha: str) -> Path:
        return self._init_image_dir() / f"{sha}.bin"

    def load(self) -> None:
        """Load persisted state (idempotent; called once at store init).

        Pending work SURVIVES. A job that was queued is still queued; a job
        caught running is returned to the queue with its attempt counted. The
        engine restarting is not the job's fault, and an unattended overnight
        batch must not be silently destroyed by one.
        """
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            path = self._history_path()
            if not path.exists():
                return
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("[image_gen] Could not read jobs.json: %s", exc)
                return

            if isinstance(raw, dict):
                self._paused = bool(raw.get("paused", False))
                self._batch_labels = dict(raw.get("batch_labels") or {})
                self._batch_created = {
                    k: float(v) for k, v in (raw.get("batch_created") or {}).items()
                }
                items = raw.get("jobs") or []
            else:
                items = raw  # legacy: a bare list of jobs

            known = {f.name for f in dataclass_fields(ImageJob)}
            requeued = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    job = ImageJob(**{k: v for k, v in item.items() if k in known})
                except Exception as exc:
                    logger.warning("[image_gen] Skipping malformed job record: %s", exc)
                    continue

                if job.status == "running":
                    # Interrupted mid-flight. Count the attempt (it consumed a
                    # try) and put it back in the queue.
                    job.status = "queued"
                    job.progress = 0.0
                    job.current_step = 0
                    job.started_at = None
                    job.last_error = "Engine restarted while the job was running"
                    job.next_attempt_at = None
                    requeued += 1
                elif job.status == "queued":
                    job.next_attempt_at = None  # don't carry a stale backoff
                    requeued += 1

                # An img2img job whose input bytes are gone can never run.
                if (
                    job.status == "queued"
                    and job.has_init_image
                    and (
                        job.init_image_sha256 is None
                        or not self._init_image_path(job.init_image_sha256).exists()
                    )
                ):
                    job.status = "failed"
                    job.error = "Input image is no longer available on disk"
                    job.finished_at = time.time()
                    requeued -= 1

                self._jobs[job.job_id] = job
                self._order.append(job.job_id)
                self._next_finished_sequence = max(
                    self._next_finished_sequence, job.finished_sequence
                )

            # Older jobs.json files predate finished_sequence. Give terminal
            # records their historical finish order before the next persist;
            # all new transitions receive a sequence at the exact moment they
            # finish.
            for job in sorted(
                (j for j in self._jobs.values() if j.status in TERMINAL_STATUSES and j.finished_sequence == 0),
                key=lambda j: (j.finished_at or 0.0, j.created_at, j.job_id),
            ):
                self._next_finished_sequence += 1
                job.finished_sequence = self._next_finished_sequence

            logger.info(
                "[image_gen] Loaded %d job(s) from history (%d pending resumed%s)",
                len(self._jobs),
                max(0, requeued),
                ", queue PAUSED" if self._paused else "",
            )
            self._sweep_init_images_locked()

    def _persist_locked(self) -> None:
        try:
            path = self._history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            # Never evict a pending job to make room for history. Terminal
            # retention follows terminal completion order, never enqueue order
            # (a retry or priority insert can make those diverge).
            terminal_ids = sorted(
                (
                    jid
                    for jid in self._order
                    if self._jobs[jid].status in TERMINAL_STATUSES
                ),
                key=self._history_sort_key,
                reverse=True,
            )[:_HISTORY_LIMIT]
            keep_ids = set(terminal_ids) | {
                jid
                for jid in self._order
                if self._jobs[jid].status not in TERMINAL_STATUSES
            }
            keep = [jid for jid in self._order if jid in keep_ids]

            live_batches = {self._jobs[j].batch_id for j in keep} - {None}
            payload = {
                "version": 2,
                "paused": self._paused,
                "batch_labels": {
                    b: label
                    for b, label in self._batch_labels.items()
                    if b in live_batches
                },
                "batch_created": {
                    b: ts
                    for b, ts in self._batch_created.items()
                    if b in live_batches
                },
                "jobs": [self._jobs[j].to_dict() for j in keep],
            }
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            logger.warning("[image_gen] Could not persist jobs.json: %s", exc)

    # ── init-image store (content-addressed, reference-counted) ────────────────

    def put_init_image(self, image_bytes: bytes) -> str:
        """Persist init-image bytes; returns their sha256.

        Durable on purpose: a queued img2img job must still be runnable after
        a restart. Identical inputs across a batch share one file.
        """
        sha = hashlib.sha256(image_bytes).hexdigest()
        with self._lock:
            self._init_cache[sha] = image_bytes
            path = self._init_image_path(sha)
            if not path.exists():
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(".tmp")
                    tmp.write_bytes(image_bytes)
                    tmp.replace(path)
                except Exception as exc:
                    logger.warning(
                        "[image_gen] Could not persist init image %s: %s", sha[:8], exc
                    )
            return sha

    def get_init_image(self, job_id: str) -> bytes | None:
        """Init-image bytes for a job (memory, then disk). None for txt2img."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or not job.has_init_image:
                return None
            sha = job.init_image_sha256
            if sha is None:
                return None
            cached = self._init_cache.get(sha)
            if cached is not None:
                return cached
            path = self._init_image_path(sha)
            if not path.exists():
                return None
            try:
                data = path.read_bytes()
            except Exception as exc:
                logger.warning(
                    "[image_gen] Could not read init image %s: %s", sha[:8], exc
                )
                return None
            self._init_cache[sha] = data
            return data

    def _sweep_init_images_locked(self) -> None:
        """Delete init images no PENDING job still needs. Called after every
        terminal transition — the disk must not grow without bound."""
        needed = {
            j.init_image_sha256
            for j in self._jobs.values()
            if j.status not in TERMINAL_STATUSES and j.init_image_sha256
        }
        for sha in list(self._init_cache):
            if sha not in needed:
                self._init_cache.pop(sha, None)
        directory = self._init_image_dir()
        if not directory.exists():
            return
        for path in directory.glob("*.bin"):
            if path.stem in needed:
                continue
            try:
                path.unlink()
            except OSError as exc:
                logger.debug("[image_gen] Could not sweep init image: %s", exc)

    # ── enqueue ───────────────────────────────────────────────────────────────

    def create(self, **kwargs: Any) -> ImageJob:
        """Enqueue one job.

        ``priority="next"`` inserts it at the FRONT of the pending queue: it
        becomes the first still-queued job (the running job is unaffected).
        """
        with self._lock:
            job = self._new_job_locked(kwargs)
            self._persist_locked()
            return job

    def create_batch(
        self,
        jobs: list[dict[str, Any]],
        *,
        label: str | None = None,
    ) -> tuple[str, list[ImageJob]]:
        """Enqueue N jobs as ONE batch, atomically and in order.

        Atomic matters: 120 separate enqueues can interleave with a "next"
        insert and shuffle the sweep, and a mid-way failure would leave half a
        batch queued. One call, one lock, one persist.
        """
        batch_id = str(uuid.uuid4())
        with self._lock:
            self._batch_labels[batch_id] = label or "Batch"
            self._batch_created[batch_id] = time.time()
            created: list[ImageJob] = []
            total = len(jobs)
            for index, spec in enumerate(jobs):
                params = dict(spec)
                params.update(
                    batch_id=batch_id,
                    batch_index=index,
                    batch_size=total,
                    batch_label=label,
                    priority="normal",  # a batch never jumps the queue
                )
                created.append(self._new_job_locked(params))
            self._persist_locked()
            logger.info(
                "[image_gen] Batch %s: enqueued %d job(s)%s",
                batch_id[:8],
                total,
                f" — {label}" if label else "",
            )
            return batch_id, created

    def _new_job_locked(self, kwargs: dict[str, Any]) -> ImageJob:
        job = ImageJob(job_id=str(uuid.uuid4()), **kwargs)
        self._jobs[job.job_id] = job
        if job.priority == "next":
            insert_at = next(
                (
                    i
                    for i, jid in enumerate(self._order)
                    if self._jobs[jid].status == "queued"
                ),
                len(self._order),
            )
            self._order.insert(insert_at, job.job_id)
        else:
            self._order.append(job.job_id)
        return job

    # ── reads ─────────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> ImageJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 50) -> list[ImageJob]:
        """Pending queue first, then terminal history in exact finish order.

        Pending jobs retain queue order because that is what users can act on.
        Terminal records are a generation *history*, so they are ordered by
        their terminal transition rather than enqueue order. ``limit`` applies
        to terminal history only; pending jobs are always included, so a
        200-job queue never makes previously completed output disappear.
        """
        with self._lock:
            pending = [
                j for j in self._order if self._jobs[j].status not in TERMINAL_STATUSES
            ]
            terminal = sorted(
                (
                    j for j in self._order if self._jobs[j].status in TERMINAL_STATUSES
                ),
                key=self._history_sort_key,
                reverse=True,
            )
            return [self._jobs[j] for j in pending] + [
                self._jobs[j] for j in terminal[:limit]
            ]

    def _history_sort_key(self, job_id: str) -> tuple[int, float, float, str]:
        """Newest terminal transition first; deterministic for legacy rows."""
        job = self._jobs[job_id]
        return (
            job.finished_sequence,
            job.finished_at or 0.0,
            job.created_at,
            job.job_id,
        )

    def _mark_terminal_locked(self, job: ImageJob) -> None:
        job.finished_at = time.time()
        self._next_finished_sequence += 1
        job.finished_sequence = self._next_finished_sequence

    def pending(self) -> list[ImageJob]:
        """Queued + running, in queue order (what the reorder UI shows)."""
        with self._lock:
            return [
                self._jobs[j]
                for j in self._order
                if self._jobs[j].status not in TERMINAL_STATUSES
            ]

    def queued_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "queued")

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def batches(self) -> list[BatchSummary]:
        """Roll-up per batch, newest first."""
        with self._lock:
            counts: dict[str, dict[str, int]] = {}
            for job in self._jobs.values():
                if job.batch_id is None:
                    continue
                bucket = counts.setdefault(
                    job.batch_id,
                    {
                        "total": 0,
                        "queued": 0,
                        "running": 0,
                        "completed": 0,
                        "failed": 0,
                        "cancelled": 0,
                    },
                )
                bucket["total"] += 1
                bucket[job.status] += 1
            out = [
                BatchSummary(
                    batch_id=batch_id,
                    label=self._batch_labels.get(batch_id),
                    created_at=self._batch_created.get(batch_id, 0.0),
                    total=c["total"],
                    queued=c["queued"],
                    running=c["running"],
                    completed=c["completed"],
                    failed=c["failed"],
                    cancelled=c["cancelled"],
                )
                for batch_id, c in counts.items()
            ]
            out.sort(key=lambda b: b.created_at, reverse=True)
            return out

    # ── queue control ─────────────────────────────────────────────────────────

    def set_paused(self, paused: bool) -> bool:
        """Pause/resume the drain. A pause lets the RUNNING job finish (killing
        a half-done generation to honour a pause would waste the GPU minutes
        already spent); nothing new starts until resume."""
        with self._lock:
            changed = self._paused != paused
            self._paused = paused
            if changed:
                self._persist_locked()
                logger.info(
                    "[image_gen] Queue %s", "PAUSED" if paused else "RESUMED"
                )
            return changed

    def reorder(self, job_ids: list[str]) -> list[str]:
        """Reorder the QUEUED jobs.

        `job_ids` is the desired order of queued jobs. Ids that are missing,
        unknown, or no longer queued are ignored; any queued job the caller
        omitted keeps its relative position at the end. The running job is
        never moved. Returns the resulting queued order.

        Racing with the worker is expected — the user drags a row at the exact
        moment it starts running. Filtering to still-queued ids makes that a
        no-op instead of a resurrection.
        """
        with self._lock:
            queued_positions = [
                i
                for i, jid in enumerate(self._order)
                if self._jobs[jid].status == "queued"
            ]
            queued_ids = [self._order[i] for i in queued_positions]
            seen: set[str] = set()
            desired: list[str] = []
            for jid in job_ids:
                if jid in queued_ids and jid not in seen:
                    seen.add(jid)
                    desired.append(jid)
            desired.extend(j for j in queued_ids if j not in seen)

            for slot, jid in zip(queued_positions, desired, strict=True):
                self._order[slot] = jid
            self._persist_locked()
            return desired

    # ── transitions ───────────────────────────────────────────────────────────

    def next_queued(self) -> ImageJob | None:
        """Oldest runnable job, or None. Respects pause and retry backoff.

        Fresh queued jobs get their first pass before retry jobs come back
        around. Otherwise a very short retry delay can let an early flaky job
        jump ahead of healthy jobs behind it in a large batch.
        """
        with self._lock:
            if self._paused:
                return None
            now = time.time()
            for retry_pass in (False, True):
                for job_id in self._order:
                    job = self._jobs[job_id]
                    if job.status != "queued":
                        continue
                    is_retry = job.attempts > 0 and "restart" not in (
                        job.last_error or ""
                    ).lower()
                    if is_retry != retry_pass:
                        continue
                    if job.next_attempt_at is not None and job.next_attempt_at > now:
                        continue  # still backing off
                    return job
            return None

    def next_wakeup(self) -> float | None:
        """Seconds until the earliest backing-off job is runnable (None if no
        job is waiting on a timer). Lets the worker sleep exactly long enough
        instead of spinning."""
        with self._lock:
            if self._paused:
                return None
            now = time.time()
            waits = [
                job.next_attempt_at - now
                for job in self._jobs.values()
                if job.status == "queued"
                and job.next_attempt_at is not None
                and job.next_attempt_at > now
            ]
            return min(waits) if waits else None

    def mark_running(self, job_id: str, *, total_steps: int, seed: int) -> bool:
        """False when the job was cancelled between dequeue and start."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                return False
            job.status = "running"
            job.started_at = time.time()
            job.total_steps = total_steps
            job.seed = seed  # the concrete seed actually used
            job.attempts += 1
            job.next_attempt_at = None
            job.progress = 0.0
            job.current_step = 0
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
        self,
        job_id: str,
        *,
        item_id: str,
        file_path: str,
        elapsed_seconds: float,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "completed"
            job.progress = 1.0
            job.item_id = item_id
            job.file_path = file_path
            # The request may omit dimensions; history and the synchronous
            # compatibility endpoint must retain the actual output size.
            if width is not None:
                job.width = width
            if height is not None:
                job.height = height
            job.error = None
            self._mark_terminal_locked(job)
            job.elapsed_seconds = round(elapsed_seconds, 1)
            self._sweep_init_images_locked()
            self._persist_locked()

    def mark_failed(self, job_id: str, error: str) -> bool:
        """Record a failed attempt.

        Returns True when the job was REQUEUED for another attempt (transient
        failure, attempts left) and False when it is terminally failed. Either
        way the queue keeps draining — a bad job never stalls the run.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                # The record was removed under us. Never let a missing job
                # raise out of the worker's except-block and kill the drain.
                return False
            job.last_error = error
            retryable = _is_retryable(error) and job.attempts < job.max_attempts

            if retryable:
                delay = _retry_delay(job.attempts)
                job.status = "queued"
                job.next_attempt_at = time.time() + delay
                job.progress = 0.0
                job.current_step = 0
                job.started_at = None
                job.error = None
                self._persist_locked()
                logger.warning(
                    "[image_gen] Job %s attempt %d/%d failed (%s) — retrying in %.0fs",
                    job_id[:8], job.attempts, job.max_attempts, error, delay,
                )
                return True

            job.status = "failed"
            job.error = error
            self._mark_terminal_locked(job)
            if job.started_at:
                job.elapsed_seconds = round(job.finished_at - job.started_at, 1)
            self._sweep_init_images_locked()
            self._persist_locked()
            logger.error(
                "[image_gen] Job %s FAILED permanently after %d attempt(s): %s",
                job_id[:8], job.attempts, error,
            )
            return False

    def mark_cancelled(self, job_id: str) -> None:
        """Terminal cancelled state. Partial progress AND cancel_requested are
        preserved on purpose — the UI shows how far it got, and the flag is the
        record that a human stopped this rather than it failing. (retry() clears
        the flag, because a retried job is no longer cancelled.)"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "cancelled"
            self._mark_terminal_locked(job)
            if job.started_at:
                job.elapsed_seconds = round(job.finished_at - job.started_at, 1)
            self._sweep_init_images_locked()
            self._persist_locked()

    def request_cancel_running(self, job_id: str) -> bool:
        """Flip cancel_requested on a RUNNING job (the "Cancelling…" flag)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return False
            job.cancel_requested = True
            self._persist_locked()
            return True

    def retry(self, job_id: str) -> bool:
        """Manually requeue a failed/cancelled job. Resets the attempt budget —
        the user asking again is a new decision, not a continuation of the
        automatic backoff."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ("failed", "cancelled"):
                return False
            if job.has_init_image and self._init_image_bytes_missing_locked(job):
                return False
            job.status = "queued"
            job.attempts = 0
            job.error = None
            job.last_error = None
            job.next_attempt_at = None
            job.progress = 0.0
            job.current_step = 0
            job.started_at = None
            job.finished_at = None
            job.finished_sequence = 0
            job.cancel_requested = False
            self._persist_locked()
            return True

    def _init_image_bytes_missing_locked(self, job: ImageJob) -> bool:
        sha = job.init_image_sha256
        if sha is None:
            return True
        return sha not in self._init_cache and not self._init_image_path(sha).exists()

    def cancel(self, job_id: str) -> str:
        """Cancel/remove a job. Returns one of:

        - "cancelled"  — was queued, now marked cancelled (record kept)
        - "removed"    — finished record deleted (the media-library item, if
                         any, is NOT deleted)
        - "running"    — in flight; the caller escalates to a mid-flight cancel
        - "not_found"
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return "not_found"
            if job.status == "queued":
                job.status = "cancelled"
                self._mark_terminal_locked(job)
                self._sweep_init_images_locked()
                self._persist_locked()
                return "cancelled"
            if job.status == "running":
                return "running"
            del self._jobs[job_id]
            self._order.remove(job_id)
            self._sweep_init_images_locked()
            self._persist_locked()
            return "removed"

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        """Cancel every QUEUED job of a batch. A running member is reported so
        the caller can escalate it to a mid-flight cancel."""
        with self._lock:
            members = [j for j in self._jobs.values() if j.batch_id == batch_id]
            if not members:
                return {"found": False, "cancelled": 0, "running_job_id": None}
            cancelled = 0
            running_job_id: str | None = None
            for job in members:
                if job.status == "queued":
                    job.status = "cancelled"
                    self._mark_terminal_locked(job)
                    cancelled += 1
                elif job.status == "running":
                    running_job_id = job.job_id
            self._sweep_init_images_locked()
            self._persist_locked()
            logger.info(
                "[image_gen] Batch %s: cancelled %d queued job(s)%s",
                batch_id[:8],
                cancelled,
                " (one still running)" if running_job_id else "",
            )
            return {
                "found": True,
                "cancelled": cancelled,
                "running_job_id": running_job_id,
            }

    def clear_finished(self) -> int:
        """Drop every terminal record. Returns how many were removed."""
        with self._lock:
            doomed = [
                j for j in self._order if self._jobs[j].status in TERMINAL_STATUSES
            ]
            for job_id in doomed:
                del self._jobs[job_id]
                self._order.remove(job_id)
            self._persist_locked()
            return len(doomed)


# ── sequential worker ─────────────────────────────────────────────────────────


class ImageJobRunner:
    """Single asyncio worker draining the queue strictly one job at a time."""

    def __init__(self, store: ImageJobStore) -> None:
        self._store = store
        self._task: asyncio.Task | None = None
        self._stopping = False

    def ensure_running(self) -> None:
        """Start (or restart) the drain task. Called after every enqueue and on
        resume, from the event loop — task creation is loop-serialized."""
        self._stopping = False
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(
            self._drain(), name="image-gen-job-worker"
        )
        self._task.add_done_callback(self._on_drain_done)

    def _on_drain_done(self, task: asyncio.Task) -> None:
        """Loud recovery: the drain loop must never die silently. Per-job
        failures are handled inside the loop; anything escaping to here is an
        infrastructure bug. The next enqueue restarts the worker either way."""
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
            if not self._stopping and self._store.queued_count() > 0:
                # Supervise the worker: durable queued jobs must not wait for
                # an unrelated future enqueue after an infrastructure crash.
                asyncio.get_running_loop().call_later(1.0, self.ensure_running)

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> dict[str, Any]:
        """Stop only the drain task; queue records remain durable on disk."""
        self._stopping = True
        task = self._task
        if task is None or task.done():
            return {"stopped": True, "active": False}
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return {"stopped": True, "active": False}

    def snapshot(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "queued_jobs": self._store.queued_count(),
            "stopping": self._stopping,
        }

    async def _drain(self) -> None:
        from app.services.image_gen.service import get_image_gen_service  # noqa: PLC0415

        svc = get_image_gen_service()
        while True:
            job = self._store.next_queued()
            if job is None:
                # Nothing runnable. If a job is merely backing off, sleep until
                # it is due rather than exiting — otherwise a retry would sit
                # there until some unrelated enqueue happened to restart us.
                wait = self._store.next_wakeup()
                if wait is None:
                    return  # drained (or paused) — enqueue/resume restarts us
                await asyncio.sleep(min(wait, 5.0))
                continue

            job_id = job.job_id
            model = svc.get_model(job.model_id)
            total_steps = (
                job.steps if job.steps is not None
                else (model.recommended_steps if model else 0)
            )
            # Concrete seed decided HERE (not inside generate()) so the job
            # record shows the reproducible seed from the moment it runs.
            used_seed = (
                job.seed if job.seed is not None else random.randint(0, 2**32 - 1)
            )
            if not self._store.mark_running(
                job_id, total_steps=total_steps, seed=used_seed
            ):
                continue  # cancelled between dequeue and start

            logger.info(
                "[image_gen] Job %s: starting (%s, attempt %d/%d, %d queued behind it)",
                job_id[:8], job.model_id, job.attempts, job.max_attempts,
                self._store.queued_count(),
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
                    init_image_bytes=self._store.get_init_image(job_id),
                    strength=job.strength,
                    loras=job.loras,
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
                        width=result.width,
                        height=result.height,
                    )
                    logger.info(
                        "[image_gen] Job %s: completed → item %s",
                        job_id[:8], result.item_id,
                    )
                else:
                    self._store.mark_failed(job_id, result.error or "Generation failed")
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
