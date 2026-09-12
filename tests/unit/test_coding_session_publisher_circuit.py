"""Fairness and outage-circuit regressions for Coding Session delivery."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.services.aidream.client import AIDreamOfflineError
from app.services.coding_sessions import service as service_module
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import (
    DELIVERY_CONCURRENCY_DEFAULT,
    DELIVERY_CONCURRENCY_MAX,
    DELIVERY_CONCURRENCY_MIN,
    DELIVERY_CONCURRENCY_SETTING,
    CodingSessionBridgeOutbox,
    PublisherCircuitConfig,
    delivery_concurrency,
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


# --------------------------------------------------------------------------
# BOUNDED CONCURRENCY ACROSS LANES (2026-09-12).
# The one-at-a-time publisher drained ~0.7 envelopes/second against a queue
# that grew ~4/second — ~193,000 envelopes across ~1,160 lanes on 1.4.89. The
# fix may only ever widen delivery ACROSS lanes: inside a lane, envelope N+1
# must not even be SENT until envelope N has been acknowledged.
# --------------------------------------------------------------------------


class _LogSink(logging.Handler):
    """The engine logger does not propagate to root, so caplog sees nothing."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)


@pytest.fixture
def logs() -> Any:
    logger = logging.getLogger("system_logger")
    sink = _LogSink()
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(sink)
    try:
        yield sink
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous_level)


class _FakeSettings:
    """Stands in for the real ~/.matrx/settings.json store, same contract."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def _use_settings(monkeypatch: pytest.MonkeyPatch, values: dict[str, Any]) -> None:
    settings = _FakeSettings(values)
    monkeypatch.setattr(service_module, "get_settings_sync", lambda: settings)


def _use_concurrency(monkeypatch: pytest.MonkeyPatch, lanes: int) -> None:
    _use_settings(monkeypatch, {DELIVERY_CONCURRENCY_SETTING: lanes})


class _LaneConcurrencyClient:
    """Records exactly what was in flight together, and in what order.

    Refuses, loudly, the one thing concurrency must never do: two envelopes of
    the SAME lane in flight at once.
    """

    def __init__(self, *, hold_seconds: float = 0.02, offline: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.events: list[tuple[str, str, str]] = []
        self.max_inflight = 0
        self.offline = offline
        self.hold_seconds = hold_seconds
        self._inflight: list[str] = []

    async def post(
        self,
        _path: str,
        payload: dict[str, Any],
        *,
        jwt: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        lane = str(payload["provider_session_id"])
        prompt = str(payload["hook_event"]["payload"]["prompt"])
        assert lane not in self._inflight, (
            f"lane {lane} had two envelopes in flight at once — order broken"
        )
        self._inflight.append(lane)
        self.max_inflight = max(self.max_inflight, len(self._inflight))
        call = {"lane": lane, "prompt": prompt, "inflight_peak": 1}
        self.calls.append(call)
        self.events.append(("start", lane, prompt))
        try:
            await asyncio.sleep(self.hold_seconds)
            call["inflight_peak"] = len(self._inflight)
            if self.offline:
                raise AIDreamOfflineError("service unreachable")
            return _ack(payload)
        finally:
            self._inflight.remove(lane)
            self.events.append(("end", lane, prompt))


def _assert_strict_lane_order(client: _LaneConcurrencyClient, lane: str) -> None:
    """Envelope N+1 of a lane is never SENT before N is acknowledged."""
    lane_events = [event for event in client.events if event[1] == lane]
    assert lane_events, f"lane {lane} never delivered anything"
    for index in range(0, len(lane_events), 2):
        start, end = lane_events[index], lane_events[index + 1]
        assert start[0] == "start" and end[0] == "end"
        assert start[2] == end[2]
    prompts = [event[2] for event in lane_events if event[0] == "start"]
    assert prompts == sorted(prompts), f"lane {lane} was reordered: {prompts}"


async def _enqueue_lane(
    outbox: CodingSessionBridgeOutbox, lane: str, rows: int
) -> None:
    for index in range(1, rows + 1):
        await outbox.enqueue(
            _request(
                session=lane,
                stable_id=f"{lane}-{index}",
                text=f"{lane}-{index}",
            )
        )


@pytest.mark.anyio
async def test_three_lane_heads_fly_together_without_reordering_any_lane(
    circuit_db: LocalDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_concurrency(monkeypatch, 3)
    client = _LaneConcurrencyClient()
    outbox = CodingSessionBridgeOutbox(
        db=circuit_db,
        client=client,  # type: ignore[arg-type]
        token_repo=_TokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
        circuit_config=PublisherCircuitConfig(),
    )
    for lane in ("alpha", "beta", "gamma"):
        await _enqueue_lane(outbox, lane, 3)

    result = await outbox.sync_pending()

    assert result == {"sent": 9, "failed": 0, "blocked": None}
    # Three DIFFERENT lanes in flight in the same tick — the throughput fix.
    assert client.max_inflight == 3
    assert max(call["inflight_peak"] for call in client.calls) == 3
    for lane in ("alpha", "beta", "gamma"):
        _assert_strict_lane_order(client, lane)
    assert len(client.calls) == 9
    assert await outbox.pending_count() == 0
    circuit = (await outbox.delivery_status())["publisher"]["transport_circuit"]
    assert circuit["config"]["delivery_concurrency"] == 3


@pytest.mark.anyio
async def test_concurrency_one_still_delivers_strictly_one_at_a_time(
    circuit_db: LocalDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_concurrency(monkeypatch, 1)
    client = _LaneConcurrencyClient()
    outbox = CodingSessionBridgeOutbox(
        db=circuit_db,
        client=client,  # type: ignore[arg-type]
        token_repo=_TokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
        circuit_config=PublisherCircuitConfig(),
    )
    for lane in ("alpha", "beta", "gamma"):
        await _enqueue_lane(outbox, lane, 3)

    result = await outbox.sync_pending()

    assert result == {"sent": 9, "failed": 0, "blocked": None}
    assert client.max_inflight == 1
    # Strict row-id order — exactly what the sequential publisher delivered.
    assert [call["prompt"] for call in client.calls] == [
        "alpha-1",
        "alpha-2",
        "alpha-3",
        "beta-1",
        "beta-2",
        "beta-3",
        "gamma-1",
        "gamma-2",
        "gamma-3",
    ]
    circuit = (await outbox.delivery_status())["publisher"]["transport_circuit"]
    assert circuit["config"]["delivery_concurrency"] == 1


@pytest.mark.anyio
async def test_half_open_circuit_sends_exactly_one_probe_before_fanning_out(
    circuit_db: LocalDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_concurrency(monkeypatch, 8)
    client = _LaneConcurrencyClient(hold_seconds=0.005, offline=True)
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
    # Distinct payload sizes so "the smallest" is a fact, not a coin toss.
    for lane, size in (
        ("lane-0", 400),
        ("lane-1", 300),
        ("lane-2", 100),
        ("lane-3", 150),
        ("lane-4", 250),
    ):
        await outbox.enqueue(
            _request(session=lane, stable_id=f"{lane}-1", text=lane + "x" * size)
        )

    assert (await outbox.sync_pending())["blocked"] == "transport_offline"
    outage_calls = len(client.calls)
    assert outage_calls == 2
    client.offline = False
    await asyncio.sleep(0.002)

    recovered = await outbox.sync_pending()

    probe, *fan_out = client.calls[outage_calls:]
    assert recovered["blocked"] is None
    # ONE probe, alone, and the smallest eligible envelope.
    assert probe["inflight_peak"] == 1
    assert probe["lane"] == "lane-3"
    # Only after it is acknowledged does the tick widen.
    assert [call["lane"] for call in fan_out] == ["lane-1", "lane-4"]
    assert max(call["inflight_peak"] for call in fan_out) == 2
    circuit = (await outbox.delivery_status())["publisher"]["transport_circuit"]
    assert circuit["state"] == "closed"


@pytest.mark.anyio
async def test_tick_metadata_explains_every_pass_of_the_publisher(
    circuit_db: LocalDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_concurrency(monkeypatch, 4)
    client = _LaneConcurrencyClient(hold_seconds=0.001)
    outbox = CodingSessionBridgeOutbox(
        db=circuit_db,
        client=client,  # type: ignore[arg-type]
        token_repo=_TokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
        circuit_config=PublisherCircuitConfig(),
    )
    for lane in ("alpha", "beta", "gamma"):
        await _enqueue_lane(outbox, lane, 1)

    await outbox.sync_pending()

    ticks = (await outbox.delivery_status())["publisher"]["ticks"]
    assert ticks["ticks_total"] == 1
    assert ticks["last_tick_sent"] == 3
    assert ticks["last_tick_failed"] == 0
    assert ticks["last_tick_blocked"] is None
    # Three lane heads were waiting when the tick started.
    assert ticks["last_tick_eligible"] == 3
    assert isinstance(ticks["last_tick_at"], str) and ticks["last_tick_at"]
    assert isinstance(ticks["last_tick_duration_ms"], float)
    assert ticks["last_tick_duration_ms"] >= 0.0
    assert isinstance(ticks["last_delivery_at"], str)
    assert ticks["last_error"] is None


@pytest.mark.anyio
async def test_idle_tick_with_eligible_rows_says_so_and_names_the_error(
    circuit_db: LocalDatabase,
    monkeypatch: pytest.MonkeyPatch,
    logs: Any,
) -> None:
    _use_concurrency(monkeypatch, 4)
    client = _LaneConcurrencyClient(hold_seconds=0.001, offline=True)
    outbox = CodingSessionBridgeOutbox(
        db=circuit_db,
        client=client,  # type: ignore[arg-type]
        token_repo=_TokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
        circuit_config=PublisherCircuitConfig(offline_failures_to_open=2),
    )
    for lane in ("alpha", "beta", "gamma"):
        await _enqueue_lane(outbox, lane, 1)

    await outbox.sync_pending()

    ticks = (await outbox.delivery_status())["publisher"]["ticks"]
    assert ticks["last_tick_sent"] == 0
    assert ticks["last_tick_eligible"] == 3
    assert ticks["last_tick_blocked"] == "transport_offline"
    assert ticks["last_error"] == {
        "code": "cloud_delivery_failed",
        "message": (
            "Cloud delivery failed; the event remains local and will be retried."
        ),
    }
    assert "publisher tick: 0 sent, 3 eligible" in logs.text
    assert "blocked=transport_offline" in logs.text


def test_delivery_concurrency_knob_clamps_loudly(
    monkeypatch: pytest.MonkeyPatch, logs: Any
) -> None:
    _use_settings(monkeypatch, {})
    assert delivery_concurrency() == DELIVERY_CONCURRENCY_DEFAULT

    _use_settings(monkeypatch, {DELIVERY_CONCURRENCY_SETTING: 3})
    assert delivery_concurrency() == 3

    _use_settings(monkeypatch, {DELIVERY_CONCURRENCY_SETTING: 0})
    assert delivery_concurrency() == DELIVERY_CONCURRENCY_MIN

    _use_settings(monkeypatch, {DELIVERY_CONCURRENCY_SETTING: 99})
    assert delivery_concurrency() == DELIVERY_CONCURRENCY_MAX

    _use_settings(monkeypatch, {DELIVERY_CONCURRENCY_SETTING: "lots"})
    assert delivery_concurrency() == DELIVERY_CONCURRENCY_DEFAULT

    warnings = [record for record in logs.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 3
    text = logs.text
    assert "is outside the supported 1–32 band" in text
    assert "is not a number" in text
    assert "Change it in Settings" in text
