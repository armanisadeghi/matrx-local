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
    assert "database is locked" in body["detail"]
    assert "Retry the operation" in body["hint"]


def test_failure_diagnostics_redact_captured_request_credentials() -> None:
    """A real request shape must not leak opaque header/query/body secrets."""
    from starlette.requests import Request

    from app.main import _failure_description, _format_request_details

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/boom",
        "query_string": b"x-api-key=query-secret&safe=ok",
        "headers": [
            (b"x-api-key", b"header-secret"),
            (b"authorization", b"Bearer bearer-secret"),
        ],
        "scheme": "http",
        "server": ("engine.test", 80),
    })
    diagnostics = _format_request_details(
        request,
        {"nested": {"refreshToken": "body-secret"}, "safe": "ok"},
    )
    detail, hint = _failure_description(
        RuntimeError("remote response x-api-key=exception-secret"), "POST", "/boom",
        secret_values=("exception-secret",),
    )

    for secret in ("query-secret", "header-secret", "bearer-secret", "body-secret", "exception-secret"):
        assert secret not in f"{diagnostics} {detail} {hint}"
    assert "[REDACTED]" in diagnostics
    assert "RuntimeError" in detail


def test_formatted_handler_redacts_exception_traceback_and_keeps_location() -> None:
    """A formatter must not append the raw exc_info after the filter ran."""
    import io
    import logging

    from app.common.system_logger import SensitiveDataFilter

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("failure-visibility-formatted-handler-test")
    logger.handlers.clear()
    logger.propagate = False
    logger.addFilter(SensitiveDataFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        try:
            raise RuntimeError("sync failed x-api-key=exception-secret")
        except RuntimeError:
            logger.warning("chat sync tick crashed", exc_info=True)
    finally:
        logger.removeHandler(handler)

    message = output.getvalue()
    assert "RuntimeError: sync failed" in message
    assert "exception-secret" not in message
    assert "[REDACTED]" in message


def test_filter_clears_cached_exception_text_and_redacts_activity_traceback() -> None:
    """Cached formatter state and Activity metadata cannot retain credentials."""
    import logging
    import sys

    from app.common.system_logger import SensitiveDataFilter

    try:
        raise RuntimeError("sync failed x-api-key=exception-secret")
    except RuntimeError:
        record = logging.LogRecord(
            "failure-visibility-cached-traceback-test",
            logging.ERROR,
            __file__,
            1,
            "sync failed",
            (),
            exc_info=sys.exc_info(),
        )
    record.exc_text = "cached exception-secret"
    record.traceback = "activity x-api-key=exception-secret"

    assert SensitiveDataFilter().filter(record)
    assert record.exc_info is None
    assert record.exc_text is None
    assert "exception-secret" not in record.traceback
    assert "[REDACTED]" in record.traceback


def test_formatted_handler_redacts_percent_encoded_sensitive_key() -> None:
    """Percent-encoding a credential key cannot bypass global log redaction."""
    import io
    import logging

    from app.common.system_logger import SensitiveDataFilter

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("failure-visibility-encoded-key-test")
    logger.handlers.clear()
    logger.propagate = False
    logger.addFilter(SensitiveDataFilter())
    logger.addHandler(handler)
    try:
        logger.error("callback query %43oDe=encoded-secret&safe=kept")
    finally:
        logger.removeHandler(handler)

    rendered = output.getvalue()
    assert "encoded-secret" not in rendered
    assert "%43oDe=[REDACTED]" in rendered
    assert "safe=kept" in rendered


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


def test_reflected_post_secret_is_absent_from_response_and_formatted_logs() -> None:
    """Raw POST bodies must survive long enough to redact reflected errors."""
    import io
    import logging

    from app.main import _log_requests_dispatch

    app = FastAPI()

    @app.post("/reflect")
    async def reflect(body: dict[str, str]) -> None:
        raise RuntimeError(f"upstream rejected client_secret={body['client_secret']}")

    app.add_middleware(BaseHTTPMiddleware, dispatch=_log_requests_dispatch)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    app_logger = logging.getLogger("system_logger")
    app_logger.addHandler(handler)

    async def _run() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://engine.test",
            timeout=30.0,
        ) as client:
            return await client.post(
                "/reflect",
                json={"client_secret": "body-secret", "context": "preserved-context"},
            )

    try:
        response = asyncio.run(_run())
    finally:
        app_logger.removeHandler(handler)

    payload = response.json()
    rendered = f"{response.text}\n{output.getvalue()}"
    assert response.status_code == 500
    assert "body-secret" not in rendered
    assert payload["failure_id"]
    assert payload["method"] == "POST"
    assert payload["path"] == "/reflect"
    assert "RuntimeError" in payload["detail"]
    assert "Traceback" in rendered
    assert "preserved-context" in rendered


def test_callback_credentials_are_absent_from_request_and_structured_access_logs(caplog) -> None:
    """The actual middleware must never log any OAuth callback value or fragment."""
    import logging
    import app.common.access_log as access_log

    from app.main import _log_requests_dispatch

    callback_app = FastAPI()

    @callback_app.get("/auth/callback")
    async def callback() -> None:
        raise RuntimeError("callback failed")

    callback_app.add_middleware(BaseHTTPMiddleware, dispatch=_log_requests_dispatch)

    async def _run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=callback_app, raise_app_exceptions=False),
            base_url="http://engine.test",
            timeout=30.0,
        ) as client:
            await client.get(
                "/auth/callback?code=test-oauth-code&state=test-oauth-state&token=test-oauth-token&safe=ok"
            )

    app_logger = logging.getLogger("system_logger")
    app_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="system_logger"):
            asyncio.run(_run())
    finally:
        app_logger.removeHandler(caplog.handler)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    entry = access_log.recent(1)[0]
    serialized_entry = str(entry)
    for secret in ("test-oauth-code", "test-oauth-state", "test-oauth-token"):
        assert secret not in messages
        assert secret not in serialized_entry
    assert "code=[REDACTED]" in messages
    assert entry["query"] == "code=%5BREDACTED%5D&state=%5BREDACTED%5D&token=%5BREDACTED%5D&safe=ok"


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


def test_engine_has_no_public_oauth_callback_transport() -> None:
    """OAuth callbacks are delivered only to Vite or the native deep-link handler."""
    from app.main import app

    assert "/auth/callback" not in app.openapi()["paths"]
