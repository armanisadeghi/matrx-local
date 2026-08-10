"""Provider-neutral local Coding Session Bridge contract tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.services.aidream.client import AIDreamOfflineError
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import (
    BridgeMutationConflict,
    CodingSessionBridgeOutbox,
)
from app.services.local_db.database import LocalDatabase


class FakeTokenRepo:
    async def get(self) -> dict[str, Any]:
        return {
            "user_id": "00000000-0000-4000-8000-000000000001",
            "access_token": "owner-jwt",
        }

    def is_expired(self, _row: dict[str, Any]) -> bool:
        return False


class FakeClient:
    def __init__(self, outcomes: list[Exception | dict[str, Any]] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple[str, dict[str, Any], str | None, float]] = []

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        jwt: str | None = None,
        timeout: float = 130.0,
    ) -> dict[str, Any]:
        self.calls.append((path, deepcopy(payload), jwt, timeout))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return {"accepted": 1}


def _hook(*, stable_id: str | None = "prompt-1", text: str = "hello") -> BridgeRequest:
    event: dict[str, Any] = {
        "name": "UserPromptSubmit",
        "payload": {"prompt": text},
    }
    if stable_id is not None:
        event["stable_event_id"] = stable_id
    return BridgeRequest.model_validate(
        {
            "schema_version": 1,
            "action": "observe_hook",
            "provider": "claude_code",
            "provider_session_id": "provider-session-1",
            "origin": "independent_hook",
            "stream_key": "main",
            "hook_event": event,
        }
    )


@pytest.fixture
async def bridge_db(tmp_path: Path):
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


def test_bridge_request_is_strict_and_matches_action_contract() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BridgeRequest.model_validate(
            {
                **_hook().model_dump(mode="json"),
                "created_by": "caller-must-not-supply-identity",
            }
        )
    with pytest.raises(ValidationError, match="origin must be independent_hook"):
        BridgeRequest.model_validate(
            {
                **_hook().model_dump(mode="json"),
                "origin": "matrx_local",
            }
        )


@pytest.mark.anyio
async def test_migration_creates_dedicated_local_outbox(bridge_db: LocalDatabase) -> None:
    row = await bridge_db.fetchone(
        "SELECT version FROM _migrations WHERE version = 19"
    )
    assert row and row["version"] == 19
    columns = await bridge_db.fetchall("PRAGMA table_info(coding_session_bridge_outbox)")
    assert {row["name"] for row in columns} >= {
        "id",
        "dedupe_key",
        "envelope_json",
        "envelope_sha256",
        "attempts",
        "next_attempt_at",
        "last_error",
    }


@pytest.mark.anyio
async def test_ack_follows_commit_and_stable_replay_is_local_noop(
    bridge_db: LocalDatabase,
) -> None:
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=FakeClient(),
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    first = await service.enqueue(_hook())
    duplicate = await service.enqueue(_hook())

    assert first.accepted is True and first.persisted is True
    assert duplicate.receipt_id == first.receipt_id
    assert duplicate.duplicate is True
    assert await service.pending_count() == 1
    persisted = await bridge_db.fetchone(
        "SELECT envelope_json FROM coding_session_bridge_outbox WHERE id = ?",
        (first.receipt_id,),
    )
    assert persisted is not None
    assert json.loads(persisted["envelope_json"])["hook_event"]["payload"] == {
        "prompt": "hello"
    }

    with pytest.raises(BridgeMutationConflict):
        await service.enqueue(_hook(text="mutated"))


@pytest.mark.anyio
async def test_missing_provider_id_is_not_falsely_deduplicated(
    bridge_db: LocalDatabase,
) -> None:
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=FakeClient(),
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    first = await service.enqueue(_hook(stable_id=None))
    second = await service.enqueue(_hook(stable_id=None))
    assert first.receipt_id != second.receipt_id
    assert await service.pending_count() == 2


@pytest.mark.anyio
async def test_unknown_remote_outcome_survives_restart_and_replays_exact_envelope(
    bridge_db: LocalDatabase,
) -> None:
    offline = FakeClient([AIDreamOfflineError("response lost")])
    first_process = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=offline,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    receipt = await first_process.enqueue(_hook())
    before = await bridge_db.fetchone(
        "SELECT envelope_json FROM coding_session_bridge_outbox WHERE id=?",
        (receipt.receipt_id,),
    )
    result = await first_process.sync_pending()
    assert result == {"sent": 0, "failed": 1, "blocked": None}
    assert await first_process.pending_count() == 1

    await bridge_db.execute(
        "UPDATE coding_session_bridge_outbox SET next_attempt_at=0 WHERE id=?",
        (receipt.receipt_id,),
    )
    await bridge_db.commit()
    online = FakeClient()
    restarted = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=online,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    replay = await restarted.sync_pending()
    assert replay == {"sent": 1, "failed": 0, "blocked": None}
    assert online.calls == [
        (
            "/coding-sessions/bridge",
            json.loads(before["envelope_json"]),
            "owner-jwt",
            30.0,
        )
    ]
    assert await restarted.pending_count() == 0


@pytest.mark.anyio
async def test_failure_at_head_blocks_later_rows_until_ordered_retry(
    bridge_db: LocalDatabase,
) -> None:
    client = FakeClient([AIDreamOfflineError("offline"), {}, {}])
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=client,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook(stable_id="event-1", text="first"))
    await service.enqueue(_hook(stable_id="event-2", text="second"))

    assert (await service.sync_pending())["failed"] == 1
    assert [call[1]["hook_event"]["payload"]["prompt"] for call in client.calls] == [
        "first"
    ]

    await bridge_db.execute(
        "UPDATE coding_session_bridge_outbox SET next_attempt_at=0"
    )
    await bridge_db.commit()
    assert (await service.sync_pending())["sent"] == 2
    assert [call[1]["hook_event"]["payload"]["prompt"] for call in client.calls] == [
        "first",
        "first",
        "second",
    ]
