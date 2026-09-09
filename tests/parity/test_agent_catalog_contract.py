"""Parity: the offline agent catalog is IDENTICAL to the platform's.

Ruling D4 (Arman, 2026-09-08) — "the SQL light mirror is simply designed to
give a user offline access ... the structure, the format, and everything else
must be absolutely identical." These tests are the enforcement:

  * the served rows carry at LEAST the 19 keys `public.agx_get_list_full()`
    declares today, with the right JSON types (uuids as strings, ISO
    timestamps, `tags` as an array, nulls as null, booleans as booleans);
  * SUPERSET TOLERANCE: a row carrying a column this build predates (the
    platform's incoming nullable `orchestra`) round-trips sync → SQLite → door
    UNCHANGED, and a row MISSING a required column is still refused — an
    additive platform migration must never take an installed desktop's catalog
    down, and a subtractive one must never pass silently;
  * a shared / org-shared agent survives the round trip (it was structurally
    impossible before 2026-09-08);
  * the ONE-agent execution door serves the `agx_get_execution_full` row
    VERBATIM from its fetch-through cache and REFUSES rather than answering
    "no variables" for an agent it can neither cache nor fetch;
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
from app.services.agent_catalog.client import (
    CATALOG_COLUMNS,
    CATALOG_RPC,
    EXECUTION_RPC,
    AgentCatalogError,
    _first_row_missing_columns,
)
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
    details: list[tuple[str, dict[str, Any]]] | None = None,
) -> None:
    """Seed a throwaway SQLite mirror, serve it over ASGI, run `scenario`."""

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "catalog.db")
        await db.connect()  # real migrations, including V32
        monkeypatch.setattr(database_module, "_instance", db)
        try:
            from app.services.local_db.repositories import (
                AgentExecutionDetailsRepo,
                AgentsRepo,
            )

            if rows:
                await AgentsRepo().replace_catalog(rows, user_id="user-1")
            if details:
                repo = AgentExecutionDetailsRepo()
                for agent_id, payload in details:
                    await repo.upsert(agent_id, payload)
            async with _client(_app()) as http:
                await scenario(http)
        finally:
            await db.close()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# The contract itself
# ---------------------------------------------------------------------------


def test_catalog_columns_match_the_rpc_contract() -> None:
    """The 19 columns are the REQUIRED MINIMUM, not an exact set."""
    assert CATALOG_COLUMNS == LIVE_RPC_COLUMNS
    assert len(CATALOG_COLUMNS) == 19


def test_served_rows_carry_the_required_keys_with_the_right_json_types(
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
                "a served row reordered or dropped a platform key, or the mirror "
                "invented a LOCAL field — extra PLATFORM columns are welcome, "
                "local additions never are"
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


def test_execution_door_serves_the_rpc_row_verbatim_and_refuses_what_it_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variables form asks for ONE agent, exactly as Cloud Chat asks Supabase.

    The payload is the `agx_get_execution_full` row VERBATIM — the SAME object
    a browser gets — so the desktop can normalize both lanes with one mapper.

    An agent with nothing cached and no way to fetch must REFUSE: an empty
    payload is indistinguishable from "this agent takes no variables", and a
    form that silently drops a required question is the defect this refuses.
    """

    execution_row = {
        "id": OWNED_ROW["id"],
        "variable_definitions": [{"name": "topic", "component_type": "text"}],
        "model_id": "10000000-0000-0000-0000-000000000001",
        "settings": {"temperature": 0.4, "stream": True},
        "tools": ["30000000-0000-0000-0000-000000000009"],
        "custom_tools": None,
        "context_policies": {"mode": "auto"},
        "auto_context_disabled": False,
        "ui_gates": None,
    }

    async def scenario(http: httpx.AsyncClient) -> None:
        resp = await http.get(f"/agents/catalog/{OWNED_ROW['id']}/execution")
        assert resp.status_code == 200
        assert resp.json() == execution_row, (
            "the execution door must hand back the RPC row byte-shape identical "
            "— a projection here is a second structure (ruling D4)"
        )
        assert resp.headers["x-matrx-agent-detail-stale"] == "false"

        # Nothing cached, and no stored JWT to call the RPC with.
        missing = await http.get(f"/agents/catalog/{SHARED_ROW['id']}/execution")
        assert missing.status_code == 404
        detail = missing.json()["detail"]
        assert detail["error"] == "agent_detail_unavailable"
        assert detail["rpc"] == EXECUTION_RPC
        assert detail["remedy"]

    run_with_mirror(
        tmp_path,
        monkeypatch,
        scenario,
        rows=RPC_ROWS,
        details=[(OWNED_ROW["id"], execution_row)],
    )


# ---------------------------------------------------------------------------
# SUPERSET TOLERANCE — the platform column list is a MINIMUM, not an exact set
# ---------------------------------------------------------------------------

#: The nullable column `agx_get_list_full()` is about to grow: the conductor
#: badge. Every installed desktop reads the SAME function the browser does, so
#: if a new column were a shape violation, one platform migration would take
#: every shipped desktop's catalog offline.
ORCHESTRA_VALUE = {
    "mode": "conductor",
    "tagline": "Runs a section",
    "depth_budget": 3,
    "member_count": 4,
    "member_titles": ["Researcher", "Writer", "Editor", "Critic"],
}


def test_an_extra_platform_column_round_trips_through_the_mirror_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sync → SQLite → door, byte-shape identical, with no desktop release.

    Proven failing-then-passing: before superset tolerance the mirror rebuilt
    each served row from a pinned 19-column list, so `orchestra` was silently
    DROPPED between the RPC and the door — the desktop's structural client
    would have rendered no conductor badge and said nothing.
    """
    superset_rows = [
        {**OWNED_ROW, "orchestra": ORCHESTRA_VALUE},
        {**SHARED_ROW, "orchestra": None},
        {**BUILTIN_ROW, "orchestra": None},
    ]

    async def scenario(http: httpx.AsyncClient) -> None:
        for path, body in (
            ("/agents/catalog", None),
            ("/agents/catalog/rpc", {"fn": CATALOG_RPC}),
        ):
            resp = (
                await http.get(path)
                if body is None
                else await http.post(path, json=body)
            )
            assert resp.status_code == 200, path
            served = resp.json()
            assert served == superset_rows, (
                f"{path} did not return the superset rows unchanged — an extra "
                "PLATFORM column must reach the desktop exactly as the browser "
                "sees it"
            )
            assert served[0]["orchestra"] == ORCHESTRA_VALUE
            assert served[1]["orchestra"] is None

    run_with_mirror(tmp_path, monkeypatch, scenario, rows=superset_rows)


def test_an_extra_column_also_lands_in_its_first_class_sqlite_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`orchestra` is queryable in SQLite, not only carried in raw_json (V33)."""

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "orchestra.db")
        await db.connect()
        monkeypatch.setattr(database_module, "_instance", db)
        try:
            from app.services.local_db.repositories import AgentsRepo

            await AgentsRepo().replace_catalog(
                [{**OWNED_ROW, "orchestra": ORCHESTRA_VALUE}], user_id="user-1"
            )
            rows = await db.fetchall(
                "SELECT id, orchestra FROM agents WHERE orchestra IS NOT NULL"
            )
            assert [r["id"] for r in rows] == [OWNED_ROW["id"]]
        finally:
            await db.close()

    asyncio.run(main())


def test_a_row_missing_a_required_column_is_still_refused() -> None:
    """Additive is fine; SUBTRACTIVE is a loud refusal, never a quiet gap."""
    complete = [{**OWNED_ROW, "orchestra": ORCHESTRA_VALUE}]
    assert _first_row_missing_columns(complete) == [], (
        "a superset row must not be reported as a shape violation"
    )

    without_access_level = dict(OWNED_ROW)
    without_access_level.pop("access_level")
    assert _first_row_missing_columns([without_access_level]) == ["access_level"]

    # And the caller turns that into a refusal with a stated remedy.
    assert issubclass(AgentCatalogError, RuntimeError)


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
