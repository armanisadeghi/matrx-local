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
    from app.services.documents.file_manager import DocumentFileManager, content_hash
    from app.services.documents.sync_engine import SyncEngine
    from app.services.local_db.database import LocalDatabase
    from app.services.local_db.repositories import NotesRepo
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
        actor_tier: str | None = None,
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


class TempDocumentFileManager(DocumentFileManager):
    """Real file operations rooted in pytest's temporary directory only."""

    def __init__(self, base_dir: Path) -> None:
        # DocumentFileManager's production constructor creates the configured
        # application directories. This regression needs real files without
        # touching any installed-app directory.
        self._explicit_base = base_dir


@pytest.fixture()
def engine(tmp_path: Path) -> SyncEngine:
    eng = SyncEngine(fm=FakeFileManager(tmp_path), sb=FakeSupabaseFull())  # type: ignore[arg-type]
    repo = FakeNotesRepo()
    eng._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    eng._repo = repo  # test-side handle
    eng._device_id = "test-device"
    eng.configure(user_id="user-1", jwt="jwt-1")
    async def _test_watcher_principal() -> str:
        eng.configure(user_id="user-1", jwt="jwt-1")
        return "user-1"
    eng._bind_watcher_principal = _test_watcher_principal  # type: ignore[method-assign]
    return eng


# ---------------------------------------------------------------------------
# _pull_note — contested path (defect 2: the suffix-chain loop)
# ---------------------------------------------------------------------------


def _seed_owner(engine: SyncEngine, fp: str, content: str, note_id: str) -> None:
    engine.fm.notes[fp] = content
    engine._repo.rows[note_id] = {
        "id": note_id,
        "user_id": "user-1",
        "file_path": fp,
        "content_hash": content_hash(content),
        "label": Path(fp).stem,
        "is_deleted": False,
    }


def test_contested_pull_identical_content_preserves_both_identities(
    engine: SyncEngine,
) -> None:
    """Identical cloud rows still need separate local identities and paths."""
    _seed_owner(engine, "Draft/idea.md", "same body", "owner-id")
    incoming = {
        "id": "dupe-id",
        "file_path": "Draft/idea.md",
        "content": "same body",
        "content_hash": content_hash("same body"),
        "label": "idea",
        "folder_name": "Draft",
    }
    engine.sb.notes["dupe-id"] = dict(incoming)
    result = _run(engine._pull_note("dupe-id", note=incoming))
    assert result is not None
    duplicate_path = engine._repo.rows["dupe-id"]["file_path"]
    assert duplicate_path.startswith("Draft/idea--")
    assert duplicate_path.endswith(".md")
    assert engine.fm.notes == {
        "Draft/idea.md": "same body",
        duplicate_path: "same body",
    }
    assert engine.sb.cas_writebacks == [("dupe-id", "Draft/idea.md", duplicate_path)]
    assert engine.sb.notes["dupe-id"]["file_path"] == duplicate_path
    assert engine.sb.path_writebacks == []

    # Replaying the converged row must retain the same identity and path.
    result2 = _run(engine._pull_note("dupe-id", note=dict(engine.sb.notes["dupe-id"])))
    assert result2 is not None
    assert set(engine.fm.notes) == {"Draft/idea.md", duplicate_path}
    assert len(engine.sb.cas_writebacks) == 1


def test_contested_pull_identical_content_preserves_pending_identity_edit(
    engine: SyncEngine,
) -> None:
    """A retry may not overwrite a locally edited duplicate identity."""
    _seed_owner(engine, "Draft/idea.md", "same body", "owner-id")
    incoming = {
        "id": "dupe-id",
        "file_path": "Draft/idea.md",
        "content": "same body",
        "content_hash": content_hash("same body"),
        "label": "idea",
        "folder_name": "Draft",
    }
    engine.sb.notes["dupe-id"] = dict(incoming)
    first = _run(engine._pull_note("dupe-id", note=incoming))
    assert first is not None
    duplicate_path = engine._repo.rows["dupe-id"]["file_path"]
    engine.fm.notes[duplicate_path] = "unsynced local edit"

    result = _run(engine._pull_note("dupe-id", note=dict(engine.sb.notes["dupe-id"])))
    assert result is not None and result.get("_conflict") is True
    assert engine.fm.notes[duplicate_path] == "unsynced local edit"


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
    rerouted_path = engine._repo.rows["other-id"]["file_path"]
    assert rerouted_path.startswith("Draft/idea--")
    assert engine.fm.notes[rerouted_path] == "different body"
    # The cloud row converged on the rerouted path via CAS — not if_null.
    assert engine.sb.cas_writebacks == [("other-id", "Draft/idea.md", rerouted_path)]
    assert engine.sb.notes["other-id"]["file_path"] == rerouted_path

    # A second pull of the converged row must retain the same path.
    result2 = _run(
        engine._pull_note("other-id", note=dict(engine.sb.notes["other-id"]))
    )
    assert result2 is not None
    assert set(engine.fm.notes) == {"Draft/idea.md", rerouted_path}
    assert len(engine.sb.cas_writebacks) == 1  # no second reroute


def test_contested_pull_cas_loser_retries_same_identity_path(
    engine: SyncEngine,
) -> None:
    """A CAS loss leaves both local identities intact and never grows suffixes."""
    _seed_owner(engine, "Draft/idea.md", "same body", "owner-id")
    incoming = {
        "id": "dupe-id",
        "file_path": "Draft/idea.md",
        "content": "same body",
        "content_hash": content_hash("same body"),
        "label": "idea",
        "folder_name": "Draft",
    }
    engine.sb.notes["dupe-id"] = dict(incoming)
    cas_calls: list[tuple[str, str, str]] = []

    async def lose_cas(
        note_id: str, expected: str, allocated: str, *_args, **_kwargs
    ) -> bool:
        cas_calls.append((note_id, expected, allocated))
        return False

    engine.sb.set_file_path_if_matches = lose_cas  # type: ignore[method-assign]
    first = _run(engine._pull_note("dupe-id", note=dict(incoming)))
    assert first is not None
    duplicate_path = engine._repo.rows["dupe-id"]["file_path"]
    second = _run(engine._pull_note("dupe-id", note=dict(incoming)))
    assert second is not None
    assert set(engine.fm.notes) == {"Draft/idea.md", duplicate_path}
    assert cas_calls == [
        ("dupe-id", "Draft/idea.md", duplicate_path),
        ("dupe-id", "Draft/idea.md", duplicate_path),
    ]


def test_contested_reroute_keeps_other_identity_real_file_and_mapping(
    tmp_path: Path,
) -> None:
    """Two persisted IDs on one path cannot let B's reroute delete A's file."""
    fm = TempDocumentFileManager(tmp_path)
    sb = FakeSupabaseFull()
    engine = SyncEngine(fm=fm, sb=sb)
    repo = FakeNotesRepo()
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    engine._device_id = "test-device"
    engine.configure(user_id="user-1", jwt="jwt-1")

    contested_path = fm.write_note("Draft", "idea", "A unsynced user work")
    repo.rows["owner-a"] = {
        "id": "owner-a",
        "user_id": "user-1",
        "file_path": contested_path,
        "content_hash": content_hash("A synced body"),
        "remote_content_hash": content_hash("A synced body"),
        "is_deleted": False,
    }
    # This is the persisted incoming mapping that previously drove the
    # unconditional old-path delete after B was rerouted.
    repo.rows["incoming-b"] = {
        "id": "incoming-b",
        "user_id": "user-1",
        "file_path": contested_path,
        "content_hash": content_hash("B old body"),
        "remote_content_hash": content_hash("B old body"),
        "is_deleted": False,
    }
    incoming = {
        "id": "incoming-b",
        "file_path": contested_path,
        "content": "B remote body",
        "content_hash": content_hash("B remote body"),
        "label": "idea",
        "folder_name": "Draft",
    }
    sb.notes["incoming-b"] = dict(incoming)
    state = engine._load_sync_state()
    state["note_hashes"] = {contested_path: content_hash("A synced body")}
    engine._save_sync_state(state)

    result = _run(engine._pull_note("incoming-b", note=incoming))
    assert result is not None
    rerouted_path = repo.rows["incoming-b"]["file_path"]
    assert repo.rows["owner-a"]["file_path"] == contested_path
    assert (tmp_path / contested_path).read_text(encoding="utf-8") == "A unsynced user work"
    assert (tmp_path / rerouted_path).read_text(encoding="utf-8") == "B remote body"


@pytest.mark.parametrize("insert_order", [("incoming-b", "owner-a"), ("owner-a", "incoming-b")])
@pytest.mark.parametrize("remote_order", [("incoming-b", "owner-a"), ("owner-a", "incoming-b")])
def test_real_sqlite_collision_set_preserves_both_ids_in_direct_and_full_sync(
    tmp_path: Path, insert_order: tuple[str, str], remote_order: tuple[str, str]
) -> None:
    """Neither SQLite nor cloud arrival order can steal unsynced ownership."""

    async def run_case(mode: str) -> None:
        root = tmp_path / f"{mode}-{insert_order[0]}-{remote_order[0]}"
        db = LocalDatabase(root / "notes.db")
        await db.connect()
        try:
            repo = NotesRepo(db)
            fm = TempDocumentFileManager(root / "files")
            sb = FakeSupabaseFull()
            engine = SyncEngine(fm=fm, sb=sb)
            engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
            engine._device_id = "test-device"
            engine.configure(user_id="user-1", jwt="jwt-1")

            contested_path = fm.write_note("Draft", "idea", "A unsynced user work")
            rows = {
                "owner-a": {
                    "id": "owner-a",
                    "user_id": "user-1",
                    "file_path": contested_path,
                    "content": "A synced body",
                    "content_hash": content_hash("A synced body"),
                    "remote_content_hash": content_hash("A synced body"),
                    "sync_status": "synced",
                },
                "incoming-b": {
                    "id": "incoming-b",
                    "user_id": "user-1",
                    "file_path": contested_path,
                    "content": "B old body",
                    "content_hash": content_hash("B old body"),
                    "remote_content_hash": content_hash("B old body"),
                    "sync_status": "synced",
                },
            }
            for note_id in insert_order:
                await repo.upsert(rows[note_id])

            remote_a = {
                "id": "owner-a",
                "file_path": contested_path,
                "content": "A synced body",
                "content_hash": content_hash("A synced body"),
                "label": "idea",
                "folder_name": "Draft",
            }
            remote_b = {
                "id": "incoming-b",
                "file_path": contested_path,
                "content": "B remote body",
                "content_hash": content_hash("B remote body"),
                "label": "idea",
                "folder_name": "Draft",
            }
            remotes = {"incoming-b": remote_b, "owner-a": remote_a}
            sb.notes = {note_id: dict(remotes[note_id]) for note_id in remote_order}

            state = engine._load_sync_state()
            state["note_hashes"] = {contested_path: content_hash("A synced body")}
            engine._save_sync_state(state)

            if mode == "direct":
                result = await engine._pull_note("incoming-b", note=remote_b)
                assert result is not None
            else:
                await engine.full_sync()

            owner = await repo.get("owner-a")
            incoming = await repo.get("incoming-b")
            assert owner is not None and incoming is not None
            assert owner["file_path"] == contested_path
            assert incoming["file_path"] != contested_path
            assert (root / "files" / contested_path).read_text(encoding="utf-8") == "A unsynced user work"
            assert (root / "files" / incoming["file_path"]).read_text(encoding="utf-8") == "B remote body"
        finally:
            await db.close()

    _run(run_case("direct"))
    _run(run_case("full"))


def test_ambiguous_same_hash_collision_preserves_files_as_conflict(tmp_path: Path) -> None:
    """Edited bytes with multiple equally plausible owners must not be guessed."""

    async def run_case() -> None:
        db = LocalDatabase(tmp_path / "notes.db")
        await db.connect()
        try:
            repo = NotesRepo(db)
            fm = TempDocumentFileManager(tmp_path / "files")
            sb = FakeSupabaseFull()
            engine = SyncEngine(fm=fm, sb=sb)
            engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
            engine._device_id = "test-device"
            engine.configure(user_id="user-1", jwt="jwt-1")

            contested_path = fm.write_note("Draft", "idea", "edited local corpus")
            synced_hash = content_hash("shared synced body")
            for note_id in ("owner-a", "incoming-b"):
                await repo.upsert({
                    "id": note_id,
                    "user_id": "user-1",
                    "file_path": contested_path,
                    "content": "shared synced body",
                    "content_hash": synced_hash,
                    "remote_content_hash": synced_hash,
                    "sync_status": "synced",
                })
            remote_a = {
                "id": "owner-a", "file_path": contested_path,
                "content": "A remote body", "content_hash": content_hash("A remote body"),
                "label": "idea", "folder_name": "Draft",
            }
            remote_b = {
                "id": "incoming-b", "file_path": contested_path,
                "content": "B remote body", "content_hash": content_hash("B remote body"),
                "label": "idea", "folder_name": "Draft",
            }
            sb.notes = {"incoming-b": remote_b, "owner-a": remote_a}
            state = engine._load_sync_state()
            state["note_hashes"] = {contested_path: synced_hash}
            engine._save_sync_state(state)

            result = await engine._pull_note("incoming-b", note=remote_b)
            assert result is not None and result.get("_collision_conflict") is True
            stats = await engine.full_sync()
            assert stats["conflicts"] >= 2
            assert stats["pulled"] == 0
            assert (tmp_path / "files" / contested_path).read_text(encoding="utf-8") == "edited local corpus"
            owner = await repo.get("owner-a")
            incoming = await repo.get("incoming-b")
            assert owner is not None and owner["file_path"] == contested_path
            assert incoming is not None and incoming["file_path"] == contested_path
        finally:
            await db.close()

    _run(run_case())


def test_singleton_remote_sibling_preserves_unmatched_local_keeper(tmp_path: Path) -> None:
    """A sole remote sibling cannot replace a local keeper missing from cloud."""

    async def run_case() -> None:
        db = LocalDatabase(tmp_path / "notes.db")
        await db.connect()
        try:
            repo = NotesRepo(db)
            fm = TempDocumentFileManager(tmp_path / "files")
            sb = FakeSupabaseFull()
            engine = SyncEngine(fm=fm, sb=sb)
            engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
            engine._device_id = "test-device"
            engine.configure(user_id="user-1", jwt="jwt-1")
            contested_path = fm.write_note("Draft", "idea", "A unsynced user work")
            synced_hash = content_hash("A synced body")
            await repo.upsert({
                "id": "owner-a", "user_id": "user-1", "file_path": contested_path,
                "content": "A synced body", "content_hash": synced_hash,
                "remote_content_hash": synced_hash, "sync_status": "synced",
            })
            remote_b = {
                "id": "incoming-b", "file_path": contested_path,
                "content": "B remote body", "content_hash": content_hash("B remote body"),
                "label": "idea", "folder_name": "Draft",
            }
            sb.notes = {"incoming-b": remote_b}
            state = engine._load_sync_state()
            state["note_hashes"] = {contested_path: synced_hash}
            engine._save_sync_state(state)

            stats = await engine.full_sync()
            owner = await repo.get("owner-a")
            incoming = await repo.get("incoming-b")
            assert owner is not None and owner["file_path"] == contested_path
            assert incoming is not None and incoming["file_path"] != contested_path
            assert (tmp_path / "files" / contested_path).read_text(encoding="utf-8") == "A unsynced user work"
            assert (tmp_path / "files" / incoming["file_path"]).read_text(encoding="utf-8") == "B remote body"
            assert stats["pushed"] == 0
        finally:
            await db.close()

    _run(run_case())


def test_clean_initial_remote_duplicate_group_materializes_all_identities(tmp_path: Path) -> None:
    """A duplicate cloud group with no local bytes gets a stable initial keeper."""

    async def run_case() -> None:
        db = LocalDatabase(tmp_path / "notes.db")
        await db.connect()
        try:
            repo = NotesRepo(db)
            fm = TempDocumentFileManager(tmp_path / "files")
            sb = FakeSupabaseFull()
            engine = SyncEngine(fm=fm, sb=sb)
            engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
            engine._device_id = "test-device"
            engine.configure(user_id="user-1", jwt="jwt-1")
            contested_path = "Draft/idea.md"
            remote_a = {
                "id": "owner-a", "file_path": contested_path, "content": "A remote body",
                "content_hash": content_hash("A remote body"), "label": "idea", "folder_name": "Draft",
            }
            remote_b = {
                "id": "incoming-b", "file_path": contested_path, "content": "B remote body",
                "content_hash": content_hash("B remote body"), "label": "idea", "folder_name": "Draft",
            }
            sb.notes = {"incoming-b": remote_b, "owner-a": remote_a}
            stats = await engine.full_sync()
            owner = await repo.get("owner-a")
            incoming = await repo.get("incoming-b")
            # Stable cloud-ID ordering selects the original-path keeper on a
            # clean first import; arrival order cannot change it.
            assert incoming is not None and incoming["file_path"] == contested_path
            assert owner is not None and owner["file_path"] != contested_path
            assert (tmp_path / "files" / contested_path).read_text(encoding="utf-8") == "B remote body"
            assert (tmp_path / "files" / owner["file_path"]).read_text(encoding="utf-8") == "A remote body"
            assert stats["conflicts"] == 0
        finally:
            await db.close()

    _run(run_case())


def test_tombstone_keeps_shared_path_owned_by_other_live_identity(tmp_path: Path) -> None:
    """A B-first SQLite lookup cannot let B's tombstone delete A's file."""

    async def run_case() -> None:
        db = LocalDatabase(tmp_path / "notes.db")
        await db.connect()
        try:
            repo = NotesRepo(db)
            fm = TempDocumentFileManager(tmp_path / "files")
            engine = SyncEngine(fm=fm, sb=FakeSupabaseFull())
            engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
            engine._device_id = "test-device"
            engine.configure(user_id="user-1", jwt="jwt-1")
            contested_path = fm.write_note("Draft", "idea", "A live bytes")
            for note_id in ("incoming-b", "owner-a"):
                await repo.upsert({
                    "id": note_id, "user_id": "user-1", "file_path": contested_path,
                    "content": "A live bytes", "content_hash": content_hash("A live bytes"),
                    "remote_content_hash": content_hash("A live bytes"), "sync_status": "synced",
                })
            result = await engine._pull_note("incoming-b", note={
                "id": "incoming-b", "file_path": contested_path, "is_deleted": True,
                "content_hash": content_hash("A live bytes"),
            })
            assert result is not None and result.get("_deleted") is True
            assert (tmp_path / "files" / contested_path).read_text(encoding="utf-8") == "A live bytes"
            owner = await repo.get("owner-a")
            incoming = await repo.get("incoming-b")
            assert owner is not None and owner["is_deleted"] is False
            assert incoming is not None and incoming["is_deleted"] is True
        finally:
            await db.close()

    _run(run_case())


def test_pending_incoming_collision_is_explicit_conflict(tmp_path: Path) -> None:
    """Rerouting never bypasses pending local work for the incoming identity."""

    fm = TempDocumentFileManager(tmp_path)
    engine = SyncEngine(fm=fm, sb=FakeSupabaseFull())
    repo = FakeNotesRepo()
    engine._get_notes_repo = lambda: repo  # type: ignore[method-assign]
    engine._device_id = "test-device"
    engine.configure(user_id="user-1", jwt="jwt-1")
    contested_path = fm.write_note("Draft", "idea", "A bytes")
    synced_hash = content_hash("A bytes")
    repo.rows["owner-a"] = {
        "id": "owner-a", "user_id": "user-1", "file_path": contested_path,
        "remote_content_hash": synced_hash, "sync_status": "synced",
    }
    repo.rows["incoming-b"] = {
        "id": "incoming-b", "user_id": "user-1", "file_path": contested_path,
        "remote_content_hash": content_hash("B old bytes"), "sync_status": "pending_push",
    }
    state = engine._load_sync_state()
    state["note_hashes"] = {contested_path: synced_hash}
    engine._save_sync_state(state)
    result = _run(engine._pull_note("incoming-b", note={
        "id": "incoming-b", "file_path": contested_path, "content": "B remote bytes",
        "content_hash": content_hash("B remote bytes"), "label": "idea", "folder_name": "Draft",
    }))
    assert result is not None and result.get("_collision_conflict") is True
    assert repo.rows["incoming-b"]["file_path"] == contested_path
    assert (tmp_path / contested_path).read_text(encoding="utf-8") == "A bytes"


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
    assert engine.fm.state["accounts"]["user-1"]["note_hashes"]["Draft/lost.md"] == h


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


def test_full_sync_unindexed_unique_file_is_deferred(engine: SyncEngine) -> None:
    """A raw file cannot be assigned to the account active at reconciliation."""
    engine.fm.notes["Draft/new.md"] = "brand new body"
    stats = _run(engine.full_sync())
    assert stats["pushed"] == 0
    assert stats["deferred_account"] == 1
    assert engine.sb.upsert_count() == 0
    assert engine.fm.notes["Draft/new.md"] == "brand new body"


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
    engine._repo.rows["artifact-copy"] = {
        "id": "artifact-copy", "user_id": "user-1",
        "file_path": "Draft/artifact-copy.md", "sync_status": "pending_push",
        "sync_enabled": True,
    }
    _run(engine._handle_external_change("Draft/artifact-copy.md"))
    assert engine.sb.upsert_count() == 0
    # The SQLite row for the file still exists (metadata tracking is fine —
    # only the cloud push is suppressed).
    row = _run(engine._repo.get_by_file_path("Draft/artifact-copy.md"))
    assert row is not None


def test_external_change_owned_unique_bytes_still_pushes(engine: SyncEngine) -> None:
    """A persisted same-account row still pushes its external edit."""
    engine.fm.notes["Draft/fresh.md"] = "unique fresh body"
    engine._repo.rows["fresh-id"] = {
        "id": "fresh-id", "user_id": "user-1", "file_path": "Draft/fresh.md",
        "sync_status": "pending_push", "sync_enabled": True,
    }
    _run(engine._handle_external_change("Draft/fresh.md"))
    assert engine.sb.upsert_count() == 1


# ---------------------------------------------------------------------------
# push_all — the remaining unguarded funnel (2026-08-07 " 2" copies incident)
# ---------------------------------------------------------------------------
#
# 34 Finder-style "label 2.md" duplicate files (made visible by the TCC fix)
# went watcher → never_synced SQLite row → push_all → 34 duplicate cloud
# notes, AFTER the full_sync/_pull_note guards shipped. push_all must apply
# the same never-mint-identical-bytes and never-resurrect-unchanged rules.


def _seed_pending_file(
    engine: SyncEngine,
    fp: str,
    content: str,
    note_id: str,
    sync_status: str = "never_synced",
    remote_content_hash: str | None = None,
) -> None:
    engine.fm.notes[fp] = content
    engine._repo.rows[note_id] = {
        "id": note_id,
        "user_id": "user-1",
        "file_path": fp,
        "label": Path(fp).stem,
        "content_hash": content_hash(content),
        "sync_status": sync_status,
        "sync_enabled": True,
        "remote_content_hash": remote_content_hash,
        "is_deleted": False,
    }


def test_push_all_never_synced_duplicate_bytes_not_minted(engine: SyncEngine) -> None:
    """A never-synced pending row whose file bytes already exist in the cloud
    under another note must NOT be pushed as a new cloud note."""
    engine.sb.notes["cloud-orig"] = {
        "id": "cloud-orig",
        "file_path": "Draft/original.md",
        "content": "same body",
        "content_hash": content_hash("same body"),
        "label": "original",
        "folder_name": "Draft",
    }
    _seed_pending_file(engine, "Draft/original 2.md", "same body", "finder-copy-id")
    stats = _run(engine.push_all())
    assert stats.get("skipped_duplicate") == 1
    assert stats["pushed"] == 0
    assert engine.sb.upsert_count() == 0
    assert "finder-copy-id" not in engine.sb.notes


def test_push_all_never_synced_unique_bytes_still_pushes(engine: SyncEngine) -> None:
    """The guard must not over-block genuinely new content."""
    _seed_pending_file(engine, "Draft/fresh.md", "unique body", "fresh-id")
    stats = _run(engine.push_all())
    assert stats["pushed"] == 1
    assert engine.sb.upsert_count() == 1


def test_push_all_unchanged_pending_with_remote_tombstone_propagates_delete(
    engine: SyncEngine,
) -> None:
    """A previously-synced pending row whose remote twin was soft-deleted and
    whose local bytes are UNCHANGED must propagate the deletion, not
    resurrect the cloud row (upsert_note clears deleted_at by design)."""
    body = "kept body"
    engine.sb.notes["dead-id"] = {
        "id": "dead-id",
        "file_path": "Draft/dead.md",
        "content": body,
        "content_hash": content_hash(body),
        "label": "dead",
        "folder_name": "Draft",
        "is_deleted": True,
    }
    _seed_pending_file(
        engine,
        "Draft/dead.md",
        body,
        "dead-id",
        sync_status="pending_push",
        remote_content_hash=content_hash(body),
    )
    stats = _run(engine.push_all())
    assert stats.get("deleted_local") == 1
    assert stats["pushed"] == 0
    assert engine.sb.upsert_count() == 0
    assert engine.sb.notes["dead-id"].get("is_deleted") is True  # not resurrected
    assert "Draft/dead.md" not in engine.fm.notes  # local file removed


def test_push_all_edited_pending_with_remote_tombstone_still_resurrects(
    engine: SyncEngine,
) -> None:
    """Edit-vs-delete resolves to keep-the-edit: a pending row with LOCAL
    EDITS whose remote twin was deleted must still push (resurrect)."""
    engine.sb.notes["edited-id"] = {
        "id": "edited-id",
        "file_path": "Draft/edited.md",
        "content": "old body",
        "content_hash": content_hash("old body"),
        "label": "edited",
        "folder_name": "Draft",
        "is_deleted": True,
    }
    _seed_pending_file(
        engine,
        "Draft/edited.md",
        "new edited body",
        "edited-id",
        sync_status="pending_push",
        remote_content_hash=content_hash("old body"),
    )
    stats = _run(engine.push_all())
    assert stats["pushed"] == 1
    assert engine.sb.upsert_count() == 1


def test_push_all_stale_pending_with_edited_then_deleted_remote_resurrects(
    engine: SyncEngine,
) -> None:
    """Remote was edited on another device and THEN deleted, and this device
    never pulled the edit: the local file still matches its stale
    remote_content_hash, but the tombstone carries the newer hash. push_all
    must NOT delete the local file — it falls through to push (resurrect),
    matching full_sync's tombstone rule (only delete what the cloud provably
    held when it was deleted)."""
    old_body = "old synced body"
    engine.sb.notes["stale-id"] = {
        "id": "stale-id",
        "file_path": "Draft/stale.md",
        "content": "newer edited body",
        "content_hash": content_hash("newer edited body"),
        "label": "stale",
        "folder_name": "Draft",
        "is_deleted": True,
    }
    _seed_pending_file(
        engine,
        "Draft/stale.md",
        old_body,
        "stale-id",
        sync_status="pending_push",
        remote_content_hash=content_hash(old_body),
    )
    stats = _run(engine.push_all())
    assert stats.get("deleted_local") is None
    assert stats["pushed"] == 1
    assert "Draft/stale.md" in engine.fm.notes  # local file NOT deleted
