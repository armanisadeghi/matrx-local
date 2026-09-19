"""Race and ownership tests for the narrow local-browser context route."""
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

USER = "11111111-1111-4111-8111-111111111111"
SESSION_A = "22222222-2222-4222-8222-222222222222"
SESSION_B = "33333333-3333-4333-8333-333333333333"
ORG_A = "44444444-4444-4444-8444-444444444444"
ORG_B = "55555555-5555-4555-8555-555555555555"


def token(user: str = USER, session: str = SESSION_A) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": user, "session_id": session}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def write_body(context: dict, organization_id: str | None) -> dict:
    return {
        "engine_boot_id": context["engine_boot_id"],
        "expected_revision": context["revision"],
        "organization_id": organization_id,
    }


@pytest.fixture
def route(monkeypatch):
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(subject.router)
    state = {"grant": (token(), USER), "memberships": [{"container_id": ORG_A}]}

    async def verified(value: str):
        claims = subject._claim(value)
        return VerifiedUser(user_id=claims[0], email=None, is_anon=False)

    class Sync:
        async def access_grant(self):
            return state["grant"]

    async def memberships(_jwt: str):
        value = state["memberships"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(subject, "verify_supabase_token", verified)
    monkeypatch.setattr(subject, "get_sync_client", lambda: Sync())
    monkeypatch.setattr(subject, "_active_memberships", memberships)
    return app, state


def client(app, host="127.0.0.1"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(host, 43210)),
        base_url="http://engine.test",
        headers={"Authorization": f"Bearer {token()}"},
    )


@pytest.mark.anyio
async def test_direct_loopback_owner_and_closed_body(route):
    app, _state = route
    async with client(app) as http:
        assert (await http.get("/local-browser/context", headers={"Authorization": ""})).status_code == 401
        initial = await http.get("/local-browser/context")
        assert initial.status_code == 200
        body = initial.json()
        assert set(body) == {"engine_boot_id", "revision", "organization_id"}
        rejected = await http.post("/local-browser/context", json={**write_body(body, None), "extra": True})
        assert rejected.status_code == 422
        malformed = await http.post("/local-browser/context", json={**write_body(body, None), "expected_revision": True})
        assert malformed.status_code == 422


@pytest.mark.anyio
async def test_refuses_tunnel_forgery_token_and_daemon_mismatch(route):
    app, state = route
    async with client(app) as http:
        assert (await http.get("/local-browser/context", headers={"cf-ray": "forged"})).status_code in {401, 403}
        state["grant"] = (token(session=SESSION_B), USER)
        assert (await http.get("/local-browser/context")).status_code == 401
    async with client(app, "203.0.113.8") as remote:
        assert (await remote.get("/local-browser/context")).status_code == 403


@pytest.mark.anyio
async def test_membership_and_same_user_relogin_fence(route, monkeypatch):
    app, state = route
    async with client(app) as http:
        initial = (await http.get("/local-browser/context")).json()
        state["memberships"] = []
        refused = await http.post("/local-browser/context", json=write_body(initial, ORG_A))
        assert refused.status_code == 403
        state["memberships"] = [{"container_id": ORG_A}]

        async def relogin(_jwt):
            state["grant"] = (token(session=SESSION_B), USER)
            return [{"container_id": ORG_A}]

        monkeypatch.setattr(subject, "_active_memberships", relogin)
        stale = await http.post("/local-browser/context", json=write_body(initial, ORG_A))
        assert stale.status_code in {401, 409}
        # The next authenticated session sees a fenced empty context, even
        # though the actor id did not change.
        fresh = await http.get("/local-browser/context", headers={"Authorization": f"Bearer {token(session=SESSION_B)}"})
        assert fresh.status_code == 200
        assert fresh.json()["organization_id"] is None


@pytest.mark.anyio
async def test_cas_null_clear_and_new_boot(route):
    app, _state = route
    async with client(app) as http:
        initial = (await http.get("/local-browser/context")).json()
        set_org = await http.post("/local-browser/context", json=write_body(initial, ORG_A))
        assert set_org.status_code == 200, set_org.text
        chosen = set_org.json()
        clear = await http.post("/local-browser/context", json=write_body(chosen, None))
        assert clear.status_code == 200
        assert clear.json()["organization_id"] is None
        old_boot = clear.json()["engine_boot_id"]

    restarted = FastAPI()
    restarted.include_router(subject.router)
    async with client(restarted) as http:
        now = (await http.get("/local-browser/context")).json()
        assert now["engine_boot_id"] != old_boot
        stale = await http.post("/local-browser/context", json=write_body(clear.json(), ORG_A))
        assert stale.status_code == 409


@pytest.mark.anyio
async def test_concurrent_cas_allows_exactly_one_and_membership_failure(route):
    app, state = route
    state["memberships"] = [{"container_id": ORG_A}, {"container_id": ORG_B}]
    async with client(app) as http:
        initial = (await http.get("/local-browser/context")).json()
        body_a = write_body(initial, ORG_A)
        body_b = write_body(initial, ORG_B)
        first, second = await asyncio.gather(
            http.post("/local-browser/context", json=body_a),
            http.post("/local-browser/context", json=body_b),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 409]
        state["memberships"] = RuntimeError("offline")
        current = (await http.get("/local-browser/context")).json()
        failure = await http.post("/local-browser/context", json=write_body(current, ORG_A))
        assert failure.status_code == 403
