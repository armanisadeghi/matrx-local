"""Lifecycle contracts for the diagnostic extension bridge socket."""

from __future__ import annotations

import asyncio
import json


def test_bridge_events_consumes_disconnect_and_releases_subscriber(monkeypatch) -> None:
    from app.api import extension_bridge_routes as routes

    class DisconnectingWebSocket:
        def __init__(self) -> None:
            self.accepted = False
            self.accepted_event = asyncio.Event()
            self.receive_calls = 0
            self.sent: list[dict[str, object]] = []

        async def accept(self) -> None:
            self.accepted = True
            self.accepted_event.set()

        async def send_text(self, value: str) -> None:
            self.sent.append(json.loads(value))

        async def receive(self) -> dict[str, object]:
            self.receive_calls += 1
            if self.receive_calls == 1:
                return {"type": "websocket.connect"}
            await self.accepted_event.wait()
            return {"type": "websocket.disconnect", "code": 1001}

    async def validate(_websocket):
        return object()

    subscribers: list[asyncio.Queue[dict[str, object]]] = []
    monkeypatch.setattr(routes, "_EVENT_SUBSCRIBERS", subscribers)
    monkeypatch.setattr(routes, "validate_extension_principal_ws", validate)
    websocket = DisconnectingWebSocket()

    asyncio.run(
        asyncio.wait_for(routes.bridge_events_websocket(websocket), timeout=0.5)
    )

    assert websocket.accepted is True
    assert websocket.sent[0]["kind"] == "bridge-events.hello"
    assert subscribers == []


def test_bridge_events_does_not_accept_after_disconnect_during_auth(monkeypatch) -> None:
    from app.api import extension_bridge_routes as routes

    class DisconnectingDuringAuthWebSocket:
        def __init__(self) -> None:
            self.accept_calls = 0
            self.receive_calls = 0

        async def accept(self) -> None:
            self.accept_calls += 1

        async def receive(self) -> dict[str, object]:
            self.receive_calls += 1
            if self.receive_calls == 1:
                return {"type": "websocket.connect"}
            return {"type": "websocket.disconnect", "code": 1001}

    auth_cancelled = asyncio.Event()

    async def validate(_websocket):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            auth_cancelled.set()
            raise

    subscribers: list[asyncio.Queue[dict[str, object]]] = []
    monkeypatch.setattr(routes, "_EVENT_SUBSCRIBERS", subscribers)
    monkeypatch.setattr(routes, "validate_extension_principal_ws", validate)
    websocket = DisconnectingDuringAuthWebSocket()

    asyncio.run(
        asyncio.wait_for(routes.bridge_events_websocket(websocket), timeout=0.5)
    )

    assert websocket.accept_calls == 0
    assert auth_cancelled.is_set()
    assert subscribers == []


def test_bridge_events_contains_final_accept_shutdown_race(monkeypatch) -> None:
    from app.api import extension_bridge_routes as routes

    class ClosingBeforeAcceptWebSocket:
        def __init__(self) -> None:
            self.accept_calls = 0
            self.receive_calls = 0

        async def accept(self) -> None:
            self.accept_calls += 1
            raise RuntimeError(
                "Expected ASGI message 'websocket.send' or 'websocket.close', "
                "but got 'websocket.accept'."
            )

        async def receive(self) -> dict[str, object]:
            self.receive_calls += 1
            if self.receive_calls == 1:
                return {"type": "websocket.connect"}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def validate(_websocket):
        await asyncio.sleep(0)
        return object()

    subscribers: list[asyncio.Queue[dict[str, object]]] = []
    monkeypatch.setattr(routes, "_EVENT_SUBSCRIBERS", subscribers)
    monkeypatch.setattr(routes, "validate_extension_principal_ws", validate)
    websocket = ClosingBeforeAcceptWebSocket()

    asyncio.run(
        asyncio.wait_for(routes.bridge_events_websocket(websocket), timeout=0.5)
    )

    assert websocket.accept_calls == 1
    assert subscribers == []


def test_bridge_events_drains_auth_when_handler_is_cancelled(monkeypatch) -> None:
    from app.api import extension_bridge_routes as routes

    class WaitingWebSocket:
        def __init__(self) -> None:
            self.accept_calls = 0
            self.receive_calls = 0

        async def accept(self) -> None:
            self.accept_calls += 1

        async def receive(self) -> dict[str, object]:
            self.receive_calls += 1
            if self.receive_calls == 1:
                return {"type": "websocket.connect"}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    auth_started = asyncio.Event()
    auth_cancelled = asyncio.Event()

    async def validate(_websocket):
        auth_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            auth_cancelled.set()
            raise

    async def scenario() -> None:
        websocket = WaitingWebSocket()
        task = asyncio.create_task(routes.bridge_events_websocket(websocket))
        await asyncio.wait_for(auth_started.wait(), timeout=0.5)
        task.cancel()
        await asyncio.wait_for(task, timeout=0.5)
        assert websocket.accept_calls == 0

    monkeypatch.setattr(routes, "_EVENT_SUBSCRIBERS", [])
    monkeypatch.setattr(routes, "validate_extension_principal_ws", validate)

    asyncio.run(scenario())
    assert auth_cancelled.is_set()
