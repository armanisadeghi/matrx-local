from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from app.services.filesystem.index import FilesystemIndex
from app.services.filesystem.models import Place, SearchPage
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


def _enrichment_settings(*, content: bool, semantic: bool) -> dict[str, object]:
    return {
        "paused": False,
        "content_enabled": content,
        "semantic_enabled": semantic,
        "embedding_model": "test-model",
        "max_content_bytes": 16 * 1024 * 1024,
        "max_embedding_entries": 100,
    }


def _make_enrichment_service(tmp_path: Path) -> tuple[FilesystemService, Path]:
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(service.index, root)
    return service, source


@pytest.mark.anyio
async def test_enrichment_retries_contended_content_commit_and_claim_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, source = _make_enrichment_service(tmp_path)
    settings = _enrichment_settings(content=True, semantic=False)
    monkeypatch.setattr(
        filesystem_service_module, "_indexing_settings", lambda: dict(settings)
    )
    monkeypatch.setattr(filesystem_service_module, "_background_capacity", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        filesystem_service_module, "_ENRICHMENT_CONTENTION_BACKOFF_SECONDS", 0.0
    )
    original_store = service.index.store_content
    original_reset = service.index.reset_enrichment_candidate
    store_attempts = 0
    reset_attempts = 0

    def flaky_store(*args: object, **kwargs: object) -> bool:
        nonlocal store_attempts
        store_attempts += 1
        if store_attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        stored = original_store(*args, **kwargs)
        service._stop.set()
        return stored

    def flaky_reset(*args: object, **kwargs: object) -> bool:
        nonlocal reset_attempts
        reset_attempts += 1
        if reset_attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_reset(*args, **kwargs)

    monkeypatch.setattr(service.index, "store_content", flaky_store)
    monkeypatch.setattr(service.index, "reset_enrichment_candidate", flaky_reset)

    await asyncio.wait_for(service._enrichment_loop(), timeout=2.0)

    assert store_attempts == 2
    assert reset_attempts == 2
    assert service.index.search_content("private", limit=10)[0]["path"] == str(source)


@pytest.mark.anyio
async def test_enrichment_retries_contended_failure_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, source = _make_enrichment_service(tmp_path)
    source.unlink()
    settings = _enrichment_settings(content=True, semantic=False)
    monkeypatch.setattr(
        filesystem_service_module, "_indexing_settings", lambda: dict(settings)
    )
    monkeypatch.setattr(filesystem_service_module, "_background_capacity", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        filesystem_service_module, "_ENRICHMENT_CONTENTION_BACKOFF_SECONDS", 0.0
    )
    original_finish = service.index.finish_enrichment_failure
    original_reset = service.index.reset_enrichment_candidate
    finish_attempts = 0
    reset_attempts = 0

    def flaky_finish(*args: object, **kwargs: object) -> bool:
        nonlocal finish_attempts
        finish_attempts += 1
        if finish_attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        finished = original_finish(*args, **kwargs)
        service._stop.set()
        return finished

    def counting_reset(*args: object, **kwargs: object) -> bool:
        nonlocal reset_attempts
        reset_attempts += 1
        return original_reset(*args, **kwargs)

    monkeypatch.setattr(service.index, "finish_enrichment_failure", flaky_finish)
    monkeypatch.setattr(service.index, "reset_enrichment_candidate", counting_reset)

    await asyncio.wait_for(service._enrichment_loop(), timeout=2.0)

    assert finish_attempts == 2
    assert reset_attempts == 1


@pytest.mark.anyio
async def test_enrichment_retries_contended_embedding_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, source = _make_enrichment_service(tmp_path)
    source_stat = source.stat()
    assert service.index.store_content(
        str(source),
        source.read_text(encoding="utf-8"),
        source_stat.st_mtime,
        source_stat.st_size,
        16 * 1024 * 1024,
    )
    settings = _enrichment_settings(content=False, semantic=True)
    monkeypatch.setattr(
        filesystem_service_module, "_indexing_settings", lambda: dict(settings)
    )
    monkeypatch.setattr(filesystem_service_module, "_background_capacity", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(filesystem_service_module.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        filesystem_service_module, "_ENRICHMENT_CONTENTION_BACKOFF_SECONDS", 0.0
    )
    from app.api import openai_compat_routes

    async def embed(_texts: tuple[str, ...], _model: str) -> list[list[float]]:
        return [[1.0, 0.0]]

    monkeypatch.setattr(openai_compat_routes, "_embed_texts", embed)
    original_store = service.index.store_embedding
    store_attempts = 0

    def flaky_store(*args: object, **kwargs: object) -> bool:
        nonlocal store_attempts
        store_attempts += 1
        if store_attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        stored = original_store(*args, **kwargs)
        service._stop.set()
        return stored

    monkeypatch.setattr(service.index, "store_embedding", flaky_store)

    await asyncio.wait_for(service._enrichment_loop(), timeout=2.0)

    assert store_attempts == 2
    assert (
        service.index.semantic_search([1.0, 0.0], "test-model", limit=10)[0][
            "entry"
        ]["path"]
        == str(source)
    )


@pytest.mark.anyio
async def test_background_task_failure_is_logged_and_removed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service = FilesystemService(tmp_path / "index.sqlite3")
    service._started = True

    async def fail() -> None:
        raise RuntimeError("background boom")

    with caplog.at_level(logging.ERROR, logger=filesystem_service_module.__name__):
        service._spawn(fail(), "filesystem-test-failure")
        task = next(iter(service._tasks))
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    assert not service._tasks
    assert "filesystem-test-failure exited unexpectedly" in caplog.text
    assert "background boom" in caplog.text


@pytest.mark.anyio
async def test_unexpected_enrichment_cancellation_queues_claim_and_restarts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = FilesystemService(tmp_path / "index.sqlite3")
    service._started = True
    first_worker_started = asyncio.Event()
    replacement_started = asyncio.Event()
    calls = 0
    claim = (str(tmp_path / "claimed.txt"), "content", 10.0, 20)

    async def controlled_worker() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            service._active_enrichment_claim = claim
            first_worker_started.set()
            await asyncio.Event().wait()
        replacement_started.set()
        service._stop.set()

    monkeypatch.setattr(service, "_enrichment_worker_loop", controlled_worker)
    with caplog.at_level(logging.ERROR, logger=filesystem_service_module.__name__):
        service._spawn(service._enrichment_loop(), "filesystem-enrichment")
        await first_worker_started.wait()
        original = next(iter(service._tasks))
        original.cancel()
        await asyncio.gather(original, return_exceptions=True)
        await asyncio.wait_for(replacement_started.wait(), timeout=1.0)
        await asyncio.sleep(0)

    assert service._active_enrichment_claim is None
    assert list(service._pending_enrichment_resets) == [claim]
    assert calls == 2
    assert "filesystem-enrichment was cancelled unexpectedly" in caplog.text


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
        "content_failures": 0,
        "embedding_failures": 0,
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
    wal_path = Path(f"{index.path}-wal")
    try:
        wal_size = wal_path.stat().st_size
    except FileNotFoundError:
        wal_size = 0
    assert wal_size == 0


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


def test_transient_content_failure_retries_after_backoff_and_resets_on_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(index, root)
    stat = source.stat()
    candidate = index.next_content_candidate((".txt",), 1024 * 1024)
    assert candidate == (str(source), stat.st_mtime, stat.st_size)
    now = [1_000.0]
    monkeypatch.setattr("app.services.filesystem.index.time.time", lambda: now[0])

    assert index.finish_enrichment_failure(
        str(source), "content", "temporarily locked", stat.st_mtime, stat.st_size
    )
    assert index.next_content_candidate((".txt",), 1024 * 1024) is None
    counts = index.enrichment_counts()
    assert counts["content_failures"] == 1

    now[0] += 5.1
    assert index.next_content_candidate((".txt",), 1024 * 1024) == (
        str(source), stat.st_mtime, stat.st_size
    )
    assert index.finish_enrichment_failure(
        str(source), "content", "still locked", stat.st_mtime, stat.st_size
    )
    now[0] += 5.1
    assert index.next_content_candidate((".txt",), 1024 * 1024) is None

    source.write_text("changed source", encoding="utf-8")
    index.upsert_path(str(source), "root")
    changed = source.stat()
    assert index.next_content_candidate((".txt",), 1024 * 1024) == (
        str(source), changed.st_mtime, changed.st_size
    )


def test_stale_enrichment_failure_cannot_poison_rebuilt_row(tmp_path: Path) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(index, root)
    stat = source.stat()
    assert index.next_content_candidate((".txt",), 1024 * 1024) is not None

    index.clear_derived()
    source.write_text("new generation", encoding="utf-8")
    fresh = source.stat()
    index.sync_roots([Place("root", "Root", str(root), "configured", 100)])
    index.upsert_path(str(source), "root")

    assert not index.finish_enrichment_failure(
        str(source), "content", "stale read", stat.st_mtime, stat.st_size
    )
    with index._connect() as db:
        row = db.execute(
            "SELECT content_state FROM filesystem_entries WHERE path=?", (str(source),)
        ).fetchone()
    assert fresh.st_mtime != stat.st_mtime or fresh.st_size != stat.st_size
    assert row["content_state"] == "not_indexed"


def test_transient_embedding_failure_retries_and_model_change_bypasses_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(index, root)
    stat = source.stat()
    assert index.store_content(
        str(source), source.read_text(), stat.st_mtime, stat.st_size, 1024 * 1024
    )
    candidate = index.next_embedding_candidate("model-a", 100)
    assert candidate is not None
    now = [2_000.0]
    monkeypatch.setattr("app.services.filesystem.index.time.time", lambda: now[0])

    assert index.finish_enrichment_failure(
        str(source),
        "embedding",
        "provider unavailable",
        stat.st_mtime,
        stat.st_size,
        model="model-a",
    )
    assert index.next_embedding_candidate("model-a", 100) is None
    assert index.enrichment_counts()["embedding_failures"] == 1
    assert index.next_embedding_candidate("model-b", 100) is not None


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
    assert first.next_cursor is not None
    assert not first.next_cursor.isdigit()
    second = await service.find("needle", cursor=first.next_cursor, limit=2)

    assert second.source == "hybrid"
    assert {entry.path for entry in (*first.entries, *second.entries)} == {
        str(path) for path in paths
    }


@pytest.mark.anyio
async def test_paused_complete_index_search_merges_new_disk_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    old = root / "needle-old.txt"
    old.write_text("old", encoding="utf-8")
    place = Place("root", "Root", str(root), "configured", 100)
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service._places = [place]
    service.index.sync_roots([place])
    claim = service.index.pop_next_directory()
    assert claim is not None
    service.index.index_directory(*claim)
    new = root / "needle-new.txt"
    new.write_text("new", encoding="utf-8")
    monkeypatch.setattr(
        filesystem_service_module,
        "_indexing_settings",
        lambda: {
            "paused": True,
            "content_enabled": True,
            "semantic_enabled": False,
            "embedding_model": "test",
            "max_content_bytes": 16 * 1024 * 1024,
            "max_embedding_entries": 100,
        },
    )

    page = await service.find("needle", limit=10)

    assert page.source == "hybrid"
    assert page.index_complete is False
    assert {entry.path for entry in page.entries} == {str(old), str(new)}


@pytest.mark.anyio
async def test_hybrid_search_cursor_pages_one_immutable_snapshot(
    tmp_path: Path
) -> None:
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
    service.index.upsert_path(str(paths[0]), place.id)

    first = await service.find("needle", limit=2)
    assert first.next_cursor is not None
    paths[2].unlink()
    (root / "needle-later.txt").write_text("later", encoding="utf-8")
    second = await service.find("needle", cursor=first.next_cursor, limit=2)

    assert second.next_cursor is None
    assert {entry.path for entry in (*first.entries, *second.entries)} == {
        str(path) for path in paths
    }


@pytest.mark.anyio
async def test_search_build_admission_is_bounded_and_cancellation_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = FilesystemService(tmp_path / "index.sqlite3")
    entered: asyncio.Queue[str] = asyncio.Queue()
    blockers = {
        query: asyncio.Event()
        for query in ("one", "two", "waiting", "three", "four", "five")
    }
    active = 0
    peak_active = 0

    async def controlled_build(
        clean: str, **_kwargs: object
    ) -> SearchPage:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await entered.put(clean)
        try:
            await blockers[clean].wait()
        finally:
            active -= 1
        return SearchPage(clean, (), None)

    monkeypatch.setattr(service, "_materialize_first_search_page", controlled_build)
    first = asyncio.create_task(service.find("one"))
    second = asyncio.create_task(service.find("two"))
    assert {await entered.get(), await entered.get()} == {"one", "two"}

    cancelled_waiter = asyncio.create_task(service.find("waiting"))
    await asyncio.sleep(0)
    assert entered.empty()
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    replacement = asyncio.create_task(service.find("three"))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.sleep(0)
    assert entered.empty()

    blockers["one"].set()
    assert await asyncio.wait_for(entered.get(), timeout=1.0) == "three"
    blockers["two"].set()
    blockers["three"].set()
    await asyncio.gather(second, replacement)

    fourth = asyncio.create_task(service.find("four"))
    fifth = asyncio.create_task(service.find("five"))
    assert {await entered.get(), await entered.get()} == {"four", "five"}
    blockers["four"].set()
    blockers["five"].set()
    await asyncio.gather(fourth, fifth)

    assert peak_active == filesystem_service_module._MAX_CONCURRENT_SEARCH_BUILDS
    assert active == 0


@pytest.mark.anyio
async def test_search_admission_owns_timed_out_threads_until_stop_drains_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = FilesystemService(tmp_path / "index.sqlite3")
    service._started = True
    started = {query: threading.Event() for query in ("one", "two", "three")}
    release = {query: threading.Event() for query in ("one", "two", "three")}
    active = 0
    peak_active = 0
    counter_lock = threading.Lock()

    def blocking_worker(query: str) -> None:
        nonlocal active, peak_active
        with counter_lock:
            active += 1
            peak_active = max(peak_active, active)
        started[query].set()
        release[query].wait(timeout=2.0)
        with counter_lock:
            active -= 1

    async def timed_out_build(
        clean: str,
        *,
        workers: set[asyncio.Task[object]],
        **_kwargs: object,
    ) -> SearchPage:
        await service._run_search_worker(
            workers, blocking_worker, clean, timeout=0.01
        )
        raise AssertionError("blocking worker should have timed out")

    monkeypatch.setattr(service, "_materialize_first_search_page", timed_out_build)
    first = asyncio.create_task(service.find("one"))
    second = asyncio.create_task(service.find("two"))
    while not (started["one"].is_set() and started["two"].is_set()):
        await asyncio.sleep(0)
    assert isinstance((await asyncio.gather(first, return_exceptions=True))[0], TimeoutError)
    assert isinstance((await asyncio.gather(second, return_exceptions=True))[0], TimeoutError)

    third = asyncio.create_task(service.find("three"))
    await asyncio.sleep(0.02)
    assert not started["three"].is_set()
    assert peak_active == filesystem_service_module._MAX_CONCURRENT_SEARCH_BUILDS

    stop = asyncio.create_task(service.stop())
    await asyncio.sleep(0)
    assert not stop.done()
    release["one"].set()
    third_result = await asyncio.gather(third, return_exceptions=True)
    assert isinstance(third_result[0], ValueError)
    assert not started["three"].is_set()
    assert not stop.done()

    release["two"].set()
    await asyncio.wait_for(stop, timeout=1.0)

    assert peak_active == filesystem_service_module._MAX_CONCURRENT_SEARCH_BUILDS
    assert active == 0
    assert not service._search_cleanup_tasks


@pytest.mark.anyio
async def test_bounded_disk_search_reports_incomplete_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    service = FilesystemService(tmp_path / "index.sqlite3")
    service.index.initialize()
    service._places = [Place("root", "Root", str(root), "configured", 100)]
    monkeypatch.setattr(
        filesystem_service_module,
        "_indexing_settings",
        lambda: {
            "paused": True,
            "content_enabled": True,
            "semantic_enabled": False,
            "embedding_model": "test",
            "max_content_bytes": 16 * 1024 * 1024,
            "max_embedding_entries": 100,
        },
    )
    monkeypatch.setattr(
        service,
        "_bounded_disk_find_snapshot",
        lambda *_args, **_kwargs: ([], True),
    )

    page = await service.find("beyond-budget", limit=10)

    assert page.entries == ()
    assert page.source == "hybrid"
    assert page.truncated is True


def test_same_mtime_size_change_invalidates_content_and_embedding(tmp_path: Path) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(index, root)
    original = source.stat()
    assert index.store_content(
        str(source), source.read_text(), original.st_mtime, original.st_size, 1024 * 1024
    )
    assert index.store_embedding(
        str(source),
        "model",
        b"\x00\x00\x00\x00",
        1,
        original.st_mtime,
        original.st_size,
    )
    source.write_text("different length", encoding="utf-8")
    source.touch()
    os.utime(source, (original.st_atime, original.st_mtime))
    index.upsert_path(str(source), "root")

    with index._connect() as db:
        row = db.execute(
            "SELECT content_state,embedding_state FROM filesystem_entries WHERE path=?",
            (str(source),),
        ).fetchone()
    assert dict(row) == {
        "content_state": "not_indexed",
        "embedding_state": "not_indexed",
    }


def test_stale_enrichment_success_cannot_release_newer_claim(tmp_path: Path) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(index, root)
    old = source.stat()
    assert index.next_content_candidate((".txt",), 1024 * 1024) is not None

    source.write_text("new generation with a different size", encoding="utf-8")
    index.upsert_path(str(source), "root")
    new = source.stat()
    assert index.next_content_candidate((".txt",), 1024 * 1024) is not None
    assert not index.store_content(
        str(source), "stale bytes", old.st_mtime, old.st_size, 1024 * 1024
    )
    with index._connect() as db:
        row = db.execute(
            "SELECT content_state FROM filesystem_entries WHERE path=?", (str(source),)
        ).fetchone()
    assert new.st_size != old.st_size
    assert row["content_state"] == "indexing"


def test_changed_file_hides_stale_content_and_blocks_stale_embedding(tmp_path: Path) -> None:
    index = FilesystemIndex(tmp_path / "index.sqlite3")
    index.initialize()
    root = tmp_path / "root"
    root.mkdir()
    source = _scan_root(index, root)
    old = source.stat()
    old_text = source.read_text()
    assert index.store_content(
        str(source), old_text, old.st_mtime, old.st_size, 1024 * 1024
    )
    assert index.store_embedding(
        str(source),
        "model",
        b"\x00\x00\x80?",
        1,
        old.st_mtime,
        old.st_size,
    )
    assert index.search_content("private", 10)
    assert index.semantic_search([1.0], "model", limit=10)

    source.write_text("replacement generation", encoding="utf-8")
    index.upsert_path(str(source), "root")
    changed = source.stat()
    assert index.next_content_candidate((".txt",), 1024 * 1024) is not None
    assert index.finish_enrichment_failure(
        str(source), "content", "temporarily unreadable", changed.st_mtime, changed.st_size
    )

    assert index.search_content("private", 10) == []
    assert index.semantic_search([1.0], "model", limit=10) == []
    assert index.next_embedding_candidate("model", 100) is None
