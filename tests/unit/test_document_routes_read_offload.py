"""Notes route filesystem reads remain responsive and content-minimal."""

from __future__ import annotations

import asyncio
import inspect
import threading
from contextvars import ContextVar
from pathlib import Path

from starlette.requests import Request

from app.api import document_routes
from app.services.documents.file_manager import DocumentFileManager


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/notes/tree",
            "headers": [],
            "query_string": b"",
        }
    )


class TempTrackingFileManager(DocumentFileManager):
    def __init__(self, root: Path) -> None:
        self._explicit_base = root
        self.scan_calls = 0
        self.read_calls = 0

    def scan_all(self):  # pragma: no cover - must never be called by tree
        self.scan_calls += 1
        raise AssertionError("tree counts must not scan or hash note bodies")

    def read_note(self, file_path: str):  # pragma: no cover - same guard
        self.read_calls += 1
        raise AssertionError("tree counts must not read note bodies")


def test_tree_counts_actual_paths_without_scanning_bodies(tmp_path, monkeypatch) -> None:
    root = tmp_path / "notes"
    (root / "Alpha").mkdir(parents=True)
    (root / ".sync").mkdir()
    (root / "Alpha" / "one.md").write_text("one", encoding="utf-8")
    (root / "Alpha" / "two.md").write_text("two", encoding="utf-8")
    (root / "root.md").write_text("root", encoding="utf-8")
    (root / ".sync" / "hidden.md").write_text("hidden", encoding="utf-8")
    (root / "Alpha" / "ignore.txt").write_text("ignore", encoding="utf-8")
    fm = TempTrackingFileManager(root)
    monkeypatch.setattr(document_routes, "file_manager", fm)
    monkeypatch.setattr(document_routes, "_configure_sync", lambda _request: None)

    result = asyncio.run(document_routes.get_folder_tree(_request()))

    assert result["total_notes"] == 3
    assert result["unfiled_notes"] == 1
    assert result["folders"][0]["name"] == "Alpha"
    assert result["folders"][0]["note_count"] == 2
    assert fm.scan_calls == 0
    assert fm.read_calls == 0


def test_tree_read_offload_keeps_event_loop_and_context_responsive(tmp_path, monkeypatch) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    (root / "note.md").write_text("body", encoding="utf-8")
    started = threading.Event()
    release = threading.Event()
    account = ContextVar("account", default=None)

    class SlowPathsFileManager(TempTrackingFileManager):
        def __init__(self) -> None:
            super().__init__(root)
            self.worker_account = None

        def list_note_paths(self):
            self.worker_account = account.get()
            started.set()
            assert release.wait(timeout=2)
            return super().list_note_paths()

    fm = SlowPathsFileManager()
    monkeypatch.setattr(document_routes, "file_manager", fm)
    monkeypatch.setattr(document_routes, "_configure_sync", lambda _request: None)

    async def run() -> None:
        token = account.set("account-a")
        try:
            tree_task = asyncio.create_task(document_routes.get_folder_tree(_request()))
            await asyncio.to_thread(started.wait)
            heartbeat = False
            await asyncio.sleep(0)
            heartbeat = True
            assert heartbeat is True
            release.set()
            result = await tree_task
        finally:
            account.reset(token)
        assert result["total_notes"] == 1

    asyncio.run(run())
    assert fm.worker_account == "account-a"


def test_every_async_route_scan_is_offloaded_and_legacy_lookup_uses_paths() -> None:
    source = inspect.getsource(document_routes)
    assert "file_manager.scan_all()" not in source
    assert source.count("await offload_read_only(file_manager.scan_all)") == 2
    assert source.count("await offload_read_only(file_manager.list_note_paths)") >= 5
    for route_name in ("get_note", "update_note", "delete_note"):
        route_source = inspect.getsource(getattr(document_routes, route_name))
        assert "list_note_paths" in route_source
