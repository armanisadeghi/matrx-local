"""A corrupt mirror file must be quarantined and rebuilt, never fatal.

2026-08-30: a corrupted ``chat.db`` ("database disk image is malformed") made
every chat_sync tick crash forever with no recovery path. The mirror is a
replica of the cloud schemas, so the correct behavior is: quarantine the
unreadable file at attach time, rebuild it empty, and let sync re-pull.
A transient lock or unrelated error must NOT trigger the rebuild.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite

from app.services.local_db import mirror as mirror_module


def _garbage_db(path: Path) -> None:
    path.write_bytes(b"this is definitely not a sqlite database" * 40)


def test_corrupt_mirror_file_is_quarantined_and_rebuilt(tmp_path: Path) -> None:
    async def scenario() -> None:
        file_path = tmp_path / "chat.db"
        _garbage_db(file_path)

        db = await aiosqlite.connect(str(tmp_path / "main.db"))
        try:
            await mirror_module._attach_schema_with_corruption_recovery(
                db, "chat", file_path
            )
            # The schema is attached and usable.
            cursor = await db.execute('PRAGMA "chat".quick_check(1)')
            row = await cursor.fetchone()
            assert row is not None and str(row[0]).lower() == "ok"
        finally:
            await db.close()

        quarantined = list(tmp_path.glob("chat.corrupt-*.db"))
        assert len(quarantined) == 1, "corrupt file must be kept for forensics"
        assert file_path.exists(), "a fresh mirror file must replace it"

    asyncio.run(scenario())


def test_healthy_mirror_file_is_left_alone(tmp_path: Path) -> None:
    async def scenario() -> None:
        file_path = tmp_path / "chat.db"
        seed = sqlite3.connect(str(file_path))
        seed.execute("CREATE TABLE conversation (id TEXT PRIMARY KEY)")
        seed.execute("INSERT INTO conversation (id) VALUES ('keep-me')")
        seed.commit()
        seed.close()

        db = await aiosqlite.connect(str(tmp_path / "main.db"))
        try:
            await mirror_module._attach_schema_with_corruption_recovery(
                db, "chat", file_path
            )
            cursor = await db.execute('SELECT id FROM "chat".conversation')
            rows = await cursor.fetchall()
            assert [r[0] for r in rows] == ["keep-me"]
        finally:
            await db.close()

        assert not list(tmp_path.glob("chat.corrupt-*.db"))

    asyncio.run(scenario())


def test_corruption_marker_matching_is_narrow() -> None:
    assert mirror_module._is_corruption(
        sqlite3.DatabaseError("database disk image is malformed")
    )
    assert mirror_module._is_corruption(
        sqlite3.DatabaseError("file is not a database")
    )
    assert not mirror_module._is_corruption(
        sqlite3.OperationalError("database is locked")
    )
