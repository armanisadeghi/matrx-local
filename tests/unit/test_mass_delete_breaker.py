"""Unit: the notes-sync mass-delete circuit breaker.

Written after the 2026-08-06..08 incident: this engine propagated local
tombstones as sequential cloud soft-deletes in four waves totalling 2,600+
notes on one account. The breaker is the guarantee that a sync client can
never again silently erase a user's cloud corpus:

  - within budget, cloud deletes are allowed and recorded;
  - past budget, the breaker TRIPS and every further delete is blocked;
  - the tripped state persists in sync state (survives restarts);
  - only the explicit reset clears it;
  - the budget scales with the observed live remote corpus.
"""

from __future__ import annotations

from typing import Any

import pytest

try:
    from app.services.documents.sync_engine import (
        MASS_DELETE_MIN_ALLOWANCE,
        SyncEngine,
    )
except Exception as exc:  # pragma: no cover — env-dependent import guard
    pytest.skip(
        f"documents sync engine not importable in this environment: {exc}",
        allow_module_level=True,
    )


class _StateOnlyFm:
    """Minimal DocumentFileManager double — the breaker only touches state."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"note_hashes": {}}

    def load_sync_state(self) -> dict[str, Any]:
        return self.state

    def save_sync_state(self, state: dict[str, Any]) -> None:
        self.state = state


class _NoopSb:
    available = False


def _engine() -> SyncEngine:
    eng = SyncEngine(fm=_StateOnlyFm(), sb=_NoopSb())  # type: ignore[arg-type]
    eng._device_id = "test-device"  # skip the machine-local id file
    return eng


def test_allows_up_to_budget_then_trips() -> None:
    eng = _engine()
    for i in range(MASS_DELETE_MIN_ALLOWANCE):
        assert eng.allow_cloud_delete(f"note-{i}") is True, f"delete {i} blocked early"
    # The (budget+1)th delete trips the breaker and is blocked.
    assert eng.allow_cloud_delete("note-over") is False
    assert eng.delete_breaker_tripped is True


def test_tripped_breaker_blocks_everything_and_persists() -> None:
    eng = _engine()
    for i in range(MASS_DELETE_MIN_ALLOWANCE + 1):
        eng.allow_cloud_delete(f"note-{i}")
    assert eng.delete_breaker_tripped is True
    assert eng.allow_cloud_delete("another") is False

    # A NEW engine over the same persisted state (restart) stays tripped.
    eng2 = SyncEngine(fm=eng.fm, sb=_NoopSb())  # type: ignore[arg-type]
    eng2._device_id = "test-device"
    assert eng2.delete_breaker_tripped is True
    assert eng2.allow_cloud_delete("post-restart") is False


def test_reset_clears_trip_and_window() -> None:
    eng = _engine()
    for i in range(MASS_DELETE_MIN_ALLOWANCE + 1):
        eng.allow_cloud_delete(f"note-{i}")
    assert eng.delete_breaker_tripped is True

    result = eng.reset_delete_breaker()
    assert result["cleared"] is not None
    assert eng.delete_breaker_tripped is False
    # Budget is fresh after an explicit confirmation.
    assert eng.allow_cloud_delete("after-reset") is True


def test_budget_scales_with_remote_corpus() -> None:
    eng = _engine()
    state = eng.fm.load_sync_state()
    state["remote_live_count"] = 1000  # 10% → 100 allowed
    eng.fm.save_sync_state(state)
    for i in range(100):
        assert eng.allow_cloud_delete(f"note-{i}") is True
    assert eng.allow_cloud_delete("note-101") is False
    assert eng.delete_breaker_tripped is True


def test_small_corpus_keeps_flat_minimum() -> None:
    eng = _engine()
    state = eng.fm.load_sync_state()
    state["remote_live_count"] = 30  # 10% = 3 < flat minimum of 25
    eng.fm.save_sync_state(state)
    for i in range(MASS_DELETE_MIN_ALLOWANCE):
        assert eng.allow_cloud_delete(f"note-{i}") is True
    assert eng.allow_cloud_delete("note-over") is False
