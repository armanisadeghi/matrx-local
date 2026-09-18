"""The ``index_writable`` verdict in the sync status payload (VERIFIER'S TEST).

NOT CS-33/R2's defect and NOT CS-33/R2's fix. This is the zero-authorship
CS-33 verifier's third red test, landed VERBATIM (assertions and comments
untouched) and kept in a file of its own because it is owned by the session
editing ``app/services/coding_sessions/title_sync.py`` and
``app/services/coding_sessions/claude_overview.py``. Fixing it from a scope
lane would collide with that work.

The owner landed the guard, so the ``xfail`` marker is GONE and this is a
real guard: an xpassing xfail is a note, not a protection. The assertion is
still exactly what the verifier wrote.

The fix has two halves, because the operation row's ``index_writable`` column
is ``NOT NULL`` and could not represent "never probed": schema v36 adds
``index_writable_probed``, ``_sync_once`` starts the flag at ``None`` instead
of ``False``, and ``status`` reads the newest row that actually PROBED.
"""

from __future__ import annotations

# ── CLAIM 4: the status payload must not report a verdict nobody measured ──
import pytest

from tests.unit.test_claude_session_labels import _FakeClient, env  # noqa: F401
from app.services.coding_sessions.title_sync import (
    ClaudeSessionMetadataReconciler,
)


@pytest.mark.anyio
async def test_a_blocked_pass_is_not_a_writability_verdict_either(env) -> None:
    """The sibling of the defect this lane closed, in the SAME payload.

    ``pin_divergence`` now refuses to believe a pass that compared nothing.
    ``index_writable`` still believes one: ``_sync_once`` initialises the flag
    to ``False`` and only replaces it AFTER ``_identities()``, so a pass
    blocked on ``no_active_user_jwt`` journals ``index_writable = 0`` — and
    ``status`` reads the newest completed row with no status filter, so the
    screen is told Claude's records are NOT WRITABLE by a probe that never ran.

    Measured on a real engine (port 22242, the real 79,255-file index copied
    to a private home, 2026-09-18): the first apply pass was blocked
    ``no_active_user_jwt`` with ``index_files`` 0 and ``index_writable`` 0, the
    status endpoint answered ``index_writable: false``, and all 198 record
    files probed by hand were openable for writing.
    """
    db, outbox, _tmp = env
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient([]),
        index_reader=lambda: ({}, {"files": 0, "records": 0, "unreadable": 0}),
    )
    await db.execute(
        """INSERT INTO coding_session_metadata_sync_operations (
               operation_id, mode, status, started_at, completed_at,
               compared_sessions, index_files, index_writable,
               index_writable_probed, error_message
           ) VALUES ('op-blocked', 'apply', 'failed', '2026-09-18T15:45:40Z',
                     '2026-09-18T15:45:40Z', 0, 0, 0, 0, 'no_active_user_jwt')"""
    )
    await db.commit()
    status = await reconciler.status()
    assert status["pin_divergence"]["checked"] is False  # already guarded
    assert status["index_writable"] is None, (
        "a pass blocked before it probed anything reported "
        f"index_writable={status['index_writable']!r} — a verdict nobody measured"
    )
