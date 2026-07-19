from __future__ import annotations

import asyncio
import logging
import socket

import pytest
import uvicorn

from app.api import routes, setup_routes, wake_word_routes
from app.common.process_shutdown import (
    ShutdownCancellationMiddleware,
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


@pytest.mark.anyio
async def test_request_cancellation_completes_response_after_process_shutdown() -> None:
    sent: list[dict] = []

    async def capture(message: dict) -> None:
        sent.append(message)

    async def cancelled_app(scope, receive, send) -> None:
        raise asyncio.CancelledError

    middleware = ShutdownCancellationMiddleware(cancelled_app)
    request_process_shutdown()

    await middleware({"type": "http"}, None, capture)

    assert sent == [
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-length", b"0")],
        },
        {"type": "http.response.body", "body": b"", "more_body": False},
    ]


@pytest.mark.anyio
async def test_stream_cancellation_emits_only_terminal_body_on_shutdown() -> None:
    sent: list[dict] = []

    async def capture(message: dict) -> None:
        sent.append(message)

    async def cancelled_stream(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {"type": "http.response.body", "body": b"partial", "more_body": True}
        )
        raise asyncio.CancelledError

    middleware = ShutdownCancellationMiddleware(cancelled_stream)
    request_process_shutdown()

    await middleware({"type": "http"}, None, capture)

    assert sent[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }
    assert sum(message["type"] == "http.response.start" for message in sent) == 1


@pytest.mark.anyio
async def test_partial_fixed_length_cancellation_propagates_on_shutdown() -> None:
    sent: list[dict] = []

    async def capture(message: dict) -> None:
        sent.append(message)

    async def cancelled_download(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"10")],
            }
        )
        await send(
            {"type": "http.response.body", "body": b"partial", "more_body": True}
        )
        raise asyncio.CancelledError

    middleware = ShutdownCancellationMiddleware(cancelled_download)
    request_process_shutdown()

    with pytest.raises(asyncio.CancelledError):
        await middleware({"type": "http"}, None, capture)

    assert len(sent) == 2


@pytest.mark.anyio
async def test_request_cancellation_propagates_during_normal_operation() -> None:
    async def cancelled_app(scope, receive, send) -> None:
        raise asyncio.CancelledError

    middleware = ShutdownCancellationMiddleware(cancelled_app)

    with pytest.raises(asyncio.CancelledError):
        await middleware({"type": "http"}, None, None)


@pytest.mark.anyio
async def test_lifespan_cancellation_always_propagates() -> None:
    async def cancelled_app(scope, receive, send) -> None:
        raise asyncio.CancelledError

    middleware = ShutdownCancellationMiddleware(cancelled_app)
    request_process_shutdown()

    with pytest.raises(asyncio.CancelledError):
        await middleware({"type": "lifespan"}, None, None)


@pytest.mark.anyio
async def test_uvicorn_shutdown_cancellation_finishes_asgi_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_started = asyncio.Event()

    async def blocked_app(scope, receive, send) -> None:
        assert scope["type"] == "http"
        request_started.set()
        await asyncio.Event().wait()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            ShutdownCancellationMiddleware(blocked_app),
            lifespan="off",
            log_level="error",
            timeout_graceful_shutdown=0.05,
        )
    )

    with caplog.at_level(logging.ERROR):
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /blocked HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            await asyncio.wait_for(request_started.wait(), timeout=1)

            request_process_shutdown()
            server.should_exit = True
            response = await asyncio.wait_for(reader.read(), timeout=2)
            await asyncio.wait_for(server_task, timeout=2)
        finally:
            server.should_exit = True
            if not server_task.done():
                server_task.cancel()
                await asyncio.gather(server_task, return_exceptions=True)
            listener.close()
            writer.close()
            await writer.wait_closed()

    assert response.startswith(b"HTTP/1.1 503 Service Unavailable")
    assert not any(
        "ASGI callable returned without" in record.getMessage()
        or "Exception in ASGI application" in record.getMessage()
        for record in caplog.records
    )
