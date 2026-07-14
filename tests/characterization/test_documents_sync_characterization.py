"""Characterization: documents (notes) sync engine pure logic.

Pins the CURRENT behavior (2026-07-10) of app/services/documents/sync_engine.py:

  - conflict resolution mode selection (keep_local / keep_remote / merge /
    append / split / exclude / unknown)
  - the pull-side hash-comparison conflict guard (local vs remote vs
    last-known-at-sync)
  - push/pull status transitions reachable purely in-memory

No network, no Supabase, no real notes directory: the engine is constructed
with fake fm/sb collaborators and a fake NotesRepo. These are
characterization tests — they document what the code DOES today, not what it
should do. If one fails, either a behavior change was intentional (update the
pin, note it in the commit) or you just caught a regression.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

try:
    # ORDER MATTERS: app.common must be imported before any module whose first
    # app-level import is app.config. app.config's own import triggers
    # app/common/__init__.py -> fancy_prints -> app.config (partially
    # initialized) and raises ImportError when app.config is the FIRST app
    # module a process touches. Latent defect tracked in
    # .matrx/AGENT_TASKS.md (circular import app.config <-> app.common).
    import app.common  # noqa: F401  (see ordering note above)

    from app.services.documents.file_manager import content_hash
    from app.services.documents.supabase_client import _normalize_note_row
    from app.services.documents.sync_engine import SyncEngine, _note_id_for_path
except Exception as exc:  # pragma: no cover — env-dependent import guard
    pytest.skip(
        f"documents sync engine not importable in this environment: {exc}",
        allow_module_level=True,
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fakes — minimal, call-recording stand-ins for fm / sb / NotesRepo
# ---------------------------------------------------------------------------


class FakeFileManager:
    """In-memory DocumentFileManager double backed by a real tmp base_dir
    (resolve_conflict reads conflict files from base_dir/.sync/conflicts)."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.notes: dict[str, str] = {}  # rel path -> content
        self.state: dict[str, Any] = {"note_hashes": {}, "last_sync_version": 0}
        self.conflicts_saved: list[dict[str, Any]] = []
        self.conflicts_resolved: list[str] = []
        self.deleted: list[str] = []

    # -- notes --------------------------------------------------------------
    def write_note(
        self,
        folder_name: str,
        label: str,
        content: str,
        file_path: str | None = None,
    ) -> str:
        fp = file_path or f"{folder_name}/{label}.md"
        self.notes[fp] = content
        return fp

    def read_note(self, file_path: str) -> str | None:
        return self.notes.get(file_path)

    def delete_note(self, file_path: str) -> bool:
        self.deleted.append(file_path)
        return self.notes.pop(file_path, None) is not None

    def unique_file_path(self, folder_name: str, label: str) -> str:
        base = f"{folder_name}/{label}.md"
        if base not in self.notes:
            return base
        n = 2
        while f"{folder_name}/{label}_{n}.md" in self.notes:
            n += 1
        return f"{folder_name}/{label}_{n}.md"

    def note_hash(self, file_path: str) -> str | None:
        content = self.notes.get(file_path)
        return content_hash(content) if content is not None else None

    def scan_all(self) -> list[dict[str, str]]:
        return [
            {
                "file_path": fp,
                "label": Path(fp).stem,
                "content_hash": content_hash(content),
            }
            for fp, content in self.notes.items()
        ]

    # -- sync state ----------------------------------------------------------
    def load_sync_state(self) -> dict[str, Any]:
        return self.state

    def save_sync_state(self, state: dict[str, Any]) -> None:
        self.state = state

    # -- conflicts -----------------------------------------------------------
    def save_conflict(
        self, file_path: str, local: str, remote: str, note_id: str
    ) -> None:
        self.conflicts_saved.append(
            {"file_path": file_path, "local": local, "remote": remote, "id": note_id}
        )
        conflict_dir = self.base_dir / ".sync" / "conflicts" / note_id
        conflict_dir.mkdir(parents=True, exist_ok=True)
        (conflict_dir / "local.md").write_text(local, encoding="utf-8")
        (conflict_dir / "remote.md").write_text(remote, encoding="utf-8")

    def resolve_conflict(self, note_id: str) -> bool:
        self.conflicts_resolved.append(note_id)
        return True

    def list_conflicts(self) -> list[str]:
        return []

    # -- mappings ------------------------------------------------------------
    def load_local_mappings(self) -> dict[str, list[str]]:
        return {}

    def sync_to_mapped_dirs(self, file_path: str, mapped: list[str]) -> None:
        raise AssertionError("sync_to_mapped_dirs must not run with empty mappings")


class FakeSupabase:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.notes: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_pushes = False

    def set_jwt(self, jwt: str) -> None:
        self.calls.append(("set_jwt", {"jwt": jwt}))

    async def list_folders(self, user_id: str) -> list[dict[str, Any]]:
        # No remote folders in the fake — the push-time folder resolver falls
        # back to a null folder_id (folder_name carries the organization).
        return []

    async def get_note(self, note_id: str) -> dict[str, Any] | None:
        return self.notes.get(note_id)

    async def upsert_note(self, **kw: Any) -> dict[str, Any]:
        if self.fail_pushes:
            raise RuntimeError("simulated network failure")
        self.calls.append(("upsert_note", kw))
        row = {**kw, "id": kw["note_id"], "sync_version": 1}
        self.notes[kw["note_id"]] = row
        return row

    async def update_note_if_unchanged(
        self,
        note_id: str,
        updates: dict[str, Any],
        expected_content_hash: str,
        device_id: str | None = None,
    ) -> dict[str, Any] | None:
        if self.fail_pushes:
            raise RuntimeError("simulated network failure")
        row = self.notes.get(note_id)
        if (
            row is None
            or row.get("is_deleted")
            or row.get("content_hash") != expected_content_hash
        ):
            self.calls.append(("update_note_if_unchanged:precondition_failed", {"note_id": note_id}))
            return None
        self.calls.append(("update_note_if_unchanged", {"note_id": note_id, **updates}))
        row = {**row, **updates, "id": note_id}
        if "content" in updates:
            from app.services.documents.file_manager import content_hash as _ch
            row["content_hash"] = _ch(updates["content"])
        self.notes[note_id] = row
        return row

    async def update_note(
        self, note_id: str, fields: dict[str, Any], device_id: str | None = None
    ) -> dict[str, Any]:
        if self.fail_pushes:
            raise RuntimeError("simulated network failure")
        self.calls.append(("update_note", {"note_id": note_id, **fields}))
        row = {**self.notes.get(note_id, {}), **fields, "id": note_id, "sync_version": 2}
        self.notes[note_id] = row
        return row

    async def soft_delete_note(self, note_id: str) -> None:
        self.calls.append(("soft_delete_note", {"note_id": note_id}))


class FakeNotesRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.status_calls: list[tuple[str, str, str | None]] = []
        self.excluded_calls: list[tuple[str, bool]] = []
        self.soft_deleted: list[str] = []

    async def get(self, note_id: str) -> dict[str, Any] | None:
        return self.rows.get(note_id)

    async def get_by_file_path(self, file_path: str) -> dict[str, Any] | None:
        for row in self.rows.values():
            if row.get("file_path") == file_path:
                return row
        return None

    async def set_sync_status(
        self, note_id: str, status: str, remote_hash: str | None = None
    ) -> None:
        self.status_calls.append((note_id, status, remote_hash))
        if note_id in self.rows:
            self.rows[note_id]["sync_status"] = status

    async def set_excluded(self, note_id: str, excluded: bool) -> None:
        self.excluded_calls.append((note_id, excluded))

    async def upsert(self, row: dict[str, Any]) -> None:
        self.rows[row["id"]] = {**self.rows.get(row["id"], {}), **row}

    async def soft_delete(self, note_id: str) -> None:
        self.soft_deleted.append(note_id)

    async def list_pending_push(self) -> list[dict[str, Any]]:
        return [
            r
            for r in self.rows.values()
            if r.get("sync_status") in ("never_synced", "pending_push", "failed")
        ]


@pytest.fixture()
def engine(tmp_path: Path) -> SyncEngine:
    eng = SyncEngine(fm=FakeFileManager(tmp_path), sb=FakeSupabase())  # type: ignore[arg-type]
    repo = FakeNotesRepo()
    eng._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    eng._repo = repo  # test-side handle
    eng._device_id = "test-device"  # avoid touching sync-state persistence
    return eng


def _configure(eng: SyncEngine) -> None:
    eng.configure(user_id="user-1", jwt="jwt-1")


# ---------------------------------------------------------------------------
# _note_id_for_path — deterministic identity
# ---------------------------------------------------------------------------


def test_note_id_for_path_is_deterministic_uuid5() -> None:
    fp = "General/hello.md"
    expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"matrx-note:{fp}"))
    assert _note_id_for_path(fp) == expected
    assert _note_id_for_path(fp) == _note_id_for_path(fp)
    assert _note_id_for_path("Other/hello.md") != expected


# ---------------------------------------------------------------------------
# push_note — status transitions reachable in memory
# ---------------------------------------------------------------------------


def test_push_without_user_id_is_local_only_and_touches_nothing(engine: SyncEngine) -> None:
    result = _run(engine.push_note("n1", "Hello", "content"))
    assert result == {"id": "n1", "label": "Hello", "_synced_to_cloud": False}
    assert engine.fm.notes == {}  # not even written locally on this path
    assert engine.sb.calls == []


def test_push_with_user_but_sb_unavailable_saves_locally_only(engine: SyncEngine) -> None:
    _configure(engine)
    engine.sb.available = False
    result = _run(engine.push_note("n1", "Hello", "body", folder_name="Work"))
    assert result["_synced_to_cloud"] is False
    assert engine.fm.notes["Work/Hello.md"] == "body"
    # note_hashes means "hash at last SUCCESSFUL cloud sync" — it must NOT be
    # recorded when nothing reached the cloud, or the next pull would treat
    # the unsynced local edit as already-synced and clobber it with older
    # remote content (contract amendment 2026-07-13).
    assert "Work/Hello.md" not in engine.fm.state["note_hashes"]
    # No repo status write happens on the local-only path.
    assert engine._repo.status_calls == []


def test_push_new_note_upserts_and_marks_synced(engine: SyncEngine) -> None:
    _configure(engine)
    result = _run(engine.push_note("n1", "Hello", "body"))
    assert result["_synced_to_cloud"] is True
    assert [c[0] for c in engine.sb.calls if c[0] == "upsert_note"] == ["upsert_note"]
    assert engine._repo.status_calls == [("n1", "synced", content_hash("body"))]
    # note_hashes recorded ONLY after the successful push.
    assert engine.fm.state["note_hashes"]["General/Hello.md"] == content_hash("body")


def test_push_existing_note_is_a_single_upsert(engine: SyncEngine) -> None:
    """Every push is ONE atomic upsert (2026-07-13 amendment).

    Cloud note_versions was graveyarded; history is captured by the
    platform._version_capture trigger cloud-side and SQLite note_versions
    locally. The old get_note -> create_version -> update_note dance is gone.
    """
    _configure(engine)
    engine.sb.notes["n1"] = {"id": "n1", "label": "Hello", "content": "old body"}
    result = _run(engine.push_note("n1", "Hello", "new body"))
    assert result["_synced_to_cloud"] is True
    kinds = [c[0] for c in engine.sb.calls if c[0] != "set_jwt"]
    assert kinds == ["upsert_note"]
    assert engine._repo.status_calls[-1] == ("n1", "synced", content_hash("new body"))


def test_push_failure_marks_status_failed(engine: SyncEngine) -> None:
    _configure(engine)
    engine.sb.fail_pushes = True
    result = _run(engine.push_note("n1", "Hello", "body"))
    # Local write succeeded; cloud flag stays False; status recorded as failed.
    assert result["_synced_to_cloud"] is False
    assert engine.fm.notes["General/Hello.md"] == "body"
    assert engine._repo.status_calls == [("n1", "failed", None)]
    # The failed push must NOT be recorded as the last-synced hash.
    assert "General/Hello.md" not in engine.fm.state["note_hashes"]


# ---------------------------------------------------------------------------
# pull-side hash-comparison conflict guard
# ---------------------------------------------------------------------------


def _seed_remote(engine: SyncEngine, fp: str, remote_content: str) -> str:
    note_id = "remote-1"
    engine.sb.notes[note_id] = {
        "id": note_id,
        "label": Path(fp).stem,
        "content": remote_content,
        "content_hash": content_hash(remote_content),
        "folder_name": "General",
        "file_path": fp,
        "sync_version": 5,
    }
    return note_id


def test_pull_overwrites_when_local_unchanged_since_last_sync(engine: SyncEngine) -> None:
    _configure(engine)
    fp = "General/n.md"
    engine.fm.notes[fp] = "local synced content"
    engine.fm.state["note_hashes"][fp] = content_hash("local synced content")
    note_id = _seed_remote(engine, fp, "remote newer content")

    result = _run(engine.pull_note(note_id))
    assert result is not None and "_conflict" not in result
    assert engine.fm.notes[fp] == "remote newer content"
    assert engine.fm.conflicts_saved == []
    # SQLite row lands as synced with the remote hash.
    row = engine._repo.rows[note_id]
    assert row["sync_status"] == "synced"
    assert row["content_hash"] == content_hash("remote newer content")


def test_pull_conflicts_when_local_edited_since_last_sync(engine: SyncEngine) -> None:
    _configure(engine)
    fp = "General/n.md"
    engine.fm.notes[fp] = "locally edited content"
    engine.fm.state["note_hashes"][fp] = content_hash("what we synced before")
    note_id = _seed_remote(engine, fp, "remote content")

    result = _run(engine.pull_note(note_id))
    assert result is not None and result.get("_conflict") is True
    # Local file preserved; both sides captured in the conflict record.
    assert engine.fm.notes[fp] == "locally edited content"
    assert engine.fm.conflicts_saved == [
        {
            "file_path": fp,
            "local": "locally edited content",
            "remote": "remote content",
            "id": note_id,
        }
    ]


def test_pull_conflicts_when_last_known_hash_missing(engine: SyncEngine) -> None:
    """Reset/corrupted sync state → cannot prove the local file has no unsynced
    edit → conflict, never overwrite (pinned fix for the clobber bug)."""
    _configure(engine)
    fp = "General/n.md"
    engine.fm.notes[fp] = "local content"
    assert fp not in engine.fm.state["note_hashes"]
    note_id = _seed_remote(engine, fp, "remote content")

    result = _run(engine.pull_note(note_id))
    assert result is not None and result.get("_conflict") is True
    assert engine.fm.notes[fp] == "local content"


def test_pull_identical_hashes_rewrites_without_conflict(engine: SyncEngine) -> None:
    _configure(engine)
    fp = "General/n.md"
    engine.fm.notes[fp] = "same content"
    note_id = _seed_remote(engine, fp, "same content")

    result = _run(engine.pull_note(note_id))
    assert result is not None and "_conflict" not in result
    assert engine.fm.conflicts_saved == []


def test_pull_of_remote_soft_delete_propagates_deletion(engine: SyncEngine) -> None:
    _configure(engine)
    fp = "General/gone.md"
    engine.fm.notes[fp] = "doomed"
    engine.fm.state["note_hashes"][fp] = content_hash("doomed")
    engine.sb.notes["dead-1"] = {
        "id": "dead-1",
        "is_deleted": True,
        "file_path": fp,
    }

    result = _run(engine.pull_note("dead-1"))
    assert result is not None and result.get("_deleted") is True
    assert fp not in engine.fm.notes
    assert fp not in engine.fm.state["note_hashes"]
    assert engine._repo.soft_deleted == ["dead-1"]


def test_pull_skips_when_remote_is_own_devices_push(engine: SyncEngine) -> None:
    """Own-push echo guard (2026-07-13): a remote row last written by THIS
    device can never conflict with or overwrite newer local work — the local
    divergence is simply an unsynced edit that will push on its own."""
    _configure(engine)
    fp = "General/n.md"
    engine.fm.notes[fp] = "newer local draft"
    engine.fm.state["note_hashes"][fp] = content_hash("what we pushed before")
    note_id = _seed_remote(engine, fp, "what we pushed before")
    engine.sb.notes[note_id]["last_device_id"] = "test-device"

    result = _run(engine.pull_note(note_id))
    assert result is not None and result.get("_skipped_own_push") is True
    assert engine.fm.notes[fp] == "newer local draft"
    assert engine.fm.conflicts_saved == []


def test_pull_still_conflicts_when_remote_is_another_device(engine: SyncEngine) -> None:
    _configure(engine)
    fp = "General/n.md"
    engine.fm.notes[fp] = "locally edited content"
    engine.fm.state["note_hashes"][fp] = content_hash("what we synced before")
    note_id = _seed_remote(engine, fp, "remote content")
    engine.sb.notes[note_id]["last_device_id"] = "other-device"

    result = _run(engine.pull_note(note_id))
    assert result is not None and result.get("_conflict") is True
    assert engine.fm.notes[fp] == "locally edited content"


def test_prune_stale_conflicts_drops_identical_sides(engine: SyncEngine) -> None:
    engine.fm.save_conflict("General/a.md", "same", "same", "conf-same")
    engine.fm.save_conflict("General/b.md", "left", "right", "conf-diff")
    engine.fm.list_conflicts = lambda: ["conf-same", "conf-diff"]  # type: ignore[method-assign]
    pruned = engine.prune_stale_conflicts()
    assert pruned == 1
    assert engine.fm.conflicts_resolved == ["conf-same"]


def test_pull_without_user_returns_none(engine: SyncEngine) -> None:
    assert _run(engine.pull_note("whatever")) is None


def test_normalize_note_row_maps_deleted_at_to_is_deleted() -> None:
    """Wire-boundary tombstone mapping (docs/SYNC_CONTRACT.md).

    The remote workbench.notes table soft-deletes via ``deleted_at``; the
    engine's tombstone branch checks ``is_deleted``. Every remote read path
    must normalize, or remote deletions silently resurrect on pull (the
    regression the 2026-06 workbench repoint introduced)."""
    assert _normalize_note_row({"id": "n", "deleted_at": None})["is_deleted"] is False
    assert (
        _normalize_note_row({"id": "n", "deleted_at": "2026-07-10T00:00:00Z"})[
            "is_deleted"
        ]
        is True
    )
    # Rows that already carry is_deleted (fakes, legacy) are left untouched.
    assert _normalize_note_row({"id": "n", "is_deleted": True, "deleted_at": None})[
        "is_deleted"
    ] is True
    # Rows without either column (narrow selects) gain nothing.
    assert "is_deleted" not in _normalize_note_row({"id": "n"})


# ---------------------------------------------------------------------------
# Conflict resolution mode selection
# ---------------------------------------------------------------------------


def _seed_conflict(
    engine: SyncEngine,
    note_id: str = "c1",
    local: str = "LOCAL body",
    remote: str = "REMOTE body",
    fp: str = "General/Conflicted.md",
) -> str:
    engine.fm.save_conflict(fp, local, remote, note_id)
    _run(
        engine._repo.upsert(
            {
                "id": note_id,
                "label": "Conflicted",
                "title": "Conflicted",
                "folder_name": "General",
                "file_path": fp,
                "sync_status": "pending_push",
            }
        )
    )
    return note_id


def test_resolve_missing_conflict_dir_returns_none(engine: SyncEngine) -> None:
    assert _run(engine.resolve_conflict("nope", "keep_local")) is None


def test_resolve_unknown_mode_returns_none_and_keeps_conflict(engine: SyncEngine) -> None:
    note_id = _seed_conflict(engine)
    assert _run(engine.resolve_conflict(note_id, "nuke_everything")) is None
    assert engine.fm.conflicts_resolved == []


def test_resolve_keep_local(engine: SyncEngine) -> None:
    note_id = _seed_conflict(engine)
    result = _run(engine.resolve_conflict(note_id, "keep_local"))
    assert result == {"id": note_id, "resolution": "keep_local", "content": "LOCAL body"}
    assert engine.fm.notes["General/Conflicted.md"] == "LOCAL body"
    assert engine.fm.conflicts_resolved == [note_id]


def test_resolve_keep_remote_marks_synced(engine: SyncEngine) -> None:
    note_id = _seed_conflict(engine)
    result = _run(engine.resolve_conflict(note_id, "keep_remote"))
    assert result == {"id": note_id, "resolution": "keep_remote", "content": "REMOTE body"}
    assert engine.fm.notes["General/Conflicted.md"] == "REMOTE body"
    assert engine._repo.status_calls == [
        (note_id, "synced", content_hash("REMOTE body"))
    ]
    assert engine.fm.conflicts_resolved == [note_id]


def test_resolve_merge_requires_merged_content(engine: SyncEngine) -> None:
    note_id = _seed_conflict(engine)
    assert _run(engine.resolve_conflict(note_id, "merge")) is None
    result = _run(engine.resolve_conflict(note_id, "merge", merged_content="MERGED"))
    assert result == {"id": note_id, "resolution": "merge", "content": "MERGED"}
    assert engine.fm.notes["General/Conflicted.md"] == "MERGED"


def test_resolve_append_combines_local_then_remote(engine: SyncEngine) -> None:
    note_id = _seed_conflict(engine)
    result = _run(engine.resolve_conflict(note_id, "append"))
    expected = (
        "LOCAL body\n\n---\n\n*— Appended from cloud sync —*\n\nREMOTE body"
    )
    assert result is not None and result["content"] == expected
    assert engine.fm.notes["General/Conflicted.md"] == expected


def test_resolve_split_keeps_both_copies(engine: SyncEngine) -> None:
    note_id = _seed_conflict(engine)
    result = _run(engine.resolve_conflict(note_id, "split"))
    assert result is not None
    assert result["content"] == "LOCAL body"
    assert result["split_note_label"] == "Conflicted (cloud copy)"
    assert engine.fm.notes["General/Conflicted.md"] == "LOCAL body"
    assert engine.fm.notes["General/Conflicted (cloud copy).md"] == "REMOTE body"


def test_resolve_exclude_sets_excluded_flag(engine: SyncEngine) -> None:
    note_id = _seed_conflict(engine)
    result = _run(engine.resolve_conflict(note_id, "exclude"))
    assert result == {"id": note_id, "resolution": "exclude", "content": "LOCAL body"}
    assert engine._repo.excluded_calls == [(note_id, True)]


# ---------------------------------------------------------------------------
# pull_changes — echo suppression of our own pushes
# ---------------------------------------------------------------------------


def test_pull_changes_skips_notes_we_just_pushed(engine: SyncEngine) -> None:
    _configure(engine)
    fp = "General/mine.md"

    async def fake_get_notes_since(user_id: str, since: str | None) -> list[dict[str, Any]]:
        return [
            {"id": "n1", "file_path": fp, "content_hash": content_hash("pushed body")}
        ]

    engine.sb.get_notes_since = fake_get_notes_since  # type: ignore[attr-defined]
    engine._last_push_hashes[fp] = content_hash("pushed body")

    result = _run(engine.pull_changes())
    assert result["pulled"] == 0 and result["conflicts"] == 0
    assert result["skipped"] == 1


def test_pull_changes_never_echo_skips_tombstones(engine: SyncEngine) -> None:
    """A soft-delete changes deleted_at but NOT content_hash — the own-echo
    hash shortcut must not suppress the deletion (pinned 2026-07-13)."""
    _configure(engine)
    fp = "General/mine.md"
    engine.fm.notes[fp] = "pushed body"
    engine.fm.state["note_hashes"][fp] = content_hash("pushed body")
    engine._last_push_hashes[fp] = content_hash("pushed body")
    engine.sb.notes["n1"] = {"id": "n1", "file_path": fp, "is_deleted": True}

    async def fake_get_notes_since(user_id: str, since: str | None) -> list[dict[str, Any]]:
        return [
            {
                "id": "n1",
                "file_path": fp,
                "content_hash": content_hash("pushed body"),
                "is_deleted": True,
                "updated_at": "2026-07-13T00:00:00+00:00",
            }
        ]

    engine.sb.get_notes_since = fake_get_notes_since  # type: ignore[attr-defined]
    result = _run(engine.pull_changes())
    assert result["deleted"] == 1
    assert fp not in engine.fm.notes
    assert engine.fm.state["last_pull_at"] == "2026-07-13T00:00:00+00:00"


def test_pull_allocates_path_for_pathless_cloud_note_without_clobbering(
    engine: SyncEngine,
) -> None:
    """Cloud notes from other clients have file_path NULL. The pull must NOT
    derive <folder>/<label>.md directly — that overwrites whichever local note
    already owns the filename (pinned 2026-07-13)."""
    _configure(engine)
    # Existing local note occupies General/Todo.md and is tracked in SQLite.
    _run(engine._repo.upsert({"id": "local-1", "file_path": "General/Todo.md"}))
    engine.fm.notes["General/Todo.md"] = "existing local note"
    # Remote pathless note with the same label.
    engine.sb.notes["cloud-1"] = {
        "id": "cloud-1",
        "label": "Todo",
        "folder_name": "General",
        "content": "web-authored note",
        "content_hash": content_hash("web-authored note"),
        "file_path": None,
    }

    result = _run(engine.pull_note("cloud-1"))
    assert result is not None
    assert engine.fm.notes["General/Todo.md"] == "existing local note"
    row = engine._repo.rows["cloud-1"]
    assert row["file_path"] != "General/Todo.md"
    assert engine.fm.notes[row["file_path"]] == "web-authored note"


def test_push_conflicts_instead_of_overwriting_foreign_remote_edit(
    engine: SyncEngine,
) -> None:
    """Optimistic-concurrency push: when the remote row moved (another device
    wrote it since our last sync), the push must preserve BOTH sides — never
    silent last-writer-wins (pinned 2026-07-13)."""
    _configure(engine)
    fp = "General/Hello.md"
    engine.fm.state["note_hashes"][fp] = content_hash("commonly synced")
    engine.sb.notes["n1"] = {
        "id": "n1",
        "label": "Hello",
        "content": "edited on ANOTHER device",
        "content_hash": content_hash("edited on ANOTHER device"),
        "last_device_id": "other-device",
        "file_path": fp,
    }

    result = _run(engine.push_note("n1", "Hello", "edited HERE", file_path=fp))
    assert result.get("_conflict") is True
    # Cloud row untouched; both sides captured.
    assert engine.sb.notes["n1"]["content"] == "edited on ANOTHER device"
    assert engine.fm.conflicts_saved == [
        {
            "file_path": fp,
            "local": "edited HERE",
            "remote": "edited on ANOTHER device",
            "id": "n1",
        }
    ]
    # note_hashes must NOT advance — nothing synced.
    assert engine.fm.state["note_hashes"][fp] == content_hash("commonly synced")
