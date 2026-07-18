"""Local notes file manager — reads/writes .md/.txt files on the user's machine.

Canonical location: ~/Documents/Matrx/Notes/<folder>/<note>.md
  (resolved from MATRX_NOTES_DIR — see config.py)

Additional mapped directories are synced copies of the canonical files.

Architecture: local-first. This manager never touches Supabase or any network.
Sync with Supabase is handled separately by sync_engine.py and is always
optional, best-effort, and never blocks a local operation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Access health ────────────────────────────────────────────────────────────
#
# All access-health state lives in the canonical access_health service
# (app/services/access_health/FEATURE.md). This module records evidence
# against the "notes-canonical" resource (or the per-mapping resource for
# mapped-directory writes) and NEVER holds its own access state. The old
# global _NotesAccessGuard — which any EACCES anywhere poisoned and any
# success anywhere cleared, always with a hardcoded "grant Full Disk Access"
# reason — was deleted 2026-07; do not reintroduce a module-level flag here.

from app.services.access_health import Capability, get_access_health
from app.services.documents.access_resources import (
    NOTES_RESOURCE,
    mapping_resource_id,
    register_notes_canonical,
)

# These module-level names are used as fallbacks during import-time initialisation
# before the path manager is fully loaded. After that, DocumentFileManager.base_dir
# resolves dynamically via safe_dir() so user overrides take effect immediately.
from app.config import MATRX_NOTES_DIR

# Backward-compat alias
DOCUMENTS_BASE_DIR = MATRX_NOTES_DIR


def _notes_dir() -> Path:
    """Resolve the current notes directory — respects user overrides."""
    try:
        from app.services.paths.manager import safe_dir

        return safe_dir("notes")
    except Exception:
        try:
            MATRX_NOTES_DIR.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass
        return MATRX_NOTES_DIR


def _ensure_dirs() -> None:
    """Create all required directory structures, respecting user path overrides."""
    try:
        from app.services.paths.manager import safe_dir

        safe_dir("notes")
        safe_dir("files")
        safe_dir("code")
        safe_dir("workspaces")
        safe_dir("agent_data")
    except Exception:
        # Path manager not yet initialised — use defaults
        from app.config import (
            MATRX_NOTES_DIR,
            MATRX_FILES_DIR,
            MATRX_CODE_DIR,
            MATRX_WORKSPACES_DIR,
            MATRX_DATA_DIR,
        )

        for d in [
            MATRX_NOTES_DIR,
            MATRX_FILES_DIR,
            MATRX_CODE_DIR,
            MATRX_WORKSPACES_DIR,
            MATRX_DATA_DIR,
        ]:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                pass

    # Sync metadata always lives inside the resolved notes dir
    notes = _notes_dir()
    try:
        (notes / ".sync").mkdir(parents=True, exist_ok=True)
        (notes / ".sync" / "conflicts").mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        get_access_health().record(
            NOTES_RESOURCE,
            Capability.CREATE,
            ok=False,
            path=str(notes / ".sync"),
            errno=e.errno,
            error=str(e),
            op="creating .sync metadata dirs",
            source="file_manager",
        )


def _atomic_write(target: Path, content: str) -> None:
    """Write *content* to *target* atomically via a sibling temp file + os.replace().

    On all POSIX systems and modern Windows, os.replace() is atomic within the
    same filesystem, so a crash mid-write leaves either the old file or the new
    file fully intact — never a partial write.

    PURE by design: raises the underlying OSError untouched and records NO
    access-health state. Callers wrap it in the access service's
    ``observing(<their resource>, REPLACE, ...)`` so evidence lands on the
    right resource. (Its previous guard side effects meant one bad mapped dir
    globally poisoned notes health — the canonical false-FDA defect.)
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _observing_notes(capability: Capability, op: str, path: Path):
    """Shorthand: observe one canonical-notes filesystem operation."""
    return get_access_health().observing(
        NOTES_RESOURCE, capability, op=op, source="file_manager", path=str(path)
    )


def _safe_filename(name: str) -> str:
    """Convert a note label to a filesystem-safe filename."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    safe = safe.strip(". ")
    return safe or "untitled"


def content_hash(content: str) -> str:
    """SHA-256 hash for content comparison."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class DocumentFileManager:
    """Manages .md/.txt notes on the local filesystem.

    base_dir resolves dynamically from the path manager so user-configured
    path overrides take effect without a restart. Pass an explicit base_dir
    only in tests.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._explicit_base = base_dir
        _ensure_dirs()

    @property
    def base_dir(self) -> Path:
        if self._explicit_base is not None:
            return self._explicit_base
        return _notes_dir()

    # ── Path helpers ─────────────────────────────────────────────────────────

    def folder_path(self, folder_name: str) -> Path:
        return self.base_dir / _safe_filename(folder_name)

    def note_path(self, folder_name: str, label: str) -> Path:
        return self.folder_path(folder_name) / f"{_safe_filename(label)}.md"

    def note_path_from_file_path(self, file_path: str) -> Path:
        """Resolve a stored file_path (e.g. 'React/hooks.md') to absolute."""
        return self.base_dir / file_path

    def relative_path(self, absolute: Path) -> str:
        """Convert an absolute path back to a relative file_path string."""
        return str(absolute.relative_to(self.base_dir))

    def unique_file_path(self, folder_name: str, label: str) -> str:
        """Return a relative file_path that does not yet exist on disk.

        If ``folder/label.md`` is taken, appends _2, _3 … _99, then falls back
        to a compact UTC timestamp suffix. This is the ONLY sanctioned way to
        pick a path for a note that doesn't already own one — deriving
        ``<folder>/<label>.md`` directly silently overwrites whichever note
        currently occupies that path.
        """
        base_path = self.note_path(folder_name, label)
        if not base_path.exists():
            return self.relative_path(base_path)

        safe_label = _safe_filename(label)
        folder_dir = self.folder_path(folder_name)
        folder_dir.mkdir(parents=True, exist_ok=True)

        for n in range(2, 100):
            candidate = folder_dir / f"{safe_label}_{n}.md"
            if not candidate.exists():
                return self.relative_path(candidate)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return self.relative_path(folder_dir / f"{safe_label}_{ts}.md")

    # ── Folder operations ────────────────────────────────────────────────────

    def create_folder(self, folder_name: str) -> Path:
        p = self.folder_path(folder_name)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def rename_folder(self, old_name: str, new_name: str) -> Path:
        old_p = self.folder_path(old_name)
        new_p = self.folder_path(new_name)
        if old_p.exists():
            old_p.rename(new_p)
        else:
            new_p.mkdir(parents=True, exist_ok=True)
        return new_p

    def delete_folder(self, folder_name: str) -> bool:
        p = self.folder_path(folder_name)
        if p.exists():
            shutil.rmtree(p)
            return True
        return False

    def list_folders(self) -> list[str]:
        access = get_access_health()
        try:
            # .exists() stats through the parent — on macOS without access
            # even that stat is denied, so it must sit inside the try.
            if not self.base_dir.exists():
                return []
            folders = sorted(
                d.name
                for d in self.base_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            )
        except PermissionError as e:
            access.record(
                NOTES_RESOURCE,
                Capability.ENUMERATE,
                ok=False,
                path=str(self.base_dir),
                errno=e.errno,
                error=str(e),
                op="listing note folders",
                source="file_manager",
            )
            return []
        access.record(
            NOTES_RESOURCE,
            Capability.ENUMERATE,
            ok=True,
            path=str(self.base_dir),
            op="listing note folders",
            source="file_manager",
        )
        return folders

    # ── Note file operations ─────────────────────────────────────────────────

    def write_note(
        self,
        folder_name: str,
        label: str,
        content: str,
        file_path: str | None = None,
    ) -> str:
        """Write note content to a .md file atomically.

        Uses write-to-temp + os.replace() so a crash or power failure mid-write
        never leaves the file in a partial/corrupt state.

        Returns the relative file_path for storage in the database.
        """
        if file_path:
            target = self.note_path_from_file_path(file_path)
        else:
            target = self.note_path(folder_name, label)

        with _observing_notes(Capability.REPLACE, f"writing note {target.name}", target):
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, content)
        return self.relative_path(target)

    def read_note(self, file_path: str) -> str | None:
        """Read note content from a .md file."""
        target = self.note_path_from_file_path(file_path)
        if target.is_file():
            return target.read_text(encoding="utf-8")
        return None

    def delete_note(self, file_path: str) -> bool:
        target = self.note_path_from_file_path(file_path)
        if target.is_file():
            target.unlink()
            return True
        return False

    def rename_note(self, old_file_path: str, new_folder: str, new_label: str) -> str:
        """Move/rename a note file atomically. Returns the new relative file_path.

        If the desired target path already exists (and is a *different* file than
        the source), a numeric suffix is appended to avoid silently overwriting
        another note.
        """
        old_target = self.note_path_from_file_path(old_file_path)
        new_target = self.note_path(new_folder, new_label)
        new_target.parent.mkdir(parents=True, exist_ok=True)

        if not old_target.is_file():
            # Source is gone — just return the intended target path.
            return self.relative_path(new_target)

        # If target exists and is a different file, find a non-colliding name.
        if new_target.exists() and not old_target.samefile(new_target):
            safe_label = _safe_filename(new_label)
            folder_dir = self.folder_path(new_folder)
            for n in range(2, 100):
                candidate = folder_dir / f"{safe_label}_{n}.md"
                if not candidate.exists():
                    new_target = candidate
                    break
            else:
                from datetime import datetime, timezone as _tz

                ts = datetime.now(_tz.utc).strftime("%Y%m%d_%H%M%S")
                new_target = folder_dir / f"{safe_label}_{ts}.md"

        os.replace(old_target, new_target)
        return self.relative_path(new_target)

    def note_hash(self, file_path: str) -> str | None:
        """Compute content hash for a local file."""
        content = self.read_note(file_path)
        if content is not None:
            return content_hash(content)
        return None

    def list_notes_in_folder(self, folder_name: str) -> list[dict[str, str]]:
        """List all .md files in a folder with their hashes."""
        folder = self.folder_path(folder_name)
        if not folder.is_dir():
            return []
        results = []
        try:
            for f in sorted(folder.iterdir()):
                if f.is_file() and f.suffix == ".md":
                    text = f.read_text(encoding="utf-8")
                    results.append(
                        {
                            "label": f.stem,
                            "file_path": self.relative_path(f),
                            "content_hash": content_hash(text),
                            "size": len(text),
                        }
                    )
        except PermissionError as e:
            get_access_health().record(
                NOTES_RESOURCE,
                Capability.ENUMERATE,
                ok=False,
                path=str(folder),
                errno=e.errno,
                error=str(e),
                op=f"listing notes in {folder.name}",
                source="file_manager",
            )
        return results

    def scan_all(self) -> list[dict[str, str]]:
        """Scan all .md files under the documents directory."""
        results: list[dict[str, str]] = []
        if not self.base_dir.exists():
            return results
        for root, dirs, files in os.walk(self.base_dir):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in sorted(files):
                if f.endswith(".md"):
                    fp = Path(root) / f
                    text = fp.read_text(encoding="utf-8")
                    results.append(
                        {
                            "label": fp.stem,
                            "file_path": self.relative_path(fp),
                            "content_hash": content_hash(text),
                            "folder": Path(root).relative_to(self.base_dir).as_posix(),
                        }
                    )
        return results

    # ── Conflict handling ────────────────────────────────────────────────────

    @property
    def _conflicts_dir(self) -> Path:
        return self.base_dir / ".sync" / "conflicts"

    def save_conflict(
        self,
        file_path: str,
        local_content: str,
        remote_content: str,
        note_id: str,
    ) -> str:
        """Save conflicting versions for manual resolution.

        First capture wins: if a conflict for this note is already filed,
        the snapshots are NOT overwritten — repeated detection passes (sync
        ticks) would otherwise keep re-capturing drifting content over the
        original divergence point. Returns the conflict directory path.
        """
        conflict_dir = self._conflicts_dir / note_id
        if (conflict_dir / "local.md").exists():
            return str(conflict_dir)
        conflict_dir.mkdir(parents=True, exist_ok=True)
        (conflict_dir / "local.md").write_text(local_content, encoding="utf-8")
        (conflict_dir / "remote.md").write_text(remote_content, encoding="utf-8")
        return str(conflict_dir)

    def list_conflicts(self) -> list[str]:
        """List note IDs that have unresolved conflicts."""
        try:
            # The .exists() stat itself is denied when the notes dir is
            # unreadable (macOS without Full Disk Access) — unguarded, that
            # PermissionError escaped through GET /notes/sync/status as a raw
            # 500 while degraded, the exact endpoint the UI's access prompt
            # depends on.
            if not self._conflicts_dir.exists():
                return []
            return [d.name for d in self._conflicts_dir.iterdir() if d.is_dir()]
        except PermissionError as e:
            get_access_health().record(
                NOTES_RESOURCE,
                Capability.ENUMERATE,
                ok=False,
                path=str(self._conflicts_dir),
                errno=e.errno,
                error=str(e),
                op="listing sync conflicts",
                source="file_manager",
            )
            return []

    def resolve_conflict(self, note_id: str) -> bool:
        """Remove a conflict directory after resolution."""
        conflict_dir = self._conflicts_dir / note_id
        if conflict_dir.exists():
            shutil.rmtree(conflict_dir)
            return True
        return False

    # ── Directory mappings (additional sync targets) ─────────────────────────

    def sync_to_mapped_dirs(
        self,
        file_path: str,
        mapped_paths: list[str],
        folder_id: str = "",
    ) -> list[str]:
        """Copy a canonical .md file to all mapped directories.

        Each mapped directory carries its OWN access-health resource: a dead
        external drive or read-only mapped folder degrades only that mapping,
        never the canonical notes health — and a healthy mapping's success
        never clears another mapping's (or the canonical dir's) failure.

        Returns list of successfully written paths.
        """
        source = self.note_path_from_file_path(file_path)
        if not source.is_file():
            return []

        content = source.read_text(encoding="utf-8")
        filename = source.name
        written: list[str] = []
        access = get_access_health()

        for mapped_dir in mapped_paths:
            target = Path(mapped_dir) / filename
            resource_id = mapping_resource_id(folder_id, mapped_dir)
            try:
                # Atomic temp+rename like the canonical note write, so a crash
                # mid-write can't leave a truncated/corrupt mapped-dir copy.
                if access.is_registered(resource_id):
                    with access.observing(
                        resource_id,
                        Capability.REPLACE,
                        op=f"syncing {filename} to mapped dir",
                        source="file_manager",
                        path=str(target),
                    ):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write(target, content)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(target, content)
                written.append(str(target))
            except Exception:
                # Best-effort per mapping: log + evidence (above), keep going.
                logger.warning(
                    "Failed to sync to mapped dir: %s", target, exc_info=True
                )

        return written

    # ── Sync state persistence ───────────────────────────────────────────────

    def _state_file(self) -> Path:
        return _notes_dir() / ".sync" / "state.json"

    def _mappings_file(self) -> Path:
        return _notes_dir() / ".sync" / "mappings.json"

    @staticmethod
    def _default_sync_state() -> dict[str, Any]:
        return {
            "last_sync_version": 0,
            "last_full_sync": None,
            "device_id": None,
            "note_hashes": {},
        }

    def load_sync_state(self) -> dict[str, Any]:
        _ensure_dirs()
        state_file = self._state_file()
        access = get_access_health()

        def _read_denied(e: PermissionError, op: str) -> None:
            access.record(
                NOTES_RESOURCE,
                Capability.READ,
                ok=False,
                path=str(state_file),
                errno=e.errno,
                error=str(e),
                op=op,
                source="file_manager",
            )

        try:
            is_file = state_file.is_file()
        except PermissionError as e:
            _read_denied(e, "checking sync state")
            return self._default_sync_state()
        if is_file:
            try:
                raw = state_file.read_text(encoding="utf-8")
            except PermissionError as e:
                # Permission denial is NOT corruption. Do not "reset" state —
                # there is nothing wrong with it; we simply can't read it.
                _read_denied(e, "reading sync state")
                return self._default_sync_state()
            except OSError:
                logger.debug(
                    "Could not read sync state (non-permission I/O error); "
                    "using defaults for this call",
                    exc_info=True,
                )
                return self._default_sync_state()
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                # Genuine corruption — invalid JSON on disk. This is the ONLY
                # case that warrants resetting.
                logger.warning("Corrupt sync state (invalid JSON), resetting")
                return self._default_sync_state()
            access.record(
                NOTES_RESOURCE,
                Capability.READ,
                ok=True,
                path=str(state_file),
                op="reading sync state",
                source="file_manager",
            )
            return state
        return self._default_sync_state()

    def save_sync_state(self, state: dict[str, Any]) -> None:
        _ensure_dirs()
        target = self._state_file()
        with _observing_notes(Capability.REPLACE, "saving sync state", target):
            _atomic_write(target, json.dumps(state, indent=2, default=str))

    def load_local_mappings(self) -> dict[str, list[str]]:
        """Load directory mappings config.

        Returns {folder_id: [local_path, ...]}.
        """
        _ensure_dirs()
        mappings_file = self._mappings_file()
        try:
            if mappings_file.is_file():
                return json.loads(mappings_file.read_text(encoding="utf-8"))
        except PermissionError as e:
            get_access_health().record(
                NOTES_RESOURCE,
                Capability.READ,
                ok=False,
                path=str(mappings_file),
                errno=e.errno,
                error=str(e),
                op="reading local mappings",
                source="file_manager",
            )
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def save_local_mappings(self, mappings: dict[str, list[str]]) -> None:
        _ensure_dirs()
        target = self._mappings_file()
        with _observing_notes(Capability.REPLACE, "saving local mappings", target):
            _atomic_write(target, json.dumps(mappings, indent=2))


# Register the canonical notes resource BEFORE the singleton's _ensure_dirs
# runs, so even import-time permission failures land as evidence.
register_notes_canonical()

# Module-level singleton
file_manager = DocumentFileManager()
