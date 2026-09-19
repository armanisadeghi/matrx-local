"""Closed-lifecycle transport tests: bounds, source correlation, and fences."""
from __future__ import annotations

import asyncio
import json

import pytest
import httpx
from fastapi import FastAPI
from starlette.websockets import WebSocketState

import app.api.extension_ws_manager as manager
import app.api.local_browser_transport as route
import app.services.local_browser_transport as transport
from app.api.auth import AuthMiddleware
from app.services.local_browser_context import BrowserContext, FreshContext, LocalBrowserContext


class Socket:
    client_state = WebSocketState.CONNECTED
    application_state = WebSocketState.CONNECTED

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def send_text(self, value: str) -> None:
        self.frames.append(value)


def _registration(registry: manager.ExtensionSessionRegistry):
    socket = Socket()
    session = registry.register(socket)  # type: ignore[arg-type]
    assert registry.register_local_browser(
        session.session_id, engine_boot_id="boot", revision=3,
        owner=("user", "session"), organization_id="org", device_id="device",
        extension_generation="11111111-1111-4111-8111-111111111111",
        connection_id="22222222-2222-4222-8222-222222222222",
    )
    return session, socket, registry.current_local_browser(
        engine_boot_id="boot", revision=3, owner=("user", "session"), organization_id="org", device_id="device",
    )


def test_closed_json_rejects_duplicate_nested_and_oversized_grants():
    assert transport._bounded_json(b'{"grant":"x","operation":"discover"}') == {"grant": "x", "operation": "discover"}
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(b'{"grant":"x","grant":"y","operation":"discover"}')
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(json.dumps({"grant": "x" * (9 * 1024), "operation": "discover"}).encode())
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(b'{"grant":"x","operation":"claim"}')


@pytest.mark.anyio
async def test_local_results_require_the_exact_socket_and_are_fenced_on_context_change(monkeypatch):
    registry = manager.ExtensionSessionRegistry()
    session, socket, registration = _registration(registry)
    assert registration is not None
    future = registry.create_local_result(registration, "private-call")
    assert future is not None
    other = Socket()
    assert not registry.resolve_local_result(session.session_id, other, "private-call", {"status": "acknowledged"})  # type: ignore[arg-type]
    assert registry.resolve_local_result(session.session_id, socket, "private-call", {"status": "acknowledged"})  # type: ignore[arg-type]
    assert (await future)["status"] == "acknowledged"

    calls: list[BrowserContext] = []
    grant = "header.eyJzdWIiOiAidXNlciIsICJzZXNzaW9uX2lkIjogIjExMTExMTExLTExMTEtNDExMS04MTExLTExMTExMTExMTExMSJ9.signature"
    context = LocalBrowserContext("boot", lambda: asyncio.sleep(0, result=(grant, "user")))
    context.subscribe(calls.append)
    assert await context.refresh() is not None
    assert calls and calls[-1].revision == 1


@pytest.mark.anyio
async def test_execute_rechecks_context_and_device_after_server_await(monkeypatch):
    token = "header.eyJzdWIiOiAidXNlciIsICJzZXNzaW9uX2lkIjogIjExMTExMTExLTExMTEtNDExMS04MTExLTExMTExMTExMTExMSJ9.signature"
    fresh = FreshContext(BrowserContext("boot", 3, "org"), ("user", "session"), token)

    class Context:
        calls = 0
        async def refresh(self):
            self.calls += 1
            return fresh if self.calls == 1 else None

    class Device:
        app_instance_id = "device"
        user_id = "user"
    class Instance:
        async def registered_device_identity(self): return Device()

    class Limits:
        async def acquire(self, _address):
            return asyncio.Semaphore(1), asyncio.Semaphore(1)

    monkeypatch.setattr(transport, "_LIMITS", Limits())
    monkeypatch.setattr(transport, "get_local_browser_context", lambda: Context())
    monkeypatch.setattr(transport, "get_instance_manager", lambda: Instance())
    monkeypatch.setattr(transport, "ensure_context_subscription", lambda: None)
    monkeypatch.setattr(transport, "current_local_browser_registration", lambda **_kw: object())
    async def verify(*_args): return {"status": "acknowledged"}
    monkeypatch.setattr(transport, "_verify", verify)
    with pytest.raises(transport.TransportRefusal) as refused:
        await transport.execute_lifecycle({"grant": "x", "operation": "discover"})
    assert refused.value.reason == "binding_changed"


@pytest.mark.anyio
async def test_execute_route_bypasses_normal_bearer_but_never_loses_no_store(monkeypatch):
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(route.router)

    async def refused(_body, *, address):
        assert address == "127.0.0.1"
        raise transport.TransportRefusal("authority_refused", 403)

    monkeypatch.setattr(route, "execute_lifecycle", refused)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 22242)), base_url="http://engine.test") as client:
        response = await client.post("/local-browser/execute?grant=must-not-be-used", content=b'{"grant":"secret","operation":"discover"}')
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"status": "refused", "operation": "discover", "reason": "authority_refused"}
