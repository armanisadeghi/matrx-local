from __future__ import annotations

import os
import sqlite3
import asyncio
import sys
import threading
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.filesystem.index import FilesystemIndex
from app.services.filesystem import index as filesystem_index_module
from app.services.filesystem.models import Place, is_hidden
from app.services.filesystem.paging import DirectoryListSessionRegistry
from app.services.filesystem import paging as filesystem_paging_module
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
    assert first.next_cursor is not None
    assert first.next_cursor != "2"
    second = await service.list_directory(str(root), cursor=first.next_cursor, limit=2)
    assert [entry.name for entry in second.entries] == ["z.txt"]
    assert second.next_cursor is None


@pytest.mark.anyio
async def test_direct_listing_snapshot_is_stable_across_directory_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "browse"
    root.mkdir()
    for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
        (root / name).write_text(name, encoding="utf-8")
    service = FilesystemService(tmp_path / "index.sqlite3")

    first = await service.list_directory(str(root), limit=2)
    assert [entry.name for entry in first.entries] == ["a.txt", "b.txt"]
    assert first.next_cursor is not None

    (root / "c.txt").unlink()
    (root / "aa.txt").write_text("new", encoding="utf-8")
    second = await service.list_directory(
        str(root), cursor=first.next_cursor, limit=2
    )

    assert [entry.name for entry in second.entries] == ["c.txt", "d.txt"]
    assert second.next_cursor is None


@pytest.mark.anyio
async def test_direct_listing_cursor_is_single_use_scoped_and_expiring(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    for name in ("a", "b"):
        (first_root / name).touch()
        (second_root / name).touch()
    now = [100.0]
    service = FilesystemService(tmp_path / "index.sqlite3")
    service._directory_lists = DirectoryListSessionRegistry(clock=lambda: now[0])

    first = await service.list_directory(str(first_root), limit=1)
    assert first.next_cursor is not None
    with pytest.raises(ValueError, match="different directory"):
        await service.list_directory(
            str(second_root), cursor=first.next_cursor, limit=1
        )
    with pytest.raises(ValueError, match="already-used"):
        await service.list_directory(
            str(first_root), cursor=first.next_cursor, limit=1
        )

    expiring = await service.list_directory(str(first_root), limit=1)
    assert expiring.next_cursor is not None
    now[0] += filesystem_paging_module.DIRECTORY_LIST_SESSION_TTL_SECONDS + 1
    with pytest.raises(ValueError, match="expired"):
        await service.list_directory(
            str(first_root), cursor=expiring.next_cursor, limit=1
        )


@pytest.mark.anyio
async def test_direct_listing_progress_pages_have_bounded_work_and_honest_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "large"
    root.mkdir()
    for index in range(7):
        (root / f"item-{index}.txt").touch()
    monkeypatch.setattr(filesystem_paging_module, "DIRECTORY_PAGE_SCAN_BUDGET", 3)
    service = FilesystemService(tmp_path / "index.sqlite3")

    first = await service.list_directory(str(root), limit=2)

    assert first.entries == ()
    assert first.total == 3
    assert first.next_cursor is not None
    second = await service.list_directory(
        str(root), cursor=first.next_cursor, limit=2
    )
    assert second.entries == ()
    assert second.total == 6
    assert second.next_cursor is not None
    third = await service.list_directory(
        str(root), cursor=second.next_cursor, limit=2
    )
    assert [entry.name for entry in third.entries] == ["item-0.txt", "item-1.txt"]
    assert third.total == 7


@pytest.mark.anyio
async def test_direct_listing_enforces_snapshot_entry_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "too-large"
    root.mkdir()
    for index in range(3):
        (root / str(index)).touch()
    monkeypatch.setattr(filesystem_paging_module, "MAX_DIRECTORY_SNAPSHOT_ENTRIES", 2)
    service = FilesystemService(tmp_path / "index.sqlite3")

    with pytest.raises(ValueError, match="more than 2 visible entries"):
        await service.list_directory(str(root))


@pytest.mark.anyio
async def test_direct_listing_caps_concurrent_first_page_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "browse"
    root.mkdir()
    (root / "entry").touch()
    entered = threading.Event()
    release = threading.Event()
    original_page = filesystem_paging_module._DirectorySnapshot.page

    def blocked_page(self, limit: int, *, scan_budget: int):  # type: ignore[no-untyped-def]
        entered.set()
        assert release.wait(2)
        return original_page(self, limit, scan_budget=scan_budget)

    monkeypatch.setattr(filesystem_paging_module, "MAX_DIRECTORY_LIST_SESSIONS", 1)
    monkeypatch.setattr(
        filesystem_paging_module._DirectorySnapshot, "page", blocked_page
    )
    service = FilesystemService(tmp_path / "index.sqlite3")
    first = asyncio.create_task(service.list_directory(str(root)))
    assert await asyncio.to_thread(entered.wait, 1)

    with pytest.raises(ValueError, match="Too many concurrent"):
        await service.list_directory(str(root))

    release.set()
    assert (await first).entries[0].name == "entry"


@pytest.mark.anyio
async def test_direct_listing_global_entry_reservations_include_active_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "browse"
    root.mkdir()
    for index in range(3):
        (root / str(index)).touch()
    entered = threading.Event()
    release = threading.Event()
    original_page = filesystem_paging_module._DirectorySnapshot.page
    first_call = [True]

    def blocked_first(self, limit: int, *, scan_budget: int):  # type: ignore[no-untyped-def]
        if first_call[0]:
            first_call[0] = False
            entered.set()
            assert release.wait(2)
        return original_page(self, limit, scan_budget=scan_budget)

    monkeypatch.setattr(filesystem_paging_module, "DIRECTORY_PAGE_SCAN_BUDGET", 2)
    monkeypatch.setattr(filesystem_paging_module, "MAX_DIRECTORY_LIST_TOTAL_ENTRIES", 2)
    monkeypatch.setattr(
        filesystem_paging_module._DirectorySnapshot, "page", blocked_first
    )
    service = FilesystemService(tmp_path / "index.sqlite3")
    first = asyncio.create_task(service.list_directory(str(root)))
    assert await asyncio.to_thread(entered.wait, 1)

    with pytest.raises(ValueError, match="service-wide 2-entry limit"):
        await service.list_directory(str(root))

    release.set()
    progress = await first
    assert progress.entries == ()
    assert progress.total == 2


@pytest.mark.anyio
async def test_direct_listing_stop_and_reaper_delete_abandoned_snapshots(
    tmp_path: Path,
) -> None:
    root = tmp_path / "browse"
    root.mkdir()
    (root / "a").touch()
    (root / "b").touch()
    now = [100.0]
    service = FilesystemService(tmp_path / "index.sqlite3")
    registry = DirectoryListSessionRegistry(clock=lambda: now[0])
    service._directory_lists = registry

    first = await service.list_directory(str(root), limit=1)
    assert first.next_cursor is not None
    session = next(iter(registry._sessions.values()))
    first_snapshot = session.snapshot.snapshot_path.parent
    assert first_snapshot.exists()
    now[0] += filesystem_paging_module.DIRECTORY_LIST_SESSION_TTL_SECONDS + 1
    await asyncio.to_thread(registry.reap_expired)
    assert registry.session_count == 0
    assert not first_snapshot.exists()

    second = await service.list_directory(str(root), limit=1)
    assert second.next_cursor is not None
    second_snapshot = next(iter(registry._sessions.values())).snapshot.snapshot_path.parent
    await service.stop()
    assert registry.session_count == 0
    assert not second_snapshot.exists()
    with pytest.raises(ValueError, match="stopping"):
        await service.list_directory(str(root))


@pytest.mark.anyio
async def test_direct_listing_does_not_follow_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "nested.txt").touch()
    root = tmp_path / "browse"
    root.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    service = FilesystemService(tmp_path / "index.sqlite3")

    page = await service.list_directory(str(root))

    assert [(entry.name, entry.kind) for entry in page.entries] == [
        ("linked", "symlink")
    ]
    assert all(entry.name != "nested.txt" for entry in page.entries)


@pytest.mark.anyio
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Invalid UTF-8 filename bytes are supported on Linux filesystems",
)
async def test_direct_listing_round_trips_opaque_posix_filename_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "browse"
    root.mkdir()
    raw_name = b"opaque-\xff.txt"
    descriptor = os.open(
        os.fsencode(root) + os.sep.encode() + raw_name,
        os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    os.close(descriptor)
    service = FilesystemService(tmp_path / "index.sqlite3")

    page = await service.list_directory(str(root))

    assert len(page.entries) == 1
    assert os.fsencode(page.entries[0].name) == raw_name
    assert page.to_dict()["entries"][0]["name"] == page.entries[0].name


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
    assert index.release_directory(claim[0], claim[4])
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
    assert index.complete_directory(claim[0], claim[4])
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
    assert index.fail_directory(claim[0], PermissionError("denied"), claim[4]) == 30

    index.sync_roots(iter([place]))

    scan = index.scan_status()
    assert scan["ready"] == 0
    assert scan["failed"] == 1
    assert scan["failures"][0]["attempts"] == 1
    assert scan["failures"][0]["consecutive_failures"] == 1
    assert index.pop_next_directory() is None


def test_root_sync_replaces_stale_path_for_stable_id(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "stale.txt").write_text("stale", encoding="utf-8")
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([Place("stable", "Stable", str(old), "configured", 130)])
    old_claim = index.pop_next_directory()
    assert old_claim is not None

    index.sync_roots([Place("stable", "Stable", str(new), "configured", 100)])

    assert index.index_directory(*old_claim) == 0
    assert (
        index.fail_directory(
            old_claim[0], PermissionError("obsolete"), old_claim[4]
        )
        is None
    )
    replacement = index.pop_next_directory()
    assert replacement is not None
    assert replacement[0] == str(new)
    assert index.pop_next_directory() is None
    assert index.search("stale", limit=10, offset=0) == []


def test_root_priority_demotion_recomputes_queue_without_erasing_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([Place("stable", "Stable", str(root), "configured", 130)])
    root_claim = index.pop_next_directory()
    assert root_claim is not None
    index.index_directory(*root_claim)
    child_claim = index.pop_next_directory()
    assert child_claim is not None
    assert child_claim[0] == str(child)
    assert (
        index.fail_directory(child_claim[0], PermissionError("denied"), child_claim[4])
        == 30
    )

    index.sync_roots([Place("stable", "Stable", str(root), "configured", 10)])

    with index._connect() as db:
        child_row = db.execute(
            """SELECT priority,consecutive_failures,last_error,next_retry_at
               FROM filesystem_scan_queue WHERE path=?""",
            (str(child),),
        ).fetchone()
        root_row = db.execute(
            "SELECT priority FROM filesystem_scan_queue WHERE path=?", (str(root),)
        ).fetchone()
    assert child_row is not None
    assert child_row["priority"] == 9_999
    assert child_row["consecutive_failures"] == 1
    assert child_row["last_error"] == "denied"
    assert child_row["next_retry_at"] is not None
    assert root_row is not None
    assert root_row["priority"] == 10_000


def test_root_sync_preserves_fair_scheduler_history(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    place = Place("stable", "Stable", str(root), "configured", 100)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([place])
    claim = index.pop_next_directory(fair=True)
    assert claim is not None
    with index._connect() as db:
        before = db.execute(
            "SELECT last_claimed_at FROM filesystem_roots WHERE id='stable'"
        ).fetchone()["last_claimed_at"]

    index.sync_roots([place])

    with index._connect() as db:
        after = db.execute(
            "SELECT last_claimed_at FROM filesystem_roots WHERE id='stable'"
        ).fetchone()["last_claimed_at"]
    assert after == before
    assert index.release_directory(claim[0], claim[4])


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
    assert (
        index.fail_directory(
            child_claim[0], PermissionError("denied"), child_claim[4]
        )
        == 30
    )

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
    assert index.release_directory(first[0], first[4])
    second = index.pop_next_directory()
    assert second is not None
    assert index.release_directory(second[0], second[4])
    third = index.pop_next_directory()
    assert third is not None
    assert index.fail_directory(third[0], PermissionError("denied"), third[4]) == 30
    failure = index.scan_status()["failures"][0]
    assert failure["attempts"] == 3
    assert failure["consecutive_failures"] == 1

    with index._connect() as db:
        db.execute(
            "UPDATE filesystem_scan_queue SET next_retry_at=0 WHERE path=?", (str(root),)
        )
    recovered = index.pop_next_directory()
    assert recovered is not None
    assert (
        index.fail_directory(
            recovered[0], PermissionError("denied twice"), recovered[4]
        )
        == 60
    )
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
    assert (
        index.fail_directory(fresh[0], PermissionError("denied again"), fresh[4])
        == 30
    )
    assert index.scan_status()["failures"][0]["consecutive_failures"] == 1


def test_second_live_initializer_does_not_steal_directory_claim(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    db_path = tmp_path / "index.sqlite3"
    index = FilesystemIndex(db_path)
    index.initialize()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    claim = index.pop_next_directory()
    assert claim is not None
    assert index.scan_status()["ready"] == 0

    second = FilesystemIndex(db_path)
    second.initialize()

    assert second.scan_status()["ready"] == 0
    assert second.pop_next_directory() is None
    assert index.release_directory(claim[0], claim[4])


def test_initialize_recovers_dead_process_claim_immediately(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    db_path = tmp_path / "index.sqlite3"
    index = FilesystemIndex(db_path)
    index.initialize()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    original = index.pop_next_directory()
    assert original is not None
    with index._connect() as db:
        db.execute(
            """UPDATE filesystem_scan_queue
               SET claim_pid=?,claim_process_started_at=? WHERE path=?""",
            (999_999_999, 1.0, str(root)),
        )

    restarted = FilesystemIndex(db_path)
    restarted.initialize()

    assert restarted.scan_status()["ready"] == 1
    recovered = restarted.pop_next_directory()
    assert recovered is not None
    assert recovered[4] != original[4]


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
async def test_prepare_open_hydrates_pointer_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "cloud-file.txt"
    target.touch()
    service = FilesystemService(tmp_path / "index.sqlite3")
    hydrated: list[str] = []

    async def hydrate(path: str) -> None:
        hydrated.append(path)
        return None

    monkeypatch.setattr("app.services.file_sync.hydration.ensure_hydrated", hydrate)

    assert await service.prepare_open(str(target)) == {
        "path": str(target),
        "ready": True,
    }
    assert hydrated == [str(target)]


@pytest.mark.anyio
async def test_prepare_open_returns_actionable_hydration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "offline-pointer.txt"
    target.touch()
    service = FilesystemService(tmp_path / "index.sqlite3")

    async def fail_hydration(_path: str) -> str:
        return "Sign in and reconnect before opening this cloud-backed file."

    monkeypatch.setattr("app.services.file_sync.hydration.ensure_hydrated", fail_hydration)

    result = await service.prepare_open(str(target))
    assert result["ready"] is False
    assert "Sign in" in str(result["error"])


class _FakeScandir:
    def __init__(self, entries: list[object]) -> None:
        self._entries = entries

    def __enter__(self) -> "_FakeScandir":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._entries)


class _FailingDirEntry:
    def __init__(self, path: Path, error: OSError) -> None:
        self.path = str(path)
        self.name = path.name
        self._error = error

    def stat(self, *, follow_symlinks: bool = False):  # type: ignore[no-untyped-def]
        del follow_symlinks
        raise self._error


class _StaticDirEntry:
    def __init__(self, path: Path, info: os.stat_result) -> None:
        self.path = str(path)
        self.name = path.name
        self._info = info

    def stat(self, *, follow_symlinks: bool = False) -> os.stat_result:
        del follow_symlinks
        return self._info


def test_entry_stat_permission_error_preserves_index_and_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "visible.txt"
    target.write_text("visible", encoding="utf-8")
    place = Place("root", "Root", str(root), "configured", 100)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([place])
    initial = index.pop_next_directory()
    assert initial is not None
    index.index_directory(*initial)
    index.sync_roots([place])
    retry = index.pop_next_directory()
    assert retry is not None
    monkeypatch.setattr(
        filesystem_index_module.os,
        "scandir",
        lambda _path: _FakeScandir(
            [_FailingDirEntry(target, PermissionError("transient denial"))]
        ),
    )

    with pytest.raises(PermissionError, match="transient denial"):
        index.index_directory(*retry)

    assert [
        entry.path for entry in index.search("visible", limit=10, offset=0)
    ] == [str(target)]
    assert index.scan_status()["claimed"] == 1
    assert (
        index.fail_directory(
            retry[0], PermissionError("transient denial"), retry[4]
        )
        == 30
    )


def test_entry_disappearing_during_stat_is_a_successful_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "gone.txt"
    target.write_text("gone", encoding="utf-8")
    place = Place("root", "Root", str(root), "configured", 100)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([place])
    initial = index.pop_next_directory()
    assert initial is not None
    index.index_directory(*initial)
    index.sync_roots([place])
    retry = index.pop_next_directory()
    assert retry is not None
    monkeypatch.setattr(
        filesystem_index_module.os,
        "scandir",
        lambda _path: _FakeScandir(
            [_FailingDirEntry(target, FileNotFoundError("concurrent deletion"))]
        ),
    )

    assert index.index_directory(*retry) == 0
    assert index.search("gone", limit=10, offset=0) == []
    assert index.scan_status()["total"] == 0


def test_directory_snapshot_does_not_delete_newer_watcher_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "raced.txt"
    target.write_text("raced", encoding="utf-8")
    place = Place("root", "Root", str(root), "configured", 100)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([place])
    initial = index.pop_next_directory()
    assert initial is not None
    index.index_directory(*initial)
    index.sync_roots([place])
    retry = index.pop_next_directory()
    assert retry is not None
    snapshot_started = filesystem_index_module.time.time()
    with index._connect() as db:
        db.execute(
            "UPDATE filesystem_entries SET indexed_at=? WHERE path=?",
            (snapshot_started + 10, str(target)),
        )
    monkeypatch.setattr(
        filesystem_index_module.os,
        "scandir",
        lambda _path: _FakeScandir([]),
    )
    monkeypatch.setattr(
        filesystem_index_module.time, "time", lambda: snapshot_started
    )

    assert index.index_directory(*retry) == 0
    assert [entry.path for entry in index.search("raced", limit=10, offset=0)] == [
        str(target)
    ]


def test_directory_snapshot_does_not_resurrect_newer_watcher_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "deleted.txt"
    target.write_text("deleted", encoding="utf-8")
    stale_info = target.stat()
    place = Place("root", "Root", str(root), "configured", 100)
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    index.sync_roots([place])
    initial = index.pop_next_directory()
    assert initial is not None
    index.index_directory(*initial)
    index.sync_roots([place])
    retry = index.pop_next_directory()
    assert retry is not None
    target.unlink()
    monkeypatch.setattr(filesystem_index_module.time, "time", lambda: 200.0)
    index.delete_path(str(target))
    monkeypatch.setattr(filesystem_index_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        filesystem_index_module.os,
        "scandir",
        lambda _path: _FakeScandir([_StaticDirEntry(target, stale_info)]),
    )

    assert index.index_directory(*retry) == 0
    assert index.search("deleted", limit=10, offset=0) == []
    with index._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM filesystem_path_mutations"
        ).fetchone()[0] == 1

    monkeypatch.setattr(filesystem_index_module.time, "time", lambda: 300.0)
    monkeypatch.setattr(
        filesystem_index_module.os, "scandir", lambda _path: _FakeScandir([])
    )
    index.sync_roots([place])
    after_deletion = index.pop_next_directory()
    assert after_deletion is not None
    assert index.index_directory(*after_deletion) == 0
    with index._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM filesystem_path_mutations"
        ).fetchone()[0] == 0


def test_initialize_migrates_pre_parent_mutation_table_safely(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE filesystem_path_mutations (
                   path_key TEXT PRIMARY KEY,
                   observed_at REAL NOT NULL
               )"""
        )
        db.execute(
            "INSERT INTO filesystem_path_mutations(path_key,observed_at) VALUES(?,?)",
            ("obsolete", 1.0),
        )

    index = FilesystemIndex(db_path)
    index.initialize()

    with index._connect() as db:
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(filesystem_path_mutations)")
        }
        indexes = {
            row["name"]
            for row in db.execute("PRAGMA index_list(filesystem_path_mutations)")
        }
        count = db.execute(
            "SELECT COUNT(*) FROM filesystem_path_mutations"
        ).fetchone()[0]
    assert "parent_key" in columns
    assert "idx_filesystem_path_mutations_parent" in indexes
    assert count == 0


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
    retry_delay = service.index.fail_directory(
        claim[0], PermissionError("permission denied by test"), claim[4]
    )

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
