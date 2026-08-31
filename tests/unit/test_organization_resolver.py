"""The ONE Python-side organization resolver
(``app.services.aidream.organization.resolve_active_organization_id``).

aidream's AuthMiddleware refuses every authenticated request that names no
organization, and refuses to pick one for the caller. `GET /auth/whoami`
cannot bootstrap it either — it now requires the header itself, so a
resolver that round-tripped through it would be circular. These tests prove
the resolver never makes an HTTP call to aidream (only to Supabase directly,
for membership + preference reads) and that a multi-org user with no stated
default gets a refusal carrying a remedy rather than a guessed organization.
"""

from __future__ import annotations

import jwt as pyjwt
import pytest

from app.services.aidream.organization import (
    OrganizationNotResolvedError,
    resolve_active_organization_id,
)

USER_ID = "11111111-1111-4111-8111-111111111111"


def _jwt() -> str:
    return pyjwt.encode({"sub": USER_ID}, "test-secret", algorithm="HS256")


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Records every call made through it — the honest way to prove "no
    aidream HTTP call was made": aidream calls go through a DIFFERENT client
    (``app.services.aidream.client.AIDreamClient``), which this test never
    touches at all. This fake only stands in for the direct Supabase REST
    calls the resolver is allowed to make."""

    calls: list[tuple[str, str]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *, json, headers):
        _FakeAsyncClient.calls.append(("POST", url))
        # mbr_for_user RPC
        return _FakeResponse(
            [
                {"container_id": cid, "status": "active"}
                for cid in _FakeAsyncClient.membership_ids
            ]
        )

    async def get(self, url, *, params, headers):
        _FakeAsyncClient.calls.append(("GET", url))
        default_id = _FakeAsyncClient.default_organization_id
        if default_id is None:
            return _FakeResponse([])
        return _FakeResponse(
            [{"preferences": {"organization": {"defaultOrganizationId": default_id}}}]
        )


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    import app.services.aidream.organization as organization_module

    organization_module._org_cache.clear()
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.membership_ids = []
    _FakeAsyncClient.default_organization_id = None
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)
    yield


@pytest.mark.anyio
async def test_resolves_sole_membership_and_never_calls_aidream_http() -> None:
    """Positive control: a single membership resolves cleanly, and every
    call made is to Supabase directly (POST rpc/mbr_for_user, GET
    user_preferences) — never a round trip through aidream's own API
    (e.g. GET /auth/whoami, which now requires the header it would be
    trying to obtain)."""
    _FakeAsyncClient.membership_ids = ["org-1"]

    org_id = await resolve_active_organization_id(_jwt())

    assert org_id == "org-1"
    for method, url in _FakeAsyncClient.calls:
        assert "/auth/whoami" not in url
        assert "aidream" not in url.lower() or "supabase" in url.lower()


@pytest.mark.anyio
async def test_resolves_stated_default_for_multi_org_user() -> None:
    """Positive control for the refusal test below: same multi-org
    membership set, but WITH a stated default preference — resolves without
    asking."""
    _FakeAsyncClient.membership_ids = ["org-1", "org-2"]
    _FakeAsyncClient.default_organization_id = "org-2"

    org_id = await resolve_active_organization_id(_jwt())

    assert org_id == "org-2"


@pytest.mark.anyio
async def test_multi_org_user_with_no_default_is_refused_with_a_remedy() -> None:
    """A multi-org user with no stated default gets a refusal carrying a
    plain-language remedy — never a guess (no owner-or-oldest, no first,
    no most-recent)."""
    _FakeAsyncClient.membership_ids = ["org-1", "org-2"]
    _FakeAsyncClient.default_organization_id = None

    with pytest.raises(OrganizationNotResolvedError) as excinfo:
        await resolve_active_organization_id(_jwt())

    assert excinfo.value.remedy
    assert "choose your organization" in excinfo.value.remedy.lower()


@pytest.mark.anyio
async def test_no_membership_is_refused_with_a_remedy() -> None:
    _FakeAsyncClient.membership_ids = []

    with pytest.raises(OrganizationNotResolvedError) as excinfo:
        await resolve_active_organization_id(_jwt())

    assert excinfo.value.remedy
