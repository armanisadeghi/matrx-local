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
from unittest.mock import AsyncMock

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


class _QueuedFileManager(DocumentFileManager):
    """Actual temporary files plus counters for the push queue boundary."""

    def __init__(self, base_dir: Path) -> None:
        super().__init__(base_dir=base_dir)
        self.queued_reads: list[str] = []
        self.conflict_reads = 0
        self.scan_calls = 0

    def scan_all(self) -> list[dict[str, str]]:
        self.scan_calls += 1
        raise AssertionError("push_all must not scan the corpus")

    def list_conflicts(self) -> list[str]:
        self.conflict_reads += 1
        return []

    def read_eligible_queued_note(self, file_path: str) -> str | None:
        self.queued_reads.append(file_path)
        return super().read_eligible_queued_note(file_path)


class _PendingRepo:
    def __init__(self, pending: list[dict[str, str]]) -> None:
        self.pending = pending

    async def list_pending_push(self, _user_id: str) -> list[dict[str, str]]:
        return self.pending


class _SnapshotClient:
    def __init__(self) -> None:
        self.snapshot_calls = 0

    def set_jwt(self, _jwt: str) -> None:
        pass

    async def get_all_notes_with_hashes(self, _user_id: str) -> list[dict[str, str]]:
        self.snapshot_calls += 1
        return []


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


def test_list_note_paths_matches_scan_membership_without_reading_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tree/count callers can retain scan membership without content I/O."""
    notes = tmp_path / "private-notes"
    (notes / "General").mkdir(parents=True)
    (notes / ".sync").mkdir()
    (notes / ".md").write_text("literal", encoding="utf-8")
    (notes / ".visible.md").write_text("root", encoding="utf-8")
    (notes / "General" / "one.md").write_text("one", encoding="utf-8")
    (notes / ".sync" / "ignored.md").write_text("hidden", encoding="utf-8")
    fm = DocumentFileManager(base_dir=notes)
    expected = [entry["file_path"] for entry in fm.scan_all()]

    def fail_read(*_args, **_kwargs):
        raise AssertionError("list_note_paths must not read note bodies")

    monkeypatch.setattr(Path, "read_text", fail_read)
    assert fm.list_note_paths() == expected


def test_eligible_queued_note_rejects_paths_outside_scan_membership(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "private-notes"
    (notes / "General").mkdir(parents=True)
    (notes / ".sync").mkdir()
    (notes / "General" / ".md").write_text("literal", encoding="utf-8")
    (notes / "General" / "queued.md").write_text("queued", encoding="utf-8")
    (notes / ".sync" / "hidden.md").write_text("hidden", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "linked.md").write_text("linked", encoding="utf-8")
    (notes / "linked").symlink_to(outside_dir, target_is_directory=True)
    fm = DocumentFileManager(base_dir=notes)

    assert fm.read_eligible_queued_note("General/.md") == "literal"
    assert fm.read_eligible_queued_note("General/queued.md") == "queued"
    assert fm.read_eligible_queued_note(".sync/hidden.md") is None
    assert fm.read_eligible_queued_note("../outside.md") is None
    assert fm.read_eligible_queued_note(str(outside)) is None
    assert fm.read_eligible_queued_note("linked/linked.md") is None


@pytest.mark.anyio
async def test_empty_push_queue_skips_all_notes_reads_and_cloud_snapshot(
    tmp_path: Path,
) -> None:
    fm = _QueuedFileManager(tmp_path / "private-notes")
    sb = _SnapshotClient()
    engine = SyncEngine(fm=fm, sb=sb)  # type: ignore[arg-type]
    engine.configure("account-a", "token-a")
    engine._get_notes_repo = lambda: _PendingRepo([])  # type: ignore[method-assign]

    assert await engine.push_all() == {
        "pushed": 0, "failed": 0, "skipped": 0, "conflicts": 0
    }
    assert fm.scan_calls == fm.conflict_reads == sb.snapshot_calls == 0
    assert fm.queued_reads == []


@pytest.mark.anyio
async def test_pending_push_reads_only_its_eligible_file_not_the_corpus(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "private-notes"
    folder = notes / "General"
    folder.mkdir(parents=True)
    (folder / ".md").write_text("queued", encoding="utf-8")
    (folder / "unrelated.md").write_text("unrelated\n" * 200_000, encoding="utf-8")
    fm = _QueuedFileManager(notes)
    sb = _SnapshotClient()
    engine = SyncEngine(fm=fm, sb=sb)  # type: ignore[arg-type]
    engine.configure("account-a", "token-a")
    engine._get_notes_repo = lambda: _PendingRepo([{
        "id": "queued", "file_path": "General/.md", "sync_enabled": True,
        "label": "queued", "folder_name": "General",
    }])  # type: ignore[method-assign]
    engine._push_note = AsyncMock(return_value={"_synced_to_cloud": True})

    result = await engine.push_all()

    assert result["pushed"] == 1
    assert fm.scan_calls == 0
    assert fm.queued_reads == ["General/.md"]
    assert sb.snapshot_calls == 1


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

    assert "self.fm.scan_all" not in push_source
    assert "self.fm.read_eligible_queued_note" in push_source
    assert "await offload_read_only(" in push_source
    assert "await offload_read_only(self.fm.scan_all)" in full_source
    assert watcher_source.count("await offload_read_only(self.fm.scan_all)") == 1
    assert "await offload_read_only(" in watcher_source
    assert 'path.read_text(encoding="utf-8")' in watcher_source
