"""End-to-end tests for the prompt-matrix BATCH surface.

These drive the real FastAPI routes and the real ImageJobRunner with a stub
pipeline — no GPU, no model weights, no live engine — so they exercise exactly
the code an overnight sweep runs through:

    POST /image-gen/jobs/batch   → all-or-nothing validation, one batch_id
    GET  /image-gen/batches      → roll-up
    POST /image-gen/queue/pause  → the running job finishes, nothing new starts
    POST /image-gen/queue/reorder→ drag-to-reorder the pending jobs
    POST /image-gen/jobs/{id}/retry
    DELETE /image-gen/batches/{id} → cancels the rest, KEEPS what it made

The load-bearing test is test_runner_drains_a_whole_batch_unattended: a real
batch, drained by the real worker, in order, retrying a transient failure on
the way — which is the entire feature in one assertion.

Run with:  uv run pytest tests/smoke/test_media_gen_batch.py -v
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.image_gen.jobs import ImageJobRunner, ImageJobStore
from app.services.image_gen.models import IMAGE_GEN_MODELS

MODEL = next(m for m in IMAGE_GEN_MODELS if m.pipeline_type == "stable-diffusion-xl")


@pytest.fixture(autouse=True)
def _force_image_gen_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.image_gen import service as service_module

    monkeypatch.setattr(service_module, "DEPS_AVAILABLE", True)
    monkeypatch.setattr(service_module, "DEPS_REASON", "")


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    # No lifespan on purpose — it would start real engine services.
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ImageJobStore:
    from app.services.image_gen import jobs as jobs_module

    monkeypatch.setattr(
        jobs_module, "generated_images_dir", lambda: tmp_path / "img-jobs"
    )
    store = ImageJobStore()
    store.load()
    return store


class _StubService:
    available = True
    unavailable_reason = ""

    def __init__(self, downloaded: bool = True) -> None:
        self._downloaded = downloaded

    def get_model(self, model_id: str):
        return MODEL if model_id == MODEL.model_id else None

    def is_downloaded(self, model_id: str) -> bool:
        return self._downloaded

    def request_cancel_job(self, job_id: str) -> dict:
        return {"found": True, "mid_flight": True}


@pytest.fixture
def wired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[ImageJobStore, _StubService]:
    """Routes wired to an isolated store + a stub service."""
    from app.api import image_gen_routes
    from app.services.image_gen import jobs as jobs_module

    store = _isolate(monkeypatch, tmp_path)
    svc = _StubService()

    class DummyRunner:
        def ensure_running(self) -> None:
            pass

    monkeypatch.setattr(image_gen_routes, "get_image_gen_service", lambda: svc)
    monkeypatch.setattr(jobs_module, "get_image_job_store", lambda: store)
    monkeypatch.setattr(jobs_module, "get_image_job_runner", lambda: DummyRunner())
    return store, svc


def _spec(prompt: str, **kw: Any) -> dict:
    return {"prompt": prompt, "model_id": MODEL.model_id, **kw}


def _matrix_body(n: int, label: str = "Portrait sweep") -> dict:
    """What the UI actually posts: N rendered runs + their variable values."""
    styles = ["noir", "anime", "oil", "3d", "pixel", "ink", "wash", "neon"]
    return {
        "label": label,
        "jobs": [
            _spec(
                f"a cat, {styles[i % len(styles)]} style",
                seed=1000 + i,
                variables={"style": styles[i % len(styles)]},
                combo_label=f"style={styles[i % len(styles)]}",
            )
            for i in range(n)
        ],
    }


# ── POST /image-gen/jobs/batch ────────────────────────────────────────────────


def test_batch_enqueues_every_run_in_order_under_one_id(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired

    r = client.post("/image-gen/jobs/batch", json=_matrix_body(12))

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["count"] == 12
    assert len(body["job_ids"]) == 12

    jobs = store.pending()
    assert len(jobs) == 12
    assert [j.batch_index for j in jobs] == list(range(12))
    assert all(j.batch_id == body["batch_id"] for j in jobs)
    assert all(j.batch_label == "Portrait sweep" for j in jobs)
    assert jobs[0].variables == {"style": "noir"}
    assert jobs[0].combo_label == "style=noir"
    assert jobs[0].seed == 1000


def test_one_bad_run_rejects_the_WHOLE_batch(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    """Half a sweep in the queue is worse than none — the user would have to
    work out which runs made it and hand-fix the rest."""
    store, _ = wired
    body = _matrix_body(5)
    body["jobs"][3]["model_id"] = "no-such-model"

    r = client.post("/image-gen/jobs/batch", json=body)

    assert r.status_code == 404, r.text
    assert "job 4 of 5" in r.json()["detail"], "must name the offending run"
    assert store.pending() == [], "NOTHING was queued"


def test_a_batch_naming_an_undownloaded_model_is_refused_with_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.api import image_gen_routes
    from app.services.image_gen import jobs as jobs_module

    store = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        image_gen_routes,
        "get_image_gen_service",
        lambda: _StubService(downloaded=False),
    )
    monkeypatch.setattr(jobs_module, "get_image_job_store", lambda: store)

    r = client.post("/image-gen/jobs/batch", json=_matrix_body(3))

    assert r.status_code == 409, r.text
    assert r.json()["needs_download"] is True
    assert store.pending() == [], "never queue a job that can only fail"


def test_a_batch_may_not_override_protected_params(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired
    body = _matrix_body(2)
    body["jobs"][1]["extra_params"] = {"prompt": "hijacked"}

    r = client.post("/image-gen/jobs/batch", json=body)

    assert r.status_code == 400, r.text
    assert "protected" in r.json()["detail"]
    assert store.pending() == []


def test_an_empty_batch_is_rejected(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    assert client.post("/image-gen/jobs/batch", json={"jobs": []}).status_code == 422


def test_a_batch_over_the_hard_ceiling_is_rejected(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    """A matrix that expands past 2000 runs is a mistake, not a plan."""
    store, _ = wired

    r = client.post("/image-gen/jobs/batch", json=_matrix_body(2001))

    assert r.status_code == 422, r.text
    assert store.pending() == []


# ── GET /image-gen/batches ────────────────────────────────────────────────────


def test_batches_endpoint_rolls_the_sweep_into_one_row(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired
    batch_id = client.post("/image-gen/jobs/batch", json=_matrix_body(4)).json()[
        "batch_id"
    ]
    jobs = store.pending()
    store.mark_running(jobs[0].job_id, total_steps=1, seed=1)
    store.mark_completed(
        jobs[0].job_id, item_id="item-1", file_path="/x.png", elapsed_seconds=2.0
    )

    r = client.get("/image-gen/batches")

    assert r.status_code == 200, r.text
    [batch] = r.json()
    assert batch["batch_id"] == batch_id
    assert batch["label"] == "Portrait sweep"
    assert (batch["total"], batch["completed"], batch["queued"]) == (4, 1, 3)
    assert batch["done"] == 1
    assert batch["finished"] is False


# ── queue control ─────────────────────────────────────────────────────────────


def test_pause_and_resume_round_trip(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired
    client.post("/image-gen/jobs/batch", json=_matrix_body(3))

    paused = client.post("/image-gen/queue/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json() == {"paused": True, "queued": 3, "running": 0}
    assert store.next_queued() is None, "a paused queue hands out no work"

    state = client.get("/image-gen/queue")
    assert state.json()["paused"] is True

    resumed = client.post("/image-gen/queue/resume")
    assert resumed.json()["paused"] is False
    assert store.next_queued() is not None


def test_reorder_route_moves_a_job_to_the_front(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired
    client.post("/image-gen/jobs/batch", json=_matrix_body(4))
    ids = [j.job_id for j in store.pending()]

    r = client.post(
        "/image-gen/queue/reorder",
        json={"job_ids": [ids[3], ids[0], ids[1], ids[2]]},
    )

    assert r.status_code == 200, r.text
    assert [j["job_id"] for j in r.json()] == [ids[3], ids[0], ids[1], ids[2]]
    assert store.next_queued().job_id == ids[3]  # type: ignore[union-attr]


def test_reorder_ignores_a_job_that_started_running_mid_drag(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired
    client.post("/image-gen/jobs/batch", json=_matrix_body(3))
    ids = [j.job_id for j in store.pending()]
    store.mark_running(ids[0], total_steps=10, seed=1)  # it just started

    r = client.post(
        "/image-gen/queue/reorder", json={"job_ids": [ids[2], ids[0], ids[1]]}
    )

    assert r.status_code == 200, r.text
    pending = store.pending()
    assert pending[0].job_id == ids[0] and pending[0].status == "running"
    assert [j.job_id for j in pending[1:]] == [ids[2], ids[1]]


# ── retry ─────────────────────────────────────────────────────────────────────


def test_retry_route_requeues_a_failed_job(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired
    client.post("/image-gen/jobs/batch", json=_matrix_body(1))
    job_id = store.pending()[0].job_id
    store.mark_running(job_id, total_steps=1, seed=1)
    store.mark_failed(job_id, "Unknown model: gone")  # terminal, non-retryable

    r = client.post(f"/image-gen/jobs/{job_id}/retry")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["attempts"] == 0, "a manual retry is a fresh decision"
    assert body["error"] is None


def test_retrying_a_job_that_did_not_fail_is_a_409(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired
    client.post("/image-gen/jobs/batch", json=_matrix_body(1))
    job_id = store.pending()[0].job_id

    r = client.post(f"/image-gen/jobs/{job_id}/retry")

    assert r.status_code == 409, r.text
    assert "queued" in r.json()["detail"]


def test_retrying_an_unknown_job_is_a_404(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    assert client.post("/image-gen/jobs/nope/retry").status_code == 404


# ── DELETE /image-gen/batches/{id} ────────────────────────────────────────────


def test_cancelling_a_batch_keeps_the_images_it_already_made(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    store, _ = wired
    batch_id = client.post("/image-gen/jobs/batch", json=_matrix_body(5)).json()[
        "batch_id"
    ]
    jobs = store.pending()
    store.mark_running(jobs[0].job_id, total_steps=1, seed=1)
    store.mark_completed(
        jobs[0].job_id, item_id="item-1", file_path="/x.png", elapsed_seconds=1.0
    )
    store.mark_running(jobs[1].job_id, total_steps=20, seed=1)  # mid-flight

    r = client.delete(f"/image-gen/batches/{batch_id}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled"] == 3, "the three still-queued runs"
    assert body["cancelling_job_id"] == jobs[1].job_id, "running one aborts mid-flight"

    done = store.get(jobs[0].job_id)
    assert done is not None
    assert done.status == "completed" and done.item_id == "item-1"
    assert store.next_queued() is None


def test_cancelling_an_unknown_batch_is_a_404(
    client: TestClient, wired: tuple[ImageJobStore, _StubService]
) -> None:
    assert client.delete("/image-gen/batches/nope").status_code == 404


# ── the whole thing, drained by the real worker ───────────────────────────────


def test_runner_drains_a_whole_batch_unattended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """THE test. A real batch, the real runner, drained end to end:

    • every run executes, in queue order
    • a transient failure RETRIES rather than costing an image
    • a hopeless failure gives up immediately instead of burning the budget
    • one bad job never stalls or kills the drain
    • the batch roll-up ends consistent
    """
    from app.services.image_gen import jobs as jobs_module
    from app.services.image_gen import service as service_module

    monkeypatch.setattr(jobs_module, "_RETRY_BASE_DELAY_S", 0.01)  # no real waiting
    store = _isolate(monkeypatch, tmp_path)

    executed: list[str] = []
    flaky_attempts = {"count": 0}

    class FlakyService:
        def get_model(self, model_id: str):
            return MODEL

        async def generate(self, **kwargs: Any):
            from types import SimpleNamespace

            prompt = kwargs["prompt"]
            executed.append(prompt)

            if prompt == "flaky":
                # Fails once with a TRANSIENT error, then succeeds — exactly
                # the CUDA-OOM case an overnight run must survive.
                flaky_attempts["count"] += 1
                if flaky_attempts["count"] == 1:
                    return SimpleNamespace(
                        success=False,
                        cancelled=False,
                        error="CUDA out of memory",
                        item_id=None,
                        file_path=None,
                        elapsed_seconds=0.1,
                    )
            if prompt == "hopeless":
                return SimpleNamespace(
                    success=False,
                    cancelled=False,
                    error="Unknown model: gone",
                    item_id=None,
                    file_path=None,
                    elapsed_seconds=0.1,
                )
            if prompt == "crash":
                raise RuntimeError("synthetic crash inside generate")

            return SimpleNamespace(
                success=True,
                cancelled=False,
                error=None,
                item_id=f"item-{prompt}",
                file_path=f"/{prompt}.png",
                elapsed_seconds=0.1,
                width=512,
                height=512,
            )

    monkeypatch.setattr(service_module, "get_image_gen_service", lambda: FlakyService())

    prompts = ["one", "flaky", "hopeless", "two", "crash", "three"]
    batch_id, jobs = store.create_batch(
        [
            {
                "prompt": p,
                "model_id": MODEL.model_id,
                "max_attempts": 2,
                "variables": {"n": p},
                "combo_label": f"n={p}",
            }
            for p in prompts
        ],
        label="Overnight",
    )
    runner = ImageJobRunner(store)

    async def main() -> None:
        runner.ensure_running()
        deadline = time.monotonic() + 15.0
        while runner.active:
            assert time.monotonic() < deadline, f"worker never drained: {executed}"
            await asyncio.sleep(0.02)

    asyncio.run(main())

    by_prompt = {j.prompt: store.get(j.job_id) for j in jobs}

    # The queue ran in order, and a job that is BACKING OFF does not stall it:
    # the worker moves on to the next job and picks the retry up when it comes
    # due. So the first pass is strictly in queue order, and the two retries
    # land afterwards — a 200-job overnight batch must never sit idle waiting
    # out a backoff timer.
    first_pass = ["one", "flaky", "hopeless", "two", "crash", "three"]
    assert executed[: len(first_pass)] == first_pass, "first pass is queue order"
    assert sorted(executed[len(first_pass) :]) == ["crash", "flaky"], (
        "the two retryable failures came back around, after the queue moved on"
    )
    assert executed.count("flaky") == 2 and executed.count("crash") == 2
    assert executed.count("hopeless") == 1, "a hopeless job is never retried"

    # The transient failure cost a retry, not an image.
    assert by_prompt["flaky"].status == "completed"  # type: ignore[union-attr]
    assert by_prompt["flaky"].attempts == 2  # type: ignore[union-attr]

    # The hopeless one gave up immediately — no wasted retries.
    assert by_prompt["hopeless"].status == "failed"  # type: ignore[union-attr]
    assert by_prompt["hopeless"].attempts == 1  # type: ignore[union-attr]

    # A job that CRASHES the worker step is retried, then fails — and the queue
    # kept going either way.
    assert by_prompt["crash"].status == "failed"  # type: ignore[union-attr]
    assert by_prompt["crash"].attempts == 2  # type: ignore[union-attr]

    # Everything healthy completed, INCLUDING the jobs queued behind the bad ones.
    for good in ("one", "two", "three"):
        assert by_prompt[good].status == "completed"  # type: ignore[union-attr]
        assert by_prompt[good].item_id == f"item-{good}"  # type: ignore[union-attr]

    # And the batch roll-up agrees.
    [summary] = store.batches()
    assert summary.batch_id == batch_id
    assert summary.label == "Overnight"
    assert (summary.total, summary.completed, summary.failed) == (6, 4, 2)
    assert summary.finished is True


def test_a_paused_queue_stops_after_the_running_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pause must not abort work already in flight — a 90%-denoised image is
    real GPU time. It finishes; nothing new starts."""
    from app.services.image_gen import jobs as jobs_module
    from app.services.image_gen import service as service_module

    store = _isolate(monkeypatch, tmp_path)
    executed: list[str] = []
    started = asyncio.Event()

    class SlowService:
        def get_model(self, model_id: str):
            return MODEL

        async def generate(self, **kwargs: Any):
            from types import SimpleNamespace

            executed.append(kwargs["prompt"])
            started.set()
            await asyncio.sleep(0.15)  # in flight while the pause lands
            return SimpleNamespace(
                success=True,
                cancelled=False,
                error=None,
                item_id="i",
                file_path="/x.png",
                elapsed_seconds=0.15,
                width=512,
                height=512,
            )

    monkeypatch.setattr(service_module, "get_image_gen_service", lambda: SlowService())

    _, jobs = store.create_batch(
        [{"prompt": p, "model_id": MODEL.model_id} for p in ("a", "b", "c")]
    )
    runner = ImageJobRunner(store)

    async def main() -> None:
        runner.ensure_running()
        await asyncio.wait_for(started.wait(), timeout=5.0)
        store.set_paused(True)  # pause WHILE "a" is generating
        deadline = time.monotonic() + 5.0
        while runner.active:
            assert time.monotonic() < deadline, "worker never parked"
            await asyncio.sleep(0.02)

    asyncio.run(main())

    assert executed == ["a"], "the running job finished; nothing new started"
    assert store.get(jobs[0].job_id).status == "completed"  # type: ignore[union-attr]
    assert store.queued_count() == 2, "the rest are still queued, not lost"
    assert jobs_module.get_image_job_store is not None  # module import sanity
