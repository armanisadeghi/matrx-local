"""The Coding Sessions read must never hold the engine, or the response, open.

Lane V-ML measured the shipped behaviour on 2026-09-15, on the app Arman opens:

  * ``GET /coding-session/claude/overview`` took 31.76 s, then 58.96 s, against
    the desktop client's hard 60 s ceiling (``desktop/src/lib/api.ts``);
  * on a fresh engine the same call took 1,209 s;
  * while it ran, ``GET /health`` answered NOTHING for a minute at a time, with
    ``sample(1)`` showing 2,182 of 2,261 samples of the asyncio thread inside
    ``os_open``.

Three separate causes, one per test below:

  1. the index scan was on the request path (and the account counts were an
     ``rglob`` of all 67,224 record files ON THE EVENT LOOP);
  2. the walk ran unbounded, so nothing else in the engine got scheduled;
  3. the server inventory was fetched inline.

Each test fails if its cause comes back.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from app.services.coding_sessions import overview as overview_module
from app.services.coding_sessions import session_providers
from app.services.coding_sessions.claude_index_store import ClaudeIndexStore
from app.services.coding_sessions.claude_provider import ClaudeCodeSessionProvider


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _write_records(root: Path, count: int) -> None:
    """A tree big enough that an unbounded read would be visible."""
    folder = root / "acct-1" / "org-1"
    folder.mkdir(parents=True, exist_ok=True)
    filler = "x" * 4096
    for index in range(count):
        session_id = f"{index:08d}-1111-4111-8111-111111111111"
        (folder / f"local_{session_id}.json").write_text(
            json.dumps(
                {
                    "cliSessionId": session_id,
                    "title": f"Conversation {index}",
                    "titleSource": "auto",
                    "cwd": "/Users/someone/code/matrx-local",
                    "lastActivityAt": 1_788_868_800_000 + index,
                    "padding": filler,
                }
            )
        )


@pytest.fixture
def isolated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.services.coding_sessions import claude_overview, cloud_state

    store = ClaudeIndexStore(tmp_path / "store" / "index.sqlite3")
    claude_overview._reset_index_state_for_tests(store)
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(tmp_path / "absent-ledger.json"))
    yield store
    claude_overview._reset_index_state_for_tests(None)


@pytest.mark.anyio
async def test_the_event_loop_keeps_answering_while_a_full_walk_runs(
    tmp_path: Path, isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A /health-shaped coroutine must still be served during the whole build.

    The bound here is the one V-ML could not get an answer inside: 200 ms. The
    in-engine refresh reads the tree in worker threads and awaits between
    chunks, so a heartbeat keeps its schedule. A synchronous direct scan would
    park the loop, which the explicit in-flight handshake below detects even
    when a fast machine finishes the complete fixture in under 210 ms.
    """
    from app.services.coding_sessions import claude_overview, cloud_state

    root = tmp_path / "claude-code-sessions"
    _write_records(root, 4000)

    worst = 0.0
    stop = asyncio.Event()
    primed = asyncio.Event()
    in_flight_progress = asyncio.Event()
    worker_acknowledged = threading.Event()
    loop = asyncio.get_running_loop()
    original_plan_refresh = claude_overview.plan_refresh

    async def heartbeat() -> None:
        nonlocal worst
        primed.set()
        while not stop.is_set():
            asked = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - asked - 0.01)
            if in_flight_progress.is_set():
                worker_acknowledged.set()

    def gated_plan_refresh(*args, **kwargs):
        """Require an event-loop heartbeat while the refresh is in flight."""
        loop.call_soon_threadsafe(in_flight_progress.set)
        if not worker_acknowledged.wait(timeout=0.2):
            raise AssertionError("the event loop did not answer during the scan")
        return original_plan_refresh(*args, **kwargs)

    pulse = asyncio.create_task(heartbeat())
    await primed.wait()
    monkeypatch.setattr(claude_overview, "plan_refresh", gated_plan_refresh)
    try:
        result = await claude_overview._refresh_in_threads(root, isolated_index)
    finally:
        stop.set()
        await pulse

    assert result["changed"] == 4000, result
    assert worst < 0.2, f"the event loop stalled for {worst:.3f}s during the walk"
    assert in_flight_progress.is_set()
    assert worker_acknowledged.is_set()


@pytest.mark.anyio
async def test_event_loop_handshake_rejects_a_synchronous_scan_variant() -> None:
    """Private canary: calling a scan on the loop cannot satisfy the handshake."""
    loop = asyncio.get_running_loop()
    primed = asyncio.Event()
    progress_requested = asyncio.Event()
    worker_acknowledged = threading.Event()
    stop = asyncio.Event()

    async def heartbeat() -> None:
        primed.set()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            if progress_requested.is_set():
                worker_acknowledged.set()

    def synchronous_scan_variant() -> None:
        loop.call_soon_threadsafe(progress_requested.set)
        if not worker_acknowledged.wait(timeout=0.2):
            raise AssertionError("the event loop did not answer during the scan")

    pulse = asyncio.create_task(heartbeat())
    await primed.wait()
    try:
        with pytest.raises(AssertionError, match="did not answer"):
            synchronous_scan_variant()
    finally:
        stop.set()
        await pulse


@pytest.mark.anyio
async def test_a_slow_server_never_delays_the_screen(
    tmp_path: Path, isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cloud inventory is cached and refreshed behind the response."""
    from app.services.coding_sessions import claude_overview, cloud_state

    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _slow_fetch():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        meta = {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": 1,
            "checked_at": "2026-09-15T00:00:00+00:00",
        }
        cloud_state._CLOUD_CACHE["claude_code"] = (time.monotonic(), {"abc": {}}, meta)
        return {"abc": {}}, meta

    monkeypatch.setattr(cloud_state, "_CLOUD_CACHE", {}, raising=False)
    monkeypatch.setattr(cloud_state, "_CLOUD_TASK", {}, raising=False)
    monkeypatch.setattr(cloud_state, "_fetch_cloud_inventory", _slow_fetch)
    monkeypatch.setattr(cloud_state, "_CLOUD_COLD_WAIT_SECONDS", 0.05)

    # Nothing cached yet: the first read waits only its cold budget and then
    # says out loud that the check is in flight.
    asked = time.perf_counter()
    rows, meta = await cloud_state.cloud_inventory()
    elapsed = time.perf_counter() - asked
    assert elapsed < 1.0, f"the first cloud read blocked for {elapsed:.2f}s"
    assert rows == {}
    assert meta["checked"] is False
    assert meta["reason"] == "cloud_check_in_flight"
    assert meta["refreshing"] is True
    assert "refresh" in (meta["detail"] or "").lower()

    # A second read while it is still in flight does not start a second one.
    await started.wait()
    await cloud_state.cloud_inventory()
    assert calls == 1, "a second request started a second server read"

    release.set()
    await cloud_state._CLOUD_TASK["claude_code"]
    rows, meta = await cloud_state.cloud_inventory()
    assert meta["checked"] is True
    assert rows == {"abc": {}}
    assert meta["age_seconds"] is not None


@pytest.mark.anyio
async def test_a_stale_cloud_answer_is_served_with_its_age_not_withheld(
    isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the TTL the screen still gets the last answer — and its age."""
    from app.services.coding_sessions import claude_overview, cloud_state

    old_meta = {
        "checked": True,
        "reason": None,
        "detail": None,
        "sessions": 7,
        "checked_at": "2026-09-15T00:00:00+00:00",
    }
    monkeypatch.setattr(
        cloud_state,
        "_CLOUD_CACHE",
        {"claude_code": (time.monotonic() - 600, {"abc": {}}, old_meta)},
        raising=False,
    )
    monkeypatch.setattr(cloud_state, "_CLOUD_TASK", {}, raising=False)

    refreshes = 0

    async def _fetch():
        nonlocal refreshes
        refreshes += 1
        return {}, dict(old_meta)

    monkeypatch.setattr(cloud_state, "_fetch_cloud_inventory", _fetch)

    asked = time.perf_counter()
    rows, meta = await cloud_state.cloud_inventory()
    assert time.perf_counter() - asked < 0.2
    assert rows == {"abc": {}}
    assert meta["checked"] is True
    assert meta["sessions"] == 7
    assert meta["age_seconds"] >= 600
    assert meta["refreshing"] is True

    task = cloud_state._CLOUD_TASK.get("claude_code")
    assert task is not None
    await task
    assert refreshes == 1, "a stale answer must still trigger exactly one refresh"


@pytest.mark.anyio
async def test_failed_cold_inventory_is_terminal_not_in_flight(
    isolated_index: ClaudeIndexStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected first-read failure must stop polling rather than spin."""
    from app.services.coding_sessions import claude_overview, cloud_state

    private_error = "jwt-and-personal-data-must-not-escape"

    async def _failed_fetch():
        raise RuntimeError(private_error)

    logged: list[tuple[object, ...]] = []

    def _record_error(_message: object, *args: object) -> None:
        logged.append((_message, *args))

    monkeypatch.setattr(cloud_state, "_CLOUD_CACHE", {}, raising=False)
    monkeypatch.setattr(cloud_state, "_CLOUD_TASK", {}, raising=False)
    monkeypatch.setattr(cloud_state, "_fetch_cloud_inventory", _failed_fetch)
    monkeypatch.setattr(cloud_state.logger, "error", _record_error)

    await cloud_state._refresh_cloud_inventory()

    rows, meta = await cloud_state.cloud_inventory()
    assert rows == {}
    assert meta["checked"] is False
    assert meta["reason"] == "cloud_inventory_refresh_failed"
    assert meta["refreshing"] is False
    assert meta["checked_at"] is None
    assert meta["age_seconds"] is None
    assert len(logged) == 1
    rendered = " ".join(str(part) for part in logged[0])
    assert private_error not in rendered
    assert "RuntimeError" in rendered
    assert "_failed_fetch@" in rendered


@pytest.mark.anyio
async def test_failed_warm_inventory_retains_rows_as_stale_then_success_clears_failure(
    isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed refresh preserves its last success as stale evidence only."""
    from app.services.coding_sessions import claude_overview, cloud_state

    checked_at = "2026-09-15T00:00:00+00:00"
    old_rows = {"bound-session": {"provider_session_id": "bound-session"}}
    old_meta = {
        "checked": True,
        "reason": None,
        "detail": None,
        "sessions": 1,
        "checked_at": checked_at,
    }
    monkeypatch.setattr(
        cloud_state,
        "_CLOUD_CACHE",
        {"claude_code": (time.monotonic() - 600, old_rows, old_meta)},
        raising=False,
    )
    monkeypatch.setattr(cloud_state, "_CLOUD_TASK", {}, raising=False)

    async def _failed_fetch():
        raise OSError("transport unavailable")

    monkeypatch.setattr(cloud_state, "_fetch_cloud_inventory", _failed_fetch)
    await cloud_state._refresh_cloud_inventory()

    retained_rows, retained_meta = await cloud_state.cloud_inventory()
    assert retained_rows == old_rows
    assert retained_meta["checked"] is False
    assert retained_meta["reason"] == "cloud_inventory_refresh_failed"
    assert retained_meta["checked_at"] == checked_at
    assert retained_meta["age_seconds"] >= 600
    assert retained_meta["refreshing"] is False
    assert (
        cloud_state._session_state(
            cloud_checked=False,
            binding=old_rows["bound-session"],
            activity_ns=0,
            queue={},
        )
        == "unknown"
    )

    async def _successful_fetch():
        fresh_rows = {"new-session": {"provider_session_id": "new-session"}}
        fresh_meta = {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": 1,
            "checked_at": "2026-09-16T00:00:00+00:00",
        }
        cloud_state._CLOUD_CACHE["claude_code"] = (time.monotonic(), fresh_rows, fresh_meta)
        return fresh_rows, fresh_meta

    monkeypatch.setattr(cloud_state, "_fetch_cloud_inventory", _successful_fetch)
    await cloud_state._refresh_cloud_inventory()
    _, fresh_meta = await cloud_state.cloud_inventory()
    assert fresh_meta["checked"] is True
    assert fresh_meta["reason"] is None
    assert fresh_meta["age_seconds"] is not None


@pytest.mark.anyio
async def test_inventory_refresh_cancellation_propagates(
    isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation is control flow, not a terminal cloud failure."""
    from app.services.coding_sessions import claude_overview, cloud_state

    async def _cancelled_fetch():
        raise asyncio.CancelledError

    monkeypatch.setattr(cloud_state, "_CLOUD_CACHE", {}, raising=False)
    monkeypatch.setattr(cloud_state, "_fetch_cloud_inventory", _cancelled_fetch)
    with pytest.raises(asyncio.CancelledError):
        await cloud_state._refresh_cloud_inventory()
    assert cloud_state._CLOUD_CACHE == {}


@pytest.mark.anyio
async def test_expected_inventory_block_is_not_reclassified_as_refresh_failure(
    isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known identity block remains its own checked-false answer."""
    from app.services.coding_sessions import claude_overview, cloud_state

    blocked_meta = {
        "checked": False,
        "reason": "identity_inventory_unavailable",
        "detail": "Sign in to check cloud sessions.",
        "sessions": 0,
        "checked_at": "2026-09-16T00:00:00+00:00",
    }

    async def _known_block():
        cloud_state._CLOUD_CACHE["claude_code"] = (time.monotonic(), {}, blocked_meta)
        return {}, blocked_meta

    monkeypatch.setattr(cloud_state, "_CLOUD_CACHE", {}, raising=False)
    monkeypatch.setattr(cloud_state, "_fetch_cloud_inventory", _known_block)
    await cloud_state._refresh_cloud_inventory()
    _, meta = await cloud_state.cloud_inventory()
    assert meta["checked"] is False
    assert meta["reason"] == "identity_inventory_unavailable"
    assert meta["checked_at"] == blocked_meta["checked_at"]


@pytest.mark.anyio
async def test_the_whole_response_is_built_without_touching_the_disk(
    tmp_path: Path, isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, with a real index: the response is rows plus caches.

    2,000 conversations here — roughly the real 1,934 — and a bound of one
    second against the measured 31.76 s / 58.96 s / 1,209 s.
    """
    from app.services.coding_sessions import claude_overview, cloud_state

    root = tmp_path / "claude-code-sessions"
    _write_records(root, 2000)
    monkeypatch.setenv("CLAUDE_DESKTOP_SESSIONS_DIR", str(root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    async def _no_transcripts(_store):
        return {}

    monkeypatch.setattr(claude_overview, "_refresh_transcripts", _no_transcripts)
    await claude_overview.warm_index_cache(root)

    async def _cloud(_provider="claude_code"):
        return {}, {"checked": True, "reason": None, "detail": None, "sessions": 0,
                    "checked_at": "2026-09-15T00:00:00+00:00"}

    async def _no_queue(_provider="claude_code"):
        return {}, {"checked": True, "reason": None, "detail": None}

    async def _no_totals():
        return (0, 0), {"checked": True, "reason": None, "detail": None}

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)
    monkeypatch.setattr(overview_module, "_queue_by_session", _no_queue)
    # Only Claude Code is registered here: this test measures the Claude
    # index's cost, and letting the other three adapters read Arman's real
    # ~/.codex and Cursor stores would make the bound meaningless.
    session_providers._reset_registry_for_tests([ClaudeCodeSessionProvider()])
    monkeypatch.setattr(overview_module, "_queue_totals", _no_totals)
    # No refresh may be what answers: the helper cannot be spawned and the
    # in-engine path is booby-trapped.
    monkeypatch.setattr(
        claude_overview.claude_index_helper,
        "helper_command",
        lambda *_a, **_k: ["/nonexistent/matrx-helper"],
    )

    async def _must_not_refresh(*_a, **_k):
        raise AssertionError("the response waited for a refresh")

    monkeypatch.setattr(claude_overview, "_refresh_in_threads", _must_not_refresh)

    asked = time.perf_counter()
    out = await overview_module.overview()
    elapsed = time.perf_counter() - asked

    assert out["totals"]["conversations"] == 2000
    assert out["schema_version"] == 3
    assert out["listed_providers"] == ["claude_code"]
    assert {row["provider"] for row in out["conversations"]} == {"claude_code"}
    assert out["index"]["files_read"] == 2000
    assert elapsed < 1.0, f"the overview took {elapsed:.2f}s to answer from rows"
