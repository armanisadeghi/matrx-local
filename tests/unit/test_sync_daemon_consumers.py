"""Daemon-grant consumers never split or retain a user's bearer."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.anyio
async def test_token_repo_returns_one_atomic_daemon_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.local_db.repositories import TokenRepo
    import app.services.sync_client as sync_client

    class Client:
        async def access_grant(self) -> tuple[str, str]:
            return "current-access-token", "current-user"

        async def access_token(self) -> str:  # pragma: no cover - must not be called
            raise AssertionError("TokenRepo must read the atomic grant")

    monkeypatch.setattr(sync_client, "get_sync_client", Client)

    row = await TokenRepo().get()

    assert row == {
        "key": "current_user",
        "access_token": "current-access-token",
        "user_id": "current-user",
        "expires_at": None,
    }


@pytest.mark.anyio
async def test_scheduler_requests_resolve_the_current_daemon_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.scheduler_host import scheduler_client_options
    import app.services.sync_client as sync_client
    from supabase import create_async_client

    grants: list[tuple[str, str] | None] = [("user-a-token", "user-a"), None]

    class Client:
        async def access_grant(self) -> tuple[str, str] | None:
            return grants.pop(0)

    seen_authorization: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers["Authorization"])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(sync_client, "get_sync_client", Client)
    options = scheduler_client_options(
        "publishable-key", transport=httpx.MockTransport(respond)
    )
    assert options.httpx_client is not None
    client = await create_async_client("https://example.test", "publishable-key", options=options)
    try:
        await client.postgrest.from_("sch_task").select("*").execute()
        await client.postgrest.from_("sch_task").select("*").execute()
    finally:
        await options.httpx_client.aclose()

    assert seen_authorization == ["Bearer user-a-token", "Bearer publishable-key"]
