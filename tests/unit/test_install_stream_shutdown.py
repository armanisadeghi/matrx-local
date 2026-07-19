from __future__ import annotations

import asyncio

import pytest

from app.api import capabilities_routes, image_gen_routes, wake_word_routes
from app.common.process_shutdown import (
    process_shutdown_event,
    request_process_shutdown,
)


@pytest.fixture(autouse=True)
def _reset_shutdown() -> None:
    process_shutdown_event.clear()
    yield
    process_shutdown_event.clear()


class _BlockingProgress:
    status = "running"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def events(self):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()
        if False:
            yield {}


@pytest.mark.anyio
async def test_capability_install_stream_reaps_pump_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = _BlockingProgress()
    monkeypatch.setattr(capabilities_routes, "uses_managed_installer", lambda _id: True)
    monkeypatch.setattr(
        capabilities_routes,
        "get_active_progress",
        lambda _id: progress,
    )
    monkeypatch.setattr(
        capabilities_routes,
        "is_capability_installed",
        lambda _id: False,
    )
    response = await capabilities_routes.stream_install_progress("image-gen")
    assert "connected" in await response.body_iterator.__anext__()
    pending = asyncio.create_task(response.body_iterator.__anext__())
    await progress.started.wait()

    request_process_shutdown()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, timeout=0.75)
    assert progress.cancelled.is_set()


@pytest.mark.anyio
async def test_wake_word_download_is_cancelled_and_reaped_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def download(_name: str, *, on_progress):
        del on_progress
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(wake_word_routes, "download_model", download)
    response = await wake_word_routes.download_model_with_progress(
        wake_word_routes.DownloadRequest(model_name="test-model")
    )
    pending = asyncio.create_task(response.body_iterator.__anext__())
    await started.wait()

    request_process_shutdown()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, timeout=0.75)
    assert cancelled.is_set()
