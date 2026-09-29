"""Access-ladder T-13: this client never reads or writes the retiring row column.

The cloud retires the row column into ``shown_to`` (a list filter) and ``published_to_web``
(the only anonymous lane) — common-docs projects/access-ladder/t13/PLAN.md phase 5. Phase 7
drops the column from the cloud, and it can only do that once every shipped client has stopped
touching it. For this app that means: the generated SQLite mirror creates, pulls and pushes no
such column, an older local file that already has one keeps it quietly (never destroyed, never
drift noise), and the file upload never sends it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from app.services.local_db import mirror as mirror_module
from app.services.local_db.mirror_schema import MIRROR_TABLES, WITHHELD_MIRROR_COLUMNS

_LEDGER = Path(__file__).resolve().parents[2] / "schema_mirror" / "withheld_columns.json"
(ROW_COLUMN,) = json.loads(_LEDGER.read_text())["columns"]


class _CapturingLogger:
    def __init__(self) -> None:
        self.errors: list[tuple[Any, ...]] = []

    def error(self, *args: Any, **kwargs: Any) -> None:
        self.errors.append(args)

    def warning(self, *args: Any, **kwargs: Any) -> None:
        pass


def test_no_mirrored_table_creates_or_syncs_the_row_column() -> None:
    carrying = [
        f"{schema}.{table}"
        for schema, tables in MIRROR_TABLES.items()
        for table, spec in tables.items()
        if ROW_COLUMN in spec["columns"]
        or ROW_COLUMN in spec["pg_types"]
        or f'"{ROW_COLUMN}"' in spec["create_sql"]
    ]
    assert carrying == []


def test_row_access_columns_are_mirrored_where_the_cloud_has_them() -> None:
    for table in ("files", "folders"):
        cols = MIRROR_TABLES["files"][table]["pg_types"]
        assert cols.get("published_to_web") == "bool"
        assert "shown_to" in cols
    assert WITHHELD_MIRROR_COLUMNS["files"]["files"] == [ROW_COLUMN]


def test_push_allowlists_never_name_the_row_column() -> None:
    from app.services.chat_sync.engine import _PUSH_COLUMNS

    assert [t for t, cols in _PUSH_COLUMNS.items() if ROW_COLUMN in cols] == []


def test_file_upload_never_sends_the_row_column() -> None:
    from app.services.matrx_files.client import MatrxFilesClient

    assert ROW_COLUMN not in inspect.signature(MatrxFilesClient.upload).parameters
    assert f'"{ROW_COLUMN}"' not in inspect.getsource(MatrxFilesClient.upload)


def test_an_older_local_file_keeps_its_row_column_without_drift_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        db = await aiosqlite.connect(":memory:")
        try:
            await db.execute("ATTACH DATABASE ':memory:' AS chat")
            await db.execute(
                f'CREATE TABLE "chat"."conversation" ("id" TEXT, "{ROW_COLUMN}" TEXT)'
            )
            logger = _CapturingLogger()
            monkeypatch.setattr(mirror_module, "logger", logger)
            spec = {
                "columns": {"id": "TEXT"},
                "create_sql": 'CREATE TABLE IF NOT EXISTS "chat"."conversation" ("id" TEXT)',
                "index_sql": [],
            }
            retained = await mirror_module._ensure_schema_tables(
                db, "chat", {"conversation": spec}
            )
            assert retained == [f"chat.conversation.{ROW_COLUMN}"]
            assert logger.errors == []
        finally:
            await db.close()

    asyncio.run(scenario())
