"""Parity: the offline agent catalog is IDENTICAL to the platform's.

Ruling D4 (Arman, 2026-09-08) — "the SQL light mirror is simply designed to
give a user offline access ... the structure, the format, and everything else
must be absolutely identical." These tests are the enforcement:

  * the served rows carry EXACTLY the 19 keys `public.agx_get_list_full()`
    declares, with the right JSON types (uuids as strings, ISO timestamps,
    `tags` as an array, nulls as null, booleans as booleans);
  * a shared / org-shared agent survives the round trip (it was structurally
    impossible before 2026-09-08);
  * the legacy `/chat/agents` projection's `shared` bucket is REAL — it was a
    hardcoded `[]`, i.e. a screen that lied;
  * an empty mirror REFUSES rather than serving an empty list.

The 19-column contract is verified against the live database definition in
`tests/parity/test_agent_catalog_contract.py::test_catalog_columns_match_the_rpc_contract`
by shape, and against the live function itself in the design doc's census.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.api.agent_catalog_routes import router as agent_catalog_router
from app.api.chat_routes import router as chat_router
from app.services.agent_catalog.client import CATALOG_COLUMNS, CATALOG_RPC
from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase

# The exact `RETURNS TABLE(...)` column list of public.agx_get_list_full(),
# read from the live database (project brsgrqvjdzwihsvnfqkf) on 2026-09-08.
LIVE_RPC_COLUMNS = (
    "id", "agent_type", "name", "description", "model_id", "category", "tags",
    "is_active", "is_archived", "is_favorite", "created_by", "organization_id",
    "task_id", "source_agent_id", "created_at", "updated_at", "is_owner",
    "access_level", "shared_by_email",
)

OWNED_ROW: dict[str, Any] = {
    "id": "00000000-0000-0000-0000-000000000001",
    "agent_type": "user",
    "name": "Owned Agent",
    "description": "mine",
    "model_id": "10000000-0000-0000-0000-000000000001",
    "category": "general",
    "tags": ["alpha", "beta"],
    "is_active": True,
    "is_archived": False,
    "is_favorite": True,
    "created_by": "aaaaaaaa-0000-0000-0000-000000000001",
    "organization_id": "20000000-0000-0000-0000-000000000001",
    "task_id": None,
    "source_agent_id": None,
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-02-01T00:00:00+00:00",
    "is_owner": True,
    "access_level": "owner",
    "shared_by_email": None,
}

SHARED_ROW: dict[str, Any] = {
    **OWNED_ROW,
    "id": "00000000-0000-0000-0000-000000000002",
    "name": "Shared With Me",
    "description": None,
    "tags": [],
    "is_favorite": False,
    "is_owner": False,
    "access_level": "view",
    "created_by": "aaaaaaaa-0000-0000-0000-000000000002",
    "shared_by_email": "owner@example.com",
    "updated_at": "2026-01-15T00:00:00+00:00",
}

BUILTIN_ROW: dict[str, Any] = {
    **OWNED_ROW,
    "id": "00000000-0000-0000-0000-000000000003",
    "agent_type": "builtin",
    "name": "General Chat",
    "description": "the platform default",
    "category": None,
    "organization_id": None,
    "tags": [],
    "is_favorite": False,
    "is_owner": False,
    "access_level": "system",
    "shared_by_email": None,
    "updated_at": "2026-01-10T00:00:00+00:00",
}

# The RPC emits its user rows first (favourites, then updated_at DESC) and then
# appends builtins — this is that sequence.
RPC_ROWS = [OWNED_ROW, SHARED_ROW, BUILTIN_ROW]


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router)
    app.include_router(agent_catalog_router)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://engine.test"
    )


def run_with_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario,
    *,
    rows: list[dict[str, Any]] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> None:
    """Seed a throwaway SQLite mirror, serve it over ASGI, run `scenario`."""

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "catalog.db")
        await db.connect()  # real migrations, including V32
        monkeypatch.setattr(database_module, "_instance", db)
        try:
            from app.services.local_db.repositories import AgentsRepo, PromptBuiltinsRepo

            if rows:
                await AgentsRepo().replace_catalog(rows, user_id="user-1")
            if details:
                await PromptBuiltinsRepo().upsert_many(details)
            async with _client(_app()) as http:
                await scenario(http)
        finally:
            await db.close()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# The contract itself
# ---------------------------------------------------------------------------


def test_catalog_columns_match_the_rpc_contract() -> None:
    assert CATALOG_COLUMNS == LIVE_RPC_COLUMNS
    assert len(CATALOG_COLUMNS) == 19


def test_served_rows_have_exactly_the_19_keys_with_the_right_json_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(http: httpx.AsyncClient) -> None:
        resp = await http.get("/agents/catalog")
        assert resp.status_code == 200
        body = resp.json()

        assert isinstance(body, list), "the catalog door returns a BARE array, no wrapper"
        assert body == RPC_ROWS, "rows must survive the mirror byte-shape identical, in RPC order"

        for row in body:
            assert list(row) == list(LIVE_RPC_COLUMNS), (
                "a served row has extra/missing/reordered keys — the mirror must "
                "never add a local field or drop a platform one"
            )
            assert isinstance(row["id"], str)
            assert isinstance(row["tags"], list)
            assert all(isinstance(t, str) for t in row["tags"])
            for boolean in ("is_active", "is_archived", "is_favorite", "is_owner"):
                assert isinstance(row[boolean], bool), f"{boolean} must be a JSON boolean"
            assert isinstance(row["created_at"], str) and "T" in row["created_at"]
            assert row["task_id"] is None or isinstance(row["task_id"], str)

        # Nulls stay null — never coerced to "" the way the old projection did.
        shared = body[1]
        assert shared["description"] is None
        assert shared["shared_by_email"] == "owner@example.com"

        assert resp.headers["x-matrx-catalog-rows"] == "3"
        assert resp.headers["x-matrx-catalog-sync-status"]

    run_with_mirror(tmp_path, monkeypatch, scenario, rows=RPC_ROWS)


def test_rpc_door_serves_the_same_rows_and_refuses_anything_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(http: httpx.AsyncClient) -> None:
        ok = await http.post("/agents/catalog/rpc", json={"fn": CATALOG_RPC})
        assert ok.status_code == 200
        assert ok.json() == RPC_ROWS

        wrong = await http.post("/agents/catalog/rpc", json={"fn": "agx_search"})
        assert wrong.status_code == 400
        assert wrong.json()["detail"]["supported"] == [CATALOG_RPC]

        with_args = await http.post(
            "/agents/catalog/rpc", json={"fn": CATALOG_RPC, "args": {"p_limit": 5}}
        )
        assert with_args.status_code == 400
        assert with_args.json()["detail"]["error"] == "unsupported_rpc_args"

    run_with_mirror(tmp_path, monkeypatch, scenario, rows=RPC_ROWS)


def test_empty_mirror_refuses_instead_of_serving_an_empty_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every signed-in user sees the builtins, so `[]` would be a lie."""

    async def scenario(http: httpx.AsyncClient) -> None:
        resp = await http.get("/agents/catalog")
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "agent_catalog_unavailable"
        assert detail["remedy"]
        assert detail["rpc"] == CATALOG_RPC

    run_with_mirror(tmp_path, monkeypatch, scenario, rows=None)


def test_status_reports_freshness_without_polluting_the_row_array(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(http: httpx.AsyncClient) -> None:
        resp = await http.get("/agents/catalog/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows"] == 3
        assert body["rpc"] == CATALOG_RPC
        assert body["mirror_user_id"] == "user-1"
        assert set(body["sync"]) == {"status", "last_synced_at", "error_message"}

    run_with_mirror(tmp_path, monkeypatch, scenario, rows=RPC_ROWS)


# ---------------------------------------------------------------------------
# The legacy projection stops lying
# ---------------------------------------------------------------------------


def test_legacy_chat_agents_shared_bucket_is_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`shared: []` was hardcoded until 2026-09-08 — a field that could never
    be non-empty while the user genuinely had shared agents."""

    details = [
        {
            "id": OWNED_ROW["id"],
            "name": OWNED_ROW["name"],
            "description": "mine",
            "category": "general",
            "tags": ["alpha"],
            "variable_defaults": [{"name": "topic"}],
            "settings": {"model_id": "m1", "stream": True},
            "is_active": True,
        }
    ]

    async def scenario(http: httpx.AsyncClient) -> None:
        resp = await http.get("/chat/agents")
        assert resp.status_code == 200
        body = resp.json()

        assert [a["id"] for a in body["shared"]] == [SHARED_ROW["id"]]
        assert body["shared"][0]["access_level"] == "view"
        assert body["shared"][0]["shared_by_email"] == "owner@example.com"
        assert [a["id"] for a in body["user"]] == [OWNED_ROW["id"]]
        assert [a["id"] for a in body["builtins"]] == [BUILTIN_ROW["id"]]
        assert body["totals"] == {"builtins": 1, "user": 1, "shared": 1, "total": 3}
        assert body["syncing"] is False

        # Variables/settings still ride along from the detail cache; a row with
        # no cached detail degrades to empty, never to a fabricated value.
        assert body["user"][0]["variable_defaults"] == [{"name": "topic"}]
        assert body["shared"][0]["variable_defaults"] == []

    run_with_mirror(tmp_path, monkeypatch, scenario, rows=RPC_ROWS, details=details)
