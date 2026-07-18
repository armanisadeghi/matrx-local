"""Characterization: the file-sync engine (app/services/file_sync/).

Pins the CURRENT behavior (2026-07-13, commit fb786ea39) of the desktop
replica of the matrx-files cloud tree against:

  - a REAL SQLite database (real schema migrations, V11 file_sync_state,
    ATTACHed files.* mirror) in a tmp_path — never ~/.matrx/matrx.db,
  - a STUBBED MatrxFilesClient (engine._client swapped for a call-recording
    fake — nothing touches the network),
  - a stubbed engine._download_to for byte movement (unit isolation), plus
    one test driving the REAL _download_to against a fake DownloadManager
    to pin the checksum-mismatch failure.

Doctrine pinned here (docs/SYNC_CONTRACT.md):
  - local write first; conflicts are never destructive (both copies kept);
  - deletions are tombstones (send2trash, never unlink);
  - edit wins over remote delete (resurrect as a fresh local upload);
  - last_synced_hash is written only after a successful push/pull;
  - echo suppression is state-based (watch hash == mirror checksum);
  - a cloud path that would escape the Files root never touches disk.

These are characterization tests: they document what the code DOES today.
A red test means a contract conversation, not a test edit.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import pytest

from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase
from app.services.file_sync import engine as engine_module
from app.services.file_sync import hydration as hydration_module
from app.services.file_sync.client import FileSyncHTTPError
from app.services.file_sync.engine import FileSyncEngine
from app.services.file_sync.index import EMPTY_SHA256, LOCAL_ID_PREFIX


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeFilesClient:
    """Call-recording MatrxFilesClient double. Feed pages for sync_changes
    are consumed FIFO; when exhausted an empty page echoing the cursor is
    returned (steady state)."""

    def __init__(
        self,
        pages: list[dict[str, Any]] | None = None,
        folders: list[dict[str, Any]] | None = None,
    ) -> None:
        self.pages = list(pages or [])
        self.folders = list(folders or [])
        self.records: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, list[Any]] = {
            "sync_changes": [],
            "sync_folders": [],
            "get_url_envelope": [],
            "get_record": [],
            "upload": [],
            "patch": [],
            "soft_delete": [],
        }
        self._jwt: str | None = None

    # -- auth surface the engine touches ---------------------------------
    def set_jwt(self, token: str | None) -> None:
        self._jwt = token

    @property
    def available(self) -> bool:
        return bool(self._jwt)

    # -- pull -------------------------------------------------------------
    async def sync_changes(
        self, *, cursor: str | None = None, since: str | None = None, limit: int = 500
    ) -> dict[str, Any]:
        self.calls["sync_changes"].append({"cursor": cursor, "since": since, "limit": limit})
        if self.pages:
            return self.pages.pop(0)
        return {"files": [], "next_cursor": cursor, "has_more": False}

    async def sync_folders(self, *, include_deleted: bool = False) -> dict[str, Any]:
        self.calls["sync_folders"].append({"include_deleted": include_deleted})
        return {"folders": self.folders}

    # -- bytes / records ---------------------------------------------------
    async def get_url_envelope(self, file_id: str) -> dict[str, Any]:
        self.calls["get_url_envelope"].append(file_id)
        return {"download_url": f"https://files.fake/{file_id}"}

    async def get_record(self, file_id: str) -> dict[str, Any]:
        self.calls["get_record"].append(file_id)
        if file_id not in self.records:
            raise FileSyncHTTPError("GET", f"/files/{file_id}", 404, "not found (fake)")
        return self.records[file_id]

    # -- push --------------------------------------------------------------
    async def upload(
        self,
        *,
        file_path: str,
        content: bytes,
        filename: str,
        mime_type: str | None = None,
        visibility: str = "private",
    ) -> dict[str, Any]:
        self.calls["upload"].append(
            {"file_path": file_path, "content": content, "filename": filename}
        )
        file_id = f"cloud-{len(self.calls['upload'])}"
        checksum = _sha(content)
        self.records[file_id] = {
            "file_id": file_id,
            "file_path": file_path,
            "file_name": filename,
            "mime_type": mime_type or "application/octet-stream",
            "size_bytes": len(content),
            "checksum": checksum,
            "visibility": visibility,
            "version": 1,
            "folder_id": None,
            "created_at": _iso(),
            "updated_at": _iso(),
            "deleted_at": None,
        }
        return {"file_id": file_id, "checksum": checksum}

    async def patch(
        self, file_id: str, *, name: str | None = None, folder: str | None = None
    ) -> dict[str, Any]:
        self.calls["patch"].append({"file_id": file_id, "name": name, "folder": folder})
        return {}

    async def soft_delete(self, file_id: str) -> dict[str, Any]:
        self.calls["soft_delete"].append(file_id)
        return {}

    def total_calls(self) -> int:
        return sum(len(v) for v in self.calls.values())


class DownloadStub:
    """Instance-attribute replacement for engine._download_to: writes the
    scripted bytes for a file_id straight to dest and records the call."""

    def __init__(self, content_by_id: dict[str, bytes] | None = None) -> None:
        self.content_by_id = content_by_id or {}
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, file_id: str, dest: Path, *, expected_checksum: str | None = None
    ) -> None:
        self.calls.append(
            {"file_id": file_id, "dest": Path(dest), "expected_checksum": expected_checksum}
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.content_by_id.get(file_id, b""))


def feed_entry(
    file_id: str, file_path: str, content: bytes = b"", **overrides: Any
) -> dict[str, Any]:
    entry = {
        "file_id": file_id,
        "file_path": file_path,
        "file_name": file_path.rsplit("/", 1)[-1],
        "mime_type": "text/plain",
        "size_bytes": len(content),
        "checksum": _sha(content),
        "visibility": "private",
        "version": 1,
        "folder_id": None,
        "created_at": _iso(),
        "updated_at": _iso(),
        "deleted_at": None,
    }
    entry.update(overrides)
    return entry


def page(entries: list[dict[str, Any]], next_cursor: str, has_more: bool = False) -> dict[str, Any]:
    return {"files": entries, "next_cursor": next_cursor, "has_more": has_more, "server_time": _iso()}


# ---------------------------------------------------------------------------
# Harness — real SQLite in tmp_path, fake client, stubbed downloads
# ---------------------------------------------------------------------------

Scenario = Callable[[FileSyncEngine, LocalDatabase, FakeFilesClient], Awaitable[None]]


def run_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Scenario,
    *,
    mode: str = "pointers",
    client: FakeFilesClient | None = None,
) -> None:
    """Connect a throwaway SQLite DB (real migrations + real files.* mirror
    beside it per mirror.mirror_dir_for), inject the fake client, run the
    scenario, always close. Sync mode is pinned via the class property so
    settings_sync is never touched."""
    monkeypatch.setattr(FileSyncEngine, "mode", property(lambda self: mode))
    fake = client or FakeFilesClient()

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "characterization.db")
        await db.connect()  # real schema migrations + ATTACHed mirror
        monkeypatch.setattr(database_module, "_instance", db)
        try:
            engine = FileSyncEngine(root=tmp_path / "FilesRoot")
            engine.root.mkdir(parents=True, exist_ok=True)
            engine._client = fake
            engine.configure("user-char", "jwt-char")
            await scenario(engine, db, fake)
        finally:
            await db.close()

    asyncio.run(main())


async def _state(db: LocalDatabase, file_id: str) -> dict[str, Any] | None:
    row = await db.fetchone("SELECT * FROM file_sync_state WHERE file_id = ?", (file_id,))
    return dict(row) if row else None


async def _state_by_path(db: LocalDatabase, rel: str) -> dict[str, Any] | None:
    row = await db.fetchone("SELECT * FROM file_sync_state WHERE rel_path = ?", (rel,))
    return dict(row) if row else None


async def _all_states(db: LocalDatabase) -> list[dict[str, Any]]:
    rows = await db.fetchall("SELECT * FROM file_sync_state ORDER BY rel_path")
    return [dict(r) for r in rows]


async def _mirror_file(db: LocalDatabase, file_id: str) -> dict[str, Any] | None:
    row = await db.fetchone('SELECT * FROM "files"."files" WHERE id = ?', (file_id,))
    return dict(row) if row else None


async def _seed_synced(
    engine: FileSyncEngine, file_id: str, rel: str, content: bytes
) -> str:
    """A file that completed a successful sync: bytes on disk, state row
    synced, last_synced_hash == on-disk hash. Returns the hash."""
    abs_path = engine.root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)
    digest = _sha(content)
    await engine._index.upsert_state(
        file_id,
        rel_path=rel,
        local_state="synced",
        local_hash=digest,
        local_size=len(content),
        local_mtime=abs_path.stat().st_mtime,
        last_synced_hash=digest,
    )
    return digest


# ---------------------------------------------------------------------------
# 1. Bootstrap pull — pointers mode
# ---------------------------------------------------------------------------


def test_bootstrap_pull_pointers_writes_mirror_state_placeholders_and_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"cloud bytes"
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "docs/a.txt", content)], next_cursor="cur-1")],
        folders=[
            {
                "folder_id": "fo1",
                "folder_path": "docs",
                "folder_name": "docs",
                "parent_id": None,
                "visibility": "private",
                "is_system": False,
                "created_at": _iso(),
                "updated_at": _iso(),
                "deleted_at": None,
            }
        ],
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        summary = await engine.sync_cycle()
        assert summary["pulled"]["applied"] == 1
        assert summary["folders"] == {"applied": 1}

        # Mirror row written under the canonical qualified name.
        mirror = await _mirror_file(db, "f1")
        assert mirror is not None
        assert mirror["file_path"] == "docs/a.txt"
        assert mirror["checksum"] == _sha(content)

        # State row: a pointer with the empty-content hash.
        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "pointer"
        assert state["rel_path"] == "docs/a.txt"
        assert state["local_hash"] == EMPTY_SHA256
        assert state["local_size"] == 0
        assert state["last_synced_hash"] is None  # bytes never moved yet

        # Zero-byte placeholder on disk; folder entry created the directory.
        placeholder = engine.root / "docs" / "a.txt"
        assert placeholder.exists() and placeholder.stat().st_size == 0
        assert (engine.root / "docs").is_dir()

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


# ---------------------------------------------------------------------------
# 2. Cursor persistence in sync_meta ('files.files')
# ---------------------------------------------------------------------------


def test_cursor_persisted_in_sync_meta_and_replayed_by_a_fresh_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "a.txt", b"x")], next_cursor="cur-42")]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine.sync_cycle()
        assert fake.calls["sync_changes"][0]["cursor"] is None  # bootstrap

        meta = await db.fetchone(
            "SELECT * FROM sync_meta WHERE entity_type = ?", ("files.files",)
        )
        assert meta is not None
        assert meta["last_hash"] == "cur-42"
        assert meta["status"] == "success"

        # A brand-new engine (fresh process) loads the cursor from sync_meta
        # and hands it back to the feed.
        engine2 = FileSyncEngine(root=engine.root)
        engine2._client = fake
        engine2.configure("user-char", "jwt-char")
        await engine2.sync_cycle()
        assert fake.calls["sync_changes"][1]["cursor"] == "cur-42"

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


# ---------------------------------------------------------------------------
# 3. Remote tombstone on an unmodified synced file → OS trash, state dropped
# ---------------------------------------------------------------------------


def test_remote_tombstone_trashes_unmodified_local_file_and_drops_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"same as cloud"
    client = FakeFilesClient(
        pages=[
            page(
                [feed_entry("f1", "a.txt", content, deleted_at=_iso())],
                next_cursor="cur-t",
            )
        ]
    )
    trashed: list[str] = []

    def fake_send2trash(path: str) -> None:
        trashed.append(path)
        Path(path).unlink()

    import send2trash as send2trash_module

    monkeypatch.setattr(send2trash_module, "send2trash", fake_send2trash)

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "a.txt", content)
        summary = await engine.sync_cycle()
        assert summary["pulled"]["tombstones"] == 1
        assert trashed == [str(engine.root / "a.txt")]  # trashed, never unlinked
        assert await _state(db, "f1") is None

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


# ---------------------------------------------------------------------------
# 4. Remote tombstone vs local edit → edit wins over delete
# ---------------------------------------------------------------------------


def test_remote_tombstone_vs_local_edit_keeps_file_and_requeues_as_local_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edited = b"edited offline after the cloud delete"
    client = FakeFilesClient(
        pages=[
            page(
                [feed_entry("f1", "a.txt", b"old cloud content", deleted_at=_iso())],
                next_cursor="cur-t2",
            )
        ]
    )

    def forbidden_trash(path: str) -> None:  # pragma: no cover — must not fire
        raise AssertionError(f"send2trash called on a locally-edited file: {path}")

    import send2trash as send2trash_module

    monkeypatch.setattr(send2trash_module, "send2trash", forbidden_trash)

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "a.txt", b"original synced content")
        (engine.root / "a.txt").write_bytes(edited)  # local edit, unsynced

        # Pull only (sync_cycle would immediately drain the re-queued upload
        # in the same cycle — pinned separately below).
        await engine._pull_changes()

        # File kept on disk with the edited content.
        assert (engine.root / "a.txt").read_bytes() == edited
        # Old cloud-keyed row is gone; re-keyed to a fresh local: id pending upload.
        assert await _state(db, "f1") is None
        state = await _state_by_path(db, "a.txt")
        assert state is not None
        assert state["file_id"].startswith(LOCAL_ID_PREFIX)
        assert state["local_state"] == "pending_push"
        assert state["pending_op"] == "upload"
        assert state["local_hash"] == _sha(edited)

        # The very next drain re-uploads the surviving edit to the cloud.
        await engine._drain_pending()
        assert fake.calls["upload"] and fake.calls["upload"][0]["content"] == edited
        state = await _state_by_path(db, "a.txt")
        assert state is not None
        assert state["file_id"] == "cloud-1"
        assert state["local_state"] == "synced"

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


# ---------------------------------------------------------------------------
# 5. Remote change, local copy clean → re-hydration
# ---------------------------------------------------------------------------


def test_remote_change_on_clean_local_file_rehydrates_and_updates_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_remote = b"new remote content"
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "a.txt", new_remote)], next_cursor="cur-r")]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "a.txt", b"old synced content")
        stub = DownloadStub({"f1": new_remote})
        engine._download_to = stub

        summary = await engine.sync_cycle()
        assert summary["pulled"]["applied"] == 1
        assert summary["pulled"]["conflicts"] == 0

        assert len(stub.calls) == 1
        assert stub.calls[0]["file_id"] == "f1"
        assert stub.calls[0]["dest"] == engine.root / "a.txt"
        assert (engine.root / "a.txt").read_bytes() == new_remote

        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "synced"
        assert state["last_synced_hash"] == _sha(new_remote)
        assert state["pending_op"] is None

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


# ---------------------------------------------------------------------------
# 6. Remote change + local modification → non-destructive conflict
# ---------------------------------------------------------------------------


def test_remote_change_on_locally_modified_file_conflicts_preserving_both_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_edit = b"my local edit"
    remote_edit = b"their remote edit"
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "a.txt", remote_edit)], next_cursor="cur-c")]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "a.txt", b"common ancestor")
        (engine.root / "a.txt").write_bytes(local_edit)
        stub = DownloadStub({"f1": remote_edit})
        engine._download_to = stub

        summary = await engine.sync_cycle()
        assert summary["pulled"]["conflicts"] == 1

        # Local copy untouched.
        assert (engine.root / "a.txt").read_bytes() == local_edit
        # Remote copy captured under .sync/conflicts/<id>/.
        remote_copy = engine.root / ".sync" / "conflicts" / "f1" / "remote_a.txt"
        assert stub.calls[0]["dest"] == remote_copy
        assert remote_copy.read_bytes() == remote_edit

        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "conflict"
        assert state["error"] == "remote_and_local_both_changed"

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


# ---------------------------------------------------------------------------
# 7. Watcher classification (via _handle_watch_event — no watchfiles loop)
# ---------------------------------------------------------------------------


def test_watch_new_local_file_becomes_local_id_pending_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import watchfiles

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        path = engine.root / "brand_new.txt"
        path.write_bytes(b"born on this machine")
        await engine._handle_watch_event(watchfiles.Change.added, str(path))

        state = await _state_by_path(db, "brand_new.txt")
        assert state is not None
        assert state["file_id"].startswith(LOCAL_ID_PREFIX)
        assert state["local_state"] == "pending_push"
        assert state["pending_op"] == "upload"
        assert state["local_hash"] == _sha(b"born on this machine")

    run_scenario(tmp_path, monkeypatch, scenario)


def test_watch_modified_tracked_file_becomes_pending_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import watchfiles

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "a.txt", b"old")
        await engine._index.upsert_remote_file(feed_entry("f1", "a.txt", b"old"))
        (engine.root / "a.txt").write_bytes(b"new local content")

        await engine._handle_watch_event(
            watchfiles.Change.modified, str(engine.root / "a.txt")
        )

        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "pending_push"
        assert state["pending_op"] == "upload"
        assert state["local_hash"] == _sha(b"new local content")

    run_scenario(tmp_path, monkeypatch, scenario)


def test_watch_deleted_tracked_file_becomes_pending_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import watchfiles

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "a.txt", b"content")
        (engine.root / "a.txt").unlink()

        await engine._handle_watch_event(
            watchfiles.Change.deleted, str(engine.root / "a.txt")
        )

        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "pending_push"
        assert state["pending_op"] == "delete"

    run_scenario(tmp_path, monkeypatch, scenario)


def test_watch_pull_echo_marks_synced_with_no_pending_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A watcher event whose content hash equals the mirror checksum is our
    own pull landing — echo suppression, never a push."""
    import watchfiles

    content = b"bytes the pull just wrote"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine._index.upsert_remote_file(feed_entry("f1", "a.txt", content))
        await engine._index.upsert_state(
            "f1", rel_path="a.txt", local_state="pointer",
            local_hash=EMPTY_SHA256, local_size=0,
        )
        (engine.root / "a.txt").write_bytes(content)

        await engine._handle_watch_event(
            watchfiles.Change.modified, str(engine.root / "a.txt")
        )

        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "synced"
        assert state["pending_op"] is None
        assert state["last_synced_hash"] == _sha(content)

    run_scenario(tmp_path, monkeypatch, scenario)


def test_watch_pointer_placeholder_zero_byte_event_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import watchfiles

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        # Cloud file has real content; local side is only the placeholder.
        await engine._index.upsert_remote_file(feed_entry("f1", "a.txt", b"real cloud bytes"))
        await engine._index.upsert_state(
            "f1", rel_path="a.txt", local_state="pointer",
            local_hash=EMPTY_SHA256, local_size=0,
        )
        placeholder = engine.root / "a.txt"
        placeholder.touch()

        await engine._handle_watch_event(watchfiles.Change.added, str(placeholder))

        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "pointer"  # untouched
        assert state["pending_op"] is None

    run_scenario(tmp_path, monkeypatch, scenario)


# ---------------------------------------------------------------------------
# 8. Rename detection — delete + add with identical hash = one move
# ---------------------------------------------------------------------------


def test_watch_delete_then_add_with_same_hash_is_a_single_pending_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import watchfiles

    content = b"identical bytes travel"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "old/name.txt", content)

        # The OS rename shows up as delete(old) then add(new).
        (engine.root / "old" / "name.txt").unlink()
        await engine._handle_watch_event(
            watchfiles.Change.deleted, str(engine.root / "old" / "name.txt")
        )
        new_path = engine.root / "new" / "renamed.txt"
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(content)
        await engine._handle_watch_event(watchfiles.Change.added, str(new_path))

        states = await _all_states(db)
        assert len(states) == 1, "rename must collapse to ONE state row"
        state = states[0]
        assert state["file_id"] == "f1"
        assert state["rel_path"] == "new/renamed.txt"
        assert state["local_state"] == "pending_push"
        assert state["pending_op"] == "move"

    run_scenario(tmp_path, monkeypatch, scenario)


# ---------------------------------------------------------------------------
# 9. Push drain — upload / delete / move
# ---------------------------------------------------------------------------


def test_push_drain_upload_rekeys_local_id_and_records_synced_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"upload me"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        (engine.root / "up.txt").write_bytes(content)
        local_id = f"{LOCAL_ID_PREFIX}0000-upload-test"
        await engine._index.upsert_state(
            local_id, rel_path="up.txt", local_state="pending_push",
            pending_op="upload", local_hash=_sha(content), local_size=len(content),
        )

        result = await engine._drain_pending()
        assert result == {"sent": 1, "failed": 0}

        assert fake.calls["upload"] == [
            {"file_path": "up.txt", "content": content, "filename": "up.txt"}
        ]
        # local: id re-keyed to the cloud id the upload returned.
        assert await _state(db, local_id) is None
        state = await _state(db, "cloud-1")
        assert state is not None
        assert state["local_state"] == "synced"
        assert state["pending_op"] is None
        assert state["last_synced_hash"] == _sha(content)
        # Echo: the cloud record landed in the mirror so the next pull does
        # not see our own push as a foreign change.
        assert (await _mirror_file(db, "cloud-1")) is not None

    run_scenario(tmp_path, monkeypatch, scenario)


def test_push_drain_delete_calls_soft_delete_and_drops_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine._index.upsert_state(
            "f-del", rel_path="gone.txt", local_state="pending_push",
            pending_op="delete", local_hash=_sha(b"was here"),
        )

        result = await engine._drain_pending()
        assert result == {"sent": 1, "failed": 0}
        assert fake.calls["soft_delete"] == ["f-del"]
        assert await _state(db, "f-del") is None

    run_scenario(tmp_path, monkeypatch, scenario)


def test_push_drain_move_patches_name_and_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"moved bytes"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        new_abs = engine.root / "dir" / "new.txt"
        new_abs.parent.mkdir(parents=True, exist_ok=True)
        new_abs.write_bytes(content)
        await engine._index.upsert_state(
            "f-mv", rel_path="dir/new.txt", local_state="pending_push",
            pending_op="move", local_hash=_sha(content), local_size=len(content),
        )
        fake.records["f-mv"] = feed_entry("f-mv", "dir/new.txt", content)

        result = await engine._drain_pending()
        assert result == {"sent": 1, "failed": 0}
        assert fake.calls["patch"] == [
            {"file_id": "f-mv", "name": "new.txt", "folder": "dir"}
        ]
        state = await _state(db, "f-mv")
        assert state is not None
        assert state["pending_op"] is None
        assert state["local_state"] == "synced"

    run_scenario(tmp_path, monkeypatch, scenario)


# ---------------------------------------------------------------------------
# 10. Path safety — the feed can never write outside the Files root
# ---------------------------------------------------------------------------


def test_feed_path_traversal_never_touches_disk_outside_the_files_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeFilesClient(
        pages=[
            page(
                [
                    feed_entry("evil-dotdot", "../evil.txt", b"escape attempt"),
                    feed_entry("evil-abs", "/abs/evil.txt", b"absolute path"),
                    feed_entry("evil-sync", ".sync/hack.txt", b"admin dir"),
                ],
                next_cursor="cur-e",
            )
        ]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine.sync_cycle()

        # '..' segments: mirror row recorded, but NO state row and NO file —
        # in particular nothing lands at <root>/../evil.txt.
        assert (await _mirror_file(db, "evil-dotdot")) is not None
        assert await _state(db, "evil-dotdot") is None
        assert not (engine.root.parent / "evil.txt").exists()

        # .sync/ is the engine's admin dir — never writable from the feed.
        assert await _state(db, "evil-sync") is None
        assert not (engine.root / ".sync" / "hack.txt").exists()

        # Absolute cloud paths are re-rooted INSIDE the Files root (current
        # behavior: leading '/' is stripped) — never at the filesystem root.
        state = await _state(db, "evil-abs")
        assert state is not None and state["rel_path"] == "abs/evil.txt"
        assert (engine.root / "abs" / "evil.txt").exists()

        # Nothing anywhere in tmp escaped the root.
        outside = [
            p for p in tmp_path.rglob("evil.txt")
            if engine.root not in p.parents
        ]
        assert outside == []

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


# ---------------------------------------------------------------------------
# 11. Mode 'off' — the auto tick idles completely
# ---------------------------------------------------------------------------


def test_auto_sync_tick_in_mode_off_makes_no_cloud_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine._auto_sync_tick()
        assert fake.total_calls() == 0
        assert not engine.watcher_active
        assert await _all_states(db) == []

    run_scenario(tmp_path, monkeypatch, scenario, mode="off")


# ---------------------------------------------------------------------------
# 12. hydrate() — pointer → bytes, checksum verified
# ---------------------------------------------------------------------------


def test_hydrate_pointer_downloads_bytes_and_marks_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"the real cloud bytes"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine._index.upsert_remote_file(feed_entry("f1", "docs/a.txt", content))
        await engine._index.upsert_state(
            "f1", rel_path="docs/a.txt", local_state="pointer",
            local_hash=EMPTY_SHA256, local_size=0,
        )
        stub = DownloadStub({"f1": content})
        engine._download_to = stub

        result = await engine.hydrate("f1")

        assert result == engine.root / "docs" / "a.txt"
        assert result.read_bytes() == content
        assert stub.calls[0]["expected_checksum"] == _sha(content)
        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "synced"
        assert state["local_hash"] == _sha(content)
        assert state["last_synced_hash"] == _sha(content)

    run_scenario(tmp_path, monkeypatch, scenario)


def test_hydrate_checksum_mismatch_fails_loud_and_leaves_state_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drives the REAL _download_to against a fake DownloadManager that
    delivers corrupted bytes — the post-download sha256 check must raise."""
    expected = b"expected content"

    class FakeDownloadManager:
        def __init__(self) -> None:
            self._entries: list[Any] = []
            self.enqueued: list[dict[str, Any]] = []

        async def enqueue(self, **kwargs: Any) -> Any:
            self.enqueued.append(kwargs)
            dest = Path(kwargs["metadata"]["dest_dir"]) / kwargs["metadata"]["dest_filename"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"CORRUPTED bytes")
            entry = SimpleNamespace(id="dl-1", status="completed", error_msg=None)
            self._entries = [entry]
            return entry

        def get_all(self) -> list[Any]:
            return self._entries

    fake_manager = FakeDownloadManager()
    from app.services.downloads import manager as downloads_manager_module

    monkeypatch.setattr(
        downloads_manager_module, "get_download_manager", lambda: fake_manager
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine._index.upsert_remote_file(feed_entry("f1", "a.txt", expected))
        await engine._index.upsert_state(
            "f1", rel_path="a.txt", local_state="pointer",
            local_hash=EMPTY_SHA256, local_size=0,
        )

        with pytest.raises(RuntimeError, match="checksum mismatch"):
            await engine.hydrate("f1")

        assert fake.calls["get_url_envelope"] == ["f1"]  # envelope minted fresh
        assert len(fake_manager.enqueued) == 1
        assert fake_manager.enqueued[0]["category"] == "file_sync"
        # State never advanced past pointer — the corrupted bytes are not sync.
        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "pointer"
        assert state["last_synced_hash"] is None

    run_scenario(tmp_path, monkeypatch, scenario)


# ---------------------------------------------------------------------------
# Tools seam — hydration.ensure_hydrated
# ---------------------------------------------------------------------------


def test_ensure_hydrated_returns_none_for_paths_outside_the_files_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        monkeypatch.setattr(engine_module, "_ENGINE", engine)
        outside = tmp_path / "elsewhere" / "not_synced.txt"
        assert await hydration_module.ensure_hydrated(str(outside)) is None
        assert fake.total_calls() == 0  # outside root = pure prefix check

    run_scenario(tmp_path, monkeypatch, scenario)


def test_ensure_hydrated_returns_error_string_when_hydrate_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        monkeypatch.setattr(engine_module, "_ENGINE", engine)
        await engine._index.upsert_state(
            "f1", rel_path="p.txt", local_state="pointer",
            local_hash=EMPTY_SHA256, local_size=0,
        )
        (engine.root / "p.txt").touch()

        async def failing_hydrate(file_id_or_path: str, *, priority: int = 10) -> Path:
            raise RuntimeError("network is a lie")

        engine.hydrate = failing_hydrate  # type: ignore[method-assign]

        message = await hydration_module.ensure_hydrated(str(engine.root / "p.txt"))
        assert message is not None
        assert "p.txt" in message
        assert "network is a lie" in message

    run_scenario(tmp_path, monkeypatch, scenario)


# ---------------------------------------------------------------------------
# Regressions — adversarial-review fixes (commit 56340714e)
# ---------------------------------------------------------------------------


def test_capture_conflict_clears_pending_op_so_the_queued_upload_never_drains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A watcher-queued upload on a row that then conflicts with a remote
    change MUST be disarmed — draining it would be silent last-writer-wins."""
    import watchfiles

    local_edit = b"my local edit queued for upload"
    remote_edit = b"their remote edit arriving via the feed"
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "a.txt", remote_edit)], next_cursor="cur-cc")]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "a.txt", b"common ancestor")
        (engine.root / "a.txt").write_bytes(local_edit)
        await engine._handle_watch_event(
            watchfiles.Change.modified, str(engine.root / "a.txt")
        )
        state = await _state(db, "f1")
        assert state is not None and state["pending_op"] == "upload"  # armed

        engine._download_to = DownloadStub({"f1": remote_edit})
        await engine._pull_changes()

        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "conflict"
        assert state["pending_op"] is None  # the fix: conflict disarms the push

        # And the next drain pushes NOTHING for it.
        result = await engine._drain_pending()
        assert result == {"sent": 0, "failed": 0}
        assert fake.calls["upload"] == []

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


def test_drain_pending_skips_conflicted_rows_clears_the_op_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth: even a directly-seeded conflict+pending_op row is
    never pushed — the op is cleared with a loud error line."""

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        (engine.root / "c.txt").write_bytes(b"conflicted bytes")
        await engine._index.upsert_state(
            "f-conf", rel_path="c.txt", local_state="conflict",
            pending_op="upload", local_hash=_sha(b"conflicted bytes"),
        )
        errors: list[str] = []
        monkeypatch.setattr(
            engine_module.logger, "error",
            lambda msg, *args, **kwargs: errors.append(msg % args if args else msg),
        )

        result = await engine._drain_pending()
        assert result == {"sent": 0, "failed": 0}
        assert fake.total_calls() == 0  # nothing pushed to the cloud

        state = await _state(db, "f-conf")
        assert state is not None
        assert state["local_state"] == "conflict"  # conflict stays open
        assert state["pending_op"] is None  # op cleared
        assert any("pending_op" in e and "c.txt" in e for e in errors)  # logged loud

    run_scenario(tmp_path, monkeypatch, scenario)


def test_first_sight_of_a_cloud_file_adopts_matching_local_content_as_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing local content whose hash matches the feed checksum is
    adopted as synced — never misrecorded as an empty pointer."""
    content = b"identical on both sides"
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "docs/a.txt", content)], next_cursor="cur-fs1")]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        abs_path = engine.root / "docs" / "a.txt"
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        stub = DownloadStub()
        engine._download_to = stub

        await engine._pull_changes()

        assert abs_path.read_bytes() == content  # never truncated to a placeholder
        assert stub.calls == []  # no bytes moved at all
        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "synced"
        assert state["local_hash"] == _sha(content)
        assert state["last_synced_hash"] == _sha(content)
        assert state["pending_op"] is None

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


def test_first_sight_with_divergent_local_content_captures_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = b"what the user already had here"
    remote = b"what the cloud says lives here"
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "a.txt", remote)], next_cursor="cur-fs2")]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        (engine.root / "a.txt").write_bytes(local)
        stub = DownloadStub({"f1": remote})
        engine._download_to = stub

        summary = await engine._pull_changes()
        assert summary["conflicts"] == 1

        # Local file untouched; remote copy captured beside it.
        assert (engine.root / "a.txt").read_bytes() == local
        remote_copy = engine.root / ".sync" / "conflicts" / "f1" / "remote_a.txt"
        assert remote_copy.read_bytes() == remote
        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "conflict"
        assert state["pending_op"] is None
        assert state["error"] == "remote_and_local_both_changed"

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


def test_first_sight_adopts_a_local_born_row_under_the_cloud_id_when_hashes_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local:<uuid> row already holding the path (offline create) merges
    into the cloud id when the content matches — one row, synced."""
    content = b"uploaded from another device, same bytes here"
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "a.txt", content)], next_cursor="cur-fs3")]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        (engine.root / "a.txt").write_bytes(content)
        local_id = f"{LOCAL_ID_PREFIX}dead-beef-first-sight"
        await engine._index.upsert_state(
            local_id, rel_path="a.txt", local_state="pending_push",
            pending_op="upload", local_hash=_sha(content), local_size=len(content),
        )

        await engine._pull_changes()

        assert await _state(db, local_id) is None  # provisional row gone
        states = await _all_states(db)
        assert len(states) == 1
        state = states[0]
        assert state["file_id"] == "f1"
        assert state["local_state"] == "synced"
        assert state["last_synced_hash"] == _sha(content)
        assert state["pending_op"] is None
        assert (engine.root / "a.txt").read_bytes() == content

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


def test_hydrate_on_a_pointer_that_gained_content_conflicts_instead_of_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A placeholder the user typed into is a local edit — hydration must
    capture a conflict and raise, never clobber the bytes."""
    user_bytes = b"the user typed straight into the placeholder"
    remote = b"real cloud content"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine._index.upsert_remote_file(feed_entry("f1", "docs/a.txt", remote))
        await engine._index.upsert_state(
            "f1", rel_path="docs/a.txt", local_state="pointer",
            local_hash=EMPTY_SHA256, local_size=0,
        )
        abs_path = engine.root / "docs" / "a.txt"
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(user_bytes)  # placeholder gained content
        stub = DownloadStub({"f1": remote})
        engine._download_to = stub

        with pytest.raises(RuntimeError, match="local changes"):
            await engine.hydrate("f1")

        assert abs_path.read_bytes() == user_bytes  # local bytes untouched
        remote_copy = engine.root / ".sync" / "conflicts" / "f1" / "remote_a.txt"
        assert remote_copy.read_bytes() == remote  # remote captured aside
        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "conflict"
        assert state["pending_op"] is None

    run_scenario(tmp_path, monkeypatch, scenario)


def test_push_upload_mid_flight_edit_keeps_the_newer_edit_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the watcher re-stamps local_hash while the upload is in flight, the
    finalize must take the not-finalized branch: last_synced_hash records
    what DID land, but the row is never flipped to synced with a stale hash —
    the pending op survives so the next drain pushes the delta."""
    uploaded = b"snapshot that went over the wire"
    newer = b"edit that landed mid-upload"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        (engine.root / "up.txt").write_bytes(uploaded)
        local_id = f"{LOCAL_ID_PREFIX}0000-midflight"
        await engine._index.upsert_state(
            local_id, rel_path="up.txt", local_state="pending_push",
            pending_op="upload", local_hash=_sha(uploaded), local_size=len(uploaded),
        )

        real_upload = fake.upload

        async def racing_upload(**kwargs: Any) -> dict[str, Any]:
            resp = await real_upload(**kwargs)
            # Simulate the watcher stamping a fresh edit before _push_upload
            # finalizes (the row is still under its local: id here).
            await engine._index.upsert_state(local_id, local_hash=_sha(newer))
            return resp

        fake.upload = racing_upload  # type: ignore[method-assign]

        result = await engine._drain_pending()
        assert result == {"sent": 1, "failed": 0}

        # Re-keyed to the cloud id, but NOT finalized as synced.
        assert await _state(db, local_id) is None
        state = await _state(db, "cloud-1")
        assert state is not None
        assert state["local_state"] == "pending_push"  # not marked synced
        assert state["pending_op"] == "upload"  # newer edit stays queued
        assert state["local_hash"] == _sha(newer)  # the fresh edit's hash intact
        assert state["last_synced_hash"] == _sha(uploaded)  # what DID land

    run_scenario(tmp_path, monkeypatch, scenario)


def test_remote_edit_wins_over_a_pending_local_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete queued locally, then the cloud content changed: the edit wins —
    the delete is dropped and the row falls back to a pointer."""
    new_remote = b"fresh cloud content after the local delete was queued"
    client = FakeFilesClient(
        pages=[page([feed_entry("f1", "a.txt", new_remote)], next_cursor="cur-ed")]
    )

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "a.txt", b"old synced content")
        (engine.root / "a.txt").unlink()
        await engine._index.upsert_state(
            "f1", local_state="pending_push", pending_op="delete"
        )

        await engine._pull_changes()

        state = await _state(db, "f1")
        assert state is not None
        assert state["pending_op"] is None  # delete dropped
        assert state["local_state"] == "pointer"  # hydrates on access/backfill
        assert state["local_hash"] == EMPTY_SHA256
        placeholder = engine.root / "a.txt"
        assert placeholder.exists() and placeholder.stat().st_size == 0

        # And the drain must not soft-delete the resurrected file.
        await engine._drain_pending()
        assert fake.calls["soft_delete"] == []

    run_scenario(tmp_path, monkeypatch, scenario, client=client)


def test_watch_delete_of_a_conflicted_file_leaves_the_conflict_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import watchfiles

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine._index.upsert_state(
            "f1", rel_path="a.txt", local_state="conflict",
            local_hash=_sha(b"conflicted"), error="remote_and_local_both_changed",
        )
        # File already gone from disk; the watcher reports the delete.
        await engine._handle_watch_event(
            watchfiles.Change.deleted, str(engine.root / "a.txt")
        )

        state = await _state(db, "f1")
        assert state is not None  # row survives
        assert state["local_state"] == "conflict"  # conflict stays open
        assert state["pending_op"] is None  # no delete queued

    run_scenario(tmp_path, monkeypatch, scenario)


def test_watch_modify_of_a_conflicted_file_tracks_bytes_but_never_rearms_a_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import watchfiles

    newer = b"user keeps editing the conflicted file"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        (engine.root / "a.txt").write_bytes(b"conflicted state bytes")
        await engine._index.upsert_state(
            "f1", rel_path="a.txt", local_state="conflict",
            local_hash=_sha(b"conflicted state bytes"),
            error="remote_and_local_both_changed",
        )
        (engine.root / "a.txt").write_bytes(newer)

        await engine._handle_watch_event(
            watchfiles.Change.modified, str(engine.root / "a.txt")
        )

        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "conflict"  # only resolve_conflict moves it
        assert state["pending_op"] is None  # never re-armed
        assert state["local_hash"] == _sha(newer)  # but the new bytes are tracked

    run_scenario(tmp_path, monkeypatch, scenario)


def test_watch_batch_orders_deletes_before_adds_so_any_input_order_is_one_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The watch loop sorts each batch deletes-first; an add+delete rename
    pair arriving add-first still collapses to a single 'move' row."""
    import watchfiles

    content = b"identical bytes travel, whatever the batch order"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await _seed_synced(engine, "f1", "old/name.txt", content)
        (engine.root / "old" / "name.txt").unlink()
        new_path = engine.root / "new" / "renamed.txt"
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(content)

        # Same-batch pair with the ADD FIRST in input order — the loop's
        # ordering rule (deletes sort before everything else) must fix it.
        batch = [
            (watchfiles.Change.added, str(new_path)),
            (watchfiles.Change.deleted, str(engine.root / "old" / "name.txt")),
        ]
        ordered = sorted(
            batch, key=lambda c: 0 if c[0] == watchfiles.Change.deleted else 1
        )
        assert ordered[0][0] == watchfiles.Change.deleted  # the loop's contract
        for change_type, change_path in ordered:
            await engine._handle_watch_event(change_type, change_path)

        states = await _all_states(db)
        assert len(states) == 1, "rename must collapse to ONE state row"
        state = states[0]
        assert state["file_id"] == "f1"
        assert state["rel_path"] == "new/renamed.txt"
        assert state["pending_op"] == "move"

    run_scenario(tmp_path, monkeypatch, scenario)


def test_resolve_conflict_keep_remote_stays_conflict_until_the_download_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """keep_remote never pre-flips the row: a failed forced hydration leaves
    the conflict open; only a successful one marks it synced."""
    local = b"local conflicted copy"
    remote = b"remote copy the user chose to keep"

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        await engine._index.upsert_remote_file(feed_entry("f1", "a.txt", remote))
        (engine.root / "a.txt").write_bytes(local)
        await engine._index.upsert_state(
            "f1", rel_path="a.txt", local_state="conflict",
            local_hash=_sha(local), local_size=len(local),
            error="remote_and_local_both_changed",
        )

        # First attempt: the download fails — the row must STAY 'conflict'.
        async def failing_download(
            file_id: str, dest: Path, *, expected_checksum: str | None = None
        ) -> None:
            raise RuntimeError("cloud unreachable")

        engine._download_to = failing_download  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="cloud unreachable"):
            await engine.resolve_conflict("f1", "keep_remote")
        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "conflict"  # never pre-flipped
        assert (engine.root / "a.txt").read_bytes() == local  # bytes untouched

        # Second attempt: the download succeeds — only NOW is it synced.
        engine._download_to = DownloadStub({"f1": remote})
        result = await engine.resolve_conflict("f1", "keep_remote")
        assert result["hydrated"] is True
        state = await _state(db, "f1")
        assert state is not None
        assert state["local_state"] == "synced"
        assert state["last_synced_hash"] == _sha(remote)
        assert (engine.root / "a.txt").read_bytes() == remote

    run_scenario(tmp_path, monkeypatch, scenario)


def test_clean_rel_path_rejects_drive_letters_ads_separators_and_admin_dir_casefold() -> None:
    """Windows drive/ADS separators and any-case .sync/ can never be local
    relative paths."""
    assert engine_module._clean_rel_path("C:/evil") is None
    assert engine_module._clean_rel_path("a:b/c") is None
    assert engine_module._clean_rel_path(".SYNC/x") is None  # casefolded admin dir
    # Sanity: a plain nested path survives untouched.
    assert engine_module._clean_rel_path("docs/a.txt") == "docs/a.txt"


def test_watch_event_on_a_part_file_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DownloadManager streams to '<name>.part' then renames — a partial
    download must never classify as a new local file."""
    import watchfiles

    async def scenario(engine: FileSyncEngine, db: LocalDatabase, fake: FakeFilesClient) -> None:
        part = engine.root / "foo.bin.part"
        part.write_bytes(b"half-downloaded bytes")

        await engine._handle_watch_event(watchfiles.Change.added, str(part))

        assert await _all_states(db) == []  # nothing tracked, nothing queued
        assert fake.total_calls() == 0

    run_scenario(tmp_path, monkeypatch, scenario)
