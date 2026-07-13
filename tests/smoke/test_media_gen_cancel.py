"""In-process tests for mid-flight generation cancellation + failure capture
(image-gen and video-gen).

IMPORTANT: like test_media_vault.py these do NOT use the spawned-engine
``http`` fixture — everything runs in-process against a FastAPI TestClient
(no lifespan → no services started, no engine touched) or directly against
service/store instances with a STUB pipeline. No real model is ever loaded.
Run with:

    uv run pytest tests/smoke/test_media_gen_cancel.py -v

Covers:
  - POST /image-gen/cancel with nothing running → {cancelled: false,
    reason: "nothing_running"}
  - GET /image-gen/status + /video-gen/status expose is_generating /
    cancel_requested / load_error
  - one-shot cancel mid-flight: a stub pipeline honoring the per-step
    callback aborts with a clean cancelled=True result (never a hang)
  - GenerateResponse carries cancelled=true over the HTTP contract
  - running-job cancel semantics at the store level (image + video)
  - the image job runner marks a mid-flight-cancelled job "cancelled" and a
    crashing job "failed" — and the worker loop survives to run the next job
  - a failed model load clears is_loading and surfaces load_error
  - OOM → friendly error text; callback hook mode detection; PROTECTED_PARAMS
    blocks user-supplied pipeline callbacks
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services.image_gen.jobs import ImageJobRunner, ImageJobStore
from app.services.image_gen.models import IMAGE_GEN_MODELS
from app.services.image_gen.service import ImageGenService
from app.services.media_gen.cancellation import (
    GenerationCancelled,
    friendly_generation_error,
    install_cancel_hook,
)
from app.services.media_gen.params import merge_extra_params
from app.services.video_gen.jobs import VideoJobStore

# Any catalog model works — the pipeline is stubbed; sdxl-turbo has 1 rec step
# so tests pass explicit steps to keep the stub spinning long enough to cancel.
MODEL = next(
    m for m in IMAGE_GEN_MODELS if m.pipeline_type == "stable-diffusion-xl"
)


@pytest.fixture(autouse=True)
def _force_image_gen_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests use STUB pipelines — they must be hermetic on CI where the
    optional image-gen packages (diffusers/transformers/accelerate) are not
    installed. Without this, ImageGenService.available is False and every
    stub generation bails with "requires optional packages" before running
    (exactly what broke CI on 2026-07-10)."""
    from app.services.image_gen import service as service_module

    monkeypatch.setattr(service_module, "DEPS_AVAILABLE", True)
    monkeypatch.setattr(service_module, "DEPS_REASON", "")


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app
    # No context manager on purpose: lifespan must NOT run (it would start
    # engine services). Plain requests still traverse the middleware stack.
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


class SlowFakePipe:
    """Stub diffusers pipeline: sleeps per step and honors the modern
    callback_on_step_end contract, so the engine's cancel hook can abort it
    exactly like a real pipeline. Never touches the GPU."""

    def __init__(self, steps_delay: float = 0.05) -> None:
        self.steps_delay = steps_delay
        self.steps_run = 0

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
        for step in range(num_inference_steps):
            time.sleep(self.steps_delay)
            self.steps_run = step + 1
            if callback_on_step_end is not None:
                callback_on_step_end(self, step, 0, {})
        raise AssertionError(
            "SlowFakePipe ran to completion — the test should have cancelled it"
        )


def make_stub_service(pipe: Any) -> ImageGenService:
    """A fresh (non-singleton) ImageGenService with a stub pipeline injected
    as if MODEL were loaded on CPU."""
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


def _isolate_video_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services.video_gen import jobs as jobs_module
    monkeypatch.setattr(
        jobs_module, "generated_videos_dir", lambda: tmp_path / "vid-jobs"
    )


# ── HTTP contract ─────────────────────────────────────────────────────────────

def test_cancel_endpoint_nothing_running(client: TestClient) -> None:
    r = client.post("/image-gen/cancel")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["cancelled"] is False
    assert data["reason"] == "nothing_running"
    assert data["job_id"] is None


def test_image_status_exposes_cancellation_state(client: TestClient) -> None:
    r = client.get("/image-gen/status")
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ("is_generating", "cancel_requested", "load_error"):
        assert key in data, f"/image-gen/status missing {key}: {list(data)}"
    assert data["is_generating"] is False
    assert data["cancel_requested"] is False


def test_video_status_exposes_cancellation_state(client: TestClient) -> None:
    r = client.get("/video-gen/status")
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ("cancel_requested", "load_error", "active_job_id"):
        assert key in data, f"/video-gen/status missing {key}: {list(data)}"
    assert data["cancel_requested"] is False


def test_generate_response_carries_cancelled_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /image-gen/generate surfaces cancelled=true from the service."""
    from app.api import image_gen_routes
    from app.services.image_gen.service import GenerationResult

    class CancelledService:
        available = True
        unavailable_reason = ""

        async def generate(self, **kwargs: Any) -> GenerationResult:
            return GenerationResult(
                success=False, error="Cancelled", cancelled=True,
                model_id=MODEL.model_id,
            )

    monkeypatch.setattr(
        image_gen_routes, "get_image_gen_service", lambda: CancelledService()
    )
    r = client.post("/image-gen/generate", json={
        "prompt": "a test prompt", "model_id": MODEL.model_id,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["success"] is False
    assert data["cancelled"] is True
    assert data["error"] == "Cancelled"


def test_delete_unknown_jobs_404(client: TestClient) -> None:
    assert client.delete("/image-gen/jobs/no-such-job").status_code == 404
    assert client.delete("/video-gen/jobs/no-such-job").status_code == 404


# ── one-shot mid-flight cancel (service level, stub pipeline) ─────────────────

def test_oneshot_cancel_mid_flight() -> None:
    pytest.importorskip("torch")
    pipe = SlowFakePipe()
    svc = make_stub_service(pipe)

    async def main():
        task = asyncio.ensure_future(
            svc.generate(prompt="x", model_id=MODEL.model_id, steps=200)
        )
        deadline = time.monotonic() + 5.0
        while not svc.get_status()["is_generating"]:
            assert time.monotonic() < deadline, "generation never registered"
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.15)  # let the stub start stepping
        res = svc.request_cancel()
        assert res["cancelled"] is True
        assert res["was"] == "oneshot"
        assert res["job_id"] is None
        # status shows Cancelling… until the abort lands
        assert svc.get_status()["cancel_requested"] is True
        return await asyncio.wait_for(task, timeout=10.0)

    result = asyncio.run(main())
    assert result.success is False
    assert result.cancelled is True
    assert result.error == "Cancelled"
    # aborted mid-flight, not after all 200 steps
    assert pipe.steps_run < 200
    # generation slot released; status is clean again
    status = svc.get_status()
    assert status["is_generating"] is False
    assert status["cancel_requested"] is False


def test_request_cancel_twice_second_hits_nothing() -> None:
    svc = ImageGenService()
    assert svc.request_cancel() == {
        "cancelled": False, "reason": "nothing_running",
    }


# ── image job store semantics ─────────────────────────────────────────────────

def test_image_store_running_cancel_semantics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_image_history(monkeypatch, tmp_path)
    store = ImageJobStore()
    job = store.create(prompt="x", model_id=MODEL.model_id)

    assert store.mark_running(job.job_id, total_steps=10, seed=1) is True
    # running → not silently removable; caller escalates to mid-flight cancel
    assert store.cancel(job.job_id) == "running"
    assert store.request_cancel_running(job.job_id) is True
    j = store.get(job.job_id)
    assert j is not None and j.cancel_requested is True and j.status == "running"

    # abort lands: terminal cancelled with partial progress preserved
    store.update_progress(job.job_id, 4, 10)
    store.mark_cancelled(job.job_id)
    j = store.get(job.job_id)
    assert j is not None
    assert j.status == "cancelled"
    assert j.current_step == 4 and j.progress == 0.4
    assert j.finished_at is not None
    # terminal record can now be removed
    assert store.cancel(job.job_id) == "removed"


def test_image_store_queued_cancel_never_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_image_history(monkeypatch, tmp_path)
    store = ImageJobStore()
    job = store.create(prompt="x", model_id=MODEL.model_id)
    assert store.cancel(job.job_id) == "cancelled"
    # the worker must skip it
    assert store.mark_running(job.job_id, total_steps=10, seed=1) is False
    assert store.request_cancel_running(job.job_id) is False


# ── image job runner: mid-flight cancel + worker survival ────────────────────

def test_runner_cancels_running_job_mid_flight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    _isolate_image_history(monkeypatch, tmp_path)
    from app.services.image_gen import service as service_module

    pipe = SlowFakePipe()
    svc = make_stub_service(pipe)
    monkeypatch.setattr(service_module, "get_image_gen_service", lambda: svc)

    store = ImageJobStore()
    runner = ImageJobRunner(store)

    async def main():
        job = store.create(prompt="x", model_id=MODEL.model_id, steps=200)
        runner.ensure_running()
        deadline = time.monotonic() + 5.0
        while True:
            j = store.get(job.job_id)
            assert j is not None
            if j.status == "running" and svc.get_status()["is_generating"]:
                break
            assert time.monotonic() < deadline, f"job never ran: {j.status}"
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.15)

        # DELETE /image-gen/jobs/{id} route behavior, at the component level
        assert store.cancel(job.job_id) == "running"
        assert store.request_cancel_running(job.job_id) is True
        info = svc.request_cancel_job(job.job_id)
        assert info["found"] is True and info["mid_flight"] is True

        deadline = time.monotonic() + 10.0
        while runner.active:
            assert time.monotonic() < deadline, "worker never drained"
            await asyncio.sleep(0.02)
        return store.get(job.job_id)

    j = asyncio.run(main())
    assert j is not None
    assert j.status == "cancelled"
    assert j.cancel_requested is True
    assert j.error is None  # a cancel is not a failure
    assert pipe.steps_run < 200


def test_worker_survives_crashing_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A generate() that raises must fail THAT job and the loop must go on to
    the next one — the queue never dies silently."""
    _isolate_image_history(monkeypatch, tmp_path)
    from app.services.image_gen import service as service_module

    calls: list[str] = []

    class ExplodingService:
        def get_model(self, model_id: str):
            return MODEL

        async def generate(self, **kwargs: Any):
            calls.append(kwargs["prompt"])
            if kwargs["prompt"] == "boom":
                raise RuntimeError("synthetic crash inside generate")
            return SimpleNamespace(
                success=False, cancelled=False, error="stub failure",
                item_id=None, file_path=None, elapsed_seconds=0.0,
            )

    monkeypatch.setattr(
        service_module, "get_image_gen_service", lambda: ExplodingService()
    )
    store = ImageJobStore()
    runner = ImageJobRunner(store)

    async def main():
        # max_attempts=1 keeps this test about the ONE thing it pins: a crash
        # must not kill the drain. The retry ladder itself (a crash normally
        # gets 3 attempts with backoff) is pinned in
        # tests/characterization/test_image_job_queue.py.
        j1 = store.create(prompt="boom", model_id=MODEL.model_id, max_attempts=1)
        j2 = store.create(prompt="after", model_id=MODEL.model_id, max_attempts=1)
        runner.ensure_running()
        deadline = time.monotonic() + 10.0
        while runner.active:
            assert time.monotonic() < deadline, "worker never drained"
            await asyncio.sleep(0.02)
        return store.get(j1.job_id), store.get(j2.job_id)

    job1, job2 = asyncio.run(main())
    assert calls == ["boom", "after"], "worker did not continue past the crash"
    assert job1 is not None and job1.status == "failed"
    assert "synthetic crash" in (job1.error or "")
    assert job2 is not None and job2.status == "failed"
    assert job2.error == "stub failure"


# ── load failure: is_loading cleared, load_error surfaced ─────────────────────

def test_load_failure_clears_is_loading_and_surfaces_load_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("diffusers")
    from app.services.image_gen import service as service_module

    # Point the models dir at an empty tmp dir: from_pretrained fails fast on
    # the missing local weights — a real load failure without any download.
    monkeypatch.setattr(
        service_module, "image_models_dir", lambda: tmp_path / "no-models"
    )
    svc = ImageGenService()
    result = svc._load_model_sync(MODEL)

    assert result["success"] is False
    assert result["error"]
    assert svc.is_loading is False, "is_loading may NEVER stay true after a failed load"
    status = svc.get_status()
    assert status["load_error"] == result["error"]
    assert status["loaded_model_id"] is None


# ── video job store semantics ─────────────────────────────────────────────────

def test_video_store_cancel_semantics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_video_history(monkeypatch, tmp_path)
    store = VideoJobStore()

    # queued → cancelled frees the one-job-at-a-time slot and never runs
    j1 = store.create(prompt="x", model_id="Lightricks/LTX-Video", seed=1)
    assert store.cancel(j1.job_id) == "cancelled"
    assert store.active_job_id is None
    assert store.mark_running(j1.job_id, total_steps=10) is False

    # a new job is allowed now; running → escalation contract
    j2 = store.create(prompt="y", model_id="Lightricks/LTX-Video", seed=2)
    assert store.mark_running(j2.job_id, total_steps=30) is True
    assert store.cancel(j2.job_id) == "running"
    assert store.request_cancel_running(j2.job_id) is True
    job = store.get(j2.job_id)
    assert job is not None and job.cancel_requested is True

    store.update_progress(j2.job_id, 7, 30)
    store.mark_cancelled(j2.job_id)
    job = store.get(j2.job_id)
    assert job is not None
    assert job.status == "cancelled"
    assert job.current_step == 7  # partial progress preserved
    assert store.active_job_id is None  # slot freed
    assert store.cancel(j2.job_id) == "removed"


# ── cancellation plumbing units ───────────────────────────────────────────────

def test_install_cancel_hook_modern_and_none() -> None:
    event = threading.Event()

    class ModernPipe:
        def __call__(self, prompt, callback_on_step_end=None): ...

    class NoCallbackPipe:
        def __call__(self, prompt): ...

    kwargs: dict[str, Any] = {}
    assert install_cancel_hook(
        ModernPipe(), kwargs, cancel_event=event, total_steps=5
    ) == "modern"
    # not cancelled: the hook passes through
    assert kwargs["callback_on_step_end"](None, 0, 0, {"a": 1}) == {"a": 1}
    # cancelled: the hook aborts the pipeline
    event.set()
    with pytest.raises(GenerationCancelled):
        kwargs["callback_on_step_end"](None, 1, 0, {})

    assert install_cancel_hook(
        NoCallbackPipe(), {}, cancel_event=threading.Event(), total_steps=5
    ) == "none"


def test_install_cancel_hook_legacy() -> None:
    event = threading.Event()

    class LegacyPipe:
        def __call__(self, prompt, callback=None, callback_steps=1): ...

    kwargs: dict[str, Any] = {}
    assert install_cancel_hook(
        LegacyPipe(), kwargs, cancel_event=event, total_steps=5
    ) == "legacy"
    assert kwargs["callback_steps"] == 1
    kwargs["callback"](0, 0, None)  # no cancel → no raise
    event.set()
    with pytest.raises(GenerationCancelled):
        kwargs["callback"](1, 0, None)


def test_friendly_generation_error_maps_oom() -> None:
    class OutOfMemoryError(Exception):  # same NAME as torch.cuda's
        pass

    assert "Out of memory" in friendly_generation_error(OutOfMemoryError("CUDA"))
    assert "Out of memory" in friendly_generation_error(
        RuntimeError("MPS backend out of memory (MPS allocated: 17.1 GB)")
    )
    assert friendly_generation_error(RuntimeError("something else")) == "something else"


def test_extra_params_cannot_displace_cancel_hook() -> None:
    with pytest.raises(ValueError, match="callback_on_step_end"):
        merge_extra_params(
            {"prompt": "x"}, {"callback_on_step_end": "sneaky"}
        )
    with pytest.raises(ValueError, match="callback"):
        merge_extra_params({"prompt": "x"}, {"callback": "sneaky"})
