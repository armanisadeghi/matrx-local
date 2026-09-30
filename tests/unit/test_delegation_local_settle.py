"""The desktop settles a call it answered in its OWN stores, at delivery.

The defect (2026-09-30, same class matrx-frontend fixed in
``features/agents/api/settle-client-tool-call.ts``): the engine POSTs a
delegated call's result to aidream, which is how the SERVER learns it is done.
This desktop's own copies learned only from the server — the Cloud Chat card
from stream events, the ``chat.tool_call`` mirror row from a later pull — and
for a delegated call aidream sends no completion event. The card spun
"running on this computer…" after the agent had moved on.

Pinned here:
  - the one delivery funnel records the answer on the per-conversation
    snapshot the UI reads (``ui_conversation_state(...)["calls"][i]["result"]``)
    for every answer path: executed, refused (disabled), user-reviewed;
  - the same funnel settles the local mirror row, and never downgrades a row
    the server already made terminal;
  - a failed delivery still settles the card (the tool DID run) while the
    call stays outstanding for the composer gate;
  - the real SQLite settle statement against a chat.tool_call table.

Without the fix every ``result`` assertion fails with KeyError/None and the
mirror row stays ``delegated``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import aiosqlite
import httpx
import pytest

from app.services.delegation.client import DelegationApiClient
from app.services.delegation.engine import (
    DISABLED_TOOL_ERROR_MESSAGE,
    DelegationEngine,
)
from app.services.delegation.mirror import (
    MemoryToolCallMirror,
    SqliteToolCallMirror,
)
from app.services.delegation.outbox import MemoryDelegationOutbox

JWT = "test-jwt"
BASE = "https://aidream.test"
CONV = "conv_1"


@pytest.fixture(autouse=True)
def _no_browser_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.delegation import engine as engine_module

    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)


class _FakeSettings:
    def __init__(self) -> None:
        self.disabled: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        if key == "cloud_tools":
            return {"disabled_tools": self.disabled}
        return default


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    settings = _FakeSettings()
    monkeypatch.setattr(
        "app.services.cloud_sync.settings_sync.get_settings_sync",
        lambda: settings,
    )
    return settings


class FakeServer:
    def __init__(self, *, fail_results: bool = False) -> None:
        self.pending: list[dict[str, Any]] = []
        self.fail_results = fail_results

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/ai/user/pending_calls":
            return httpx.Response(200, json=self.pending)
        if path.endswith("/tool_results"):
            if self.fail_results:
                return httpx.Response(503, json={"detail": "down"})
            body = json.loads(request.content.decode())
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
                    "conversation_id": CONV,
                },
            )
        raise AssertionError(f"unexpected request path: {path}")


def _pending(tool_name: str = "local_file", **overrides: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "id": "row-1",
        "call_id": "call_1",
        "conversation_id": CONV,
        "user_request_id": "req_1",
        "message_id": None,
        "tool_name": tool_name,
        "arguments": {"action": "list", "path": "."},
        "iteration": 1,
        "created_at": "2026-09-30T00:00:00Z",
        "expires_at": None,
    }
    call.update(overrides)
    return call


def _engine(server: FakeServer, mirror: MemoryToolCallMirror) -> DelegationEngine:
    engine = DelegationEngine(
        client=DelegationApiClient(BASE, transport=server.transport()),
        poll_interval=999.0,
        outbox=MemoryDelegationOutbox(),
        mirror=mirror,
    )

    async def fake_creds() -> str:
        return JWT

    async def fake_execute(entry: Any, tool_name: str, call_id: str, args: Any) -> dict[str, Any]:
        return {
            "call_id": call_id,
            "tool_name": tool_name,
            "output": {"output": "3 files"},
            "is_error": False,
            "error_message": None,
            "duration_ms": 42,
        }

    engine._get_credentials = fake_creds  # type: ignore[method-assign]
    engine._resolve_tool = lambda name: SimpleNamespace(dispatcher_name="Files")  # type: ignore[method-assign]
    engine._execute = fake_execute  # type: ignore[method-assign]
    return engine


async def _sweep(engine: DelegationEngine) -> None:
    await engine.sweep_once()
    while engine._call_tasks:
        await asyncio.gather(*list(engine._call_tasks), return_exceptions=True)


def _call(engine: DelegationEngine, call_id: str = "call_1") -> dict[str, Any]:
    calls = engine.ui_conversation_state(CONV)["calls"]
    return next(c for c in calls if c["call_id"] == call_id)


def _mirror_row(status: str = "delegated") -> MemoryToolCallMirror:
    return MemoryToolCallMirror({(CONV, "call_1"): {"status": status, "output": None}})


def test_executed_call_is_settled_on_the_ui_snapshot_and_mirror() -> None:
    server = FakeServer()
    server.pending = [_pending()]
    mirror = _mirror_row()
    engine = _engine(server, mirror)

    asyncio.run(_sweep(engine))

    call = _call(engine)
    assert call["state"] == "delivered"
    assert call["result"] == {
        "is_error": False,
        "error_message": None,
        "output": {"output": "3 files"},
        "duration_ms": 42,
    }
    row = mirror.rows[(CONV, "call_1")]
    assert row["status"] == "completed"
    assert json.loads(row["output"]) == {"output": "3 files"}


def test_refused_call_settles_as_an_error(fake_settings: _FakeSettings) -> None:
    fake_settings.disabled = ["local_file"]
    server = FakeServer()
    server.pending = [_pending()]
    mirror = _mirror_row()
    engine = _engine(server, mirror)

    asyncio.run(_sweep(engine))

    result = _call(engine)["result"]
    assert result["is_error"] is True
    assert result["error_message"] == DISABLED_TOOL_ERROR_MESSAGE
    assert mirror.rows[(CONV, "call_1")]["status"] == "error"


def test_user_review_decision_settles_the_card() -> None:
    server = FakeServer()
    server.pending = [
        _pending(
            "google_email_send",
            arguments={"to": "a@example.com", "subject": "Hi", "body": "Hello"},
        )
    ]
    mirror = _mirror_row()
    engine = _engine(server, mirror)

    async def run() -> None:
        await _sweep(engine)
        assert "result" not in _call(engine)  # parked: nothing answered yet
        await engine.resolve_review("call_1", {"outcome": "declined"})

    asyncio.run(run())

    result = _call(engine)["result"]
    assert result["is_error"] is False
    assert result["output"]["declined"] is True
    assert mirror.rows[(CONV, "call_1")]["status"] == "completed"


def test_mirror_row_already_terminal_is_never_downgraded() -> None:
    server = FakeServer()
    server.pending = [_pending()]
    mirror = MemoryToolCallMirror(
        {(CONV, "call_1"): {"status": "error", "output": "server truth"}}
    )
    engine = _engine(server, mirror)

    asyncio.run(_sweep(engine))

    assert mirror.rows[(CONV, "call_1")] == {"status": "error", "output": "server truth"}


def test_failed_delivery_still_settles_the_card_but_keeps_the_call_outstanding() -> None:
    server = FakeServer(fail_results=True)
    server.pending = [_pending()]
    mirror = _mirror_row()
    engine = _engine(server, mirror)

    asyncio.run(_sweep(engine))

    state = engine.ui_conversation_state(CONV)
    assert state["calls"][0]["result"]["output"] == {"output": "3 files"}
    # The server does not know yet: the composer gate must still hold.
    assert [c["call_id"] for c in state["outstanding"]] == ["call_1"]
    assert "call_1" in engine._undelivered


def test_sqlite_settle_updates_only_an_unsettled_row() -> None:
    async def run() -> list[tuple[Any, ...]]:
        db = await aiosqlite.connect(":memory:")
        await db.execute("ATTACH DATABASE ':memory:' AS chat")
        await db.execute(
            "CREATE TABLE chat.tool_call (id TEXT PRIMARY KEY, conversation_id TEXT, "
            "call_id TEXT, status TEXT, success INTEGER, is_error INTEGER, "
            "error_message TEXT, output TEXT, output_chars INTEGER, duration_ms INTEGER, "
            "completed_at TEXT, resolved_at TEXT, resolution_source TEXT, "
            "updated_at TEXT, version INTEGER)"
        )
        await db.execute(
            "INSERT INTO chat.tool_call (id, conversation_id, call_id, status, updated_at, version) "
            "VALUES ('r1', ?, 'call_1', 'delegated', 'T0', 3), "
            "('r2', ?, 'call_2', 'completed', 'T0', 3)",
            (CONV, CONV),
        )
        mirror = SqliteToolCallMirror(db)
        payload = {"output": {"output": "ok"}, "is_error": False, "duration_ms": 7}
        assert await mirror.settle(CONV, "call_1", payload) is True
        assert await mirror.settle(CONV, "call_2", payload) is False
        cursor = await db.execute(
            "SELECT call_id, status, success, output, duration_ms, updated_at, version, "
            "resolution_source FROM chat.tool_call ORDER BY call_id"
        )
        rows = await cursor.fetchall()
        await db.close()
        return rows

    rows = asyncio.run(run())
    # Settled — and updated_at/version untouched so the next pull still
    # applies the server's own row over this local settle.
    assert rows[0] == ("call_1", "completed", 1, '{"output":"ok"}', 7, "T0", 3, "client_post")
    assert rows[1][:4] == ("call_2", "completed", None, None)
