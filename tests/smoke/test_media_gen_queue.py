"""In-process tests for the image-gen generation gate + priority-"next" queue.

Like test_media_gen_cancel.py these NEVER touch the live engine: everything
runs against a FastAPI TestClient (no lifespan) or directly against
service/store/runner instances with a STUB pipeline. No real model is loaded.
Run with:

    uv run pytest tests/smoke/test_media_gen_queue.py -v

Covers the July 2026 "clicking Generate while jobs are queued breaks
everything" bug:
  - THE GATE: a one-shot arriving while a job generation is mid-flight WAITS
    its turn — the two pipe() calls never overlap, both complete, in order.
  - Cancelling the RUNNING generation never cancels (or deadlocks) a waiter
    parked on the gate — the waiter runs to completion right after.
  - priority="next" inserts a job at the front of the pending queue:
    enqueue A, B, then C-with-next while A runs → execution order A, C, B.
  - The priority insertion + flag persist across a store reload (jobs.json).
  - POST /image-gen/jobs accepts {"priority": "next"} and GET /image-gen/jobs
    carries the priority field.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.image_gen.jobs import ImageJobRunner, ImageJobStore
from app.services.image_gen.models import IMAGE_GEN_MODELS
from app.services.image_gen.service import ImageGenService

MODEL = next(
    m for m in IMAGE_GEN_MODELS if m.pipeline_type == "stable-diffusion-xl"
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app
    # No context manager on purpose: lifespan must NOT run (it would start
    # engine services). Plain requests still traverse the middleware stack.
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


class CompletingFakePipe:
    """Stub diffusers pipeline that runs its steps (honoring the modern
    callback contract, so cancels land exactly like a real pipeline) and then
    returns a real non-constant PIL image. Tracks concurrency so a test can
    PROVE two generations never overlapped."""

    def __init__(self, steps_delay: float = 0.02) -> None:
        self.steps_delay = steps_delay
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.completed_prompts: list[str] = []

    def __call__(
        self,
        prompt: str,
        num_inference_steps: int,
        width: int,
        height: int,
        generator: Any = None,
        guidance_scale: float | None = None,
        negative_prompt: str | None = None,
        callback_on_step_end: Any = None,
        **kwargs: Any,
    ) -> Any:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            for step in range(num_inference_steps):
                time.sleep(self.steps_delay)
                if callback_on_step_end is not None:
                    # Raises GenerationCancelled when a cancel landed.
                    callback_on_step_end(self, step, 0, {})
            from PIL import Image  # noqa: PLC0415
            import numpy as np  # noqa: PLC0415

            # Non-constant pixels — the service's NaN/black-image guard
            # rejects constant output.
            arr = (
                np.arange(16 * 16 * 3, dtype=np.uint32).reshape(16, 16, 3) % 251
            ).astype("uint8")
            image = Image.fromarray(arr, mode="RGB")
            with self._lock:
                self.completed_prompts.append(prompt)
            return type("Out", (), {"images": [image]})()
        finally:
            with self._lock:
                self.active -= 1


def make_stub_service(pipe: Any) -> ImageGenService:
    svc = ImageGenService()
    svc._pipeline = pipe
    svc._loaded_model_id = MODEL.model_id
    svc._device = "cpu"
    return svc


def _isolate_image_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services.image_gen import jobs as jobs_module
    monkeypatch.setattr(
        jobs_module, "generated_images_dir", lambda: tmp_path / "img-jobs"
    )


def _stub_library_save(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Replace the media-library persistence with a lightweight fake so the
    stubbed generations complete without touching ~/.matrx."""
    from app.services.media_gen import library as library_module

    counter = {"n": 0}

    def fake_save(png_bytes: bytes, **kwargs: Any) -> dict:
        counter["n"] += 1
        path = tmp_path / f"gen-{counter['n']}.png"
        path.write_bytes(png_bytes)
        return {"id": f"item-{counter['n']}", "file_path": str(path)}

    monkeypatch.setattr(library_module, "save_generated_image", fake_save)


# ── THE GATE: one-shot vs running job never overlap ──────────────────────────

def test_oneshot_during_job_serializes_and_both_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("PIL")
    _isolate_image_history(monkeypatch, tmp_path)
    _stub_library_save(monkeypatch, tmp_path)
    from app.services.image_gen import service as service_module

    pipe = CompletingFakePipe()
    svc = make_stub_service(pipe)
    monkeypatch.setattr(service_module, "get_image_gen_service", lambda: svc)

    store = ImageJobStore()
    runner = ImageJobRunner(store)

    async def main():
        job = store.create(prompt="job-a", model_id=MODEL.model_id, steps=20)
        runner.ensure_running()
        deadline = time.monotonic() + 5.0
        while not (
            store.get(job.job_id).status == "running"  # type: ignore[union-attr]
            and svc.get_status()["is_generating"]
        ):
            assert time.monotonic() < deadline, "job never started"
            await asyncio.sleep(0.01)

        # One-shot fired MID-JOB — exactly the shipped bug. It must wait its
        # turn on the gate, not run concurrently against the same pipeline.
        oneshot = asyncio.ensure_future(
            svc.generate(prompt="oneshot-b", model_id=MODEL.model_id, steps=3)
        )
        await asyncio.sleep(0.1)
        # While the job is still generating the one-shot has NOT started.
        assert pipe.active == 1
        result = await asyncio.wait_for(oneshot, timeout=30.0)

        deadline = time.monotonic() + 10.0
        while runner.active:
            assert time.monotonic() < deadline, "worker never drained"
            await asyncio.sleep(0.02)
        return store.get(job.job_id), result

    job, oneshot_result = asyncio.run(main())

    assert pipe.max_active == 1, (
        "two generations ran concurrently on the same pipeline — the gate "
        "is broken"
    )
    assert pipe.completed_prompts == ["job-a", "oneshot-b"]
    assert oneshot_result.success is True
    assert oneshot_result.image_b64
    assert job is not None and job.status == "completed"


def test_cancel_running_job_frees_gate_for_waiting_oneshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancel the RUNNING generation while a one-shot waiter is parked on the
    gate → the running job lands as cancelled, and the waiter (which the
    cancel must NOT touch) runs to completion. No deadlock, no lost waiter."""
    pytest.importorskip("torch")
    pytest.importorskip("PIL")
    _isolate_image_history(monkeypatch, tmp_path)
    _stub_library_save(monkeypatch, tmp_path)
    from app.services.image_gen import service as service_module

    pipe = CompletingFakePipe()
    svc = make_stub_service(pipe)
    monkeypatch.setattr(service_module, "get_image_gen_service", lambda: svc)

    store = ImageJobStore()
    runner = ImageJobRunner(store)

    async def main():
        job = store.create(prompt="job-a", model_id=MODEL.model_id, steps=2000)
        runner.ensure_running()
        deadline = time.monotonic() + 5.0
        while not (
            store.get(job.job_id).status == "running"  # type: ignore[union-attr]
            and svc.get_status()["is_generating"]
        ):
            assert time.monotonic() < deadline, "job never started"
            await asyncio.sleep(0.01)

        oneshot = asyncio.ensure_future(
            svc.generate(prompt="oneshot-b", model_id=MODEL.model_id, steps=3)
        )
        await asyncio.sleep(0.1)  # let the waiter park on the gate

        # Cancel "the currently running generation" (the /image-gen/cancel
        # semantics). The waiter is NOT registered yet, so it must survive.
        res = svc.request_cancel()
        assert res["cancelled"] is True
        assert res["was"] == "job"
        assert res["job_id"] == job.job_id
        store.request_cancel_running(job.job_id)

        result = await asyncio.wait_for(oneshot, timeout=30.0)
        deadline = time.monotonic() + 10.0
        while runner.active:
            assert time.monotonic() < deadline, "worker never drained"
            await asyncio.sleep(0.02)
        return store.get(job.job_id), result

    job, oneshot_result = asyncio.run(main())
    assert job is not None and job.status == "cancelled"
    assert oneshot_result.success is True, oneshot_result.error
    assert oneshot_result.cancelled is False
    assert pipe.max_active == 1
    assert pipe.completed_prompts == ["oneshot-b"]


# ── priority="next" ordering ─────────────────────────────────────────────────

def test_priority_next_runs_after_current_before_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Enqueue A, B; while A runs enqueue C with priority=next → execution
    order A, C, B."""
    pytest.importorskip("torch")
    pytest.importorskip("PIL")
    _isolate_image_history(monkeypatch, tmp_path)
    _stub_library_save(monkeypatch, tmp_path)
    from app.services.image_gen import service as service_module

    pipe = CompletingFakePipe()
    svc = make_stub_service(pipe)
    monkeypatch.setattr(service_module, "get_image_gen_service", lambda: svc)

    store = ImageJobStore()
    runner = ImageJobRunner(store)

    async def main():
        a = store.create(prompt="A", model_id=MODEL.model_id, steps=20)
        b = store.create(prompt="B", model_id=MODEL.model_id, steps=2)
        runner.ensure_running()
        deadline = time.monotonic() + 5.0
        while store.get(a.job_id).status != "running":  # type: ignore[union-attr]
            assert time.monotonic() < deadline, "A never started"
            await asyncio.sleep(0.01)
        c = store.create(
            prompt="C", model_id=MODEL.model_id, steps=2, priority="next"
        )
        assert store.next_queued() is not None
        assert store.next_queued().job_id == c.job_id  # type: ignore[union-attr]
        deadline = time.monotonic() + 30.0
        while runner.active:
            assert time.monotonic() < deadline, "worker never drained"
            await asyncio.sleep(0.02)
        return [store.get(j.job_id) for j in (a, b, c)]

    finished = asyncio.run(main())
    assert pipe.completed_prompts == ["A", "C", "B"]
    for job in finished:
        assert job is not None and job.status == "completed"
    assert finished[2].priority == "next"  # type: ignore[union-attr]


def test_priority_next_persists_across_reload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_image_history(monkeypatch, tmp_path)
    store = ImageJobStore()
    a = store.create(prompt="A", model_id=MODEL.model_id)
    assert store.mark_running(a.job_id, total_steps=10, seed=1)
    b = store.create(prompt="B", model_id=MODEL.model_id)
    c = store.create(prompt="C", model_id=MODEL.model_id, priority="next")

    # In-memory: C is the next queued job (front of the pending queue).
    assert store.next_queued().job_id == c.job_id  # type: ignore[union-attr]

    # Reload from jobs.json: order AND the priority flag survive. (Statuses
    # do NOT: by design load() marks any in-flight queued/running job as
    # failed — jobs are never silently resurrected after a restart.)
    store2 = ImageJobStore()
    store2.load()
    reloaded = {j.job_id: j for j in store2.recent()}
    assert reloaded[c.job_id].priority == "next"
    assert reloaded[b.job_id].priority == "normal"
    order = [j.job_id for j in store2.recent()]  # newest-first display order
    # _order on disk is [A, C, B] → recent() (reversed) is [B, C, A].
    assert order == [b.job_id, c.job_id, a.job_id]
    # No job was resurrected as queued/running.
    for job in reloaded.values():
        assert job.status == "failed"
        assert job.error == "Engine restarted while the job was in flight"


# ── HTTP contract ─────────────────────────────────────────────────────────────

def test_enqueue_route_accepts_priority_next(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.api import image_gen_routes
    from app.services.image_gen import jobs as jobs_module

    _isolate_image_history(monkeypatch, tmp_path)
    store = ImageJobStore()

    class StubService:
        available = True
        unavailable_reason = ""

        def get_model(self, model_id: str):
            return MODEL

        def is_downloaded(self, model_id: str) -> bool:
            return True

    class DummyRunner:
        def ensure_running(self) -> None:
            pass

    monkeypatch.setattr(
        image_gen_routes, "get_image_gen_service", lambda: StubService()
    )
    monkeypatch.setattr(jobs_module, "get_image_job_store", lambda: store)
    monkeypatch.setattr(jobs_module, "get_image_job_runner", lambda: DummyRunner())

    body = {"prompt": "a", "model_id": MODEL.model_id}
    r_a = client.post("/image-gen/jobs", json=body)
    r_b = client.post("/image-gen/jobs", json={**body, "prompt": "b"})
    r_c = client.post(
        "/image-gen/jobs", json={**body, "prompt": "c", "priority": "next"}
    )
    assert r_a.status_code == 202, r_a.text
    assert r_b.status_code == 202, r_b.text
    assert r_c.status_code == 202, r_c.text
    c_id = r_c.json()["job_id"]

    # The priority job is first in FIFO execution order.
    assert store.next_queued().job_id == c_id  # type: ignore[union-attr]

    # GET /image-gen/jobs carries the priority field.
    listing = client.get("/image-gen/jobs")
    assert listing.status_code == 200, listing.text
    by_id = {j["job_id"]: j for j in listing.json()}
    assert by_id[c_id]["priority"] == "next"
    assert by_id[r_a.json()["job_id"]]["priority"] == "normal"

    # An invalid priority is rejected loudly, never silently coerced.
    bad = client.post(
        "/image-gen/jobs", json={**body, "priority": "urgent"}
    )
    assert bad.status_code == 422


def test_long_prompts_accepted_and_never_truncated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The old 1000-char prompt cap was an arbitrary API restriction, not a
    model limit (FLUX/T5 reads ~512 tokens ≈ 2000+ chars). >1000-char prompts
    must round-trip through /generate and /jobs verbatim — no 422, no silent
    truncation anywhere on our side. 10k chars remains the sanity bound."""
    from app.api import image_gen_routes
    from app.services.image_gen import jobs as jobs_module
    from app.services.image_gen.service import GenerationResult

    long_prompt = ("a lantern-lit harbor at dusk, volumetric fog, 35mm " * 60).strip()
    assert len(long_prompt) > 2500  # comfortably beyond the old cap

    seen_prompts: list[str] = []

    class StubService:
        available = True
        unavailable_reason = ""

        def get_model(self, model_id: str):
            return MODEL

        def is_downloaded(self, model_id: str) -> bool:
            return True

        async def generate(self, **kwargs: Any) -> GenerationResult:
            seen_prompts.append(kwargs["prompt"])
            return GenerationResult(
                success=False, error="stub", model_id=MODEL.model_id
            )

    class DummyRunner:
        def ensure_running(self) -> None:
            pass

    _isolate_image_history(monkeypatch, tmp_path)
    store = ImageJobStore()
    monkeypatch.setattr(
        image_gen_routes, "get_image_gen_service", lambda: StubService()
    )
    monkeypatch.setattr(jobs_module, "get_image_job_store", lambda: store)
    monkeypatch.setattr(jobs_module, "get_image_job_runner", lambda: DummyRunner())

    # One-shot: full prompt reaches the service verbatim.
    r = client.post("/image-gen/generate", json={
        "prompt": long_prompt, "model_id": MODEL.model_id,
    })
    assert r.status_code == 200, r.text
    assert seen_prompts == [long_prompt]

    # Job enqueue: full prompt persisted verbatim.
    r = client.post("/image-gen/jobs", json={
        "prompt": long_prompt, "model_id": MODEL.model_id,
    })
    assert r.status_code == 202, r.text
    job = store.get(r.json()["job_id"])
    assert job is not None and job.prompt == long_prompt

    # The 10k sanity bound still rejects absurd payloads loudly.
    r = client.post("/image-gen/generate", json={
        "prompt": "x" * 10001, "model_id": MODEL.model_id,
    })
    assert r.status_code == 422
