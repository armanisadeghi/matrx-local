"""The server catalog reads (/ai-tools, /ai-models) are AUTHENTICATED reads.

WHY THIS SUITE EXISTS: on 2026-09-29 aidream declared ``GET /api/ai-tools``
and ``GET /api/ai-models`` with ``authenticated_resource_bootstrap``: a signed-in
read that needs no organization but does need a person. This client still
fetched both anonymously (its docstrings said "public, no auth needed"), so
every call was a 401. The installed app's session adoption refreshes the tool
catalog, failed on that 401, and retried every ~32 s forever: 628 failed
``/ai-tools`` reads and 18 failed ``/ai-models`` reads in one day
(MXL-R-006 / MXL-R-007 / MXL-R-004), and the signed-in session never finished
connecting its services.

Guards, each shown failing before the fix:
  * the transport sends the bearer on both catalog reads;
  * a person with no organization chosen still reads the catalog (the server
    admits it; the transport must not refuse it with a local 400);
  * the remote tool bridge never asks the server anonymously.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.aidream import organization as organization_module
from app.services.aidream.client import AIDreamClient, AIDreamError

JWT = "test-jwt"
ORG = "11111111-2222-4333-8444-555555555555"
BASE = "https://aidream.test"


def _server_requiring_a_person() -> tuple[list[httpx.Request], httpx.MockTransport]:
    """Answers exactly like aidream: 401 without a bearer, rows with one."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return httpx.Response(401, json={"error": "authentication_required"})
        if request.url.path == "/api/ai-models":
            return httpx.Response(200, json={"models": [{"id": "m1"}]})
        return httpx.Response(200, json={"tools": [{"id": "t1", "name": "x"}]})

    return seen, httpx.MockTransport(handler)


@pytest.fixture
def resolves_org(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_jwt: str) -> str:
        return ORG

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", _resolve
    )


@pytest.fixture
def cannot_resolve_org(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_jwt: str) -> str:
        raise organization_module.OrganizationNotResolvedError(
            "You belong to more than one organization and haven't set one.",
            remedy="Choose your organization in the desktop app.",
        )

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", _resolve
    )


@pytest.mark.anyio
async def test_tool_catalog_read_carries_the_person(resolves_org: None) -> None:
    seen, transport = _server_requiring_a_person()
    client = AIDreamClient(BASE, transport=transport)

    tools = await client.fetch_tools(jwt=JWT)

    assert tools == [{"id": "t1", "name": "x"}]
    assert seen[0].url.path == "/api/ai-tools"
    assert seen[0].headers["authorization"] == f"Bearer {JWT}"
    assert seen[0].headers["x-organization-id"] == ORG


@pytest.mark.anyio
async def test_model_catalog_read_carries_the_person(resolves_org: None) -> None:
    seen, transport = _server_requiring_a_person()
    client = AIDreamClient(BASE, transport=transport)

    models = await client.fetch_models(jwt=JWT)

    assert models == [{"id": "m1"}]
    assert seen[0].headers["authorization"] == f"Bearer {JWT}"


@pytest.mark.anyio
async def test_app_tool_catalog_read_carries_the_person(resolves_org: None) -> None:
    seen, transport = _server_requiring_a_person()
    client = AIDreamClient(BASE, transport=transport)

    await client.fetch_tools_for_app("matrx_local", jwt=JWT)

    assert seen[0].url.path == "/api/ai-tools/app/matrx_local/all"
    assert seen[0].headers["authorization"] == f"Bearer {JWT}"


@pytest.mark.anyio
async def test_catalog_read_without_an_organization_still_reaches_the_server(
    cannot_resolve_org: None,
) -> None:
    """The server admits these reads with no organization; so must we."""
    seen, transport = _server_requiring_a_person()
    client = AIDreamClient(BASE, transport=transport)

    tools = await client.fetch_tools(jwt=JWT)

    assert tools, "the catalog read was refused locally for want of an organization"
    assert "x-organization-id" not in seen[0].headers


@pytest.mark.anyio
async def test_an_org_gated_read_is_still_refused_without_an_organization(
    cannot_resolve_org: None,
) -> None:
    """The org-optional lane is exactly the catalog reads — nothing wider."""
    seen, transport = _server_requiring_a_person()
    client = AIDreamClient(BASE, transport=transport)

    with pytest.raises(AIDreamError) as info:
        await client.get("/agents", jwt=JWT)

    assert info.value.status == 400
    assert seen == []


@pytest.mark.anyio
async def test_bridge_refresh_reads_the_catalog_as_the_signed_in_person(
    monkeypatch: pytest.MonkeyPatch, resolves_org: None
) -> None:
    from app.services.ai import remote_tool_bridge as bridge_module
    from app.services.ai.remote_tool_bridge import RemoteToolBridge

    seen, transport = _server_requiring_a_person()
    monkeypatch.setattr(
        bridge_module,
        "get_aidream_client",
        lambda: AIDreamClient(BASE, transport=transport),
    )

    async def _stored_token() -> str:
        return JWT

    monkeypatch.setattr(bridge_module, "_stored_jwt", _stored_token)

    await RemoteToolBridge().refresh()

    assert seen and all(
        r.headers.get("authorization") == f"Bearer {JWT}" for r in seen
    )


@pytest.mark.anyio
async def test_bridge_refresh_signed_out_never_asks_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai import remote_tool_bridge as bridge_module
    from app.services.ai.remote_tool_bridge import RemoteToolBridge

    seen, transport = _server_requiring_a_person()
    monkeypatch.setattr(
        bridge_module,
        "get_aidream_client",
        lambda: AIDreamClient(BASE, transport=transport),
    )

    async def _no_token() -> None:
        return None

    monkeypatch.setattr(bridge_module, "_stored_jwt", _no_token)

    with pytest.raises(AIDreamError) as info:
        await RemoteToolBridge().refresh()

    assert info.value.status == 401
    assert seen == [], "a signed-out refresh spent a guaranteed-401 round trip"
