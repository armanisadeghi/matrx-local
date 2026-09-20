"""Closed registration-reply correlation for the private browser channel."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.websockets import WebSocketState

import app.api.extension_routes as routes
import app.api.extension_ws_manager as manager
import app.services.cloud_sync.instance_manager as instance_manager
import app.services.local_browser_context as context_module
from app.services.local_browser_context import BrowserContext, FreshContext

BOOT = "00000000-0000-0000-0000-000000000001"
GENERATION = "00000000-0000-0000-0000-000000000002"
CONNECTION = "00000000-0000-0000-0000-000000000003"
DEVICE = "00000000-0000-0000-0000-000000000004"


class Socket:
    client_state = WebSocketState.CONNECTED
    application_state = WebSocketState.CONNECTED

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_text(self, payload: str) -> None:
        self.frames.append(json.loads(payload))


class Context:
    def __init__(self, *fresh: FreshContext | None) -> None:
        self._fresh = iter(fresh)

    async def refresh(self) -> FreshContext | None:
        return next(self._fresh)


def _fresh(
    *, revision: int = 4, owner: tuple[str, str] = ("user", "session")
) -> FreshContext:
    return FreshContext(BrowserContext(BOOT, revision, "organization"), owner, "daemon")


def _message(**changes: object) -> dict[str, object]:
    message: dict[str, object] = {
        "type": "local_browser.register",
        "version": 1,
        "engine_boot_id": BOOT,
        "expected_revision": 4,
        "extension_generation": GENERATION,
        "connection_id": CONNECTION,
    }
    message.update(changes)
    return message


def _wire(
    monkeypatch: pytest.MonkeyPatch, context: Context
) -> tuple[manager.ExtensionSessionRegistry, str, Socket]:
    registry = manager.ExtensionSessionRegistry()
    socket = Socket()
    session = registry.register(socket)  # type: ignore[arg-type]
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    monkeypatch.setattr(
        routes, "register_local_browser_session", registry.register_local_browser
    )
    monkeypatch.setattr(context_module, "get_local_browser_context", lambda: context)
    monkeypatch.setattr(
        instance_manager,
        "get_instance_manager",
        lambda: SimpleNamespace(registered_device_identity=lambda: _identity()),
    )
    return registry, session.session_id, socket


async def _identity() -> object:
    return SimpleNamespace(app_instance_id=DEVICE)


@pytest.mark.anyio
async def test_registration_acknowledges_the_exact_validated_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, session_id, socket = _wire(
        monkeypatch, Context(_fresh(), _fresh(), _fresh())
    )
    assert await routes._handle_extension_message(session_id, _message())
    assert socket.frames == [
        {
            "type": "local_browser.registration",
            "version": 1,
            "status": "acknowledged",
            "engine_boot_id": BOOT,
            "expected_revision": 4,
            "extension_generation": GENERATION,
            "connection_id": CONNECTION,
        }
    ]
    assert (
        registry.current_local_browser(
            engine_boot_id=BOOT,
            revision=4,
            owner=("user", "session"),
            organization_id="organization",
            device_id=DEVICE,
        )
        is not None
    )


@pytest.mark.anyio
async def test_context_change_while_identity_awaits_refuses_the_submitted_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, session_id, socket = _wire(
        monkeypatch, Context(_fresh(), _fresh(revision=5), _fresh(revision=5))
    )
    assert await routes._handle_extension_message(session_id, _message())
    assert socket.frames == [
        {
            "type": "local_browser.registration",
            "version": 1,
            "status": "refused",
            "engine_boot_id": BOOT,
            "expected_revision": 4,
            "extension_generation": GENERATION,
            "connection_id": CONNECTION,
            "reason": "registration_unavailable",
        }
    ]
    assert (
        registry.current_local_browser(
            engine_boot_id=BOOT,
            revision=4,
            owner=("user", "session"),
            organization_id="organization",
            device_id=DEVICE,
        )
        is None
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"engine_boot_id": "not-a-uuid"},
        {"expected_revision": -1},
        {"expected_revision": True},
        {"extension_generation": "not-a-uuid"},
        {"connection_id": "not-a-uuid"},
        {"expected_revision": 9007199254740992},
    ],
)
async def test_registration_ignores_malformed_correlation_values(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, object]
) -> None:
    registry, session_id, socket = _wire(monkeypatch, Context(_fresh(), _fresh()))
    assert await routes._handle_extension_message(session_id, _message(**changes))
    assert socket.frames == []
    assert (
        registry.current_local_browser(
            engine_boot_id=BOOT,
            revision=4,
            owner=("user", "session"),
            organization_id="organization",
            device_id=DEVICE,
        )
        is None
    )


@pytest.mark.anyio
async def test_old_socket_cannot_install_and_duplicate_candidate_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, session_id, socket = _wire(
        monkeypatch, Context(*[_fresh() for _ in range(9)])
    )
    socket.client_state = WebSocketState.DISCONNECTED
    assert not await routes._handle_extension_message(session_id, _message())
    assert (
        registry.current_local_browser(
            engine_boot_id=BOOT,
            revision=4,
            owner=("user", "session"),
            organization_id="organization",
            device_id=DEVICE,
        )
        is None
    )

    socket = Socket()
    session_id = registry.register(socket).session_id  # type: ignore[arg-type]
    assert await routes._handle_extension_message(session_id, _message())
    conflicting = _message(connection_id="00000000-0000-0000-0000-000000000099")
    assert await routes._handle_extension_message(session_id, conflicting)
    assert socket.frames[-1] == {
        "type": "local_browser.registration",
        "version": 1,
        "status": "refused",
        "engine_boot_id": BOOT,
        "expected_revision": 4,
        "extension_generation": GENERATION,
        "connection_id": conflicting["connection_id"],
        "reason": "registration_unavailable",
    }


@pytest.mark.anyio
async def test_identity_invalidation_during_final_context_read_cannot_be_undone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, session_id, socket = _wire(monkeypatch, Context())
    calls = 0

    async def refresh() -> FreshContext:
        nonlocal calls
        calls += 1
        if calls == 3:
            # The production identity listener fences even an empty registry.
            registry.invalidate_local_browser("binding_changed")
        return _fresh()

    monkeypatch.setattr(
        context_module,
        "get_local_browser_context",
        lambda: SimpleNamespace(refresh=refresh),
    )
    assert await routes._handle_extension_message(session_id, _message())
    assert socket.frames[-1]["status"] == "refused"
    assert (
        registry.current_local_browser(
            engine_boot_id=BOOT,
            revision=4,
            owner=("user", "session"),
            organization_id="organization",
            device_id=DEVICE,
        )
        is None
    )
    # A later independently validated request can register under the new fence.
    assert await routes._handle_extension_message(session_id, _message())
    assert socket.frames[-1]["status"] == "acknowledged"


@pytest.mark.anyio
async def test_context_invalidation_during_identity_read_refuses_even_restored_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, session_id, socket = _wire(
        monkeypatch, Context(_fresh(), _fresh(), _fresh())
    )
    calls = 0

    async def identity() -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            registry.invalidate_local_browser("binding_changed")
        return SimpleNamespace(app_instance_id=DEVICE)

    monkeypatch.setattr(
        instance_manager,
        "get_instance_manager",
        lambda: SimpleNamespace(registered_device_identity=identity),
    )
    assert await routes._handle_extension_message(session_id, _message())
    assert socket.frames[-1]["status"] == "refused"
    assert (
        registry.current_local_browser(
            engine_boot_id=BOOT,
            revision=4,
            owner=("user", "session"),
            organization_id="organization",
            device_id=DEVICE,
        )
        is None
    )


@pytest.mark.anyio
async def test_approve_result_accepts_only_closed_receipt_and_sanitized_inspect_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = manager.ExtensionSessionRegistry()
    socket = Socket()
    session = registry.register(socket)  # type: ignore[arg-type]
    received: list[dict[str, object]] = []
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    monkeypatch.setattr(
        routes,
        "resolve_local_browser_result",
        lambda _session, _socket, _call, payload: received.append(dict(payload))
        or True,
    )
    frame = {
        "type": "local_browser.result",
        "version": 1,
        "call_id": "00000000-0000-4000-8000-000000000099",
        "operation": "approve",
        "status": "acknowledged",
        "terminal_receipt": {
            "command_id": "00000000-0000-4000-8000-000000000097",
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
            "document_id": "00000000-0000-4000-8000-000000000098",
        },
    }
    assert await routes._handle_extension_message(session.session_id, frame)
    assert received == [frame]
    malicious_receipt = {
        **frame,
        "terminal_receipt": {"secret": "raw-injection-value"},
    }
    assert await routes._handle_extension_message(session.session_id, malicious_receipt)
    assert len(received) == 1
    frame["document"] = {
        "url": "https://example.com/login?secret=never-relayed",
        "document_id": "00000000-0000-4000-8000-000000000098",
    }
    assert await routes._handle_extension_message(session.session_id, frame)
    assert len(received) == 1
