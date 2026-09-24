"""Guard: aimatrx.com can POST a chat turn straight to this engine.

When a conversation is attached to the user's own computer (compute target
kind "local-pc"), matrx-frontend's ``resolveBackendForConversation``
(features/agents/redux/execution-system/thunks/resolve-base-url.ts, channel
"local-runtime") sends the AI stream DIRECT to ``http://127.0.0.1:2214x/ai/...``
with the same headers it sends aidream. Any of those headers missing from
CORSMiddleware's ``allow_headers`` makes Starlette answer the preflight with
``400 Disallowed CORS headers``; the browser never sends the POST and the chat
shows "Stream connection failure: Failed to fetch" (live 2026-09-23 for
``X-Organization-Id``).

Plain TestClient without lifespan, like test_setup_install_cors.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

WEB_ORIGIN = "https://www.aimatrx.com"

# Every non-safelisted request header the web app sends on an /ai call.
WEB_CLIENT_HEADERS = [
    "authorization",
    "content-type",
    "x-organization-id",
    "x-fingerprint-id",
    "x-guest-fingerprint",
    "x-request-id",
    "x-idempotency-key",
    "last-event-id",
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_web_chat_turn_preflight_is_admitted(client: TestClient) -> None:
    response = client.options(
        "/ai/conversations/00000000-0000-0000-0000-000000000000",
        headers={
            "Origin": WEB_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": ", ".join(WEB_CLIENT_HEADERS),
            "Access-Control-Request-Private-Network": "true",
        },
    )

    assert response.status_code == 200, (
        f"preflight refused ({response.status_code}: {response.text!r}) — a "
        "header the web app sends is missing from CORSMiddleware allow_headers"
    )
    assert response.headers.get("access-control-allow-origin") == WEB_ORIGIN
    assert response.headers.get("access-control-allow-private-network") == "true"
    allowed = {
        h.strip().lower()
        for h in response.headers.get("access-control-allow-headers", "").split(",")
    }
    missing = [h for h in WEB_CLIENT_HEADERS if h not in allowed]
    assert not missing, f"not allowed: {missing}"
