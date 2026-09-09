"""Parity: the offline agent catalog is IDENTICAL to the platform's.

Ruling D4 (Arman, 2026-09-08) — "the SQL light mirror is simply designed to
give a user offline access ... the structure, the format, and everything else
must be absolutely identical." These tests are the enforcement:

  * the served rows carry EXACTLY the 19 keys `public.agx_get_list_full()`
    declares, with the right JSON types (uuids as strings, ISO timestamps,
    `tags` as an array, nulls as null, booleans as booleans);
  * a shared / org-shared agent survives the round trip (it was structurally
    impossible before 2026-09-08);
  * the ONE-agent execution door serves variables/settings from the detail
    cache and REFUSES (404) rather than answering "no variables" for an agent
    it has never mirrored;
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
# The ONE-agent execution door (variables/settings are never list columns)
# ---------------------------------------------------------------------------


def test_execution_door_serves_the_detail_cache_and_refuses_what_it_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variables form asks for ONE agent, exactly as Cloud Chat asks Supabase.

    An agent with no mirrored detail must 404: an empty payload is
    indistinguishable from "this agent takes no variables", and a form that
    silently drops a required question is the defect this refuses.
    """

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
        resp = await http.get(f"/agents/catalog/{OWNED_ROW['id']}/execution")
        assert resp.status_code == 200
        body = resp.json()
        assert body["variable_defaults"] == [{"name": "topic"}]
        assert body["settings"]["model_id"] == "m1"

        missing = await http.get(f"/agents/catalog/{SHARED_ROW['id']}/execution")
        assert missing.status_code == 404
        assert missing.json()["detail"]["error"] == "agent_detail_not_mirrored"

    run_with_mirror(tmp_path, monkeypatch, scenario, rows=RPC_ROWS, details=details)


# ---------------------------------------------------------------------------
# The torn mirror — a read during a refresh must never see a SHORT catalog
# ---------------------------------------------------------------------------


def test_a_read_during_a_refresh_never_sees_a_half_written_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured live 2026-09-08 on the desktop's own picker.

    `replace_catalog` DELETEs then re-INSERTs every row on the process's ONE
    shared aiosqlite connection. A read issued between the DELETE and the
    COMMIT sees a half-written table — and the offline picker rendered
    "Public 326" beside the cloud picker's "Public 413" for the identical
    468-row catalog. A confidently short list is indistinguishable from lost
    access, which is exactly what ruling D4 forbids.

    This drives a read CONCURRENTLY with a slowed refresh. Without the mirror
    lock it observes a partial table; with it, it sees the whole catalog —
    either the old one or the new one, never a torn one.
    """

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "torn.db")
        await db.connect()
        monkeypatch.setattr(database_module, "_instance", db)
        try:
            from app.services.local_db.repositories import AgentsRepo

            repo = AgentsRepo()
            await repo.replace_catalog(RPC_ROWS, user_id="user-1")

            # A refresh whose per-row insert yields to the event loop, so a
            # concurrent reader gets every chance to observe the middle of it.
            original_insert = AgentsRepo._insert

            async def slow_insert(self, row, *, user_id, position):  # type: ignore[no-untyped-def]
                await asyncio.sleep(0)
                await original_insert(self, row, user_id=user_id, position=position)

            monkeypatch.setattr(AgentsRepo, "_insert", slow_insert)

            observed: list[int] = []

            async def read_repeatedly() -> None:
                for _ in range(40):
                    observed.append(len(await AgentsRepo().list_catalog()))
                    await asyncio.sleep(0)

            await asyncio.gather(
                AgentsRepo().replace_catalog(RPC_ROWS, user_id="user-1"),
                read_repeatedly(),
            )

            assert observed, "the concurrent reader never ran"
            # Every observation is a WHOLE catalog. A torn read shows up as any
            # count strictly between 0 and the full row set.
            torn = [n for n in observed if n != len(RPC_ROWS)]
            assert not torn, (
                "A read observed a HALF-WRITTEN agent mirror "
                f"({sorted(set(torn))} rows instead of {len(RPC_ROWS)}). "
                "The picker would have shown a short list and said nothing. "
                "AgentsRepo._mirror_lock must cover both the replace and the read."
            )
        finally:
            await db.close()

    asyncio.run(main())
