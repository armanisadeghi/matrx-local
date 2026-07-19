"""Lifecycle contracts for the diagnostic extension bridge socket."""

from __future__ import annotations

import asyncio
import json


def test_bridge_events_consumes_disconnect_and_releases_subscriber(monkeypatch) -> None:
    from app.api import extension_bridge_routes as routes

    class DisconnectingWebSocket:
        def __init__(self) -> None:
            self.accepted = False
            self.sent: list[dict[str, object]] = []

        async def accept(self) -> None:
            self.accepted = True

        async def send_text(self, value: str) -> None:
            self.sent.append(json.loads(value))

        async def receive(self) -> dict[str, object]:
            await asyncio.sleep(0)
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
