"""Tool-layer hydration seam.

The agent must see one uniform filesystem regardless of sync mode: a Read /
Copy / Move that touches a pointer file under the Files root transparently
fetches its bytes first. This is the ONE helper the tools call — cheap for
every path outside the Files root (a string prefix check), loud when a
pointer cannot be hydrated (an empty placeholder must never masquerade as
the file's real content).
"""

from __future__ import annotations

from pathlib import Path
import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from app.common.system_logger import get_logger

logger = get_logger()
T = TypeVar("T")
if TYPE_CHECKING:
    from app.services.file_sync.engine import FileSyncEngine


async def ensure_hydrated(abs_path: str) -> str | None:
    """Hydrate ``abs_path`` if it is a pointer file. Returns None when the
    path is ready to use, or a human-readable error string when it is a
    pointer whose bytes could not be fetched."""
    from app.services.file_sync import get_file_sync_engine

    engine = get_file_sync_engine()
    try:
        root = engine.root
        rel = Path(abs_path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return None  # outside the Files root — nothing to do

    try:
        state = await engine._index.get_state_by_path(rel)
    except Exception as exc:
        logger.error("[file_sync] hydration state lookup failed for %s", rel, exc_info=True)
        return (
            f"{rel} is inside the managed Files folder, but Matrx could not verify whether "
            f"its bytes are stored locally ({exc}). Check file sync status and retry; "
            "the placeholder was not opened as real content."
        )
    if state is None or state["local_state"] != "pointer":
        return None

    try:
        await engine.hydrate(state["file_id"])
        return None
    except Exception as exc:
        logger.error("[file_sync] on-demand hydration of %s failed: %s", rel, exc)
        return (
            f"{rel} is a cloud file that is not stored on this machine yet, and "
            f"fetching it failed ({exc}). Check connectivity/sign-in and retry, "
            f"or run POST /file-sync/hydrate."
        )


async def ensure_tree_hydrated(abs_path: str) -> str | None:
    """Hydrate a file or every pointer descendant of a directory.

    A directory move/copy must never preserve zero-byte pointer placeholders
    as though they were real user files. Paths outside the managed Files root
    remain a cheap no-op.
    """
    target = Path(abs_path)
    if not target.is_dir():
        return await ensure_hydrated(abs_path)

    from app.services.file_sync import get_file_sync_engine

    engine = get_file_sync_engine()
    try:
        relative = target.resolve().relative_to(engine.root.resolve())
        # Path.as_posix() represents the root-relative empty path as ".".
        # File-sync rows never carry that prefix, so the canonical empty
        # prefix means every managed descendant.
        rel = "" if relative == Path(".") else relative.as_posix()
    except (ValueError, OSError):
        return None
    async with engine._sync_lock:
        return await _ensure_tree_hydrated_locked(engine, target, rel)


async def run_tree_operation_hydrated(
    abs_path: str, operation: Callable[[], T]
) -> tuple[T | None, str | None]:
    """Hydrate and execute one copy/move under a single file-sync guard.

    The guard spans enumeration, bounded hydration, and the actual filesystem
    mutation, so a pull cycle cannot introduce a new pointer in the TOCTOU gap.
    Synchronous filesystem work runs off the event loop while the guard stays
    held.
    """
    from app.services.file_sync import get_file_sync_engine

    engine = get_file_sync_engine()
    target = Path(abs_path)
    try:
        relative = target.resolve().relative_to(engine.root.resolve())
        rel = "" if relative == Path(".") else relative.as_posix()
    except (ValueError, OSError):
        return await asyncio.to_thread(operation), None

    async with engine._sync_lock:
        error = await _ensure_tree_hydrated_locked(engine, target, rel)
        if error:
            return None, error
        return await asyncio.to_thread(operation), None


async def _ensure_tree_hydrated_locked(
    engine: "FileSyncEngine", target: Path, rel: str
) -> str | None:
    """Hydrate a managed path while the caller holds the engine sync lock."""
    if not target.is_dir():
        try:
            state = await engine._index.get_state_by_path(rel)
        except Exception as exc:
            logger.error("[file_sync] hydration state lookup failed for %s", rel, exc_info=True)
            return (
                f"{rel} is inside the managed Files folder, but Matrx could not verify whether "
                f"its bytes are stored locally ({exc}). Check file sync status and retry; "
                "the placeholder was not used as real content."
            )
        if state is None or state["local_state"] != "pointer":
            return None
        try:
            await engine._hydrate(state["file_id"])
            return None
        except Exception as exc:
            return (
                f"{rel} is a cloud file that is not stored on this machine yet, and "
                f"fetching it failed ({exc}). Check connectivity/sign-in and retry."
            )

    failures: list[str] = []
    after_rel_path: str | None = None
    while True:
        try:
            pointers = await engine._index.list_by_state_under_path(
                "pointer", rel, limit=200, after_rel_path=after_rel_path
            )
        except Exception as exc:
            logger.error(
                "[file_sync] pointer-descendant lookup failed for %s", rel, exc_info=True
            )
            return (
                f"{rel} is inside the managed Files folder, but Matrx could not verify whether "
                f"all cloud-backed files in it are stored locally ({exc}). Check file sync "
                "status and retry. No copy/move was started."
            )
        if not pointers:
            break
        rows = iter(pointers)

        async def _worker() -> None:
            while True:
                try:
                    row = next(rows)
                except StopIteration:
                    return
                try:
                    await engine._hydrate(row["file_id"])
                except Exception as exc:
                    failures.append(f"{row['rel_path']}: {exc}")

        await asyncio.gather(*(_worker() for _ in range(4)))
        after_rel_path = str(pointers[-1]["rel_path"])

    if failures:
        preview = "; ".join(failures[:5])
        suffix = f" (+{len(failures) - 5} more)" if len(failures) > 5 else ""
        return (
            "Some cloud-backed files in this directory could not be stored locally before "
            f"the operation: {preview}{suffix}. No copy/move was started."
        )
    return None
