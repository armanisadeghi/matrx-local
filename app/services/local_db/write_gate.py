"""One in-process gate for every SQLite write transaction on ``matrx.db``.

WHY THIS EXISTS
---------------
Observed on a live machine 2026-08-30: clicking "Preview session detail
changes" produced only ``TypeError: Load failed`` in the UI.  The engine log
told the real story — the request arrived, raised
``sqlite3.OperationalError: database is locked`` inside
``title_sync._start_operation``, and never produced a response.

The cause is contention this process creates against itself.  The coding-session
bridge deliberately commits each hook on its OWN short connection with
``BEGIN IMMEDIATE`` and ``synchronous=FULL`` so an unrelated coroutine on the
shared connection cannot roll its transaction back (see
``coding_sessions/FEATURE.md``).  That is correct for durability, but it takes
an exclusive, fsyncing write lock on the single database file.  On this machine
``POST /coding-session/hooks`` had fired 54,449 times: under a normal Claude
Code workload the hook stream is close to continuous, and any *other* writer —
the shared application connection included — loses the race and exhausts its
``busy_timeout``.

Raising the timeout only converts a fast failure into a slow one; the writers
still queue inside SQLite, where the loser gets an exception rather than a turn.
Every writer here lives in ONE process, so the contention can simply be removed:
serialize write transactions in Python, and SQLite is never asked to arbitrate.

WHAT IT DOES AND DOES NOT COVER
-------------------------------
* Acquire it around a **write transaction** — the whole ``BEGIN IMMEDIATE`` →
  ``COMMIT`` span, never a sub-step, or two holders can interleave.
* **Reads do not take it.** WAL keeps readers concurrent with a writer, so
  gating reads would serialize the app for no benefit.
* It is a within-process gate only. ``busy_timeout`` stays configured on every
  connection as the backstop for a genuine second process (a CLI, a test run,
  an editor with the file open).

WHO ACTUALLY TAKES IT (fixed 2026-09-14)
---------------------------------------
🚨 For two weeks this gate could not do the job described above, because only
ONE SIDE of the contention it names ever took it. The 22 call sites in
``coding_sessions/`` — the BEGIN IMMEDIATE connections — took it. The shared
application connection, named above as the writer that "loses the race", did
not: ~70 write call sites across repositories.py, outbox.py, scrape_store.py,
artifacts/service.py, chat_sync/engine.py, file_sync/engine.py and index.py,
delegation/outbox.py, ai/conversation_handler.py and downloads/manager.py went
straight through ``LocalDatabase.execute``/``commit``, which took no gate at
all. Two parties with one of them gated is not mutual exclusion — so SQLite
still arbitrated and still handed the loser an exception: 328 ``database is
locked`` tracebacks plus 48 ``SQLITE_BUSY_SNAPSHOT`` in the 72h to 2026-09-14.

The fix is at the seam, not at the 70 call sites: ``LocalDatabase`` itself now
holds this gate for the whole span of its implicit write transaction (first
write → COMMIT/ROLLBACK). That required the gate to become REENTRANT PER TASK,
because the gated ``coding_sessions`` blocks call ``LocalDatabase.execute``
from inside their own ``async with write_gate():`` — with a plain
``asyncio.Lock`` that is an instant self-deadlock.

Two consequences worth knowing:

* Reentrancy is per TASK, not per call: a nested acquire by the same task is
  free, a different task waits. That is exactly what SQLite's write lock does.
* Waiting is BOUNDED (``GATE_WAIT_TIMEOUT_SECONDS``). A writer that opens a
  transaction and never commits would otherwise hang every other writer
  forever, and a silent hang is worse than the error this gate replaces. On
  timeout the waiter logs an ERROR naming the holder and proceeds ungated, so
  SQLite arbitrates as it did before — loudly — instead of the app freezing.

Guard: tests/unit/test_local_db_write_gate_covers_shared_connection.py
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.common.system_logger import get_logger

__all__ = [
    "GATE_WAIT_POLL_SECONDS",
    "GATE_WAIT_TIMEOUT_SECONDS",
    "gate_holder_name",
    "write_gate",
]

logger = get_logger()

# A write transaction on this database is milliseconds of work. Anything past
# this is a writer that forgot to commit, and blocking on it forever would be
# a worse failure than the lock error this gate exists to remove.
GATE_WAIT_TIMEOUT_SECONDS = 30.0
# How often a waiter re-checks whether the holder's transaction is already
# over. Only contended waits poll, and the handoff has to feel instant.
GATE_WAIT_POLL_SECONDS = 0.05


class _WriteGateState:
    """The process-wide gate: one owner task at a time, reentrant for it."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._holder: asyncio.Task | None = None
        self._holder_name: str = ""
        self._depth = 0
        self._stale_check: Callable[[], bool] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def holder_name(self) -> str:
        return self._holder_name

    def _rebind_if_loop_changed(self) -> None:
        """An ``asyncio.Lock`` belongs to the loop that first awaited it.

        The engine has exactly one loop for its whole life, so this never
        fires in production — but the gate is a process singleton and a test
        run builds a fresh loop per test, where a lock held by a dead loop
        raises "bound to a different event loop" on the next acquire. Nothing
        from the old loop can still be running, so rebuilding is safe.
        """
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        self._loop = loop
        self._lock = asyncio.Lock()
        self._holder = None
        self._holder_name = ""
        self._depth = 0
        self._stale_check = None

    def _reap_stale_holder(self) -> None:
        """Take the gate back from a holder whose transaction is already over.

        The shared connection can leave its transaction by a route this module
        cannot see — a raw ``connection.rollback()``, for instance. The hold is
        then protecting nothing, and a waiter would sit out its whole bounded
        timeout for a lock SQLite has already released. So the holder declares
        how to tell (``stale_check``) and a waiter checks before waiting.
        """
        check = self._stale_check
        if check is None or self._holder is None:
            return
        try:
            stale = check()
        except Exception:  # noqa: BLE001 — a check we cannot run is not proof
            return
        if not stale:
            return
        logger.debug(
            "[write_gate] reclaiming the gate from %r — its transaction is over",
            self._holder_name or "unknown",
        )
        self._stale_check = None
        self._holder = None
        self._holder_name = ""
        self._depth = 0
        if self._lock.locked():
            self._lock.release()

    async def acquire(self, stale_check: Callable[[], bool] | None = None) -> bool:
        """Take the gate for the current task. True if the caller must release.

        False means either this task already holds it (reentrant — the outer
        holder releases) or the bounded wait expired and we proceed ungated.
        ``stale_check`` lets a long-lived holder say when its hold has become
        meaningless (see ``_reap_stale_holder``).
        """
        self._rebind_if_loop_changed()
        task = asyncio.current_task()
        if self._holder is not None and task is not None and task is self._holder:
            self._depth += 1
            return False
        # Wait in slices, re-checking staleness between them. A holder can end
        # its transaction by a route this module never sees (a raw rollback),
        # and a single long wait_for would sit out the whole timeout for a lock
        # that was released a millisecond in.
        deadline = asyncio.get_running_loop().time() + GATE_WAIT_TIMEOUT_SECONDS
        acquired = False
        while True:
            self._reap_stale_holder()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(
                    self._lock.acquire(),
                    timeout=min(GATE_WAIT_POLL_SECONDS, remaining),
                )
            except (TimeoutError, asyncio.TimeoutError):
                continue
            acquired = True
            break
        if not acquired:
            # Declaring the holder broken and then leaving it in place would
            # poison the gate for the rest of the process: every later writer
            # would wait the full timeout and proceed ungated, for ever. If the
            # hold is broken, TAKE it — loudly.
            logger.error(
                "[write_gate] waited %.0fs for the SQLite write gate held by %r and "
                "took it back. That holder is a write transaction nobody committed, "
                "so writes may now interleave with it and fail with 'database is "
                "locked' — find it. (Gate reclaimed; the app keeps working.)",
                GATE_WAIT_TIMEOUT_SECONDS, self._holder_name or "unknown",
            )
            # Take OWNERSHIP of the lock the broken holder is sitting on
            # rather than releasing it: releasing would open a window for a
            # queued waiter and we would be back to waiting.
            self._stale_check = None
            if not self._lock.locked():
                await self._lock.acquire()
        self._holder = task
        self._holder_name = task.get_name() if task is not None else "no-task"
        self._depth = 1
        self._stale_check = stale_check
        return True

    def release(self, owned: bool) -> None:
        if not owned:
            if self._depth > 1:
                self._depth -= 1
            return
        self._depth = 0
        self._holder = None
        self._holder_name = ""
        self._stale_check = None
        if self._lock.locked():
            self._lock.release()


class _WriteGateHold:
    """One ``async with write_gate():`` span. Single use, by design."""

    __slots__ = ("_owned", "_state")

    def __init__(self, state: _WriteGateState) -> None:
        self._state = state
        self._owned = False

    async def __aenter__(self) -> "_WriteGateHold":
        self._owned = await self._state.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        self._state.release(self._owned)
        self._owned = False
        return False

    # LocalDatabase spans a transaction across separate awaits (first write …
    # COMMIT), which no single ``async with`` block can wrap.
    async def acquire(self, stale_check: Callable[[], bool] | None = None) -> None:
        self._owned = await self._state.acquire(stale_check)

    def release(self) -> None:
        self._state.release(self._owned)
        self._owned = False


_WRITE_GATE: _WriteGateState | None = None


def write_gate() -> _WriteGateHold:
    """The process-wide write gate, as a fresh single-use hold.

    Built lazily so the lock binds to the running loop rather than to whichever
    loop happened to be current at import time. ``async with write_gate():``
    behaves exactly as it did when this returned a bare ``asyncio.Lock``, and
    is now reentrant for the task that already holds it.
    """
    global _WRITE_GATE
    if _WRITE_GATE is None:
        _WRITE_GATE = _WriteGateState()
    return _WriteGateHold(_WRITE_GATE)


def gate_holder_name() -> str:
    """Which task holds the gate right now (diagnostics; "" when free)."""
    return _WRITE_GATE.holder_name if _WRITE_GATE is not None else ""
