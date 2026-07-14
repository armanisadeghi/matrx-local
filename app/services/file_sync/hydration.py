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
