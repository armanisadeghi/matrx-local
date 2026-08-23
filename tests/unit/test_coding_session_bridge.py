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
from app.services.aidream.client import AIDreamError, AIDreamOfflineError
from app.services.coding_sessions.claude_history import ClaudeHistoryImporter
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import (
    BridgeMutationConflict,
    CodingSessionBridgeOutbox,
    _delivery_lane_key,
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


class MutableTokenRepo(FakeTokenRepo):
    def __init__(self, access_token: str = "owner-jwt") -> None:
        self.access_token = access_token

    async def get(self) -> dict[str, Any]:
        row = await super().get()
        row["access_token"] = self.access_token
        return row


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


def _hook(
    *,
    stable_id: str | None = "prompt-1",
    text: str = "hello",
    provider: str = "claude_code",
    provider_session_id: str = "provider-session-1",
) -> BridgeRequest:
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
            "provider": provider,
            "provider_session_id": provider_session_id,
            "origin": "independent_hook",
            "stream_key": "main",
            "hook_event": event,
        }
    )


def _native_import() -> BridgeRequest:
    return BridgeRequest.model_validate(
        {
            "action": "append_native",
            "provider": "claude_code",
            "provider_session_id": "claude-sdk:project:session",
            "provider_project_key": "claude-local:project",
            "origin": "matrx_local",
            "writer_runtime_id": "matrx-local:claude-history:test",
            "conversation": {
                "conversation_id": "22222222-2222-4222-8222-222222222222",
                "is_new": True,
                "store": True,
            },
            "entries": [
                {
                    "entry_id": "native-1",
                    "source_sequence": 0,
                    "kind": "user",
                    "payload": {"type": "user", "message": "hello"},
                }
            ],
            "source_metadata": {
                "source_kind": "claude_local_jsonl",
                "provider_native_session_id": "11111111-1111-4111-8111-111111111111",
                "provider_account_key": "a" * 64,
                "importer_version": "matrx-local/test",
                "transcript_sha256": "b" * 64,
                "transcript_bytes": 10,
                "transcript_entry_count": 1,
                "transcript_mtime_ns": 1,
                "source_complete": True,
            },
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
    activity_migration = await bridge_db.fetchone(
        "SELECT version FROM _migrations WHERE version = 24"
    )
    assert activity_migration and activity_migration["version"] == 24
    activity_columns = await bridge_db.fetchall(
        "PRAGMA table_info(coding_session_bridge_delivery_activity)"
    )
    activity_column_names = {row["name"] for row in activity_columns}
    assert activity_column_names >= {
        "provider",
        "action",
        "source",
        "last_enqueued_at",
        "last_acknowledged_at",
        "last_acknowledged_accepted",
        "last_acknowledged_duplicates",
    }
    assert "envelope_json" not in activity_column_names
    assert "payload" not in activity_column_names
    lane_migration = await bridge_db.fetchone(
        "SELECT version FROM _migrations WHERE version = 25"
    )
    assert lane_migration and lane_migration["version"] == 25
    metadata_migration = await bridge_db.fetchone(
        "SELECT version FROM _migrations WHERE version = 26"
    )
    assert metadata_migration and metadata_migration["version"] == 26
    item_count_migration = await bridge_db.fetchone(
        "SELECT version FROM _migrations WHERE version = 28"
    )
    assert item_count_migration and item_count_migration["version"] == 28
    outbox_columns = await bridge_db.fetchall(
        "PRAGMA table_info(coding_session_bridge_outbox)"
    )
    assert "lane_key" in {row["name"] for row in outbox_columns}
    metadata_columns = await bridge_db.fetchall(
        "PRAGMA table_info(coding_session_bridge_queue_metadata)"
    )
    assert "item_count" in {row["name"] for row in metadata_columns}


@pytest.mark.anyio
async def test_delivery_envelope_details_are_paginated_safe_and_actionable(
    bridge_db: LocalDatabase,
) -> None:
    service = CodingSessionBridgeOutbox(db=bridge_db, cloud_enabled=False)
    native = await service.enqueue(_native_import())
    hook = await service.enqueue(_hook(provider="codex", stable_id="safe-detail"))

    first = await service.delivery_envelopes(queue_state="pending", limit=1)

    assert first["terminology"] == "delivery_envelopes"
    assert first["total"] == 2
    assert first["has_more"] is True
    assert first["next_cursor"] == native.receipt_id
    assert first["items"][0]["item_count"] == 1
    assert first["items"][0]["actions"] == {
        "retry": True,
        "discard": True,
        "discard_requires_confirmation": True,
    }
    serialized = json.dumps(first)
    assert "claude-sdk:project:session" not in serialized

    second = await service.delivery_envelopes(
        queue_state="pending",
        limit=10,
        after_receipt_id=first["next_cursor"],
        provider="codex",
    )
    assert [item["receipt_id"] for item in second["items"]] == [hook.receipt_id]
    assert second["items"][0]["created_at"].endswith("Z")

    preview = await service.discard_delivery_envelope(hook.receipt_id, confirmed=False)
    assert preview["confirmation_required"] is True
    assert preview["discarded"] is False
    assert await service.pending_count() == 2
    discarded = await service.discard_delivery_envelope(hook.receipt_id, confirmed=True)
    assert discarded["discarded"] is True
    assert await service.pending_count() == 1


@pytest.mark.anyio
async def test_one_preserved_envelope_can_be_retried_without_touching_others(
    bridge_db: LocalDatabase,
) -> None:
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=FakeClient([AIDreamError(409, '{"error":"entry_mutated"}')]),
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    receipt = await service.enqueue(_hook(stable_id="preserved-retry"))
    await service.sync_pending()
    assert await service.quarantined_count() == 1

    result = await service.retry_delivery_envelope(receipt.receipt_id)

    assert result == {
        "receipt_id": receipt.receipt_id,
        "previous_state": "quarantine",
        "state": "pending",
        "retry_requested": True,
    }
    assert await service.quarantined_count() == 0
    assert await service.pending_count() == 1
    detail = await service.delivery_envelopes(queue_state="pending")
    assert detail["items"][0]["attempts"] == 0
    assert detail["items"][0]["error"] is None


@pytest.mark.anyio
async def test_provider_neutral_status_shows_codex_pending_and_quarantine_safely(
    bridge_db: LocalDatabase,
) -> None:
    client = FakeClient(
        [
            AIDreamError(
                409,
                'HTTP 409: {"error":"entry_mutated",'
                '"echo":"private prompt /Users/private/repo"}',
            )
        ]
    )
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=client,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(
        _hook(provider="codex", stable_id="poison", text="private prompt")
    )
    await service.sync_pending()
    await service.enqueue(
        _hook(provider="codex", stable_id="waiting", text="second secret")
    )

    status = await service.delivery_status()

    assert status["schema_version"] == 2
    assert status["pending"]["total"] == 1
    assert status["quarantine"]["total"] == 1
    assert status["pending"]["by_provider"]["codex"] == 1
    assert status["quarantine"]["by_provider"]["codex"] == 1
    assert status["providers"]["codex"]["pending"] == 1
    assert status["providers"]["codex"]["pending_sessions"] == 1
    assert status["providers"]["codex"]["quarantined"] == 1
    assert status["providers"]["codex"]["quarantined_sessions"] == 1
    assert status["pending"]["payload_bytes"] > 0
    assert status["quarantine"]["reasons"] == [
        {
            "code": "entry_mutated",
            "message": "The cloud already has this event identity with different content.",
            "count": 1,
        }
    ]
    assert status["providers"]["codex"]["by_action"]["observe_hook"] == {
        "pending": 1,
        "quarantined": 1,
        "last_enqueue": status["providers"]["codex"]["last_enqueue"],
        "last_acknowledgement": None,
    }
    assert set(status["providers"]) == {
        "claude_code",
        "codex",
        "cursor",
        "vscode",
    }
    assert status["providers"]["claude_code"]["capabilities"] == {
        "event_mirror": True,
        "historical_import": True,
        "title_sync": True,
        "local_runtime": True,
        "native_resume": True,
        "participant_conversations": False,
        "limitations": [],
    }
    assert status["providers"]["codex"]["capabilities"]["event_mirror"] is True
    assert status["providers"]["codex"]["capabilities"]["historical_import"] is False
    assert status["providers"]["cursor"]["capabilities"]["limitations"] == [
        "Capture is limited to events the Cursor host exposes; full historical host fidelity is not available."
    ]
    assert status["providers"]["vscode"]["capabilities"] == {
        "event_mirror": False,
        "historical_import": False,
        "title_sync": False,
        "local_runtime": False,
        "native_resume": False,
        "participant_conversations": True,
        "limitations": [
            "Only AI Matrx @matrx participant conversations are available; unrelated VS Code chat history is outside the extension API boundary."
        ],
    }
    serialized = json.dumps(status)
    assert "private prompt" not in serialized
    assert "/Users/private/repo" not in serialized
    assert status["head_blocker"] is None, "a ready row is not a blocker"


@pytest.mark.anyio
async def test_successful_codex_ack_is_persisted_after_validation(
    bridge_db: LocalDatabase,
) -> None:
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=FakeClient(),
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    receipt = await service.enqueue(_hook(provider="codex"))
    queued = await service.delivery_status()
    assert queued["last_enqueue"]["receipt_id"] == receipt.receipt_id
    assert queued["last_enqueue"]["at"].endswith("Z")
    assert queued["last_acknowledgement"] is None

    assert await service.sync_pending() == {
        "sent": 1,
        "failed": 0,
        "blocked": None,
    }
    delivered = await service.delivery_status()
    acknowledgement = delivered["providers"]["codex"]["last_acknowledgement"]
    assert delivered["pending"]["total"] == 0
    assert delivered["providers"]["codex"]["acknowledged_envelopes"] == 1
    assert acknowledgement == delivered["last_acknowledgement"]
    assert acknowledgement["at"].endswith("Z")
    assert acknowledgement == {
        "receipt_id": receipt.receipt_id,
        "at": acknowledgement["at"],
        "provider": "codex",
        "action": "observe_hook",
        "source": "independent_hook",
        "accepted": 1,
        "duplicates": 0,
        "fidelity": "event_mirror",
    }
    activity = await bridge_db.fetchone(
        """SELECT acknowledged_envelopes
           FROM coding_session_bridge_delivery_activity
           WHERE provider='codex' AND action='observe_hook'"""
    )
    assert activity and activity["acknowledged_envelopes"] == 1


@pytest.mark.anyio
async def test_retry_blocks_only_its_session_lane_while_other_lanes_progress(
    bridge_db: LocalDatabase,
) -> None:
    client = FakeClient([AIDreamError(500, "session-specific rejection"), {}, {}, {}])
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=client,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    blocked_first = await service.enqueue(
        _hook(stable_id="a-1", provider_session_id="claude-session-a")
    )
    await service.enqueue(
        _hook(stable_id="a-2", provider_session_id="claude-session-a")
    )
    await service.enqueue(
        _hook(
            stable_id="codex-1",
            provider="codex",
            provider_session_id="codex-session",
        )
    )
    await service.enqueue(
        _hook(stable_id="b-1", provider_session_id="claude-session-b")
    )

    result = await service.sync_pending(limit=4)

    assert result == {"sent": 2, "failed": 1, "blocked": None}
    assert [call[1]["hook_event"]["stable_event_id"] for call in client.calls] == [
        "a-1",
        "codex-1",
        "b-1",
    ]
    assert await service.pending_count() == 2
    status = await service.delivery_status()
    assert status["head_blocker"]["receipt_id"] == blocked_first.receipt_id
    assert status["head_blocker"]["provider"] == "claude_code"

    await bridge_db.execute("UPDATE coding_session_bridge_outbox SET next_attempt_at=0")
    await bridge_db.commit()
    retry = await service.sync_pending(limit=2)

    assert retry == {"sent": 2, "failed": 0, "blocked": None}
    assert [call[1]["hook_event"]["stable_event_id"] for call in client.calls] == [
        "a-1",
        "codex-1",
        "b-1",
        "a-1",
        "a-2",
    ]


def test_session_actions_share_a_lane_while_sessionless_actions_do_not() -> None:
    native = _native_import()
    metadata_payload = native.model_dump(mode="json")
    metadata_payload.update(
        {
            "action": "observe_hook",
            "origin": "independent_hook",
            "entries": [],
            "source_metadata": None,
            "conversation": None,
            "writer_runtime_id": None,
            "stream_key": "metadata-plane",
            "hook_event": {
                "name": "SessionMetadata",
                "stable_event_id": "metadata-1",
                "payload": {"title": "Same lane"},
            },
        }
    )
    metadata = BridgeRequest.model_validate(metadata_payload)

    assert native.action.value != metadata.action.value
    assert native.stream_key != metadata.stream_key
    assert _delivery_lane_key(native) == _delivery_lane_key(metadata)

    health = BridgeRequest.model_validate(
        {"action": "health", "provider": "claude_code"}
    )
    listing = BridgeRequest.model_validate(
        {"action": "list_native", "provider": "claude_code"}
    )
    assert _delivery_lane_key(health) != _delivery_lane_key(listing)


@pytest.mark.anyio
async def test_claude_history_status_separates_queued_from_acknowledged(
    bridge_db: LocalDatabase,
    tmp_path: Path,
) -> None:
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=FakeClient(),
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_native_import())
    importer = ClaudeHistoryImporter(
        db=bridge_db,
        outbox=service,
        config_dir=tmp_path / ".claude",
    )

    queued = await importer.status()
    assert queued["delivery"]["state"] == "queued"
    assert queued["delivery"]["queued_batches"] == 1
    assert queued["delivery"]["last_acknowledgement"] is None

    assert (await service.sync_pending())["sent"] == 1
    acknowledged = await importer.status()
    assert acknowledged["delivery"]["state"] == "acknowledged"
    assert acknowledged["delivery"]["queued_batches"] == 0
    assert acknowledged["delivery"]["last_acknowledgement"]["accepted"] == 1


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
async def test_first_cloud_401_pauses_all_lanes_until_credentials_change(
    bridge_db: LocalDatabase,
) -> None:
    rejected = AIDreamError(401, "HTTP 401")
    client = FakeClient([rejected])
    tokens = MutableTokenRepo()
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=client,
        token_repo=tokens,  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook(stable_id="lane-a", provider_session_id="session-a"))
    await service.enqueue(_hook(stable_id="lane-b", provider_session_id="session-b"))

    first = await service.sync_pending()
    assert first == {
        "sent": 0,
        "failed": 1,
        "blocked": "cloud_credentials_rejected",
    }
    assert len(client.calls) == 1, "one rejection must stop cross-lane fan-out"

    paused = await service.sync_pending()
    assert paused == {
        "sent": 0,
        "failed": 0,
        "blocked": "cloud_credentials_rejected",
    }
    assert len(client.calls) == 1, "the rejected token must not be retried"

    status = await service.delivery_status()
    blocker = status["publisher"]["blocker"]
    assert blocker == {
        "code": "cloud_credentials_rejected",
        "message": (
            "AI Matrx rejected the stored session. Sign in again to resume "
            "delivery; queued events remain safe on this Mac."
        ),
        "http_status": 401,
        "receipt_id": 1,
        "provider": "claude_code",
    }
    assert status["head_blocker"]["error"]["code"] == ("cloud_credentials_rejected")
    assert status["head_blocker"]["created_at"].endswith("Z")

    tokens.access_token = "replacement-jwt"
    await service.credentials_changed()
    resumed = await service.sync_pending()
    assert resumed == {"sent": 2, "failed": 0, "blocked": None}
    assert len(client.calls) == 3
    assert (await service.delivery_status())["publisher"]["blocker"] is None


@pytest.mark.anyio
async def test_history_retry_recovers_ordered_drain_without_dropping_hook(
    bridge_db: LocalDatabase,
) -> None:
    client = FakeClient([AIDreamOfflineError("offline")])
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=client,
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_native_import())
    await service.enqueue(_hook(stable_id="after-import"))

    assert (await service.sync_pending())["failed"] == 1
    assert await service.pending_native_import_count() == 1
    oldest = await service.oldest_native_import()
    assert oldest is not None and oldest["attempts"] == 1
    assert oldest["created_at"].endswith("Z")
    assert oldest["error"]
    assert "last_error" not in oldest

    assert await service.retry_pending_native_imports() == {
        "retried": 1,
        "pending": 1,
    }
    assert (await service.sync_pending(limit=2))["sent"] == 1
    assert [call[1]["action"] for call in client.calls] == [
        "append_native",
        "observe_hook",
        "append_native",
    ]
    assert await service.pending_count() == 0


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
    status = await service.delivery_status()
    assert status["last_acknowledgement"] is None


@pytest.mark.anyio
async def test_persisted_envelope_integrity_failure_is_preserved_without_upload(
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

    assert result == {"sent": 0, "failed": 0, "blocked": None}
    assert client.calls == []
    assert await service.pending_count() == 0
    assert await service.quarantined_count() == 1


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
