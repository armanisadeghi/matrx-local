"""Guard: the write gate covers BOTH sides of the race it was built for.

`app/services/local_db/write_gate.py` exists to serialize this process's
SQLite writers so SQLite is never asked to arbitrate a lock one of them must
lose. Its own docstring names the two parties: the coding-session bridge's
`BEGIN IMMEDIATE` connections, and "the shared application connection".

Only the first party ever took it. The shared connection — reached through
`LocalDatabase.execute`/`executemany`/`commit` by roughly 70 write call sites
across repositories.py, outbox.py, scrape_store.py, artifacts/service.py,
chat_sync/engine.py, file_sync/engine.py + index.py, delegation/outbox.py,
ai/conversation_handler.py and downloads/manager.py — took nothing. A mutex
one of two parties honours is not a mutex: 328 `database is locked` tracebacks
and 48 `SQLITE_BUSY_SNAPSHOT` in the 72h to 2026-09-14.

These guards hold the seam (one place, not 70) and the properties that seam
needs to be safe: reentrancy for the already-gated callers, and a bounded,
self-healing wait so a writer that never commits can never hang the app.

This suite EXTENDS test_local_db_lock_race_rollback.py, which guards what
happens after a race is lost. This one guards the race not happening.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.local_db import write_gate as write_gate_module
from app.services.local_db.database import LocalDatabase
from app.services.local_db.write_gate import write_gate


@pytest.fixture
async def db(tmp_path) -> LocalDatabase:
    database = LocalDatabase(tmp_path / "matrx.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


# ── The seam: the shared connection is inside the gate ──────────────────────


@pytest.mark.anyio
async def test_a_shared_connection_write_holds_the_gate_until_it_commits(
    db: LocalDatabase,
) -> None:
    """THE defect. A bridge-style writer must wait for the shared connection's
    open transaction instead of racing it into SQLite."""
    await db.execute("CREATE TABLE gate_probe (x INTEGER)")
    await db.commit()

    await db.execute("INSERT INTO gate_probe (x) VALUES (1)")  # opens a tx

    entered = asyncio.Event()

    async def bridge_style_writer() -> None:
        async with write_gate():
            entered.set()

    waiter = asyncio.create_task(bridge_style_writer())
    await asyncio.sleep(0.15)
    assert not entered.is_set(), (
        "the shared connection held an open write transaction and another "
        "writer walked straight into SQLite — this is SR-06"
    )

    await db.commit()
    await asyncio.wait_for(waiter, timeout=2)
    assert entered.is_set()


@pytest.mark.anyio
async def test_the_gate_is_handed_on_the_moment_the_transaction_ends(
    db: LocalDatabase,
) -> None:
    """Held for the transaction, not for the process: a committed write must
    not leave the next writer waiting."""
    await db.execute("CREATE TABLE gate_probe (x INTEGER)")
    await db.commit()
    await db.execute("INSERT INTO gate_probe (x) VALUES (1)")
    await db.commit()

    async with asyncio.timeout(1):
        async with write_gate():
            pass


@pytest.mark.anyio
async def test_a_statement_that_opens_no_transaction_keeps_nothing(
    db: LocalDatabase,
) -> None:
    """DDL and PRAGMAs autocommit — waiting on a COMMIT that never comes would
    be a hang the gate itself caused."""
    await db.execute("CREATE TABLE autocommit_probe (x INTEGER)")

    async with asyncio.timeout(1):
        async with write_gate():
            pass


@pytest.mark.anyio
async def test_a_read_never_takes_the_gate(db: LocalDatabase) -> None:
    """WAL keeps readers concurrent with a writer; gating reads would
    serialize the whole app for nothing."""
    await db.execute("CREATE TABLE read_probe (x INTEGER)")
    await db.commit()

    async with write_gate():
        async with asyncio.timeout(1):
            assert await db.fetchall("SELECT x FROM read_probe") == []


# ── The properties that seam needs ──────────────────────────────────────────


@pytest.mark.anyio
async def test_the_gate_is_reentrant_for_the_task_that_holds_it(
    db: LocalDatabase,
) -> None:
    """The 22 already-gated call sites call LocalDatabase.execute from INSIDE
    `async with write_gate():`. With a plain asyncio.Lock at the seam that is
    an instant self-deadlock, which is why the gate is per-task reentrant."""
    async with asyncio.timeout(2):
        async with write_gate():
            await db.execute("CREATE TABLE reentrant_probe (x INTEGER)")
            await db.execute("INSERT INTO reentrant_probe (x) VALUES (1)")
            await db.commit()
            async with write_gate():
                await db.execute("INSERT INTO reentrant_probe (x) VALUES (2)")
                await db.commit()

    assert len(await db.fetchall("SELECT x FROM reentrant_probe")) == 2


@pytest.mark.anyio
async def test_a_different_task_still_waits(db: LocalDatabase) -> None:
    """Reentrancy is per TASK — it must not degrade the gate into a no-op."""
    order: list[str] = []

    async def writer(name: str) -> None:
        async with write_gate():
            order.append(f"{name}-in")
            await asyncio.sleep(0.05)
            order.append(f"{name}-out")

    await asyncio.gather(writer("a"), writer("b"))

    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    ), order


@pytest.mark.anyio
async def test_a_transaction_ended_by_a_raw_rollback_releases_the_gate(
    db: LocalDatabase,
) -> None:
    """A caller can leave the transaction without passing through commit().
    The hold then guards nothing and must not cost the next writer its whole
    bounded wait."""
    await db.execute("CREATE TABLE rollback_probe (x INTEGER)")
    await db.commit()
    await db.execute("INSERT INTO rollback_probe (x) VALUES (1)")

    entered = asyncio.Event()

    async def waiter() -> None:
        async with write_gate():
            entered.set()

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.1)
    assert not entered.is_set()

    await db.db.rollback()
    await asyncio.wait_for(task, timeout=2)
    assert entered.is_set()


@pytest.mark.anyio
async def test_the_wait_is_bounded_and_the_gate_reclaims_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A writer that never releases must never hang the app, and must never
    poison the gate for the rest of the process either — the waiter says so
    loudly and takes it back."""
    monkeypatch.setattr(write_gate_module, "GATE_WAIT_TIMEOUT_SECONDS", 0.2)
    errors: list[str] = []
    monkeypatch.setattr(
        write_gate_module.logger,
        "error",
        lambda t, *a: errors.append(t % a if a else t),
    )

    # In ANOTHER task: the gate is reentrant for its own holder, so a hold
    # taken here would simply be re-entered rather than waited for.
    holding = asyncio.Event()

    async def never_releases() -> None:
        async with write_gate():
            holding.set()
            await asyncio.sleep(30)

    stuck = asyncio.create_task(never_releases())
    await asyncio.wait_for(holding.wait(), timeout=2)

    async with asyncio.timeout(3):
        async with write_gate():
            pass
        # And the gate still works for everyone after the reclaim.
        async with write_gate():
            pass

    assert errors and "took it back" in errors[0]
    assert "write transaction nobody committed" in errors[0]
    stuck.cancel()


# ── The census: nobody gets to write outside the gate again ─────────────────


@pytest.mark.anyio
async def test_no_new_writer_opens_its_own_connection_to_the_local_db_ungated() -> None:
    """The class, not the instance: every file that opens its OWN aiosqlite
    connection onto matrx.db must take the gate, and every other writer must
    reach SQLite through LocalDatabase (which now takes it at the seam).

    This is the check that was missing. `write_gate` shipped with zero tests
    asserting anybody used it, so nothing noticed that the largest family of
    writers never did.
    """
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []

    for source in sorted(app_dir.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        # Only connections onto the main local DB — the filesystem index, the
        # snapshot cache and the read-only OS/provider DBs are other files.
        opens_local_db = any(
            marker in text
            for marker in (
                "aiosqlite.connect(str(self._db.path))",
                "aiosqlite.connect(str(self._database().path))",
                "aiosqlite.connect(str(db.path))",
            )
        )
        if not opens_local_db:
            continue
        if "write_gate" not in text:
            offenders.append(str(source.relative_to(app_dir.parent)))

    assert offenders == [], (
        "these files open their own write connection onto matrx.db without the "
        f"write gate, so they race every other writer: {offenders}"
    )


@pytest.mark.anyio
async def test_the_shared_connection_seam_still_takes_the_gate() -> None:
    """Read the wiring, not just the behaviour: if these three methods stop
    taking the gate, ~70 write call sites silently leave it again."""
    import inspect

    from app.services.local_db.database import LocalDatabase as Database

    for method in (Database.execute, Database.executemany):
        assert "_hold_write_gate" in inspect.getsource(method), method.__name__
    assert "_release_write_gate" in inspect.getsource(Database.commit)
