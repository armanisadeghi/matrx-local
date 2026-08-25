"""Characterization: the /extension/rpc HANDLERS registry (matrx-extend surface).

Pins the exact set of registered RPC commands and the routing/validation
behavior of the `tool` handler as of 2026-07-10. The Chrome extension speaks
this contract — any change here must be coordinated with matrx-extend
(/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md).

Runs without an engine, network, or credentials. `dispatch` is monkeypatched
so no real tool executes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api import extension_handlers
from app.api.extension_handlers import HANDLERS, register
from app.tools.types import ToolResult, ToolResultType

# The extension RPC command surface — exact, as of 2026-07-10.
EXPECTED_COMMANDS = {"health", "version", "capabilities", "tool"}

# handle_* signatures take (args, req) but none of the current handlers read
# the Request object — a plain sentinel is sufficient and keeps this suite
# free of FastAPI test plumbing.
FAKE_REQUEST: Any = object()


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_handlers_registry_exact() -> None:
    actual = set(HANDLERS.keys())
    assert actual == EXPECTED_COMMANDS, (
        f"EXTENSION RPC SURFACE CHANGED: registered commands are {sorted(actual)}, "
        f"expected {sorted(EXPECTED_COMMANDS)}. The matrx-extend Chrome extension "
        "depends on this exact command set — if the change is intentional, update "
        "this characterization AND /Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md AND the "
        "extension client."
    )


def test_register_rejects_duplicates() -> None:
    """Re-registering an existing command must raise — silent replacement of a
    live RPC handler is exactly the drift this registry forbids."""
    with pytest.raises(ValueError, match="already registered"):
        register("health")(lambda args, req: None)  # type: ignore[arg-type,return-value]


# ---------------------------------------------------------------------------
# health / version / capabilities payload shapes
# ---------------------------------------------------------------------------


def test_health_payload_shape() -> None:
    payload = _run(HANDLERS["health"]({}, FAKE_REQUEST))
    assert set(payload.keys()) == {"status", "version", "user_id"}
    assert payload["status"] == "ok"
    assert isinstance(payload["version"], str) and payload["version"]
    # user_id is None until the auth handshake lands (master plan B3).
    assert payload["user_id"] is None


def test_version_payload_shape() -> None:
    payload = _run(HANDLERS["version"]({}, FAKE_REQUEST))
    assert set(payload.keys()) == {"version", "build"}
    assert isinstance(payload["version"], str) and payload["version"]
    # In a dev (non-frozen) process the build identifier is None.
    assert payload["build"] is None


def test_capabilities_returns_dispatcher_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """`capabilities` must delegate to dispatcher.list_tool_specs — never a
    duplicated catalog."""
    sentinel = [{"name": "FakeTool", "description": "x", "category": "y", "input_schema": {}}]
    monkeypatch.setattr(extension_handlers, "list_tool_specs", lambda: sentinel)
    payload = _run(HANDLERS["capabilities"]({}, FAKE_REQUEST))
    assert payload == {"tools": sentinel}


# ---------------------------------------------------------------------------
# tool handler — validation (no dispatch call may happen)
# ---------------------------------------------------------------------------


def _install_dispatch_spy(
    monkeypatch: pytest.MonkeyPatch,
    result: ToolResult | Exception | None = None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_dispatch(tool_name, tool_input, session):
        calls.append(
            {"tool_name": tool_name, "tool_input": tool_input, "session": session}
        )
        if isinstance(result, Exception):
            raise result
        return result if result is not None else ToolResult(
            type=ToolResultType.SUCCESS, output="ok"
        )

    monkeypatch.setattr(extension_handlers, "dispatch", fake_dispatch)
    return calls


@pytest.mark.parametrize(
    "args",
    [
        {},  # tool_name missing
        {"tool_name": ""},  # empty
        {"tool_name": 42},  # wrong type
    ],
    ids=["missing", "empty", "non-string"],
)
def test_tool_rejects_bad_tool_name(
    monkeypatch: pytest.MonkeyPatch, args: dict[str, Any]
) -> None:
    calls = _install_dispatch_spy(monkeypatch)
    payload = _run(HANDLERS["tool"](args, FAKE_REQUEST))
    assert payload["ok"] is False
    assert payload["error_type"] == "ValidationError"
    assert "tool_name" in payload["error"]
    assert calls == [], "dispatch must NOT be called on validation failure"


def test_tool_rejects_non_dict_tool_input(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_dispatch_spy(monkeypatch)
    payload = _run(
        HANDLERS["tool"]({"tool_name": "Read", "tool_input": ["nope"]}, FAKE_REQUEST)
    )
    assert payload == {
        "ok": False,
        "error": "tool_input must be an object",
        "error_type": "ValidationError",
    }
    assert calls == []


def test_tool_rejects_non_string_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_dispatch_spy(monkeypatch)
    payload = _run(
        HANDLERS["tool"]({"tool_name": "Read", "session_id": 7}, FAKE_REQUEST)
    )
    assert payload["ok"] is False
    assert payload["error_type"] == "ValidationError"
    assert calls == []


# ---------------------------------------------------------------------------
# tool handler — routing into app.tools.dispatcher.dispatch
# ---------------------------------------------------------------------------


def test_tool_routes_into_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    result = ToolResult(type=ToolResultType.SUCCESS, output="hello")
    calls = _install_dispatch_spy(monkeypatch, result=result)

    payload = _run(
        HANDLERS["tool"](
            {"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}},
            FAKE_REQUEST,
        )
    )

    assert len(calls) == 1
    assert calls[0]["tool_name"] == "Read"
    assert calls[0]["tool_input"] == {"file_path": "/tmp/x"}
    # A fresh request-scoped ToolSession per call (B2 behavior).
    from app.tools.session import ToolSession

    assert isinstance(calls[0]["session"], ToolSession)

    assert payload["ok"] is True
    # The wire envelope deliberately omits absent optional fields.
    assert payload["result"] == result.model_dump(exclude_none=True)


def test_tool_none_tool_input_defaults_to_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_dispatch_spy(monkeypatch)
    payload = _run(
        HANDLERS["tool"]({"tool_name": "Read", "tool_input": None}, FAKE_REQUEST)
    )
    assert payload["ok"] is True
    assert calls[0]["tool_input"] == {}


def test_tool_dispatch_exception_becomes_ok_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising dispatch is encoded as {ok: False, error, error_type} inside a
    successful RPC payload — the extension distinguishes tool-layer failures
    from RPC-layer failures this way."""
    _install_dispatch_spy(monkeypatch, result=RuntimeError("boom"))
    payload = _run(HANDLERS["tool"]({"tool_name": "Read"}, FAKE_REQUEST))
    assert payload == {"ok": False, "error": "boom", "error_type": "RuntimeError"}


def test_tool_error_result_still_ok_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ToolResult of type ERROR is a successful dispatch — ok stays True and
    the error travels inside result (current behavior; the extension inspects
    result.type)."""
    err = ToolResult(type=ToolResultType.ERROR, output="Unknown tool: Nope")
    _install_dispatch_spy(monkeypatch, result=err)
    payload = _run(HANDLERS["tool"]({"tool_name": "Nope"}, FAKE_REQUEST))
    assert payload["ok"] is True
    assert payload["result"] == err.model_dump(exclude_none=True)
