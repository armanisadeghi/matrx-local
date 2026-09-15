from __future__ import annotations
import pytest
from app.services.daemon_session_reconciler import DaemonSessionReconciler
from app.services.sync_client.client import SessionSnapshot


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import app.services.local_db.sync_engine as sync_engine

    monkeypatch.setattr(
        sync_engine, "get_sync_engine", lambda: SimpleNamespace(sync_agents=AsyncMock())
    )


@pytest.mark.anyio
async def test_transitions_and_retry(monkeypatch):
    import app.services.sync_client as sc
    import app.services.scraper.scrape_store as ss
    import app.services.ai.key_manager as km
    import app.services.ai.engine as ae
    import app.api.extension_broadcast as eb

    state = {"user": None, "fail": False}
    calls = []

    class Client:
        async def session(self):
            u = state["user"]
            return SessionSnapshot(
                state="signed_in" if u else "signed_out",
                state_reason="",
                signed_in=bool(u),
                user_id=u,
            )

        async def user_id(self):
            return state["user"]

    async def scrape():
        calls.append(("scrape", state["user"]))
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("transient")

    async def vault():
        calls.append(("vault", state["user"]))

    async def tools():
        calls.append(("tools", state["user"]))

    async def connect(u):
        calls.append(("connect", u))

    async def disconnect(u):
        calls.append(("disconnect", u))

    monkeypatch.setattr(sc, "get_sync_client", lambda: Client())
    monkeypatch.setattr(ss, "sync_after_sign_in", scrape)
    monkeypatch.setattr(km, "refresh_vault_keys", vault)
    monkeypatch.setattr(ae, "refresh_server_tool_definitions", tools)
    monkeypatch.setattr(eb, "connect_broadcast", connect)
    monkeypatch.setattr(eb, "disconnect_broadcast", disconnect)
    monkeypatch.setattr(km, "clear_vault_keys", lambda: calls.append(("clear", None)))
    import app.config

    monkeypatch.setattr(app.config, "CLOUD_PARTICIPATION_ENABLED", True)
    r = DaemonSessionReconciler()
    await r.reconcile()
    assert calls == []
    state["user"] = "A"
    await r.reconcile()
    await r._adoption
    assert ("connect", "A") in calls
    before = len(calls)
    await r.reconcile()
    assert len(calls) == before
    await r.reconcile(rotated=True)
    await r._adoption
    assert len(calls) > before
    state["user"] = "B"
    await r.reconcile()
    await r._adoption
    assert (
        ("clear", None) in calls
        and ("disconnect", "A") in calls
        and ("connect", "B") in calls
    )
    state["user"] = None
    await r.reconcile()
    state["user"] = "A"
    state["fail"] = True
    before = len([c for c in calls if c == ("scrape", "A")])
    await r.reconcile()
    import asyncio

    await asyncio.wait_for(r._adoption, timeout=4)
    assert len([c for c in calls if c == ("scrape", "A")]) == before + 2
    assert r._completed


@pytest.mark.anyio
async def test_vault_response_for_retired_owner_is_not_applied(monkeypatch):
    from types import SimpleNamespace
    from app.services.ai import key_manager as km
    from app.services.credential_vault import provider_keys
    from app.services import sync_client

    owner = {"id": "A"}

    class Client:
        async def user_id(self):
            return owner["id"]

    async def fetch(**kwargs):
        owner["id"] = "B"
        return SimpleNamespace(ok=True, values={"anthropic": "test-secret"})

    monkeypatch.setattr(sync_client, "get_sync_client", lambda: Client())
    monkeypatch.setattr(provider_keys, "fetch_provider_snapshot", fetch)
    monkeypatch.setattr(km, "_user_keys_loaded", True)
    monkeypatch.setattr(km, "_vault_keys", {})
    await km.refresh_vault_keys()
    assert km.get_vault_keys() == {}


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["grant", "subscribe"])
async def test_broadcast_recovers_without_another_session_event(monkeypatch, failure):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import supabase
    import app.config
    import app.api.extension_broadcast as eb
    import app.services.sync_client as sc
    import app.services.scraper.scrape_store as ss
    import app.services.ai.key_manager as km
    import app.services.ai.engine as ae

    attempts = {"grant": 0, "subscribe": 0}

    class Daemon:
        async def session(self):
            return SessionSnapshot(
                state="signed_in", state_reason="", signed_in=True, user_id="A"
            )

        async def user_id(self):
            return "A"

        async def access_grant(self):
            attempts["grant"] += 1
            return (
                None
                if failure == "grant" and attempts["grant"] == 1
                else ("current-access", "A")
            )

    class Channel:
        def on_broadcast(self, **kwargs):
            pass

        async def subscribe(self):
            attempts["subscribe"] += 1
            if failure == "subscribe" and attempts["subscribe"] == 1:
                raise RuntimeError("temporary subscription failure")

        async def unsubscribe(self):
            pass

    realtime = SimpleNamespace(set_auth=AsyncMock(), disconnect=AsyncMock())
    client = SimpleNamespace(realtime=realtime, channel=lambda name: Channel())
    monkeypatch.setattr(sc, "get_sync_client", Daemon)
    monkeypatch.setattr(supabase, "create_async_client", AsyncMock(return_value=client))
    monkeypatch.setattr(app.config, "CLOUD_PARTICIPATION_ENABLED", True)
    monkeypatch.setattr(eb, "is_broadcast_enabled", lambda: True)
    monkeypatch.setattr(eb, "_channels", {})
    monkeypatch.setattr(eb, "_clients", {})
    monkeypatch.setattr(ss, "sync_after_sign_in", AsyncMock())
    monkeypatch.setattr(km, "refresh_vault_keys", AsyncMock())
    monkeypatch.setattr(ae, "refresh_server_tool_definitions", AsyncMock())
    r = DaemonSessionReconciler()
    await r.reconcile()
    await asyncio.wait_for(r._adoption, timeout=5)
    assert r._completed
    assert attempts["grant"] == 2
    assert "A" in eb._channels
    realtime.set_auth.assert_called_with("current-access")
    if failure == "subscribe":
        realtime.disconnect.assert_awaited_once()
    await r.stop()
