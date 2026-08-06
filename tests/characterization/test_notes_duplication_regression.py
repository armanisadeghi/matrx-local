"""Regression pins for the 2026-07/08 notes duplication factory.

Two shipped defects mass-duplicated cloud notes (764 corpus clones on
2026-07-14; ~2,100 suffix-chained ``label_2_2_...`` notes on 2026-07-29/30),
fixed in e02846daf + 24a7e659e:

  1. full_sync minted a brand-new cloud note (uuid5 of path) for any local
     file with no SQLite identity and no cloud file_path match — any identity
     loss re-minted the entire replica as duplicates.
  2. The contested-path reroute in _pull_note allocated ``label_2.md`` but
     never converged the cloud row on it, so every pull re-detected the same
     collision and allocated yet another suffix.
  3. full_sync's live-rows-only cloud snapshot made remotely-tombstoned notes
     look "local-only", and pushing them under their existing id resurrected
     soft-deleted rows (reviving the 2,599-row duplicate cleanup).

Every test here reproduces the pre-fix trigger and asserts the guard holds.
If one of these fails, the duplicate factory is back — do NOT weaken the
assertion; fix the sync engine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

try:
    from app.services.documents.file_manager import content_hash
    from app.services.documents.sync_engine import SyncEngine, _note_id_for_path
except Exception as exc:  # pragma: no cover — env-dependent import guard
    pytest.skip(
        f"documents sync engine not importable in this environment: {exc}",
        allow_module_level=True,
    )

from tests.characterization.test_documents_sync_characterization import (
    FakeFileManager,
    FakeNotesRepo,
    FakeSupabase,
)


def _run(coro):
    return asyncio.run(coro)


class FakeSupabaseFull(FakeSupabase):
    """FakeSupabase extended with the full_sync / path write-back surface."""

    def __init__(self) -> None:
        super().__init__()
        self.path_writebacks: list[tuple[str, str, str | None]] = []
        self.cas_writebacks: list[tuple[str, str, str]] = []

    async def get_all_notes_with_hashes(self, user_id: str) -> list[dict[str, Any]]:
        # Mirrors the real client: LIVE rows only — tombstones are excluded
        # from the full_sync snapshot by design (docs/SYNC_CONTRACT.md).
        return [r for r in self.notes.values() if not r.get("is_deleted")]

    async def set_file_path_if_null(
        self, note_id: str, file_path: str, device_id: str | None = None
    ) -> bool:
        self.path_writebacks.append((note_id, file_path, device_id))
        row = self.notes.get(note_id)
        if row is None or row.get("file_path"):
            return False
        row["file_path"] = file_path
        return True

    async def set_file_path_if_matches(
        self,
        note_id: str,
        expected_file_path: str,
        new_file_path: str,
        device_id: str | None = None,
    ) -> bool:
        self.cas_writebacks.append((note_id, expected_file_path, new_file_path))
        row = self.notes.get(note_id)
        if row is None or row.get("file_path") != expected_file_path:
            return False
        row["file_path"] = new_file_path
        return True

    async def soft_delete_note(
        self, note_id: str, device_id: str | None = None
    ) -> None:
        self.calls.append(("soft_delete_note", {"note_id": note_id}))
        row = self.notes.get(note_id)
        if row is not None:
            row["is_deleted"] = True

    def upsert_count(self) -> int:
        return sum(1 for c in self.calls if c[0] == "upsert_note")


@pytest.fixture()
def engine(tmp_path: Path) -> SyncEngine:
    eng = SyncEngine(fm=FakeFileManager(tmp_path), sb=FakeSupabaseFull())  # type: ignore[arg-type]
    repo = FakeNotesRepo()
    eng._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    eng._repo = repo  # test-side handle
    eng._device_id = "test-device"
    eng.configure(user_id="user-1", jwt="jwt-1")
    return eng


# ---------------------------------------------------------------------------
# _pull_note — contested path (defect 2: the suffix-chain loop)
# ---------------------------------------------------------------------------


def _seed_owner(engine: SyncEngine, fp: str, content: str, note_id: str) -> None:
    engine.fm.notes[fp] = content
    engine._repo.rows[note_id] = {
        "id": note_id,
        "file_path": fp,
        "content_hash": content_hash(content),
        "label": Path(fp).stem,
        "is_deleted": False,
    }


def test_contested_pull_identical_content_is_not_materialized(engine: SyncEngine) -> None:
    """A cloud-side duplicate row (same path, same bytes) must NOT be given a
    local ``label_2.md`` — materializing it seeded the duplicate factory."""
    _seed_owner(engine, "Draft/idea.md", "same body", "owner-id")
    incoming = {
        "id": "dupe-id",
        "file_path": "Draft/idea.md",
        "content": "same body",
        "content_hash": content_hash("same body"),
        "label": "idea",
        "folder_name": "Draft",
    }
    result = _run(engine._pull_note("dupe-id", note=incoming))
    assert result is not None and result.get("_skipped_duplicate_content") is True
    assert set(engine.fm.notes) == {"Draft/idea.md"}  # no idea_2.md
    assert "dupe-id" not in engine._repo.rows  # no local row minted
    assert engine.sb.cas_writebacks == []
    assert engine.sb.path_writebacks == []


def test_contested_pull_divergent_content_reroutes_and_converges_cloud(
    engine: SyncEngine,
) -> None:
    """Genuine divergence still reroutes — but MUST converge the cloud row on
    the new path (CAS on the contested one). Without the write-back every
    pull allocated another _2 suffix (label_2_2_2.md chain)."""
    _seed_owner(engine, "Draft/idea.md", "owner body", "owner-id")
    engine.sb.notes["other-id"] = {
        "id": "other-id",
        "file_path": "Draft/idea.md",
        "content": "different body",
        "content_hash": content_hash("different body"),
        "label": "idea",
        "folder_name": "Draft",
    }
    result = _run(engine._pull_note("other-id", note=dict(engine.sb.notes["other-id"])))
    assert result is not None and result.get("_conflict") is None
    assert engine.fm.notes["Draft/idea_2.md"] == "different body"
    # The cloud row converged on the rerouted path via CAS — not if_null.
    assert engine.sb.cas_writebacks == [("other-id", "Draft/idea.md", "Draft/idea_2.md")]
    assert engine.sb.notes["other-id"]["file_path"] == "Draft/idea_2.md"

    # A SECOND pull of the (now converged) row must not allocate idea_3.md.
    result2 = _run(engine._pull_note("other-id", note=dict(engine.sb.notes["other-id"])))
    assert result2 is not None
    assert "Draft/idea_3.md" not in engine.fm.notes
    assert len(engine.sb.cas_writebacks) == 1  # no second reroute


# ---------------------------------------------------------------------------
# full_sync — identity loss (defect 1: the mass re-mint)
# ---------------------------------------------------------------------------


def test_full_sync_identity_loss_adopts_pathless_twin(engine: SyncEngine) -> None:
    """Local file with no SQLite row + pathless cloud note with identical
    bytes → BIND them; never mint a new cloud note."""
    engine.fm.notes["Draft/lost.md"] = "twin body"
    h = content_hash("twin body")
    engine.sb.notes["twin-id"] = {
        "id": "twin-id",
        "file_path": None,
        "content": "twin body",
        "content_hash": h,
        "label": "lost",
        "folder_name": "Draft",
        "sync_version": 3,
    }
    stats = _run(engine.full_sync())
    assert stats.get("adopted") == 1
    assert stats["pushed"] == 0
    assert engine.sb.upsert_count() == 0  # nothing minted
    assert engine._repo.rows["twin-id"]["file_path"] == "Draft/lost.md"
    assert engine.sb.notes["twin-id"]["file_path"] == "Draft/lost.md"
    assert engine.fm.state["note_hashes"]["Draft/lost.md"] == h


def test_full_sync_identity_loss_skips_bound_twin(engine: SyncEngine) -> None:
    """Identical bytes already live in the cloud under ANOTHER path → the
    local file is a redundant copy; do not push it as a new note."""
    engine.fm.notes["Draft/copy.md"] = "shared body"
    engine.sb.notes["bound-id"] = {
        "id": "bound-id",
        "file_path": "Draft/original.md",
        "content": "shared body",
        "content_hash": content_hash("shared body"),
        "label": "original",
        "folder_name": "Draft",
    }
    # The bound twin's own path also exists locally so it reads "unchanged".
    _seed_owner(engine, "Draft/original.md", "shared body", "bound-id")
    stats = _run(engine.full_sync())
    assert stats.get("skipped_duplicate") == 1
    assert stats["pushed"] == 0
    assert engine.sb.upsert_count() == 0
    # The redundant file itself is left alone on disk.
    assert engine.fm.notes["Draft/copy.md"] == "shared body"


def test_full_sync_genuinely_new_file_still_pushes(engine: SyncEngine) -> None:
    """The guard must not over-block: unique local content with no cloud twin
    is a real new note and still pushes."""
    engine.fm.notes["Draft/new.md"] = "brand new body"
    stats = _run(engine.full_sync())
    assert stats["pushed"] == 1
    assert engine.sb.upsert_count() == 1
    pushed = next(c[1] for c in engine.sb.calls if c[0] == "upsert_note")
    assert pushed["note_id"] == _note_id_for_path("Draft/new.md")


# ---------------------------------------------------------------------------
# full_sync — remote tombstone vs local-only file (defect 3: resurrection)
# ---------------------------------------------------------------------------


def _seed_tombstoned_remote(engine: SyncEngine, fp: str, content: str, note_id: str) -> None:
    _seed_owner(engine, fp, content, note_id)
    engine.sb.notes[note_id] = {
        "id": note_id,
        "file_path": fp,
        "content": content,
        "content_hash": content_hash(content),
        "label": Path(fp).stem,
        "folder_name": "Draft",
        "is_deleted": True,
    }


def test_full_sync_unchanged_local_file_with_remote_tombstone_deletes_locally(
    engine: SyncEngine,
) -> None:
    """A remotely soft-deleted note (excluded from the live snapshot) looks
    'local-only'. For byte-unchanged content the deletion wins — pushing it
    would resurrect the row (revival of the 2026-08-06 duplicate cleanup)."""
    _seed_tombstoned_remote(engine, "Draft/deleted.md", "stale body", "dead-id")
    stats = _run(engine.full_sync())
    assert stats["deleted_local"] == 1
    assert stats["pushed"] == 0
    assert engine.sb.upsert_count() == 0  # not resurrected
    assert "Draft/deleted.md" not in engine.fm.notes  # deletion propagated
    assert "dead-id" in engine._repo.soft_deleted


def test_full_sync_edited_local_file_with_remote_tombstone_resurrects(
    engine: SyncEngine,
) -> None:
    """A LOCAL EDIT after the remote delete is user work — resurrection is
    correct there; the tombstone guard must not eat it."""
    _seed_tombstoned_remote(engine, "Draft/edited.md", "old body", "dead-id")
    engine.fm.notes["Draft/edited.md"] = "edited after delete"
    engine._repo.rows["dead-id"]["content_hash"] = content_hash("old body")
    stats = _run(engine.full_sync())
    assert stats["deleted_local"] == 0
    assert stats["pushed"] == 1
    assert engine.sb.upsert_count() == 1
    assert engine.fm.notes["Draft/edited.md"] == "edited after delete"


# ---------------------------------------------------------------------------
# _handle_external_change — watcher-side duplicate guard
# ---------------------------------------------------------------------------


def test_external_change_identical_bytes_not_pushed_as_new_note(
    engine: SyncEngine,
) -> None:
    """A watcher-detected 'new' file whose exact bytes already exist in the
    cloud under another note is a sync artifact — pushing it minted a
    duplicate cloud note."""
    engine.sb.notes["existing-id"] = {
        "id": "existing-id",
        "file_path": "Draft/original.md",
        "content": "shared body",
        "content_hash": content_hash("shared body"),
        "label": "original",
        "folder_name": "Draft",
    }
    engine.fm.notes["Draft/artifact-copy.md"] = "shared body"
    _run(engine._handle_external_change("Draft/artifact-copy.md"))
    assert engine.sb.upsert_count() == 0
    # The SQLite row for the file still exists (metadata tracking is fine —
    # only the cloud push is suppressed).
    row = _run(engine._repo.get_by_file_path("Draft/artifact-copy.md"))
    assert row is not None


def test_external_change_unique_bytes_still_pushes(engine: SyncEngine) -> None:
    """The watcher guard must not over-block genuinely new content."""
    engine.fm.notes["Draft/fresh.md"] = "unique fresh body"
    _run(engine._handle_external_change("Draft/fresh.md"))
    assert engine.sb.upsert_count() == 1
