"""Closed-lifecycle transport tests: bounds, source correlation, and fences."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

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
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(b'{"grant":"\\ud800","operation":"discover"}')
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json((b"[" * 16_000) + (b"]" * 16_000))


@pytest.mark.anyio
async def test_server_callback_uses_actual_closed_contract_and_streams_limit(monkeypatch):
    sent: dict[str, object] = {}
    payload = {
        "status": "accepted", "operation": "admit",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "app_instance_id": "00000000-0000-0000-0000-000000000002",
        "controller_revision": 7,
        "jti": "00000000-0000-0000-0000-000000000003",
        "expires_at_ms": 4_000_000_000_000,
        "extension_generation": "00000000-0000-0000-0000-000000000004",
        "connection_id": "00000000-0000-0000-0000-000000000005",
    }

    class Response:
        status_code = 200
        headers = {"cache-control": "no-store"}
        async def aiter_bytes(self):
            yield json.dumps(payload).encode()

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        @asynccontextmanager
        async def stream(self, method, url, json):
            sent.update(method=method, url=url, body=json)
            yield Response()

    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)
    monkeypatch.setattr(transport, "get_aidream_server_url", lambda: "https://server.example")
    fresh = FreshContext(BrowserContext("boot", 1, "org"), ("user", "session"), "daemon")
    registration = type("Registration", (), {"extension_generation": "00000000-0000-0000-0000-000000000004", "connection_id": "00000000-0000-0000-0000-000000000005"})()
    result = await transport._verify(fresh, "00000000-0000-0000-0000-000000000002", registration, {"grant": "opaque", "operation": "admit"})
    assert result["status"] == "accepted"
    assert sent["body"] == {"grant": "opaque", "operation": "admit", "app_instance_id": "00000000-0000-0000-0000-000000000002"}

    payload["connection_id"] = "00000000-0000-0000-0000-000000000099"
    with pytest.raises(transport.TransportRefusal) as wrong_pair:
        await transport._verify(fresh, "00000000-0000-0000-0000-000000000002", registration, {"grant": "opaque", "operation": "admit"})
    assert wrong_pair.value.reason == "binding_changed"
    payload["connection_id"] = "00000000-0000-0000-0000-000000000005"
    payload["expires_at_ms"] = 1
    with pytest.raises(transport.TransportRefusal):
        await transport._verify(fresh, "00000000-0000-0000-0000-000000000002", registration, {"grant": "opaque", "operation": "admit"})

    class TooLarge(Response):
        async def aiter_bytes(self):
            yield b"x" * (4 * 1024 + 1)
    with pytest.raises(transport.TransportRefusal):
        await transport._read_response(TooLarge())


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
@pytest.mark.parametrize(
    ("operation", "result", "expected"),
    [
        ("admit", {"status": "acknowledged", "operation": "admit", "receipt": "failed"}, {"status": "acknowledged", "operation": "admit", "receipt": "failed"}),
        ("cleanup", {"status": "acknowledged", "operation": "cleanup", "receipt": "unconfirmed"}, {"status": "acknowledged", "operation": "cleanup", "receipt": "unconfirmed"}),
    ],
)
async def test_dispatch_propagates_only_the_exact_extension_receipt(monkeypatch, operation, result, expected):
    future = asyncio.get_running_loop().create_future()
    future.set_result(result)
    monkeypatch.setattr(transport, "create_local_browser_future", lambda *_: future)
    async def sent(*_): return True
    monkeypatch.setattr(transport, "send_local_browser_execute", sent)
    monkeypatch.setattr(transport, "drop_local_browser_future", lambda *_: None)
    entry = transport._ReplayEntry(b"digest", transport._ReplayIdentity(("u", "s"), "b", 1, "o", "d", "g", "c", 1), 4_000_000_000_000, asyncio.get_running_loop().create_future())
    assert await transport._dispatch(object(), {"grant": "opaque", "operation": operation}, entry) == expected


@pytest.mark.anyio
async def test_dispatch_refuses_unknown_extension_receipt(monkeypatch):
    future = asyncio.get_running_loop().create_future()
    future.set_result({"status": "acknowledged", "operation": "cleanup", "receipt": "invented"})
    monkeypatch.setattr(transport, "create_local_browser_future", lambda *_: future)
    async def sent(*_): return True
    monkeypatch.setattr(transport, "send_local_browser_execute", sent)
    monkeypatch.setattr(transport, "drop_local_browser_future", lambda *_: None)
    entry = transport._ReplayEntry(b"digest", transport._ReplayIdentity(("u", "s"), "b", 1, "o", "d", "g", "c", 1), 4_000_000_000_000, asyncio.get_running_loop().create_future())
    with pytest.raises(transport.TransportRefusal):
        await transport._dispatch(object(), {"grant": "opaque", "operation": "cleanup"}, entry)


@pytest.mark.anyio
async def test_replay_joins_exact_bytes_and_identity_but_refuses_conflicts():
    table = transport._ReplayTable()
    identity = transport._ReplayIdentity(("u", "s"), "boot", 1, "org", "device", "gen", "conn", 1)
    first, creator = await table.join_or_create(jti="00000000-0000-0000-0000-000000000001", raw=b'{"grant":"a"}', identity=identity, expires_at_ms=4_000_000_000_000)
    second, joined = await table.join_or_create(jti="00000000-0000-0000-0000-000000000001", raw=b'{"grant":"a"}', identity=identity, expires_at_ms=4_000_000_000_000)
    assert creator and not joined and first is second
    with pytest.raises(transport.TransportRefusal) as conflicting_bytes:
        await table.join_or_create(jti="00000000-0000-0000-0000-000000000001", raw=b'{"grant":"b"}', identity=identity, expires_at_ms=4_000_000_000_000)
    assert conflicting_bytes.value.reason == "retry_conflict"
    with pytest.raises(transport.TransportRefusal):
        await table.join_or_create(jti="00000000-0000-0000-0000-000000000001", raw=b'{"grant":"a"}', identity=transport._ReplayIdentity(("u", "s"), "boot", 2, "org", "device", "gen", "conn", 1), expires_at_ms=4_000_000_000_000)


@pytest.mark.anyio
async def test_execute_route_bypasses_normal_bearer_but_never_loses_no_store(monkeypatch):
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(route.router)

    async def refused(_body, *, address, raw_bytes):
        assert address == "127.0.0.1"
        raise transport.TransportRefusal("authority_refused", 403)

    monkeypatch.setattr(route, "execute_lifecycle", refused)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 22242)), base_url="http://engine.test") as client:
        response = await client.post("/local-browser/execute?grant=must-not-be-used", content=b'{"grant":"secret","operation":"discover"}')
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"status": "refused", "operation": "discover", "reason": "authority_refused"}
