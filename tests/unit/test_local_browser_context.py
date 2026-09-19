"""Race and lifecycle tests for the direct local-browser context route."""
from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from fastapi import FastAPI

from app.api import local_browser_context as subject
from app.api.auth import AuthMiddleware
from app.api.remote_auth import VerifiedUser
from app.services.local_browser_context import LocalBrowserContext

USER = "11111111-1111-4111-8111-111111111111"
OTHER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SESSION_A = "22222222-2222-4222-8222-222222222222"
SESSION_B = "33333333-3333-4333-8333-333333333333"
ORG_A = "44444444-4444-4444-8444-444444444444"
ORG_B = "55555555-5555-4555-8555-555555555555"


def token(user: str = USER, session: str = SESSION_A) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sub": user, "session_id": session}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def write_body(context: dict, organization_id: str | None) -> dict:
    return {"engine_boot_id": context["engine_boot_id"], "expected_revision": context["revision"], "organization_id": organization_id}


@pytest.fixture
def route(monkeypatch):
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(subject.router)
    state = {"grant": (token(), USER), "memberships": [{"container_id": ORG_A}], "context": LocalBrowserContext("engine-a")}

    async def verified(value: str):
        claims = subject._claim(value)
        return VerifiedUser(user_id=claims[0], email=None, is_anon=False)

    class Sync:
        async def access_grant(self): return state["grant"]

    async def memberships(_jwt: str):
        value = state["memberships"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(subject, "verify_supabase_token", verified)
    monkeypatch.setattr(subject, "get_sync_client", lambda: Sync())
    monkeypatch.setattr(subject, "get_local_browser_context", lambda: state["context"])
    monkeypatch.setattr(subject, "_active_memberships", memberships)
    return app, state


def client(app, host="127.0.0.1"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 43210)), base_url="http://engine.test", headers={"Authorization": f"Bearer {token()}"})


@pytest.mark.anyio
async def test_direct_loopback_owner_and_closed_body(route):
    app, _state = route
    async with client(app) as http:
        missing = await http.get("/local-browser/context", headers={"Authorization": ""})
        assert missing.status_code == 401
        assert missing.headers["cache-control"] == "no-store"
        initial = await http.get("/local-browser/context")
        assert initial.status_code == 200
        assert initial.headers["cache-control"] == "no-store"
        assert set(initial.json()) == {"engine_boot_id", "revision", "organization_id"}
        assert (await http.post("/local-browser/context", json={**write_body(initial.json(), None), "extra": True})).status_code == 422
        assert (await http.post("/local-browser/context", json={**write_body(initial.json(), None), "expected_revision": True})).status_code == 422


@pytest.mark.anyio
async def test_forged_caller_cannot_clear_current_context(route):
    app, state = route
    async with client(app) as http:
        initial = (await http.get("/local-browser/context")).json()
        assert (await http.post("/local-browser/context", json=write_body(initial, ORG_A))).status_code == 200
        assert (await http.get("/local-browser/context", headers={"Authorization": f"Bearer {token(session=SESSION_B)}"})).status_code == 401
        valid = await http.get("/local-browser/context")
        assert valid.json()["organization_id"] == ORG_A
        assert (await http.get("/local-browser/context", headers={"cf-ray": "forged"})).status_code in {401, 403}
        assert state["grant"] == (token(), USER)


@pytest.mark.anyio
async def test_daemon_loss_account_and_same_user_relogin_retire_before_authenticated_get(route):
    app, state = route
    async with client(app) as http:
        initial = (await http.get("/local-browser/context")).json()
        assert (await http.post("/local-browser/context", json=write_body(initial, ORG_A))).status_code == 200
        state["grant"] = None
        assert (await http.get("/local-browser/context")).status_code == 401
        assert (await state["context"].fresh_for_daemon_grant(None)) is None
        state["grant"] = (token(user=OTHER), OTHER)
        assert (await http.get("/local-browser/context", headers={"Authorization": f"Bearer {token(user=OTHER)}"})).json()["organization_id"] is None
        state["grant"] = (token(), USER)
        old = (await http.get("/local-browser/context")).json()
        assert (await http.post("/local-browser/context", json=write_body(old, ORG_A))).status_code == 200
        state["grant"] = (token(session=SESSION_B), USER)
        # Old-session bearer is rejected after trusted retirement; the state is
        # already clear when the fresh session performs its first GET.
        assert (await http.get("/local-browser/context")).status_code == 401
        fresh = await http.get("/local-browser/context", headers={"Authorization": f"Bearer {token(session=SESSION_B)}"})
        assert fresh.json()["organization_id"] is None


@pytest.mark.anyio
async def test_membership_cas_null_clear_and_boot_restart(route):
    app, state = route
    state["memberships"] = [{"container_id": ORG_A}, {"container_id": ORG_B}]
    async with client(app) as http:
        initial = (await http.get("/local-browser/context")).json()
        first, second = await asyncio.gather(http.post("/local-browser/context", json=write_body(initial, ORG_A)), http.post("/local-browser/context", json=write_body(initial, ORG_B)))
        assert sorted([first.status_code, second.status_code]) == [200, 409]
        chosen = (first if first.status_code == 200 else second).json()
        cleared = await http.post("/local-browser/context", json=write_body(chosen, None))
        assert cleared.json()["organization_id"] is None
        state["memberships"] = RuntimeError("offline")
        assert (await http.post("/local-browser/context", json=write_body(cleared.json(), ORG_A))).status_code == 403
        old_body = write_body(cleared.json(), None)
        state["context"] = LocalBrowserContext("engine-b")
        assert (await http.post("/local-browser/context", json=old_body)).status_code == 409


@pytest.mark.anyio
async def test_old_membership_wait_cannot_regress_new_daemon_owner(route, monkeypatch):
    app, state = route
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed(_jwt: str):
        started.set()
        await release.wait()
        return [{"container_id": ORG_A}]

    monkeypatch.setattr(subject, "_active_memberships", delayed)
    async with client(app) as http:
        initial = (await http.get("/local-browser/context")).json()
        old_write = asyncio.create_task(http.post("/local-browser/context", json=write_body(initial, ORG_A)))
        await started.wait()
        state["grant"] = (token(session=SESSION_B), USER)
        # A fresh session fences synchronously while old membership I/O waits.
        fresh = await http.get("/local-browser/context", headers={"Authorization": f"Bearer {token(session=SESSION_B)}"})
        assert fresh.json()["organization_id"] is None
        release.set()
        assert (await old_write).status_code in {401, 409}
        assert (await http.get("/local-browser/context", headers={"Authorization": f"Bearer {token(session=SESSION_B)}"})).json()["organization_id"] is None
