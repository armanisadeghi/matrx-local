"""Fairness and outage-circuit regressions for Coding Session delivery."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.services.aidream.client import AIDreamOfflineError
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import (
    CodingSessionBridgeOutbox,
    PublisherCircuitConfig,
)
from app.services.local_db.database import LocalDatabase


class _TokenRepo:
    async def get(self) -> dict[str, Any]:
        return {
            "user_id": "00000000-0000-4000-8000-000000000001",
            "access_token": "owner-jwt",
        }

    def is_expired(self, _row: dict[str, Any]) -> bool:
        return False


def _request(*, session: str, stable_id: str, text: str) -> BridgeRequest:
    return BridgeRequest.model_validate(
        {
            "schema_version": 1,
            "action": "observe_hook",
            "provider": "claude_code",
            "provider_session_id": session,
            "origin": "independent_hook",
            "stream_key": "main",
            "hook_event": {
                "name": "UserPromptSubmit",
                "stable_event_id": stable_id,
                "payload": {"prompt": text},
            },
        }
    )


def _ack(payload: dict[str, Any]) -> dict[str, Any]:
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


class _SizeSensitiveClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def post(
        self,
        _path: str,
        payload: dict[str, Any],
        *,
        jwt: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        assert jwt == "owner-jwt"
        assert timeout == 30.0
        self.calls.append(deepcopy(payload))
        prompt = payload["hook_event"]["payload"]["prompt"]
        if prompt.startswith("large:"):
            raise AIDreamOfflineError("transport failed for this envelope")
        return _ack(payload)


class _OfflineClient:
    def __init__(self, *, recover_after: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.recover_after = recover_after

    async def post(
        self,
        _path: str,
        payload: dict[str, Any],
        *,
        jwt: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        self.calls.append(deepcopy(payload))
        if self.recover_after is None or len(self.calls) <= self.recover_after:
            raise AIDreamOfflineError("service unreachable")
        return _ack(payload)


class _CountingClient:
    def __init__(self, expected: int) -> None:
        self.calls: list[dict[str, Any]] = []
        self.complete = asyncio.Event()
        self.expected = expected

    async def post(
        self,
        _path: str,
        payload: dict[str, Any],
        *,
        jwt: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        self.calls.append(deepcopy(payload))
        if len(self.calls) == self.expected:
            self.complete.set()
        return _ack(payload)


@pytest.fixture
async def circuit_db(tmp_path: Path):
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.anyio
async def test_large_envelope_failure_does_not_starve_other_lane_or_reorder_its_lane(
    circuit_db: LocalDatabase,
) -> None:
    client = _SizeSensitiveClient()
    outbox = CodingSessionBridgeOutbox(
        db=circuit_db,
        client=client,  # type: ignore[arg-type]
        token_repo=_TokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
        circuit_config=PublisherCircuitConfig(offline_failures_to_open=2),
    )
    await outbox.enqueue(
        _request(session="large-lane", stable_id="large-1", text="large:" + "x" * 5000)
    )
    await outbox.enqueue(
        _request(session="large-lane", stable_id="large-2", text="same-lane-tail")
    )
    await outbox.enqueue(
        _request(session="small-lane", stable_id="small-1", text="small")
    )

    result = await outbox.sync_pending()

    assert result == {"sent": 1, "failed": 1, "blocked": None}
    assert [call["hook_event"]["payload"]["prompt"] for call in client.calls] == [
        "large:" + "x" * 5000,
        "small",
    ]
    assert await outbox.pending_count() == 2
    rows = await circuit_db.fetchall(
        "SELECT attempts FROM coding_session_bridge_outbox ORDER BY id"
    )
    assert [row["attempts"] for row in rows] == [1, 0]
    circuit = (await outbox.delivery_status())["publisher"]["transport_circuit"]
    assert circuit["state"] == "closed"
    assert circuit["failure_count"] == 0


@pytest.mark.anyio
async def test_true_outage_opens_after_bounded_cross_lane_probe(
    circuit_db: LocalDatabase,
) -> None:
    client = _OfflineClient()
    outbox = CodingSessionBridgeOutbox(
        db=circuit_db,
        client=client,  # type: ignore[arg-type]
        token_repo=_TokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
        circuit_config=PublisherCircuitConfig(
            offline_failures_to_open=2,
            offline_cooldown_seconds=60,
        ),
    )
    for index in range(5):
        await outbox.enqueue(
            _request(
                session=f"lane-{index}",
                stable_id=f"event-{index}",
                text=f"event-{index}",
            )
        )

    first = await outbox.sync_pending()
    second = await outbox.sync_pending()

    assert first == {"sent": 0, "failed": 2, "blocked": "transport_offline"}
    assert second == {"sent": 0, "failed": 0, "blocked": "transport_offline"}
    assert len(client.calls) == 2
    attempts = await circuit_db.fetchall(
        "SELECT attempts FROM coding_session_bridge_outbox ORDER BY id"
    )
    assert [row["attempts"] for row in attempts] == [1, 1, 0, 0, 0]
    circuit = (await outbox.delivery_status())["publisher"]["transport_circuit"]
    assert circuit["state"] == "open"
    assert circuit["reason"] == "repeated_transport_offline"
    assert circuit["failure_count"] == 2
    assert circuit["retry_in_seconds"] > 0


@pytest.mark.anyio
async def test_half_open_probe_closes_circuit_and_converges_after_recovery(
    circuit_db: LocalDatabase,
) -> None:
    client = _OfflineClient(recover_after=2)
    outbox = CodingSessionBridgeOutbox(
        db=circuit_db,
        client=client,  # type: ignore[arg-type]
        token_repo=_TokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
        circuit_config=PublisherCircuitConfig(
            offline_failures_to_open=2,
            offline_cooldown_seconds=0.001,
        ),
    )
    for index in range(3):
        await outbox.enqueue(
            _request(
                session=f"lane-{index}",
                stable_id=f"event-{index}",
                text=f"event-{index}",
            )
        )

    assert (await outbox.sync_pending())["blocked"] == "transport_offline"
    await asyncio.sleep(0.002)
    recovered = await outbox.sync_pending()

    assert recovered == {"sent": 1, "failed": 0, "blocked": None}
    circuit = (await outbox.delivery_status())["publisher"]["transport_circuit"]
    assert circuit["state"] == "closed"
    assert circuit["failure_count"] == 0
    assert await outbox.pending_count() == 2


@pytest.mark.anyio
async def test_background_publisher_continues_immediately_after_full_batch(
    circuit_db: LocalDatabase,
) -> None:
    client = _CountingClient(expected=5)
    outbox = CodingSessionBridgeOutbox(
        db=circuit_db,
        client=client,  # type: ignore[arg-type]
        token_repo=_TokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
        circuit_config=PublisherCircuitConfig(
            batch_size=2,
            poll_interval_seconds=60,
        ),
    )
    for index in range(5):
        await outbox.enqueue(
            _request(
                session=f"lane-{index}",
                stable_id=f"event-{index}",
                text=f"event-{index}",
            )
        )

    await outbox.start_background()
    try:
        await asyncio.wait_for(client.complete.wait(), timeout=1)
        async with asyncio.timeout(1):
            while await outbox.pending_count() != 0:
                await asyncio.sleep(0)
    finally:
        await asyncio.wait_for(outbox.stop_background(), timeout=1)

    assert len(client.calls) == 5
    assert await outbox.pending_count() == 0
