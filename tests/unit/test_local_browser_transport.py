"""Closed-lifecycle transport tests: bounds, source correlation, and fences."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager

import pytest
import httpx
from fastapi import FastAPI
from starlette.websockets import WebSocketState

import app.api.extension_ws_manager as manager
import app.api.local_browser_transport as route
import app.services.local_browser_transport as transport
from app.api.auth import AuthMiddleware
from app.services.local_browser_context import (
    BrowserContext,
    FreshContext,
    LocalBrowserContext,
)


class Socket:
    client_state = WebSocketState.CONNECTED
    application_state = WebSocketState.CONNECTED

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def send_text(self, value: str) -> None:
        self.frames.append(value)


@pytest.mark.anyio
async def test_authority_peer_close_cannot_stall_browser_control_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow HTTP pool close must run outside the browser-control event loop."""
    payload = {
        "status": "accepted",
        "operation": "admit",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "app_instance_id": "00000000-0000-0000-0000-000000000002",
        "controller_revision": 7,
        "jti": "00000000-0000-0000-0000-000000000003",
        "expires_at_ms": 4_000_000_000_000,
        "extension_generation": "00000000-0000-0000-0000-000000000004",
        "connection_id": "00000000-0000-0000-0000-000000000005",
    }
    closed = threading.Event()

    class Response:
        status_code = 200
        headers = {"cache-control": "no-store", "connection": "close"}

        async def aiter_bytes(self):
            yield json.dumps(payload).encode()

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            # Models HTTPX cleanup when the authority peer closes its socket.
            time.sleep(0.25)
            closed.set()

        @asynccontextmanager
        async def stream(self, *_args, **_kwargs):
            yield Response()

    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)
    monkeypatch.setattr(transport, "_AUTHORITY_RESULT_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(
        transport, "get_aidream_server_url", lambda: "https://server.example"
    )
    fresh = FreshContext(
        BrowserContext("boot", 1, "org"), ("user", "session"), "daemon"
    )
    registration = type(
        "Registration",
        (),
        {
            "extension_generation": payload["extension_generation"],
            "connection_id": payload["connection_id"],
        },
    )()
    ticks: list[float] = []

    async def ticker() -> None:
        await asyncio.sleep(0.03)
        ticks.append(time.monotonic())

    tick_task = asyncio.create_task(ticker())
    result = await transport._verify(
        fresh,
        payload["app_instance_id"],
        registration,
        {"grant": "opaque", "operation": "admit"},
    )
    finished = time.monotonic()
    await tick_task
    assert result["status"] == "accepted"
    assert ticks and finished <= ticks[0]
    assert not closed.is_set(), "response must return before slow pool cleanup"
    assert await asyncio.to_thread(closed.wait, 1)


@pytest.mark.anyio
async def test_authority_callback_never_carries_peer_cookie_to_next_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "status": "accepted",
        "operation": "admit",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "app_instance_id": "00000000-0000-0000-0000-000000000002",
        "controller_revision": 7,
        "jti": "00000000-0000-0000-0000-000000000003",
        "expires_at_ms": 4_000_000_000_000,
        "extension_generation": "00000000-0000-0000-0000-000000000004",
        "connection_id": "00000000-0000-0000-0000-000000000005",
    }
    seen: list[tuple[str | None, str | None]] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.headers.get("x-organization-id"),
                request.headers.get("cookie"),
            )
        )
        return httpx.Response(
            200,
            headers={"cache-control": "no-store", "set-cookie": "peer=ambient"},
            json=payload,
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        transport.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(endpoint), **kwargs),
    )
    monkeypatch.setattr(
        transport, "get_aidream_server_url", lambda: "https://server.example"
    )
    registration = type(
        "Registration",
        (),
        {
            "extension_generation": payload["extension_generation"],
            "connection_id": payload["connection_id"],
        },
    )()
    for org in ("org-one", "org-two"):
        fresh = FreshContext(
            BrowserContext("boot", 1, org), ("user", "session"), "daemon"
        )
        assert (
            await transport._verify(
                fresh,
                payload["app_instance_id"],
                registration,
                {"grant": "opaque", "operation": "admit"},
            )
        )["status"] == "accepted"
    assert seen == [("org-one", None), ("org-two", None)]


def _registration(registry: manager.ExtensionSessionRegistry):
    socket = Socket()
    session = registry.register(socket)  # type: ignore[arg-type]
    assert registry.register_local_browser(
        session.session_id,
        engine_boot_id="boot",
        revision=3,
        owner=("user", "session"),
        organization_id="org",
        device_id="device",
        extension_generation="11111111-1111-4111-8111-111111111111",
        connection_id="22222222-2222-4222-8222-222222222222",
    )
    return (
        session,
        socket,
        registry.current_local_browser(
            engine_boot_id="boot",
            revision=3,
            owner=("user", "session"),
            organization_id="org",
            device_id="device",
        ),
    )


def test_closed_json_rejects_duplicate_nested_and_oversized_grants():
    assert transport._bounded_json(b'{"grant":"x","operation":"discover"}') == {
        "grant": "x",
        "operation": "discover",
    }
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(b'{"grant":"x","grant":"y","operation":"discover"}')
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(
            json.dumps({"grant": "x" * (9 * 1024), "operation": "discover"}).encode()
        )
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(b'{"grant":"x","operation":"claim"}')
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(b'{"grant":"\\ud800","operation":"discover"}')
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json((b"[" * 16_000) + (b"]" * 16_000))


def test_approve_requires_exact_command_bytes_and_forbids_them_on_lifecycle():
    command = '{"operation":"inspect_login"}'
    assert transport._bounded_json(
        json.dumps(
            {"grant": "x", "operation": "approve", "command_json": command}
        ).encode()
    ) == {"grant": "x", "operation": "approve", "command_json": command}
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(b'{"grant":"x","operation":"approve"}')
    with pytest.raises(transport.TransportRefusal):
        transport._bounded_json(
            b'{"grant":"x","operation":"renew","command_json":"{}"}'
        )


@pytest.mark.anyio
async def test_approve_verify_forwards_exact_command_and_rejects_wrong_digest(
    monkeypatch,
):
    command = '{"operation":"inspect_login"}'
    payload = {
        "status": "accepted",
        "operation": "approve",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "app_instance_id": "00000000-0000-0000-0000-000000000002",
        "controller_revision": 7,
        "jti": "00000000-0000-0000-0000-000000000003",
        "expires_at_ms": 4_000_000_000_000,
        "extension_generation": "00000000-0000-0000-0000-000000000004",
        "connection_id": "00000000-0000-0000-0000-000000000005",
        "actor_id": "00000000-0000-0000-0000-000000000006",
        "organization_id": "00000000-0000-0000-0000-000000000007",
        "profile_id": "00000000-0000-0000-0000-000000000008",
        "admission_id": "00000000-0000-0000-0000-000000000009",
        "command_id": "00000000-0000-0000-0000-000000000010",
        "sequence": 1,
        "command_digest": transport._command_digest(command),
        "approval_id": "00000000-0000-0000-0000-000000000003",
        "deadline_ms": 4_000_000_000_000,
    }
    seen = {}

    class Response:
        status_code = 200
        headers = {"cache-control": "no-store"}

        async def aiter_bytes(self):
            yield json.dumps(payload).encode()

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        @asynccontextmanager
        async def stream(self, _method, _url, json, headers=None):
            seen.update(json)
            yield Response()

    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        transport, "get_aidream_server_url", lambda: "https://server.example"
    )
    fresh = FreshContext(
        BrowserContext("boot", 1, "org"), ("user", "session"), "daemon"
    )
    registration = type(
        "Registration",
        (),
        {
            "extension_generation": payload["extension_generation"],
            "connection_id": payload["connection_id"],
        },
    )()
    assert (
        await transport._verify(
            fresh,
            payload["app_instance_id"],
            registration,
            {"grant": "opaque", "operation": "approve", "command_json": command},
        )
    )["approval_id"] == payload["jti"]
    assert seen["command_json"] == command
    payload["command_digest"] = "0" * 64
    with pytest.raises(transport.TransportRefusal):
        await transport._verify(
            fresh,
            payload["app_instance_id"],
            registration,
            {"grant": "opaque", "operation": "approve", "command_json": command},
        )


def test_closed_terminal_receipt_refuses_secret_bearing_unknown_data() -> None:
    malicious = {
        "command_id": "00000000-0000-4000-8000-000000000002",
        "operation": "inspect_login",
        "outcome": "completed",
        "reason": "none",
        "data": {"secret": "raw-injection-value"},
    }
    assert not transport.valid_terminal_receipt(malicious)
    assert (
        transport._approve_result(
            {
                "status": "acknowledged",
                "operation": "approve",
                "terminal_receipt": malicious,
            },
            inspect=True,
        )
        is None
    )


def test_closed_terminal_receipt_refuses_malformed_origin_without_raising() -> None:
    receipt = {
        "command_id": "00000000-0000-4000-8000-000000000002",
        "operation": "navigate",
        "outcome": "completed",
        "reason": "none",
        "data": {"origin": "https://["},
    }
    assert transport.valid_terminal_receipt(receipt) is False
    assert (
        transport._document({"url": "https://[", "document_id": receipt["command_id"]})
        is None
    )


def test_document_id_is_opaque_but_utf8_bounded() -> None:
    document = {"url": "https://example.com/login", "document_id": "A" * 32}
    assert transport._document(document) == document
    assert transport._document({**document, "document_id": ""}) is None
    assert transport._document({**document, "document_id": "x" * 129}) is None
    assert transport._document({**document, "document_id": "€" * 43}) is None
    assert transport._document({**document, "document_id": "\ud800"}) is None
    assert transport._document({**document, "document_id": 1}) is None


def test_authenticator_receipt_requires_real_verification_outcome() -> None:
    receipt = {
        "command_id": "00000000-0000-4000-8000-000000000002",
        "operation": "authenticator",
        "outcome": "completed",
        "reason": "none",
        "data": {"filled": True, "submitted": True, "challenge_detected": False},
    }
    assert transport.valid_terminal_receipt(receipt) is False
    receipt["data"]["verification"] = "verified"
    assert transport.valid_terminal_receipt(receipt) is True
    receipt["data"]["verification"] = "made_up"
    assert transport.valid_terminal_receipt(receipt) is False


@pytest.mark.anyio
async def test_approve_dispatch_forwards_exact_bytes_and_inspect_document_once(
    monkeypatch,
):
    command = '{"operation":"inspect_login"}'
    future = asyncio.get_running_loop().create_future()
    future.set_result(
        {
            "status": "acknowledged",
            "operation": "approve",
            "terminal_receipt": {
                "command_id": "00000000-0000-4000-8000-000000000002",
                "operation": "inspect_login",
                "outcome": "completed",
                "reason": "none",
                "data": {
                    "origin": "https://example.com",
                    "form": "login",
                    "challenge": "none",
                },
            },
            "document": {
                "url": "https://example.com/login",
                "document_id": "00000000-0000-4000-8000-000000000001",
            },
        }
    )
    sent = {}
    monkeypatch.setattr(transport, "create_local_browser_future", lambda *_: future)

    async def send(_registration, frame):
        sent.update(frame)
        return True

    monkeypatch.setattr(transport, "send_local_browser_execute", send)
    monkeypatch.setattr(transport, "drop_local_browser_future", lambda *_: None)
    entry = transport._ReplayEntry(
        b"digest",
        transport._ReplayIdentity(("u", "s"), "b", 1, "o", "d", "g", "c", 1),
        4_000_000_000_000,
        asyncio.get_running_loop().create_future(),
        inspect=True,
    )
    result = await transport._dispatch(
        object(),
        {"grant": "opaque", "operation": "approve", "command_json": command},
        entry,
    )
    assert sent["command_json"] == command
    assert result["document"]["url"] == "https://example.com/login"


@pytest.mark.anyio
async def test_inspect_tombstone_precedes_publish_and_never_retains_document():
    table = transport._ReplayTable()
    identity = transport._ReplayIdentity(
        ("u", "s"), "boot", 1, "org", "device", "gen", "conn", 1
    )
    entry, creator = await table.join_or_create(
        jti="00000000-0000-0000-0000-000000000001",
        raw=b'{"operation":"inspect_login"}',
        identity=identity,
        expires_at_ms=4_000_000_000_000,
        inspect=True,
    )
    assert creator
    await table.consume_inspect(entry)
    assert table._entries == {}  # noqa: SLF001 - prove no completed future remains reachable
    assert len(table._inspect_tombstones) == 1  # noqa: SLF001 - identity-only tombstone
    with pytest.raises(transport.TransportRefusal) as replay:
        await table.join_or_create(
            jti="00000000-0000-0000-0000-000000000001",
            raw=b'{"operation":"inspect_login"}',
            identity=identity,
            expires_at_ms=4_000_000_000_000,
            inspect=True,
        )
    assert replay.value.reason == "discovery_refresh_required"


@pytest.mark.anyio
async def test_server_callback_uses_actual_closed_contract_and_streams_limit(
    monkeypatch,
):
    sent: dict[str, object] = {}
    payload = {
        "status": "accepted",
        "operation": "admit",
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

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        @asynccontextmanager
        async def stream(self, method, url, json, headers=None):
            sent.update(method=method, url=url, body=json)
            yield Response()

    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        transport, "get_aidream_server_url", lambda: "https://server.example"
    )
    fresh = FreshContext(
        BrowserContext("boot", 1, "org"), ("user", "session"), "daemon"
    )
    registration = type(
        "Registration",
        (),
        {
            "extension_generation": "00000000-0000-0000-0000-000000000004",
            "connection_id": "00000000-0000-0000-0000-000000000005",
        },
    )()
    result = await transport._verify(
        fresh,
        "00000000-0000-0000-0000-000000000002",
        registration,
        {"grant": "opaque", "operation": "admit"},
    )
    assert result["status"] == "accepted"
    assert sent["body"] == {
        "grant": "opaque",
        "operation": "admit",
        "app_instance_id": "00000000-0000-0000-0000-000000000002",
    }

    payload["connection_id"] = "00000000-0000-0000-0000-000000000099"
    with pytest.raises(transport.TransportRefusal) as wrong_pair:
        await transport._verify(
            fresh,
            "00000000-0000-0000-0000-000000000002",
            registration,
            {"grant": "opaque", "operation": "admit"},
        )
    assert wrong_pair.value.reason == "binding_changed"
    payload["connection_id"] = "00000000-0000-0000-0000-000000000005"
    payload["expires_at_ms"] = 1
    with pytest.raises(transport.TransportRefusal):
        await transport._verify(
            fresh,
            "00000000-0000-0000-0000-000000000002",
            registration,
            {"grant": "opaque", "operation": "admit"},
        )

    with pytest.raises(transport.TransportRefusal):
        transport._strict_response_json(b'{"status":"accepted","status":"accepted"}')
    with pytest.raises(transport.TransportRefusal):
        transport._strict_response_json(b'{"value":NaN}')
    with pytest.raises(transport.TransportRefusal):
        transport._strict_response_json(b'{"value":"\\ud800"}')

    payload["expires_at_ms"] = 4_000_000_000_000
    Response.headers = {"cache-control": "no-store", "content-encoding": "gzip"}
    with pytest.raises(transport.TransportRefusal):
        await transport._verify(
            fresh,
            "00000000-0000-0000-0000-000000000002",
            registration,
            {"grant": "opaque", "operation": "admit"},
        )
    Response.headers = {"cache-control": "no-store"}

    class TooLarge(Response):
        async def aiter_bytes(self):
            yield b"x" * (4 * 1024 + 1)

    with pytest.raises(transport.TransportRefusal):
        await transport._read_response(TooLarge())


@pytest.mark.anyio
async def test_local_results_require_the_exact_socket_and_are_fenced_on_context_change(
    monkeypatch,
):
    registry = manager.ExtensionSessionRegistry()
    session, socket, registration = _registration(registry)
    assert registration is not None
    future = registry.create_local_result(registration, "private-call")
    assert future is not None
    other = Socket()
    assert not registry.resolve_local_result(
        session.session_id, other, "private-call", {"status": "acknowledged"}
    )  # type: ignore[arg-type]
    assert registry.resolve_local_result(
        session.session_id, socket, "private-call", {"status": "acknowledged"}
    )  # type: ignore[arg-type]
    assert (await future)["status"] == "acknowledged"

    calls: list[BrowserContext] = []
    grant = "header.eyJzdWIiOiAidXNlciIsICJzZXNzaW9uX2lkIjogIjExMTExMTExLTExMTEtNDExMS04MTExLTExMTExMTExMTExMSJ9.signature"
    context = LocalBrowserContext(
        "boot", lambda: asyncio.sleep(0, result=(grant, "user"))
    )
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
        async def registered_device_identity(self):
            return Device()

    class Limits:
        async def acquire(self, _address):
            return transport.CapacityLease(asyncio.Semaphore(1), asyncio.Semaphore(1))

    monkeypatch.setattr(transport, "_LIMITS", Limits())
    monkeypatch.setattr(transport, "get_local_browser_context", lambda: Context())
    monkeypatch.setattr(transport, "get_instance_manager", lambda: Instance())
    monkeypatch.setattr(transport, "ensure_context_subscription", lambda: None)
    monkeypatch.setattr(
        transport, "current_local_browser_registration", lambda **_kw: object()
    )

    async def verify(*_args):
        return {
            "status": "accepted",
            "jti": "00000000-0000-0000-0000-000000000001",
            "expires_at_ms": 4_000_000_000_000,
            "controller_revision": 1,
        }

    monkeypatch.setattr(transport, "_verify", verify)
    with pytest.raises(transport.TransportRefusal) as refused:
        await transport.execute_lifecycle({"grant": "x", "operation": "discover"})
    assert refused.value.reason == "binding_changed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "result", "expected"),
    [
        (
            "admit",
            {"status": "acknowledged", "operation": "admit", "receipt": "failed"},
            {"status": "acknowledged", "operation": "admit", "receipt": "failed"},
        ),
        (
            "cleanup",
            {
                "status": "acknowledged",
                "operation": "cleanup",
                "receipt": "unconfirmed",
            },
            {
                "status": "acknowledged",
                "operation": "cleanup",
                "receipt": "unconfirmed",
            },
        ),
    ],
)
async def test_dispatch_propagates_only_the_exact_extension_receipt(
    monkeypatch, operation, result, expected
):
    future = asyncio.get_running_loop().create_future()
    future.set_result(result)
    monkeypatch.setattr(transport, "create_local_browser_future", lambda *_: future)

    async def sent(*_):
        return True

    monkeypatch.setattr(transport, "send_local_browser_execute", sent)
    monkeypatch.setattr(transport, "drop_local_browser_future", lambda *_: None)
    entry = transport._ReplayEntry(
        b"digest",
        transport._ReplayIdentity(("u", "s"), "b", 1, "o", "d", "g", "c", 1),
        4_000_000_000_000,
        asyncio.get_running_loop().create_future(),
    )
    assert (
        await transport._dispatch(
            object(), {"grant": "opaque", "operation": operation}, entry
        )
        == expected
    )


@pytest.mark.anyio
async def test_dispatch_refuses_unknown_extension_receipt(monkeypatch):
    diagnostics = []
    monkeypatch.setattr(transport, "_lifecycle_diagnostic", diagnostics.append)
    future = asyncio.get_running_loop().create_future()
    future.set_result(
        {"status": "acknowledged", "operation": "cleanup", "receipt": "invented"}
    )
    monkeypatch.setattr(transport, "create_local_browser_future", lambda *_: future)

    async def sent(*_):
        return True

    monkeypatch.setattr(transport, "send_local_browser_execute", sent)
    monkeypatch.setattr(transport, "drop_local_browser_future", lambda *_: None)
    entry = transport._ReplayEntry(
        b"digest",
        transport._ReplayIdentity(("u", "s"), "b", 1, "o", "d", "g", "c", 1),
        4_000_000_000_000,
        asyncio.get_running_loop().create_future(),
    )
    with pytest.raises(transport.TransportRefusal):
        await transport._dispatch(
            object(), {"grant": "opaque", "operation": "cleanup"}, entry
        )
    assert diagnostics == ["dispatch_result_shape"]


@pytest.mark.anyio
async def test_dispatch_logs_closed_label_for_invalid_approve_result(monkeypatch):
    diagnostics = []
    monkeypatch.setattr(transport, "_lifecycle_diagnostic", diagnostics.append)
    future = asyncio.get_running_loop().create_future()
    future.set_result({"status": "acknowledged", "operation": "approve"})
    monkeypatch.setattr(transport, "create_local_browser_future", lambda *_: future)

    async def sent(*_):
        return True

    monkeypatch.setattr(transport, "send_local_browser_execute", sent)
    monkeypatch.setattr(transport, "drop_local_browser_future", lambda *_: None)
    entry = transport._ReplayEntry(
        b"digest",
        transport._ReplayIdentity(("u", "s"), "b", 1, "o", "d", "g", "c", 1),
        4_000_000_000_000,
        asyncio.get_running_loop().create_future(),
    )
    with pytest.raises(transport.TransportRefusal) as refused:
        await transport._dispatch(
            object(),
            {"grant": "opaque", "operation": "approve", "command_json": "{}"},
            entry,
        )
    assert refused.value.status_code == 503
    assert diagnostics == ["dispatch_approve_result"]


@pytest.mark.anyio
async def test_replay_joins_exact_bytes_and_identity_but_refuses_conflicts():
    table = transport._ReplayTable()
    identity = transport._ReplayIdentity(
        ("u", "s"), "boot", 1, "org", "device", "gen", "conn", 1
    )
    first, creator = await table.join_or_create(
        jti="00000000-0000-0000-0000-000000000001",
        raw=b'{"grant":"a"}',
        identity=identity,
        expires_at_ms=4_000_000_000_000,
    )
    second, joined = await table.join_or_create(
        jti="00000000-0000-0000-0000-000000000001",
        raw=b'{"grant":"a"}',
        identity=identity,
        expires_at_ms=4_000_000_000_000,
    )
    assert creator and not joined and first is second
    with pytest.raises(transport.TransportRefusal) as conflicting_bytes:
        await table.join_or_create(
            jti="00000000-0000-0000-0000-000000000001",
            raw=b'{"grant":"b"}',
            identity=identity,
            expires_at_ms=4_000_000_000_000,
        )
    assert conflicting_bytes.value.reason == "retry_conflict"
    with pytest.raises(transport.TransportRefusal):
        await table.join_or_create(
            jti="00000000-0000-0000-0000-000000000001",
            raw=b'{"grant":"a"}',
            identity=transport._ReplayIdentity(
                ("u", "s"), "boot", 2, "org", "device", "gen", "conn", 1
            ),
            expires_at_ms=4_000_000_000_000,
        )


@pytest.mark.anyio
async def test_cancelled_creator_settles_duplicate_waiter(monkeypatch):
    table = transport._ReplayTable()
    identity = transport._ReplayIdentity(
        ("u", "s"), "boot", 1, "org", "device", "gen", "conn", 1
    )
    entry, _ = await table.join_or_create(
        jti="00000000-0000-0000-0000-000000000001",
        raw=b"bytes",
        identity=identity,
        expires_at_ms=4_000_000_000_000,
    )
    monkeypatch.setattr(transport, "_assert_current", lambda *_: asyncio.sleep(0))
    duplicate = asyncio.create_task(
        transport._join_replay(
            entry,
            FreshContext(BrowserContext("boot", 1, "org"), ("u", "s"), "jwt"),
            "device",
            object(),
        )
    )
    await asyncio.sleep(0)
    transport._settle_failure(entry)
    with pytest.raises(transport.TransportRefusal):
        await duplicate


@pytest.mark.anyio
async def test_expiry_before_send_refuses_without_dispatch(monkeypatch):
    sent = False

    async def send(*_):
        nonlocal sent
        sent = True
        return True

    monkeypatch.setattr(transport, "send_local_browser_execute", send)
    entry = transport._ReplayEntry(
        b"digest",
        transport._ReplayIdentity(("u", "s"), "b", 1, "o", "d", "g", "c", 1),
        1,
        asyncio.get_running_loop().create_future(),
    )
    with pytest.raises(transport.TransportRefusal):
        await transport._dispatch(
            object(), {"grant": "opaque", "operation": "cleanup"}, entry
        )
    assert not sent


@pytest.mark.anyio
async def test_revocation_after_receipt_refuses_before_return(monkeypatch):
    fresh = FreshContext(BrowserContext("boot", 1, "org"), ("user", "session"), "jwt")

    class Context:
        async def refresh(self):
            return None

    class Instance:
        async def registered_device_identity(self):
            return None

    monkeypatch.setattr(transport, "get_local_browser_context", lambda: Context())
    monkeypatch.setattr(transport, "get_instance_manager", lambda: Instance())
    with pytest.raises(transport.TransportRefusal) as refused:
        await transport._assert_current(fresh, "device", object(), 4_000_000_000_000)
    assert refused.value.reason == "binding_changed"


@pytest.mark.anyio
async def test_identity_readiness_notifies_existing_socket_without_polling(monkeypatch):
    fresh = FreshContext(BrowserContext("boot", 4, "org"), ("user", "session"), "jwt")

    class Context:
        async def refresh(self):
            return fresh

    class Registry:
        session_ids = ["ready-socket"]

    delivered = []

    async def send(session_id, frame):
        delivered.append((session_id, frame))
        return True

    identity = type("Device", (), {"app_instance_id": "device"})()
    monkeypatch.setattr(transport, "get_local_browser_context", lambda: Context())
    monkeypatch.setattr(transport, "get_registry", lambda: Registry())
    monkeypatch.setattr(transport, "send_to_extension_session", send)
    monkeypatch.setattr(transport, "_observed_device_identity", None)
    transport._on_device_identity_change(identity)
    await asyncio.sleep(0)
    assert delivered == [
        (
            "ready-socket",
            {
                "type": "local_browser.register_required",
                "version": 1,
                "engine_boot_id": "boot",
                "revision": 4,
            },
        )
    ]


@pytest.mark.anyio
async def test_startup_installs_and_shutdown_removes_identity_subscription(monkeypatch):
    class Context:
        def subscribe(self, listener):
            self.listener = listener
            return lambda: setattr(self, "removed", True)

    class Manager:
        def subscribe_registered_device_identity(self, listener):
            self.listener = listener
            return lambda: setattr(self, "removed", True)

        async def registered_device_identity(self):
            return None

    context, manager = Context(), Manager()
    monkeypatch.setattr(transport, "get_local_browser_context", lambda: context)
    monkeypatch.setattr(transport, "get_instance_manager", lambda: manager)
    transport.uninstall_transport_subscriptions()
    await transport.install_transport_subscriptions()
    assert callable(context.listener) and callable(manager.listener)
    transport.uninstall_transport_subscriptions()
    assert context.removed and manager.removed


@pytest.mark.anyio
async def test_detached_replay_retains_global_capacity_after_creator_cancels(
    monkeypatch,
):
    limits = transport.CallbackLimits()
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    peak = 0

    async def blocked_dispatch(*_args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.set()
        await release.wait()
        active -= 1
        return {
            "status": "acknowledged",
            "operation": "discover",
            "receipt": "accepted",
        }

    async def current(*_args):
        return None

    monkeypatch.setattr(transport, "_dispatch", blocked_dispatch)
    monkeypatch.setattr(transport, "_assert_current", current)
    entries = []
    tasks = []
    identity = transport._ReplayIdentity(
        ("u", "s"), "boot", 1, "org", "device", "gen", "conn", 1
    )
    fresh = FreshContext(BrowserContext("boot", 1, "org"), ("u", "s"), "jwt")
    for index in range(4):
        capacity = await limits.acquire(f"127.0.0.{index + 1}")
        entry = transport._ReplayEntry(
            b"digest",
            identity,
            4_000_000_000_000,
            asyncio.get_running_loop().create_future(),
        )
        entries.append(entry)
        tasks.append(
            asyncio.create_task(
                transport._run_replay(
                    entry,
                    object(),
                    {"grant": "x", "operation": "discover"},
                    fresh,
                    "device",
                    capacity,
                )
            )
        )
    await started.wait()
    while active < 4:
        await asyncio.sleep(0)
    with pytest.raises(transport.TransportRefusal) as limited:
        await limits.acquire("127.0.0.9")
    assert limited.value.reason == "rate_limited"
    assert peak <= 4 and limits.global_gate._value == 0  # noqa: SLF001
    release.set()
    await asyncio.gather(*tasks)
    assert limits.global_gate._value == 4  # noqa: SLF001


@pytest.mark.anyio
async def test_callback_limits_admit_complete_credential_burst_then_refill(monkeypatch):
    """A complete inspect, two-page password, MFA, and cleanup stays bounded."""
    diagnostics: list[str] = []
    monkeypatch.setattr(transport, "_lifecycle_diagnostic", diagnostics.append)
    now = transport.time.monotonic() + 1.0
    monkeypatch.setattr(transport.time, "monotonic", lambda: now)
    limits = transport.CallbackLimits()
    address = "127.0.0.1"
    canonical_callbacks = (
        "discover",
        "admit",
        "renew",
        "approve",
        "renew",
        "approve",
        "renew",
        "renew",
        "renew",
        "approve",
        "renew",
        "approve",
        "cleanup",
    )

    assert len(canonical_callbacks) == transport._CANONICAL_CREDENTIAL_CALLBACK_BURST
    for _operation in canonical_callbacks:
        capacity = await limits.acquire(address)
        capacity.release()

    with pytest.raises(transport.TransportRefusal) as exhausted:
        await limits.acquire(address)
    assert exhausted.value.reason == "rate_limited"
    assert "rate_global_bucket" in diagnostics

    now += 2.0
    capacity = await limits.acquire(address)
    capacity.release()
    with pytest.raises(transport.TransportRefusal) as refilled_once:
        await limits.acquire(address)
    assert refilled_once.value.reason == "rate_limited"
    assert "rate_address_bucket" in diagnostics


@pytest.mark.anyio
async def test_cancelled_creator_leaves_its_capacity_with_detached_replay(monkeypatch):
    """Cancelling HTTP cannot make an in-flight socket relay exceed its cap."""
    fresh = FreshContext(BrowserContext("boot", 3, "org"), ("user", "session"), "jwt")
    device = type("Device", (), {"app_instance_id": "device", "user_id": "user"})()
    registration = type(
        "Registration",
        (),
        {
            "extension_generation": "11111111-1111-4111-8111-111111111111",
            "connection_id": "22222222-2222-4222-8222-222222222222",
        },
    )()

    class Context:
        async def refresh(self):
            return fresh

    class Instance:
        async def registered_device_identity(self):
            return device

    dispatched = asyncio.Event()
    release = asyncio.Event()

    async def blocked_dispatch(*_args):
        dispatched.set()
        await release.wait()
        return {
            "status": "acknowledged",
            "operation": "discover",
            "receipt": "accepted",
        }

    async def accepted(*_args):
        return {
            "status": "accepted",
            "jti": "00000000-0000-0000-0000-000000000006",
            "expires_at_ms": 4_000_000_000_000,
            "controller_revision": 1,
        }

    async def current(*_args):
        return None

    limits = transport.CallbackLimits()
    monkeypatch.setattr(transport, "_LIMITS", limits)
    monkeypatch.setattr(transport, "_REPLAYS", transport._ReplayTable())
    monkeypatch.setattr(transport, "ensure_context_subscription", lambda: None)
    monkeypatch.setattr(transport, "get_local_browser_context", lambda: Context())
    monkeypatch.setattr(transport, "get_instance_manager", lambda: Instance())
    monkeypatch.setattr(
        transport, "current_local_browser_registration", lambda **_kw: registration
    )
    monkeypatch.setattr(transport, "_verify", accepted)
    monkeypatch.setattr(transport, "_assert_current", current)
    monkeypatch.setattr(transport, "_dispatch", blocked_dispatch)
    creator = asyncio.create_task(
        transport.execute_lifecycle(
            {"grant": "x", "operation": "discover"}, address="127.0.0.1", raw_bytes=b"x"
        )
    )
    await dispatched.wait()
    creator.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creator
    assert limits.global_gate._value == 3  # noqa: SLF001
    release.set()
    await asyncio.sleep(0)
    assert limits.global_gate._value == 4  # noqa: SLF001


@pytest.mark.anyio
async def test_composed_app_never_redirects_or_logs_private_callback_variants(
    monkeypatch,
):
    from app import main
    import app.api.local_browser_transport as api

    seen = []
    for name in ("debug", "info", "warning", "error"):
        monkeypatch.setattr(
            main.logger, name, lambda *args, **kwargs: seen.append((args, kwargs))
        )

    async def exploded(*_args, **_kwargs):
        raise RuntimeError("must stay private")

    monkeypatch.setattr(api, "execute_lifecycle", exploded)
    sentinel = "COMPOSED_PRIVATE_GRANT_SENTINEL"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 22242)),
        base_url="http://engine.test",
    ) as client:
        exact = await client.post(
            "/local-browser/execute?opaque=COMPOSED_PRIVATE_GRANT_SENTINEL",
            content=json.dumps({"grant": sentinel, "operation": "discover"}).encode(),
        )
        slash = await client.post(
            "/local-browser/execute/",
            content=json.dumps({"grant": sentinel, "operation": "discover"}).encode(),
        )
        malformed = await client.post("/local-browser/execute", content=b'{"grant":')
        overflow = await client.post(
            "/local-browser/execute", content=b"x" * (32 * 1024 + 1)
        )
    assert exact.status_code == 503 and slash.status_code == 404
    assert slash.headers.get("location") is None
    assert all(
        response.headers["cache-control"] == "no-store"
        for response in (exact, slash, malformed, overflow)
    )
    assert all(sentinel not in repr(item) for item in seen)


@pytest.mark.anyio
async def test_execute_route_bypasses_normal_bearer_but_never_loses_no_store(
    monkeypatch,
):
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(route.router)

    async def refused(_body, *, address, raw_bytes):
        assert address == "127.0.0.1"
        raise transport.TransportRefusal("authority_refused", 403)

    monkeypatch.setattr(route, "execute_lifecycle", refused)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 22242)),
        base_url="http://engine.test",
    ) as client:
        response = await client.post(
            "/local-browser/execute?grant=must-not-be-used",
            content=b'{"grant":"secret","operation":"discover"}',
        )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "status": "refused",
        "operation": "discover",
        "reason": "authority_refused",
    }


@pytest.mark.anyio
async def test_callback_negotiates_identity_instead_of_rejecting_requested_compression(
    monkeypatch,
):
    """A compliant intermediary compresses only when the client requests it."""
    import gzip

    payload = {
        "status": "accepted",
        "operation": "admit",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "app_instance_id": "00000000-0000-0000-0000-000000000002",
        "controller_revision": 7,
        "jti": "00000000-0000-0000-0000-000000000003",
        "expires_at_ms": 4_000_000_000_000,
        "extension_generation": "00000000-0000-0000-0000-000000000004",
        "connection_id": "00000000-0000-0000-0000-000000000005",
    }
    requests = []

    def endpoint(request):
        requests.append(request)
        raw = json.dumps(payload).encode()
        headers = {"cache-control": "no-store"}
        if "gzip" in request.headers.get("accept-encoding", ""):
            raw = gzip.compress(raw)
            headers["content-encoding"] = "gzip"
        return httpx.Response(200, headers=headers, content=raw)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        transport.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(endpoint), **kwargs),
    )
    monkeypatch.setattr(
        transport, "get_aidream_server_url", lambda: "https://server.example"
    )
    fresh = FreshContext(
        BrowserContext("boot", 1, "org"), ("user", "session"), "daemon"
    )
    registration = type(
        "Registration",
        (),
        {
            "extension_generation": payload["extension_generation"],
            "connection_id": payload["connection_id"],
        },
    )()
    result = await transport._verify(
        fresh,
        payload["app_instance_id"],
        registration,
        {
            "grant": "opaque",
            "operation": "admit",
        },
    )
    assert result["status"] == "accepted"
    assert requests[0].headers["accept-encoding"] == "identity"
