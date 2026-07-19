"""Unit tests for fleet version + remote-config provenance reporting.

`public.app_config.min_supported_app_version` gates shipped clients, but the
gate is blind unless every instance records the version it is actually
RUNNING. These tests pin the two writes that carry it:

  * registration  → `app_instances.app_version` (every startup / re-configure)
  * heartbeat     → merged `app_instances.metadata` provenance, but ONLY when
                    the provenance content changed (no write amplification)

No network: httpx is stubbed at the module seam the code imports lazily.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.services.cloud_sync.instance_manager import (
    PROVENANCE_CONTENT_KEYS,
    build_config_provenance,
    current_app_version,
    get_instance_manager,
)
from app.services.cloud_sync.settings_sync import SettingsSync


# ---------------------------------------------------------------------------
# httpx stub — captures the request bodies the engine would send
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int = 200, json_body=None):
        self.status_code = status_code
        self._json = json_body if json_body is not None else []
        self.text = "stub"

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return self._json


class _Client:
    def __init__(self, calls: list, resp: _Resp):
        self._calls = calls
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self._calls.append(("POST", url, json))
        return self._resp

    async def patch(self, url, json=None, headers=None):
        self._calls.append(("PATCH", url, json))
        return self._resp


class _Http:
    """Recorder for the requests the engine would have sent."""

    def __init__(self) -> None:
        self.calls: list = []
        self.resp = _Resp()

    def set_response(self, resp: _Resp) -> None:
        self.resp = resp

    def bodies(self, method: str) -> list[dict]:
        return [b for m, _u, b in self.calls if m == method and b is not None]


@pytest.fixture
def http(monkeypatch):
    """Install a fake `httpx` module; yields the request recorder."""
    rec = _Http()

    fake = types.ModuleType("httpx")
    fake.AsyncClient = lambda *a, **kw: _Client(rec.calls, rec.resp)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", fake)
    return rec


def _configured_sync() -> SettingsSync:
    sync = SettingsSync()
    sync._supabase_url = "https://db.example.test"
    sync._supabase_key = "pk_test"
    sync._jwt = "jwt_test"
    sync._user_id = "user-1"
    sync._instance_id = "inst_test"
    sync._configured = True
    return sync


# ---------------------------------------------------------------------------
# app_version
# ---------------------------------------------------------------------------


def test_registration_payload_carries_running_app_version():
    payload = get_instance_manager().get_registration_payload()
    assert payload["app_version"] == current_app_version()
    assert payload["app_version"], "app_version must never be an empty string"


def test_current_app_version_is_the_one_resolver():
    from app.api.routes import _APP_VERSION

    assert current_app_version() == str(_APP_VERSION)


def test_register_instance_sends_app_version(http):
    http.set_response(_Resp(200, [{"instance_id": "inst_test", "metadata": {}}]))
    sync = _configured_sync()

    asyncio.run(
        sync.register_instance(get_instance_manager().get_registration_payload())
    )

    body = http.bodies("POST")[0]
    assert body["app_version"] == current_app_version()


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def test_build_config_provenance_shape():
    p = build_config_provenance()
    assert set(PROVENANCE_CONTENT_KEYS) <= set(p)
    assert "provenance_reported_at" in p
    # Resolved from the live services — tier is a real tier, not a placeholder.
    assert p["app_config_tier"] in {"remote", "cache", "defaults", "env", None}
    assert p["catalogs_tier"] in {"remote", "cache", "defaults", None}


def test_build_config_provenance_never_raises(monkeypatch):
    import app.services.app_config as app_config_pkg

    def boom():
        raise RuntimeError("service exploded")

    monkeypatch.setattr(app_config_pkg, "get_app_config", boom)
    p = build_config_provenance()
    assert p["app_config_tier"] is None
    assert "provenance_reported_at" in p


def test_heartbeat_merges_provenance_without_clobbering(http):
    """Keys written by other writers survive; ours are added alongside."""
    http.set_response(
        _Resp(200, [{"instance_id": "inst_test", "metadata": {"owned_by_web": "keep"}}])
    )
    sync = _configured_sync()
    asyncio.run(sync.register_instance({"instance_id": "inst_test"}))

    asyncio.run(sync.heartbeat())

    body = http.bodies("PATCH")[0]
    assert body["app_version"] == current_app_version()
    meta = body["metadata"]
    assert meta["owned_by_web"] == "keep"  # not clobbered
    for key in PROVENANCE_CONTENT_KEYS:
        assert key in meta
    assert meta["provenance_reported_at"]


def test_heartbeat_does_not_rewrite_unchanged_provenance(http):
    """No write amplification: the second heartbeat carries no metadata."""
    http.set_response(_Resp(200, [{"instance_id": "inst_test", "metadata": {}}]))
    sync = _configured_sync()
    asyncio.run(sync.register_instance({"instance_id": "inst_test"}))

    asyncio.run(sync.heartbeat())
    asyncio.run(sync.heartbeat())

    patches = http.bodies("PATCH")
    assert "metadata" in patches[0]
    assert "metadata" not in patches[1]
    assert "app_version" not in patches[1]
    assert "last_seen" in patches[1]  # the heartbeat itself is unaffected


def test_failed_heartbeat_retries_provenance(http):
    """A non-2xx must not mark the provenance write as landed."""
    http.set_response(_Resp(200, [{"instance_id": "inst_test", "metadata": {}}]))
    sync = _configured_sync()
    asyncio.run(sync.register_instance({"instance_id": "inst_test"}))

    http.set_response(_Resp(500))
    asyncio.run(sync.heartbeat())

    http.set_response(_Resp(200))
    asyncio.run(sync.heartbeat())

    patches = http.bodies("PATCH")
    assert "metadata" in patches[0]
    assert "metadata" in patches[1], "provenance must be retried after a failure"


def test_dry_run_exposes_the_payload_without_a_session():
    """A sessionless engine can still prove what it reports to the fleet."""
    sync = SettingsSync()
    assert not sync.is_configured

    body = sync.dry_run_instance_write()

    assert body["registration"]["app_version"] == current_app_version()
    hb = body["heartbeat_provenance"]
    assert hb["app_version"] == current_app_version()
    for key in PROVENANCE_CONTENT_KEYS:
        assert key in hb["metadata"]


def test_debug_state_reports_version_and_provenance():
    state = SettingsSync().get_debug_state()
    assert state["app_version"] == current_app_version()
    assert state["config_provenance"] is None  # nothing written yet
    assert state["config_provenance_pending"] is False


def test_provenance_never_raises_into_heartbeat(monkeypatch, http):
    import app.services.cloud_sync.instance_manager as im

    monkeypatch.setattr(
        im, "build_config_provenance", lambda: (_ for _ in ()).throw(RuntimeError("x"))
    )
    http.set_response(_Resp(200))
    sync = _configured_sync()

    asyncio.run(sync.heartbeat())  # must not raise

    body = http.bodies("PATCH")[0]
    assert "last_seen" in body
    assert "metadata" not in body
