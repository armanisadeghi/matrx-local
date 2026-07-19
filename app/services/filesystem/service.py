"""Progressive filesystem service used by tools, APIs, and future UI surfaces."""

from __future__ import annotations

import asyncio
from collections import deque
import importlib.util
import logging
import os
import time
import threading
from array import array
from pathlib import Path
from typing import Any

import psutil

from app.services.filesystem.index import FilesystemIndex, _should_skip_directory
from app.services.filesystem.models import DirectoryPage, FileEntry, Place, SearchPage
from app.services.filesystem.paging import (
    DIRECTORY_LIST_SESSION_TTL_SECONDS,
    DirectoryListSessionRegistry,
)
from app.services.filesystem.roots import (
    configured_priority_roots,
    discover_places,
    normalize_path_key,
)

logger = logging.getLogger(__name__)

_MATRX_HOME_DIR = Path(os.environ.get("MATRX_HOME_DIR", str(Path.home() / ".matrx")))

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
_RECONCILE_SECONDS = 6 * 60 * 60
_TEXT_EXTENSIONS = (
    ".c", ".cc", ".cpp", ".css", ".csv", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".json", ".jsx", ".kt", ".md", ".py", ".rb", ".rs",
    ".sh", ".sql", ".swift", ".toml", ".ts", ".tsx", ".txt", ".xml",
    ".yaml", ".yml",
)
_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
_DEFAULT_CONTENT_QUOTA_BYTES = 512 * 1024 * 1024
_DEFAULT_EMBEDDING_QUOTA_ENTRIES = 10_000


class FilesystemService:
    """Metadata-first local discovery that remains useful before indexing ends."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.index = FilesystemIndex(db_path or _MATRX_HOME_DIR / "filesystem-index.sqlite3")
        self._places: list[Place] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._stop = asyncio.Event()
        self._thread_stop = threading.Event()
        self._started = False
        self._indexed_this_run = 0
        self._last_reconcile = 0.0
        self._crawl_iterations = 0
        self._watch_signature: tuple[str, ...] = ()
        self._maintenance_lock = asyncio.Lock()
        self._directory_lists = DirectoryListSessionRegistry()

    async def start(self) -> None:
        if self._started:
            return
        self._stop = asyncio.Event()
        self._thread_stop = threading.Event()
        self._directory_lists.start()
        await asyncio.to_thread(self.index.initialize)
        await self.refresh_roots()
        self._started = True
        self._spawn(self._crawl_loop(), "filesystem-crawl")
        self._spawn(self._watch_loop(), "filesystem-watch")
        self._spawn(self._enrichment_loop(), "filesystem-enrichment")
        self._spawn(self._directory_list_reaper(), "filesystem-list-reaper")

    async def stop(self) -> None:
        self._stop.set()
        self._thread_stop.set()
        tasks = list(self._tasks)
        crawl = [task for task in tasks if task.get_name() == "filesystem-crawl"]
        others = [task for task in tasks if task not in crawl]
        for task in others:
            task.cancel()
        if others:
            await asyncio.gather(*others, return_exceptions=True)
        if crawl:
            try:
                await asyncio.wait_for(asyncio.gather(*crawl, return_exceptions=True), timeout=2.0)
            except asyncio.TimeoutError:
                for task in crawl:
                    task.cancel()
                await asyncio.gather(*crawl, return_exceptions=True)
        self._tasks.clear()
        await asyncio.to_thread(self._directory_lists.close)
        self._started = False

    def _spawn(self, coroutine: Any, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _directory_list_reaper(self) -> None:
        """Delete expired disk snapshots even when a client abandons its cursor."""
        interval = min(60.0, DIRECTORY_LIST_SESSION_TTL_SECONDS)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                await asyncio.to_thread(self._directory_lists.reap_expired)

    async def refresh_roots(self) -> list[Place]:
        places = await asyncio.to_thread(discover_places)
        async with self._maintenance_lock:
            await asyncio.to_thread(self.index.sync_roots, places)
        self._places = places
        self._last_reconcile = time.time()
        signature = tuple(self._watch_roots())
        if self._started and signature != self._watch_signature:
            await self._restart_watcher(signature)
        return places

    async def places(self) -> list[dict[str, object]]:
        if not self._places:
            await self.refresh_roots()
        return [place.to_dict() for place in self._places]

    async def set_priority_roots(self, roots: list[dict[str, str] | str]) -> list[dict[str, object]]:
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, item in enumerate(roots):
            if isinstance(item, str):
                raw_path, label = item, Path(item).name or f"Priority root {index + 1}"
            else:
                raw_path = item.get("path", "")
                label = item.get("label") or Path(raw_path).name or f"Priority root {index + 1}"
            if not raw_path.strip():
                continue
            path = str(Path(raw_path).expanduser().absolute())
            key = normalize_path_key(path)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"label": label, "path": path})
        from app.services.cloud_sync.settings_sync import get_settings_sync

        sync = get_settings_sync()
        current = sync.get("filesystem_index", {}) or {}
        sync.set("filesystem_index", {**current, "priority_roots": normalized})
        await self.refresh_roots()
        return await self.places()

    async def indexing_settings(self) -> dict[str, object]:
        """Return authored indexing policy; never infer it from merged Places."""
        return {
            "priority_roots": configured_priority_roots(),
            **_indexing_settings(),
        }

    async def set_indexing_settings(
        self,
        *,
        content_enabled: bool,
        semantic_enabled: bool,
        embedding_model: str = _DEFAULT_EMBEDDING_MODEL,
        max_content_bytes: int = _DEFAULT_CONTENT_QUOTA_BYTES,
        max_embedding_entries: int = _DEFAULT_EMBEDDING_QUOTA_ENTRIES,
    ) -> dict[str, object]:
        from app.services.cloud_sync.settings_sync import get_settings_sync

        async with self._maintenance_lock:
            old_settings = _indexing_settings()
            sync = get_settings_sync()
            current = sync.get("filesystem_index", {}) or {}
            active_model = embedding_model.strip() or _DEFAULT_EMBEDDING_MODEL
            sync.set(
                "filesystem_index",
                {
                    **current,
                    "content_enabled": content_enabled,
                    "semantic_enabled": semantic_enabled,
                    "embedding_model": active_model,
                    "max_content_bytes": max_content_bytes,
                    "max_embedding_entries": max_embedding_entries,
                },
            )
            await asyncio.to_thread(
                self.index.apply_enrichment_limits,
                max_content_bytes,
                max_embedding_entries,
                active_model,
                reset_content_quota=max_content_bytes > int(old_settings["max_content_bytes"]),
            )
        return await self.status()

    async def control_index(self, action: str) -> dict[str, object]:
        """Apply a user-authored lifecycle action without disabling direct browsing."""
        if action not in {"pause", "resume", "rebuild", "clear"}:
            raise ValueError(f"unsupported filesystem index action: {action}")
        self._set_paused(action in {"pause", "clear"})
        if action == "pause":
            return await self.status()

        places = await asyncio.to_thread(discover_places)
        async with self._maintenance_lock:
            if action in {"rebuild", "clear"}:
                await asyncio.to_thread(self.index.clear_derived)
                self._indexed_this_run = 0
            if action in {"resume", "rebuild"}:
                await asyncio.to_thread(self.index.sync_roots, places)
                self._places = places
                self._last_reconcile = time.time()
        if action in {"resume", "rebuild"}:
            signature = tuple(self._watch_roots())
            if self._started and signature != self._watch_signature:
                await self._restart_watcher(signature)
        return await self.status()

    def _set_paused(self, paused: bool) -> None:
        from app.services.cloud_sync.settings_sync import get_settings_sync

        sync = get_settings_sync()
        current = sync.get("filesystem_index", {}) or {}
        sync.set("filesystem_index", {**current, "paused": paused})

    async def list_directory(
        self,
        path: str,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        show_hidden: bool = False,
    ) -> DirectoryPage:
        absolute = str(Path(path).expanduser().absolute())
        if not os.path.isdir(absolute):
            raise NotADirectoryError(absolute)
        safe_limit = min(max(1, limit), MAX_PAGE_SIZE)
        return await asyncio.wait_for(
            asyncio.to_thread(
                self._directory_lists.page,
                absolute,
                cursor=cursor,
                limit=safe_limit,
                show_hidden=show_hidden,
            ),
            timeout=5.0,
        )

    async def prepare_open(self, path: str) -> dict[str, object]:
        """Return an OS-open target, hydrating cloud-backed pointer files first."""
        absolute = str(Path(path).expanduser().absolute())
        if not os.path.exists(absolute):
            raise FileNotFoundError(absolute)
        if os.path.isfile(absolute):
            from app.services.file_sync.hydration import ensure_hydrated

            hydration_error = await ensure_hydrated(absolute)
            if hydration_error:
                return {"path": absolute, "ready": False, "error": hydration_error}
        return {"path": absolute, "ready": True}

    async def find(
        self,
        query: str,
        *,
        root: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        timeout_seconds: float = 2.0,
    ) -> SearchPage:
        clean = query.strip()
        if not clean:
            raise ValueError("query must not be empty")
        safe_limit = min(max(1, limit), MAX_PAGE_SIZE)
        offset = _decode_cursor(cursor)
        resolved_root = str(Path(root).expanduser().absolute()) if root else None
        queued = await asyncio.to_thread(self.index.queue_count)
        if queued > 0:
            # A partial index and the bounded disk fallback form one logical
            # result set. Rebuild its bounded prefix for every numeric offset
            # so a full indexed first page cannot hide cold disk-only matches.
            prefix_limit = min(offset + safe_limit + 1, 50_001)
            index_search = asyncio.wait_for(
                asyncio.to_thread(
                    self.index.search, clean, limit=prefix_limit, offset=0, root=resolved_root
                ),
                timeout=min(max(timeout_seconds, 0.1), 10.0),
            )
            disk_roots = [resolved_root] if resolved_root else await asyncio.to_thread(
                self.index.pending_root_paths
            )
            disk_search = asyncio.wait_for(
                asyncio.to_thread(
                    self._bounded_disk_find,
                    clean,
                    resolved_root,
                    prefix_limit,
                    timeout_seconds,
                    disk_roots,
                ),
                timeout=min(max(timeout_seconds + 0.25, 0.25), 10.25),
            )
            indexed_entries, disk_entries = await asyncio.gather(index_search, disk_search)
            by_path = {entry.path: entry for entry in indexed_entries}
            for entry in disk_entries:
                by_path.setdefault(entry.path, entry)
            merged = list(by_path.values())
            entries = merged[offset : offset + safe_limit]
            source = "hybrid"
            has_more = len(merged) > offset + safe_limit
        else:
            entries = await asyncio.wait_for(
                asyncio.to_thread(
                    self.index.search, clean, limit=safe_limit + 1, offset=offset, root=resolved_root
                ),
                timeout=min(max(timeout_seconds, 0.1), 10.0),
            )
            source = "index"
            if not entries and offset == 0:
                entries = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._bounded_disk_find,
                        clean,
                        resolved_root,
                        safe_limit + 1,
                        timeout_seconds,
                    ),
                    timeout=min(max(timeout_seconds + 0.25, 0.25), 10.25),
                )
                source = "disk"
            has_more = len(entries) > safe_limit
            entries = entries[:safe_limit]
        paused = bool(_indexing_settings()["paused"])
        unavailable = await asyncio.to_thread(self._unavailable_priority_roots)
        return SearchPage(
            clean,
            tuple(entries),
            str(offset + safe_limit) if has_more else None,
            source=source,
            index_complete=queued == 0 and not paused and not unavailable,
            root=resolved_root,
        )

    async def semantic_find(self, query: str, *, limit: int = 20) -> dict[str, object]:
        clean = query.strip()
        if not clean:
            raise ValueError("query must not be empty")
        settings = _indexing_settings()
        if not settings["semantic_enabled"]:
            raise RuntimeError("Semantic filesystem search is disabled in indexing settings.")
        if importlib.util.find_spec("fastembed") is None:
            raise RuntimeError("Semantic filesystem search requires the optional FastEmbed capability.")
        from app.api.openai_compat_routes import _embed_texts

        vectors = await _embed_texts((clean,), str(settings["embedding_model"]))
        results = await asyncio.to_thread(
            self.index.semantic_search,
            vectors[0],
            str(settings["embedding_model"]),
            limit=min(max(1, limit), 100),
        )
        return {
            "kind": "filesystem.semantic-search",
            "namespace": "host",
            "query": clean,
            "model": settings["embedding_model"],
            "results": results,
        }

    def _bounded_disk_find(
        self,
        query: str,
        root: str | None,
        limit: int,
        timeout_seconds: float,
        roots: list[str] | None = None,
    ) -> list[FileEntry]:
        deadline = time.monotonic() + min(max(timeout_seconds, 0.1), 5.0)
        needle = query.casefold()
        candidates = roots or ([root] if root else [place.path for place in self._places if place.available])
        queue = deque(dict.fromkeys(value for value in candidates if value))
        seen: set[str] = set()
        results: list[FileEntry] = []
        visited = 0
        while queue and len(results) < limit and time.monotonic() < deadline and visited < 50_000:
            directory = queue.popleft()
            normalized = os.path.normcase(os.path.abspath(directory))
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                iterator = os.scandir(directory)
            except OSError:
                continue
            with iterator:
                for item in iterator:
                    visited += 1
                    if needle in os.path.abspath(item.path).casefold():
                        results.append(FileEntry.from_dir_entry(item))
                        if len(results) >= limit:
                            break
                    try:
                        is_dir = item.is_dir(follow_symlinks=False)
                    except OSError:
                        is_dir = False
                    if is_dir and not _should_skip_directory(item.name):
                        queue.append(item.path)
                    if visited >= 50_000 or time.monotonic() >= deadline:
                        break
        return results

    async def status(self) -> dict[str, object]:
        entries, scan, enrichment = await asyncio.gather(
            asyncio.to_thread(self.index.entry_count), asyncio.to_thread(self.index.scan_status),
            asyncio.to_thread(self.index.enrichment_counts),
        )
        settings = _indexing_settings()
        paused = bool(settings["paused"])
        fastembed_available = importlib.util.find_spec("fastembed") is not None
        queued = int(scan["total"])
        failed = int(scan["failed"])
        unavailable_priority_roots = await asyncio.to_thread(
            self._unavailable_priority_roots
        )
        metadata_state = (
            "partial" if failed or unavailable_priority_roots
            else "paused" if paused else "indexing" if queued else "complete"
        )
        return {
            "started": self._started,
            "database": str(self.index.path),
            "fts5": self.index.fts_available,
            "entries": entries,
            "directories_pending": queued,
            "directories_failed": failed,
            "directories_claimed": int(scan["claimed"]),
            "directories_ready": int(scan["ready"]),
            "scan_failures": scan["failures"],
            "unavailable_priority_roots": unavailable_priority_roots,
            "metadata_state": metadata_state,
            "index_complete": metadata_state == "complete",
            "paused": paused,
            "indexed_this_run": self._indexed_this_run,
            "places": len(self._places),
            "last_reconcile_at": self._last_reconcile or None,
            "last_scan_at": await asyncio.to_thread(self.index.last_scan_at),
            "storage_bytes": await asyncio.to_thread(self.index.storage_bytes),
            **enrichment,
            "content_indexing": "enabled" if settings["content_enabled"] else "disabled",
            "embedding_indexing": (
                "enabled" if settings["semantic_enabled"] and fastembed_available
                else "unavailable" if settings["semantic_enabled"]
                else "disabled"
            ),
            "embedding_model": settings["embedding_model"],
            "max_content_bytes": settings["max_content_bytes"],
            "max_embedding_entries": settings["max_embedding_entries"],
            "fastembed_available": fastembed_available,
            "policy": "priority roots first; all discovered accessible volumes remain queued",
        }

    def _unavailable_priority_roots(self) -> list[dict[str, str]]:
        unavailable: list[dict[str, str]] = []
        for root in configured_priority_roots():
            raw_path = str(root.get("path") or "")
            if not raw_path:
                continue
            path = Path(raw_path).expanduser().absolute()
            if path.is_dir():
                continue
            unavailable.append(
                {"path": raw_path, "label": str(root.get("label") or raw_path)}
            )
        return unavailable

    async def _crawl_loop(self) -> None:
        while not self._stop.is_set():
            item: tuple[str, str, int, int, str] | None = None
            try:
                if bool(_indexing_settings()["paused"]):
                    await asyncio.sleep(2.0)
                    continue
                if time.time() - self._last_reconcile >= _RECONCILE_SECONDS:
                    await self.refresh_roots()
                if not _background_capacity(self.index.path, cpu_limit=70):
                    await asyncio.sleep(2.0)
                    continue
                self._crawl_iterations += 1
                async with self._maintenance_lock:
                    item = await asyncio.to_thread(
                        self.index.pop_next_directory,
                        fair=self._crawl_iterations % 5 == 0,
                    )
                    if item is None:
                        count = 0
                    else:
                        path, root_id, depth, priority, claim_token = item
                        count = await asyncio.to_thread(
                            self.index.index_directory,
                            path,
                            root_id,
                            depth,
                            priority,
                            claim_token,
                            self._thread_stop,
                        )
                if item is None:
                    await asyncio.sleep(5.0)
                    continue
                self._indexed_this_run += count
                await asyncio.sleep(0.01 if priority >= 80_000 else 0.1)
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                if item is not None:
                    retry_delay = await asyncio.to_thread(
                        self.index.fail_directory, item[0], exc, item[4]
                    )
                    if retry_delay is not None:
                        logger.warning(
                            "Filesystem directory scan failed; retrying in %.0fs path=%s",
                            retry_delay,
                            item[0],
                            exc_info=True,
                        )
                    else:
                        logger.info(
                            "Filesystem directory scan failed after its claim was superseded: %s",
                            item[0],
                        )
                await asyncio.sleep(1.0)
            except Exception:
                if item is not None:
                    await asyncio.to_thread(
                        self.index.release_directory, item[0], item[4]
                    )
                logger.warning("Filesystem crawl iteration failed; continuing", exc_info=True)
                await asyncio.sleep(1.0)

    async def _watch_loop(self) -> None:
        # Watching every mounted volume is expensive on some OSes.  Watch user
        # and configured roots continuously; periodic reconciliation covers the
        # remaining low-priority volumes without excluding them from indexing.
        roots = self._watch_roots()
        self._watch_signature = tuple(roots)
        if not roots:
            return
        try:
            import watchfiles

            async for changes in watchfiles.awatch(*roots, debounce=1000, step=250):
                if bool(_indexing_settings()["paused"]):
                    continue
                for change, path in changes:
                    await self._apply_watch_path(
                        path,
                        deleted=change == watchfiles.Change.deleted,
                    )
                if self._stop.is_set():
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Filesystem watcher stopped; periodic reconciliation remains active", exc_info=True)

    async def _apply_watch_path(self, path: str, *, deleted: bool) -> None:
        """Apply one watcher mutation only if indexing is still admitted."""
        async with self._maintenance_lock:
            # Pause/clear may have won the lock after this watch batch was
            # received. The decision inside the lock is the commit fence.
            if bool(_indexing_settings()["paused"]):
                return
            if deleted:
                await asyncio.to_thread(self.index.delete_path, path)
            else:
                root_id = self._root_id_for_path(path)
                await asyncio.to_thread(self.index.upsert_path, path, root_id)

    def _watch_roots(self) -> list[str]:
        return _minimal_roots([
            place.path for place in self._places
            if place.available and place.category in {"standard", "configured"}
            and os.path.isdir(place.path)
        ])

    def _root_id_for_path(self, path: str) -> str:
        owners = [
            place for place in self._places
            if place.available and _is_under(path, place.path)
        ]
        if not owners:
            return "home"
        return max(owners, key=lambda place: len(Path(place.path).parts)).id

    async def _restart_watcher(self, signature: tuple[str, ...]) -> None:
        watchers = [task for task in self._tasks if task.get_name() == "filesystem-watch"]
        for task in watchers:
            task.cancel()
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)
        self._watch_signature = signature
        self._spawn(self._watch_loop(), "filesystem-watch")

    async def _enrichment_loop(self) -> None:
        while not self._stop.is_set():
            settings = _indexing_settings()
            if bool(settings["paused"]):
                await asyncio.sleep(2.0)
                continue
            if not _background_capacity(self.index.path, cpu_limit=45):
                await asyncio.sleep(5.0)
                continue
            worked = False
            if settings["content_enabled"]:
                candidate = await asyncio.to_thread(
                    self.index.next_content_candidate,
                    _TEXT_EXTENSIONS,
                    int(settings["max_content_bytes"]),
                )
                if candidate:
                    path, modified_at, source_size = candidate
                    try:
                        content = await asyncio.to_thread(
                            Path(path).read_text, encoding="utf-8", errors="replace"
                        )
                        async with self._maintenance_lock:
                            commit_settings = _indexing_settings()
                            if commit_settings["content_enabled"] and not commit_settings["paused"]:
                                await asyncio.to_thread(
                                    self.index.store_content,
                                    path,
                                    content,
                                    modified_at,
                                    source_size,
                                    int(commit_settings["max_content_bytes"]),
                                )
                            else:
                                await asyncio.to_thread(
                                    self.index.mark_content_skipped, path, "not_indexed"
                                )
                    except OSError:
                        await asyncio.to_thread(self.index.mark_content_skipped, path, "error")
                    worked = True

            if settings["semantic_enabled"] and importlib.util.find_spec("fastembed") is not None:
                candidate = await asyncio.to_thread(
                    self.index.next_embedding_candidate,
                    str(settings["embedding_model"]),
                    int(settings["max_embedding_entries"]),
                )
                if candidate:
                    path, content, modified_at, source_size = candidate
                    try:
                        from app.api.openai_compat_routes import _embed_texts

                        vectors = await _embed_texts(
                            (content[:16_000],), str(settings["embedding_model"])
                        )
                        vector = vectors[0]
                        async with self._maintenance_lock:
                            commit_settings = _indexing_settings()
                            model = str(settings["embedding_model"])
                            if (
                                commit_settings["semantic_enabled"]
                                and not commit_settings["paused"]
                                and str(commit_settings["embedding_model"]) == model
                            ):
                                await asyncio.to_thread(
                                    self.index.store_embedding,
                                    path,
                                    model,
                                    array("f", vector).tobytes(),
                                    len(vector),
                                    modified_at,
                                    source_size,
                                    max_entries=int(commit_settings["max_embedding_entries"]),
                                )
                            else:
                                await asyncio.to_thread(
                                    self.index.reset_embedding_candidate, path
                                )
                    except Exception:
                        logger.warning("Background embedding failed for %s", path, exc_info=True)
                        await asyncio.to_thread(self.index.mark_embedding_unavailable, path)
                    worked = True
            await asyncio.sleep(0.1 if worked else 10.0)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid cursor") from exc
    if value < 0:
        raise ValueError("invalid cursor")
    return value


def _minimal_roots(paths: list[str]) -> list[str]:
    """Remove descendant watch roots already covered by an ancestor."""
    ordered = sorted({os.path.abspath(path) for path in paths}, key=lambda path: (len(Path(path).parts), path))
    result: list[str] = []
    for candidate in ordered:
        if any(_is_under(candidate, parent) for parent in result):
            continue
        result.append(candidate)
    return result


def _is_under(candidate: str, parent: str) -> bool:
    try:
        return os.path.commonpath([candidate, parent]) == os.path.abspath(parent)
    except ValueError:
        # Windows different-drive/UNC paths are independent roots.
        return False


def _indexing_settings() -> dict[str, object]:
    try:
        from app.services.cloud_sync.settings_sync import get_settings_sync

        stored = get_settings_sync().get("filesystem_index", {}) or {}
    except Exception:
        # A transient settings failure must never silently re-enable private
        # local indexing after the user paused or disabled enrichment.
        stored = {
            "paused": True,
            "content_enabled": False,
            "semantic_enabled": False,
        }
    return {
        "paused": bool(stored.get("paused", False)),
        "content_enabled": bool(stored.get("content_enabled", True)),
        "semantic_enabled": bool(stored.get("semantic_enabled", False)),
        "embedding_model": str(stored.get("embedding_model") or _DEFAULT_EMBEDDING_MODEL),
        "max_content_bytes": max(
            16 * 1024 * 1024,
            min(int(stored.get("max_content_bytes") or _DEFAULT_CONTENT_QUOTA_BYTES), 20 * 1024 * 1024 * 1024),
        ),
        "max_embedding_entries": max(
            100,
            min(int(stored.get("max_embedding_entries") or _DEFAULT_EMBEDDING_QUOTA_ENTRIES), 50_000),
        ),
    }


def _background_capacity(db_path: Path, *, cpu_limit: float) -> bool:
    if psutil.cpu_percent(interval=None) >= cpu_limit:
        return False
    if psutil.virtual_memory().percent >= 85:
        return False
    battery = psutil.sensors_battery()
    if battery is not None and not battery.power_plugged and battery.percent < 40:
        return False
    try:
        if psutil.disk_usage(str(db_path.parent)).free < 1_000_000_000:
            return False
    except OSError:
        return False
    return True


_service: FilesystemService | None = None


def get_filesystem_service() -> FilesystemService:
    global _service
    if _service is None:
        _service = FilesystemService()
    return _service
