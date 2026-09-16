"""Regression coverage for recovery of columns added to the chat mirror.

All cases use real SQLite mirror files and a fake PostgREST page source.  No
engine process, cloud request, or user database is involved.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiosqlite
import pytest

from app.services.chat_sync import engine as chat_engine_module
from app.services.chat_sync.client import ChatSyncHTTPError
from app.services.chat_sync.engine import ChatSyncEngine
from app.services.local_db import database as database_module
from app.services.local_db import mirror as mirror_module
from app.services.local_db.database import LocalDatabase
from app.services.local_db.mirror_schema import MIRROR_TABLES


def _run(tmp_path: Path, scenario: Callable[[LocalDatabase], Awaitable[None]]) -> None:
    async def main() -> None:
        db = LocalDatabase(tmp_path / "matrx.db")
        await db.connect()
        old = database_module._instance
        database_module._instance = db
        try:
            await scenario(db)
        finally:
            database_module._instance = old
            await db.close()

    asyncio.run(main())


async def _mark(db: LocalDatabase, table: str, columns: list[str]) -> None:
    await db.execute(
        'INSERT INTO chat._mirror_meta (key, value) VALUES (?, ?)',
        (f"column_hydration:{table}", json.dumps({"columns": columns})),
    )
    await db.commit()


async def _marker(db: LocalDatabase, table: str) -> dict[str, Any] | None:
    row = await db.fetchone(
        'SELECT value FROM chat._mirror_meta WHERE key=?', (f"column_hydration:{table}",)
    )
    return json.loads(row["value"]) if row else None


def _old_table(path: Path, table: str, omitted: set[str]) -> None:
    spec = MIRROR_TABLES["chat"][table]
    columns = [name for name in spec["columns"] if name not in omitted]
    definitions = [f'"{name}" TEXT' for name in columns]
    definitions[columns.index("id")] = '"id" TEXT PRIMARY KEY'
    raw = sqlite3.connect(path)
    raw.execute(f'CREATE TABLE "{table}" ({", ".join(definitions)})')
    raw.execute(
        f'INSERT INTO "{table}" (id, updated_at) VALUES (?, ?)',
        (f"{table}-1", "2026-09-14T00:00:00Z"),
    )
    raw.commit()
    raw.close()


def test_fresh_mirror_has_no_hydration_marker(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = await aiosqlite.connect(str(tmp_path / "main.db"))
        try:
            await mirror_module.attach_and_ensure_mirror(db, tmp_path / "main.db")
            rows = await (
                await db.execute(
                    'SELECT key FROM chat._mirror_meta WHERE key LIKE "column_hydration:%"'
                )
            ).fetchall()
            assert rows == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_existing_column_upgrade_adds_the_column_before_its_generated_index() -> None:
    async def scenario() -> None:
        db = await aiosqlite.connect(":memory:")
        try:
            await db.execute("ATTACH DATABASE ':memory:' AS chat")
            await db.execute('CREATE TABLE chat._mirror_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            await db.execute('CREATE TABLE chat.upgrade_probe (id TEXT PRIMARY KEY)')
            await db.execute("INSERT INTO chat.upgrade_probe (id) VALUES ('row-1')")
            spec = {
                "columns": {"id": "TEXT", "new_cloud_field": "TEXT"},
                "create_sql": 'CREATE TABLE IF NOT EXISTS chat.upgrade_probe (id TEXT PRIMARY KEY, new_cloud_field TEXT)',
                "index_sql": ['CREATE INDEX IF NOT EXISTS chat.idx_upgrade_probe_new_cloud_field ON upgrade_probe (new_cloud_field)'],
            }
            await mirror_module._ensure_schema_tables(db, "chat", {"upgrade_probe": spec})
            columns = await (await db.execute('PRAGMA chat.table_info("upgrade_probe")')).fetchall()
            assert {row[1] for row in columns} == {"id", "new_cloud_field"}
            index = await (
                await db.execute(
                    "SELECT 1 FROM chat.sqlite_master WHERE type='index' AND name=?",
                    ("idx_upgrade_probe_new_cloud_field",),
                )
            ).fetchone()
            assert index is not None
        finally:
            await db.close()

    asyncio.run(scenario())


def test_existing_sr10_rows_are_marked_before_columns_are_added(tmp_path: Path) -> None:
    async def scenario() -> None:
        mirror_dir = tmp_path / "mirror"
        mirror_dir.mkdir()
        sr10_columns = {
            "agent_plan": {"deleted_at"},
            "agent_task": {"deleted_at"},
            "code_edit": {"deleted_at"},
            "code_message_file": {"deleted_at"},
            "observational_memory_event": {"deleted_at"},
            "pending_injection": {"deleted_at"},
            "request_snapshot": {
                "deleted_at",
                "pinned_at",
                "pin_reason",
                "agent_definition_version",
                "workflow_definition_version",
            },
            "tool_trace": {"deleted_at"},
            "user_todo": {"deleted_at"},
        }
        for table, columns in sr10_columns.items():
            _old_table(mirror_dir / "chat.db", table, columns)

        db = await aiosqlite.connect(str(tmp_path / "main.db"))
        try:
            await mirror_module.attach_and_ensure_mirror(db, tmp_path / "main.db")
            rows = await (
                await db.execute(
                    'SELECT key, value FROM chat._mirror_meta '
                    'WHERE key LIKE "column_hydration:%" ORDER BY key'
                )
            ).fetchall()
            markers = {row[0]: json.loads(row[1]) for row in rows}
            assert {
                key.removeprefix("column_hydration:"): set(value["columns"])
                for key, value in markers.items()
            } == sr10_columns
            assert await (
                await db.execute(
                    'SELECT 1 FROM chat._mirror_meta WHERE key=?',
                    ("column_hydration_bootstrap:sr10-v1",),
                )
            ).fetchone()
        finally:
            await db.close()

    asyncio.run(scenario())


def test_current_populated_sr10_mirror_bootstraps_all_historical_fields(tmp_path: Path) -> None:
    """An installed mirror can already have the columns but lack their old values."""
    expected = {
        "agent_plan": {"deleted_at"},
        "agent_task": {"deleted_at"},
        "code_edit": {"deleted_at"},
        "code_message_file": {"deleted_at"},
        "observational_memory_event": {"deleted_at"},
        "pending_injection": {"deleted_at"},
        "request_snapshot": {
            "deleted_at",
            "pinned_at",
            "pin_reason",
            "agent_definition_version",
            "workflow_definition_version",
        },
        "tool_trace": {"deleted_at"},
        "user_todo": {"deleted_at"},
    }

    async def scenario() -> None:
        main_db_path = tmp_path / "main.db"
        first = await aiosqlite.connect(str(main_db_path))
        try:
            await mirror_module.attach_and_ensure_mirror(first, main_db_path)
            for table in expected:
                await first.execute(
                    f'INSERT INTO chat."{table}" (id, updated_at) VALUES (?, ?)',
                    (f"{table}-1", "2026-09-14T00:00:00Z"),
                )
            await first.execute(
                'DELETE FROM chat._mirror_meta WHERE key=?',
                ("column_hydration_bootstrap:sr10-v1",),
            )
            await first.commit()
        finally:
            await first.close()

        reopened = await aiosqlite.connect(str(main_db_path))
        try:
            await mirror_module.attach_and_ensure_mirror(reopened, main_db_path)
            rows = await (
                await reopened.execute(
                    'SELECT key, value FROM chat._mirror_meta '
                    'WHERE key LIKE "column_hydration:%" ORDER BY key'
                )
            ).fetchall()
            markers = {
                key.removeprefix("column_hydration:"): set(json.loads(value)["columns"])
                for key, value in rows
            }
            assert markers == expected
            assert "coding_session" not in markers
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_equal_timestamp_hydration_writes_only_marked_columns_and_null(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        table = "request_snapshot"
        row_id = "snapshot-1"
        stamp = "2026-09-14T00:00:00Z"
        await db.execute(
            'INSERT INTO chat.request_snapshot (id, updated_at, provider) VALUES (?, ?, ?)',
            (row_id, stamp, "local-provider"),
        )
        await _mark(db, table, ["deleted_at", "pinned_at"])
        engine = ChatSyncEngine()
        calls = 0

        async def page(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return [{"id": row_id, "updated_at": "2026-09-14T00:00:00+00:00", "deleted_at": None, "pinned_at": "2026-09-15T00:00:00+00:00", "provider": "cloud-provider"}]
            return []

        engine._client.get_rows_since = page  # type: ignore[method-assign]
        await engine._hydrate_marked_columns(table, MIRROR_TABLES["chat"][table], "updated_at")
        row = await db.fetchone(
            'SELECT deleted_at, pinned_at, provider FROM chat.request_snapshot WHERE id=?', (row_id,)
        )
        assert dict(row) == {
            "deleted_at": None,
            "pinned_at": "2026-09-15T00:00:00+00:00",
            "provider": "local-provider",
        }
        assert await _marker(db, table) is None

    _run(tmp_path, scenario)


def test_pending_hydration_restarts_without_overwriting_local_row(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        table = "tool_trace"
        row_id = "trace-1"
        await db.execute(
            'INSERT INTO chat.tool_trace (id, updated_at, deleted_at) VALUES (?, ?, ?)',
            (row_id, "2026-09-14T00:00:00Z", None),
        )
        await db.execute(
            "INSERT INTO sync_queue (entity_type, entity_id, action, payload) VALUES (?, ?, 'upsert', '{}')",
            ("chat.tool_trace", row_id),
        )
        await _mark(db, table, ["deleted_at"])
        engine = ChatSyncEngine()
        calls = 0

        async def page(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return ([{"id": row_id, "updated_at": "2026-09-14T00:00:00+00:00", "deleted_at": "2026-09-15T00:00:00+00:00"}] if calls == 1 else [])

        engine._client.get_rows_since = page  # type: ignore[method-assign]
        await engine._hydrate_marked_columns(table, MIRROR_TABLES["chat"][table], "updated_at")
        row = await db.fetchone('SELECT deleted_at FROM chat.tool_trace WHERE id=?', (row_id,))
        assert row["deleted_at"] is None
        assert await _marker(db, table) == {"columns": ["deleted_at"], "deferred": False, "missing": False}
        cursor = await db.fetchone(
            'SELECT value FROM chat._mirror_meta WHERE key=?', ("column_hydration_cursor:tool_trace",)
        )
        assert cursor is None

    _run(tmp_path, scenario)


def test_missing_remote_field_restarts_the_marker_and_newer_local_row_does_not(tmp_path: Path) -> None:
    async def scenario(db: LocalDatabase) -> None:
        table = "tool_trace"
        await db.execute(
            'INSERT INTO chat.tool_trace (id, updated_at) VALUES (?, ?)',
            ("missing-1", "2026-09-14T00:00:00Z"),
        )
        await _mark(db, table, ["deleted_at"])
        engine = ChatSyncEngine()
        calls = 0

        async def missing_then_present(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return [{"id": "missing-1", "updated_at": "2026-09-14T00:00:00+00:00"}]
            if calls == 2:
                return [{"id": "missing-1", "updated_at": "2026-09-14T00:00:00+00:00", "deleted_at": None}]
            return []

        engine._client.get_rows_since = missing_then_present  # type: ignore[method-assign]
        await engine._hydrate_marked_columns(table, MIRROR_TABLES["chat"][table], "updated_at")
        assert await _marker(db, table) == {"columns": ["deleted_at"], "missing": False}
        await engine._hydrate_marked_columns(table, MIRROR_TABLES["chat"][table], "updated_at")
        assert await _marker(db, table) is None

        await db.execute(
            'INSERT INTO chat.tool_trace (id, updated_at) VALUES (?, ?)',
            ("newer-1", "2026-09-15T00:00:00Z"),
        )
        await _mark(db, table, ["deleted_at"])

        async def older_remote(*_args, **_kwargs):
            return [{"id": "newer-1", "updated_at": "2026-09-14T00:00:00+00:00", "deleted_at": None}]

        engine._client.get_rows_since = older_remote  # type: ignore[method-assign]
        await engine._hydrate_marked_columns(table, MIRROR_TABLES["chat"][table], "updated_at")
        assert await _marker(db, table) is None

    _run(tmp_path, scenario)


def test_hydration_resumes_after_a_page_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_engine_module, "_PULL_PAGE_SIZE", 1)

    async def scenario(db: LocalDatabase) -> None:
        table = "tool_trace"
        for row_id, stamp in (("trace-1", "2026-09-14T00:00:00Z"), ("trace-2", "2026-09-14T00:00:01Z")):
            await db.execute(
                'INSERT INTO chat.tool_trace (id, updated_at) VALUES (?, ?)', (row_id, stamp)
            )
        await _mark(db, table, ["deleted_at"])
        engine = ChatSyncEngine()
        requests: list[tuple[str | None, str | None]] = []

        async def interrupted(*_args, **kwargs):
            requests.append((kwargs["cursor_ts"], kwargs["cursor_id"]))
            if len(requests) == 1:
                return [{"id": "trace-1", "updated_at": "2026-09-14T00:00:00+00:00", "deleted_at": None}]
            raise ChatSyncHTTPError("GET", table, 503, "temporary")

        engine._client.get_rows_since = interrupted  # type: ignore[method-assign]
        with pytest.raises(ChatSyncHTTPError):
            await engine._hydrate_marked_columns(table, MIRROR_TABLES["chat"][table], "updated_at")

        async def resumed(*_args, **kwargs):
            requests.append((kwargs["cursor_ts"], kwargs["cursor_id"]))
            if kwargs["cursor_id"] == "trace-1":
                return [{"id": "trace-2", "updated_at": "2026-09-14T00:00:01+00:00", "deleted_at": None}]
            return []

        engine._client.get_rows_since = resumed  # type: ignore[method-assign]
        await engine._hydrate_marked_columns(table, MIRROR_TABLES["chat"][table], "updated_at")
        assert requests[2] == ("2026-09-14T00:00:00+00:00", "trace-1")
        assert await _marker(db, table) is None

    _run(tmp_path, scenario)
