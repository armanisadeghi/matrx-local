"""Characterization: local_db background sync engine pull/upsert logic.

Pins the CURRENT behavior (2026-07-10) of app/services/local_db/sync_engine.py
against a REAL SQLite database (real schema migrations) in a tmp_path — never
~/.matrx/matrx.db. The AIDream client is faked; nothing touches the network.

Injection points used:
  - app.services.local_db.database._instance is monkeypatched to a
    LocalDatabase(tmp_path/...) so every repo constructed via get_db()
    lands in the throwaway DB.
  - app.services.local_db.sync_engine.get_aidream_client is monkeypatched
    for the models pull and the (legacy) agent variables/settings detail cache.
  - app.services.local_db.sync_engine.fetch_agent_catalog is monkeypatched
    for the AGENT CATALOG pull. Since 2026-09-08 the catalog's source is the
    Supabase RPC ``public.agx_get_list_full()`` — the same one matrx-frontend,
    matrx-extend and workflow-studio read (ruling D4: matrx-local is never an
    exception). It is NOT the aidream ``GET /agents`` route, whose membership
    silently omitted every shared and org-shared agent.

Each test runs its whole scenario inside ONE asyncio.run() so the aiosqlite
connection lives and dies on a single event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest

from app.services.local_db import database as database_module
from app.services.local_db import sync_engine as sync_engine_module
from app.services.local_db.database import LocalDatabase
from app.services.local_db.sync_engine import (
    SyncEngine,
    _hash_list,
)
from app.services.agent_catalog.client import (
    CATALOG_COLUMNS,
    AgentCatalogAuthError,
    AgentCatalogError,
)
from app.services.aidream.client import AIDreamOfflineError
from app.tools.catalog import get_catalog


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class FakeAIDreamClient:
    def __init__(
        self,
        models: list[dict[str, Any]] | None = None,
        offline: bool = False,
    ) -> None:
        self._models = models or []
        self._offline = offline

    async def fetch_models(self) -> list[dict[str, Any]]:
        if self._offline:
            raise AIDreamOfflineError("server unreachable (fake)")
        return self._models


class FakeTokenRepo:
    """A signed-in user, without touching the OS keychain."""

    def __init__(self, jwt: str = "jwt-abc", user_id: str = "user-1") -> None:
        self._row = {"access_token": jwt, "user_id": user_id}

    async def get(self) -> dict[str, Any]:
        return dict(self._row)

    def is_expired(self, token_row: dict[str, Any]) -> bool:
        return False


class FakeCatalog:
    """Stands in for the ``agx_get_list_full`` PostgREST read."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.rows = rows if rows is not None else []
        self.raises = raises
        self.jwts: list[str] = []

    async def __call__(self, jwt: str) -> list[dict[str, Any]]:
        self.jwts.append(jwt)
        if self.raises is not None:
            raise self.raises
        return [dict(r) for r in self.rows]


def _catalog_row(**overrides: Any) -> dict[str, Any]:
    """A complete 19-column ``agx_get_list_full()`` row."""
    row: dict[str, Any] = {
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
        "created_by": "user-1",
        "organization_id": "20000000-0000-0000-0000-000000000001",
        "task_id": None,
        "source_agent_id": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-02-01T00:00:00+00:00",
        "is_owner": True,
        "access_level": "owner",
        "shared_by_email": None,
    }
    row.update(overrides)
    assert set(row) == set(CATALOG_COLUMNS), "fixture drifted from the RPC contract"
    return row


SHARED_ROW = _catalog_row(
    id="00000000-0000-0000-0000-000000000002",
    name="Shared With Me",
    description="someone else's, shared",
    is_favorite=False,
    is_owner=False,
    access_level="view",
    created_by="user-2",
    shared_by_email="owner@example.com",
    updated_at="2026-01-15T00:00:00+00:00",
)

BUILTIN_ROW = _catalog_row(
    id="00000000-0000-0000-0000-000000000003",
    agent_type="builtin",
    name="General Chat",
    is_favorite=False,
    is_owner=False,
    access_level="system",
    tags=[],
    category=None,
    organization_id=None,
    shared_by_email=None,
    updated_at="2026-01-10T00:00:00+00:00",
)


def run_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Callable[[SyncEngine, LocalDatabase], Awaitable[None]],
    *,
    client: FakeAIDreamClient | None = None,
    catalog: FakeCatalog | None = None,
    signed_in: bool = False,
) -> None:
    """Connect a throwaway SQLite DB, run `scenario`, always close."""
    monkeypatch.setattr(
        sync_engine_module, "get_aidream_client", lambda: client
    )
    monkeypatch.setattr(
        sync_engine_module, "fetch_agent_catalog", catalog or FakeCatalog()
    )

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "characterization.db")
        await db.connect()  # runs the REAL schema migrations
        monkeypatch.setattr(database_module, "_instance", db)
        try:
            engine = SyncEngine()  # repos resolve get_db() -> our tmp db
            if signed_in:
                engine._token_repo = FakeTokenRepo()
            await scenario(engine, db)
        finally:
            await db.close()

    asyncio.run(main())


async def _sync_status(db: LocalDatabase, entity: str) -> dict[str, Any] | None:
    row = await db.fetchone(
        "SELECT * FROM sync_meta WHERE entity_type = ?", (entity,)
    )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Tools sync — canonical catalog -> tools table
# ---------------------------------------------------------------------------


def test_sync_tools_caches_full_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_tools()

        rows = await db.fetchall("SELECT id, name, source, version FROM tools")
        by_id = {r["id"]: dict(r) for r in rows}
        assert sorted(by_id) == sorted(e.cloud_name for e in get_catalog()), (
            "tools table does not mirror the tool catalog exactly"
        )
        assert len(rows) == len(get_catalog())
        assert all(r["source"] == "local" for r in by_id.values())
        # id == name for local tools (current behavior).
        assert all(r["id"] == r["name"] for r in by_id.values())

        meta = await _sync_status(db, "tools")
        assert meta is not None and meta["status"] == "success"
        assert meta["last_hash"]  # content hash recorded

    run_scenario(tmp_path, monkeypatch, scenario)


def test_sync_tools_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_tools()
        first = await _sync_status(db, "tools")
        await engine.sync_tools()
        second = await _sync_status(db, "tools")
        assert first is not None and second is not None
        assert first["last_hash"] == second["last_hash"]
        row = await db.fetchone("SELECT COUNT(*) AS cnt FROM tools")
        assert row["cnt"] == len(get_catalog())

    run_scenario(tmp_path, monkeypatch, scenario)


# ---------------------------------------------------------------------------
# Models sync — filtering / provider mapping / delete_missing
# ---------------------------------------------------------------------------


_RAW_MODELS = [
    # kept: endpoint maps to a provider
    {"id": "m-claude", "name": "claude", "common_name": "Claude", "endpoints": ["anthropic_chat"]},
    # kept: FIRST mappable endpoint wins (openai before google)
    {"id": "m-multi", "name": "multi", "endpoints": ["openai_chat", "google_chat"]},
    # kept: endpoints arrive as a JSON string and are decoded
    {"id": "m-string", "name": "stringy", "endpoints": '["groq_chat"]'},
    # dropped: deprecated
    {"id": "m-dead", "name": "dead", "endpoints": ["anthropic_chat"], "is_deprecated": True},
    # dropped: no mappable endpoint
    {"id": "m-alien", "name": "alien", "endpoints": ["weird_endpoint"]},
    # dropped: unparseable endpoints string decodes to []
    {"id": "m-bad-json", "name": "badjson", "endpoints": "not-json"},
]


def test_sync_models_filters_and_maps_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_models()

        rows = await db.fetchall("SELECT id, provider FROM ai_models")
        providers = {r["id"]: r["provider"] for r in rows}
        assert providers == {
            "m-claude": "anthropic",
            "m-multi": "openai",  # first mappable endpoint wins
            "m-string": "groq",  # JSON-string endpoints decoded
        }, (
            "MODEL PULL BEHAVIOR CHANGED: deprecated rows, unmappable "
            "endpoints, and bad endpoint JSON must be dropped; provider comes "
            "from the first mappable endpoint."
        )

        meta = await _sync_status(db, "models")
        assert meta is not None and meta["status"] == "success"

    run_scenario(
        tmp_path, monkeypatch, scenario, client=FakeAIDreamClient(models=_RAW_MODELS)
    )


def test_sync_models_removes_models_missing_from_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeAIDreamClient(models=list(_RAW_MODELS))

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_models()
        row = await db.fetchone("SELECT COUNT(*) AS cnt FROM ai_models")
        assert row["cnt"] == 3

        # Second pull: the feed shrank to one model — stale rows are deleted.
        client._models = [_RAW_MODELS[0]]
        await engine.sync_models()
        rows = await db.fetchall("SELECT id FROM ai_models")
        assert [r["id"] for r in rows] == ["m-claude"]

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


def test_sync_models_without_client_records_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_models()
        meta = await _sync_status(db, "models")
        assert meta is not None and meta["status"] == "skipped"
        assert meta["error_message"] == "AIDream server URL unavailable from app config"
        row = await db.fetchone("SELECT COUNT(*) AS cnt FROM ai_models")
        assert row["cnt"] == 0

    run_scenario(tmp_path, monkeypatch, scenario, client=None)


# ---------------------------------------------------------------------------
# Agent catalog sync — source, shape, membership, failure posture
# ---------------------------------------------------------------------------


def test_sync_agents_without_jwt_skips_and_keeps_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = FakeCatalog(rows=[_catalog_row()])

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        # Pre-seed a mirrored row from a previous successful sync.
        await engine._agents_repo.upsert(BUILTIN_ROW, user_id="user-1")

        await engine.sync_agents()  # no token stored -> must skip

        # The RPC was never called, and the mirror survived.
        assert catalog.jwts == []
        rows = await db.fetchall("SELECT id FROM agents")
        assert [r["id"] for r in rows] == [BUILTIN_ROW["id"]]

        meta = await _sync_status(db, "agents")
        assert meta is not None and meta["status"] == "skipped"
        assert "requires authentication" in (meta["error_message"] or "")

    run_scenario(tmp_path, monkeypatch, scenario, catalog=catalog)


def test_sync_agents_mirrors_the_rpc_rows_verbatim_and_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling D4: the mirror changes WHERE the catalog lives, never WHAT it is.

    Every one of the 19 columns survives with its JSON type, and the rows come
    back in the order the database returned them — not re-sorted locally.
    """
    owned = _catalog_row()
    rpc_rows = [owned, SHARED_ROW, BUILTIN_ROW]
    catalog = FakeCatalog(rows=rpc_rows)

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_agents()

        assert catalog.jwts == ["jwt-abc"]
        mirrored = await engine._agents_repo.list_catalog()
        assert mirrored == rpc_rows, (
            "the SQLite mirror must return the RPC rows byte-shape identical "
            "and in the RPC's own order"
        )

        meta = await _sync_status(db, "agents")
        assert meta is not None and meta["status"] == "success"

    run_scenario(tmp_path, monkeypatch, scenario, catalog=catalog, signed_in=True)


def test_sync_agents_keeps_shared_and_org_shared_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE DEFECT THIS FIXES: shared agents used to be structurally impossible.

    The old source (aidream ``GET /agents``) returned builtins plus rows the
    caller created, so no shared or org-shared agent could ever reach the
    desktop. This test fails the moment a membership filter creeps back in.
    """
    catalog = FakeCatalog(rows=[SHARED_ROW, BUILTIN_ROW])

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_agents()

        rows = {r["id"]: r for r in await engine._agents_repo.list_catalog()}
        shared = rows[SHARED_ROW["id"]]
        assert shared["is_owner"] is False
        assert shared["access_level"] == "view"
        assert shared["shared_by_email"] == "owner@example.com"

    run_scenario(tmp_path, monkeypatch, scenario, catalog=catalog, signed_in=True)


def test_sync_agents_auth_failure_is_loud_and_keeps_the_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired JWT never silently downgrades to a stale membership claim."""
    catalog = FakeCatalog(raises=AgentCatalogAuthError("rejected", status_code=401))

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine._agents_repo.upsert(BUILTIN_ROW, user_id="user-1")
        await engine.sync_agents()

        rows = await db.fetchall("SELECT id FROM agents")
        assert [r["id"] for r in rows] == [BUILTIN_ROW["id"]]
        meta = await _sync_status(db, "agents")
        assert meta is not None and meta["status"] == "error"
        assert "rejected" in (meta["error_message"] or "")

    run_scenario(tmp_path, monkeypatch, scenario, catalog=catalog, signed_in=True)


def test_sync_agents_network_failure_keeps_the_mirror_and_records_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = FakeCatalog(raises=AgentCatalogError("agx_get_list_full unreachable: boom"))

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine._agents_repo.upsert(BUILTIN_ROW, user_id="user-1")
        await engine.sync_agents()

        rows = await db.fetchall("SELECT id FROM agents")
        assert [r["id"] for r in rows] == [BUILTIN_ROW["id"]]
        meta = await _sync_status(db, "agents")
        assert meta is not None and meta["status"] == "offline"
        assert "unreachable" in (meta["error_message"] or "")

    run_scenario(tmp_path, monkeypatch, scenario, catalog=catalog, signed_in=True)


def test_sync_agents_empty_rpc_result_never_wipes_the_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every signed-in user sees the active builtins — zero rows is a failure."""
    catalog = FakeCatalog(rows=[])

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine._agents_repo.upsert(BUILTIN_ROW, user_id="user-1")
        await engine.sync_agents()

        rows = await db.fetchall("SELECT id FROM agents")
        assert [r["id"] for r in rows] == [BUILTIN_ROW["id"]]
        meta = await _sync_status(db, "agents")
        assert meta is not None and meta["status"] == "error"
        assert "zero rows" in (meta["error_message"] or "")

    run_scenario(tmp_path, monkeypatch, scenario, catalog=catalog, signed_in=True)


def test_the_aidream_agents_listing_route_has_no_caller_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sidecar reads the platform, never the aidream `GET /agents` list.

    That route's membership is builtins + agents the caller CREATED, it
    carried 7 of the catalog's columns, and it 400s outright for a user
    belonging to several organizations with no default — which left every
    variables form silently empty. Both the catalog and the per-agent
    execution detail now come from Supabase RPCs. The client method is gone;
    this pins that it cannot come back through the sync engine.
    """
    from app.services.aidream import client as aidream_client_module

    assert not hasattr(aidream_client_module.AIDreamClient, "fetch_agents"), (
        "AIDreamClient.fetch_agents is back — the aidream /agents listing route "
        "is a second catalog and a second membership rule (ruling D4)"
    )

    catalog = FakeCatalog(rows=[BUILTIN_ROW])

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_agents()

        catalog_ids = [r["id"] for r in await db.fetchall("SELECT id FROM agents")]
        assert catalog_ids == [BUILTIN_ROW["id"]]

        # Execution detail is LAZY: a catalog sync must not fire one RPC per
        # agent. Nothing is cached until an /execution request asks for one.
        cached = await db.fetchall("SELECT agent_id FROM agent_execution_details")
        assert cached == [], (
            "sync_agents pre-warmed the execution cache — 468 agents would be "
            "468 RPC calls at startup"
        )

    run_scenario(
        tmp_path,
        monkeypatch,
        scenario,
        client=FakeAIDreamClient(),
        catalog=catalog,
        signed_in=True,
    )


def test_the_mirror_carries_a_platform_column_this_build_predates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An additive platform migration must never take the catalog down."""
    superset = {**BUILTIN_ROW, "orchestra": {"mode": "conductor", "member_count": 2}}
    catalog = FakeCatalog(rows=[superset])

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        await engine.sync_agents()

        meta = await _sync_status(db, "agents")
        assert meta is not None and meta["status"] == "success", (
            "a NEW platform column was treated as a failure — that is a "
            "self-inflicted outage on every installed desktop"
        )
        served = await engine._agents_repo.list_catalog()
        assert served == [superset]

    run_scenario(tmp_path, monkeypatch, scenario, catalog=catalog, signed_in=True)


# ---------------------------------------------------------------------------
# sync_all — per-entity status aggregation
# ---------------------------------------------------------------------------


def test_sync_all_reports_offline_per_entity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        results = await engine.sync_all()
        # models raises AIDreamOfflineError → "offline". agents never reaches
        # the network: the JWT gate fires first (no stored token) and returns
        # normally, so sync_all reports "success" with the truthful skip in
        # sync_meta. tools is purely local and always succeeds.
        assert results == {"models": "offline", "agents": "success", "tools": "success"}
        agents_meta = await _sync_status(db, "agents")
        assert agents_meta is not None and agents_meta["status"] == "skipped"

    run_scenario(
        tmp_path, monkeypatch, scenario, client=FakeAIDreamClient(offline=True)
    )


def test_sync_all_with_no_client_reports_success_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHARACTERIZATION, not endorsement: with no AIDream client configured the
    per-entity sync methods return normally after recording status=skipped in
    sync_meta, so sync_all's aggregate says 'success' for entities that were
    actually skipped. The truthful skip state lives in sync_meta only."""

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        results = await engine.sync_all()
        assert results == {"models": "success", "agents": "success", "tools": "success"}
        models_meta = await _sync_status(db, "models")
        agents_meta = await _sync_status(db, "agents")
        assert models_meta is not None and models_meta["status"] == "skipped"
        assert agents_meta is not None and agents_meta["status"] == "skipped"

    run_scenario(tmp_path, monkeypatch, scenario, client=None)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_hash_list_pins() -> None:
    a = [{"x": 1, "y": [1, 2]}]
    assert _hash_list(a) == _hash_list([{"y": [1, 2], "x": 1}])  # key order free
    assert _hash_list(a) != _hash_list([{"x": 2, "y": [1, 2]}])
    assert len(_hash_list(a)) == 16  # truncated sha256 hex
