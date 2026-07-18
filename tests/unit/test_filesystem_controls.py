from __future__ import annotations

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
