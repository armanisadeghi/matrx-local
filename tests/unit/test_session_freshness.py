"""The engine never refreshes the desktop's session behind its back — it asks.

Proven here: an expired stored token becomes a VISIBLE publisher blocker with
a remedy (it used to be a silent early return that deferred the head row and
said nothing anywhere), the desktop is asked over the socket at most once a
minute per lane, and the blocker clears on the first tick with a usable token.

Also proven: the gap the desktop is in the middle of filling is a quiet
"session_refreshing" status with nothing for the person to do, and it escalates
to the honest signed-out blocker — with its remedy — only once the window
closes unanswered. An account switch revokes engine custody on purpose and the
new account's sign-in took 27 s and 34 s (measured 2026-09-14); every cloud
lane screamed "this Mac has no valid signed-in session" for that whole window.
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
        # This test owns the credential-blocker branch. Make cloud participation
        # explicit so CI's headless environment cannot short-circuit the SUT at
        # the earlier configuration-blocker branch.
        outbox = CodingSessionBridgeOutbox(
            db=db, client_factory=lambda: None, cloud_enabled=True
        )
        outbox._tokens = tokens  # type: ignore[attr-defined]

        result = await outbox.sync_pending()
        assert result["blocked"] == "no_active_user_jwt"
        blocker = outbox.publisher_blocker
        assert blocker is not None
        # Visible, never silent — but a gap the desktop is already answering is
        # a status, not an error. The honest signed-out blocker and its remedy
        # are proven by the escalation test below.
        assert blocker["code"] == session_freshness.REFRESHING_CODE
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


async def test_refreshing_window_is_quiet_then_escalates_to_the_honest_blocker(
    manager: _Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"t": 1_000.0}
    monkeypatch.setattr(session_freshness, "_now", lambda: clock["t"])
    session_freshness.reset_rate_limit()

    # Nobody has asked yet: a missing session is the honest blocker.
    cold = session_freshness.session_blocker(lane="coding_session_bridge")
    assert cold["code"] == session_freshness.NO_SESSION_CODE
    assert cold["remedy"]

    # The engine asks the desktop and the desktop is listening.
    assert await session_freshness.request_ui_session_refresh(
        lane="coding_session_bridge", reason="stored access token missing or expired"
    )
    quiet = session_freshness.session_blocker(lane="coding_session_bridge", since="s")
    assert quiet["code"] == session_freshness.REFRESHING_CODE
    assert quiet["remedy"] is None
    assert quiet["message"] == "Refreshing your AI Matrx session\u2026"
    assert quiet["since"] == "s"

    # Still inside the window at 89 s — a slow account switch is not an error.
    clock["t"] += 89.0
    assert session_freshness.session_refresh_pending()
    assert (
        session_freshness.session_blocker(lane="coding_session_bridge")["code"]
        == session_freshness.REFRESHING_CODE
    )

    # A retry that gets delivered must NOT re-arm the window; otherwise a
    # genuinely signed-out Mac stays "refreshing" forever.
    clock["t"] += 2.0
    assert await session_freshness.request_ui_session_refresh(
        lane="coding_session_bridge", reason="stored access token missing or expired"
    )
    honest = session_freshness.session_blocker(lane="coding_session_bridge")
    assert honest["code"] == session_freshness.NO_SESSION_CODE
    assert honest["remedy"]

    # The session lands: the window closes at once.
    session_freshness.session_restored()
    assert not session_freshness.session_refresh_pending()


async def test_every_lane_reports_the_refreshing_state_not_an_error(
    tmp_path: Path, manager: _Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The publisher is the lane Arman sees; it must use the shared helper."""
    clock = {"t": 2_000.0}
    monkeypatch.setattr(session_freshness, "_now", lambda: clock["t"])
    session_freshness.reset_rate_limit()

    db = LocalDatabase(tmp_path / "t.db")
    await db.connect()
    try:
        tokens = _Tokens({"access_token": "x", "user_id": "u"}, expired=True)
        outbox = CodingSessionBridgeOutbox(
            db=db, client_factory=lambda: None, cloud_enabled=True
        )
        outbox._tokens = tokens  # type: ignore[attr-defined]

        await outbox.sync_pending()
        blocker = outbox.publisher_blocker
        assert blocker is not None
        assert blocker["code"] == session_freshness.REFRESHING_CODE
        assert blocker["remedy"] is None

        # Nobody answered inside the window: the person is told the truth.
        clock["t"] += 91.0
        blocker = outbox.publisher_blocker
        assert blocker is not None
        assert blocker["code"] == session_freshness.NO_SESSION_CODE
        assert blocker["remedy"]
    finally:
        await db.close()
