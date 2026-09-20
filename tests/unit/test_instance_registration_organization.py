"""Forcing guards for organization-bound app_instances registration."""

from __future__ import annotations

import sys
import types

import pytest

from app.services.cloud_sync.settings_sync import SettingsSync
from app.services.cloud_sync import instance_manager
from app.services.cloud_sync.instance_manager import InstanceManager


USER = "11111111-1111-4111-8111-111111111111"
ORG_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORG_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
INSTANCE = "instance-1"


def _sync() -> SettingsSync:
    sync = SettingsSync()
    sync.configure("https://db.matrxserver.com", "key", USER, INSTANCE)
    return sync


def _response(monkeypatch, payloads, *, organization_id=ORG_A):
    class Response:
        is_success = True
        status_code = 201
        text = ""

        def json(self):
            return [{"id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "user_id": USER, "instance_id": INSTANCE, "organization_id": organization_id}]

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

    calls = 0

    async def selected(token):
        nonlocal calls
        calls += 1
        assert token == "daemon-token"
        return ORG_A

    monkeypatch.setattr(sync_client, "get_sync_client", lambda: types.SimpleNamespace(access_grant=grant))
    monkeypatch.setattr(organization, "resolve_active_organization_id", selected)

    owner, headers, organization_id = await sync._registration_context(expected_owner=USER)
    assert owner == USER
    assert organization_id == ORG_A
    assert headers["X-Organization-Id"] == ORG_A
    assert calls == 2


@pytest.mark.anyio
async def test_registration_writes_device_selected_organization_not_caller_data(monkeypatch, tmp_path):
    sync = _sync()
    sent = []
    _response(monkeypatch, sent)

    async def context(*, expected_owner=None):
        assert expected_owner in (None, USER)
        return USER, {"Authorization": "Bearer daemon", "X-Organization-Id": ORG_A}, ORG_A

    monkeypatch.setattr(sync, "_registration_context", context)
    manager = InstanceManager()
    manager._instance_id = INSTANCE
    monkeypatch.setattr(instance_manager, "INSTANCE_FILE", tmp_path / "instance.json")
    monkeypatch.setattr(
        "app.services.cloud_sync.instance_manager.get_instance_manager",
        lambda: manager,
    )

    assert await sync.register_instance({"instance_id": INSTANCE}) is not None
    assert sent[0][0]["organization_id"] == ORG_A
    assert sent[0][1]["X-Organization-Id"] == ORG_A
    assert manager._instance_record()["registered_device"]["user_id"] == USER


@pytest.mark.anyio
async def test_registration_refuses_a_caller_supplied_organization_before_http(monkeypatch):
    sync = _sync()
    async def context(*, expected_owner=None):
        return USER, {"Authorization": "Bearer daemon", "X-Organization-Id": ORG_A}, ORG_A

    monkeypatch.setattr(sync, "_registration_context", context)

    assert await sync.register_instance({"instance_id": INSTANCE, "organization_id": ORG_B}) is None
    assert sync.get_debug_state()["last_registration_result"] == "error:registration_identity_spoof"


@pytest.mark.anyio
async def test_registration_refuses_a_caller_supplied_user_before_http(monkeypatch):
    sync = _sync()

    async def context(*, expected_owner=None):
        return USER, {"Authorization": "Bearer daemon", "X-Organization-Id": ORG_A}, ORG_A

    monkeypatch.setattr(sync, "_registration_context", context)
    assert await sync.register_instance({"instance_id": INSTANCE, "user_id": ORG_B}) is None


@pytest.mark.anyio
async def test_configured_owner_a_with_current_daemon_b_never_posts(monkeypatch):
    import app.services.aidream.organization as organization
    import app.services.sync_client as sync_client

    sync = _sync()

    async def grant():
        return "daemon-b", ORG_B

    async def selected(_token):
        return ORG_A

    monkeypatch.setattr(sync_client, "get_sync_client", lambda: types.SimpleNamespace(access_grant=grant))
    monkeypatch.setattr(organization, "resolve_active_organization_id", selected)
    assert await sync.register_instance({"instance_id": INSTANCE}) is None
    assert sync.is_orphan


@pytest.mark.anyio
async def test_owner_switch_during_organization_resolution_never_posts(monkeypatch):
    import app.services.aidream.organization as organization
    import app.services.sync_client as sync_client

    sync = _sync()
    grants = iter([("daemon-a", USER), ("daemon-b", ORG_B)])

    async def grant():
        return next(grants)

    async def selected(_token):
        return ORG_A

    monkeypatch.setattr(sync_client, "get_sync_client", lambda: types.SimpleNamespace(access_grant=grant))
    monkeypatch.setattr(organization, "resolve_active_organization_id", selected)
    assert await sync.register_instance({"instance_id": INSTANCE}) is None
    assert sync.is_orphan


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


@pytest.mark.anyio
async def test_registration_refuses_late_device_switch_during_final_daemon_grant(
    monkeypatch, tmp_path
):
    """The final grant await must not accept an already-resolved old org.

    This switches the actual selected-device state after both post-response
    organization resolutions have returned A, precisely while the sixth and
    final daemon-grant await is in flight.  The pre-generation-fence code
    persisted the server's A row here.
    """
    import app.services.aidream.organization as organization
    import app.services.sync_client as sync_client
    from app.services.local_db import repositories

    class SettingsRepo:
        values: dict[str, object] = {}

        async def get(self, key, default=None):
            return type(self).values.get(key, default)

        async def set(self, key, value):
            type(self).values[key] = value

    sync = _sync()
    sent = []
    manager = InstanceManager()
    manager._instance_id = INSTANCE
    monkeypatch.setattr(instance_manager, "INSTANCE_FILE", tmp_path / "instance.json")
    monkeypatch.setattr(
        "app.services.cloud_sync.instance_manager.get_instance_manager", lambda: manager
    )
    monkeypatch.setattr(repositories, "AppSettingsRepo", SettingsRepo)

    async def _none():
        return None

    monkeypatch.setattr(organization, "_clear_hold", lambda: _none())
    organization.invalidate_organization_cache()
    await organization.set_device_organization(ORG_A, user_id=USER)
    _response(monkeypatch, sent)

    calls = 0
    switched = False

    async def grant():
        nonlocal calls, switched
        calls += 1
        if calls == 6:
            await organization.set_device_organization(ORG_B, user_id=USER)
            switched = True
        return "daemon-token", USER

    async def selected(_token):
        return ORG_A

    monkeypatch.setattr(sync_client, "get_sync_client", lambda: types.SimpleNamespace(access_grant=grant))
    monkeypatch.setattr(organization, "resolve_active_organization_id", selected)

    assert await sync.register_instance({"instance_id": INSTANCE}) is None
    assert switched is True
    assert len(sent) == 1
    assert "registered_device" not in manager._instance_record()


@pytest.mark.anyio
async def test_wrong_response_organization_cannot_persist_registered_device(monkeypatch, tmp_path):
    sync = _sync()
    sent = []
    _response(monkeypatch, sent, organization_id=ORG_B)
    manager = InstanceManager()
    manager._instance_id = INSTANCE
    monkeypatch.setattr(instance_manager, "INSTANCE_FILE", tmp_path / "instance.json")
    monkeypatch.setattr("app.services.cloud_sync.instance_manager.get_instance_manager", lambda: manager)

    async def context(*, expected_owner=None):
        return USER, {"Authorization": "Bearer daemon", "X-Organization-Id": ORG_A}, ORG_A

    monkeypatch.setattr(sync, "_registration_context", context)
    assert await sync.register_instance({"instance_id": INSTANCE}) is None
    assert "registered_device" not in manager._instance_record()
