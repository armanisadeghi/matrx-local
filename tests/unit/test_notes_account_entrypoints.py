"""Account-bound Notes entrypoint regressions."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.documents.file_manager import content_hash
from app.services.documents.sync_engine import SyncEngine
from tests.characterization.test_documents_sync_characterization import (
    FakeFileManager,
    FakeNotesRepo,
    FakeSupabase,
)


def _run(coro):
    return asyncio.run(coro)


def _engine(tmp_path: Path) -> SyncEngine:
    engine = SyncEngine(fm=FakeFileManager(tmp_path), sb=FakeSupabase())  # type: ignore[arg-type]
    repo = FakeNotesRepo()
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    engine._repo = repo  # type: ignore[attr-defined]
    engine._device_id = "test-device"
    engine.configure("account-b", "jwt-b")
    return engine


def test_deferred_pull_holds_account_cursor(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine._repo.rows["foreign"] = {"id": "foreign", "user_id": "account-a"}
    engine.sb.notes["foreign"] = {"id": "foreign", "user_id": "account-a"}
    engine.sb.notes["foreign"] = {"id": "foreign", "user_id": "account-a"}

    async def changes(_user: str, _cursor: str | None):
        return [{"id": "foreign", "updated_at": "2026-09-16T00:00:00Z"}]

    engine.sb.get_notes_since = changes  # type: ignore[method-assign]
    result = _run(engine.pull_changes())
    assert result["deferred_account"] == 1
    assert "account-b" not in engine.fm.state["accounts"]


def test_foreign_path_pull_never_rewrites_file(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.fm.notes["General/n.md"] = "account a"
    engine._repo.rows["a"] = {"id": "a", "user_id": "account-a", "file_path": "General/n.md"}
    result = _run(engine._pull_note("b", note={
        "id": "b", "user_id": "account-b", "file_path": "General/n.md",
        "content": "account b", "content_hash": content_hash("account b"),
    }))
    assert result and result["_deferred_account"]
    assert engine.fm.notes["General/n.md"] == "account a"


def test_breaker_keeps_account_checkpoint(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.fm.state["accounts"] = {"account-b": {"note_hashes": {"b.md": "b"}}}
    engine.fm.state["remote_live_count"] = 0
    assert engine.allow_cloud_delete("one")
    assert engine.fm.state["accounts"]["account-b"]["note_hashes"] == {"b.md": "b"}
    assert engine.fm.state["delete_window"]


def test_foreign_pending_row_is_not_pushed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine._repo.rows["foreign"] = {
        "id": "foreign", "user_id": "account-a", "sync_status": "pending_push",
        "sync_enabled": True, "is_deleted": False, "file_path": "General/n.md",
    }
    engine.fm.notes["General/n.md"] = "body"
    result = _run(engine.push_all())
    assert result["pushed"] == 0
    assert not [call for call in engine.sb.calls if call[0] == "upsert_note"]


def test_pull_all_deferred_row_is_not_counted_as_pulled(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine._repo.rows["foreign"] = {"id": "foreign", "user_id": "account-a"}
    engine.sb.notes["foreign"] = {"id": "foreign", "user_id": "account-a"}

    async def all_notes(_user: str):
        return [{"id": "foreign", "user_id": "account-a"}]

    engine.sb.get_all_notes_with_hashes = all_notes  # type: ignore[method-assign]
    result = _run(engine.pull_all())
    assert result["pulled"] == 0
    assert result["deferred_account"] == 1


def test_status_uses_bound_account_checkpoint(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.fm.state["accounts"] = {
        "account-a": {"note_hashes": {"a": "1"}},
        "account-b": {"note_hashes": {"b": "2"}, "last_pull_at": "b-cursor"},
    }
    status = engine.get_status()
    assert status["tracked_files"] == 1
    assert status["last_pull_at"] == "b-cursor"
