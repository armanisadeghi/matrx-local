"""SQLite connection manager with automatic schema migrations.

The database file lives at ``~/.matrx/matrx.db`` (configurable via
``MATRX_LOCAL_DB`` env var) so it survives app reinstalls and updates.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Optional

import aiosqlite

from app.config import LOCAL_DB_PATH, MATRX_HOME_DIR
from app.common.system_logger import get_logger
from app.services.local_db.mirror import attach_and_ensure_mirror
from app.services.local_db.schema import MIGRATIONS

logger = get_logger()

_instance: Optional["LocalDatabase"] = None


class LocalDatabase:
    """Async SQLite wrapper with migration support."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or LOCAL_DB_PATH
        self._db: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the database and run any pending migrations."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The DB holds Supabase JWTs/refresh tokens and (base64) API keys.
        # Lock the directory + file to the owner so other local users can't
        # read the user's credentials. Best-effort: chmod is a no-op / not
        # meaningful on Windows, so we only enforce it on POSIX.
        if not sys.platform.startswith("win"):
            try:
                os.chmod(self.path.parent, stat.S_IRWXU)  # 0o700
            except OSError:
                logger.debug("[local_db] could not chmod data dir", exc_info=True)

        self._db = await aiosqlite.connect(str(self.path))

        if not sys.platform.startswith("win"):
            try:
                os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except OSError:
                logger.debug("[local_db] could not chmod db file", exc_info=True)

        # WAL mode for concurrent reads while writing
        await self._db.execute("PRAGMA journal_mode=WAL")
        # Wait up to 5s for a competing writer instead of erroring immediately
        # with SQLITE_BUSY (background sync loop vs request handlers race).
        await self._db.execute("PRAGMA busy_timeout=5000")
        # Foreign keys are off by default in SQLite
        await self._db.execute("PRAGMA foreign_keys=ON")
        # Sync less aggressively — we have WAL for crash safety
        await self._db.execute("PRAGMA synchronous=NORMAL")

        # Attach the structural mirror of the canonical cloud schemas
        # (chat.conversation etc. — see app/services/local_db/mirror.py).
        # MUST run before migrations: V10+ migration SQL references chat.*.
        await attach_and_ensure_mirror(self._db, self.path)

        await self._run_migrations()
        logger.info("[local_db] Connected to %s", self.path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("[local_db] Closed database connection")

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("LocalDatabase not connected — call await connect() first")
        return self._db

    # ------------------------------------------------------------------
    # Convenience: execute / fetch
    # ------------------------------------------------------------------

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        return await self.db.execute(sql, params)

    async def executemany(self, sql: str, params_seq) -> aiosqlite.Cursor:
        return await self.db.executemany(sql, params_seq)

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(sql, params)
        return await cursor.fetchall()

    async def commit(self) -> None:
        await self.db.commit()

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------

    async def _run_migrations(self) -> None:
        """Apply all pending migrations in order."""
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        await self.db.commit()

        cursor = await self.db.execute("SELECT MAX(version) FROM _migrations")
        row = await cursor.fetchone()
        current_version = row[0] if row and row[0] is not None else 0

        for version, sql in MIGRATIONS:
            if version <= current_version:
                continue
            logger.info("[local_db] Applying migration v%d ...", version)
            try:
                # Execute each statement in the migration
                for stmt in sql.split(";\n"):
                    stmt = stmt.strip()
                    if stmt:
                        await self.db.execute(stmt)
                await self.db.execute(
                    "INSERT INTO _migrations (version) VALUES (?)", (version,)
                )
                await self.db.commit()
            except Exception:
                # Roll back the partial migration so a later unrelated
                # commit() on this shared connection can't persist a
                # half-applied migration without its version row (which
                # would poison every subsequent boot).
                logger.error(
                    "[local_db] Migration v%d FAILED — rolling back", version,
                    exc_info=True,
                )
                try:
                    await self.db.rollback()
                except Exception:
                    logger.error("[local_db] rollback after failed migration also failed",
                                 exc_info=True)
                raise
            logger.info("[local_db] Migration v%d applied ✓", version)


def get_db() -> LocalDatabase:
    """Return the singleton LocalDatabase instance."""
    global _instance
    if _instance is None:
        _instance = LocalDatabase()
    return _instance
