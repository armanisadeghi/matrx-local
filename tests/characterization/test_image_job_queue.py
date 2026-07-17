"""The image job queue as an UNATTENDED runner.

The prompt-matrix feature enqueues hundreds of jobs and walks away, so the
queue's real contract is not "does it generate an image" — it's:

  • a restart RESUMES pending work instead of destroying it
  • a transient failure RETRIES with backoff; a hopeless one fails fast
  • a bad job never stalls or kills the drain
  • a batch is one unit you can watch, reorder, pause and cancel
  • the history cap never evicts work that has not run yet

Every test here pins one of those. They run against the real ImageJobStore
with its persistence redirected to tmp_path — no GPU, no engine, no network.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.services.image_gen.jobs import (
    DEFAULT_MAX_ATTEMPTS,
    ImageJobStore,
    _is_retryable,
    _retry_delay,
)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ImageJobStore:
    """A fresh store whose jobs.json lives in tmp_path."""
    from app.services.image_gen import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "generated_images_dir", lambda: tmp_path / "jobs")
    s = ImageJobStore()
    s.load()
    return s


def _reload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ImageJobStore:
    """Simulate an engine restart: a brand-new store over the same files."""
    from app.services.image_gen import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "generated_images_dir", lambda: tmp_path / "jobs")
    s = ImageJobStore()
    s.load()
    return s


def _enqueue(store: ImageJobStore, prompt: str = "a cat", **kw: object):
    return store.create(prompt=prompt, model_id="sdxl-turbo", **kw)


# ── restart resume ────────────────────────────────────────────────────────────


def test_restart_keeps_queued_jobs_queued(
    store: ImageJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one that matters: an overnight batch survives a 2am restart.

    The old store marked every queued job "failed" on load — one restart threw
    the entire remaining queue away.
    """
    ids = [_enqueue(store, f"prompt {i}").job_id for i in range(5)]

    after = _reload(monkeypatch, tmp_path)

    assert [j.job_id for j in after.pending()] == ids, "queue order must survive"
    assert all(after.get(j).status == "queued" for j in ids)  # type: ignore[union-attr]
    assert after.queued_count() == 5


def test_restart_requeues_the_job_that_was_running(
    store: ImageJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job = _enqueue(store)
    store.mark_running(job.job_id, total_steps=20, seed=7)
    store.update_progress(job.job_id, 12, 20)

    after = _reload(monkeypatch, tmp_path)
    revived = after.get(job.job_id)

    assert revived is not None
    assert revived.status == "queued", "interrupted work goes back in the queue"
    assert revived.attempts == 1, "the interrupted run counted as an attempt"
    assert revived.progress == 0.0 and revived.current_step == 0
    assert revived.last_error is not None and "restart" in revived.last_error.lower()


def test_restart_preserves_the_paused_flag(
    store: ImageJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enqueue(store)
    store.set_paused(True)

    after = _reload(monkeypatch, tmp_path)

    assert after.paused is True
    assert after.next_queued() is None, "a paused queue hands out no work"


def test_restart_fails_an_img2img_job_whose_input_image_is_gone(
    store: ImageJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It can never succeed, so it must not sit in the queue pretending it can."""
    sha = store.put_init_image(b"\x89PNG-not-really")
    job = _enqueue(store, has_init_image=True, init_image_sha256=sha)
    (tmp_path / "jobs" / "init-images" / f"{sha}.bin").unlink()

    after = _reload(monkeypatch, tmp_path)
    revived = after.get(job.job_id)

    assert revived is not None
    assert revived.status == "failed"
    assert "no longer available" in (revived.error or "")


def test_init_image_bytes_survive_a_restart(
    store: ImageJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = b"fake-png-bytes"
    sha = store.put_init_image(raw)
    job = _enqueue(store, has_init_image=True, init_image_sha256=sha)

    after = _reload(monkeypatch, tmp_path)

    assert after.get(job.job_id).status == "queued"  # type: ignore[union-attr]
    assert after.get_init_image(job.job_id) == raw


def test_init_images_are_swept_when_no_pending_job_needs_them(
    store: ImageJobStore, tmp_path: Path
) -> None:
    raw = b"fake-png-bytes"
    sha = store.put_init_image(raw)
    job = _enqueue(store, has_init_image=True, init_image_sha256=sha)
    path = tmp_path / "jobs" / "init-images" / f"{sha}.bin"
    assert path.exists()

    store.mark_running(job.job_id, total_steps=1, seed=1)
    store.mark_completed(
        job.job_id, item_id="i1", file_path="/tmp/x.png", elapsed_seconds=1.0
    )

    assert not path.exists(), "the disk must not grow without bound"


def test_terminal_history_uses_completion_not_enqueue_order(store: ImageJobStore) -> None:
    """A retry/priority reorder must never rewrite the completed timeline."""
    first = _enqueue(store, "first-submitted")
    second = _enqueue(store, "second-submitted")

    # Complete the later submission first, then the earlier one. This is easy
    # to reach after retries and is the case the UI must represent faithfully.
    store.mark_running(second.job_id, total_steps=1, seed=2)
    store.mark_completed(second.job_id, item_id="second", file_path="/tmp/2", elapsed_seconds=1)
    store.mark_running(first.job_id, total_steps=1, seed=1)
    store.mark_completed(first.job_id, item_id="first", file_path="/tmp/1", elapsed_seconds=1)

    history = store.recent()
    assert [job.job_id for job in history] == [first.job_id, second.job_id]
    assert history[0].finished_sequence > history[1].finished_sequence


def test_pending_queue_does_not_hide_terminal_history(store: ImageJobStore) -> None:
    """The terminal-history limit is independent from active queue size."""
    done = _enqueue(store, "already-completed")
    store.mark_running(done.job_id, total_steps=1, seed=1)
    store.mark_completed(done.job_id, item_id="done", file_path="/tmp/done", elapsed_seconds=1)
    for index in range(75):
        _enqueue(store, f"pending-{index}")

    history = store.recent(limit=1)
    assert len(history) == 76
    assert history[-1].job_id == done.job_id


# ── retry ─────────────────────────────────────────────────────────────────────


def test_transient_failure_requeues_with_backoff(store: ImageJobStore) -> None:
    job = _enqueue(store)
    store.mark_running(job.job_id, total_steps=20, seed=1)

    requeued = store.mark_failed(job.job_id, "CUDA out of memory")

    assert requeued is True
    revived = store.get(job.job_id)
    assert revived is not None
    assert revived.status == "queued"
    assert revived.attempts == 1
    assert revived.last_error == "CUDA out of memory"
    assert revived.error is None, "it has not failed — it is about to try again"
    assert revived.next_attempt_at is not None

    # Backing off: not runnable yet, but the worker knows exactly how long to wait.
    assert store.next_queued() is None
    wait = store.next_wakeup()
    assert wait is not None and 0 < wait <= _retry_delay(1)


def test_retries_are_exhausted_then_the_job_fails_for_good(
    store: ImageJobStore,
) -> None:
    job = _enqueue(store)

    for attempt in range(1, DEFAULT_MAX_ATTEMPTS + 1):
        j = store.get(job.job_id)
        assert j is not None and j.status == "queued"
        j.next_attempt_at = None  # skip the backoff wait
        store.mark_running(job.job_id, total_steps=20, seed=1)
        requeued = store.mark_failed(job.job_id, "CUDA out of memory")
        assert requeued is (attempt < DEFAULT_MAX_ATTEMPTS)

    final = store.get(job.job_id)
    assert final is not None
    assert final.status == "failed"
    assert final.attempts == DEFAULT_MAX_ATTEMPTS
    assert final.error == "CUDA out of memory"


def test_a_hopeless_failure_fails_immediately_without_burning_retries(
    store: ImageJobStore,
) -> None:
    """Retrying "unknown model" three times wastes GPU minutes to reach the
    same answer, while the rest of the queue waits behind it."""
    job = _enqueue(store)
    store.mark_running(job.job_id, total_steps=20, seed=1)

    requeued = store.mark_failed(job.job_id, "Unknown model: not-a-model")

    assert requeued is False
    final = store.get(job.job_id)
    assert final is not None and final.status == "failed" and final.attempts == 1


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        ("CUDA out of memory", True),
        ("Connection reset by peer", True),
        ("No space left on device", True),
        ("Unknown model: xyz", False),
        ("model not downloaded", False),
        ("LoRA 'x' is incompatible with sdxl", False),
        ("got an unexpected keyword argument 'foo'", False),
    ],
)
def test_retryability_classification(error: str, retryable: bool) -> None:
    assert _is_retryable(error) is retryable


def test_backoff_grows_and_is_capped() -> None:
    delays = [_retry_delay(a) for a in range(1, 10)]
    assert delays[0] < delays[1] < delays[2], "must back off exponentially"
    assert max(delays) <= 120.0, "but never wait absurdly long"


def test_manual_retry_resets_the_attempt_budget(store: ImageJobStore) -> None:
    job = _enqueue(store)
    store.mark_running(job.job_id, total_steps=20, seed=1)
    store.mark_failed(job.job_id, "Unknown model: xyz")  # terminal

    assert store.retry(job.job_id) is True

    revived = store.get(job.job_id)
    assert revived is not None
    assert revived.status == "queued"
    assert revived.attempts == 0, "the user asking again is a new decision"
    assert revived.error is None and revived.next_attempt_at is None
    assert store.next_queued() is not None, "runnable straight away"


def test_manual_retry_refuses_a_job_whose_input_image_is_gone(
    store: ImageJobStore, tmp_path: Path
) -> None:
    sha = store.put_init_image(b"bytes")
    job = _enqueue(store, has_init_image=True, init_image_sha256=sha)
    store.mark_running(job.job_id, total_steps=1, seed=1)
    store.mark_failed(job.job_id, "Unknown model: xyz")  # terminal → sweeps the image

    assert store.retry(job.job_id) is False
    assert store.get(job.job_id).status == "failed"  # type: ignore[union-attr]


def test_a_running_job_that_is_cancelled_is_not_retried(store: ImageJobStore) -> None:
    job = _enqueue(store)
    store.mark_running(job.job_id, total_steps=20, seed=1)
    store.request_cancel_running(job.job_id)
    store.mark_cancelled(job.job_id)

    final = store.get(job.job_id)
    assert final is not None
    assert final.status == "cancelled", "a cancel is a decision, not a failure"
    assert store.next_queued() is None


# ── pause / resume ────────────────────────────────────────────────────────────


def test_pause_stops_handing_out_work_and_resume_restores_it(
    store: ImageJobStore,
) -> None:
    _enqueue(store, "a")
    _enqueue(store, "b")

    store.set_paused(True)
    assert store.next_queued() is None
    assert store.next_wakeup() is None, "a paused queue must not busy-wait"

    store.set_paused(False)
    nxt = store.next_queued()
    assert nxt is not None and nxt.prompt == "a"


# ── ordering + reorder ────────────────────────────────────────────────────────


def test_queue_is_fifo_and_next_priority_jumps_the_line(store: ImageJobStore) -> None:
    _enqueue(store, "a")
    _enqueue(store, "b")
    _enqueue(store, "urgent", priority="next")

    assert [j.prompt for j in store.pending()] == ["urgent", "a", "b"]


def test_reorder_rearranges_the_pending_jobs(store: ImageJobStore) -> None:
    a, b, c = (_enqueue(store, p) for p in ("a", "b", "c"))

    store.reorder([c.job_id, a.job_id, b.job_id])

    assert [j.prompt for j in store.pending()] == ["c", "a", "b"]
    assert store.next_queued().prompt == "c"  # type: ignore[union-attr]


def test_reorder_never_moves_the_running_job(store: ImageJobStore) -> None:
    a, b, c = (_enqueue(store, p) for p in ("a", "b", "c"))
    store.mark_running(a.job_id, total_steps=20, seed=1)

    store.reorder([c.job_id, b.job_id, a.job_id])  # asks to move the running job too

    pending = store.pending()
    assert pending[0].job_id == a.job_id and pending[0].status == "running"
    assert [j.prompt for j in pending[1:]] == ["c", "b"]


def test_reorder_ignores_unknown_and_finished_ids_instead_of_exploding(
    store: ImageJobStore,
) -> None:
    """The user drags a row at the exact moment it starts running. That is a
    no-op, not a crash and never a resurrection."""
    a, b = _enqueue(store, "a"), _enqueue(store, "b")
    store.mark_running(a.job_id, total_steps=1, seed=1)
    store.mark_completed(a.job_id, item_id="i", file_path="/x.png", elapsed_seconds=1)

    result = store.reorder([a.job_id, "does-not-exist", b.job_id, b.job_id])

    assert result == [b.job_id]
    assert store.get(a.job_id).status == "completed"  # type: ignore[union-attr]


def test_reorder_keeps_omitted_jobs_at_the_end(store: ImageJobStore) -> None:
    a, b, c = (_enqueue(store, p) for p in ("a", "b", "c"))

    store.reorder([c.job_id])  # caller only mentions one

    assert [j.prompt for j in store.pending()] == ["c", "a", "b"]


def test_reorder_survives_a_restart(
    store: ImageJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b, c = (_enqueue(store, p) for p in ("a", "b", "c"))
    store.reorder([c.job_id, b.job_id, a.job_id])

    after = _reload(monkeypatch, tmp_path)

    assert [j.prompt for j in after.pending()] == ["c", "b", "a"]


# ── batches ───────────────────────────────────────────────────────────────────


def _batch_specs(n: int) -> list[dict]:
    return [
        {
            "prompt": f"a cat, style {i}",
            "model_id": "sdxl-turbo",
            "seed": 1000 + i,
            "variables": {"style": f"s{i}"},
            "combo_label": f"style=s{i}",
        }
        for i in range(n)
    ]


def test_batch_enqueues_in_order_under_one_id(store: ImageJobStore) -> None:
    batch_id, jobs = store.create_batch(_batch_specs(5), label="Portrait sweep")

    assert len(jobs) == 5
    assert [j.batch_index for j in jobs] == [0, 1, 2, 3, 4]
    assert all(j.batch_id == batch_id for j in jobs)
    assert all(j.batch_size == 5 for j in jobs)
    assert all(j.batch_label == "Portrait sweep" for j in jobs)
    assert jobs[2].variables == {"style": "s2"}
    assert jobs[2].combo_label == "style=s2"
    assert [j.prompt for j in store.pending()] == [j.prompt for j in jobs]


def test_batch_summary_rolls_the_whole_sweep_into_one_row(
    store: ImageJobStore,
) -> None:
    batch_id, jobs = store.create_batch(_batch_specs(4), label="Sweep")

    store.mark_running(jobs[0].job_id, total_steps=1, seed=1)
    store.mark_completed(
        jobs[0].job_id, item_id="i", file_path="/x.png", elapsed_seconds=1
    )
    store.mark_running(jobs[1].job_id, total_steps=1, seed=1)

    summaries = store.batches()
    assert len(summaries) == 1
    s = summaries[0]
    assert s.batch_id == batch_id and s.label == "Sweep"
    assert (s.total, s.completed, s.running, s.queued) == (4, 1, 1, 2)
    assert s.finished is False
    assert s.to_dict()["done"] == 1


def test_cancelling_a_batch_spares_the_images_it_already_made(
    store: ImageJobStore,
) -> None:
    batch_id, jobs = store.create_batch(_batch_specs(5))
    store.mark_running(jobs[0].job_id, total_steps=1, seed=1)
    store.mark_completed(
        jobs[0].job_id, item_id="item-1", file_path="/x.png", elapsed_seconds=1
    )
    store.mark_running(jobs[1].job_id, total_steps=20, seed=1)

    result = store.cancel_batch(batch_id)

    assert result["found"] is True
    assert result["cancelled"] == 3, "the three still-queued jobs"
    assert result["running_job_id"] == jobs[1].job_id, "caller escalates this one"

    done = store.get(jobs[0].job_id)
    assert done is not None
    assert done.status == "completed" and done.item_id == "item-1"
    assert store.next_queued() is None


def test_cancel_batch_reports_an_unknown_batch(store: ImageJobStore) -> None:
    assert store.cancel_batch("nope")["found"] is False


def test_a_batch_survives_a_restart_intact(
    store: ImageJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    batch_id, jobs = store.create_batch(_batch_specs(3), label="Overnight")
    store.mark_running(jobs[0].job_id, total_steps=20, seed=1)

    after = _reload(monkeypatch, tmp_path)

    summaries = after.batches()
    assert len(summaries) == 1
    assert summaries[0].batch_id == batch_id
    assert summaries[0].label == "Overnight", "the batch's name survives too"
    assert summaries[0].queued == 3, "including the one that was mid-flight"
    assert after.get(jobs[0].job_id).variables == {"style": "s0"}  # type: ignore[union-attr]


# ── history cap ───────────────────────────────────────────────────────────────


def test_history_cap_never_evicts_a_job_that_has_not_run(
    store: ImageJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A big batch must not push its own tail out of existence."""
    from app.services.image_gen import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "_HISTORY_LIMIT", 5)

    for i in range(8):  # terminal jobs, more than the cap
        j = _enqueue(store, f"old {i}")
        store.mark_running(j.job_id, total_steps=1, seed=1)
        store.mark_completed(
            j.job_id, item_id=f"i{i}", file_path="/x.png", elapsed_seconds=1
        )
    pending_ids = [_enqueue(store, f"pending {i}").job_id for i in range(6)]

    after = _reload(monkeypatch, tmp_path)

    survivors = {j.job_id for j in after.recent(limit=1000)}
    assert set(pending_ids) <= survivors, "every pending job survived"
    assert after.queued_count() == 6
    completed = [j for j in after.recent(limit=1000) if j.status == "completed"]
    assert len(completed) == 5, "only terminal records are capped"


def test_recent_always_includes_pending_jobs_however_small_the_limit(
    store: ImageJobStore,
) -> None:
    """A 200-job queue viewed with limit=50 must not hide 150 of them."""
    for i in range(60):
        _enqueue(store, f"p{i}")

    listed = store.recent(limit=10)

    assert sum(1 for j in listed if j.status == "queued") == 60


def test_clear_finished_leaves_pending_work_alone(store: ImageJobStore) -> None:
    done = _enqueue(store, "done")
    store.mark_running(done.job_id, total_steps=1, seed=1)
    store.mark_completed(
        done.job_id, item_id="i", file_path="/x.png", elapsed_seconds=1
    )
    _enqueue(store, "still queued")

    assert store.clear_finished() == 1
    assert [j.prompt for j in store.recent()] == ["still queued"]


# ── persistence shape ─────────────────────────────────────────────────────────


def test_jobs_json_is_written_atomically_and_carries_queue_state(
    store: ImageJobStore, tmp_path: Path
) -> None:
    store.create_batch(_batch_specs(2), label="Sweep")
    store.set_paused(True)

    path = tmp_path / "jobs" / "jobs.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["paused"] is True
    assert len(payload["jobs"]) == 2
    assert payload["jobs"][0]["variables"] == {"style": "s0"}
    assert list(payload["batch_labels"].values()) == ["Sweep"]
    assert not list(path.parent.glob("*.tmp")), "no temp file left behind"


def test_a_corrupt_history_file_does_not_take_the_engine_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.image_gen import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "generated_images_dir", lambda: tmp_path / "jobs")
    (tmp_path / "jobs").mkdir(parents=True)
    (tmp_path / "jobs" / "jobs.json").write_text("{not json", encoding="utf-8")

    s = ImageJobStore()
    s.load()  # must not raise

    assert s.recent() == []
    assert _enqueue(s).job_id, "and the queue still works"


def test_a_legacy_bare_list_history_still_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """jobs.json used to be a bare list. Old installs must not lose history."""
    from app.services.image_gen import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "generated_images_dir", lambda: tmp_path / "jobs")
    (tmp_path / "jobs").mkdir(parents=True)
    (tmp_path / "jobs" / "jobs.json").write_text(
        json.dumps(
            [
                {
                    "job_id": "old-1",
                    "prompt": "a cat",
                    "model_id": "sdxl-turbo",
                    "status": "completed",
                    "item_id": "item-1",
                    "created_at": time.time(),
                }
            ]
        ),
        encoding="utf-8",
    )

    s = ImageJobStore()
    s.load()

    old = s.get("old-1")
    assert old is not None
    assert old.status == "completed" and old.item_id == "item-1"
    assert old.attempts == 0 and old.batch_id is None, "new fields get defaults"
