"""The ONE Python-side organization resolver
(``app.services.aidream.organization.resolve_active_organization_id``).

THE RULING UNDER TEST (Arman, 2026-09-19)

    A saved "default organization" on the user's account is at most a display
    preference. Nothing that builds a request may read it, and the personal
    organization is never a fallback. What a client may use is what the user
    THEMSELVES SET on this device. With nothing set, the work HOLDS: the
    sidecar publishes an ``organization_required`` action-needed item, the
    desktop turns that into the picker, and the caller retries with the set
    value.

    "one missed org check that should have just failed turns into 50 in a
    month and 5,000 in a year, and suddenly we don't have orgs any more, we
    have a user and a default org, which means we just have user now."

These tests are built to FAIL if either deleted rung comes back. The fake
HTTP client below REFUSES the ``user_preferences`` and
``current_personal_org_id`` endpoints outright, so a resolver that reaches
for them cannot pass quietly — and the headline case also asserts the hold
was published, which a guessing resolver would never do.

They keep the older guarantee too: the resolver never round-trips through
aidream (``GET /auth/whoami`` itself 400s without ``X-Organization-Id``, so
asking it to bootstrap the header is circular).
"""

from __future__ import annotations

import jwt as pyjwt
import pytest

from app.services.aidream import organization as org_module
from app.services.aidream.organization import (
    OrganizationNotResolvedError,
    resolve_active_organization_id,
)

USER_ID = "11111111-1111-4111-8111-111111111111"
ORG_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORG_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ORG_PERSONAL = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

# Naming the banned endpoints is how this test proves the resolver never
# reaches for either one: the fake HTTP client below REFUSES them.
# org-default-exempt: named only so the fake client can refuse them, never read
BANNED_ENDPOINTS = ("user_preferences", "current_personal_org_id")


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
    """Stands in for the direct Supabase REST calls the resolver is allowed
    to make, and BLOWS UP on the two it is not.

    aidream calls go through a different client entirely
    (``app.services.aidream.client.AIDreamClient``), which these tests never
    touch — so "no aidream HTTP call" is structural, not asserted.
    """

    calls: list[tuple[str, str]] = []
    memberships: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def _record(self, method: str, url: str) -> None:
        type(self).calls.append((method, url))
        for banned in BANNED_ENDPOINTS:
            if banned in url:
                raise AssertionError(
                    f"the resolver read {banned!r} — a request may only use "
                    "what the user SET on this device"
                )

    async def post(self, url, **kwargs):
        self._record("POST", url)
        if url.endswith("/rpc/mbr_for_user"):
            return _FakeResponse(
                [
                    {"container_id": org_id, "status": "active"}
                    for org_id in type(self).memberships
                ]
            )
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, **kwargs):
        self._record("GET", url)
        raise AssertionError(f"unexpected GET {url}")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    import httpx

    _FakeAsyncClient.calls = []
    _FakeAsyncClient.memberships = []
    org_module.invalidate_organization_cache()
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    # No device selection, and nothing publishes for real, unless a test says so.
    monkeypatch.setattr(org_module, "get_device_organization", _none_device)
    holds: list[str | None] = []
    monkeypatch.setattr(org_module, "_raise_hold", _recorder(holds))
    monkeypatch.setattr(org_module, "_clear_hold", _noop)
    yield holds
    org_module.invalidate_organization_cache()


async def _none_device(user_id=None):
    return None


async def _noop():
    return None


def _recorder(sink):
    async def _raise_hold(user_id):
        sink.append(user_id)

    return _raise_hold


def _device(organization_id: str | None):
    async def _get(user_id=None):
        return organization_id

    return _get


@pytest.mark.anyio
async def test_this_devices_set_organization_wins(monkeypatch):
    _FakeAsyncClient.memberships = [ORG_A, ORG_B]
    monkeypatch.setattr(org_module, "get_device_organization", _device(ORG_B))

    assert await resolve_active_organization_id(_jwt()) == ORG_B


@pytest.mark.anyio
async def test_sole_membership_resolves_without_asking(_isolate):
    _FakeAsyncClient.memberships = [ORG_A]

    assert await resolve_active_organization_id(_jwt()) == ORG_A
    assert _isolate == [], "nothing to choose — the user should not be asked"


@pytest.mark.anyio
async def test_multi_org_with_nothing_set_holds_and_asks(_isolate):
    """THE RULING. No preference read, no personal-org fallback, no guess —
    the work is held and the user is asked."""
    _FakeAsyncClient.memberships = [ORG_A, ORG_B, ORG_PERSONAL]

    with pytest.raises(OrganizationNotResolvedError) as excinfo:
        await resolve_active_organization_id(_jwt())

    error = excinfo.value
    assert error.held is True
    assert _isolate == [USER_ID], "the picker was never asked for"
    assert "default" not in str(error).lower()
    assert "default" not in error.remedy.lower()
    assert "choose your organization" in error.remedy.lower()
    # Structural proof the deleted rungs are gone: neither endpoint was even
    # reached (the fake client raises on them).
    urls = " ".join(url for _, url in _FakeAsyncClient.calls)
    for banned in BANNED_ENDPOINTS:
        assert banned not in urls


@pytest.mark.anyio
async def test_a_stale_device_selection_is_not_used(_isolate, monkeypatch):
    """The user left that organization. Hold — never silently substitute."""
    _FakeAsyncClient.memberships = [ORG_A, ORG_B]
    monkeypatch.setattr(org_module, "get_device_organization", _device("org-gone"))

    with pytest.raises(OrganizationNotResolvedError):
        await resolve_active_organization_id(_jwt())
    assert _isolate == [USER_ID]


@pytest.mark.anyio
async def test_no_membership_at_all_is_a_different_answer(_isolate):
    """Nothing to choose FROM is not the same as nothing chosen: asking the
    user to pick from an empty list would be a screen that lies."""
    _FakeAsyncClient.memberships = []

    with pytest.raises(OrganizationNotResolvedError) as excinfo:
        await resolve_active_organization_id(_jwt())
    assert excinfo.value.held is False
    assert "added to an organization" in excinfo.value.remedy
    assert _isolate == []


@pytest.mark.anyio
async def test_a_blinking_membership_lookup_never_re_asks_an_answered_question(
    _isolate, monkeypatch
):
    """THE DOUBLE-ASK. This Mac has already been told which organization to
    work in. The membership lookup is a NETWORK call, and a background job that
    runs while Supabase is unreachable must not throw a picker at somebody who
    already chose: the SERVER is the referee on membership (it refuses an
    organization the caller is not in), so sending the set value and letting it
    verify is both safe and the only behaviour that does not re-ask.
    """

    class _Unreachable(_FakeAsyncClient):
        async def post(self, url, **kwargs):
            raise RuntimeError("connection refused")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Unreachable)
    monkeypatch.setattr(org_module, "get_device_organization", _device(ORG_B))

    assert await resolve_active_organization_id(_jwt()) == ORG_B
    assert _isolate == [], "the user was asked a question they had answered"


@pytest.mark.anyio
async def test_an_unreachable_lookup_with_nothing_set_is_NOT_held(_isolate, monkeypatch):
    """The other half: with nothing set, an unreachable lookup is a connection
    problem, not a question. Calling it HELD would put a picker in front of
    somebody whose real problem is their network."""

    class _Unreachable(_FakeAsyncClient):
        async def post(self, url, **kwargs):
            raise RuntimeError("connection refused")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Unreachable)

    with pytest.raises(OrganizationNotResolvedError) as excinfo:
        await resolve_active_organization_id(_jwt())
    assert excinfo.value.held is False
    assert "connection" in excinfo.value.remedy.lower()
    assert _isolate == []


@pytest.mark.anyio
async def test_the_deleted_rungs_are_gone_from_the_module():
    """A resolver can be rewritten to call these again; this says plainly
    that today it cannot, because they do not exist."""
    assert not hasattr(org_module, "_default_organization_id")
    assert not hasattr(org_module, "_personal_organization_id")
