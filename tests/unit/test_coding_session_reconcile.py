"""Guards for the Reconcile action beside the sync truth (CS-25).

Two properties matter and both were specified before the code existed:

1. IDEMPOTENT. Reconcile on a conversation that is already in sync must touch
   nothing. A button that re-uploads a 6 MB transcript every time someone
   clicks it is a new bug, not a fix.
2. QUARANTINE IS HONOURED. 148 of the 265 preserved envelopes on Arman's Mac
   are `provider_account_conflict`: AI Matrx holds that session under another
   Claude account and will refuse the delivery every single time. Reconcile
   must refuse in words, not retry forever and not fail silently.

Every test names the production change that turns it red.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.coding_sessions import sync_truth_reader as reader


def _truth(
    *,
    code: str = "in_sync",
    transcript_entries: int = 384,
    on_disk: bool = True,
    cloud_entries: int | None = 384,
    error_entries: int = 0,
    pending_entries: int = 0,
    conversation_id: str | None = "cf1d62fc-0000-0000-0000-000000000000",
    cloud_checked: bool = True,
    quarantine: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": "460c5cf5-39a9-4f93-af06-9bb196509561",
        "verdict": {"code": code},
        "counts": {"transcript": transcript_entries},
        "transcript": {"on_disk": on_disk, "entries": transcript_entries},
        "delivered": {"quarantine_reasons": quarantine or []},
        "cloud": {
            "checked": cloud_checked,
            "entries": cloud_entries,
            "error_entries": error_entries,
            "pending_entries": pending_entries,
            "conversation_id": conversation_id,
        },
        "mirror": {"checked": True},
    }


# --------------------------------------------------------------------------
# Idempotency, step by step. Each step decides for itself whether there is
# anything to do, which is what makes the whole action safe to click twice.
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delivery_does_nothing_when_the_cloud_already_has_every_entry() -> None:
    """Red if Reconcile ever re-uploads a complete transcript -- the failure
    mode that turns a diagnosis button into a bandwidth bug."""
    step = await reader._redeliver("s", _truth(transcript_entries=384, cloud_entries=384))
    assert step["outcome"] == "nothing_to_do"
    assert step["detail"] == "AI Matrx already has every entry in the transcript."


@pytest.mark.anyio
async def test_delivery_does_nothing_when_the_cloud_is_ahead() -> None:
    """A compacted transcript is shorter than the cloud. Red if that is ever
    mistaken for a shortfall and re-sent."""
    step = await reader._redeliver("s", _truth(transcript_entries=4363, cloud_entries=4938))
    assert step["outcome"] == "nothing_to_do"


@pytest.mark.anyio
async def test_delivery_is_skipped_when_there_is_no_file_to_send() -> None:
    step = await reader._redeliver("s", _truth(on_disk=False))
    assert step["outcome"] == "skipped"
    assert "no transcript file" in step["detail"]


@pytest.mark.anyio
async def test_reprojection_does_nothing_when_every_entry_became_a_message() -> None:
    """Red if Reconcile starts asking the server to re-project a session with
    nothing stuck -- work the server would do for no reason, on every click."""
    step = await reader._reproject("s", _truth(error_entries=0, pending_entries=0))
    assert step["outcome"] == "nothing_to_do"


@pytest.mark.anyio
async def test_reprojection_is_skipped_when_the_server_could_not_be_read() -> None:
    """Red if an unreachable server is ever reported as a completed step."""
    step = await reader._reproject("s", _truth(cloud_checked=False, cloud_entries=None))
    assert step["outcome"] == "skipped"
    assert "nothing was changed" in step["detail"]


@pytest.mark.anyio
async def test_the_pull_is_skipped_when_there_is_no_conversation_yet() -> None:
    step = await reader._pull(_truth(conversation_id=None))
    assert step["outcome"] == "skipped"


# --------------------------------------------------------------------------
# Quarantine.
# --------------------------------------------------------------------------


CONFLICT = {
    "code": "provider_account_conflict",
    "message": (
        "AI Matrx already holds this session bound to a DIFFERENT Claude "
        "account, so a delivery from this account cannot replace it. The "
        "conversation is in AI Matrx; discard this delivery."
    ),
    "count": 14820,
}


def test_an_account_conflict_is_recognised_as_blocking() -> None:
    assert reader._blocking_conflict(_truth(quarantine=[CONFLICT])) == CONFLICT


def test_an_ordinary_quarantine_reason_is_not_blocking() -> None:
    """Red if `entry_mutated` or a 409 is ever treated as permanent -- those
    are retryable and the person is entitled to a retry."""
    other = {"code": "entry_mutated", "message": "Different content.", "count": 88}
    assert reader._blocking_conflict(_truth(quarantine=[other])) is None


@pytest.mark.anyio
async def test_reconcile_refuses_to_resend_an_account_conflict_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE GUARD THAT MATTERS MOST HERE. Red if the delivery path is ever
    reached for a session AI Matrx holds under another Claude account: that
    delivery is refused server-side every time, so retrying it is an infinite
    loop that also looks, to the person, like nothing happening."""
    truth = _truth(code="quarantined", cloud_entries=0, quarantine=[CONFLICT])
    monkeypatch.setattr(reader, "sync_truth", _fixed(truth))
    delivered: list[str] = []

    async def _never(session_id: str, _truth_arg: dict[str, Any]) -> dict[str, Any]:
        delivered.append(session_id)
        return {"step": "deliver", "outcome": "queued", "detail": "sent"}

    monkeypatch.setattr(reader, "_redeliver", _never)
    monkeypatch.setattr(reader, "_reproject", _noop("reproject"))
    monkeypatch.setattr(reader, "_pull", _noop_single("pull"))

    result = await reader.reconcile("460c5cf5-39a9-4f93-af06-9bb196509561")

    assert delivered == []
    deliver_step = next(s for s in result["actions"] if s["step"] == "deliver")
    assert deliver_step["outcome"] == "refused"
    assert "different Claude account" in deliver_step["detail"]
    assert "would fail every time" in deliver_step["detail"]


# --------------------------------------------------------------------------
# The whole action, twice.
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_twice_on_a_synced_conversation_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red if any step loses its precondition and starts doing work on every
    call. The second run must report the same outcomes as the first."""
    monkeypatch.setattr(reader, "sync_truth", _fixed(_truth()))
    monkeypatch.setattr(reader, "_pull", _noop_single("pull"))

    first = await reader.reconcile("s")
    second = await reader.reconcile("s")

    outcomes = [(s["step"], s["outcome"]) for s in first["actions"]]
    assert outcomes == [(s["step"], s["outcome"]) for s in second["actions"]]
    assert outcomes == [
        ("deliver", "nothing_to_do"),
        ("reproject", "nothing_to_do"),
        ("pull", "nothing_to_do"),
    ]


@pytest.mark.anyio
async def test_reconcile_always_answers_with_freshly_read_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red if reconcile ever reports what it *expected* its steps to achieve
    instead of re-reading. An optimistic answer is the dead fish again."""
    reads: list[str] = []
    before = _truth(code="behind_local", cloud_entries=73)
    after = _truth(code="in_sync", cloud_entries=384)

    async def _sequence(session_id: str) -> dict[str, Any]:
        reads.append(session_id)
        return before if len(reads) == 1 else after

    monkeypatch.setattr(reader, "sync_truth", _sequence)
    monkeypatch.setattr(reader, "_redeliver", _noop("deliver"))
    monkeypatch.setattr(reader, "_reproject", _noop("reproject"))
    monkeypatch.setattr(reader, "_pull", _noop_single("pull"))

    result = await reader.reconcile("s")

    assert len(reads) == 2
    assert result["truth"]["verdict"]["code"] == "in_sync"


@pytest.mark.anyio
async def test_reconcile_returns_none_for_a_session_nobody_can_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red if an unknown session gets a confident-looking empty report instead
    of a 404."""

    async def _missing(session_id: str) -> None:
        return None

    monkeypatch.setattr(reader, "sync_truth", _missing)
    assert await reader.reconcile("nope") is None


# --------------------------------------------------------------------------


def _fixed(truth: dict[str, Any]):
    async def _read(session_id: str) -> dict[str, Any]:
        return truth

    return _read


def _noop(step: str):
    async def _run(session_id: str, _truth_arg: dict[str, Any]) -> dict[str, Any]:
        return {"step": step, "outcome": "nothing_to_do", "detail": "stub"}

    return _run


def _noop_single(step: str):
    async def _run(_truth_arg: dict[str, Any]) -> dict[str, Any]:
        return {"step": step, "outcome": "nothing_to_do", "detail": "stub"}

    return _run
