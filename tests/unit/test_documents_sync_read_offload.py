"""Regression coverage for Notes sync filesystem work off the event loop.

These tests use an explicit temporary ``DocumentFileManager`` root.  They do
not start an engine, open a real notes directory, or configure cloud sync.
"""

from __future__ import annotations

import asyncio
import ast
import inspect
import threading
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.documents.file_manager import DocumentFileManager, content_hash
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


@pytest.mark.anyio
async def test_conflict_snapshot_read_keeps_health_equivalent_work_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real conflict snapshot read must not stop unrelated loop work."""
    notes = tmp_path / "private-notes"
    conflict = notes / ".sync" / "conflicts" / "note-1"
    conflict.mkdir(parents=True)
    local_snapshot = conflict / "local.md"
    local_snapshot.write_text("local", encoding="utf-8")
    (conflict / "remote.md").write_text("remote", encoding="utf-8")
    current_file = notes / "General" / "Note.md"
    current_file.parent.mkdir(parents=True)
    current_file.write_text("current", encoding="utf-8")
    fm = DocumentFileManager(base_dir=notes)
    engine = SyncEngine(fm=fm)
    engine.configure("account-a", "token-a")
    repo = AsyncMock()
    repo.get.return_value = {
        "id": "note-1", "user_id": "account-a", "label": "Note",
        "folder_name": "General", "file_path": "General/Note.md",
    }
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    engine._push_note = AsyncMock(return_value={"_synced_to_cloud": True})

    started = threading.Event()
    release = threading.Event()
    original_read_text = Path.read_text

    def controlled_read(self: Path, *args: object, **kwargs: object) -> str:
        if self == local_snapshot:
            started.set()
            release.wait()
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_read)
    resolve = asyncio.create_task(engine.resolve_conflict("note-1", "keep_remote"))
    await _wait_for(started)

    beats = 0
    for _ in range(8):
        await asyncio.sleep(0)
        beats += 1

    release.set()
    result = await resolve

    assert beats == 8
    assert result and result["content"] == "remote"
    assert repo.set_sync_status.await_count == 1


@pytest.mark.anyio
async def test_cancelled_conflict_snapshot_read_drains_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation cannot leave a conflict-body read touching user data."""
    notes = tmp_path / "private-notes"
    conflict = notes / ".sync" / "conflicts" / "note-1"
    conflict.mkdir(parents=True)
    local_snapshot = conflict / "local.md"
    local_snapshot.write_text("local", encoding="utf-8")
    (conflict / "remote.md").write_text("remote", encoding="utf-8")
    engine = SyncEngine(fm=DocumentFileManager(base_dir=notes))

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_read_text = Path.read_text

    def controlled_read(self: Path, *args: object, **kwargs: object) -> str:
        if self == local_snapshot:
            started.set()
            release.wait()
            finished.set()
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_read)
    resolve = asyncio.create_task(engine.resolve_conflict("note-1", "keep_remote"))
    await _wait_for(started)

    resolve.cancel()
    await asyncio.sleep(0)
    resolve.cancel()
    await asyncio.sleep(0)
    assert not resolve.done()
    assert not finished.is_set()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await resolve
    assert finished.is_set()


@pytest.mark.anyio
async def test_keep_local_defers_edit_made_after_worker_captures_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-read external edit cannot be overwritten after the await."""
    notes = tmp_path / "private-notes"
    conflict = notes / ".sync" / "conflicts" / "note-1"
    conflict.mkdir(parents=True)
    (conflict / "local.md").write_text("conflict-local", encoding="utf-8")
    (conflict / "remote.md").write_text("conflict-remote", encoding="utf-8")
    current_file = notes / "General" / "Note.md"
    current_file.parent.mkdir(parents=True)
    current_file.write_text("before-edit", encoding="utf-8")
    fm = DocumentFileManager(base_dir=notes)
    engine = SyncEngine(fm=fm)
    engine.configure("account-a", "token-a")
    repo = AsyncMock()
    repo.get.return_value = {
        "id": "note-1", "user_id": "account-a", "label": "Note",
        "folder_name": "General", "file_path": "General/Note.md",
    }
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    engine._push_note = AsyncMock(return_value={"_synced_to_cloud": True})

    started = threading.Event()
    release = threading.Event()
    original_snapshot = fm.read_note_snapshot

    def controlled_snapshot(file_path: str):
        snapshot = original_snapshot(file_path)
        if file_path == "General/Note.md":
            started.set()
            release.wait()
        return snapshot

    monkeypatch.setattr(fm, "read_note_snapshot", controlled_snapshot)
    resolve = asyncio.create_task(engine.resolve_conflict("note-1", "keep_local"))
    await _wait_for(started)
    current_file.write_text("edited-while-read-pending", encoding="utf-8")
    release.set()

    result = await resolve

    assert result == {"id": "note-1", "_deferred_revision": True}
    assert current_file.read_text(encoding="utf-8") == "edited-while-read-pending"


@pytest.mark.anyio
async def test_pull_defers_post_capture_edit_before_remote_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote pull cannot overwrite a local edit made after its worker read."""
    notes = tmp_path / "private-notes"
    current_file = notes / "General" / "Note.md"
    current_file.parent.mkdir(parents=True)
    current_file.write_text("synced-local", encoding="utf-8")
    fm = DocumentFileManager(base_dir=notes)
    engine = SyncEngine(fm=fm)
    engine.configure("account-a", "token-a")
    local_hash = content_hash("synced-local")
    repo = AsyncMock()
    local_row = {
        "id": "note-1", "user_id": "account-a", "file_path": "General/Note.md",
        "remote_content_hash": local_hash, "sync_status": "synced",
    }
    repo.get.return_value = local_row
    repo.list_live_by_file_path.return_value = [local_row]
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    state = {"note_hashes": {"General/Note.md": local_hash}}
    engine._load_sync_state = lambda: state  # type: ignore[method-assign]
    engine._save_sync_state = lambda _state: None  # type: ignore[method-assign]

    started = threading.Event()
    release = threading.Event()
    original_snapshot = fm.read_note_snapshot

    def controlled_snapshot(file_path: str):
        snapshot = original_snapshot(file_path)
        if file_path == "General/Note.md":
            started.set()
            release.wait()
        return snapshot

    monkeypatch.setattr(fm, "read_note_snapshot", controlled_snapshot)
    pull = asyncio.create_task(engine._pull_note("note-1", {
        "id": "note-1", "created_by": "account-a", "file_path": "General/Note.md",
        "label": "Note", "folder_name": "General", "content": "remote-body",
        "content_hash": content_hash("remote-body"),
    }))
    await _wait_for(started)
    current_file.write_text("edited-after-capture", encoding="utf-8")
    release.set()

    result = await pull

    assert result and result.get("_deferred_revision") is True
    assert current_file.read_text(encoding="utf-8") == "edited-after-capture"
    assert repo.upsert.await_count == 0


@pytest.mark.anyio
async def test_reroute_cleanup_preserves_post_capture_old_path_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A move may materialize its new path but never delete a changed old one."""
    notes = tmp_path / "private-notes"
    old_file = notes / "General" / "Old.md"
    old_file.parent.mkdir(parents=True)
    old_file.write_text("synced-old", encoding="utf-8")
    fm = DocumentFileManager(base_dir=notes)
    engine = SyncEngine(fm=fm)
    engine.configure("account-a", "token-a")
    old_hash = content_hash("synced-old")
    local_row = {
        "id": "note-1", "user_id": "account-a", "file_path": "General/Old.md",
        "remote_content_hash": old_hash, "sync_status": "synced",
    }
    repo = AsyncMock()
    repo.get.return_value = local_row
    repo.list_live_by_file_path.side_effect = lambda fp: [local_row] if fp == "General/Old.md" else []
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    state = {"note_hashes": {"General/Old.md": old_hash}}
    engine._load_sync_state = lambda: state  # type: ignore[method-assign]
    engine._save_sync_state = lambda _state: None  # type: ignore[method-assign]

    started = threading.Event()
    release = threading.Event()
    original_snapshot = fm.read_note_snapshot

    def controlled_snapshot(file_path: str):
        snapshot = original_snapshot(file_path)
        if file_path == "General/Old.md":
            started.set()
            release.wait()
        return snapshot

    monkeypatch.setattr(fm, "read_note_snapshot", controlled_snapshot)
    pull = asyncio.create_task(engine._pull_note("note-1", {
        "id": "note-1", "created_by": "account-a", "file_path": "General/New.md",
        "label": "Note", "folder_name": "General", "content": "remote-body",
        "content_hash": content_hash("remote-body"),
    }))
    await _wait_for(started)
    old_file.write_text("edited-after-capture", encoding="utf-8")
    release.set()

    await pull

    assert old_file.read_text(encoding="utf-8") == "edited-after-capture"
    assert (notes / "General" / "New.md").read_text(encoding="utf-8") == "remote-body"
    assert "General/Old.md" in state["note_hashes"]


@pytest.mark.anyio
async def test_prune_preserves_conflict_changed_after_worker_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pruning never clears a conflict whose snapshot changed after capture."""
    notes = tmp_path / "private-notes"
    conflict = notes / ".sync" / "conflicts" / "note-1"
    conflict.mkdir(parents=True)
    local_file = conflict / "local.md"
    local_file.write_text("same", encoding="utf-8")
    (conflict / "remote.md").write_text("same", encoding="utf-8")
    fm = DocumentFileManager(base_dir=notes)
    engine = SyncEngine(fm=fm)

    started = threading.Event()
    release = threading.Event()
    original_snapshot = fm.read_note_snapshot

    def controlled_snapshot(file_path: str):
        snapshot = original_snapshot(file_path)
        if file_path.endswith("/local.md"):
            started.set()
            release.wait()
        return snapshot

    monkeypatch.setattr(fm, "read_note_snapshot", controlled_snapshot)
    prune = asyncio.create_task(engine.prune_stale_conflicts())
    await _wait_for(started)
    local_file.write_text("changed-after-capture", encoding="utf-8")
    release.set()

    assert await prune == 0
    assert conflict.exists()


def test_unreadable_snapshot_is_not_treated_as_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed stat/read is a deferral signal, never a safe missing file."""
    fm = DocumentFileManager(base_dir=tmp_path / "private-notes")

    class UnreadablePath:
        def stat(self):
            raise PermissionError("private fixture access denied")

    monkeypatch.setattr(fm, "note_path_from_file_path", lambda _path: UnreadablePath())

    snapshot = fm.read_note_snapshot("General/Note.md")

    assert snapshot.state == "unreadable"
    assert fm.note_snapshot_is_current("General/Note.md", snapshot) is False


def test_sync_engine_routes_all_repeating_note_reads_through_offload() -> None:
    """Every asynchronous Notes content read is owned by the offload helper."""
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

    tree = ast.parse(inspect.getsource(__import__("app.services.documents.sync_engine", fromlist=["*"])))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"read_note", "read_note_snapshot", "note_hash", "read_text"}:
            continue
        owner: ast.AST | None = node
        while owner is not None and not isinstance(owner, (ast.AsyncFunctionDef, ast.FunctionDef)):
            owner = parents.get(owner)
        if not isinstance(owner, ast.AsyncFunctionDef):
            continue
        ancestor: ast.AST | None = node
        while ancestor is not None:
            if (
                isinstance(ancestor, ast.Call)
                and isinstance(ancestor.func, ast.Name)
                and ancestor.func.id == "offload_read_only"
            ):
                break
            ancestor = parents.get(ancestor)
        assert ancestor is not None, f"async {owner.name} reads content without offload"
