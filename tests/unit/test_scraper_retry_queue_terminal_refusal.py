"""A permanent refusal is a STATE, not a retry schedule.

WHY THIS SUITE EXISTS (SR-04, measured 2026-09-11 → 2026-09-14): the desktop
retry-queue poller asked `GET /api/scraper/queue/pending` for 22+ hours and got
HTTP 400 every single time. Its backoff-and-recover logic was correct and
tested; what it never had was the idea that a server can answer *permanently*.
So it sat in DEGRADED forever, re-asking a question that had been answered,
and the one thing it never did was say what the answer was — the server's own
sentence ("Send the X-Organization-Id header naming the organization you are
acting in") was on the wire the whole time and never reached a log or a screen.

The rule these guards hold: a 4xx that is not 408/425/429 stops the poll, parks
the service in FAILED with the server's remedy attached, and re-arms only when
a DIFFERENT user signs in. A 5xx, a timeout, or a 429 keeps the old backoff.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.scraper import retry_queue


def _status_error(status: int, body: object) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://scraper.test/api/scraper/queue/pending")
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# ── Classification ──────────────────────────────────────────────────────────


def test_organization_required_400_is_terminal_and_carries_the_servers_words() -> None:
    """The exact live body from scraper.app.matrxserver.com on 2026-09-14."""
    failure = retry_queue._classify(
        _status_error(
            400,
            {
                "error": "organization_required",
                "code": "organization_required",
                "message": "This request carried an identity but no organization.",
                "user_message": "Choose the organization you're working in, then try again.",
            },
        ),
        "get_pending failed",
    )

    assert failure.terminal is True
    assert failure.http_status == 400
    assert failure.server_code == "organization_required"
    assert failure.remedy == "Choose the organization you're working in, then try again."


def test_a_4xx_with_no_readable_reason_still_names_a_remedy() -> None:
    """Nothing fails silently — a bodyless refusal says who has to act."""
    failure = retry_queue._classify(_status_error(404, "nope"), "get_pending failed")

    assert failure.terminal is True
    assert "developer" in failure.remedy


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503])
def test_retryable_statuses_are_not_terminal(status: int) -> None:
    """Rate limits and outages are exactly what the backoff is for."""
    failure = retry_queue._classify(_status_error(status, {}), "get_pending failed")
    assert failure.terminal is False


def test_a_transport_error_is_not_terminal() -> None:
    failure = retry_queue._classify(
        httpx.ConnectError("no route to host"), "get_pending failed"
    )
    assert failure.terminal is False


def test_an_unresolvable_organization_is_terminal_with_its_own_remedy() -> None:
    from app.services.scraper.remote_client import RemoteScraperOrganizationError

    failure = retry_queue._classify(
        RemoteScraperOrganizationError(
            "no org", remedy="You need to be added to an organization."
        ),
        "get_pending failed",
    )
    assert failure.terminal is True
    assert failure.remedy == "You need to be added to an organization."


# ── The loop ────────────────────────────────────────────────────────────────


class _FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def ready(self, name: str, **fields: object) -> None:
        self.calls.append(("ready", (name,), fields))

    def degraded(self, name: str, reason: str, **fields: object) -> None:
        self.calls.append(("degraded", (name, reason), fields))

    def failed(self, name: str, error: str, **fields: object) -> None:
        self.calls.append(("failed", (name, error), fields))


@pytest.fixture
def driven_loop(monkeypatch: pytest.MonkeyPatch):
    """Run `_loop` with time removed and everything remote stubbed."""
    registry = _FakeRegistry()
    monkeypatch.setattr("app.launcher.get_registry", lambda: registry)

    state: dict[str, object] = {"sleeps": 0}

    async def _sleep(_seconds: float) -> None:
        state["sleeps"] = int(state["sleeps"]) + 1
        if int(state["sleeps"]) >= 12:
            raise asyncio.CancelledError
        return None

    monkeypatch.setattr(retry_queue.asyncio, "sleep", _sleep)
    return registry, state


@pytest.mark.anyio
async def test_a_permanent_refusal_stops_polling_and_explains_itself(
    driven_loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, state = driven_loop
    polls = {"n": 0}

    async def _poll_once() -> retry_queue.PollFailure:
        polls["n"] += 1
        return retry_queue.PollFailure(
            summary="get_pending failed: HTTP 400 organization_required",
            terminal=True,
            remedy="Choose the organization you're working in, then try again.",
            http_status=400,
            server_code="organization_required",
        )

    monkeypatch.setattr(retry_queue, "_poll_once", _poll_once)

    async def _acting_user_id() -> str:
        return "user-a"

    monkeypatch.setattr(retry_queue, "_acting_user_id", _acting_user_id)

    await retry_queue._loop()

    # The refusal was asked ONCE, not once per backoff window.
    assert polls["n"] == 1
    failed = [c for c in registry.calls if c[0] == "failed"]
    assert len(failed) == 1
    assert failed[0][2]["remedy"] == (
        "Choose the organization you're working in, then try again."
    )
    assert failed[0][2]["http_status"] == 400
    assert failed[0][2]["terminal"] is True
    # And never the misleading "unreachable" state: the server answered.
    assert not [c for c in registry.calls if c[0] == "degraded"]


@pytest.mark.anyio
async def test_a_transient_failure_still_degrades_and_keeps_retrying(
    driven_loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, _state = driven_loop
    polls = {"n": 0}

    async def _poll_once() -> retry_queue.PollFailure:
        polls["n"] += 1
        return retry_queue.PollFailure(summary="get_pending failed: HTTP 503")

    monkeypatch.setattr(retry_queue, "_poll_once", _poll_once)
    await retry_queue._loop()

    assert polls["n"] > 3
    assert [c for c in registry.calls if c[0] == "degraded"]
    assert not [c for c in registry.calls if c[0] == "failed"]


@pytest.mark.anyio
async def test_a_terminal_refusal_re_arms_for_a_different_signed_in_user(
    driven_loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is about THIS caller's request, so a new caller is a new
    question — but a token refresh for the same person is not."""
    registry, _state = driven_loop
    polls = {"n": 0}
    users = ["user-a", "user-a", "user-a", "user-b", "user-b"]

    async def _poll_once() -> retry_queue.PollFailure:
        polls["n"] += 1
        return retry_queue.PollFailure(
            summary="HTTP 400", terminal=True, remedy="do a thing", http_status=400
        )

    async def _acting_user_id() -> str:
        return users.pop(0) if users else "user-b"

    monkeypatch.setattr(retry_queue, "_poll_once", _poll_once)
    monkeypatch.setattr(retry_queue, "_acting_user_id", _acting_user_id)

    await retry_queue._loop()

    # Refused for user-a (1 poll), idle while user-a stayed signed in, then
    # asked again once user-b appeared.
    assert polls["n"] == 2
    assert len([c for c in registry.calls if c[0] == "failed"]) == 2
