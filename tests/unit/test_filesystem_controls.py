from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.filesystem.index import FilesystemIndex
from app.services.filesystem.models import Place
from app.services.filesystem import service as filesystem_service_module
from app.services.filesystem.service import FilesystemService


def _scan_root(index: FilesystemIndex, root: Path) -> Path:
    source = root / "document.txt"
    source.write_text("private local filesystem content", encoding="utf-8")
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    claim = index.pop_next_directory()
    assert claim is not None
    assert index.index_directory(*claim) == 1
    return source


def test_enrichment_limits_prune_content_vectors_and_old_models(tmp_path: Path) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(index, root)
    stat = source.stat()
    assert index.store_content(
        str(source),
        source.read_text(encoding="utf-8"),
        stat.st_mtime,
        stat.st_size,
        1024 * 1024,
    )
    assert index.store_embedding(
        str(source), "old-model", b"\x00\x00\x00\x00", 1,
        stat.st_mtime, stat.st_size,
    )

    index.apply_enrichment_limits(0, 0, "new-model")

    assert index.enrichment_counts() == {
        "content_entries": 0,
        "embedding_entries": 0,
        "content_bytes": 0,
    }
    with index._connect() as db:
        row = db.execute(
            "SELECT content_state,embedding_state FROM filesystem_entries WHERE path=?",
            (str(source),),
        ).fetchone()
    assert dict(row) == {"content_state": "quota", "embedding_state": "not_indexed"}


def test_clear_derived_removes_rebuildable_state_and_reports_storage(tmp_path: Path) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    _scan_root(index, root)

    assert index.last_scan_at() is not None
    assert index.storage_bytes() > 0

    index.clear_derived()

    assert index.entry_count() == 0
    assert index.queue_count() == 0
    assert index.last_scan_at() is None
    assert not Path(f"{index.path}-wal").exists() or Path(f"{index.path}-wal").stat().st_size == 0


def test_embedding_commit_enforces_current_quota_atomically(tmp_path: Path) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(index, root)
    stat = source.stat()
    assert index.store_content(
        str(source), source.read_text(encoding="utf-8"), stat.st_mtime, stat.st_size, 1024
    )

    assert not index.store_embedding(
        str(source), "model", b"\x00\x00\x00\x00", 1,
        stat.st_mtime, stat.st_size, max_entries=0,
    )
    assert index.enrichment_counts()["embedding_entries"] == 0


@pytest.mark.anyio
async def test_clear_pauses_background_index_but_direct_browsing_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "visible.txt"
    source.write_text("still browsable", encoding="utf-8")
    place = Place("root", "Root", str(root), "configured", 100)
    settings: dict[str, object] = {
        "paused": False,
        "content_enabled": True,
        "semantic_enabled": False,
        "embedding_model": "test-model",
        "max_content_bytes": 16 * 1024 * 1024,
        "max_embedding_entries": 100,
    }
    monkeypatch.setattr(filesystem_service_module, "_indexing_settings", lambda: dict(settings))
    monkeypatch.setattr(filesystem_service_module, "discover_places", lambda: [place])
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service.index.sync_roots([place])
    monkeypatch.setattr(service, "_set_paused", lambda value: settings.__setitem__("paused", value))

    status = await service.control_index("clear")
    page = await service.list_directory(str(root))

    assert status["paused"] is True
    assert status["metadata_state"] == "paused"
    assert status["entries"] == 0
    assert [entry.name for entry in page.entries] == ["visible.txt"]


@pytest.mark.anyio
async def test_pause_fences_watcher_mutation_already_waiting_on_maintenance_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "late.txt"
    source.write_text("late", encoding="utf-8")
    place = Place("root", "Root", str(root), "configured", 100)
    settings = {"paused": False}
    monkeypatch.setattr(filesystem_service_module, "_indexing_settings", lambda: dict(settings))
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service._places = [place]

    await service._maintenance_lock.acquire()
    task = asyncio.create_task(service._apply_watch_path(str(source), deleted=False))
    settings["paused"] = True
    service._maintenance_lock.release()
    await task

    assert service.index.entry_count() == 0


@pytest.mark.anyio
async def test_unavailable_authored_root_keeps_status_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable = tmp_path / "unmounted-archive"
    authored = [{"path": str(unavailable), "label": "Archive drive"}]
    monkeypatch.setattr(filesystem_service_module, "configured_priority_roots", lambda: authored)
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service._places = [
        Place("archive", "Archive drive", str(unavailable), "configured", 130, False, True)
    ]

    status = await service.status()

    assert status["metadata_state"] == "partial"
    assert status["index_complete"] is False
    assert status["unavailable_priority_roots"] == authored


@pytest.mark.anyio
async def test_removed_authored_root_invalidates_cached_available_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    removable = tmp_path / "archive"
    removable.mkdir()
    authored = [{"path": str(removable), "label": "Archive drive"}]
    monkeypatch.setattr(filesystem_service_module, "configured_priority_roots", lambda: authored)
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service._places = [
        Place("archive", "Archive drive", str(removable), "configured", 130, True, True)
    ]
    removable.rmdir()

    status = await service.status()

    assert status["metadata_state"] == "partial"
    assert status["unavailable_priority_roots"] == authored


def test_indexing_settings_fail_closed_when_settings_store_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.cloud_sync import settings_sync

    def fail_settings() -> None:
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(settings_sync, "get_settings_sync", fail_settings)

    settings = filesystem_service_module._indexing_settings()

    assert settings["paused"] is True
    assert settings["content_enabled"] is False
    assert settings["semantic_enabled"] is False


def test_bounded_disk_search_matches_full_path_like_index_search(tmp_path: Path) -> None:
    root = tmp_path / "needle-parent"
    root.mkdir()
    child = root / "plain.txt"
    child.write_text("plain", encoding="utf-8")
    service = FilesystemService(tmp_path / "index.sqlite3")

    results = service._bounded_disk_find(
        "needle-parent", None, 10, 1.0, [str(root)]
    )

    assert [entry.path for entry in results] == [str(child)]


@pytest.mark.anyio
async def test_partial_index_search_merges_index_and_bounded_disk_results(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    indexed = root / "needle-indexed.txt"
    unindexed = root / "needle-unindexed.txt"
    indexed.write_text("indexed", encoding="utf-8")
    unindexed.write_text("unindexed", encoding="utf-8")
    place = Place("root", "Root", str(root), "configured", 100)
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service._places = [place]
    service.index.sync_roots([place])
    service.index.upsert_path(str(indexed), place.id)

    page = await service.find("needle", limit=10)

    assert page.source == "hybrid"
    assert {entry.path for entry in page.entries} == {str(indexed), str(unindexed)}
    assert page.index_complete is False


@pytest.mark.anyio
async def test_partial_index_search_pages_one_merged_result_space(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    paths = [root / f"needle-{index}.txt" for index in range(4)]
    for path in paths:
        path.write_text(path.name, encoding="utf-8")
    place = Place("root", "Root", str(root), "configured", 100)
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service._places = [place]
    service.index.sync_roots([place])
    for path in paths[:3]:
        service.index.upsert_path(str(path), place.id)

    first = await service.find("needle", limit=2)
    assert first.source == "hybrid"
    assert first.next_cursor == "2"
    second = await service.find("needle", cursor=first.next_cursor, limit=2)

    assert second.source == "hybrid"
    assert {entry.path for entry in (*first.entries, *second.entries)} == {
        str(path) for path in paths
    }
