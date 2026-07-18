"""User tool-exposure gate in the delegation engine (settings key `cloud_tools`).

Pins the enforcement contract:
  - a pending delegated call whose tool_name is in cloud_tools.disabled_tools
    is NEVER dispatched — the server receives an explicit is_error result with
    DISABLED_TOOL_ERROR_MESSAGE instead;
  - the setting is read fresh at every sweep (no caching across sweeps);
  - states, not errors: one INFO per block transition, not one per call/sweep.

Engine under test: app/services/delegation/engine.py (sweep_once gate).
Style mirrors tests/unit/test_delegation_client.py (httpx.MockTransport fake
aidream server; no network, no engine boot).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.delegation.client import DelegationApiClient
from app.services.delegation.engine import (
    DISABLED_TOOL_ERROR_MESSAGE,
    DelegationEngine,
)
from app.services.delegation.outbox import MemoryDelegationOutbox

JWT = "test-jwt"
BASE = "https://aidream.test"


@pytest.fixture(autouse=True)
def _no_browser_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.delegation import engine as engine_module

    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)


class _FakeSettings:
    """Stands in for SettingsSync — only .get() is needed by the gate."""

    def __init__(self, disabled: list[str]) -> None:
        self.disabled = disabled

    def get(self, key: str, default: Any = None) -> Any:
        if key == "cloud_tools":
            return {"disabled_tools": self.disabled}
        return default


@pytest.fixture()
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    settings = _FakeSettings([])
    monkeypatch.setattr(
        "app.services.cloud_sync.settings_sync.get_settings_sync",
        lambda: settings,
    )
    return settings


class FakeServer:
    """pending_calls + tool_results recorder (continuation not needed)."""

    def __init__(self) -> None:
        self.pending: list[dict[str, Any]] = []
        self.tool_results_bodies: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers.get("authorization") == f"Bearer {JWT}"
        if path == "/ai/user/pending_calls":
            return httpx.Response(200, json=self.pending)
        if path.endswith("/tool_results"):
            body = json.loads(request.content.decode())
            self.tool_results_bodies.append(body)
            resolved = [r["call_id"] for r in body["results"]]
            self.pending = [c for c in self.pending if c["call_id"] not in resolved]
            return httpx.Response(
                200,
                json={
                    "resolved": resolved,
                    "already_resolved": [],
                    "not_found": [],
                    "continuation_needed": False,
                    "user_request_id": "req_1",
                    "conversation_id": "conv_1",
                },
            )
        raise AssertionError(f"unexpected request path: {path}")


def _pending_call(**overrides: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "id": "row-1",
        "call_id": "call_1",
        "conversation_id": "conv_1",
        "user_request_id": "req_1",
        "message_id": None,
        "tool_name": "local_file",
        "arguments": {"action": "list", "path": "."},
        "iteration": 1,
        "created_at": "2026-07-14T00:00:00Z",
        "expires_at": None,
    }
    call.update(overrides)
    return call


def _engine(server: FakeServer) -> DelegationEngine:
    client = DelegationApiClient(BASE, transport=server.transport())
    engine = DelegationEngine(
        client=client, poll_interval=999.0, outbox=MemoryDelegationOutbox()
    )

    async def fake_creds() -> str:
        return JWT

    engine._get_credentials = fake_creds  # type: ignore[method-assign]
    return engine


async def _sweep_and_settle(engine: DelegationEngine) -> int:
    dispatched = await engine.sweep_once()
    while engine._call_tasks:
        await asyncio.gather(*list(engine._call_tasks), return_exceptions=True)
    return dispatched


def _forbid_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dispatch() must not be called for a disabled tool")

    monkeypatch.setattr("app.tools.dispatcher.dispatch", _explode)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_disabled_tool_yields_error_result_and_no_dispatch(
    fake_settings: _FakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_dispatch(monkeypatch)
    fake_settings.disabled = ["local_file"]
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    dispatched = asyncio.run(_sweep_and_settle(engine))

    assert dispatched == 0  # blocked calls are not executions
    assert len(server.tool_results_bodies) == 1
    result = server.tool_results_bodies[0]["results"][0]
    assert result["call_id"] == "call_1"
    assert result["tool_name"] == "local_file"
    assert result["is_error"] is True
    assert result["error_message"] == DISABLED_TOOL_ERROR_MESSAGE
    assert result["output"] is None
    # Refused, delivered, and never re-processed.
    assert "call_1" in engine._handled
    assert engine._undelivered == {}


def test_setting_is_read_fresh_each_sweep(
    fake_settings: _FakeSettings, tmp_path: Path
) -> None:
    (tmp_path / "hello.txt").write_text("hi")
    fake_settings.disabled = ["local_file"]
    server = FakeServer()
    server.pending = [
        _pending_call(arguments={"action": "list", "path": str(tmp_path)})
    ]
    engine = _engine(server)

    async def run() -> None:
        # Sweep 1: blocked.
        assert await _sweep_and_settle(engine) == 0
        assert server.tool_results_bodies[0]["results"][0]["is_error"] is True

        # User re-enables the tool (cloud pull / UI toggle) — NO engine restart.
        fake_settings.disabled = []
        server.pending = [
            _pending_call(
                call_id="call_2",
                arguments={"action": "list", "path": str(tmp_path)},
            )
        ]
        # Sweep 2: same engine, same loop — now executes for real.
        assert await _sweep_and_settle(engine) == 1

    asyncio.run(run())

    assert len(server.tool_results_bodies) == 2
    executed = server.tool_results_bodies[1]["results"][0]
    assert executed["call_id"] == "call_2"
    assert executed["is_error"] is False
    assert "hello.txt" in executed["output"]["output"]


def test_block_logs_one_info_per_transition(
    fake_settings: _FakeSettings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _forbid_dispatch(monkeypatch)
    fake_settings.disabled = ["local_file"]
    server = FakeServer()
    server.pending = [
        _pending_call(),
        _pending_call(call_id="call_2"),
    ]
    engine = _engine(server)

    # The app's system_logger sets propagate=False, so caplog's root handler
    # never sees its records — attach the capture handler directly.
    system_logger = logging.getLogger("system_logger")
    system_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="system_logger"):
            asyncio.run(_sweep_and_settle(engine))
            # Another blocked call for the same tool on a later sweep: no new log.
            server.pending = [_pending_call(call_id="call_3")]
            asyncio.run(_sweep_and_settle(engine))
    finally:
        system_logger.removeHandler(caplog.handler)

    blocked_logs = [
        r for r in caplog.records if "disabled on this computer" in r.getMessage()
    ]
    assert len(blocked_logs) == 1
    assert blocked_logs[0].levelno == logging.INFO
    # All three refusals were still delivered.
    assert len(server.tool_results_bodies) == 3
