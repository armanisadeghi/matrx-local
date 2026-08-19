"""A DELIVERED envelope must never be uploaded twice, and must still retire.

Found live 2026-08-19 on v1.4.35. The v1.4.34 wedge (an ack-write exception
raising out of the tick) was fixed, but the underlying local write was still
attempted on the application's SHARED aiosqlite connection while the codex hook
ingress held the SQLite write lock almost continuously with its own private
`BEGIN IMMEDIATE` transactions. Every post-delivery write lost with
`database is locked`, so:

  * the delivered row was never deleted,
  * the deferral write that was supposed to push it back also failed,
  * the loop `continue`d and re-selected the very same row, and
  * outbox row 72184 was re-POSTed to aidream 12+ times in 10 minutes while
    the outbox GREW from 21,636 to 22,124.

Two properties are pinned here:
  1. the post-delivery retirement runs on its own durable connection, so it
     wins the lock a shared-connection write loses; and
  2. if the delete genuinely cannot land, the row is NOT sent again.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.services.coding_sessions.models import (
    BridgeAction,
    BridgeHookEvent,
    BridgeRequest,
)
from app.services.coding_sessions.service import (
    _DURABLE_WRITE_BUSY_TIMEOUT_MS,
    CodingSessionBridgeOutbox,
)
from app.services.local_db.database import LocalDatabase


def _request() -> BridgeRequest:
    return BridgeRequest(
        schema_version=1,
        action=BridgeAction.OBSERVE_HOOK,
        provider="codex",
        provider_session_id=str(uuid4()),
        origin="independent_hook",
        stream_key="main",
        hook_event=BridgeHookEvent(
            name="UserPromptSubmit",
            stable_event_id=str(uuid4()),
            payload={"prompt": "hi"},
        ),
    )


class _CountingClient:
    """Accepts everything and counts how many times each envelope arrived."""

    def __init__(self) -> None:
        self.uploads: list[str] = []

    async def post(self, path: str, payload: dict, jwt: str, timeout: float) -> Any:  # noqa: ARG002
        self.uploads.append(payload["hook_event"]["stable_event_id"])
        return {
            "schema_version": 1,
            "action": "observe_hook",
            "provider": "codex",
            "fidelity": "event_mirror",
            "session_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "accepted": 1,
            "duplicates": 0,
            "conflicts": 0,
        }


async def _seed_user(db: LocalDatabase) -> None:
    await db.execute(
        """INSERT INTO auth_tokens (key, access_token, user_id, updated_at)
           VALUES ('current_user', 'test-token', ?, datetime('now'))""",
        ("00000000-0000-4000-8000-000000000001",),
    )
    await db.commit()


@pytest.mark.anyio
async def test_retirement_does_not_depend_on_the_shared_connection(
    tmp_path: Path,
) -> None:
    """Post-delivery writes must run on their own durable connection.

    The shared connection is closed out from under the publisher. Pre-fix the
    ack write and the delete both went through it, so the delivered row could
    not be retired at all; the durable path does not touch it.
    """
    path = tmp_path / "matrx.db"
    db = LocalDatabase(path)
    await db.connect()
    await _seed_user(db)

    client = _CountingClient()
    outbox = CodingSessionBridgeOutbox(db=db, client=client, cloud_enabled=True)
    await outbox.enqueue(_request())

    row = await db.fetchone("SELECT id FROM coding_session_bridge_outbox")
    assert row is not None
    outbox_id = int(row["id"])

    await db.close()

    assert await outbox._delete_delivered_row(outbox_id) is True

    verify = sqlite3.connect(str(path))
    try:
        remaining = verify.execute(
            "SELECT count(*) FROM coding_session_bridge_outbox"
        ).fetchone()[0]
    finally:
        verify.close()
    assert remaining == 0


@pytest.mark.anyio
async def test_the_durable_write_waits_for_the_lock_instead_of_failing_fast(
    tmp_path: Path,
) -> None:
    """A busy_timeout long enough to outlast a hook-ingress write burst.

    Production held the lock in short bursts; the shared connection's 5s was
    not enough and every post-delivery write died with `database is locked`.
    """
    assert _DURABLE_WRITE_BUSY_TIMEOUT_MS >= 15000


@pytest.mark.anyio
async def test_a_delivered_row_that_cannot_be_deleted_is_never_resent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact production wedge: delete impossible, upload must not repeat."""
    path = tmp_path / "matrx.db"
    db = LocalDatabase(path)
    await db.connect()
    await _seed_user(db)

    client = _CountingClient()
    outbox = CodingSessionBridgeOutbox(db=db, client=client, cloud_enabled=True)
    await outbox.enqueue(_request())

    async def _always_locked(self: Any, outbox_id: int) -> bool:  # noqa: ARG001
        return False

    monkeypatch.setattr(
        CodingSessionBridgeOutbox, "_delete_delivered_row", _always_locked
    )

    async def _locked_retirement(self: Any, **kwargs: Any) -> None:  # noqa: ARG001
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        CodingSessionBridgeOutbox,
        "_commit_delivery_retirement",
        _locked_retirement,
    )

    first = await outbox.sync_pending()
    assert first["sent"] == 0
    assert len(client.uploads) == 1, "the row is delivered exactly once"
    assert await outbox.pending_count() == 1, "nothing is lost while locked"

    # Pre-fix this tick (and every tick after it) re-POSTed the same envelope.
    for _ in range(5):
        await outbox.sync_pending()

    assert len(client.uploads) == 1, (
        "a DELIVERED envelope was uploaded again — this is the live wedge that "
        "re-sent outbox row 72184 twelve times in ten minutes"
    )
    await db.close()


@pytest.mark.anyio
async def test_the_row_retires_once_the_lock_clears(tmp_path: Path) -> None:
    """The stuck delete is retried on a later tick, without a second upload."""
    path = tmp_path / "matrx.db"
    db = LocalDatabase(path)
    await db.connect()
    await _seed_user(db)

    client = _CountingClient()
    outbox = CodingSessionBridgeOutbox(db=db, client=client, cloud_enabled=True)
    await outbox.enqueue(_request())

    # Simulate the tick that delivered the row but could not retire it.
    row = await db.fetchone("SELECT id FROM coding_session_bridge_outbox")
    assert row is not None
    stuck_id = int(row["id"])
    outbox._delivered_undeleted.add(stuck_id)

    result = await outbox.sync_pending()

    assert client.uploads == [], "a delivered row must not be uploaded again"
    assert result["sent"] == 0
    assert await outbox.pending_count() == 0, "the retried delete must land"
    assert outbox._delivered_undeleted == set()
    await db.close()
