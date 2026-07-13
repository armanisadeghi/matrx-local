# Image generation — the job queue and prompt-matrix batches

Durable rules for `app/services/image_gen/`. Touch this code → update this file
in the same change.

---

## The queue is the unit of unattended work

Jobs run **strictly one at a time** (one GPU, one pipeline) in queue order. The
queue exists so a user can enqueue 200 runs from a prompt matrix, close the
window, restart the machine, and come back to a finished sweep. Every rule below
follows from that, and breaking any one of them silently destroys someone's
overnight run.

### 1. A restart RESUMES. It never destroys pending work.

`ImageJobStore.load()`:

- a **queued** job stays **queued**
- a job caught **running** goes back to **queued**, with its attempt counted
- the queue ORDER and the `paused` flag survive
- an img2img job whose input bytes are gone → **failed** (it can never run, so
  it must not sit in the queue pretending it can)

> Until July 2026 `load()` marked every queued/running job **failed** ("Engine
> restarted while the job was in flight"). One 2am restart threw away the entire
> remaining queue. Do not reintroduce that — `tests/smoke/test_media_gen_queue.py`
> and `tests/characterization/test_image_job_queue.py` pin the resume contract.

### 2. Failures retry — except the ones that can't succeed.

`mark_failed()` returns `True` when it **requeued** the job and `False` when it
is terminally failed. Default 3 attempts, exponential backoff (5s → 10s → 20s,
capped at 120s).

A failure matching `_NON_RETRYABLE_MARKERS` (unknown model, not downloaded, LoRA
family mismatch, unexpected kwarg, …) fails **immediately**: retrying it would
burn the same GPU minutes to reach the same conclusion three times while the
rest of the queue waits behind it.

**A backing-off job does not stall the queue.** `next_queued()` skips it until
`next_attempt_at` is due and the worker moves on to the next runnable job;
`next_wakeup()` tells the worker exactly how long to sleep when nothing else is
runnable. A 200-job batch must never sit idle waiting out a backoff timer.

A **cancel is not a failure** — cancelled jobs are never retried.

### 3. Init images are durable and reference-counted.

img2img bytes are content-addressed on disk at
`~/.matrx/generated/images/init-images/<sha256>.bin` (`put_init_image` /
`get_init_image`). They must outlive a restart, or a queued img2img job wakes up
with no input. `_sweep_init_images_locked()` deletes any file no **pending** job
still needs, after every terminal transition — the disk must not grow without
bound. `get_init_image` is **idempotent** (a retry needs the bytes again).

### 4. The history cap never evicts pending work.

`_persist_locked()` keeps **every non-terminal job** plus the most recent
`_HISTORY_LIMIT` (1000) terminal ones. `recent(limit=N)` likewise always includes
every pending job whatever `N` is — a 200-job queue viewed with `limit=50` must
not hide 150 of them.

### 5. One bad job never kills the drain.

`ImageJobRunner._drain()` catches per-job exceptions; `_on_drain_done` logs
loudly if anything escapes. The store's `mark_*` methods tolerate a missing job
id rather than raising out of the worker's except-block.

---

## Batches (the prompt matrix)

A batch is N jobs sharing a `batch_id` — the unit a user actually thinks in
("cancel that 120-image sweep"), not 120 loose rows.

- **`create_batch()` is atomic.** One lock, one persist. 120 separate enqueues
  could interleave with a `priority="next"` insert and shuffle the sweep, and a
  mid-way failure would leave half a batch queued.
- **`POST /image-gen/jobs/batch` validates EVERY run before queueing ANY of
  them.** One bad run (unknown model, undownloaded weights, protected kwarg)
  rejects the whole request, naming the offending index. Half a sweep in the
  queue is worse than none: the user would have to work out which runs made it
  and hand-fix the rest.
- A batch job carries `variables` (`{"style": "noir"}`) and `combo_label`
  (`style=noir · subject=cat`) so the queue row is legible and a result traces
  back to the combination that produced it.
- **Cancelling a batch never destroys what it already made.** `cancel_batch()`
  cancels queued members and reports the running one for a mid-flight cancel;
  completed jobs and their media-library items are untouched.
- A batch never jumps the queue (`priority` is forced to `normal`).

## Queue control

| Endpoint | Rule |
|---|---|
| `POST /queue/pause` | Stops **starting** jobs. The RUNNING job finishes — aborting a 90%-denoised generation to honour a pause throws away real GPU time. |
| `POST /queue/resume` | Must call `ensure_running()`; the worker exits when the queue drains. |
| `POST /queue/reorder` | Reorders **queued** jobs only. The running job never moves. Unknown / no-longer-queued ids are ignored — the user dragging a row at the instant it starts running is a no-op, never a resurrection. |
| `POST /jobs/{id}/retry` | Resets the attempt budget to 0: the user asking again is a new decision, not a continuation of the automatic backoff. 409 if the input image is gone. |

## The client side

The `{{variable}}` engine itself lives in the desktop app and is **media-agnostic**
— see `desktop/src/lib/prompt-matrix/` (parsing, combination strategies, exact
counting) and its `MatrixTarget` seam. The engine here knows nothing about
variables; it just runs the jobs the matrix expands into.

## Tests

| File | Covers |
|---|---|
| `tests/characterization/test_image_job_queue.py` | The store: restart-resume, retry ladder, backoff, reorder, batch, history cap, corrupt/legacy `jobs.json` |
| `tests/smoke/test_media_gen_batch.py` | The HTTP batch surface + **a real batch drained by the real runner**, retrying a transient failure on the way |
| `tests/smoke/test_media_gen_batch_live.py` | Opt-in (`MATRX_LIVE_GEN=1`): a real 4-run matrix generating real images on a real engine |
| `tests/smoke/test_media_gen_queue.py`, `test_media_gen_cancel.py` | The generation gate, priority-`next`, mid-flight cancel |
