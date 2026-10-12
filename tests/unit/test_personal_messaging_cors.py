"""Actual full application CORS; no duplicate middleware or fake native route."""

import asyncio
import httpx
from app.main import app
from app import main as native_main
import os
from pathlib import Path


def test_managed_preview_personal_preflight_uses_canonical_full_application():
    assert (
        Path(native_main.__file__).resolve().parents[1]
        == Path(
            os.environ.get(
                "EXPECTED_NATIVE_SOURCE", str(Path(__file__).resolve().parents[2])
            )
        ).resolve()
    )
    assert any(
        getattr(route, "path", None) == "/personal-messaging/beeper/select-account"
        for route in app.routes
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 41230)),
            base_url="http://127.0.0.1:22259",
        ) as client:
            headers = {
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type,X-Organization-Id",
            }
            for origin in (
                "http://meta-customer-connections.localhost:3001",
                "http://localhost:3001",
                "http://127.0.0.1:3001",
            ):
                response = await client.options(
                    "/personal-messaging/beeper/select-account",
                    headers={**headers, "Origin": origin},
                )
                assert (
                    response.status_code == 200
                    and response.headers["Access-Control-Allow-Origin"] == origin
                )
                assert response.headers["Access-Control-Allow-Credentials"] == "true"
            for origin in (
                "http://evil.localhost.com:3001",
                "http://foo.bar.localhost:3001",
                "http://meta-customer-connections.localhost:3002",
                "https://meta-customer-connections.localhost:3001",
            ):
                response = await client.options(
                    "/personal-messaging/beeper/select-account",
                    headers={**headers, "Origin": origin},
                )
                assert (
                    response.status_code == 400
                    and "Access-Control-Allow-Origin" not in response.headers
                )

    asyncio.run(run())
