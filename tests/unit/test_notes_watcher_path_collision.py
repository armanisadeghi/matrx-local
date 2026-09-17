"""Private watcher regressions for contested Notes file paths."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from app.services.documents.file_manager import content_hash
from app.services.documents.sync_engine import SyncEngine
from tests.characterization.test_documents_sync_characterization import (
    FakeFileManager,
    FakeNotesRepo,
    FakeSupabase,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class RecordingRepo(FakeNotesRepo):
    def __init__(self) -> None:
        super().__init__()
        self.upserts: list[str] = []

    async def list_live_by_file_path(self, file_path: str) -> list[dict[str, Any]]:
        return [
            row for row in self.rows.values()
            if row.get("file_path") == file_path and not row.get("is_deleted")
        ]

    async def upsert(self, row: dict[str, Any]) -> None:
        self.upserts.append(row["id"])
        await super().upsert(row)


class WatcherSupabase(FakeSupabase):
    def __init__(self) -> None:
        super().__init__()
        self.snapshot_error = False

    async def get_all_notes_with_hashes(self, user_id: str) -> list[dict[str, Any]]:
        if self.snapshot_error:
            raise RuntimeError("offline")
        return list(self.notes.values())


@pytest.fixture()
def engine(tmp_path: Path) -> SyncEngine:
    eng = SyncEngine(fm=FakeFileManager(tmp_path), sb=WatcherSupabase())  # type: ignore[arg-type]
    repo = RecordingRepo()
    eng._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    eng._repo = repo  # type: ignore[attr-defined]
    eng._device_id = "watcher-collision-test"

    async def watcher_principal() -> str:
        eng.configure("user-1", "jwt-1")
        return "user-1"

    eng._bind_watcher_principal = watcher_principal  # type: ignore[method-assign]
    return eng


def _owner(note_id: str, remote_hash: str | None = None, user_id: str = "user-1") -> dict[str, Any]:
    return {
        "id": note_id,
        "user_id": user_id,
        "file_path": "Draft/shared.md",
        "sync_status": "synced",
        "sync_enabled": True,
        "remote_content_hash": remote_hash,
    }


@pytest.mark.parametrize("reverse_local,reverse_remote", [(False, False), (True, True)])
def test_watcher_uses_historical_hash_keeper_regardless_of_row_order(
    engine: SyncEngine, reverse_local: bool, reverse_remote: bool
) -> None:
    """The watcher updates the stable keeper, never the first local/cloud row."""
    historical = content_hash("last synced")
    local_rows = [_owner("keeper", historical), _owner("replica", content_hash("other"))]
    remote_rows = [
        {"id": "keeper", "created_by": "user-1", "file_path": "Draft/shared.md", "content_hash": historical, "label": "keeper", "folder_name": "Draft"},
        {"id": "replica", "created_by": "user-1", "file_path": "Draft/shared.md", "content_hash": content_hash("other"), "label": "replica", "folder_name": "Draft"},
    ]
    if reverse_local:
        local_rows.reverse()
    if reverse_remote:
        remote_rows.reverse()
    for row in local_rows:
        engine._repo.rows[row["id"]] = row  # type: ignore[attr-defined]
    for row in remote_rows:
        engine.sb.notes[row["id"]] = row
    engine.fm.notes["Draft/shared.md"] = "edited locally"
    engine.fm.state["accounts"]["user-1"]["note_hashes"]["Draft/shared.md"] = historical

    _run(engine._handle_external_change("Draft/shared.md"))

    assert engine._repo.upserts == ["keeper"]  # type: ignore[attr-defined]
    updates = [payload for kind, payload in engine.sb.calls if kind == "update_note_if_unchanged"]
    assert [payload["note_id"] for payload in updates] == ["keeper"]


def test_watcher_defers_ambiguous_unsynced_collision_without_writes(
    engine: SyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    engine._repo.rows["first"] = _owner("first")  # type: ignore[attr-defined]
    engine._repo.rows["second"] = _owner("second")  # type: ignore[attr-defined]
    engine.fm.notes["Draft/shared.md"] = "unsynced edit"

    with caplog.at_level(logging.WARNING, logger="app.services.documents.sync_engine"):
        _run(engine._handle_external_change("Draft/shared.md"))

    assert engine._repo.upserts == []  # type: ignore[attr-defined]
    assert engine.sb.calls == [("set_jwt", {"jwt": "jwt-1"})]
    assert "contested path Draft/shared.md (ambiguous)" in caplog.text


def test_watcher_ignores_unowned_raw_file_without_collision_warning(
    engine: SyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    engine.fm.notes["Draft/shared.md"] = "raw local file"

    with caplog.at_level(logging.WARNING, logger="app.services.documents.sync_engine"):
        _run(engine._handle_external_change("Draft/shared.md"))

    assert engine._repo.upserts == []  # type: ignore[attr-defined]
    assert "contested path" not in caplog.text


def test_watcher_offline_snapshot_keeps_sole_owner_edit_pending(engine: SyncEngine) -> None:
    engine._repo.rows["sole"] = _owner("sole")  # type: ignore[attr-defined]
    engine.fm.notes["Draft/shared.md"] = "local edit while offline"
    engine.sb.snapshot_error = True

    _run(engine._handle_external_change("Draft/shared.md"))

    row = engine._repo.rows["sole"]  # type: ignore[attr-defined]
    assert engine._repo.upserts == ["sole"]  # type: ignore[attr-defined]
    assert row["content"] == "local edit while offline"
    assert row["sync_status"] == "pending_push"
    assert not [payload for kind, payload in engine.sb.calls if kind == "upsert_note"]


def test_watcher_defers_foreign_path_owner_without_writes(engine: SyncEngine) -> None:
    engine._repo.rows["foreign"] = _owner("foreign", user_id="other-user")  # type: ignore[attr-defined]
    engine.fm.notes["Draft/shared.md"] = "local bytes"

    _run(engine._handle_external_change("Draft/shared.md"))

    assert engine._repo.upserts == []  # type: ignore[attr-defined]
    assert engine.sb.calls == [("set_jwt", {"jwt": "jwt-1"})]


def test_watcher_defers_foreign_cloud_identity_without_writes(engine: SyncEngine) -> None:
    engine._repo.rows["sole"] = _owner("sole")  # type: ignore[attr-defined]
    engine.fm.notes["Draft/shared.md"] = "local bytes"
    engine.sb.notes["sole"] = {
        "id": "sole", "created_by": "other-user", "file_path": "Draft/shared.md",
        "content_hash": content_hash("remote bytes"),
    }

    _run(engine._handle_external_change("Draft/shared.md"))

    assert engine._repo.upserts == []  # type: ignore[attr-defined]
    assert not [payload for kind, payload in engine.sb.calls if kind == "upsert_note"]


def test_watcher_sole_owner_pushes_its_own_identity(engine: SyncEngine) -> None:
    engine._repo.rows["sole"] = _owner("sole")  # type: ignore[attr-defined]
    engine.fm.notes["Draft/shared.md"] = "new body"

    _run(engine._handle_external_change("Draft/shared.md"))

    pushes = [payload for kind, payload in engine.sb.calls if kind == "upsert_note"]
    assert engine._repo.upserts == ["sole"]  # type: ignore[attr-defined]
    assert [payload["note_id"] for payload in pushes] == ["sole"]


def test_watcher_duplicate_guard_still_blocks_new_cloud_identity(engine: SyncEngine) -> None:
    engine._repo.rows["sole"] = _owner("sole")  # type: ignore[attr-defined]
    engine.fm.notes["Draft/shared.md"] = "duplicate bytes"
    engine.sb.notes["other"] = {
        "id": "other", "created_by": "user-1", "file_path": "Draft/other.md",
        "content_hash": content_hash("duplicate bytes"),
    }

    _run(engine._handle_external_change("Draft/shared.md"))

    assert engine._repo.upserts == ["sole"]  # type: ignore[attr-defined]
    assert not [payload for kind, payload in engine.sb.calls if kind == "upsert_note"]
