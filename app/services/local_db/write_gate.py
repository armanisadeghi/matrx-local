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
"""

from __future__ import annotations

import asyncio

__all__ = ["write_gate"]

_WRITE_GATE: asyncio.Lock | None = None


def write_gate() -> asyncio.Lock:
    """The process-wide write gate.

    Built lazily so the lock binds to the running loop rather than to whichever
    loop happened to be current at import time.
    """
    global _WRITE_GATE
    if _WRITE_GATE is None:
        _WRITE_GATE = asyncio.Lock()
    return _WRITE_GATE
