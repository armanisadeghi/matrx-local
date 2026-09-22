"""The two defects that ended the owner's desktop-agent runs on 2026-09-22.

Measured from `chat.tool_call` for conversations 86d096b9… and 60b6f5e7…
(claim lag = `claimed_at - created_at`):

* most calls were claimed in 0.6-1.2 s — the broadcast wake works;
* nine were claimed after 16, 17, 19, 16, 33, 50, 64 and 82 s — one whole
  poll interval or more. Every one of them was created while the desktop was
  INSIDE the sweep that produced the previous result, because the sweep
  executes the tool and drains the continuation stream. `_loop` cleared the
  wake event AFTER the sweep, so the wake published during it was thrown
  away and the call waited out the full backstop poll.
* the run then ended on HTTP 409 `outstanding_delegated_calls`: the engine's
  per-conversation continuation fact stayed `needed: True` after the UI had
  already used it (the user_request_id does not change across a tool-using
  turn), so the UI's next wait returned it instantly and posted `/resume`
  while `toolu_01KCH6aM36tYXNk6CBExGmEZ` was still delegated.

These guards fail on the code as it was and pass on the code as it is.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.services.delegation.engine import (
    ACTIVE_POLL_INTERVAL,
    UNSETTLED_CALL_STATES,
    DelegationEngine,
)
from app.services.delegation.outbox import MemoryDelegationOutbox


def _engine(poll_interval: float = 15.0) -> DelegationEngine:
    engine = DelegationEngine(
        client=None,  # type: ignore[arg-type]
        poll_interval=poll_interval,
        outbox=MemoryDelegationOutbox(),
    )

    async def no_creds() -> None:
        return None

    engine._get_credentials = no_creds  # type: ignore[method-assign]
    return engine


# ── the lost wake ────────────────────────────────────────────────────────


def test_a_wake_that_arrives_during_a_sweep_is_not_thrown_away():
    """The next delegated call of a turn is created DURING the sweep."""
    engine = _engine()
    sweeps: list[float] = []

    async def slow_sweep() -> int:
        sweeps.append(time.monotonic())
        if len(sweeps) == 1:
            # aidream publishes the wake for the NEXT call while this sweep is
            # still executing the previous one and draining its continuation.
            engine.request_sweep("wake during sweep")
            await asyncio.sleep(0.05)
        if len(sweeps) >= 2:
            engine._stop.set()
            engine._wake.set()
        return 0

    engine.sweep_once = slow_sweep  # type: ignore[method-assign]

    async def run() -> None:
        await asyncio.wait_for(engine._loop(), timeout=2.0)

    started = time.monotonic()
    asyncio.run(run())
    assert len(sweeps) >= 2, "the wake never produced a second sweep"
    # Without the fix the second sweep waits the full 15 s poll interval.
    assert sweeps[1] - started < 1.0, (
        f"second sweep started {sweeps[1] - started:.2f}s in — the wake was "
        "cleared after the sweep instead of before it"
    )


# ── the backstop tightens only while something is in flight ──────────────


def test_the_backstop_poll_is_lazy_when_nothing_is_happening():
    engine = _engine(poll_interval=15.0)
    assert engine._next_interval() == 15.0


@pytest.mark.parametrize("state", sorted(UNSETTLED_CALL_STATES))
def test_the_backstop_poll_tightens_while_a_call_is_unsettled(state: str):
    engine = _engine(poll_interval=15.0)
    engine._note_call("conv_1", "call_1", "local_window", state)
    assert engine._next_interval() == ACTIVE_POLL_INTERVAL


def test_the_backstop_poll_tightens_while_the_ui_owns_a_stream():
    engine = _engine(poll_interval=15.0)
    engine.claim_ui_stream("conv_1", ttl_seconds=30)
    assert engine._next_interval() == ACTIVE_POLL_INTERVAL


# ── the stale continuation that produced the 409 ─────────────────────────


def test_a_continuation_is_not_offered_while_a_sibling_call_is_running():
    """The exact shape of the owner's 19:21:55 failure."""
    engine = _engine()
    # Call A ran, was delivered, and the server said a continuation is due.
    engine._note_call("conv_1", "call_a", "local_window", "delivered")
    engine._note_continuation("conv_1", "req_1", True)
    assert engine.ui_conversation_state("conv_1")["continuation"]["needed"] is True

    # The turn resumed and suspended again: call B is now running here.
    engine._note_call("conv_1", "call_b", "local_window", "executing")

    state = engine.ui_conversation_state("conv_1")
    assert [c["call_id"] for c in state["outstanding"]] == ["call_b"]
    continuation = state["continuation"]
    assert not (continuation and continuation.get("needed")), (
        "the engine offered a continuation while call_b was still delegated — "
        "posting /resume on it is HTTP 409 outstanding_delegated_calls"
    )


def test_the_continuation_returns_once_the_sibling_lands():
    engine = _engine()
    engine._note_call("conv_1", "call_b", "local_window", "executing")
    engine._note_call("conv_1", "call_b", "local_window", "delivered")
    engine._note_continuation("conv_1", "req_1", True)
    state = engine.ui_conversation_state("conv_1")
    assert state["outstanding"] == []
    assert state["continuation"]["user_request_id"] == "req_1"


def test_a_new_running_call_invalidates_the_recorded_continuation():
    engine = _engine()
    engine._note_continuation("conv_1", "req_1", True)
    engine._note_call("conv_1", "call_b", "local_window", "executing")
    facts = engine._conversation_facts["conv_1"]
    assert facts["continuation"] is None
