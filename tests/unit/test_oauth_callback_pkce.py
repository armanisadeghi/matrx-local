"""The real callback routes only a code/state pair, never implicit/raw values."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.api.auth import oauth_callback


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("query", [b"code=test-code", b"access_token=test-token&refresh_token=test-refresh"])
async def test_incomplete_or_implicit_callback_never_broadcasts(monkeypatch, query):
    send = AsyncMock()
    manager = SimpleNamespace(connections={"local": SimpleNamespace(via_tunnel=False)}, _send=send)
    monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(websocket_manager=manager))
    result = await oauth_callback(Request({"type": "http", "query_string": query}))
    assert result.status_code == 400
    send.assert_not_awaited()


@pytest.mark.anyio
async def test_code_and_state_go_only_to_local_clients_without_other_parameters(monkeypatch):
    local = SimpleNamespace(via_tunnel=False)
    remote = SimpleNamespace(via_tunnel=True)
    send = AsyncMock()
    manager = SimpleNamespace(connections={"local": local, "remote": remote}, _send=send)
    monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(websocket_manager=manager))
    result = await oauth_callback(Request({"type": "http", "query_string":
        b"code=test-code&state=test-state&access_token=discard-me&extra=discard-me"}))
    assert result.status_code == 200
    send.assert_awaited_once_with(local, {"type": "oauth-callback", "code": "test-code", "state": "test-state"})
