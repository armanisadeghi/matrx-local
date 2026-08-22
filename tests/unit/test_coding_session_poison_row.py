"""A permanently-rejected row must never stall the ordered outbox forever.

Found live 2026-08-17: one row had failed 2,520 times since 2026-08-13 with
HTTP 409 `entry_mutated` and had blocked 3,709 rows behind it for four days,
silently. Publication is ordered on purpose, so "retry forever" and "strict
order" together mean "stop forever" the moment one envelope is unacceptable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.services.aidream.client import AIDreamError, AIDreamOfflineError
from app.services.coding_sessions.service import (
    _QUARANTINE_AFTER_ATTEMPTS,
    CodingSessionBridgeOutbox,
    _is_terminal_rejection,
)
from app.services.coding_sessions.models import (
    BridgeAction,
    BridgeHookEvent,
    BridgeRequest,
)
from app.services.local_db.database import LocalDatabase

_MUTATED = (
    "[aidream_client] /coding-sessions/bridge → HTTP 409: "
    '{"error":"entry_mutated","message":"entry was previously stored with a '
    'different payload"}'
)


def _request(event_id: str) -> BridgeRequest:
    return BridgeRequest(
        schema_version=1,
        action=BridgeAction.OBSERVE_HOOK,
        provider="claude_code",
        provider_session_id=str(uuid4()),
        origin="independent_hook",
        stream_key="main",
        hook_event=BridgeHookEvent(
            name="UserPromptSubmit",
            stable_event_id=event_id,
            payload={"prompt": "hi"},
        ),
    )


class _Client:
    """Refuses the first envelope permanently, accepts everything after it."""

    def __init__(self, poison_event_id: str) -> None:
        self._poison = poison_event_id
        self.accepted: list[str] = []

    async def post(self, path: str, payload: dict, jwt: str, timeout: float) -> Any:  # noqa: ARG002
        event_id = payload["hook_event"]["stable_event_id"]
        if event_id == self._poison:
            raise AIDreamError(409, _MUTATED)
        self.accepted.append(event_id)
        return {
            "schema_version": 1,
            "action": "observe_hook",
            "provider": "claude_code",
            "fidelity": "event_mirror",
            "session_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "accepted": 1,
            "duplicates": 0,
            "conflicts": 0,
        }


def test_offline_is_never_terminal() -> None:
    """The server said nothing; assuming refusal would be real data loss."""
    assert not _is_terminal_rejection(AIDreamOfflineError("down"), 9_999)


def test_entry_mutated_is_terminal_immediately() -> None:
    assert _is_terminal_rejection(AIDreamError(409, _MUTATED), 1)


def test_a_bare_conflict_gets_a_long_retry_run_first() -> None:
    """A proxy or mid-deploy server can 409 transiently; do not drop early."""
    generic = AIDreamError(409, "HTTP 409: gateway hiccup")
    assert not _is_terminal_rejection(generic, 1)
    assert _is_terminal_rejection(generic, _QUARANTINE_AFTER_ATTEMPTS)


def test_a_server_error_is_always_retryable() -> None:
    assert not _is_terminal_rejection(AIDreamError(500, "boom"), 9_999)


@pytest.mark.anyio
async def test_poison_row_is_quarantined_and_the_queue_drains(tmp_path: Path) -> None:
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    await db.execute(
        """INSERT INTO auth_tokens (key, access_token, user_id, updated_at)
           VALUES ('current_user', 'test-token', ?, datetime('now'))""",
        ("00000000-0000-4000-8000-000000000001",),
    )
    await db.commit()

    poison = str(uuid4())
    client = _Client(poison)
    outbox = CodingSessionBridgeOutbox(db=db, client=client, cloud_enabled=True)
    # The poison row is FIRST, so under ordered delivery it blocks the rest.
    for event_id in (poison, str(uuid4()), str(uuid4())):
        await outbox.enqueue(_request(event_id))

    result = await outbox.sync_pending()

    assert result["sent"] == 2, "rows behind the poison row must still deliver"
    assert len(client.accepted) == 2
    assert await outbox.pending_count() == 0
    assert await outbox.quarantined_count() == 1
    # Preserved, not dropped: zero data loss is the contract.
    rows = await db.fetchall(
        "SELECT envelope_json, http_status FROM coding_session_bridge_quarantine"
    )
    assert len(rows) == 1
    assert rows[0]["http_status"] == 409
    assert poison in str(rows[0]["envelope_json"])
    await db.close()


@pytest.mark.anyio
async def test_invalid_local_envelope_is_quarantined_without_retrying(
    tmp_path: Path,
) -> None:
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    await db.execute(
        """INSERT INTO auth_tokens (key, access_token, user_id, updated_at)
           VALUES ('current_user', 'test-token', ?, datetime('now'))""",
        ("00000000-0000-4000-8000-000000000001",),
    )
    await db.commit()

    client = _Client("never-refuse")
    outbox = CodingSessionBridgeOutbox(db=db, client=client, cloud_enabled=True)
    await outbox.enqueue(_request(str(uuid4())))
    accepted_event = str(uuid4())
    await outbox.enqueue(_request(accepted_event))
    await db.execute(
        "UPDATE coding_session_bridge_outbox SET envelope_json='{}' WHERE id=1"
    )
    await db.commit()

    result = await outbox.sync_pending()

    assert result["sent"] == 1
    assert client.accepted == [accepted_event]
    assert await outbox.pending_count() == 0
    rows = await db.fetchall(
        "SELECT http_status, last_error FROM coding_session_bridge_quarantine"
    )
    assert len(rows) == 1
    assert rows[0]["http_status"] is None
    assert "integrity" in str(rows[0]["last_error"])
    await db.close()
