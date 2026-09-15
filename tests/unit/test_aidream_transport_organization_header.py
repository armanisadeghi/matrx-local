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
    """An org-gated POST (the runtime spine, tools) names the organization."""
    seen, transport = _recorder()
    client = AIDreamClient(BASE, transport=transport)

    await client.post("/v2/runtime/open", {}, jwt=JWT)

    assert seen[0].headers["X-Organization-Id"] == ORG
    assert seen[0].headers["Authorization"] == f"Bearer {JWT}"


@pytest.mark.anyio
async def test_authenticated_get_names_its_organization(resolves_org: None) -> None:
    """GET /api/agents rides this."""
    seen, transport = _recorder()
    client = AIDreamClient(BASE, transport=transport)

    await client.get("/agents", jwt=JWT)

    assert seen[0].headers["X-Organization-Id"] == ORG


@pytest.mark.anyio
async def test_owner_scoped_coding_session_routes_send_without_an_organization(
    cannot_resolve_org: None,
) -> None:
    """The server exempts /coding-sessions/bridge and /coding-sessions/sessions
    from the organization gate and resolves the organization inside the
    handler from the signed-in user. Refusing to send them without a header
    the server would not read is what paused 116,803 deliveries for nine days
    on a Mac with several memberships and no default (2026-09-08)."""
    seen, transport = _recorder()
    client = AIDreamClient(BASE, transport=transport)

    await client.post("/coding-sessions/bridge", {"entries": []}, jwt=JWT)
    await client.get("/coding-sessions/sessions?provider=claude_code", jwt=JWT)

    assert len(seen) == 2
    for request in seen:
        assert "X-Organization-Id" not in request.headers
        assert request.headers["Authorization"] == f"Bearer {JWT}"


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
        await client.post("/v2/runtime/open", {}, jwt=JWT)

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


# ── RemoteScraperClient — the scraper.app.matrxserver.com transport ─────────
#
# THE THIRD TRANSPORT, FOUND 22 HOURS INTO AN OUTAGE (SR-04, 2026-09-14).
# This suite's premise is that EVERY authenticated call this app makes names
# its organization. It covered two transports; there were three. The scraper
# client sent `Authorization` and nothing else, so the desktop retry queue's
# every poll of GET /api/scraper/queue/pending answered
#
#     400 {"error": "organization_required", ...}
#
# from at least 2026-09-13 22:40 until the fix. Proven live against
# scraper.app.matrxserver.com on 2026-09-14 with the admin@admin.com JWT:
# without the header 400 organization_required, with it 200 and one real
# queued URL waiting since 2026-09-12. The backoff logic in
# `scraper/retry_queue.py` was never at fault — it cannot out-wait a contract.


@pytest.fixture
def scraper_transport(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Record what the scraper client actually puts on the wire."""
    from app.services.scraper import remote_client as remote_client_module

    seen, transport = _recorder()
    real_client = httpx.AsyncClient

    def _factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=transport)

    monkeypatch.setattr(remote_client_module.httpx, "AsyncClient", _factory)
    return seen


def _scraper_client():
    from app.services.scraper.remote_client import RemoteScraperClient

    return RemoteScraperClient(server_url="https://scraper.test", api_key="")


@pytest.mark.anyio
async def test_scraper_queue_poll_names_its_organization(
    resolves_org: None, scraper_transport: list[httpx.Request]
) -> None:
    """The SR-04 path itself: GET /api/scraper/queue/pending."""
    await _scraper_client().get_pending(tier="desktop", limit=5, auth_token=JWT)

    request = scraper_transport[0]
    assert request.headers["X-Organization-Id"] == ORG
    assert request.headers["Authorization"] == f"Bearer {JWT}"


@pytest.mark.anyio
async def test_every_user_scoped_scraper_call_names_its_organization(
    resolves_org: None, scraper_transport: list[httpx.Request]
) -> None:
    """Not just the poll — the whole retry cycle plus the content push. A fix
    applied only to `get_pending` would have left claim/submit/fail 400ing the
    moment the poll started working, which is the per-call-site failure mode
    this suite exists to prevent."""
    client = _scraper_client()

    await client.get_pending(auth_token=JWT)
    await client.claim_items(item_ids=["x"], client_id="c", auth_token=JWT)
    await client.submit_result(
        queue_item_id="x", url="https://e.test", content={}, auth_token=JWT
    )
    await client.report_failure(queue_item_id="x", error="no", auth_token=JWT)
    await client.save_content(
        url="https://e.test", page_name="e", content={}, auth_token=JWT
    )

    assert len(scraper_transport) == 5
    for request in scraper_transport:
        assert request.headers["X-Organization-Id"] == ORG, request.url.path


@pytest.mark.anyio
async def test_scraper_api_key_lane_states_no_organization(
    resolves_org: None, scraper_transport: list[httpx.Request]
) -> None:
    """The approved-server lane names no acting user, so nothing is org-scoped
    — and the transport must not invent a tenant for it."""
    from app.services.scraper.remote_client import RemoteScraperClient

    client = RemoteScraperClient(server_url="https://scraper.test", api_key="svc-key")
    await client.get_pending()

    request = scraper_transport[0]
    assert "X-Organization-Id" not in request.headers
    assert request.headers["Authorization"] == "Bearer svc-key"


@pytest.mark.anyio
async def test_scraper_unresolvable_organization_refuses_with_a_remedy(
    cannot_resolve_org: None, scraper_transport: list[httpx.Request]
) -> None:
    from app.services.scraper.remote_client import RemoteScraperOrganizationError

    with pytest.raises(RemoteScraperOrganizationError) as excinfo:
        await _scraper_client().get_pending(auth_token=JWT)

    assert "Choose your organization in the desktop app" in excinfo.value.remedy
    assert scraper_transport == []
