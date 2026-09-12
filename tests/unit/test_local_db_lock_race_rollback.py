"""Guard: a write that loses a lock race must not poison the shared connection.

Measured on a live machine 2026-09-12 (matrx-local 1.4.86). During the startup
sync burst one write on the shared ``LocalDatabase`` connection lost a genuine
``SQLITE_BUSY`` race to the hook bridge's own ``BEGIN IMMEDIATE`` connections.
Python's sqlite3 had already opened an implicit transaction for that write and
nothing rolled it back. Every later read on the connection then ran inside that
lingering transaction and pinned a WAL snapshot (a passive checkpoint stayed
stuck at exactly 147 pages while the log grew); the next commit by any hook made
the snapshot stale, and from then on EVERY write on the connection failed
instantly with ``SQLITE_BUSY_SNAPSHOT`` -- a code for which SQLite never
consults the busy timeout -- for the life of the process. Eighteen failures
across ten callers (catalog + tools sync, sync status, the token save, the
scraper store, the capture reconciler, the history scan, the pin reconciler)
while an outside connection acquired the write lock in 2 ms.

This test recreates that exact sequence. Without the rollback in
``LocalDatabase.execute`` the final write raises ``SQLITE_BUSY_SNAPSHOT``.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from app.services.local_db.database import LocalDatabase


@pytest.mark.anyio
async def test_lost_lock_race_does_not_poison_shared_connection(tmp_path, caplog) -> None:
    path = tmp_path / "matrx.db"
    db = LocalDatabase(path)
    await db.connect()
    try:
        # A short busy timeout makes the genuine race lose fast. The poisoning
        # that follows does not depend on the value: it is never consulted.
        await db.execute("PRAGMA busy_timeout=200")
        await db.execute("CREATE TABLE poison_probe (x INTEGER)")
        await db.commit()

        # The app logger never propagates to the root logger (system_logger
        # sets propagate=False), so listen to it directly.
        app_log = logging.getLogger("system_logger")
        app_log.addHandler(caplog.handler)
        hook = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
        try:
            # 1. A hook writer holds the write lock; the shared connection loses.
            hook.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.OperationalError):
                await db.execute("INSERT INTO poison_probe VALUES (1)")
            hook.execute("COMMIT")

            # 2. An ordinary read on the shared connection -- every overview
            #    and reconciler pass does this ...
            await db.fetchone("SELECT count(*) FROM poison_probe")
            # 3. ... while a hook commits on its own connection.
            hook.execute("INSERT INTO poison_probe VALUES (99)")

            # 4. Nobody holds any lock now. This write must succeed.
            await db.execute("INSERT INTO poison_probe VALUES (2)")
            await db.commit()
        finally:
            hook.close()
            app_log.removeHandler(caplog.handler)

        assert not db.db.in_transaction
        row = await db.fetchone("SELECT count(*) FROM poison_probe")
        assert row[0] == 2
        # Nothing silent: the discarded transaction is announced.
        assert any("rolled back" in r.getMessage() for r in caplog.records), (
            "the rollback after a lock error must be logged"
        )
    finally:
        await db.close()
