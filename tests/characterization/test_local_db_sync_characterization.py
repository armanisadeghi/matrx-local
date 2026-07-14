"""Characterization: local_db background sync engine pull/upsert logic.

Pins the CURRENT behavior (2026-07-10) of app/services/local_db/sync_engine.py
against a REAL SQLite database (real schema migrations) in a tmp_path — never
~/.matrx/matrx.db. The AIDream client is faked; nothing touches the network.

Injection points used:
  - app.services.local_db.database._instance is monkeypatched to a
    LocalDatabase(tmp_path/...) so every repo constructed via get_db()
    lands in the throwaway DB.
  - app.services.local_db.sync_engine.get_aidream_client is monkeypatched
    for the models/agents pulls.

Each test runs its whole scenario inside ONE asyncio.run() so the aiosqlite
connection lives and dies on a single event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest

# ORDER MATTERS: app.common first — importing app.config as the process's
# first app module trips the app.config <-> app.common circular import
# (tracked in .matrx/AGENT_TASKS.md).
import app.common  # noqa: F401

from app.services.local_db import database as database_module
from app.services.local_db import sync_engine as sync_engine_module
from app.services.local_db.database import LocalDatabase
from app.services.local_db.sync_engine import (
    SyncEngine,
    _extract_settings,
    _hash_list,
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
        agents: list[dict[str, Any]] | None = None,
        offline: bool = False,
    ) -> None:
        self._models = models or []
        self._agents = agents or []
        self._offline = offline
        self.fetch_agents_jwts: list[str] = []

    async def fetch_models(self) -> list[dict[str, Any]]:
        if self._offline:
            raise AIDreamOfflineError("server unreachable (fake)")
        return self._models

    async def fetch_agents(self, jwt: str) -> list[dict[str, Any]]:
        if self._offline:
            raise AIDreamOfflineError("server unreachable (fake)")
        self.fetch_agents_jwts.append(jwt)
        return self._agents


def run_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Callable[[SyncEngine, LocalDatabase], Awaitable[None]],
    *,
    client: FakeAIDreamClient | None = None,
) -> None:
    """Connect a throwaway SQLite DB, run `scenario`, always close."""
    monkeypatch.setattr(
        sync_engine_module, "get_aidream_client", lambda: client
    )

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "characterization.db")
        await db.connect()  # runs the REAL schema migrations
        monkeypatch.setattr(database_module, "_instance", db)
        try:
            engine = SyncEngine()  # repos resolve get_db() -> our tmp db
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
        assert len(rows) == 115  # catalog size pinned by the registry gate too
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
        assert row["cnt"] == 115

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
        assert "AIDREAM_SERVER_URL_LIVE" in (meta["error_message"] or "")
        row = await db.fetchone("SELECT COUNT(*) AS cnt FROM ai_models")
        assert row["cnt"] == 0

    run_scenario(tmp_path, monkeypatch, scenario, client=None)


# ---------------------------------------------------------------------------
# Agents sync — JWT gate keeps the cache
# ---------------------------------------------------------------------------


def test_sync_agents_without_jwt_skips_and_keeps_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeAIDreamClient(agents=[{"id": "a-new", "name": "New Agent"}])

    async def scenario(engine: SyncEngine, db: LocalDatabase) -> None:
        # Pre-seed a cached agent from a previous successful sync.
        await engine._agents_repo.upsert_many(
            [
                {
                    "id": "a-cached",
                    "name": "Cached Agent",
                    "description": "",
                    "source": "builtin",
                    "user_id": "",
                    "is_active": True,
                }
            ]
        )

        await engine.sync_agents()  # no token stored → must skip

        # The fetch never happened, and the cache survived.
        assert client.fetch_agents_jwts == []
        rows = await db.fetchall("SELECT id FROM agents")
        assert [r["id"] for r in rows] == ["a-cached"]

        meta = await _sync_status(db, "agents")
        assert meta is not None and meta["status"] == "skipped"
        assert "requires auth" in (meta["error_message"] or "")

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


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


def test_extract_settings_pins() -> None:
    # settings dict wins; max_output_tokens is an accepted alias.
    row = {
        "settings": {"model_id": "m1", "max_output_tokens": 512},
        "temperature": 0.9,
    }
    assert _extract_settings(row) == {
        "model_id": "m1",
        "temperature": 0.9,  # falls through to the row when absent in settings
        "max_tokens": 512,
        "stream": True,  # default
        "tools": [],
    }

    # settings arrives as a JSON string → decoded.
    row2 = {"settings": '{"model_id": "m2", "stream": false}'}
    assert _extract_settings(row2)["model_id"] == "m2"
    assert _extract_settings(row2)["stream"] is False

    # unparseable settings string → treated as empty, row-level fallbacks used.
    row3 = {"settings": "garbage", "model_id": "m3", "max_tokens": 99}
    extracted = _extract_settings(row3)
    assert extracted["model_id"] == "m3"
    assert extracted["max_tokens"] == 99
