"""Process-wide shutdown signal for long-lived application work.

Uvicorn's ``should_exit`` flag is private to the server loop. Long-lived SSE
generators also need an application-owned signal so they can stop accepting
work and unregister before Uvicorn's graceful connection-drain deadline.
``threading.Event`` is intentional: shutdown can originate in the main signal
handler, the parent watchdog, the tray thread, or the Uvicorn server thread.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any


process_shutdown_event = threading.Event()


def request_process_shutdown() -> None:
    """Announce that this process is beginning graceful shutdown."""
    process_shutdown_event.set()


def process_shutdown_requested() -> bool:
    """Return whether graceful process shutdown has begun."""
    return process_shutdown_event.is_set()


class ShutdownCancellationMiddleware:
    """Safely complete cancellable HTTP responses during announced shutdown.

    Uvicorn cancels any connection that outlives its bounded graceful-drain
    window.  Starlette's ``BaseHTTPMiddleware`` otherwise lets the resulting
    ``CancelledError`` escape to Uvicorn, which emits an alarming ASGI
    traceback even though shutdown is intentional and completes cleanly. It
    is not enough to swallow cancellation: Uvicorn then reports that the ASGI
    callable returned without completing its response. Track the response
    boundary and emit the missing terminal ASGI message instead.

    Only announced process shutdowns on unstarted or chunked HTTP responses
    are normalized. Partial fixed-length responses, normal-operation
    cancellations, WebSockets, and lifespan always propagate unchanged.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        response_started = False
        response_completed = False
        response_content_length: int | None = None
        response_body_bytes = 0

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal response_started, response_completed
            nonlocal response_content_length, response_body_bytes
            await send(message)
            message_type = message.get("type")
            if message_type == "http.response.start":
                response_started = True
                for name, value in message.get("headers", []):
                    if name.lower() == b"content-length":
                        try:
                            response_content_length = int(value)
                        except (TypeError, ValueError):
                            response_content_length = None
                        break
            elif message_type == "http.response.body" and not message.get(
                "more_body", False
            ):
                response_body_bytes += len(message.get("body", b""))
                response_completed = True
            elif message_type == "http.response.body":
                response_body_bytes += len(message.get("body", b""))

        try:
            await self.app(scope, receive, tracked_send)
        except asyncio.CancelledError:
            if scope.get("type") != "http":
                raise
            if not process_shutdown_requested():
                raise
            # A partial fixed-length response cannot be truthfully completed
            # without fabricating the missing bytes. Preserve cancellation in
            # that case; Uvicorn will close the connection and retain the real
            # failure signal instead of logging a synthetic protocol error.
            if (
                response_started
                and response_content_length is not None
                and response_body_bytes < response_content_length
            ):
                raise
            if not response_started:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [(b"content-length", b"0")],
                    }
                )
            if not response_completed:
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    }
                )
