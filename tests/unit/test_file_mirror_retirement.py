"""The retired file mirror's one-time cleanup removes ONLY its own placeholders.

SUT: ``retire_file_mirror`` over a REAL SQLite database (real schema
migrations, real ``file_sync_state``) and a real directory tree mixing tracked
zero-byte placeholders, a placeholder that gained bytes, synced real files,
untracked files, a path escaping the root, and a symlink leading out of it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.file_sync.retirement import RETIREMENT_ENTITY, retire_file_mirror
from app.services.local_db.database import LocalDatabase
from app.services.local_db.repositories import SyncMetaRepo

pytestmark = pytest.mark.anyio


async def _track(db: LocalDatabase, file_id: str, rel_path: str, state: str) -> None:
    await db.execute(
        "INSERT INTO file_sync_state (file_id, rel_path, local_state) VALUES (?, ?, ?)",
        (file_id, rel_path, state),
    )
    await db.commit()


def _write(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _all_files(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() or p.is_symlink()
    }


async def _mixed_tree(tmp_path: Path) -> tuple[LocalDatabase, Path, Path]:
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    root = tmp_path / "Files"
    outside = tmp_path / "Elsewhere"

    # Tracked placeholders — the ONLY things that may go.
    for i in range(3):
        rel = f"coding-sessions/claude/s{i}/deep/file{i}.md"
        _write(root / rel)
        await _track(db, f"p{i}", rel, "pointer")
    _write(root / "Reports/q3.pdf")
    await _track(db, "p-top", "Reports/q3.pdf", "pointer")
    # A tracked placeholder already gone from disk: its row is cleared.
    await _track(db, "p-gone", "Reports/gone.txt", "pointer")

    # A tracked "pointer" that gained content is the person's file now.
    _write(root / "Reports/edited.txt", b"my notes")
    await _track(db, "p-edited", "Reports/edited.txt", "pointer")
    # Real synced files.
    _write(root / "Photos/beach.jpg", b"\xff\xd8real-bytes")
    await _track(db, "s1", "Photos/beach.jpg", "synced")
    _write(root / "coding-sessions/claude/s0/keep.txt", b"synced")
    await _track(db, "s2", "coding-sessions/claude/s0/keep.txt", "synced")
    # Untracked files — even an empty one — are never touched.
    _write(root / "mine/empty-on-purpose.txt")
    _write(root / "coding-sessions/claude/s1/untracked.md", b"x")
    # A recorded path that escapes the root, with a real empty file there.
    _write(outside / "escape.txt")
    await _track(db, "p-escape", "../Elsewhere/escape.txt", "pointer")
    # A symlinked folder inside the root that leads outside it.
    _write(outside / "linked/target.txt")
    os.symlink(outside / "linked", root / "via-link")
    await _track(db, "p-link", "via-link/target.txt", "pointer")
    # A tracked conflict row stays exactly as it is.
    _write(root / "Reports/conflicted.txt")
    await _track(db, "c1", "Reports/conflicted.txt", "conflict")
    return db, root, outside


async def test_removes_only_tracked_zero_byte_placeholders_and_their_empty_folders(
    tmp_path: Path,
) -> None:
    db, root, outside = await _mixed_tree(tmp_path)
    try:
        before = _all_files(root)
        assert len(before) == 11

        result = await retire_file_mirror(db=db, root=root)

        after = _all_files(root)
        removed = before - after
        assert removed == {
            "coding-sessions/claude/s0/deep/file0.md",
            "coding-sessions/claude/s1/deep/file1.md",
            "coding-sessions/claude/s2/deep/file2.md",
            "Reports/q3.pdf",
        }
        assert result["status"] == "done"
        assert result["removed"] == 4
        assert result["already_gone"] == 1
        assert result["kept_with_bytes"] == 1
        assert result["kept_outside_root"] == 2
        assert result["errors"] == 0
        # s0/deep, s1/deep, s2/deep and s2 are now empty and go; s0, s1 still
        # hold real files; coding-sessions/claude and Reports stay non-empty.
        assert result["dirs_removed"] == 4
        assert not (root / "coding-sessions/claude/s2").exists()
        assert (root / "coding-sessions/claude/s0/keep.txt").read_bytes() == b"synced"
        assert (root / "Reports/edited.txt").read_bytes() == b"my notes"
        assert (root / "mine/empty-on-purpose.txt").exists()
        assert (outside / "escape.txt").exists()
        assert (outside / "linked/target.txt").exists()
        assert root.is_dir()

        rows = {
            r["file_id"]: r["local_state"]
            for r in await db.fetchall("SELECT file_id, local_state FROM file_sync_state")
        }
        assert set(rows) == {"p-edited", "s1", "s2", "p-escape", "p-link", "c1"}

        marker = await SyncMetaRepo(db).get_last_sync(RETIREMENT_ENTITY)
        assert marker and marker["status"] == "done"
        assert await retire_file_mirror(db=db, root=root) == {"status": "already_done"}
    finally:
        await db.close()


async def test_unreadable_tracking_table_touches_nothing(tmp_path: Path) -> None:
    db, root, _outside = await _mixed_tree(tmp_path)
    try:
        await db.execute("DROP TABLE file_sync_state")
        await db.commit()
        before = _all_files(root)

        result = await retire_file_mirror(db=db, root=root)

        assert result["status"] == "skipped"
        assert "unreadable" in result["reason"]
        assert _all_files(root) == before
        assert await SyncMetaRepo(db).get_last_sync(RETIREMENT_ENTITY) is None
    finally:
        await db.close()


async def test_unreachable_files_folder_keeps_rows_and_retries(tmp_path: Path) -> None:
    db, root, _outside = await _mixed_tree(tmp_path)
    try:
        result = await retire_file_mirror(db=db, root=tmp_path / "not-mounted")
        assert result["status"] == "partial"
        assert result["removed"] == 0
        pointers = await db.fetchall(
            "SELECT file_id FROM file_sync_state WHERE local_state = 'pointer'"
        )
        assert len(pointers) == 8
        # Not marked done, so the real folder is cleaned on the next start.
        result = await retire_file_mirror(db=db, root=root)
        assert result["status"] == "done" and result["removed"] == 4
    finally:
        await db.close()
