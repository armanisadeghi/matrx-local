"""Notes live in HOMES (scopes). A note's local directory is its home's name;
the organization is appended only when two visible homes share a name; a
directory that matches no home never becomes a home; moving a file between
home directories is set_home; a moved/renamed home moves the file, never
duplicates it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.services.documents.file_manager import DocumentFileManager, content_hash
from app.services.documents.homes import build_directory
from app.services.documents.sync_engine import SyncEngine
from app.services.local_db.database import LocalDatabase
from app.services.local_db.repositories import NotesRepo


def _scopes() -> list[dict[str, Any]]:
    return [
        {"id": "g-ai", "organization_id": "org-ai", "name": "General"},
        {"id": "g-law", "organization_id": "org-law", "name": "General"},
        {"id": "s-abc", "organization_id": "org-law", "name": "ABC Legal"},
    ]


ORGS = {"org-ai": "AI Matrx", "org-law": "Law Firm"}


def test_directory_names_append_organization_only_on_a_shared_name() -> None:
    d = build_directory(_scopes(), ORGS)
    assert d.dir_for("g-ai") == "General · AI Matrx"
    assert d.dir_for("g-law") == "General · Law Firm"
    assert d.dir_for("s-abc") == "ABC Legal"
    assert d.home_for_dir("abc legal").id == "s-abc"
    assert d.home_for_dir("General") is None  # matches no home
    single = build_directory(_scopes()[:1], ORGS)
    assert single.dir_for("g-ai") == "General"


class FakeSb:
    available = True
    current_jwt = "jwt"

    def __init__(self) -> None:
        self.scopes = _scopes()
        self.set_home_calls: list[tuple[str, str]] = []
        self.fail_set_home = False
        self.notes: dict[str, dict[str, Any]] = {}
        self.upserts: list[dict[str, Any]] = []
        self.file_path_updates: list[tuple[str, str]] = []

    async def list_scopes(self):
        return self.scopes

    async def list_organization_names(self):
        return ORGS

    async def set_home(self, note_id, scope_id, expected_version=None):
        if self.fail_set_home:
            raise RuntimeError("refused")
        self.set_home_calls.append((note_id, scope_id))

    async def update_note(self, note_id, updates, device_id=None):
        if "file_path" in updates:
            self.file_path_updates.append((note_id, updates["file_path"]))
        return {}

    async def get_note(self, note_id):
        return self.notes.get(note_id)

    async def update_note_if_unchanged(self, *a, **k):
        return None

    async def upsert_note(self, **kw):
        self.upserts.append(kw)
        row = {"id": kw["note_id"], "home_scope_id": "g-ai", "organization_id": kw.get("organization_id"),
               "content_hash": content_hash(kw["content"]), "file_path": kw["file_path"]}
        self.notes[kw["note_id"]] = row
        return dict(row)


async def _engine(tmp_path: Path, sb: FakeSb):
    db = LocalDatabase(path=tmp_path / "notes.db")
    await db.connect()
    repo = NotesRepo(db)
    fm = DocumentFileManager(base_dir=tmp_path / "Notes")
    engine = SyncEngine(fm=fm, sb=sb)
    engine._device_id = "dev"
    engine._user_id = "u1"
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]

    async def org() -> str:
        return "org-ai"

    engine.active_organization_id = org  # type: ignore[method-assign]
    return db, repo, fm, engine


def test_new_local_note_carries_org_no_home_and_moves_into_its_general(tmp_path: Path) -> None:
    async def run() -> None:
        sb = FakeSb()
        db, repo, fm, engine = await _engine(tmp_path, sb)
        try:
            await repo.upsert({"id": "n1", "user_id": "u1", "file_path": "General/Idea.md", "label": "Idea"})
            result = await engine.push_note("n1", "Idea", "body", file_path="General/Idea.md", owner_user_id="u1")
            assert result["_synced_to_cloud"] is True
            sent = sb.upserts[0]
            assert sent["organization_id"] == "org-ai"
            assert "home_scope_id" not in sent and "home_dir" not in sent
            row = await repo.get("n1")
            assert row["home_scope_id"] == "g-ai"
            assert row["home_dir"] == "General · AI Matrx"
            assert row["file_path"] == "General · AI Matrx/Idea.md"
            assert fm.read_note("General · AI Matrx/Idea.md") == "body"
            assert fm.read_note("General/Idea.md") is None  # moved, not copied
        finally:
            await db.close()
    asyncio.run(run())


def _seed_note(fm: DocumentFileManager, directory: str, name: str, body: str) -> str:
    return fm.write_note(directory, name, body)


def test_file_moved_to_another_home_directory_is_set_home(tmp_path: Path) -> None:
    async def run() -> None:
        sb = FakeSb()
        db, repo, fm, engine = await _engine(tmp_path, sb)
        try:
            old = _seed_note(fm, "General · AI Matrx", "Plan", "x")
            await repo.upsert({"id": "n1", "user_id": "u1", "file_path": old, "label": "Plan",
                               "content": "x", "content_hash": content_hash("x"),
                               "home_scope_id": "g-ai", "home_dir": "General · AI Matrx", "sync_status": "synced"})
            new = fm.move_note_to_dir(old, "ABC Legal")
            row = await repo.get("n1")
            moved_to = await engine._find_moved_file(row, old)
            assert moved_to == new
            assert await engine._apply_local_move(row, new, "u1") is True
            assert sb.set_home_calls == [("n1", "s-abc")]
            after = await repo.get("n1")
            assert (after["home_scope_id"], after["home_dir"], after["file_path"]) == ("s-abc", "ABC Legal", new)
            assert after["organization_id"] == "org-law"
        finally:
            await db.close()
    asyncio.run(run())


def test_file_moved_to_a_directory_that_matches_no_home_stays_in_its_home_and_says_so(tmp_path: Path) -> None:
    async def run() -> None:
        sb = FakeSb()
        db, repo, fm, engine = await _engine(tmp_path, sb)
        try:
            old = _seed_note(fm, "ABC Legal", "Memo", "x")
            await repo.upsert({"id": "n1", "user_id": "u1", "file_path": old, "label": "Memo",
                               "home_scope_id": "s-abc", "home_dir": "ABC Legal", "sync_status": "synced"})
            new = fm.move_note_to_dir(old, "Random Folder")
            assert await engine._apply_local_move(await repo.get("n1"), new, "u1") is False
            assert sb.set_home_calls == []
            after = await repo.get("n1")
            assert after["home_scope_id"] == "s-abc"
            assert after["file_path"] == "ABC Legal/Memo.md"
            assert fm.read_note("ABC Legal/Memo.md") == "x"
            assert fm.read_note("Random Folder/Memo.md") is None
            assert engine.home_notices[-1]["kind"] == "home_unmatched"
            assert "Random Folder" in engine.home_notices[-1]["message"]
        finally:
            await db.close()
    asyncio.run(run())


def test_refused_set_home_puts_the_file_back_and_says_so(tmp_path: Path) -> None:
    async def run() -> None:
        sb = FakeSb()
        sb.fail_set_home = True
        db, repo, fm, engine = await _engine(tmp_path, sb)
        try:
            old = _seed_note(fm, "General · AI Matrx", "Plan", "x")
            await repo.upsert({"id": "n1", "user_id": "u1", "file_path": old, "label": "Plan",
                               "home_scope_id": "g-ai", "home_dir": "General · AI Matrx", "sync_status": "synced"})
            new = fm.move_note_to_dir(old, "ABC Legal")
            assert await engine._apply_local_move(await repo.get("n1"), new, "u1") is False
            assert (await repo.get("n1"))["home_scope_id"] == "g-ai"
            assert fm.read_note("General · AI Matrx/Plan.md") == "x"
            assert engine.home_notices[-1]["kind"] == "home_move_failed"
        finally:
            await db.close()
    asyncio.run(run())


def test_pull_of_a_note_whose_home_changed_moves_the_file_instead_of_copying(tmp_path: Path) -> None:
    async def run() -> None:
        sb = FakeSb()
        db, repo, fm, engine = await _engine(tmp_path, sb)
        try:
            old = _seed_note(fm, "General · AI Matrx", "Plan", "same body")
            h = content_hash("same body")
            await repo.upsert({"id": "n1", "user_id": "u1", "file_path": old, "label": "Plan", "content": "same body",
                               "content_hash": h, "remote_content_hash": h, "home_scope_id": "g-ai",
                               "home_dir": "General · AI Matrx", "sync_status": "synced"})
            state = engine._load_sync_state()
            state.setdefault("note_hashes", {})[old] = h
            engine._save_sync_state(state)
            remote = {"id": "n1", "label": "Plan", "content": "same body", "content_hash": h, "file_path": old,
                      "home_scope_id": "s-abc", "organization_id": "org-law", "sync_version": 2,
                      "last_device_id": "other", "is_deleted": False, "tags": [], "metadata": {}}
            await engine._pull_note("n1", note=remote)
            after = await repo.get("n1")
            assert after["file_path"] == "ABC Legal/Plan.md"
            assert after["home_scope_id"] == "s-abc"
            assert fm.read_note("ABC Legal/Plan.md") == "same body"
            assert fm.read_note(old) is None, "the old copy must be gone, not duplicated"
        finally:
            await db.close()
    asyncio.run(run())


def test_local_database_migration_adds_homes_and_retires_folders(tmp_path: Path) -> None:
    async def run() -> None:
        db = LocalDatabase(path=tmp_path / "notes.db")
        await db.connect()
        try:
            cols = {r["name"] for r in await db.fetchall("PRAGMA table_info(notes)")}
            assert {"home_scope_id", "home_dir", "organization_id"} <= cols
            tables = {r["name"] for r in await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "note_folders" not in tables
        finally:
            await db.close()
    asyncio.run(run())
