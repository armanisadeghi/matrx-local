"""Regression coverage for Notes sync filesystem work off the event loop.

These tests use an explicit temporary ``DocumentFileManager`` root.  They do
not start an engine, open a real notes directory, or configure cloud sync.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from contextvars import ContextVar
from pathlib import Path

import pytest

from app.services.documents.file_manager import DocumentFileManager
from app.services.documents.async_io import offload_read_only
from app.services.documents.sync_engine import SyncEngine


_READ_PRINCIPAL: ContextVar[str | None] = ContextVar("read_principal", default=None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _ControlledScanFileManager(DocumentFileManager):
    """Real-file scanner whose worker lifetime is controlled by the test."""

    def __init__(self, base_dir: Path) -> None:
        super().__init__(base_dir=base_dir)
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.principal_seen: str | None = None
        self.fail_after_release = False

    def scan_all(self) -> list[dict[str, str]]:
        # This deliberately exercises the production scan implementation,
        # including ``Path.read_text`` and hashing, before holding the worker.
        scanned = super().scan_all()
        self.principal_seen = _READ_PRINCIPAL.get()
        self.started.set()
        self.release.wait()
        self.finished.set()
        if self.fail_after_release:
            raise OSError("controlled read failure")
        return scanned


async def _wait_for(event: threading.Event) -> None:
    """Wait cooperatively without a timeout that could mask a leaked worker."""
    while not event.is_set():
        await asyncio.sleep(0)


def _file_manager_with_real_files(tmp_path: Path) -> _ControlledScanFileManager:
    notes = tmp_path / "private-notes"
    folder = notes / "General"
    folder.mkdir(parents=True)
    for index in range(16):
        (folder / f"note-{index}.md").write_text(
            f"note {index}\n" + ("private corpus content\n" * 256),
            encoding="utf-8",
        )
    return _ControlledScanFileManager(notes)


@pytest.mark.anyio
async def test_read_only_scan_keeps_the_event_loop_responsive(tmp_path: Path) -> None:
    """A real-file scan must not stop unrelated loop work while it is running."""
    fm = _file_manager_with_real_files(tmp_path)
    token = _READ_PRINCIPAL.set("account-a")
    scan = asyncio.create_task(offload_read_only(fm.scan_all))
    await _wait_for(fm.started)

    beats = 0
    for _ in range(8):
        await asyncio.sleep(0)
        beats += 1

    fm.release.set()
    files = await scan

    assert beats == 8
    assert len(files) == 16
    assert {entry["file_path"] for entry in files} == {
        f"General/note-{index}.md" for index in range(16)
    }
    assert fm.finished.is_set()
    assert fm.principal_seen == "account-a"
    _READ_PRINCIPAL.reset(token)


@pytest.mark.anyio
async def test_cancelled_read_only_scan_drains_its_owned_worker(tmp_path: Path) -> None:
    """Cancellation waits for the scan worker, so it cannot outlive its owner."""
    fm = _file_manager_with_real_files(tmp_path)
    scan = asyncio.create_task(offload_read_only(fm.scan_all))
    await _wait_for(fm.started)

    scan.cancel()
    await asyncio.sleep(0)
    scan.cancel()
    await asyncio.sleep(0)
    assert not scan.done()
    assert not fm.finished.is_set()

    fm.release.set()
    with pytest.raises(asyncio.CancelledError):
        await scan
    assert fm.finished.is_set()


@pytest.mark.anyio
async def test_cancelled_read_only_scan_preserves_cancellation_after_worker_error(
    tmp_path: Path,
) -> None:
    """A drained read failure must not replace its owner's cancellation."""
    fm = _file_manager_with_real_files(tmp_path)
    fm.fail_after_release = True
    scan = asyncio.create_task(offload_read_only(fm.scan_all))
    await _wait_for(fm.started)

    scan.cancel()
    await asyncio.sleep(0)
    fm.release.set()

    with pytest.raises(asyncio.CancelledError):
        await scan
    assert fm.finished.is_set()


def test_sync_engine_routes_all_repeating_note_reads_through_offload() -> None:
    """The three scans and native watcher hash read stay outside the loop."""
    push_source = inspect.getsource(SyncEngine.push_all)
    full_source = inspect.getsource(SyncEngine.full_sync)
    watcher_source = inspect.getsource(SyncEngine._watch_loop)

    assert "await offload_read_only(self.fm.scan_all)" in push_source
    assert "await offload_read_only(self.fm.scan_all)" in full_source
    assert watcher_source.count("await offload_read_only(self.fm.scan_all)") == 1
    assert "await offload_read_only(" in watcher_source
    assert 'path.read_text(encoding="utf-8")' in watcher_source
