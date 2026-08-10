"""Provider-neutral local Coding Session Bridge contract tests."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.api.coding_session_routes import _is_loopback_host
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
    def __init__(
        self, outcomes: list[Exception | dict[str, Any]] | None = None
    ) -> None:
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
            if outcome:
                return outcome
        return {
            "schema_version": 1,
            "action": payload["action"],
            "provider": payload["provider"],
            "session_id": "11111111-1111-4111-8111-111111111111",
            "conversation_id": "22222222-2222-4222-8222-222222222222",
            "fidelity": "event_mirror",
            "accepted": 1,
            "duplicates": 0,
            "conflicts": 0,
        }


class BlockingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        jwt: str | None = None,
        timeout: float = 130.0,
    ) -> dict[str, Any]:
        self.calls.append((path, deepcopy(payload), jwt, timeout))
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("publisher cancellation did not stop the upload")


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


def test_ingress_peer_check_accepts_only_ipv4_and_ipv6_loopback() -> None:
    assert _is_loopback_host("127.0.0.1") is True
    assert _is_loopback_host("127.255.255.254") is True
    assert _is_loopback_host("::1") is True
    assert _is_loopback_host("::ffff:127.0.0.1") is True
    assert _is_loopback_host("192.168.1.5") is False
    assert _is_loopback_host("203.0.113.7") is False
    assert _is_loopback_host("localhost") is False
    assert _is_loopback_host(None) is False


def test_conversation_store_cannot_disable_cloud_persistence() -> None:
    payload = _hook().model_dump(mode="json")
    payload["conversation"] = {
        "conversation_id": "11111111-2222-4333-8444-555555555555",
        "is_new": True,
        "store": False,
    }
    with pytest.raises(ValidationError, match="Input should be True"):
        BridgeRequest.model_validate(payload)


@pytest.mark.anyio
async def test_migration_creates_dedicated_local_outbox(
    bridge_db: LocalDatabase,
) -> None:
    row = await bridge_db.fetchone("SELECT version FROM _migrations WHERE version = 19")
    assert row and row["version"] == 19
    columns = await bridge_db.fetchall(
        "PRAGMA table_info(coding_session_bridge_outbox)"
    )
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

    renamed = _hook().model_dump(mode="json")
    renamed["hook_event"]["name"] = "Stop"
    with pytest.raises(BridgeMutationConflict):
        await service.enqueue(BridgeRequest.model_validate(renamed))

    equivalent = _hook().model_dump(mode="json")
    equivalent.pop("origin")
    with pytest.raises(BridgeMutationConflict):
        await service.enqueue(BridgeRequest.model_validate(equivalent))


@pytest.mark.anyio
async def test_durable_ack_isolated_from_shared_connection_rollback(
    bridge_db: LocalDatabase,
) -> None:
    await bridge_db.execute("CREATE TABLE shared_tx_probe (value TEXT)")
    await bridge_db.commit()
    await bridge_db.execute(
        "INSERT INTO shared_tx_probe (value) VALUES ('rollback-me')"
    )

    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=FakeClient(),
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    enqueue_task = asyncio.create_task(service.enqueue(_hook()))
    await asyncio.sleep(0.05)
    assert not enqueue_task.done(), (
        "private writer must wait for the active transaction"
    )

    await bridge_db.db.rollback()
    receipt = await enqueue_task

    assert await bridge_db.fetchone("SELECT value FROM shared_tx_probe") is None
    assert await bridge_db.fetchone(
        "SELECT id FROM coding_session_bridge_outbox WHERE id=?",
        (receipt.receipt_id,),
    )


@pytest.mark.anyio
async def test_concurrent_stable_replays_commit_one_row_without_rollback(
    bridge_db: LocalDatabase,
) -> None:
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=FakeClient(),
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    receipts = await asyncio.gather(*(service.enqueue(_hook()) for _ in range(8)))
    assert len({receipt.receipt_id for receipt in receipts}) == 1
    assert sum(receipt.duplicate for receipt in receipts) == 7
    assert await service.pending_count() == 1


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
    online = FakeClient(
        [
            {
                "schema_version": 1,
                "action": "observe_hook",
                "provider": "claude_code",
                "session_id": "11111111-1111-4111-8111-111111111111",
                "conversation_id": "22222222-2222-4222-8222-222222222222",
                "fidelity": "event_mirror",
                "accepted": 0,
                "duplicates": 1,
                "conflicts": 0,
            }
        ]
    )
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

    await bridge_db.execute("UPDATE coding_session_bridge_outbox SET next_attempt_at=0")
    await bridge_db.commit()
    assert (await service.sync_pending())["sent"] == 2
    assert [call[1]["hook_event"]["payload"]["prompt"] for call in client.calls] == [
        "first",
        "first",
        "second",
    ]


@pytest.mark.anyio
async def test_malformed_upstream_2xx_does_not_delete_durable_row(
    bridge_db: LocalDatabase,
) -> None:
    client = FakeClient([{"accepted": 1}])
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=client,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook())

    result = await service.sync_pending()

    assert result == {"sent": 0, "failed": 1, "blocked": None}
    assert await service.pending_count() == 1
    head = await bridge_db.fetchone(
        "SELECT attempts, last_error FROM coding_session_bridge_outbox ORDER BY id LIMIT 1"
    )
    assert head["attempts"] == 1
    assert "acknowledgement schema_version" in head["last_error"]


@pytest.mark.anyio
async def test_persisted_envelope_integrity_failure_blocks_upload(
    bridge_db: LocalDatabase,
) -> None:
    client = FakeClient()
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=client,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    receipt = await service.enqueue(_hook())
    await bridge_db.execute(
        "UPDATE coding_session_bridge_outbox SET envelope_json='{}' WHERE id=?",
        (receipt.receipt_id,),
    )
    await bridge_db.commit()

    result = await service.sync_pending()

    assert result == {"sent": 0, "failed": 1, "blocked": None}
    assert client.calls == []
    assert await service.pending_count() == 1


@pytest.mark.anyio
async def test_shutdown_cancels_inflight_upload_and_keeps_durable_row(
    bridge_db: LocalDatabase,
) -> None:
    client = BlockingClient()
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=client,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook())
    await service.start_background()
    publisher = service._task
    await asyncio.wait_for(client.started.wait(), timeout=1)

    await asyncio.wait_for(service.stop_background(), timeout=1)

    assert publisher is not None and publisher.done()
    assert service.active is False
    assert await service.pending_count() == 1
