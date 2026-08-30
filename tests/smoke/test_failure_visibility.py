"""Failures must reach the browser AND the access log.

Both regressions here were found together on a live machine (2026-08-30) while
diagnosing a Claude session-detail sync that only ever said ``Load failed``:

* An unhandled exception unwound past ``CORSMiddleware`` to Starlette's
  ``ServerErrorMiddleware``, which answers 500 with no CORS headers. The browser
  rejects that before any JS sees it, so ``fetch`` throws a bare
  ``TypeError: Load failed`` — no status, no message, nothing to act on.
* The request-logging middleware is the INNERMOST one, so anything refused by
  auth never reached it. ``access.log`` held only 200/202 across ~59k requests,
  reading as "nothing ever failed" when failures were simply unloggable.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

WEBVIEW_ORIGIN = "tauri://localhost"


@pytest.fixture()
def failing_app() -> FastAPI:
    """The real logging middleware under a real CORS layer, over a route that raises."""
    from app.main import _log_requests_dispatch

    app = FastAPI()

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("database is locked")

    # Same order as app/main.py: logging innermost, CORS outside it.
    app.add_middleware(BaseHTTPMiddleware, dispatch=_log_requests_dispatch)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[WEBVIEW_ORIGIN],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["Authorization", "Content-Type"],
    )
    return app


def _recent_statuses(n: int = 20) -> list[int]:
    import app.common.access_log as access_log

    return [entry.get("status") for entry in access_log.recent(n)]


def test_unhandled_exception_returns_a_cors_visible_500(failing_app: FastAPI) -> None:
    async def _run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=failing_app, raise_app_exceptions=False),
            base_url="http://engine.test",
            timeout=30.0,
        ) as client:
            return await client.get("/boom", headers={"Origin": WEBVIEW_ORIGIN})

    response = asyncio.run(_run())

    assert response.status_code == 500
    # Without this header the webview discards the response and the caller sees
    # only "TypeError: Load failed".
    assert response.headers.get("access-control-allow-origin") == WEBVIEW_ORIGIN

    body = response.json()
    assert body["failure_id"], "a failure must be traceable to a log line"
    assert body["path"] == "/boom"
    assert body["method"] == "GET"
    assert "detail" in body


def test_unhandled_exception_is_recorded_in_the_access_log(failing_app: FastAPI) -> None:
    async def _run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=failing_app, raise_app_exceptions=False),
            base_url="http://engine.test",
            timeout=30.0,
        ) as client:
            await client.get("/boom", headers={"Origin": WEBVIEW_ORIGIN})

    asyncio.run(_run())
    assert 500 in _recent_statuses(), "a 500 must be visible in the access log"


def test_auth_rejection_is_recorded_in_the_access_log() -> None:
    """A refused request never reaches the logging middleware; auth records it."""
    from starlette.requests import Request

    from app.api.auth import _auth_error_response

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/coding-session/claude/labels/status",
        "headers": [
            (b"origin", WEBVIEW_ORIGIN.encode()),
            (b"user-agent", b"pytest"),
        ],
        "query_string": b"",
    }
    response = _auth_error_response(
        "/coding-session/claude/labels/status",
        request=Request(scope),
        status_code=401,
        message="Authorization required",
        code="authorization_required",
    )

    assert response.status_code == 401
    assert 401 in _recent_statuses()
