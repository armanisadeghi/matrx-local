from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from app.services.cloud_sync import instance_manager
from app.services.cloud_sync.instance_manager import InstanceManager

USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ROW = "33333333-3333-4333-8333-333333333333"
ORIGIN = "https://db.matrxserver.com"
OTHER_ORIGIN = "https://other.db.matrxserver.com"
INSTANCE_ID = "inst_stable"


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setattr(instance_manager, "INSTANCE_FILE", tmp_path / "instance.json")
    value = InstanceManager()
    value._instance_id = INSTANCE_ID
    return value


def _row(**overrides):
    return {"id": ROW, "user_id": USER, "instance_id": INSTANCE_ID, **overrides}


def _grant(owner):
    async def value():
        return "opaque", owner

    return value


def _request_context(owner):
    async def value(*, expected_owner=None):
        assert expected_owner in (None, owner)
        return owner, {"Authorization": "Bearer opaque"}

    return value


def _install_response(monkeypatch, response_row, on_post=None):
    class Response:
        is_success = True
        status_code = 201
        text = ""

        def json(self):
            return [response_row]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *_args, **_kwargs):
            if on_post:
                on_post()
            return Response()

    monkeypatch.setitem(
        sys.modules, "httpx", types.SimpleNamespace(AsyncClient=lambda **_: Client())
    )


@pytest.mark.parametrize(
    "row",
    [
        _row(),
        _row(id="not-a-canonical-uuid"),
        _row(user_id=OTHER),
        _row(instance_id="inst_other"),
        {"user_id": USER, "instance_id": INSTANCE_ID},
    ],
)
def test_persisted_binding_requires_exact_canonical_row_owner_and_instance(manager, row):
    accepted = manager.accept_registration_identity(
        row, expected_origin=ORIGIN, expected_user_id=USER
    )
    assert accepted is (row == _row())
    if accepted:
        binding = manager._instance_record()["registered_device"]
        assert binding == {
            "app_instance_id": ROW,
            "registration_origin": ORIGIN,
            "user_id": USER,
            "instance_id": INSTANCE_ID,
        }
    else:
        assert "registered_device" not in manager._instance_record()


def test_identity_listeners_only_observe_real_ready_and_revocation_transitions(manager):
    observed = []
    manager.subscribe_registered_device_identity(observed.append)
    assert manager.accept_registration_identity(
        _row(), expected_origin=ORIGIN, expected_user_id=USER
    )
    # Repeated heartbeat/registration of the same durable binding is quiet.
    assert manager.accept_registration_identity(
        _row(), expected_origin=ORIGIN, expected_user_id=USER
    )
    manager.clear_registered_device_identity()
    manager.clear_registered_device_identity()
    assert [None if value is None else value.app_instance_id for value in observed] == [ROW, None]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response_row",
    [
        _row(id="not-a-canonical-uuid"),
        _row(user_id=OTHER),
        _row(instance_id="inst_other"),
        {"user_id": USER, "instance_id": INSTANCE_ID},
    ],
)
async def test_registration_response_rejects_wrong_identity(manager, monkeypatch, response_row):
    import app.services.cloud_sync.instance_manager as instances
    from app.services.cloud_sync.settings_sync import SettingsSync

    monkeypatch.setattr(instances, "get_instance_manager", lambda: manager)
    _install_response(monkeypatch, response_row)
    sync = SettingsSync()
    sync.configure(ORIGIN, "key", USER, INSTANCE_ID)
    assert manager.accept_registration_identity(
        _row(), expected_origin=ORIGIN, expected_user_id=USER
    )
    monkeypatch.setattr(sync, "_request_context", _request_context(USER))

    assert await sync.register_instance({"instance_id": INSTANCE_ID}) is None
    assert "registered_device" not in manager._instance_record()


@pytest.mark.anyio
async def test_restart_configure_offline_retains_only_exact_binding_and_accessor_rechecks_owner(
    manager, monkeypatch
):
    import app.services.cloud_sync.instance_manager as instances
    import app.services.cloud_sync.settings_sync as settings
    import app.services.sync_client as client
    from app.services.cloud_sync.settings_sync import SettingsSync

    assert manager.accept_registration_identity(
        _row(), expected_origin=ORIGIN, expected_user_id=USER
    )
    restarted = InstanceManager()
    restarted._instance_id = INSTANCE_ID
    monkeypatch.setattr(instances, "get_instance_manager", lambda: restarted)
    sync = SettingsSync()
    sync.configure(f"{ORIGIN}/", "key", USER, INSTANCE_ID)
    monkeypatch.setattr(settings, "get_settings_sync", lambda: sync)
    monkeypatch.setattr(
        client, "get_sync_client", lambda: SimpleNamespace(access_grant=_grant(USER))
    )

    assert (await restarted.registered_device_identity()).app_instance_id == ROW
    monkeypatch.setattr(
        client, "get_sync_client", lambda: SimpleNamespace(access_grant=_grant(OTHER))
    )
    assert await restarted.registered_device_identity() is None


@pytest.mark.anyio
async def test_same_owner_origin_change_during_http_cannot_accept_old_response(manager, monkeypatch):
    import app.services.cloud_sync.instance_manager as instances
    from app.services.cloud_sync.settings_sync import SettingsSync

    monkeypatch.setattr(instances, "get_instance_manager", lambda: manager)
    sync = SettingsSync()
    sync.configure(ORIGIN, "key", USER, INSTANCE_ID)
    _install_response(
        monkeypatch,
        _row(),
        on_post=lambda: sync.configure(OTHER_ORIGIN, "key", USER, INSTANCE_ID),
    )
    monkeypatch.setattr(sync, "_request_context", _request_context(USER))

    assert await sync.register_instance({"instance_id": INSTANCE_ID}) is None
    assert "registered_device" not in manager._instance_record()


@pytest.mark.anyio
async def test_late_old_account_response_cannot_persist_identity(manager, monkeypatch):
    import app.services.cloud_sync.instance_manager as instances
    from app.services.cloud_sync.settings_sync import SettingsSync

    monkeypatch.setattr(instances, "get_instance_manager", lambda: manager)
    _install_response(monkeypatch, _row())
    sync = SettingsSync()
    sync.configure(ORIGIN, "key", USER, INSTANCE_ID)
    calls = 0

    async def request_context(*, expected_owner=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return USER, {"Authorization": "Bearer opaque"}
        raise RuntimeError("sync_daemon_owner_changed")

    monkeypatch.setattr(sync, "_request_context", request_context)
    assert await sync.register_instance({"instance_id": INSTANCE_ID}) is None
    assert "registered_device" not in manager._instance_record()


@pytest.mark.anyio
async def test_cleanup_write_failure_fences_memory_before_signout_finishes(manager, monkeypatch):
    import app.services.cloud_sync.instance_manager as instances
    import app.services.cloud_sync.settings_sync as settings
    import app.services.sync_client as client
    from app.services.cloud_sync.settings_sync import SettingsSync

    monkeypatch.setattr(instances, "get_instance_manager", lambda: manager)
    sync = SettingsSync()
    sync.configure(ORIGIN, "key", USER, INSTANCE_ID)
    assert manager.accept_registration_identity(
        _row(), expected_origin=ORIGIN, expected_user_id=USER
    )
    monkeypatch.setattr(manager, "_save_instance_record", lambda _value: (_ for _ in ()).throw(OSError()))

    sync.clear_credentials()
    assert not sync.is_configured
    assert sync.expected_user_id is None
    assert sync.expected_instance_id is None
    assert sync.registration_origin is None
    monkeypatch.setattr(settings, "get_settings_sync", lambda: sync)
    monkeypatch.setattr(
        client, "get_sync_client", lambda: SimpleNamespace(access_grant=_grant(USER))
    )
    assert await manager.registered_device_identity() is None

@pytest.mark.anyio
async def test_initial_signed_in_phase7_preserves_binding_until_configure_then_accessor(
    manager, monkeypatch
):
    import app.services.ai.key_manager as key_manager
    import app.services.cloud_sync.instance_manager as instances
    import app.services.cloud_sync.settings_sync as settings
    import app.services.sync_client as sync_client
    from app.services.cloud_sync.settings_sync import SettingsSync
    from app.services.daemon_session_reconciler import DaemonSessionReconciler
    from app.services.sync_client.client import SessionSnapshot

    assert manager.accept_registration_identity(
        _row(), expected_origin=ORIGIN, expected_user_id=USER
    )
    sync = SettingsSync()

    class Client:
        async def session(self):
            return SessionSnapshot("signed_in", "", True, USER)

        async def access_grant(self):
            return "opaque", USER

    monkeypatch.setattr(instances, "get_instance_manager", lambda: manager)
    monkeypatch.setattr(settings, "get_settings_sync", lambda: sync)
    monkeypatch.setattr(sync_client, "get_sync_client", lambda: Client())
    monkeypatch.setattr(
        key_manager,
        "clear_vault_keys",
        lambda: pytest.fail("initial signed-in snapshot must not clear identity"),
    )
    reconciler = DaemonSessionReconciler()

    async def adopt(*_args):
        return None

    monkeypatch.setattr(reconciler, "_adopt", adopt)
    await reconciler.reconcile()
    await reconciler._adoption
    sync.configure(f"{ORIGIN}/", "key", USER, INSTANCE_ID)

    assert (await manager.registered_device_identity()).app_instance_id == ROW


@pytest.mark.anyio
async def test_initial_signed_out_then_signin_fences_persisted_binding(manager, monkeypatch):
    import app.services.cloud_sync.instance_manager as instances
    import app.services.cloud_sync.settings_sync as settings
    import app.services.sync_client as sync_client
    from app.services.cloud_sync.settings_sync import SettingsSync
    from app.services.daemon_session_reconciler import DaemonSessionReconciler
    from app.services.sync_client.client import SessionSnapshot

    assert manager.accept_registration_identity(
        _row(), expected_origin=ORIGIN, expected_user_id=USER
    )
    sync = SettingsSync()
    sync.configure(ORIGIN, "key", USER, INSTANCE_ID)
    state = {"user": None}

    class Client:
        async def session(self):
            user = state["user"]
            return SessionSnapshot("signed_in" if user else "signed_out", "", bool(user), user)

    monkeypatch.setattr(instances, "get_instance_manager", lambda: manager)
    monkeypatch.setattr(settings, "get_settings_sync", lambda: sync)
    monkeypatch.setattr(sync_client, "get_sync_client", lambda: Client())
    reconciler = DaemonSessionReconciler()

    async def noop(*_args):
        return None

    monkeypatch.setattr(reconciler, "_adopt", noop)
    monkeypatch.setattr(reconciler, "_reconcile_home_connection", noop)
    await reconciler.reconcile()
    assert not sync.is_configured
    assert "registered_device" not in manager._instance_record()
    state["user"] = USER
    await reconciler.reconcile()
    await reconciler._adoption
    assert "registered_device" not in manager._instance_record()


@pytest.mark.anyio
async def test_switch_continues_when_identity_cleanup_write_fails(manager, monkeypatch):
    import app.api.extension_broadcast as broadcast
    import app.services.cloud_sync.instance_manager as instances
    import app.services.cloud_sync.settings_sync as settings
    import app.services.sync_client as sync_client
    from app.services.cloud_sync.settings_sync import SettingsSync
    from app.services.daemon_session_reconciler import DaemonSessionReconciler
    from app.services.sync_client.client import SessionSnapshot

    assert manager.accept_registration_identity(
        _row(), expected_origin=ORIGIN, expected_user_id=USER
    )
    sync = SettingsSync()
    sync.configure(ORIGIN, "key", USER, INSTANCE_ID)
    manager._save_instance_record = lambda _value: (_ for _ in ()).throw(OSError())
    disconnected = []

    class Client:
        async def session(self):
            return SessionSnapshot("signed_in", "", True, OTHER)

    async def disconnect(user):
        disconnected.append(user)

    monkeypatch.setattr(instances, "get_instance_manager", lambda: manager)
    monkeypatch.setattr(settings, "get_settings_sync", lambda: sync)
    monkeypatch.setattr(sync_client, "get_sync_client", lambda: Client())
    monkeypatch.setattr(broadcast, "disconnect_broadcast", disconnect)
    reconciler = DaemonSessionReconciler()
    reconciler._session_observed = True
    reconciler._user_id = USER

    async def noop(*_args):
        return None

    monkeypatch.setattr(reconciler, "_adopt", noop)
    await reconciler.reconcile()
    await reconciler._adoption

    assert reconciler._user_id == OTHER
    assert disconnected == [USER]
    assert not sync.is_configured
