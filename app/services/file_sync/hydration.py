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

from app.common.system_logger import get_logger

logger = get_logger()


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
    except Exception:
        logger.debug("[file_sync] hydration state lookup failed for %s", rel, exc_info=True)
        return None
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
        rel = target.resolve().relative_to(engine.root.resolve()).as_posix()
    except (ValueError, OSError):
        return None
    try:
        pointers = await engine._index.list_by_state_under_path("pointer", rel)
    except Exception:
        logger.debug("[file_sync] pointer-descendant lookup failed for %s", rel, exc_info=True)
        return None
    semaphore = asyncio.Semaphore(4)

    async def _hydrate(row: dict) -> str | None:
        async with semaphore:
            try:
                await engine.hydrate(row["file_id"])
                return None
            except Exception as exc:
                return f"{row['rel_path']}: {exc}"

    failures = [error for error in await asyncio.gather(*(_hydrate(row) for row in pointers)) if error]
    if failures:
        preview = "; ".join(failures[:5])
        suffix = f" (+{len(failures) - 5} more)" if len(failures) > 5 else ""
        return (
            "Some cloud-backed files in this directory could not be stored locally before "
            f"the operation: {preview}{suffix}. No copy/move was started."
        )
    return None
