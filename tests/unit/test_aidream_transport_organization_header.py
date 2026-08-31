"""Every authenticated call this app makes names its organization — at the
TRANSPORT, not at whichever call site happened to remember.

WHY THIS SUITE EXISTS: on 2026-08-30 aidream's AuthMiddleware began refusing
authenticated requests that carry no ``X-Organization-Id`` (400
``organization_required``) before routing. matrx-local attached the header at
four call sites (runtime spine, remote tool bridge, credential vault, desktop
content-IR) and nowhere else, so in the first 47 minutes of the gate being
live production logged ~3,000 rejects from this app alone:

    POST /api/coding-sessions/bridge     2823
    GET  /ai/user/pending_calls           181
    GET  /api/coding-sessions/sessions      3
    GET  /api/agents                        4

Those four paths ride the two transports covered here. The header is now built
where ``Authorization`` is built — one place per transport — so a new call site
cannot forget it. These are the failing-then-passing guards for that.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.aidream import organization as organization_module
from app.services.aidream.client import AIDreamClient, AIDreamError
from app.services.delegation.client import DelegationApiClient, DelegationApiError

JWT = "test-jwt"
ORG = "11111111-2222-4333-8444-555555555555"
BASE = "https://aidream.test"


@pytest.fixture
def resolves_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ONLY the network identity lookup — never the header assembly."""

    async def _resolve(_jwt: str) -> str:
        return ORG

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", _resolve
    )


@pytest.fixture
def cannot_resolve_org(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_jwt: str) -> str:
        raise organization_module.OrganizationNotResolvedError(
            "You belong to more than one organization and haven't set a default.",
            remedy="Choose your organization in the desktop app, then try again.",
        )

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", _resolve
    )


def _recorder() -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # pending_calls answers with a bare list; the other paths with an
        # object. One recorder serves both.
        if request.url.path.endswith("/pending_calls"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"ok": True})

    return seen, httpx.MockTransport(handler)


# ── AIDreamClient — the coding-session bridge / agents / sessions transport ──


@pytest.mark.anyio
async def test_authenticated_post_names_its_organization(resolves_org: None) -> None:
    """The 2823-reject path: POST /api/coding-sessions/bridge."""
    seen, transport = _recorder()
    client = AIDreamClient(BASE, transport=transport)

    await client.post("/coding-sessions/bridge", {"entries": []}, jwt=JWT)

    assert seen[0].headers["X-Organization-Id"] == ORG
    assert seen[0].headers["Authorization"] == f"Bearer {JWT}"


@pytest.mark.anyio
async def test_authenticated_get_names_its_organization(resolves_org: None) -> None:
    """GET /api/agents and GET /api/coding-sessions/sessions ride this."""
    seen, transport = _recorder()
    client = AIDreamClient(BASE, transport=transport)

    await client.get("/coding-sessions/sessions", jwt=JWT)

    assert seen[0].headers["X-Organization-Id"] == ORG


@pytest.mark.anyio
async def test_public_call_states_no_organization(resolves_org: None) -> None:
    """No JWT means no identity to scope — the gate does not apply, and the
    transport must not invent a tenant for an anonymous catalog read."""
    seen, transport = _recorder()
    client = AIDreamClient(BASE, transport=transport)

    await client.get("/ai-models")

    assert "X-Organization-Id" not in seen[0].headers
    assert "Authorization" not in seen[0].headers


@pytest.mark.anyio
async def test_caller_supplied_organization_wins(resolves_org: None) -> None:
    """The runtime spine and tool bridge act under a SPECIFIC lease's
    organization; the transport fills a gap, it never overrules a caller."""
    seen, transport = _recorder()
    client = AIDreamClient(BASE, transport=transport)
    lease_org = "99999999-8888-4777-8666-555555555555"

    await client.post(
        "/v2/runtime/open",
        {},
        jwt=JWT,
        headers={"X-Organization-Id": lease_org},
    )

    assert seen[0].headers["X-Organization-Id"] == lease_org


@pytest.mark.anyio
async def test_unresolvable_organization_refuses_with_a_remedy(
    cannot_resolve_org: None,
) -> None:
    """Never send a request that is a guaranteed 400, and never fail silently:
    the refusal names what the person has to do."""
    seen, transport = _recorder()
    client = AIDreamClient(BASE, transport=transport)

    with pytest.raises(AIDreamError) as excinfo:
        await client.post("/coding-sessions/bridge", {"entries": []}, jwt=JWT)

    assert excinfo.value.status == 400
    assert "Choose your organization in the desktop app" in str(excinfo.value)
    assert seen == []


# ── DelegationApiClient — the /ai/user/pending_calls transport ───────────────


@pytest.mark.anyio
async def test_pending_calls_names_its_organization(resolves_org: None) -> None:
    """The 181-reject path: GET /ai/user/pending_calls."""
    seen, transport = _recorder()
    client = DelegationApiClient(BASE, transport=transport)

    await client.list_pending_calls(JWT)

    assert seen[0].headers["X-Organization-Id"] == ORG
    assert seen[0].headers["Authorization"] == f"Bearer {JWT}"


@pytest.mark.anyio
async def test_delegation_unresolvable_organization_refuses_with_a_remedy(
    cannot_resolve_org: None,
) -> None:
    seen, transport = _recorder()
    client = DelegationApiClient(BASE, transport=transport)

    with pytest.raises(DelegationApiError) as excinfo:
        await client.list_pending_calls(JWT)

    assert excinfo.value.status == 400
    assert "Choose your organization in the desktop app" in str(excinfo.value)
    assert seen == []
