from __future__ import annotations

import pytest
import httpx

from app.services.aidream import organization as organization_module
from app.services.aidream.client import AIDreamClient
from app.services.ai.tool_source import OrganizationAwareToolSource


class FakeClient:
    def __init__(self, _base_url: str) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def get(self, path: str, jwt: str | None = None):
        self.calls.append((path, jwt))
        return {
            "tools": [{"id": "tool-1", "name": "filesystem_list"}],
            "executor_name": "matrx-local",
        }


@pytest.mark.anyio
async def test_tool_source_waits_for_a_session_then_uses_the_shared_transport() -> None:
    client = FakeClient("https://aidream.test")
    token: str | None = None
    source = OrganizationAwareToolSource(
        server_url="https://aidream.test",
        source_app="matrx_local",
        get_jwt=lambda: token,
        client_factory=lambda _url: client,  # type: ignore[arg-type]
    )

    assert await source.list_tools() == []
    assert client.calls == []

    token = "jwt-a"
    assert await source.list_tools() == [{"id": "tool-1", "name": "filesystem_list"}]
    assert client.calls == [("/ai-tools/app/matrx_local/all", "jwt-a")]
    assert await source.list_bindings() == [
        {"tool_id": "tool-1", "executor_name": "matrx-local"}
    ]


@pytest.mark.anyio
async def test_tool_source_names_the_organization_on_its_server_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve_organization(_jwt: str) -> str:
        return "11111111-2222-4333-8444-555555555555"

    monkeypatch.setattr(
        organization_module,
        "resolve_active_organization_id",
        resolve_organization,
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"tools": []})

    transport = httpx.MockTransport(handler)
    source = OrganizationAwareToolSource(
        server_url="https://aidream.test",
        source_app="matrx_local",
        get_jwt=lambda: "jwt-a",
        client_factory=lambda url: AIDreamClient(url, transport=transport),
    )

    assert await source.list_tools() == []
    assert seen[0].headers["Authorization"] == "Bearer jwt-a"
    assert seen[0].headers["X-Organization-Id"] == "11111111-2222-4333-8444-555555555555"


@pytest.mark.anyio
async def test_async_daemon_provider_is_read_for_every_discovery() -> None:
    client = FakeClient("https://aidream.test")
    tokens = iter(["first-grant", "rotated-grant", None])

    async def current_token() -> str | None:
        return next(tokens)

    source = OrganizationAwareToolSource(
        server_url="https://aidream.test",
        source_app="matrx_local",
        get_jwt=current_token,
        client_factory=lambda _url: client,
    )
    await source.list_tools()
    await source.list_tools()
    assert await source.list_tools() == []
    assert [token for _, token in client.calls] == ["first-grant", "rotated-grant"]


@pytest.mark.anyio
async def test_engine_provider_reads_daemon_after_rotation_and_signout(monkeypatch) -> None:
    from app.services.ai.engine import _get_jwt
    from app.services import sync_client

    class Daemon:
        token = "first-grant"

        async def access_token(self):
            return self.token

    daemon = Daemon()
    monkeypatch.setattr(sync_client, "get_sync_client", lambda: daemon)
    assert await _get_jwt() == "first-grant"
    daemon.token = "rotated-grant"
    assert await _get_jwt() == "rotated-grant"
    daemon.token = None
    assert await _get_jwt() is None
