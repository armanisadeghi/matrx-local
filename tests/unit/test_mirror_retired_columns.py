"""Regression coverage for non-destructive cloud-column retirement.

The structural mirror is additive because app upgrades must never destroy a
user's local data. When the cloud drops a column, an older SQLite mirror keeps
it forever; known retirements are safe local history, while an unknown extra
column remains a loud drift error.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from app.services.local_db import mirror as mirror_module
from app.services.local_db.mirror_schema import RETIRED_MIRROR_COLUMNS


REPO_ROOT = Path(__file__).resolve().parents[2]


class _CapturingLogger:
    def __init__(self) -> None:
        self.errors: list[tuple[Any, ...]] = []

    def error(self, *args: Any, **kwargs: Any) -> None:
        self.errors.append(args)

    def warning(self, *args: Any, **kwargs: Any) -> None:
        pass


def _spec(table: str) -> dict[str, Any]:
    return {
        "columns": {"id": "TEXT"},
        "create_sql": (
            f'CREATE TABLE IF NOT EXISTS "chat"."{table}" ("id" TEXT)'
        ),
        "index_sql": [],
    }


def test_retirement_ledger_matches_generated_contract() -> None:
    ledger = json.loads(
        (REPO_ROOT / "schema_mirror" / "retired_columns.json").read_text()
    )
    assert RETIRED_MIRROR_COLUMNS == ledger


def test_known_chat_cut_columns_are_preserved_without_drift_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        db = await aiosqlite.connect(":memory:")
        try:
            await db.execute("ATTACH DATABASE ':memory:' AS chat")
            tables = {
                "agent_memory": "user_id",
                "agent_run": "user_id",
                "conversation": "project_id",
                "observational_memory": "user_id",
                "user_request": "user_id",
            }
            for table, retired_column in tables.items():
                await db.execute(
                    f'CREATE TABLE "chat"."{table}" '
                    f'("id" TEXT, "{retired_column}" TEXT)'
                )

            logger = _CapturingLogger()
            monkeypatch.setattr(mirror_module, "logger", logger)
            retained = await mirror_module._ensure_schema_tables(
                db,
                "chat",
                {table: _spec(table) for table in tables},
            )

            assert sorted(retained) == sorted(
                f"chat.{table}.{column}" for table, column in tables.items()
            )
            assert logger.errors == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_unknown_extra_column_remains_loud_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        db = await aiosqlite.connect(":memory:")
        try:
            await db.execute("ATTACH DATABASE ':memory:' AS chat")
            await db.execute(
                'CREATE TABLE "chat"."conversation" '
                '("id" TEXT, "local_debug_column" TEXT)'
            )
            logger = _CapturingLogger()
            monkeypatch.setattr(mirror_module, "logger", logger)

            retained = await mirror_module._ensure_schema_tables(
                db, "chat", {"conversation": _spec("conversation")}
            )

            assert retained == []
            assert len(logger.errors) == 1
            assert "unknown local column" in logger.errors[0][0]
            assert logger.errors[0][-1] == "local_debug_column"
        finally:
            await db.close()

    asyncio.run(scenario())
