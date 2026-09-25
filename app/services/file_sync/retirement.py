"""One-time cleanup of the retired file mirror's placeholders.

The old mirror (removed 2026-09-24) wrote a zero-byte placeholder file under
the Files folder for every cloud file it had not downloaded, and recorded each
one in its own tracking table, ``file_sync_state``, with ``local_state =
'pointer'``. On a Mac whose Documents folder is in iCloud those placeholders
flooded iCloud and the person's Recents.

This pass removes exactly those placeholders and nothing else:

* only a path the tracking table records as ``pointer``;
* only a regular file (never a symlink or a directory) that is still zero
  bytes — a placeholder that gained content is the person's file now;
* only inside the Files folder — a recorded path that would resolve outside
  it is left alone;
* then only the directories that held a removed placeholder, and their
  parents up to (never including) the Files folder, and only while empty.

Every real file — the ones the mirror downloaded and anything the person put
there — stays. If the tracking table cannot be read, nothing is touched and
the reason is logged. The pass records completion in ``sync_meta`` so it runs
once; a pass that hit errors runs again on the next start.
"""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger

logger = get_logger()

RETIREMENT_ENTITY = "file_sync.retirement"
_TAG = "[file_sync_retirement]"


@dataclass
class _Outcome:
    removed: int = 0
    already_gone: int = 0
    kept_with_bytes: int = 0
    kept_not_a_file: int = 0
    kept_outside_root: int = 0
    errors: int = 0
    dirs_removed: int = 0
    cleared_ids: list[str] = field(default_factory=list)
    error_samples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "removed": self.removed,
            "already_gone": self.already_gone,
            "kept_with_bytes": self.kept_with_bytes,
            "kept_not_a_file": self.kept_not_a_file,
            "kept_outside_root": self.kept_outside_root,
            "errors": self.errors,
            "dirs_removed": self.dirs_removed,
        }


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _remove_placeholders(root: Path, rows: list[tuple[str, str]]) -> _Outcome:
    out = _Outcome()
    try:
        root_real = root.resolve(strict=True)
    except OSError as exc:
        # The Files folder is not reachable (e.g. an unmounted drive). Touch
        # nothing and keep the tracking rows so the next start tries again.
        if rows:
            out.errors = 1
            out.error_samples.append(f"Files folder {root} not reachable: {exc}")
        return out

    touched_dirs: set[Path] = set()
    for file_id, rel_path in rows:
        parts = [p for p in str(rel_path or "").replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or ".." in parts or str(rel_path).startswith("/"):
            out.kept_outside_root += 1
            continue
        candidate = root_real.joinpath(*parts)
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            out.already_gone += 1
            out.cleared_ids.append(file_id)
            continue
        except OSError as exc:
            out.errors += 1
            if len(out.error_samples) < 3:
                out.error_samples.append(f"{rel_path}: {exc}")
            continue
        if not stat.S_ISREG(info.st_mode):
            out.kept_not_a_file += 1
            continue
        try:
            parent_real = candidate.parent.resolve(strict=True)
        except OSError:
            out.kept_outside_root += 1
            continue
        if not _inside(parent_real, root_real):
            # A symlinked directory inside the Files folder points elsewhere.
            out.kept_outside_root += 1
            continue
        if info.st_size != 0:
            out.kept_with_bytes += 1
            continue
        try:
            os.unlink(candidate)
        except FileNotFoundError:
            out.already_gone += 1
            out.cleared_ids.append(file_id)
            continue
        except OSError as exc:
            out.errors += 1
            if len(out.error_samples) < 3:
                out.error_samples.append(f"{rel_path}: {exc}")
            continue
        out.removed += 1
        out.cleared_ids.append(file_id)
        touched_dirs.add(parent_real)

    # Deepest first, so a parent is only tried once its children are gone.
    pending = sorted(touched_dirs, key=lambda p: len(p.parts), reverse=True)
    seen: set[Path] = set()
    for directory in pending:
        current = directory
        while current != root_real and _inside(current, root_real) and current not in seen:
            seen.add(current)
            try:
                os.rmdir(current)  # refuses a non-empty directory — that is the check
            except OSError:
                break
            out.dirs_removed += 1
            current = current.parent
    return out


async def retire_file_mirror(*, db: Any = None, root: Path | None = None) -> dict[str, Any]:
    """Remove the retired mirror's zero-byte placeholders once. Never raises."""
    from app.services.local_db.database import get_db
    from app.services.local_db.repositories import SyncMetaRepo

    db = db or get_db()
    meta = SyncMetaRepo(db)
    try:
        done = await meta.get_last_sync(RETIREMENT_ENTITY)
    except Exception as exc:
        logger.warning("%s tracking database unreadable — no placeholder removed: %s", _TAG, exc)
        return {"status": "skipped", "reason": f"tracking database unreadable: {exc}"}
    if done and done.get("status") == "done":
        return {"status": "already_done"}

    try:
        rows = await db.fetchall(
            "SELECT file_id, rel_path FROM file_sync_state WHERE local_state = 'pointer'"
        )
    except Exception as exc:
        logger.warning(
            "%s the old mirror's tracking table is absent or unreadable — no placeholder "
            "removed: %s",
            _TAG,
            exc,
        )
        return {"status": "skipped", "reason": f"tracking table unreadable: {exc}"}

    if root is None:
        from app.services.paths.manager import get_path

        root = get_path("files")
    pairs = [(str(r["file_id"]), str(r["rel_path"])) for r in rows]
    try:
        outcome = await asyncio.to_thread(_remove_placeholders, root, pairs)
        if outcome.cleared_ids:
            await db.executemany(
                "DELETE FROM file_sync_state WHERE file_id = ?",
                [(file_id,) for file_id in outcome.cleared_ids],
            )
            await db.commit()
        status = "done" if outcome.errors == 0 else "partial"
        error_text = "; ".join(outcome.error_samples) or None
        await meta.set_last_sync(RETIREMENT_ENTITY, status=status, error_message=error_text)
    except Exception as exc:
        logger.error("%s cleanup failed — retried next start: %s", _TAG, exc, exc_info=True)
        return {"status": "failed", "reason": str(exc)}

    counts = outcome.as_dict()
    logger.info(
        "%s %s under %s: removed %d placeholders and %d empty folders; kept %d files "
        "with content, %d non-files, %d outside the Files folder; %d already gone; "
        "%d errors%s",
        _TAG,
        status,
        root,
        outcome.removed,
        outcome.dirs_removed,
        outcome.kept_with_bytes,
        outcome.kept_not_a_file,
        outcome.kept_outside_root,
        outcome.already_gone,
        outcome.errors,
        f" (e.g. {error_text}) — retried next start" if error_text else "",
    )
    return {"status": status, "root": str(root), "tracked_pointers": len(pairs), **counts}
