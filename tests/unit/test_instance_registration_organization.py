"""Forcing guards for organization-bound app_instances registration."""

from __future__ import annotations

import sys
import types

import pytest

from app.services.cloud_sync.settings_sync import SettingsSync


USER = "11111111-1111-4111-8111-111111111111"
ORG_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORG_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
INSTANCE = "instance-1"


def _sync() -> SettingsSync:
    sync = SettingsSync()
    sync.configure("https://db.matrxserver.com", "key", USER, INSTANCE)
    return sync


def _response(monkeypatch, payloads):
    class Response:
        is_success = True
        status_code = 201
        text = ""

        def json(self):
            return [{"id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "user_id": USER, "instance_id": INSTANCE}]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, _url, *, json, headers):
            payloads.append((json, headers))
            return Response()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=lambda **_: Client()))


@pytest.mark.anyio
async def test_registration_context_uses_current_daemon_grant_and_device_organization(monkeypatch):
    import app.services.aidream.organization as organization
    import app.services.sync_client as sync_client

    sync = _sync()

    async def grant():
        return "daemon-token", USER

    async def selected(token):
        assert token == "daemon-token"
        return ORG_A

    monkeypatch.setattr(sync_client, "get_sync_client", lambda: types.SimpleNamespace(access_grant=grant))
    monkeypatch.setattr(organization, "resolve_active_organization_id", selected)

    owner, headers, organization_id = await sync._registration_context(expected_owner=USER)
    assert owner == USER
    assert organization_id == ORG_A
    assert headers["X-Organization-Id"] == ORG_A


@pytest.mark.anyio
async def test_registration_writes_device_selected_organization_not_caller_data(monkeypatch):
    sync = _sync()
    sent = []
    _response(monkeypatch, sent)

    async def context(*, expected_owner=None):
        assert expected_owner in (None, USER)
        return USER, {"Authorization": "Bearer daemon", "X-Organization-Id": ORG_A}, ORG_A

    monkeypatch.setattr(sync, "_registration_context", context)
    # Avoid this test depending on durable device-identity persistence.
    monkeypatch.setattr(
        "app.services.cloud_sync.instance_manager.get_instance_manager",
        lambda: types.SimpleNamespace(accept_registration_identity=lambda *_a, **_k: True),
    )

    assert await sync.register_instance({"instance_id": INSTANCE}) is not None
    assert sent[0][0]["organization_id"] == ORG_A
    assert sent[0][1]["X-Organization-Id"] == ORG_A


@pytest.mark.anyio
async def test_registration_refuses_a_caller_supplied_organization_before_http(monkeypatch):
    sync = _sync()
    async def context(*, expected_owner=None):
        return USER, {"Authorization": "Bearer daemon", "X-Organization-Id": ORG_A}, ORG_A

    monkeypatch.setattr(sync, "_registration_context", context)

    assert await sync.register_instance({"instance_id": INSTANCE, "organization_id": ORG_B}) is None
    assert sync.get_debug_state()["last_registration_result"] == "error:registration_org_spoof"


@pytest.mark.anyio
async def test_registration_without_a_resolved_organization_never_posts(monkeypatch):
    from app.services.aidream.organization import OrganizationNotResolvedError

    sync = _sync()

    async def context(*, expected_owner=None):
        raise OrganizationNotResolvedError("This Mac has no organization chosen yet.", held=True)

    monkeypatch.setattr(sync, "_registration_context", context)
    assert await sync.register_instance({"instance_id": INSTANCE}) is None
    assert sync.is_orphan


@pytest.mark.anyio
async def test_registration_discards_response_when_device_organization_changes_during_http(monkeypatch):
    sync = _sync()
    sent = []
    _response(monkeypatch, sent)
    calls = 0

    async def context(*, expected_owner=None):
        nonlocal calls
        calls += 1
        return USER, {"Authorization": "Bearer daemon", "X-Organization-Id": ORG_A if calls == 1 else ORG_B}, ORG_A if calls == 1 else ORG_B

    monkeypatch.setattr(sync, "_registration_context", context)

    assert await sync.register_instance({"instance_id": INSTANCE}) is None
    assert len(sent) == 1
