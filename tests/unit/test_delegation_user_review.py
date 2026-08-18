"""User-review delegated calls — the Gmail authorization boundary, desktop side.

Pins the contract that makes `google_email_send` safe on this surface:

  - a delegated `google_email_send` is NEVER dispatched — no tool runs, no
    mail leaves, and the call is parked for the human instead;
  - the parked review exposes the exact proposed message so the card can
    render it (this is the one place arguments are surfaced on purpose);
  - only an explicit decision resolves it, and the delivered tool result
    matches matrx-frontend's `GoogleEmailSendResult` shape;
  - declining is a normal outcome (`declined`), dismissing is `cancelled`,
    and a failed send is `{sent: false, error}` — never a success;
  - a second decision for the same call_id delivers nothing (no double send);
  - the user's tool-exposure gate still refuses it with an error result;
  - a review the server no longer lists is dropped, never auto-answered.

Engine under test: app/services/delegation/engine.py + user_review.py.
Harness mirrors tests/unit/test_delegation_disabled_tools.py.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.services.delegation.engine import (
    DISABLED_TOOL_ERROR_MESSAGE,
    DelegationEngine,
)
from app.services.delegation.client import DelegationApiClient
from app.services.delegation.outbox import MemoryDelegationOutbox
from app.services.delegation.user_review import (
    build_review_output,
    normalize_review_arguments,
)

JWT = "test-jwt"
BASE = "https://aidream.test"

EMAIL_ARGS = {
    "to": "recipient@example.com",
    "cc": ["cc@example.com"],
    "subject": "Quarterly numbers",
    "body": "Here are the numbers we discussed.",
}


@pytest.fixture(autouse=True)
def _no_browser_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.delegation import engine as engine_module

    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)


class _FakeSettings:
    def __init__(self, disabled: list[str]) -> None:
        self.disabled = disabled

    def get(self, key: str, default: Any = None) -> Any:
        if key == "cloud_tools":
            return {"disabled_tools": self.disabled}
        return default


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    settings = _FakeSettings([])
    monkeypatch.setattr(
        "app.services.cloud_sync.settings_sync.get_settings_sync",
        lambda: settings,
    )
    return settings


class FakeServer:
    def __init__(self) -> None:
        self.pending: list[dict[str, Any]] = []
        self.tool_results_bodies: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/ai/user/pending_calls":
            return httpx.Response(200, json=self.pending)
        if path.endswith("/tool_results"):
            body = json.loads(request.content.decode())
            self.tool_results_bodies.append(body)
            resolved = [r["call_id"] for r in body["results"]]
            self.pending = [c for c in self.pending if c["call_id"] not in resolved]
            return httpx.Response(
                200,
                json={
                    "resolved": resolved,
                    "already_resolved": [],
                    "not_found": [],
                    "continuation_needed": False,
                    "user_request_id": "req_1",
                    "conversation_id": "conv_1",
                },
            )
        raise AssertionError(f"unexpected request path: {path}")


def _pending_call(**overrides: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "id": "row-1",
        "call_id": "call_1",
        "conversation_id": "conv_1",
        "user_request_id": "req_1",
        "message_id": None,
        "tool_name": "google_email_send",
        "arguments": dict(EMAIL_ARGS),
        "iteration": 1,
        "created_at": "2026-08-18T00:00:00Z",
        "expires_at": None,
    }
    call.update(overrides)
    return call


def _engine(server: FakeServer) -> DelegationEngine:
    engine = DelegationEngine(
        client=DelegationApiClient(BASE, transport=server.transport()),
        poll_interval=999.0,
        outbox=MemoryDelegationOutbox(),
    )

    async def fake_creds() -> str:
        return JWT

    engine._get_credentials = fake_creds  # type: ignore[method-assign]
    return engine


async def _settle(engine: DelegationEngine) -> None:
    while engine._call_tasks:
        await asyncio.gather(*list(engine._call_tasks), return_exceptions=True)


def _forbid_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dispatch() must never run a user-review tool")

    monkeypatch.setattr("app.tools.dispatcher.dispatch", _explode)


# ---------------------------------------------------------------------------
# Parking (nothing runs, nothing is sent)
# ---------------------------------------------------------------------------


def test_email_send_is_parked_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_dispatch(monkeypatch)
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    dispatched = asyncio.run(engine.sweep_once())

    assert dispatched == 0
    assert server.tool_results_bodies == []  # nothing answered without a human
    reviews = engine.pending_reviews()
    assert len(reviews) == 1
    assert reviews[0]["call_id"] == "call_1"
    assert reviews[0]["kind"] == "email_review"
    # The card needs the exact proposed message.
    assert reviews[0]["arguments"] == EMAIL_ARGS
    # The UI wait loop must know a human is holding this conversation.
    assert engine.ui_conversation_state("conv_1")["reviews_pending"] == 1


def test_repeated_sweeps_do_not_duplicate_or_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_dispatch(monkeypatch)
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    async def run() -> None:
        for _ in range(3):
            assert await engine.sweep_once() == 0
        assert len(engine.pending_reviews()) == 1
        assert server.tool_results_bodies == []

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Resolution (only a human decision produces a result)
# ---------------------------------------------------------------------------


def test_send_decision_delivers_the_reviewed_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_dispatch(monkeypatch)
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    async def run() -> None:
        await engine.sweep_once()
        outcome = await engine.resolve_review(
            "call_1",
            {
                "outcome": "sent",
                "message_id": "msg-123",
                # The user edited the recipient before sending.
                "to": "someone-else@example.com",
                "cc": [],
                "subject": "Quarterly numbers (revised)",
                "from_email": "me@example.com",
                "edited": True,
            },
        )
        await _settle(engine)
        assert outcome is not None and outcome["resolved"] is True

    asyncio.run(run())

    assert len(server.tool_results_bodies) == 1
    result = server.tool_results_bodies[0]["results"][0]
    assert result["is_error"] is False
    assert result["output"] == {
        "sent": True,
        "message_id": "msg-123",
        "to": "someone-else@example.com",
        "cc": [],
        "subject": "Quarterly numbers (revised)",
        "edited": True,
        "from_email": "me@example.com",
    }
    assert engine.pending_reviews() == []


def test_decline_is_a_normal_outcome() -> None:
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    async def run() -> None:
        await engine.sweep_once()
        await engine.resolve_review("call_1", {"outcome": "declined"})
        await _settle(engine)

    asyncio.run(run())

    result = server.tool_results_bodies[0]["results"][0]
    assert result["is_error"] is False
    assert result["output"] == {"sent": False, "declined": True}


def test_failed_send_reports_not_sent_never_success() -> None:
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    async def run() -> None:
        await engine.sweep_once()
        await engine.resolve_review(
            "call_1", {"outcome": "error", "error": "Gmail rejected the message."}
        )
        await _settle(engine)

    asyncio.run(run())

    output = server.tool_results_bodies[0]["results"][0]["output"]
    assert output["sent"] is False
    assert output["error"] == "Gmail rejected the message."
    assert "message_id" not in output


def test_second_decision_delivers_nothing() -> None:
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    async def run() -> None:
        await engine.sweep_once()
        assert await engine.resolve_review("call_1", {"outcome": "declined"})
        await _settle(engine)
        # A double click, a retried request, or a second window.
        assert await engine.resolve_review("call_1", {"outcome": "sent"}) is None
        await _settle(engine)

    asyncio.run(run())

    assert len(server.tool_results_bodies) == 1


def test_unknown_call_id_is_not_resolvable() -> None:
    server = FakeServer()
    engine = _engine(server)
    assert asyncio.run(engine.resolve_review("nope", {"outcome": "sent"})) is None


# ---------------------------------------------------------------------------
# Gates and self-healing
# ---------------------------------------------------------------------------


def test_user_disabled_tool_is_refused_not_parked(
    fake_settings: _FakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_dispatch(monkeypatch)
    fake_settings.disabled = ["google_email_send"]
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    async def run() -> None:
        await engine.sweep_once()
        await _settle(engine)

    asyncio.run(run())

    assert engine.pending_reviews() == []
    result = server.tool_results_bodies[0]["results"][0]
    assert result["is_error"] is True
    assert result["error_message"] == DISABLED_TOOL_ERROR_MESSAGE


def test_review_dropped_when_server_no_longer_lists_it() -> None:
    server = FakeServer()
    server.pending = [_pending_call()]
    engine = _engine(server)

    async def run() -> None:
        await engine.sweep_once()
        assert len(engine.pending_reviews()) == 1
        # Expired server-side, or resolved by another client.
        server.pending = []
        await engine.sweep_once()
        assert engine.pending_reviews() == []
        # Dropping is never an answer — nothing was ever sent or reported.
        assert server.tool_results_bodies == []

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Argument normalization (a malformed proposal still gets a card)
# ---------------------------------------------------------------------------


def test_normalize_arguments_tolerates_a_sloppy_proposal() -> None:
    normalized = normalize_review_arguments(
        "google_email_send",
        {"to": "  a@b.com ", "cc": "x@y.com, z@y.com", "subject": None, "body": 42},
    )
    assert normalized == {
        "to": "a@b.com",
        "cc": ["x@y.com", "z@y.com"],
        "subject": "",
        "body": "",
    }


def test_agent_cannot_smuggle_a_confirmation_flag() -> None:
    normalized = normalize_review_arguments(
        "google_email_send",
        {**EMAIL_ARGS, "user_confirmed": True, "always_send": True},
    )
    assert set(normalized) == {"to", "cc", "subject", "body"}
    # And no decision the agent could name produces a send.
    assert build_review_output("google_email_send", {"outcome": "user_confirmed"}) == {
        "sent": False,
        "error": "The message was not sent.",
    }
