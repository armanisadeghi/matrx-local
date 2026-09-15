"""Daemon-current credentials recover without requesting a renderer token push."""

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
    from app.services import sync_client

    class Client:
        last_state = None

        async def access_grant(self):
            return None

    monkeypatch.setattr(sync_client, "get_sync_client", Client)
    session_freshness.session_restored()
    return fake


async def test_current_daemon_grant_needs_no_renderer(manager, monkeypatch):
    from app.services import sync_client

    class Client:
        last_state = None

        async def access_grant(self):
            return ("current-token", "user-a")

    monkeypatch.setattr(sync_client, "get_sync_client", Client)
    assert await session_freshness.request_session_grant(lane="a", reason="expired")
    assert manager.connections["ui"].sent == []


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
        # No daemon grant is an honest blocker, never a phantom UI refresh.
        assert blocker["code"] == session_freshness.NO_SESSION_CODE
        # Python never asks the renderer to install credentials.
        assert manager.connections["ui"].sent == []

        # A usable token on the next tick clears the blocker — the only thing
        # left in the way is that no server is configured, which is now said too.
        tokens.expired = False
        result = await outbox.sync_pending()
        assert result["blocked"] == "aidream_server_unconfigured"
        blocker = outbox.publisher_blocker
        assert blocker is not None and blocker["code"] == "aidream_server_unconfigured"
    finally:
        await db.close()


async def test_temporary_daemon_failure_is_bounded_and_signout_is_explicit(monkeypatch):
    from app.services import sync_client
    from app.services.sync_client.client import SessionSnapshot

    class Client:
        last_state = SessionSnapshot(state="offline", state_reason="network")

        async def access_grant(self):
            return None

    client = Client()
    monkeypatch.setattr(sync_client, "get_sync_client", lambda: client)
    session_freshness.session_restored()
    assert not await session_freshness.request_session_grant(lane="a", reason="offline")
    assert session_freshness.session_refresh_pending()
    assert not session_freshness.session_refresh_pending(
        now=session_freshness._recovery_since + 91
    )
    client.last_state = SessionSnapshot(state="signed_out", state_reason="logout")
    assert not await session_freshness.request_session_grant(
        lane="a", reason="signed_out"
    )
    assert not session_freshness.session_refresh_pending()
