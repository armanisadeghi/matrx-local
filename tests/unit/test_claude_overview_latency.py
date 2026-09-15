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
import time
from pathlib import Path

import pytest

from app.services.coding_sessions.claude_index_store import ClaudeIndexStore


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
    from app.services.coding_sessions import claude_overview

    store = ClaudeIndexStore(tmp_path / "store" / "index.sqlite3")
    claude_overview._reset_index_state_for_tests(store)
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(tmp_path / "absent-ledger.json"))
    yield store
    claude_overview._reset_index_state_for_tests(None)


@pytest.mark.anyio
async def test_the_event_loop_keeps_answering_while_a_full_walk_runs(
    tmp_path: Path, isolated_index: ClaudeIndexStore
) -> None:
    """A /health-shaped coroutine must still be served during the whole build.

    The bound here is the one V-ML could not get an answer inside: 200 ms. The
    in-engine refresh reads the tree in bounded chunks and awaits between them,
    so a heartbeat keeps its schedule; a refresh that read the tree in one hop
    (the shipped ``asyncio.to_thread(_session_index, root)`` fallback) parks
    the loop for the whole read and this goes red.
    """
    from app.services.coding_sessions import claude_overview

    root = tmp_path / "claude-code-sessions"
    _write_records(root, 4000)

    worst = 0.0
    beats = 0
    stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal worst, beats
        while not stop.is_set():
            asked = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - asked - 0.01)
            beats += 1

    pulse = asyncio.create_task(heartbeat())
    try:
        result = await claude_overview._refresh_in_threads(root, isolated_index)
    finally:
        stop.set()
        await pulse

    assert result["changed"] == 4000, result
    assert worst < 0.2, f"the event loop stalled for {worst:.3f}s during the walk"
    assert beats > 20, f"the event loop stalled: only {beats} heartbeats ran"


@pytest.mark.anyio
async def test_a_slow_server_never_delays_the_screen(
    tmp_path: Path, isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cloud inventory is cached and refreshed behind the response."""
    from app.services.coding_sessions import claude_overview

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
        claude_overview._CLOUD_CACHE = (time.monotonic(), {"abc": {}}, meta)
        return {"abc": {}}, meta

    monkeypatch.setattr(claude_overview, "_CLOUD_CACHE", None, raising=False)
    monkeypatch.setattr(claude_overview, "_CLOUD_TASK", None, raising=False)
    monkeypatch.setattr(claude_overview, "_fetch_cloud_inventory", _slow_fetch)
    monkeypatch.setattr(claude_overview, "_CLOUD_COLD_WAIT_SECONDS", 0.05)

    # Nothing cached yet: the first read waits only its cold budget and then
    # says out loud that the check is in flight.
    asked = time.perf_counter()
    rows, meta = await claude_overview.cloud_inventory()
    elapsed = time.perf_counter() - asked
    assert elapsed < 1.0, f"the first cloud read blocked for {elapsed:.2f}s"
    assert rows == {}
    assert meta["checked"] is False
    assert meta["reason"] == "cloud_check_in_flight"
    assert meta["refreshing"] is True
    assert "refresh" in (meta["detail"] or "").lower()

    # A second read while it is still in flight does not start a second one.
    await started.wait()
    await claude_overview.cloud_inventory()
    assert calls == 1, "a second request started a second server read"

    release.set()
    await claude_overview._CLOUD_TASK
    rows, meta = await claude_overview.cloud_inventory()
    assert meta["checked"] is True
    assert rows == {"abc": {}}
    assert meta["age_seconds"] is not None


@pytest.mark.anyio
async def test_a_stale_cloud_answer_is_served_with_its_age_not_withheld(
    isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the TTL the screen still gets the last answer — and its age."""
    from app.services.coding_sessions import claude_overview

    old_meta = {
        "checked": True,
        "reason": None,
        "detail": None,
        "sessions": 7,
        "checked_at": "2026-09-15T00:00:00+00:00",
    }
    monkeypatch.setattr(
        claude_overview,
        "_CLOUD_CACHE",
        (time.monotonic() - 600, {"abc": {}}, old_meta),
        raising=False,
    )
    monkeypatch.setattr(claude_overview, "_CLOUD_TASK", None, raising=False)

    refreshes = 0

    async def _fetch():
        nonlocal refreshes
        refreshes += 1
        return {}, dict(old_meta)

    monkeypatch.setattr(claude_overview, "_fetch_cloud_inventory", _fetch)

    asked = time.perf_counter()
    rows, meta = await claude_overview.cloud_inventory()
    assert time.perf_counter() - asked < 0.2
    assert rows == {"abc": {}}
    assert meta["checked"] is True
    assert meta["sessions"] == 7
    assert meta["age_seconds"] >= 600
    assert meta["refreshing"] is True

    task = claude_overview._CLOUD_TASK
    assert task is not None
    await task
    assert refreshes == 1, "a stale answer must still trigger exactly one refresh"


@pytest.mark.anyio
async def test_the_whole_response_is_built_without_touching_the_disk(
    tmp_path: Path, isolated_index: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, with a real index: the response is rows plus caches.

    2,000 conversations here — roughly the real 1,934 — and a bound of one
    second against the measured 31.76 s / 58.96 s / 1,209 s.
    """
    from app.services.coding_sessions import claude_overview

    root = tmp_path / "claude-code-sessions"
    _write_records(root, 2000)
    monkeypatch.setenv("CLAUDE_DESKTOP_SESSIONS_DIR", str(root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    async def _no_transcripts(_store):
        return {}

    monkeypatch.setattr(claude_overview, "_refresh_transcripts", _no_transcripts)
    await claude_overview.warm_index_cache(root)

    async def _cloud():
        return {}, {"checked": True, "reason": None, "detail": None, "sessions": 0,
                    "checked_at": "2026-09-15T00:00:00+00:00"}

    async def _no_queue():
        return {}

    async def _no_totals():
        return 0, 0

    monkeypatch.setattr(claude_overview, "cloud_inventory", _cloud)
    monkeypatch.setattr(claude_overview, "_queue_by_session", _no_queue)
    monkeypatch.setattr(claude_overview, "_queue_totals", _no_totals)
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
    out = await claude_overview.overview()
    elapsed = time.perf_counter() - asked

    assert out["totals"]["conversations"] == 2000
    assert out["schema_version"] == 2
    assert out["index"]["files_read"] == 2000
    assert elapsed < 1.0, f"the overview took {elapsed:.2f}s to answer from rows"
