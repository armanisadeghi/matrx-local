from __future__ import annotations

import threading
import asyncio

from app.api import admin_routes
from app.launcher import capture_status_thread_snapshot


def _blocked_status_worker(ready: threading.Event, release: threading.Event) -> None:
    ready.set()
    release.wait()


def test_thread_snapshot_captures_a_real_controlled_worker_without_paths_or_values() -> None:
    ready = threading.Event()
    release = threading.Event()
    worker = threading.Thread(target=_blocked_status_worker, args=(ready, release))
    worker.start()
    assert ready.wait(timeout=2)
    try:
        snapshot = capture_status_thread_snapshot(thread_limit=32, frame_limit=4)
        row = next(item for item in snapshot["threads"] if item["thread_id"] == worker.ident)
        assert row["native_thread_id"] == worker.native_id
        assert any(frame["function"] == "_blocked_status_worker" for frame in row["stack"])
        assert all("/" not in frame["file"] and "\\" not in frame["file"] for frame in row["stack"])
        assert snapshot["cpu_units"] == "cumulative_seconds"
    finally:
        release.set()
        worker.join(timeout=2)


def test_thread_snapshot_respects_thread_and_frame_bounds() -> None:
    snapshot = capture_status_thread_snapshot(thread_limit=1, frame_limit=1)
    assert len(snapshot["threads"]) <= 1
    assert all(len(row["stack"]) <= 1 for row in snapshot["threads"])


def test_status_default_is_unchanged_and_opt_in_is_bounded(monkeypatch) -> None:
    class Registry:
        def snapshot(self) -> dict[str, object]:
            return {"services": {}}

    monkeypatch.setattr(admin_routes, "get_registry", Registry)
    called = False

    def capture(*, thread_limit: int, frame_limit: int) -> dict[str, object]:
        nonlocal called
        called = True
        assert (thread_limit, frame_limit) == (3, 2)
        return {"available": True, "threads": [], "truncated": False}

    monkeypatch.setattr(admin_routes, "capture_status_thread_snapshot", capture)
    async def exercise() -> None:
        assert await admin_routes.admin_status(False, 3, 2) == {"services": {}}
        assert not called
        assert (await admin_routes.admin_status(True, 3, 2))["thread_snapshot"] == {
            "available": True,
            "threads": [],
            "truncated": False,
        }

    asyncio.run(exercise())
