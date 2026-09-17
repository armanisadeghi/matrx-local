"""Controlled-ASGI regressions for extension WebSocket disconnect cleanup."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.websockets import WebSocket, WebSocketState

import app.api.extension_routes as extension_routes
import app.api.extension_ws_manager as manager


def _websocket(receive, send) -> WebSocket:
    return WebSocket(
        {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/extension/ws",
            "raw_path": b"/extension/ws",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 22140),
            "subprotocols": [],
        },
        receive=receive,
        send=send,
    )


def test_closed_socket_send_short_circuits_without_retry() -> None:
    async def run() -> None:
        sent: list[dict[str, Any]] = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        websocket = _websocket(receive, send)
        websocket.client_state = WebSocketState.DISCONNECTED
        websocket.application_state = WebSocketState.DISCONNECTED
        session = manager.ExtensionSession("session", websocket)

        assert await session.send({"type": "pong"}) is False
        assert await session.send({"type": "pong"}) is False
        assert sent == []

    asyncio.run(run())


def test_manager_failed_send_unregisters_and_cancels_pending(monkeypatch) -> None:
    async def run() -> None:
        registry = manager.ExtensionSessionRegistry()
        monkeypatch.setattr(manager, "_REGISTRY", registry)

        async def receive():
            return {"type": "websocket.connect"}

        async def send(_message):
            raise RuntimeError("socket closed")

        websocket = _websocket(receive, send)
        websocket.client_state = WebSocketState.CONNECTED
        websocket.application_state = WebSocketState.DISCONNECTED
        session = registry.register(websocket)
        pending = asyncio.get_running_loop().create_future()
        session.pending_calls["call"] = pending

        assert await manager.send_to_extension_session(session.session_id, {"type": "ping"}) is False
        assert registry.active_count == 0
        with pytest.raises(ConnectionError):
            pending.result()

    asyncio.run(run())


def test_unrelated_send_runtime_error_stays_visible() -> None:
    async def run() -> None:
        async def receive():
            return {"type": "websocket.connect"}

        async def send(_message):
            raise RuntimeError("programming defect")

        websocket = _websocket(receive, send)
        websocket.client_state = WebSocketState.CONNECTED
        websocket.application_state = WebSocketState.CONNECTED
        session = manager.ExtensionSession("session", websocket)

        with pytest.raises(RuntimeError, match="programming defect"):
            await session.send({"type": "pong"})

    asyncio.run(run())


def test_failed_pong_exits_route_and_cancels_pending(monkeypatch) -> None:
    """A state-backed failed pong never falls through to another receive."""
    async def run() -> None:
        registry = manager.ExtensionSessionRegistry()
        monkeypatch.setattr(manager, "_REGISTRY", registry)
        pending: asyncio.Future | None = None
        websocket: WebSocket | None = None
        receive_messages = iter(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": json.dumps({"type": "ping"})},
        ]
        )

        async def receive():
            return next(receive_messages)

        async def send(message):
            nonlocal pending
            if message["type"] != "websocket.send":
                return
            payload = json.loads(message["text"])
            if payload["type"] == "hello":
                session = registry.get(payload["session_id"])
                assert session is not None
                pending = asyncio.get_running_loop().create_future()
                session.pending_calls["call"] = pending
                registry.bind_call("call", session.session_id)
            elif payload["type"] == "pong":
                assert websocket is not None
                websocket.application_state = WebSocketState.DISCONNECTED
                raise RuntimeError("socket closed after peer disconnect")

        websocket = _websocket(receive, send)

        async def principal(_websocket):
            return SimpleNamespace(raw_token="test-token")

        async def record(*_args, **_kwargs):
            return None

        monkeypatch.setattr(extension_routes, "validate_extension_principal_ws", principal)
        monkeypatch.setattr(extension_routes, "record_metric", record)
        monkeypatch.setattr(extension_routes, "publish_event", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(extension_routes, "tool_catalog_hash", lambda: "catalog")

        await extension_routes.extension_websocket(websocket)

        assert registry.active_count == 0
        assert pending is not None
        with pytest.raises(ConnectionError):
            pending.result()

    asyncio.run(run())
