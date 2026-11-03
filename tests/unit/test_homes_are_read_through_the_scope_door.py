"""The homes directory is read through custom.context_tree, never the custom.context_scopes view.

People have no SELECT on the view, so every listing answered 403 / 42501 and the
homes directory was always empty: 3,440 matrx-local system_error rows
(2026-10-11..23, "Supabase PostgREST request rejected ... table=context_scopes
status=403"). The door the web's scope tree uses answers the same homes for the
person's live memberships. RED on the old list_scopes (it read the view), GREEN now.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.services.aidream import organization as org_module
from app.services.documents.supabase_client import SupabaseDocClient


def _forbidden(table: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://db.example.test/rest/v1/{table}")
    return httpx.HTTPStatusError(
        "403", request=request, response=httpx.Response(403, request=request)
    )


def test_list_scopes_asks_the_scope_door_for_every_live_membership(monkeypatch) -> None:
    client = SupabaseDocClient()
    client.set_jwt("jwt")
    rpc_calls: list[tuple[str, dict[str, Any], str]] = []

    async def memberships(jwt: str):
        assert jwt == "jwt"
        return [{"container_id": "org-law", "status": "active"}, {"container_id": "org-ai", "status": "active"}]

    async def request(method, table, *a, **k):
        if table == "context_scopes":
            raise _forbidden(table)  # what live PostgREST answers a person
        raise AssertionError(f"unexpected table read {table}")

    async def rpc(fn, args, *, schema):
        rpc_calls.append((fn, args, schema))
        return {
            "types": [{"id": "t1"}],
            "scopes": [
                {"id": "s-abc", "organization_id": "org-law", "name": "ABC Legal", "scope_type_id": "t1"},
                {"id": "g-ai", "organization_id": "org-ai", "name": "General", "scope_type_id": "t1"},
            ],
        }

    monkeypatch.setattr(org_module, "_active_memberships", memberships)
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "_rpc", rpc)

    rows = asyncio.run(client.list_scopes())

    assert rpc_calls == [("context_tree", {"p_organization_ids": ["org-ai", "org-law"]}, "custom")]
    assert rows == [
        {"id": "s-abc", "organization_id": "org-law", "name": "ABC Legal"},
        {"id": "g-ai", "organization_id": "org-ai", "name": "General"},
    ]


def test_no_membership_means_no_homes_and_no_door_call(monkeypatch) -> None:
    client = SupabaseDocClient()
    client.set_jwt("jwt")

    async def memberships(jwt: str):
        return []

    async def rpc(*a, **k):
        raise AssertionError("asked the door with no organization")

    monkeypatch.setattr(org_module, "_active_memberships", memberships)
    monkeypatch.setattr(client, "_rpc", rpc)
    assert asyncio.run(client.list_scopes()) == []


def test_signed_out_is_refused_loudly() -> None:
    with pytest.raises(RuntimeError):
        asyncio.run(SupabaseDocClient().list_scopes())
