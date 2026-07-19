"""Status and cancel must never queue behind a model load.

The v1.3.145 freeze: ImageGenService used ONE threading.Lock for both pipeline
lifecycle and active-generation bookkeeping. `_load_model_sync` holds it for the
whole load (import torch, from_pretrained, pipe.to(device) -- ~50s), while
`GET /image-gen/status` called `get_status()` SYNCHRONOUSLY inside `async def`
(app/api/image_gen_routes.py). The event loop parked on the mutex, so nine
unrelated endpoints -- media-library, video-gen, prompt-matrix -- all hit their
30s client timeout without ever being dequeued.

The fix splits the two concerns: `_lock` (pipeline lifecycle, long-held) and
`_gens_lock` (active generations, microseconds). These tests hold `_lock` the
way a load does and assert the loop-facing calls still return promptly.
"""

from __future__ import annotations

import threading
import time

from app.services.image_gen.service import ImageGenService
from app.services.video_gen.service import VideoGenService

# A real load takes ~50s. Anything above this while _lock is held means the
# caller is serialized behind the load, which is the shipped bug.
_MAX_BLOCKED_SECONDS = 0.5


def _hold_pipeline_lock(service, released: threading.Event) -> threading.Thread:
    """Stand in for _load_model_sync, which holds _lock across the whole load."""
    holding = threading.Event()

    def hold() -> None:
        with service._lock:
            holding.set()
            released.wait(timeout=10)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert holding.wait(timeout=5), "helper never acquired the pipeline lock"
    return thread


def _time_while_load_holds_lock(service, call) -> float:
    """Return how long ``call`` takes while a load holds the pipeline lock.

    ``call`` is invoked once BEFORE timing to warm it. get_status() probes
    diffusers/torch on its first run (``select_device`` -> ``torch.cuda.is_available()``
    performs driver init on a CUDA host), and timing that alongside the lock
    would fail this test on GPU CI with a message blaming the lock.
    """
    call()
    released = threading.Event()
    thread = _hold_pipeline_lock(service, released)
    try:
        start = time.perf_counter()
        call()
        return time.perf_counter() - start
    finally:
        released.set()
        thread.join(timeout=5)


def test_image_status_does_not_block_on_a_model_load() -> None:
    service = ImageGenService()
    elapsed = _time_while_load_holds_lock(service, service.get_status)

    assert elapsed < _MAX_BLOCKED_SECONDS, (
        f"get_status() blocked {elapsed:.2f}s behind a model load -- this is the "
        "v1.3.145 engine freeze"
    )


def test_image_cancel_does_not_block_on_a_model_load() -> None:
    """Cancel is the one call that MUST work while a model is loading."""
    service = ImageGenService()

    def cancel() -> None:
        service.request_cancel()
        service.request_cancel_job("job-1")

    elapsed = _time_while_load_holds_lock(service, cancel)

    assert elapsed < _MAX_BLOCKED_SECONDS, (
        f"cancel blocked {elapsed:.2f}s behind a model load"
    )


def test_video_cancel_does_not_block_on_a_model_load() -> None:
    """Same defect in video-gen: DELETE /video-gen/jobs/{id} -> request_cancel_job."""
    service = VideoGenService()
    elapsed = _time_while_load_holds_lock(
        service, lambda: service.request_cancel_job("job-1")
    )

    assert elapsed < _MAX_BLOCKED_SECONDS, (
        f"video cancel blocked {elapsed:.2f}s behind a model load"
    )


def test_video_status_does_not_block_on_a_model_load() -> None:
    """`GET /video-gen/status` is served from three routes, all on the loop.

    VideoGenService.get_status() reads load state WITHOUT any lock today, so it
    is safe only by omission. The obvious next "correctness" cleanup — wrapping
    it in `with self._lock` — silently reinstates the v1.3.145 freeze. This test
    is what makes that cleanup fail loudly instead.
    """
    service = VideoGenService()
    elapsed = _time_while_load_holds_lock(service, service.get_status)

    assert elapsed < _MAX_BLOCKED_SECONDS, (
        f"video get_status() blocked {elapsed:.2f}s behind a model load"
    )


def test_generation_bookkeeping_uses_a_separate_lock() -> None:
    """Pin the decomposition itself, not just its current timing.

    A future refactor that folds _gens_lock back into _lock would reintroduce
    the freeze while these timing assertions still passed on an idle service.
    """
    for service in (ImageGenService(), VideoGenService()):
        assert service._gens_lock is not service._lock, (
            f"{type(service).__name__} shares one lock between pipeline "
            "lifecycle and generation bookkeeping -- the v1.3.145 freeze"
        )
