from __future__ import annotations

import os
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.filesystem.index import FilesystemIndex
from app.services.filesystem import index as filesystem_index_module
from app.services.filesystem.models import Place, is_hidden
from app.services.filesystem.roots import normalize_path_key
from app.services.filesystem import service as filesystem_service_module
from app.services.filesystem.service import FilesystemService, _minimal_roots
from app.tools.session import ToolSession
from app.tools.tools import file_ops


@pytest.mark.anyio
async def test_direct_listing_is_paged_before_index_is_warm(tmp_path: Path) -> None:
    root = tmp_path / "browse"
    root.mkdir()
    (root / "folder").mkdir()
    (root / "z.txt").write_text("z", encoding="utf-8")
    (root / "a.txt").write_text("a", encoding="utf-8")
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()

    first = await service.list_directory(str(root), limit=2)
    assert [entry.name for entry in first.entries] == ["folder", "a.txt"]
    assert first.next_cursor == "2"
    second = await service.list_directory(str(root), cursor=first.next_cursor, limit=2)
    assert [entry.name for entry in second.entries] == ["z.txt"]


def test_index_search_content_and_crash_safe_queue_lease(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "filesystem_design.md"
    source.write_text("progressive metadata indexing", encoding="utf-8")
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])

    claim = index.pop_next_directory()
    assert claim is not None
    assert index.pop_next_directory() is None  # leased, not destructively popped
    index.release_directory(claim[0])
    claim = index.pop_next_directory()
    assert claim is not None
    assert index.index_directory(*claim) == 1
    assert [entry.path for entry in index.search("filesystem", limit=10, offset=0)] == [str(source)]

    candidate = index.next_content_candidate((".md",), 1024 * 1024)
    assert candidate == (str(source), source.stat().st_mtime, source.stat().st_size)
    assert index.store_content(
        str(source), source.read_text(encoding="utf-8"), source.stat().st_mtime,
        source.stat().st_size, 1024 * 1024,
    )
    assert index.search_content("metadata")[0]["path"] == str(source)
    source.unlink()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    refreshed = index.pop_next_directory()
    assert refreshed is not None
    index.index_directory(*refreshed)
    assert index.search("filesystem", limit=10, offset=0) == []
    assert index.search_content("metadata") == []


def test_changed_priority_root_drops_old_high_priority_queue(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([Place("priority-old", "Old", str(old), "configured", 110)])
    index.sync_roots([Place("priority-new", "New", str(new), "configured", 110)])
    claim = index.pop_next_directory()
    assert claim is not None
    assert claim[0] == str(new)
    index.complete_directory(claim[0])
    assert index.pop_next_directory() is None


def test_watch_roots_remove_descendants_but_keep_independent_volumes() -> None:
    if os.name == "nt":
        roots = [r"C:\Users\me", r"C:\Users\me\Code", r"D:\Data"]
    else:
        roots = ["/home/me", "/home/me/Code", "/mnt/data"]
    assert _minimal_roots(roots) == [roots[0], roots[2]]


def test_windows_normalization_handles_drive_and_unc_paths() -> None:
    assert normalize_path_key("C:/Users/Me/Code", platform="win32") == normalize_path_key(
        r"c:\users\me\code", platform="win32"
    )
    assert normalize_path_key(r"\\?\C:\Users\Me\Code", platform="win32") == normalize_path_key(
        r"c:\users\me\code", platform="win32"
    )
    assert normalize_path_key(
        r"\\?\UNC\SERVER\Share\Folder\..\File", platform="win32"
    ) == normalize_path_key(r"\\server\share\file", platform="win32")


def test_root_sync_preserves_claim_and_retry_state(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    place = Place("root", "Root", str(root), "configured", 100)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots(iter([place]))
    claim = index.pop_next_directory()
    assert claim is not None
    index.sync_roots(iter([place]))
    assert index.scan_status()["claimed"] == 1
    assert index.pop_next_directory() is None
    assert index.fail_directory(claim[0], PermissionError("denied")) == 30

    index.sync_roots(iter([place]))

    scan = index.scan_status()
    assert scan["ready"] == 0
    assert scan["failed"] == 1
    assert scan["failures"][0]["attempts"] == 1
    assert scan["failures"][0]["consecutive_failures"] == 1
    assert index.pop_next_directory() is None


def test_child_rediscovery_preserves_retry_state(tmp_path: Path) -> None:
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    root_claim = index.pop_next_directory()
    assert root_claim is not None
    index.index_directory(*root_claim)
    child_claim = index.pop_next_directory()
    assert child_claim is not None
    assert child_claim[0] == str(child)
    assert index.fail_directory(child_claim[0], PermissionError("denied")) == 30

    with index._connect() as db:
        db.execute(
            "UPDATE filesystem_scanned_dirs SET scanned_at=0 WHERE path=?", (str(root),)
        )
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    refreshed_root = index.pop_next_directory()
    assert refreshed_root is not None
    assert refreshed_root[0] == str(root)
    index.index_directory(*refreshed_root)

    scan = index.scan_status()
    assert scan["total"] == 1
    assert scan["ready"] == 0
    assert scan["failures"][0]["path"] == str(child)
    assert scan["failures"][0]["consecutive_failures"] == 1


def test_claims_do_not_inflate_failure_backoff_and_success_resets_it(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    place = Place("root", "Root", str(root), "configured", 100)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([place])

    first = index.pop_next_directory()
    assert first is not None
    index.release_directory(first[0])
    second = index.pop_next_directory()
    assert second is not None
    index.release_directory(second[0])
    third = index.pop_next_directory()
    assert third is not None
    assert index.fail_directory(third[0], PermissionError("denied")) == 30
    failure = index.scan_status()["failures"][0]
    assert failure["attempts"] == 3
    assert failure["consecutive_failures"] == 1

    with index._connect() as db:
        db.execute(
            "UPDATE filesystem_scan_queue SET next_retry_at=0 WHERE path=?", (str(root),)
        )
    recovered = index.pop_next_directory()
    assert recovered is not None
    assert index.fail_directory(recovered[0], PermissionError("denied twice")) == 60
    assert index.scan_status()["failures"][0]["consecutive_failures"] == 2
    with index._connect() as db:
        db.execute(
            "UPDATE filesystem_scan_queue SET next_retry_at=0 WHERE path=?", (str(root),)
        )
    recovered = index.pop_next_directory()
    assert recovered is not None
    index.index_directory(*recovered)
    index.sync_roots([place])
    fresh = index.pop_next_directory()
    assert fresh is not None
    assert index.fail_directory(fresh[0], PermissionError("denied again")) == 30
    assert index.scan_status()["failures"][0]["consecutive_failures"] == 1


def test_initialize_recovers_persisted_directory_claim_without_erasing_retry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    db_path = tmp_path / "index.sqlite3"
    index = FilesystemIndex(db_path)
    index.initialize()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    assert index.pop_next_directory() is not None
    assert index.scan_status()["ready"] == 0

    restarted = FilesystemIndex(db_path)
    restarted.initialize()

    assert restarted.scan_status()["ready"] == 1
    assert restarted.pop_next_directory() is not None


def test_scan_status_ready_excludes_live_claim_but_includes_stale_claim(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    assert index.pop_next_directory() is not None
    assert index.scan_status()["ready"] == 0

    with index._connect() as db:
        db.execute(
            "UPDATE filesystem_scan_queue SET claimed_at=? WHERE path=?",
            (filesystem_index_module.time.time() - 301, str(root)),
        )
    stale = index.scan_status()
    assert stale["claimed"] == 0
    assert stale["ready"] == 1


def test_component_safe_delete_handles_wildcards_without_sibling_damage(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = root / "a%_"
    sibling = root / "aZZ"
    target.mkdir(parents=True)
    sibling.mkdir()
    (target / "inside.txt").write_text("inside", encoding="utf-8")
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    claim = index.pop_next_directory()
    assert claim is not None
    index.index_directory(*claim)
    while (claim := index.pop_next_directory()) is not None:
        index.index_directory(*claim)
    index.delete_path(str(target))
    assert index.search("inside", limit=10, offset=0) == []
    assert [entry.name for entry in index.search("keep", limit=10, offset=0)] == ["keep.txt"]
    assert index.search("keep", limit=10, offset=0, root=str(target)) == []
    assert [entry.name for entry in index.search("keep", limit=10, offset=0, root=str(sibling))] == ["keep.txt"]


def test_content_cas_quota_and_semantic_similarity(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    alpha = root / "alpha.txt"
    beta = root / "beta.txt"
    alpha.write_text("alpha concepts", encoding="utf-8")
    beta.write_text("beta concepts", encoding="utf-8")
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    claim = index.pop_next_directory()
    assert claim is not None
    index.index_directory(*claim)

    alpha_stat = alpha.stat()
    assert not index.store_content(
        str(alpha), alpha.read_text(), alpha_stat.st_mtime, alpha_stat.st_size, 2
    )
    assert index.enrichment_counts()["content_bytes"] == 0
    assert index.store_content(
        str(alpha), alpha.read_text(), alpha_stat.st_mtime, alpha_stat.st_size, 1024
    )
    beta_stat = beta.stat()
    assert index.store_content(
        str(beta), beta.read_text(), beta_stat.st_mtime, beta_stat.st_size, 1024
    )
    assert index.store_embedding(
        str(alpha), "test-model", array("f", [1.0, 0.0]).tobytes(), 2,
        alpha_stat.st_mtime, alpha_stat.st_size,
    )
    assert index.store_embedding(
        str(beta), "test-model", array("f", [0.0, 1.0]).tobytes(), 2,
        beta_stat.st_mtime, beta_stat.st_size,
    )
    results = index.semantic_search([0.9, 0.1], "test-model", limit=2)
    assert results[0]["entry"]["name"] == "alpha.txt"
    alpha.write_text("changed after claim", encoding="utf-8")
    index.upsert_path(str(alpha), "root")
    assert not index.store_content(
        str(alpha), "stale", alpha_stat.st_mtime, alpha_stat.st_size, 1024
    )


def test_platform_hidden_attributes() -> None:
    assert is_hidden("normal.txt", SimpleNamespace(st_file_attributes=0x2, st_flags=0))
    assert is_hidden("normal.txt", SimpleNamespace(st_file_attributes=0, st_flags=0x00008000))
    assert not is_hidden("normal.txt", SimpleNamespace(st_file_attributes=0, st_flags=0))


def test_watcher_resolves_containing_canonical_root(tmp_path: Path) -> None:
    service = FilesystemService(tmp_path / "index.sqlite3")
    home = tmp_path / "home"
    code = home / "Code"
    code.mkdir(parents=True)
    service._places = [
        Place("home", "Home", str(home), "home", 100),
        Place("code", "Code", str(code), "configured", 125),
    ]
    assert service._root_id_for_path(str(code / "project" / "file.py")) == "code"


@pytest.mark.anyio
async def test_index_not_complete_while_directory_lease_is_outstanding(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service.index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    assert service.index.pop_next_directory() is not None
    status = await service.status()
    assert status["directories_pending"] == 1
    assert status["index_complete"] is False
    assert status["max_content_bytes"] == 512 * 1024 * 1024
    assert normalize_path_key(r"\\SERVER\Share\Folder\..\File", platform="win32") == normalize_path_key(
        r"\\server\share\file", platform="win32"
    )


@pytest.mark.anyio
async def test_priority_settings_round_trip_independently_of_merged_places(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authored = [{"label": "Code first", "path": str(tmp_path / "Code")}]
    monkeypatch.setattr(
        filesystem_service_module, "configured_priority_roots", lambda: authored
    )
    service = FilesystemService(tmp_path / "index.sqlite3")

    settings = await service.indexing_settings()

    assert settings["priority_roots"] == authored
    assert settings["content_enabled"] is True
    assert settings["semantic_enabled"] is False


@pytest.mark.anyio
async def test_scoped_search_page_preserves_root_for_cursor_followups(tmp_path: Path) -> None:
    root = tmp_path / "scope"
    root.mkdir()
    (root / "match-one.txt").write_text("one", encoding="utf-8")
    (root / "match-two.txt").write_text("two", encoding="utf-8")
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    place = Place("scope", "Scope", str(root), "configured", 100)
    service.index.sync_roots([place])
    claim = service.index.pop_next_directory()
    assert claim is not None
    service.index.index_directory(*claim)

    page = await service.find("match", root=str(root), limit=1)

    assert page.root == str(root.resolve())
    assert page.to_dict()["root"] == str(root.resolve())
    assert page.next_cursor == "1"


@pytest.mark.anyio
async def test_inaccessible_directory_stays_partial_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "protected"
    root.mkdir()
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    place = Place("protected", "Protected", str(root), "configured", 100)
    service.index.sync_roots([place])
    claim = service.index.pop_next_directory()
    assert claim is not None

    real_scandir = filesystem_index_module.os.scandir

    def deny_scan(_path: str) -> os.ScandirIterator[str]:
        raise PermissionError("permission denied by test")

    monkeypatch.setattr(filesystem_index_module.os, "scandir", deny_scan)
    with pytest.raises(PermissionError):
        service.index.index_directory(*claim)
    retry_delay = service.index.fail_directory(claim[0], PermissionError("permission denied by test"))

    assert retry_delay == 30
    scan = service.index.scan_status()
    assert scan["total"] == 1
    assert scan["failed"] == 1
    assert scan["ready"] == 0
    assert scan["failures"][0]["path"] == str(root)
    status = await service.status()
    assert status["metadata_state"] == "partial"
    assert status["index_complete"] is False
    assert status["directories_failed"] == 1

    monkeypatch.setattr(filesystem_index_module.os, "scandir", real_scandir)
    service.index.sync_roots([place])
    assert service.index.pop_next_directory() is None
    with service.index._connect() as db:
        db.execute(
            "UPDATE filesystem_scan_queue SET next_retry_at=0 WHERE path=?", (str(root),)
        )
    retry = service.index.pop_next_directory()
    assert retry is not None
    service.index.index_directory(*retry)
    assert service.index.scan_status()["total"] == 0


@pytest.mark.anyio
async def test_copy_overwrite_failure_preserves_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("new", encoding="utf-8")
    destination.write_text("original", encoding="utf-8")

    def fail_copy(_source: str, _destination: str) -> None:
        raise OSError("simulated copy failure")

    monkeypatch.setattr(file_ops, "_copy_path", fail_copy)
    result = await file_ops.tool_copy(
        ToolSession(working_dir=str(tmp_path)), "source.txt", "destination.txt", overwrite=True
    )
    assert result.type.value == "error"
    assert destination.read_text(encoding="utf-8") == "original"
    assert source.read_text(encoding="utf-8") == "new"
