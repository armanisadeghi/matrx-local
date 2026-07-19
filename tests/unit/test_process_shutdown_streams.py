from __future__ import annotations

import asyncio
import time

import pytest

from app.api import routes, setup_routes, wake_word_routes
from app.common.process_shutdown import (
    process_shutdown_event,
    request_process_shutdown,
)
from app.services.downloads.manager import DownloadManager
from app.services.permissions import checker as permission_checker


@pytest.fixture(autouse=True)
def _reset_process_shutdown_event():
    process_shutdown_event.clear()
    yield
    process_shutdown_event.clear()


@pytest.mark.anyio
async def test_setup_status_gpu_probe_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_gpu_probe() -> tuple[bool, str | None]:
        time.sleep(0.15)
        return True, "Test GPU"

    async def permissions() -> setup_routes.ComponentStatus:
        return setup_routes.ComponentStatus(
            id="permissions",
            label="Permissions",
            description="test",
            status="ready",
        )

    monkeypatch.setattr(setup_routes, "_check_gpu", slow_gpu_probe)
    monkeypatch.setattr(setup_routes, "_check_permissions", permissions)

    started = time.monotonic()
    request_task = asyncio.create_task(setup_routes.get_setup_status())
    await asyncio.sleep(0.02)

    # If _check_gpu ran on this loop, the 20ms timer could not fire until the
    # 150ms blocking probe completed.
    assert time.monotonic() - started < 0.10
    result = await request_task
    assert result.gpu_name == "Test GPU"


@pytest.mark.anyio
async def test_download_stream_unsubscribes_on_process_shutdown() -> None:
    manager = DownloadManager()
    stream = manager.sse_stream()
    pending = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    assert len(manager._subscribers) == 1

    request_process_shutdown()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, timeout=0.75)
    assert manager._subscribers == []


@pytest.mark.anyio
async def test_access_log_stream_unsubscribes_on_process_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[dict] = asyncio.Queue()
    unsubscribed: list[asyncio.Queue[dict]] = []
    monkeypatch.setattr(routes.access_log, "subscribe", lambda: queue)
    monkeypatch.setattr(routes.access_log, "unsubscribe", unsubscribed.append)

    response = await routes.stream_access_log()
    pending = asyncio.create_task(response.body_iterator.__anext__())
    await asyncio.sleep(0)
    request_process_shutdown()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, timeout=0.75)
    assert unsubscribed == [queue]


@pytest.mark.anyio
async def test_system_log_stream_exits_on_process_shutdown(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "system.log").write_text("")
    monkeypatch.setattr(routes, "LOG_DIR", tmp_path)
    response = await routes.stream_system_log()

    request_process_shutdown()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(response.body_iterator.__anext__(), timeout=0.25)


@pytest.mark.anyio
async def test_setup_log_stream_exits_on_process_shutdown() -> None:
    class Request:
        async def is_disconnected(self) -> bool:
            return False

    request_process_shutdown()
    response = await setup_routes.stream_logs(Request())

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(response.body_iterator.__anext__(), timeout=0.25)


@pytest.mark.anyio
async def test_wake_word_stream_exits_on_process_shutdown() -> None:
    response = await wake_word_routes.event_stream()
    assert await response.body_iterator.__anext__() == "event: ping\ndata: {}\n\n"

    request_process_shutdown()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(response.body_iterator.__anext__(), timeout=0.25)


@pytest.mark.anyio
async def test_permission_scan_cancels_probes_on_process_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_probe() -> permission_checker.PermissionResult:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(
        permission_checker,
        "PERMISSION_CHECKERS",
        {"slow": slow_probe},
    )
    scan = asyncio.create_task(permission_checker.check_all_permissions())
    await started.wait()

    request_process_shutdown()

    assert await asyncio.wait_for(scan, timeout=0.5) == []
    assert cancelled.is_set()
