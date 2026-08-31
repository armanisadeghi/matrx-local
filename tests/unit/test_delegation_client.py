"""Characterization: the headless suspend/resume delegation client.

Pins the full execute → tool_results → resume round-trip against an
httpx.MockTransport "aidream server" — no network, no engine boot, real
dispatcher execution (the File mega-tool actually lists a tmp dir).

Protocol contract: matrx-frontend/features/agents/docs/CLIENT_TOOL_SUSPEND_RESUME.md
Engine under test: app/services/delegation/engine.py
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from app.services.delegation.client import DelegationApiClient
from app.services.delegation.engine import DelegationEngine
from app.services.delegation.outbox import MemoryDelegationOutbox

JWT = "test-jwt"
BASE = "https://aidream.test"
INSTANCE_ID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture(autouse=True)
def _no_browser_grace_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.delegation import engine as engine_module

    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _organization_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every authenticated call now names its organization at the TRANSPORT
    (2026-08-30 admission gate). Resolving it is a Supabase round trip, so
    these transport tests stub the lookup — never the header assembly, which
    has its own guards in test_aidream_transport_organization_header.py."""
    from app.services.aidream import organization as organization_module

    async def _resolve(_jwt: str) -> str:
        return "11111111-2222-4333-8444-555555555555"

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", _resolve
    )


def _pending_call(tmp_path: Path | None = None, **overrides: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "id": "row-1",
        "call_id": "call_1",
        "conversation_id": "conv_1",
        "user_request_id": "req_1",
        "message_id": None,
        "tool_name": "local_file",
        "arguments": {"action": "list", "path": str(tmp_path) if tmp_path else "."},
        "iteration": 1,
        "created_at": "2026-07-14T00:00:00Z",
        "expires_at": None,
    }
    call.update(overrides)
    return call


class FakeServer:
    """Route table + request recorder for the three delegation endpoints."""

    def __init__(self) -> None:
        self.pending: list[dict[str, Any]] = []
        self.visible_pending: list[dict[str, Any]] | None = None
        self.requests: list[httpx.Request] = []
        self.tool_results_bodies: list[dict[str, Any]] = []
        self.resume_bodies: list[dict[str, Any]] = []
        self.tool_results_responder: (
            Callable[[httpx.Request], httpx.Response] | None
        ) = None
        self.resume_responder: Callable[[httpx.Request], httpx.Response] | None = None
        self.continuation_needed = True

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        assert request.headers.get("authorization") == f"Bearer {JWT}"
        if path == "/ai/user/pending_calls":
            has_instance = "instance_id" in dict(request.url.params)
            if has_instance:
                return httpx.Response(200, json=self.pending)
            return httpx.Response(
                200,
                json=self.pending
                if self.visible_pending is None
                else self.visible_pending,
            )
        if path.endswith("/tool_results"):
            body = json.loads(request.content.decode())
            self.tool_results_bodies.append(body)
            if self.tool_results_responder is not None:
                return self.tool_results_responder(request)
            resolved = [r["call_id"] for r in body["results"]]
            # The delivered call leaves the ledger.
            self.pending = [c for c in self.pending if c["call_id"] not in resolved]
            return httpx.Response(
                200,
                json={
                    "resolved": resolved,
                    "already_resolved": [],
                    "not_found": [],
                    "continuation_needed": self.continuation_needed,
                    "user_request_id": "req_1",
                    "conversation_id": "conv_1",
                },
            )
        if path.endswith("/resume"):
            self.resume_bodies.append(json.loads(request.content.decode()))
            if self.resume_responder is not None:
                return self.resume_responder(request)
            # NDJSON continuation stream, SSE noise included to pin tolerance.
            stream = (
                "event: tool_event\n"
                'data: {"eventName":"tool_event","data":{"event":"tool_delegated","call_id":"call_2"}}\n'
                "\n"
                '{"phase":"complete"}\n'
                '{"event":"end","data":{"reason":"complete"}}\n'
            )
            return httpx.Response(200, content=stream.encode())
        raise AssertionError(f"unexpected request path: {path}")


def _engine(
    server: FakeServer, outbox: MemoryDelegationOutbox | None = None
) -> DelegationEngine:
    client = DelegationApiClient(
        BASE,
        transport=server.transport(),
        instance_id_provider=lambda: INSTANCE_ID,
    )
    engine = DelegationEngine(
        client=client,
        poll_interval=999.0,
        outbox=outbox or MemoryDelegationOutbox(),
    )

    async def fake_creds() -> str:
        return JWT

    engine._get_credentials = fake_creds  # type: ignore[method-assign]
    return engine


async def _sweep_and_settle(engine: DelegationEngine) -> int:
    dispatched = await engine.sweep_once()
    while engine._call_tasks:
        await asyncio.gather(*list(engine._call_tasks), return_exceptions=True)
    return dispatched


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


def test_full_round_trip_execute_deliver_resume(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hi")
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)

    async def run() -> None:
        dispatched = await _sweep_and_settle(engine)
        assert dispatched == 1

    asyncio.run(run())

    # tool_results: exactly one POST, correct wire shape, real execution output.
    assert len(server.tool_results_bodies) == 1
    assert server.tool_results_bodies[0]["instance_id"] == INSTANCE_ID
    result = server.tool_results_bodies[0]["results"][0]
    assert result["call_id"] == "call_1"
    assert result["tool_name"] == "local_file"
    assert result["is_error"] is False
    assert result["error_message"] is None
    assert isinstance(result["duration_ms"], int)
    assert "hello.txt" in result["output"]["output"]

    # resume: fired on continuation_needed with the desktop client envelope.
    assert len(server.resume_bodies) == 1
    resume = server.resume_bodies[0]
    assert resume["user_request_id"] == "req_1"
    assert resume["client"]["surface"] == "matrx-local/desktop"
    assert resume["client"]["capabilities"] == ["desktop-native"]
    assert resume["client"]["state"]["desktop-native"]["platform"]

    # Re-entrancy: a post-resume sweep was requested (the resumed stream
    # carried a tool_delegated event; the next sweep would pick up call_2).
    assert engine._wake.is_set()


def test_no_resume_when_continuation_not_needed(tmp_path: Path) -> None:
    server = FakeServer()
    server.continuation_needed = False
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)
    asyncio.run(_sweep_and_settle(engine))
    assert len(server.tool_results_bodies) == 1
    assert server.resume_bodies == []


def test_browser_gets_grace_period_before_desktop_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.delegation import engine as engine_module

    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)
    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.01)

    started = time.monotonic()
    asyncio.run(_sweep_and_settle(engine))

    assert time.monotonic() - started >= 0.009
    assert len(server.resume_bodies) == 1


def test_ui_claim_defers_resume_then_self_heals_on_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the desktop Cloud Chat UI holds a stream claim the engine
    executes + delivers but leaves /resume to the UI; releasing (or losing)
    the claim lets the retained result obligation resume headlessly."""
    from app.services.delegation import engine as engine_module

    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)
    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)

    engine.claim_ui_stream("conv_1", ttl_seconds=60.0)
    asyncio.run(_sweep_and_settle(engine))

    # Delivered, but no resume — the UI owns the continuation stream.
    assert len(server.tool_results_bodies) == 1
    assert server.resume_bodies == []
    # Retained obligation survives so an abandoned claim self-heals.
    assert "call_1" in engine._undelivered

    state = engine.ui_conversation_state("conv_1")
    assert state["claimed"] is True
    assert state["continuation"]["user_request_id"] == "req_1"
    assert state["continuation"]["needed"] is True
    assert state["calls"][0]["state"] == "delivered"

    # Claim gone → next sweep re-posts the retained result (idempotent) and
    # resumes headlessly since continuation is still needed.
    engine.release_ui_stream("conv_1")
    asyncio.run(_sweep_and_settle(engine))
    assert len(server.resume_bodies) == 1
    assert engine.ui_conversation_state("conv_1")["claimed"] is False


# ---------------------------------------------------------------------------
# Ownership + dedup
# ---------------------------------------------------------------------------


def test_foreign_tool_left_for_its_owner() -> None:
    server = FakeServer()
    server.pending = [_pending_call(tool_name="read_page", call_id="call_x")]
    engine = _engine(server)

    async def run() -> None:
        dispatched = await _sweep_and_settle(engine)
        assert dispatched == 0

    asyncio.run(run())
    # No execution, no result POST — a browser tool is the extension's job.
    assert server.tool_results_bodies == []


def test_duplicate_pending_call_executes_once(tmp_path: Path) -> None:
    server = FakeServer()
    call = _pending_call(tmp_path)
    engine = _engine(server)

    async def run() -> None:
        server.pending = [call]
        await _sweep_and_settle(engine)
        # Server keeps listing it (e.g. resolution raced) — must NOT re-run.
        server.pending = [call]
        dispatched = await _sweep_and_settle(engine)
        assert dispatched == 0

    asyncio.run(run())
    assert len(server.tool_results_bodies) == 1


# ---------------------------------------------------------------------------
# Error results
# ---------------------------------------------------------------------------


def test_tool_error_becomes_is_error_result(tmp_path: Path) -> None:
    server = FakeServer()
    server.continuation_needed = False
    server.pending = [
        _pending_call(tmp_path, arguments={"action": "definitely_not_an_action"})
    ]
    engine = _engine(server)
    asyncio.run(_sweep_and_settle(engine))
    result = server.tool_results_bodies[0]["results"][0]
    assert result["is_error"] is True
    assert result["output"] is None
    assert "definitely_not_an_action" in result["error_message"]


def test_screen_result_delivery_contains_cloud_ref_and_no_base64() -> None:
    """Delegated screenshots cross the durable boundary as Content IR refs."""
    server = FakeServer()
    server.continuation_needed = False
    server.pending = [
        _pending_call(
            tool_name="local_screen",
            arguments={"action": "screenshot"},
        )
    ]
    engine = _engine(server)

    async def fake_execute(
        entry: Any, tool_name: str, call_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        output: dict[str, Any] = {
            "kind": "image_ref",
            "artifact_id": "artifact-1",
            "availability": "cloud_ready",
            "media_type": "image/png",
            "file_id": "file-1",
            "media_ref": {"file_id": "file-1"},
        }
        return {
            "call_id": call_id,
            "tool_name": tool_name,
            "output": output,
            "is_error": False,
            "error_message": None,
            "duration_ms": 1,
        }

    engine._execute = fake_execute  # type: ignore[method-assign]
    asyncio.run(_sweep_and_settle(engine))

    result = server.tool_results_bodies[0]["results"][0]
    assert result["tool_name"] == "local_screen"
    assert result["is_error"] is False
    assert result["output"]["file_id"] == "file-1"
    assert "base64" not in json.dumps(result).lower()
    assert "/tmp/" not in json.dumps(result)


def test_malformed_pending_rows_are_skipped() -> None:
    server = FakeServer()
    server.pending = [{"tool_name": "local_file"}]  # no call_id / conversation_id
    engine = _engine(server)

    async def run() -> None:
        assert await _sweep_and_settle(engine) == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Delivery retry (execute once, deliver until acknowledged)
# ---------------------------------------------------------------------------


def test_failed_delivery_is_retried_without_reexecution(tmp_path: Path) -> None:
    server = FakeServer()
    server.continuation_needed = False
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)

    executions = 0
    orig_execute = engine._execute

    async def counting_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal executions
        executions += 1
        return await orig_execute(*args, **kwargs)

    engine._execute = counting_execute  # type: ignore[method-assign]

    fail_next = {"on": True}

    def flaky(request: httpx.Request) -> httpx.Response:
        if fail_next["on"]:
            fail_next["on"] = False
            return httpx.Response(503, text="upstream sad")
        return httpx.Response(
            200,
            json={
                "resolved": ["call_1"],
                "already_resolved": [],
                "not_found": [],
                "continuation_needed": False,
                "user_request_id": "req_1",
                "conversation_id": "conv_1",
            },
        )

    server.tool_results_responder = flaky

    async def run() -> None:
        await _sweep_and_settle(engine)
        assert "call_1" in engine._undelivered
        server.pending = []  # resolved server-side view irrelevant; retry is local
        await _sweep_and_settle(engine)

    asyncio.run(run())
    assert executions == 1
    assert engine._undelivered == {}
    assert len(server.tool_results_bodies) == 2  # one failed attempt + one success


def test_failed_resume_reposts_result_until_continuation_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.delegation import engine as engine_module

    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(engine_module, "_RESUME_SUPPRESS_TTL", 0.0)
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)
    executions = 0
    original_execute = engine._execute

    async def counting_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal executions
        executions += 1
        return await original_execute(*args, **kwargs)

    engine._execute = counting_execute  # type: ignore[method-assign]
    attempts = {"n": 0}

    def resume(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, text="temporary resume outage")
        return httpx.Response(
            200, content=b'{"event":"end","data":{"reason":"complete"}}\n'
        )

    server.resume_responder = resume

    async def run() -> None:
        await _sweep_and_settle(engine)
        assert "call_1" in engine._undelivered
        server.pending = []
        await _sweep_and_settle(engine)

    asyncio.run(run())
    assert executions == 1
    assert attempts["n"] == 2
    assert len(server.tool_results_bodies) == 2
    assert engine._undelivered == {}


def test_fatal_resume_stream_keeps_retry_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.delegation import engine as engine_module

    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    server.resume_responder = lambda request: httpx.Response(
        200,
        content=(
            b'{"event":"error","data":{"error_type":"system_error",'
            b'"message":"synthetic fatal"}}\n'
            b'{"event":"end","data":{"reason":"complete"}}\n'
        ),
    )
    engine = _engine(server)

    asyncio.run(_sweep_and_settle(engine))

    assert "call_1" in engine._undelivered
    assert "fatal error" in (engine._last_error or "")


def test_truncated_resume_stream_keeps_retry_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.delegation import engine as engine_module

    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    server.resume_responder = lambda request: httpx.Response(
        200, content=b'{"event":"text","data":{"text":"partial"}}\n'
    )
    engine = _engine(server)

    asyncio.run(_sweep_and_settle(engine))

    assert "call_1" in engine._undelivered
    assert "terminal end event" in (engine._last_error or "")


def test_cancelled_resume_stream_keeps_retry_obligation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.delegation import engine as engine_module

    monkeypatch.setattr(engine_module, "_BROWSER_RESUME_GRACE_SECONDS", 0.0)
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    server.resume_responder = lambda request: httpx.Response(
        200, content=b'{"event":"end","data":{"reason":"cancelled"}}\n'
    )
    engine = _engine(server)

    asyncio.run(_sweep_and_settle(engine))

    assert "call_1" in engine._undelivered


def test_restart_redelivers_saved_result_without_reexecution(tmp_path: Path) -> None:
    server = FakeServer()
    server.continuation_needed = False
    server.pending = [_pending_call(tmp_path)]
    outbox = MemoryDelegationOutbox()
    saved = {
        "call_id": "call_1",
        "tool_name": "local_file",
        "output": {"output": "saved result"},
        "is_error": False,
        "error_message": None,
        "duration_ms": 10,
    }

    async def seed_and_run() -> None:
        assert await outbox.enqueue(server.pending[0])
        assert await outbox.mark_executing("call_1")
        assert await outbox.store_result("call_1", saved)
        engine = _engine(server, outbox)

        async def must_not_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("restored call was executed again")

        engine._execute = must_not_execute  # type: ignore[method-assign]
        assert await _sweep_and_settle(engine) == 0

    asyncio.run(seed_and_run())
    assert server.tool_results_bodies[0]["results"] == [saved]
    assert outbox.entries == {}


def test_restart_does_not_repeat_ambiguous_side_effect(tmp_path: Path) -> None:
    server = FakeServer()
    server.continuation_needed = False
    server.pending = [_pending_call(tmp_path)]
    outbox = MemoryDelegationOutbox()

    async def seed_and_run() -> None:
        assert await outbox.enqueue(server.pending[0])
        assert await outbox.mark_executing("call_1")
        outbox.entries["call_1"]["lease_expires_at"] = 0.0
        engine = _engine(server, outbox)

        async def must_not_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("ambiguous call was executed again")

        engine._execute = must_not_execute  # type: ignore[method-assign]
        assert await _sweep_and_settle(engine) == 0

    asyncio.run(seed_and_run())
    result = server.tool_results_bodies[0]["results"][0]
    assert result["is_error"] is True
    assert "not executed again" in result["error_message"]
    assert outbox.entries == {}


def test_restart_executes_work_that_never_left_the_queue(tmp_path: Path) -> None:
    server = FakeServer()
    server.continuation_needed = False
    server.pending = [_pending_call(tmp_path)]
    outbox = MemoryDelegationOutbox()
    executions = 0

    async def seed_and_run() -> None:
        nonlocal executions
        assert await outbox.enqueue(server.pending[0])
        engine = _engine(server, outbox)
        original_execute = engine._execute

        async def counting_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal executions
            executions += 1
            return await original_execute(*args, **kwargs)

        engine._execute = counting_execute  # type: ignore[method-assign]
        assert await _sweep_and_settle(engine) == 1

    asyncio.run(seed_and_run())
    assert executions == 1
    assert outbox.entries == {}


def test_orphaned_never_executed_queue_entry_is_discarded(tmp_path: Path) -> None:
    server = FakeServer()
    call = _pending_call(tmp_path)
    outbox = MemoryDelegationOutbox()

    async def seed_and_run() -> None:
        assert await outbox.enqueue(call)
        engine = _engine(server, outbox)
        assert await _sweep_and_settle(engine) == 0

    asyncio.run(seed_and_run())
    assert outbox.entries == {}


def test_cleanup_failure_does_not_repost_every_sweep(tmp_path: Path) -> None:
    class DeleteFailingOutbox(MemoryDelegationOutbox):
        async def delete(self, call_id: str) -> None:
            raise OSError("synthetic cleanup failure")

    server = FakeServer()
    server.continuation_needed = False
    server.pending = [_pending_call(tmp_path)]
    outbox = DeleteFailingOutbox()
    engine = _engine(server, outbox)

    async def run() -> None:
        await _sweep_and_settle(engine)
        server.pending = []
        await _sweep_and_settle(engine)

    asyncio.run(run())
    assert len(server.tool_results_bodies) == 1


def test_auth_rejection_keeps_durable_result_for_refreshed_credentials(
    tmp_path: Path,
) -> None:
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    outbox = MemoryDelegationOutbox()
    engine = _engine(server, outbox)
    server.tool_results_responder = lambda request: httpx.Response(401, text="expired")
    asyncio.run(_sweep_and_settle(engine))
    assert "call_1" in engine._undelivered
    assert outbox.entries["call_1"]["state"] == "result_pending"
    assert server.resume_bodies == []


def test_malformed_200_ack_keeps_durable_result(tmp_path: Path) -> None:
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    outbox = MemoryDelegationOutbox()
    engine = _engine(server, outbox)
    server.tool_results_responder = lambda request: httpx.Response(200, json={})
    asyncio.run(_sweep_and_settle(engine))
    assert "call_1" in engine._undelivered
    assert outbox.entries["call_1"]["state"] == "result_pending"


def test_blank_continuation_id_keeps_durable_result(tmp_path: Path) -> None:
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    outbox = MemoryDelegationOutbox()
    engine = _engine(server, outbox)
    server.tool_results_responder = lambda request: httpx.Response(
        200,
        json={
            "resolved": ["call_1"],
            "already_resolved": [],
            "not_found": [],
            "continuation_needed": True,
            "user_request_id": "   ",
            "conversation_id": "conv_1",
        },
    )
    asyncio.run(_sweep_and_settle(engine))
    assert "call_1" in engine._undelivered
    assert outbox.entries["call_1"]["state"] == "result_pending"


# ---------------------------------------------------------------------------
# Resume 409 handling (§2.5) + single-flight (§2.6)
# ---------------------------------------------------------------------------


def _conflict_response(code: str, retryable: bool) -> httpx.Response:
    return httpx.Response(
        409,
        json={
            "error": code,
            "message": f"{code}: details",
            "details": {"code": code, "retryable": retryable, "status": "pending"},
        },
    )


def test_resume_conflict_is_retried_bounded(tmp_path: Path) -> None:
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)

    attempts = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            return _conflict_response("resume_conflict", retryable=True)
        return httpx.Response(
            200, content=b'{"event":"end","data":{"reason":"complete"}}\n'
        )

    server.resume_responder = responder
    asyncio.run(_sweep_and_settle(engine))
    assert attempts["n"] == 3  # 2 conflicts + 1 success


def test_not_resumable_never_retries(tmp_path: Path) -> None:
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)

    attempts = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return _conflict_response("not_resumable", retryable=False)

    server.resume_responder = responder
    asyncio.run(_sweep_and_settle(engine))
    assert attempts["n"] == 1


def test_outstanding_delegated_calls_never_retries(tmp_path: Path) -> None:
    server = FakeServer()
    server.pending = [_pending_call(tmp_path)]
    engine = _engine(server)

    attempts = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return _conflict_response("outstanding_delegated_calls", retryable=False)

    server.resume_responder = responder
    asyncio.run(_sweep_and_settle(engine))
    assert attempts["n"] == 1


def test_resume_single_flight_per_user_request(tmp_path: Path) -> None:
    server = FakeServer()
    engine = _engine(server)

    async def run() -> None:
        await engine._maybe_resume("conv_1", "req_dup", JWT)
        await engine._maybe_resume("conv_1", "req_dup", JWT)  # inside TTL → suppressed

    asyncio.run(run())
    assert len(server.resume_bodies) == 1


# ---------------------------------------------------------------------------
# States, not errors
# ---------------------------------------------------------------------------


def test_idle_without_credentials_makes_no_http_calls() -> None:
    server = FakeServer()
    client = DelegationApiClient(BASE, transport=server.transport())
    engine = DelegationEngine(
        client=client,
        poll_interval=999.0,
        outbox=MemoryDelegationOutbox(),
    )

    async def no_creds() -> None:
        engine._log_idle("no signed-in user")
        return None

    engine._get_credentials = no_creds  # type: ignore[method-assign]

    async def run() -> None:
        assert await engine.sweep_once() == 0

    asyncio.run(run())
    assert server.requests == []
    status = engine.status_payload()
    assert status["active"] is False
    assert status["server_url"] == BASE
    assert status["idle_reason"] == "no signed-in user"
    assert status["counts"]["undelivered"] == 0
    assert status["timestamps"]["last_sweep_at"] is not None


def test_unreachable_server_is_a_state_not_a_crash() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = DelegationApiClient(BASE, transport=httpx.MockTransport(boom))
    engine = DelegationEngine(
        client=client,
        poll_interval=999.0,
        outbox=MemoryDelegationOutbox(),
    )

    async def fake_creds() -> str:
        return JWT

    engine._get_credentials = fake_creds  # type: ignore[method-assign]

    async def run() -> None:
        assert await engine.sweep_once() == 0
        assert engine._server_unreachable is True

    asyncio.run(run())
    status = engine.status_payload()
    assert status["server_unreachable"] is True
    assert "no route to host" in status["last_error"]


def test_visible_pending_diagnostics_explain_target_mismatch() -> None:
    """If the instance-scoped claim poll returns nothing but the user has
    delegated rows, status must show the visible rows without executing them."""
    server = FakeServer()
    server.pending = []
    server.visible_pending = [
        _pending_call(
            tool_name="local_screen",
            call_id="screen_call",
            arguments={"action": "screenshot"},
            target_instance_id="other-desktop",
            claimed_by_instance_id=None,
        )
    ]
    engine = _engine(server)

    async def run() -> None:
        assert await engine.sweep_once() == 0

    asyncio.run(run())

    assert server.tool_results_bodies == []
    status = engine.status_payload()
    assert status["pending"]["claimed_count"] == 0
    assert status["pending"]["visible_count"] == 1
    assert status["pending"]["visible_tools"] == ["local_screen:1"]
    assert (
        status["pending"]["visible_sample"][0]["target_instance_id"] == "other-desktop"
    )
    assert any(e["event"] == "visible_but_not_claimed" for e in status["recent_events"])


def test_idle_visible_pending_diagnostic_does_not_repeat_every_poll() -> None:
    server = FakeServer()
    server.pending = []
    server.visible_pending = []
    engine = _engine(server)

    async def run() -> None:
        assert await engine.sweep_once() == 0
        assert await engine.sweep_once() == 0
        assert await engine.sweep_once() == 0

    asyncio.run(run())

    scoped = [r for r in server.requests if "instance_id" in dict(r.url.params)]
    unscoped = [r for r in server.requests if "instance_id" not in dict(r.url.params)]
    assert len(scoped) == 3
    assert len(unscoped) == 1


# ---------------------------------------------------------------------------
# Broadcast wake routing
# ---------------------------------------------------------------------------


def test_delegation_wake_envelope_triggers_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.cross_component_router import route_envelope
    from app.services import delegation as delegation_pkg

    swept: list[str] = []

    class FakeEngine:
        def request_sweep(self, reason: str) -> None:
            swept.append(reason)

    monkeypatch.setattr(delegation_pkg, "get_delegation_engine", lambda: FakeEngine())

    route_envelope(
        {
            "v": 2,
            "kind": "wake",
            "direction": "server->any",
            "action": "tool_call.delegated",
            "requestId": "wake-1",
            "payload": {"conversationId": "conv_1", "callIds": ["call_1"]},
            "timestamp": 1_720_000_000_000,
            "fromInstance": {"component": "server", "instanceId": "aidream-x"},
            "toInstance": None,
        },
        "user-1",
    )
    assert len(swept) == 1
    assert "conv_1" in swept[0]
