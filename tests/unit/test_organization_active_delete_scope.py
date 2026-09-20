"""``DELETE /organization/active`` must be bearer-scoped like GET/PUT.

THE DEFECT THIS CLOSES

    The route took no bearer at all. On a Mac where more than one account
    signs in to Matrx Local, ANY local caller of the route could wipe
    whichever user's SET organization happened to be stored — including a
    different, currently-signed-in user's choice. ``clear_device_organization``
    is the ONE writer of the device-organization setting
    (``app.services.aidream.organization``), so this is where the scoping
    lives: a caller may only clear the pick if it is their own.

These tests exercise the service function directly (the same function the
route calls) against a fake settings store, and the route's auth gate
directly against ``organization_routes``. A test that only checked "some
bearer was required" without proving cross-user isolation would miss the
actual defect — the old code technically ran under an authenticated caller
too; it just never looked at WHO.
"""

from __future__ import annotations

import pytest

from app.services.aidream import organization as org_module

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"


class _FakeAppSettingsRepo:
    """In-memory stand-in for AppSettingsRepo, scoped to one test's store."""

    _store: dict[str, object] = {}

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def get(self, key: str, default=None):
        return type(self)._store.get(key, default)

    async def set(self, key: str, value) -> None:
        type(self)._store[key] = value


@pytest.fixture(autouse=True)
def _fake_repo(monkeypatch: pytest.MonkeyPatch):
    _FakeAppSettingsRepo._store = {}
    monkeypatch.setattr(
        "app.services.local_db.repositories.AppSettingsRepo", _FakeAppSettingsRepo
    )
    org_module.invalidate_organization_cache()
    yield
    _FakeAppSettingsRepo._store = {}


@pytest.mark.anyio
async def test_clear_device_organization_wipes_the_callers_own_choice():
    await org_module.set_device_organization("org-1", user_id=USER_A)

    await org_module.clear_device_organization(user_id=USER_A)

    assert await org_module.get_device_organization(USER_A) is None


@pytest.mark.anyio
async def test_clear_device_organization_never_wipes_a_different_users_choice():
    # THE FORCING CASE: before this fix, clear_device_organization() took no
    # user_id at all and unconditionally wiped the stored setting — so this
    # is exactly the call the old signature made impossible to guard.
    await org_module.set_device_organization("org-1", user_id=USER_A)

    await org_module.clear_device_organization(user_id=USER_B)

    # USER_A's choice survives a DELETE made under USER_B's identity.
    assert await org_module.get_device_organization(USER_A) == "org-1"


@pytest.mark.anyio
async def test_clear_device_organization_is_a_noop_when_nothing_is_stored():
    await org_module.clear_device_organization(user_id=USER_A)
    assert await org_module.get_device_organization(USER_A) is None


@pytest.mark.anyio
async def test_set_and_successful_clear_advance_the_device_selection_generation():
    """Awaiting callers can fence the actual device selection mutation."""
    before = org_module.device_organization_generation()

    await org_module.set_device_organization("org-1", user_id=USER_A)
    after_set = org_module.device_organization_generation()
    await org_module.clear_device_organization(user_id=USER_A)

    assert after_set == before + 1
    assert org_module.device_organization_generation() == after_set + 1


def test_forget_active_organization_route_requires_a_bearer():
    """The route's own auth gate — same shape as GET/PUT `_bearer()`."""
    from fastapi import HTTPException

    from app.api.organization_routes import _bearer

    with pytest.raises(HTTPException) as exc_info:
        _bearer(None)
    assert exc_info.value.status_code == 401
