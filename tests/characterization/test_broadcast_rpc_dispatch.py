"""Characterization: the Supabase-Broadcast rpc dispatcher (Channel B fallback).

Pins the cross-component inbound router's behavior: a `kind:"rpc"` v2
envelope arriving on `matrx-local-bridge:<userId>` dispatches into the SAME
`extension_handlers.HANDLERS` registry as `POST /extension/rpc`, and the
result is published back as a reply envelope (`action: "<action>.result"`,
same `requestId`, `direction: "local->extension"`).

Runs without an engine, network, or Supabase — `publish_envelope` is
monkeypatched to capture replies in-memory.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from app.api import cross_component_router as router_mod
from app.api.cross_component_envelope import parse_envelope
from app.api.cross_component_router import route_envelope

USER_ID = "user-abc"


def _envelope(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "v": 2,
        "kind": "rpc",
        "direction": "extension->local",
        "action": "version",
        "requestId": "req-1",
        "payload": {},
        "timestamp": 1_720_000_000_000,
        "fromInstance": {"component": "extension", "instanceId": "ext-1"},
        "toInstance": {"component": "local"},
    }
    base.update(overrides)
    return base


async def _drive(
    monkeypatch: pytest.MonkeyPatch,
    raw: Dict[str, Any],
    user_id: Optional[str] = USER_ID,
) -> List[Dict[str, Any]]:
    """Run route_envelope inside a live loop and collect published replies."""
    published: List[Dict[str, Any]] = []

    async def fake_publish(uid: str, envelope: Dict[str, Any]) -> bool:
        published.append({"user_id": uid, "envelope": envelope})
        return True

    # _dispatch_rpc imports publish_envelope from extension_broadcast at call
    # time — patch it at the source module.
    import app.api.extension_broadcast as broadcast_mod

    monkeypatch.setattr(broadcast_mod, "publish_envelope", fake_publish)
    monkeypatch.setattr(
        router_mod, "_local_instance_id", lambda: "inst_test", raising=True
    )

    route_envelope(raw, user_id)
    # Let the scheduled dispatch task run to completion.
    for _ in range(20):
        await asyncio.sleep(0)
        pending = [
            t
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
    return published


# ---------------------------------------------------------------------------
# Envelope schema — version field
# ---------------------------------------------------------------------------


def test_envelope_version_defaults_to_2_for_v1_publishers() -> None:
    raw = _envelope()
    raw.pop("v")
    env = parse_envelope(raw)
    assert env.v == 2


def test_envelope_version_roundtrips() -> None:
    env = parse_envelope(_envelope(v=3))
    assert env.v == 3


# ---------------------------------------------------------------------------
# rpc dispatch — happy path into the shared HANDLERS registry
# ---------------------------------------------------------------------------


def test_rpc_version_command_dispatches_and_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = asyncio.run(_drive(monkeypatch, _envelope(action="version")))
    assert len(published) == 1
    assert published[0]["user_id"] == USER_ID
    reply = published[0]["envelope"]
    assert reply["v"] == 2
    assert reply["kind"] == "rpc"
    assert reply["direction"] == "local->extension"
    assert reply["action"] == "version.result"
    assert reply["requestId"] == "req-1"
    assert reply["fromInstance"] == {"component": "local", "instanceId": "inst_test"}
    assert reply["toInstance"] == {"component": "extension", "instanceId": "ext-1"}
    # The payload is the transport-agnostic invoke_command envelope — the
    # same handler that serves POST /extension/rpc produced `data`.
    payload = reply["payload"]
    assert payload["ok"] is True
    assert isinstance(payload["data"]["version"], str) and payload["data"]["version"]


def test_rpc_unknown_command_replies_ok_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = asyncio.run(_drive(monkeypatch, _envelope(action="nope.nothing")))
    assert len(published) == 1
    payload = published[0]["envelope"]["payload"]
    assert payload == {
        "ok": False,
        "error": "Unknown command: nope.nothing",
        "error_type": "UnknownCommand",
    }
    assert published[0]["envelope"]["action"] == "nope.nothing.result"


def test_rpc_tool_command_routes_into_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic `tool` command works over Broadcast exactly as over HTTP."""
    from app.api import extension_handlers
    from app.tools.types import ToolResult, ToolResultType

    calls: List[Dict[str, Any]] = []

    async def fake_dispatch(tool_name: str, tool_input: dict, session: Any) -> ToolResult:
        calls.append({"tool_name": tool_name, "tool_input": tool_input})
        return ToolResult(type=ToolResultType.SUCCESS, output="broadcast-ok")

    monkeypatch.setattr(extension_handlers, "dispatch", fake_dispatch)

    published = asyncio.run(
        _drive(
            monkeypatch,
            _envelope(
                action="tool",
                payload={"tool_name": "SystemInfo", "tool_input": {"x": 1}},
            ),
        )
    )
    assert calls == [{"tool_name": "SystemInfo", "tool_input": {"x": 1}}]
    payload = published[0]["envelope"]["payload"]
    assert payload["ok"] is True
    assert payload["data"]["ok"] is True
    assert payload["data"]["result"]["output"] == "broadcast-ok"


# ---------------------------------------------------------------------------
# Filtering + failure posture
# ---------------------------------------------------------------------------


def test_rpc_from_local_component_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Self-origin envelopes (our own replies echoed back) never dispatch."""
    raw = _envelope(fromInstance={"component": "local", "instanceId": "inst_test"})
    published = asyncio.run(_drive(monkeypatch, raw))
    assert published == []


def test_rpc_addressed_to_other_component_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _envelope(toInstance={"component": "frontend"})
    published = asyncio.run(_drive(monkeypatch, raw))
    assert published == []


def test_rpc_without_user_context_is_dropped_without_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = asyncio.run(_drive(monkeypatch, _envelope(), user_id=None))
    assert published == []


def test_malformed_envelope_is_dropped_without_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = asyncio.run(
        _drive(monkeypatch, {"kind": "rpc", "direction": "extension->local"})
    )
    assert published == []
