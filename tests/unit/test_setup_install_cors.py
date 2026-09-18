"""Regression coverage for the /setup/install CORS preflight (dev-harness first run).

FirstRunScreen.tsx (desktop/src/components/FirstRunScreen.tsx) issues its
`POST /setup/install?mode=first_run` fetch with an explicit
``Cache-Control: no-cache`` request header (to keep the SSE stream from being
buffered). ``Cache-Control`` is not a CORS-safelisted request header, so the
browser sends a preflight ``OPTIONS`` asking whether the server allows it.

If the server's CORSMiddleware ``allow_headers`` list omits ``Cache-Control``,
Starlette answers the preflight with ``400 Disallowed CORS headers`` — the
browser then refuses to send the real POST at all and ``fetch`` fails with a
bare "Failed to fetch" network error, which is exactly what re-shows the
first-run setup wizard on every full page load of the dev harness
(http://localhost:1420).

Like tests/smoke/test_media_vault.py, this uses a plain FastAPI TestClient
with NO lifespan (no engine services started) — only the middleware stack
(CORSMiddleware, AuthMiddleware, logging) plus route registration run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

DEV_HARNESS_ORIGIN = "http://localhost:1420"


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    # No context manager on purpose: lifespan must NOT run (it would start
    # engine services). Plain requests still traverse the middleware stack,
    # including CORSMiddleware.
    return TestClient(app)


def test_setup_install_preflight_allows_the_dev_harnesss_cache_control_header(
    client: TestClient,
) -> None:
    """The exact preflight FirstRunScreen.tsx triggers must succeed.

    Chrome/WebKit send `Access-Control-Request-Headers: accept, cache-control`
    for this route's real fetch() call. A 400 here is what the user sees as
    "Setting up Matrx Local … Connection error: Failed to fetch".
    """
    response = client.options(
        "/setup/install",
        params={"mode": "first_run"},
        headers={
            "Origin": DEV_HARNESS_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "accept, cache-control",
        },
    )

    assert response.status_code == 200, (
        f"preflight was refused ({response.status_code}: {response.text!r}) — "
        "Cache-Control is missing from CORSMiddleware's allow_headers"
    )
    assert (
        response.headers.get("access-control-allow-origin") == DEV_HARNESS_ORIGIN
    )
    allowed_headers = {
        h.strip().lower()
        for h in response.headers.get("access-control-allow-headers", "").split(",")
    }
    assert "cache-control" in allowed_headers
