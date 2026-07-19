"""Channel B round-trip smoke tests — extension ↔ engine, both directions,
against the REAL test engine on port 22399 (no live browser required).

Browser → engine: POST /extension/rpc with the generic `tool` command hits
the real dispatcher (SystemInfo).

Engine → browser: a WebSocket client SIMULATES the extension — it connects
to /extension/ws (pairing-token auth, `?token=` query param exactly like
the offscreen document), receives the `hello` envelope, then services an
`extension.invoke` pushed by the engine when we trigger
`invoke_extension_tool` via POST /extension/invoke. This exercises the full
callId-correlation protocol in `app/api/extension_invoke.py` +
`extension_ws_manager.py`.

Auth: /extension/* requires a WELL-FORMED JWT (malformed bearers fail
closed in `extension_auth._verify_token`). We mint a local HS256 token —
unverifiable by JWKS/introspection, so it is accepted presence-only over
loopback, which is the documented desktop posture.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict

import httpx
import pytest

from tests.conftest import TEST_BASE_URL, TEST_PORT

# ---------------------------------------------------------------------------
# Local HS256 JWT — parses as a JWT (required), verifies nowhere (accepted
# presence-only over loopback).
# ---------------------------------------------------------------------------


def _mint_local_jwt() -> str:
    import jwt as _jwt

    return _jwt.encode(
        {"sub": "smoke-test-user", "role": "authenticated", "exp": int(time.time()) + 3600},
        "matrx-local-smoke-secret-0123456789abcdef",
        algorithm="HS256",
    )


@pytest.fixture(scope="module")
def ext_token() -> str:
    return _mint_local_jwt()


@pytest.fixture(scope="module")
def ext_http(engine_url: str, ext_token: str):
    with httpx.Client(
        base_url=engine_url,
        headers={"Authorization": f"Bearer {ext_token}"},
        timeout=30.0,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Browser → engine: POST /extension/rpc
# ---------------------------------------------------------------------------


def test_rpc_requires_bearer(engine_url: str) -> None:
    r = httpx.post(f"{engine_url}/extension/rpc", json={"command": "health"}, timeout=10)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Pairing bootstrap: POST /extension/pair (loopback, no auth) → engine-issued
# token → that token authenticates /extension/rpc AND /extension/ws.
# ---------------------------------------------------------------------------


def test_pair_issues_stable_token_without_auth(engine_url: str) -> None:
    r = httpx.post(f"{engine_url}/extension/pair", timeout=10)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["service"] == "matrx-local"
    token = body["pair_token"]
    assert isinstance(token, str) and token.startswith("mxl_pair_")
    # Stable across calls (persisted, not re-minted per request).
    again = httpx.post(f"{engine_url}/extension/pair", timeout=10).json()
    assert again["pair_token"] == token
    # GET works too (desktop UI read path).
    got = httpx.get(f"{engine_url}/extension/pair", timeout=10).json()
    assert got["pair_token"] == token


def test_pair_rejected_over_tunnel_headers(engine_url: str) -> None:
    # A cloudflared-fronted request carries tunnel marker headers; pairing
    # must hard-reject those regardless of any auth presented.
    r = httpx.post(
        f"{engine_url}/extension/pair",
        headers={"Cf-Connecting-Ip": "203.0.113.7"},
        timeout=10,
    )
    assert r.status_code in (401, 403), r.text[:200]


def test_pair_token_authenticates_rpc_and_ws(engine_url: str) -> None:
    pair_token = httpx.post(f"{engine_url}/extension/pair", timeout=10).json()["pair_token"]

    # HTTP RPC with the pair token as bearer.
    r = httpx.post(
        f"{engine_url}/extension/rpc",
        json={"command": "health"},
        headers={"Authorization": f"Bearer {pair_token}"},
        timeout=10,
    )
    assert r.status_code == 200, r.text[:200]
    assert r.json()["ok"] is True

    # WS upgrade with the pair token in ?token= (browser posture).
    from websockets.sync.client import connect as ws_connect

    ws_url = f"ws://127.0.0.1:{TEST_PORT}/extension/ws?token={pair_token}"
    with ws_connect(ws_url, open_timeout=10) as ws:
        hello = json.loads(ws.recv(timeout=10))
        assert hello["type"] == "hello"
        assert hello["session_id"]


def test_wrong_pair_token_is_rejected(engine_url: str) -> None:
    r = httpx.post(
        f"{engine_url}/extension/rpc",
        json={"command": "health"},
        headers={"Authorization": "Bearer mxl_pair_definitely-not-the-real-one"},
        timeout=10,
    )
    assert r.status_code == 401


def test_rpc_health_version_capabilities(ext_http: httpx.Client) -> None:
    for command in ("health", "version", "capabilities"):
        r = ext_http.post("/extension/rpc", json={"command": command})
        assert r.status_code == 200, f"{command}: {r.text[:200]}"
        body = r.json()
        assert body["ok"] is True, f"{command}: {body}"
    caps = ext_http.post("/extension/rpc", json={"command": "capabilities"}).json()
    tools = caps["data"]["tools"]
    assert isinstance(tools, list) and len(tools) > 50
    assert any(t["name"] == "SystemInfo" for t in tools)


def test_rpc_tool_command_hits_real_dispatcher(ext_http: httpx.Client) -> None:
    """The generic `tool` command runs a REAL dispatcher tool end to end."""
    r = ext_http.post(
        "/extension/rpc",
        json={"command": "tool", "args": {"tool_name": "SystemInfo", "tool_input": {}}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, body
    payload = body["data"]
    assert payload["ok"] is True, payload
    result = payload["result"]
    assert result["type"] == "success", result
    assert result["output"], "SystemInfo returned empty output"


def test_rpc_unknown_command_is_ok_false(ext_http: httpx.Client) -> None:
    r = ext_http.post("/extension/rpc", json={"command": "definitely.not.a.command"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "Unknown command" in body["error"]


# ---------------------------------------------------------------------------
# Engine → browser: /extension/ws reverse channel (simulated extension)
# ---------------------------------------------------------------------------


def _ws_url(token: str) -> str:
    return f"ws://127.0.0.1:{TEST_PORT}/extension/ws?token={token}"


def test_ws_hello_and_ping_pong(ext_token: str) -> None:
    from websockets.sync.client import connect

    with connect(_ws_url(ext_token), open_timeout=10) as ws:
        hello = json.loads(ws.recv(timeout=10))
        assert hello["type"] == "hello"
        assert hello["session_id"]
        assert hello["engine_version"]
        assert hello["tool_catalog_hash"]

        ws.send(json.dumps({"type": "ping", "timestamp": int(time.time() * 1000)}))
        pong = json.loads(ws.recv(timeout=10))
        assert pong["type"] == "pong"
        assert pong["engine_version"] == hello["engine_version"]
        assert pong["tool_catalog_hash"] == hello["tool_catalog_hash"]


def test_ws_reverse_invoke_round_trip(ext_token: str) -> None:
    """Full engine→extension tool invocation over the reverse channel.

    The WS client plays the extension: it receives `extension.invoke` and
    answers `extension.result` with the matching callId. The trigger is
    POST /extension/invoke, the same primitive engine-side callers use
    (thin wrapper around `invoke_extension_tool`).
    """
    from websockets.sync.client import connect

    invoke_response: Dict[str, Any] = {}

    with connect(_ws_url(ext_token), open_timeout=10) as ws:
        hello = json.loads(ws.recv(timeout=10))
        session_id = hello["session_id"]

        def _post_invoke() -> None:
            r = httpx.post(
                f"{TEST_BASE_URL}/extension/invoke",
                headers={"Authorization": f"Bearer {ext_token}"},
                json={
                    "session_id": session_id,
                    "tool_name": "read_page",
                    "args": {"mode": "text"},
                    "timeout_seconds": 15,
                },
                timeout=30,
            )
            invoke_response["status"] = r.status_code
            invoke_response["body"] = r.json()

        poster = threading.Thread(target=_post_invoke, daemon=True)
        poster.start()

        # Service the engine-pushed invocation like the extension would.
        invoke = json.loads(ws.recv(timeout=15))
        assert invoke["type"] == "extension.invoke", invoke
        assert invoke["toolName"] == "read_page"
        assert invoke["args"] == {"mode": "text"}
        call_id = invoke["callId"]
        assert call_id

        ws.send(
            json.dumps(
                {
                    "type": "extension.result",
                    "callId": call_id,
                    "ok": True,
                    "result": {"text": "simulated page text", "url": "https://example.com"},
                }
            )
        )

        poster.join(timeout=20)
        assert not poster.is_alive(), "POST /extension/invoke never returned"

    assert invoke_response["status"] == 200, invoke_response
    body = invoke_response["body"]
    assert body["ok"] is True, body
    envelope = body["envelope"]
    assert envelope["ok"] is True
    assert envelope["callId"] == call_id
    assert envelope["result"] == {
        "text": "simulated page text",
        "url": "https://example.com",
    }


def test_ws_reverse_invoke_error_propagates(ext_token: str) -> None:
    """extension.result ok=false surfaces to the engine-side caller intact."""
    from websockets.sync.client import connect

    invoke_response: Dict[str, Any] = {}

    with connect(_ws_url(ext_token), open_timeout=10) as ws:
        hello = json.loads(ws.recv(timeout=10))
        session_id = hello["session_id"]

        def _post_invoke() -> None:
            r = httpx.post(
                f"{TEST_BASE_URL}/extension/invoke",
                headers={"Authorization": f"Bearer {ext_token}"},
                json={
                    "session_id": session_id,
                    "tool_name": "click_element",
                    "args": {"ref": "missing"},
                    "timeout_seconds": 15,
                },
                timeout=30,
            )
            invoke_response["status"] = r.status_code
            invoke_response["body"] = r.json()

        poster = threading.Thread(target=_post_invoke, daemon=True)
        poster.start()

        invoke = json.loads(ws.recv(timeout=15))
        ws.send(
            json.dumps(
                {
                    "type": "extension.result",
                    "callId": invoke["callId"],
                    "ok": False,
                    "error": "element not found",
                    "errorType": "NotFound",
                }
            )
        )
        poster.join(timeout=20)
        assert not poster.is_alive()

    body = invoke_response["body"]
    # HTTP layer ok=True (the round trip completed); tool layer ok=False.
    assert body["ok"] is True
    assert body["envelope"]["ok"] is False
    assert body["envelope"]["error"] == "element not found"
    assert body["envelope"]["errorType"] == "NotFound"
