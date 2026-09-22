"""``PUT /organization/active`` — THE ONE CHOICE — also answers the server's
coding-session filing question (Arman, 2026-09-21: one organization per Mac,
one selector, every API uses it and never skips it).

Before this, the top-bar choice set this Mac's organization and the
coding-session lane asked AGAIN, in different words, which organization to
file sessions in. The route now writes the same value through the server's
door, best-effort: the desktop's choice stands whether or not the server
could be reached.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api import organization_routes
from app.services.aidream import organization as org_module

USER_A = "11111111-1111-4111-8111-111111111111"
ORG = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class _FakeAppSettingsRepo:
    _store: dict[str, object] = {}

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def get(self, key: str, default=None):
        return type(self)._store.get(key, default)

    async def set(self, key: str, value) -> None:
        type(self)._store[key] = value


class _FakeOutbox:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.filed: list[str] = []

    async def set_connection_organization(self, organization_id: str) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("server unreachable")
        self.filed.append(organization_id)
        return {"organization_id": organization_id, "blocker": None}


@pytest.fixture(autouse=True)
def _fakes(monkeypatch: pytest.MonkeyPatch):
    _FakeAppSettingsRepo._store = {}
    monkeypatch.setattr(
        "app.services.local_db.repositories.AppSettingsRepo", _FakeAppSettingsRepo
    )
    org_module.invalidate_organization_cache()

    async def memberships(_jwt: str) -> list[dict[str, Any]]:
        return [{"container_id": ORG, "status": "active"}]

    monkeypatch.setattr(organization_routes, "_active_memberships", memberships)
    monkeypatch.setattr(organization_routes, "jwt_user_id", lambda _jwt: USER_A)
    yield
    _FakeAppSettingsRepo._store = {}


def _install_outbox(monkeypatch: pytest.MonkeyPatch, outbox: _FakeOutbox) -> None:
    import app.services.coding_sessions.service as service_module

    monkeypatch.setattr(
        service_module, "get_coding_session_bridge_outbox", lambda: outbox
    )


@pytest.mark.anyio
async def test_setting_the_organization_files_coding_sessions_in_it_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox = _FakeOutbox()
    _install_outbox(monkeypatch, outbox)

    result = await organization_routes.write_active_organization(
        organization_routes.SetActiveOrganization(organization_id=ORG),
        authorization="Bearer jwt",
    )

    assert result.organization_id == ORG
    assert await org_module.get_device_organization(USER_A) == ORG
    assert outbox.filed == [ORG], "the same value reached the server's coding-session door"


@pytest.mark.anyio
async def test_the_choice_stands_when_the_server_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_outbox(monkeypatch, _FakeOutbox(fail=True))

    result = await organization_routes.write_active_organization(
        organization_routes.SetActiveOrganization(organization_id=ORG),
        authorization="Bearer jwt",
    )

    # The device value is THE value; the server mirror is best-effort and the
    # publisher answers the server's hold from it when delivery next runs.
    assert result.organization_id == ORG
    assert await org_module.get_device_organization(USER_A) == ORG
