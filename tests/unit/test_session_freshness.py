"""The engine never refreshes the desktop's session behind its back — it asks.

Proven here: an expired stored token becomes a VISIBLE publisher blocker with
a remedy (it used to be a silent early return that deferred the head row and
said nothing anywhere), the desktop is asked over the socket at most once a
minute per lane, and the blocker clears on the first tick with a usable token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.anyio

from app.services import session_freshness
from app.services.coding_sessions.service import CodingSessionBridgeOutbox
from app.services.local_db.database import LocalDatabase


class _Tokens:
    def __init__(self, row: dict[str, Any] | None, expired: bool) -> None:
        self.row = row
        self.expired = expired

    async def get(self) -> dict[str, Any] | None:
        return self.row

    def is_expired(self, _row: dict[str, Any]) -> bool:
        return self.expired


class _Conn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []


class _Manager:
    def __init__(self) -> None:
        self.connections = {"ui": _Conn()}

    async def _send(self, conn: _Conn, data: dict[str, Any]) -> None:
        conn.sent.append(data)


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> _Manager:
    fake = _Manager()
    import types

    fake_main = types.SimpleNamespace(websocket_manager=fake)
    monkeypatch.setitem(__import__("sys").modules, "app.main", fake_main)
    session_freshness.reset_rate_limit()
    return fake


async def test_request_is_rate_limited_per_lane(manager: _Manager) -> None:
    assert await session_freshness.request_ui_session_refresh(lane="a", reason="expired")
    assert not await session_freshness.request_ui_session_refresh(lane="a", reason="expired")
    assert await session_freshness.request_ui_session_refresh(lane="b", reason="expired")
    sent = manager.connections["ui"].sent
    assert [m["type"] for m in sent] == [session_freshness.EVENT_TYPE] * 2
    assert {m["lane"] for m in sent} == {"a", "b"}


async def test_expired_token_is_a_visible_blocker_that_clears(
    tmp_path: Path, manager: _Manager
) -> None:
    db = LocalDatabase(tmp_path / "t.db")
    await db.connect()
    try:
        tokens = _Tokens({"access_token": "x", "user_id": "u"}, expired=True)
        outbox = CodingSessionBridgeOutbox(db=db, client_factory=lambda: None)
        outbox._tokens = tokens  # type: ignore[attr-defined]

        result = await outbox.sync_pending()
        assert result["blocked"] == "no_active_user_jwt"
        blocker = outbox.publisher_blocker
        assert blocker is not None
        assert blocker["code"] == "no_active_user_jwt"
        assert blocker["remedy"]
        # The desktop was asked for a fresh session.
        assert manager.connections["ui"].sent[-1]["type"] == session_freshness.EVENT_TYPE

        # A usable token on the next tick clears the blocker — the only thing
        # left in the way is that no server is configured, which is now said too.
        tokens.expired = False
        result = await outbox.sync_pending()
        assert result["blocked"] == "aidream_server_unconfigured"
        blocker = outbox.publisher_blocker
        assert blocker is not None and blocker["code"] == "aidream_server_unconfigured"
    finally:
        await db.close()
