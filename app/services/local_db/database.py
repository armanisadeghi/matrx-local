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

from app.config import LOCAL_DB_PATH
from app.common.system_logger import get_logger
from app.services.local_db.mirror import attach_and_ensure_mirror
from app.services.local_db.schema import MIGRATIONS
from app.services.local_db.write_gate import write_gate

logger = get_logger()

_instance: Optional["LocalDatabase"] = None


class LocalDatabase:
    """Async SQLite wrapper with migration support."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or LOCAL_DB_PATH
        self._db: Optional[aiosqlite.Connection] = None
        # The write-gate hold covering this connection's open transaction, and
        # whether a statement has actually opened one under it yet.
        self._gate = None
        self._gate_armed = False

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
        # Wait for a competing writer instead of erroring with SQLITE_BUSY
        # (background sync loop vs request handlers vs the coding-session
        # durable-ack connections all target this one file). 5s proved too
        # short on 2026-08-30 — first-boot sync bursts held the write lock
        # long enough that ASGI handlers and sync ticks crashed with
        # "database is locked". 15s matches the durable-write connections
        # (_DURABLE_WRITE_BUSY_TIMEOUT_MS in coding_sessions/service.py).
        await self._db.execute("PRAGMA busy_timeout=15000")
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
        self._release_write_gate()
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

    # ── The write gate on the SHARED connection ──────────────────────────
    #
    # 🚨 THIS CONNECTION IS THE OTHER HALF OF THE RACE (fixed 2026-09-14).
    # `write_gate.py` was written to serialize this app's writers against the
    # coding-session bridge's BEGIN IMMEDIATE connections. Only the bridge side
    # ever took it; ~70 write call sites reached SQLite through the three
    # methods below and took nothing, so SQLite kept arbitrating and kept
    # handing the loser `database is locked` — 328 tracebacks and 48
    # SQLITE_BUSY_SNAPSHOT in the 72h to 2026-09-14.
    #
    # Gating it HERE, once, is what brings those ~70 writers under the gate
    # without touching them. It is held for the whole implicit transaction
    # (first write → COMMIT/ROLLBACK), not per statement: a per-statement hold
    # would release while this connection still owned SQLite's write lock,
    # which is the same race with extra steps. The gate is reentrant per task,
    # so the `async with write_gate():` blocks that call these methods are
    # unaffected.
    _WRITE_VERBS = (
        "INSERT", "UPDATE", "DELETE", "REPLACE", "UPSERT",
        "CREATE", "DROP", "ALTER", "TRUNCATE", "VACUUM", "BEGIN",
    )

    @classmethod
    def _is_write(cls, sql: str) -> bool:
        head = sql.lstrip().lstrip("(").lstrip()[:9].upper()
        return any(head.startswith(verb) for verb in cls._WRITE_VERBS)

    async def _hold_write_gate(self, sql: str) -> None:
        """Take the gate before the first write of a transaction."""
        if self._gate is not None or not self._is_write(sql):
            return
        hold = write_gate()
        self._gate_armed = False
        await hold.acquire(stale_check=self._gate_is_stale)
        self._gate = hold

    def _gate_is_stale(self) -> bool:
        """True once this connection's transaction is over by ANY route.

        Callers can end it without passing through ``commit()`` — a raw
        ``connection.rollback()`` does, and tests do exactly that. The hold
        then guards nothing, and the next writer should not wait for it.
        """
        return (
            self._gate_armed
            and self._db is not None
            and not self._db.in_transaction
        )

    def _release_write_gate(self) -> None:
        self._gate_armed = False
        hold, self._gate = self._gate, None
        if hold is not None:
            hold.release()

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        await self._hold_write_gate(sql)
        try:
            cursor = await self.db.execute(sql, params)
        except sqlite3.OperationalError as exc:
            await self._discard_transaction_after(exc, sql)
            # A rolled-back or never-opened transaction holds no write lock,
            # so the gate goes on to the next writer instead of waiting out
            # its bounded timeout. A transaction still open (a non-lock error
            # mid-write) keeps it, because this connection still owns SQLite's
            # write lock.
            self._release_gate_if_no_transaction()
            raise
        except BaseException:
            self._release_gate_if_no_transaction()
            raise
        self._release_gate_if_no_transaction()
        return cursor

    async def executemany(self, sql: str, params_seq) -> aiosqlite.Cursor:
        await self._hold_write_gate(sql)
        try:
            cursor = await self.db.executemany(sql, params_seq)
        except sqlite3.OperationalError as exc:
            await self._discard_transaction_after(exc, sql)
            # A rolled-back or never-opened transaction holds no write lock,
            # so the gate goes on to the next writer instead of waiting out
            # its bounded timeout. A transaction still open (a non-lock error
            # mid-write) keeps it, because this connection still owns SQLite's
            # write lock.
            self._release_gate_if_no_transaction()
            raise
        except BaseException:
            self._release_gate_if_no_transaction()
            raise
        self._release_gate_if_no_transaction()
        return cursor

    def _release_gate_if_no_transaction(self) -> None:
        """A statement that left no open transaction (DDL, a PRAGMA, an
        autocommit write) has nothing left to protect — hand the gate on
        immediately instead of waiting for a COMMIT that will never come."""
        db = self._db
        if self._gate is None:
            return
        if db is not None and db.in_transaction:
            self._gate_armed = True
            return
        self._release_write_gate()

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(sql, params)
        return await cursor.fetchall()

    async def commit(self) -> None:
        try:
            await self.db.commit()
        except sqlite3.OperationalError as exc:
            await self._discard_transaction_after(exc, "COMMIT")
            raise
        finally:
            # The transaction is over either way; never keep the gate past it.
            self._release_write_gate()

    # Extended result codes meaning "another connection holds or held the
    # lock". The busy handler covers plain BUSY; BUSY_SNAPSHOT is the one
    # SQLite raises INSTANTLY, without consulting busy_timeout, when THIS
    # connection's read snapshot went stale inside a still-open transaction.
    _LOCK_ERROR_NAMES = frozenset({
        "SQLITE_BUSY", "SQLITE_BUSY_SNAPSHOT", "SQLITE_BUSY_RECOVERY",
        "SQLITE_BUSY_TIMEOUT", "SQLITE_LOCKED", "SQLITE_LOCKED_SHAREDCACHE",
    })

    async def _discard_transaction_after(
        self, exc: sqlite3.OperationalError, sql: str
    ) -> None:
        """Roll back the implicit transaction a failed write left open.

        Why (measured 2026-09-12 on 1.4.86): Python's sqlite3 begins a
        transaction before the first write and does NOT close it when that
        write raises. After one lost lock race against the hook bridge's
        BEGIN IMMEDIATE connections during the startup burst, this connection
        stayed inside that transaction. Every later read then pinned a WAL
        snapshot (a passive checkpoint sat at 147 pages while the log grew),
        the next hook commit made the snapshot stale, and every write for the
        rest of the process died in 0.00s with SQLITE_BUSY_SNAPSHOT: 18
        failures across 10 callers -- catalog/tools sync, sync status, the
        token save, the scraper store, the capture reconciler, the history
        scan, the pin reconciler -- while an outside connection acquired the
        write lock in 2 ms.

        The caller still sees its original error. What changes is that the
        NEXT writer is not doomed by it. Guard (fails without this):
        tests/unit/test_local_db_lock_race_rollback.py
        """
        name = getattr(exc, "sqlite_errorname", None)
        if name not in self._LOCK_ERROR_NAMES and "locked" not in str(exc).lower():
            return
        db = self._db
        if db is None or not db.in_transaction:
            return
        head = " ".join(sql.split())[:80]
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 -- the caller's original error is the one that matters
            logger.error(
                "[local_db] %s on %r and the rollback ALSO failed; later writes on "
                "this connection may fail instantly (SQLITE_BUSY_SNAPSHOT) until restart",
                name or exc, head, exc_info=True,
            )
            return
        logger.warning(
            "[local_db] %s on %r -- open transaction rolled back so this connection "
            "is not poisoned; the caller sees the original error",
            name or exc, head,
        )
        # The transaction this gate was covering is gone with the rollback.
        self._release_write_gate()

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
