"""File sync engine — the desktop replica of the matrx-files cloud tree.

Same spine as the notes and chat engines (engine-owned background loop,
credentials from the persisted auth_tokens row, keyset cursor in sync_meta,
loud failures), with two user-choosable modes (docs/handoffs/file-sync-system.md):

- ``pointers`` — the full metadata tree lives locally (files.* mirror +
  zero-byte placeholder files under the Files root) and bytes hydrate on
  demand. The agent sees one uniform filesystem either way.
- ``full`` — every file's bytes are mirrored locally; bidirectional and
  offline-capable.
- ``off`` — the engine idles completely.

Doctrine (docs/SYNC_CONTRACT.md):
- Local write first; a failed network never blocks local work. Push
  intents queue in file_sync_state.pending_op and drain every cycle.
- Conflicts are NEVER destructive: both copies preserved
  (``.sync/conflicts/<file_id>/`` under the Files root), user resolves.
- Deletions are tombstones both ways (cloud ``deleted_at``; local files go
  to the OS trash via send2trash, never unlinked).
- ``last_synced_hash`` = the cloud checksum at the last SUCCESSFUL sync,
  written only after a push landed or a pull wrote the bytes.
- Echo suppression is state-based: a watcher event whose content hash
  equals the mirror checksum is our own pull landing, never a push.

Bytes move through the DownloadManager ONLY (category ``file_sync``) using
freshly-minted URL envelopes from the matrx-files API — never a second
downloader, never a hand-constructed URL.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import posixpath
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.services.file_sync.client import FileSyncHTTPError, MatrxFilesClient
from app.services.file_sync.index import (
    EMPTY_SHA256,
    LOCAL_ID_PREFIX,
    FileSyncIndex,
    new_local_id,
)
from app.services.local_db.database import get_db
from app.services.local_db.repositories import SyncMetaRepo, TokenRepo

logger = get_logger()

MODES = ("off", "pointers", "full")
DEFAULT_INTERVAL = int(os.getenv("MATRX_FILE_SYNC_INTERVAL", "300"))

_CURSOR_ENTITY = "files.files"
_FEED_PAGE_LIMIT = 500
_MAX_PAGES_PER_CYCLE = 20
_HYDRATE_BACKFILL_PER_CYCLE = 25
_HYDRATE_TIMEOUT_SECONDS = 300.0
_UPLOAD_MAX_BYTES = 512 * 1024 * 1024  # hard sanity ceiling for buffered uploads
_SYNC_ADMIN_DIR = ".sync"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _clean_rel_path(cloud_path: str) -> str | None:
    """Cloud file_path -> safe local relative path. Returns None (with the
    caller logging loudly) for anything that would escape the Files root."""
    parts = [p for p in cloud_path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    # Windows drive/ADS separators can re-anchor a path — never representable.
    if any(":" in p for p in parts):
        return None
    # Case-insensitive filesystems (macOS default, Windows) would let
    # ".SYNC/…" collide with the admin dir.
    if parts[0].casefold() == _SYNC_ADMIN_DIR:
        return None
    return "/".join(parts)


class FileSyncEngine:
    """Pull the change feed, reconcile the local replica, drain pushes."""

    def __init__(self, root: Path | None = None) -> None:
        self._root_override = root
        self._client = MatrxFilesClient()
        self._index = FileSyncIndex()
        self._meta = SyncMetaRepo()
        self._user_id: str | None = None
        self._sync_lock = asyncio.Lock()
        self._auto_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._auto_last_skip_reason: str | None = None
        self._interval = DEFAULT_INTERVAL
        self._last_cycle: dict[str, Any] = {}
        self._cursor: str | None = None
        self._cursor_loaded = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        if self._root_override is not None:
            return self._root_override
        from app.services.paths.manager import safe_dir

        return safe_dir("files")

    @property
    def mode(self) -> str:
        from app.services.cloud_sync.settings_sync import get_settings_sync

        raw = get_settings_sync().get("file_sync_mode", "pointers")
        if raw not in MODES:
            logger.error(
                "[file_sync] invalid file_sync_mode %r in settings — treating as 'off' "
                "(valid: %s)", raw, "|".join(MODES),
            )
            return "off"
        return str(raw)

    def configure(self, user_id: str, jwt: str) -> None:
        self._user_id = user_id
        self._client.set_jwt(jwt)

    @property
    def is_configured(self) -> bool:
        return bool(self._user_id and self._client.available)

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    async def sync_cycle(self) -> dict[str, Any]:
        if not self.is_configured:
            raise RuntimeError("file sync engine not configured (no user/JWT)")
        async with self._sync_lock:
            folders = await self._pull_folders()
            pulled = await self._pull_changes()
            pushed = await self._drain_pending()
            recaptured = await self._retry_conflict_captures()
            hydrated = 0
            if self.mode == "full":
                hydrated = await self._backfill_hydration()
        summary = {
            "mode": self.mode,
            "folders": folders,
            "pulled": pulled,
            "pushed": pushed,
            "conflict_captures_retried": recaptured,
            "hydration_enqueued": hydrated,
            "at": _now_iso(),
        }
        self._last_cycle = summary
        return summary

    # ------------------------------------------------------------------
    # Pull — folders
    # ------------------------------------------------------------------

    async def _pull_folders(self) -> dict[str, Any]:
        try:
            payload = await self._client.sync_folders(include_deleted=True)
        except FileSyncHTTPError as exc:
            logger.error("[file_sync] PULL FAILED for files.folders — %s", exc)
            return {"error": exc.status_code}
        if payload.get("truncated"):
            logger.error(
                "[file_sync] the folder listing was TRUNCATED by the service — "
                "the local folder tree is incomplete until the feed grows a cursor"
            )
        applied = 0
        for entry in payload.get("folders", []):
            await self._index.upsert_remote_folder(entry)
            if not entry.get("deleted_at") and not entry.get("is_system"):
                rel = _clean_rel_path(entry.get("folder_path") or "")
                if rel is None:
                    logger.error(
                        "[file_sync] cloud folder path %r is not representable locally — skipped",
                        entry.get("folder_path"),
                    )
                    continue
                (self.root / rel).mkdir(parents=True, exist_ok=True)
            applied += 1
        await get_db().commit()
        return {"applied": applied}

    # ------------------------------------------------------------------
    # Pull — the change feed
    # ------------------------------------------------------------------

    async def _load_cursor(self) -> str | None:
        if not self._cursor_loaded:
            meta = await self._meta.get_last_sync(_CURSOR_ENTITY)
            self._cursor = (meta or {}).get("last_hash") or None
            self._cursor_loaded = True
        return self._cursor

    async def _save_cursor(self, cursor: str | None, *, status: str, error: str | None = None) -> None:
        self._cursor = cursor
        await self._meta.set_last_sync(
            _CURSOR_ENTITY, status=status, last_hash=cursor, error_message=error
        )

    async def _pull_changes(self) -> dict[str, Any]:
        cursor = await self._load_cursor()
        applied = tombstones = conflicts = pages = 0
        try:
            while pages < _MAX_PAGES_PER_CYCLE:
                payload = await self._client.sync_changes(
                    cursor=cursor, limit=_FEED_PAGE_LIMIT
                )
                entries = payload.get("files", [])
                pages += 1
                for entry in entries:
                    kind = await self._apply_remote_entry(entry)
                    if kind == "tombstone":
                        tombstones += 1
                    elif kind == "conflict":
                        conflicts += 1
                    else:
                        applied += 1
                await get_db().commit()
                cursor = payload.get("next_cursor") or cursor
                await self._save_cursor(cursor, status="success")
                if not payload.get("has_more"):
                    break
        except FileSyncHTTPError as exc:
            logger.error("[file_sync] PULL FAILED for files.files — %s", exc)
            await self._save_cursor(cursor, status="error", error=str(exc))
            return {"error": exc.status_code, "applied": applied}
        if pages >= _MAX_PAGES_PER_CYCLE:
            logger.warning(
                "[file_sync] pull hit the %d-page cap this cycle — more rows remain; "
                "the next cycle continues from the checkpoint", _MAX_PAGES_PER_CYCLE,
            )
        return {
            "applied": applied,
            "tombstones": tombstones,
            "conflicts": conflicts,
            "pages": pages,
        }

    async def _apply_remote_entry(self, entry: dict[str, Any]) -> str:
        """Reconcile one feed entry against the mirror + disk. Returns
        'applied' | 'tombstone' | 'conflict'."""
        file_id = str(entry.get("file_id") or "")
        if not file_id:
            logger.error("[file_sync] feed entry missing file_id — skipped: %r", entry)
            return "applied"
        await self._index.upsert_remote_file(entry)

        state = await self._index.get_state(file_id)
        rel = _clean_rel_path(entry.get("file_path") or "")
        if rel is not None:
            # Belt-and-suspenders containment: the joined path must resolve
            # inside the Files root (symlinked segments could still escape).
            root = self.root.resolve()
            try:
                contained = (root / rel).resolve().is_relative_to(root)
            except OSError:
                contained = False
            if not contained:
                rel = None
        if rel is None:
            logger.error(
                "[file_sync] cloud path %r (id=%s) is not representable locally — "
                "index updated, disk untouched", entry.get("file_path"), file_id,
            )
            return "applied"

        if entry.get("deleted_at"):
            return await self._apply_remote_tombstone(file_id, rel, state)

        checksum = entry.get("checksum")
        if state is None:
            return await self._adopt_remote_file(file_id, rel, entry)

        # Remote rename/move: relocate the local file or placeholder.
        if state["rel_path"] != rel:
            await self._relocate_local(state, rel)
            state = await self._index.get_state(file_id) or state

        if state["local_state"] == "pointer":
            # Not hydrated — the mirror row (already updated) IS the sync.
            return "applied"

        if checksum and checksum == state.get("last_synced_hash"):
            # Remote content unchanged relative to what we synced; any local
            # divergence stays queued as a push.
            return "applied"

        # Remote content changed while a local delete is queued: the edit
        # wins over the delete (notes doctrine) — drop the delete and fall
        # back to a pointer so the new content hydrates on access/backfill.
        if state.get("pending_op") == "delete":
            logger.warning(
                "[file_sync] %s changed in cloud while a local delete was queued — "
                "the remote edit wins; delete dropped", state["rel_path"],
            )
            await self._ensure_placeholder(state["rel_path"])
            await self._index.upsert_state(
                file_id,
                local_state="pointer",
                pending_op=None,
                local_hash=EMPTY_SHA256,
                local_size=0,
                last_synced_hash=None,
            )
            return "applied"

        # Remote content changed. Locally modified too?
        abs_path = self.root / state["rel_path"]
        locally_modified = await self._is_locally_modified(state, abs_path)
        if locally_modified:
            await self._capture_conflict(file_id, state, entry)
            return "conflict"
        # Clean local copy — re-hydrate to the new content.
        try:
            await self._hydrate(file_id, force=True)
        except Exception as exc:
            logger.error(
                "[file_sync] re-hydration of %s (%s) failed: %s — will retry next cycle",
                state["rel_path"], file_id, exc,
            )
        return "applied"

    async def _adopt_remote_file(
        self, file_id: str, rel: str, entry: dict[str, Any]
    ) -> str:
        """First sight of a cloud file: adopt whatever already occupies its
        local path instead of blindly claiming it as an empty pointer —
        pre-existing local content must never be misrecorded as a
        placeholder (that misrecording is what let hydration overwrite it)."""
        checksum = entry.get("checksum")
        abs_path = self.root / rel

        # A local-born row already holds this path (created offline / watcher).
        existing = await self._index.get_state_by_path(rel)
        if existing is not None and existing["file_id"] != file_id:
            if not existing["file_id"].startswith(LOCAL_ID_PREFIX):
                logger.error(
                    "[file_sync] cloud files %s and %s both claim local path %s — "
                    "index updated for the feed row only; disk untouched",
                    existing["file_id"], file_id, rel,
                )
                return "applied"
            local_hash = existing.get("local_hash")
            await self._index.delete_state(existing["file_id"])
            if abs_path.exists():
                current, size = await asyncio.to_thread(_sha256_file, abs_path)
                if checksum and current == checksum:
                    await self._index.upsert_state(
                        file_id, rel_path=rel, local_state="synced",
                        pending_op=None, local_hash=current, local_size=size,
                        local_mtime=abs_path.stat().st_mtime,
                        last_synced_hash=checksum,
                    )
                    return "applied"
                # Same path, different content on both sides: conflict.
                await self._index.upsert_state(
                    file_id, rel_path=rel, local_state="pending_push",
                    local_hash=current, local_size=size,
                    last_synced_hash=None,
                )
                await self._capture_conflict(
                    file_id,
                    {"file_id": file_id, "rel_path": rel, "local_state": "pending_push"},
                    entry,
                )
                return "conflict"
            # Row without a file (pending upload of a gone file) — fall through.

        if abs_path.exists() and abs_path.is_file():
            size = abs_path.stat().st_size
            if size > 0:
                current, size = await asyncio.to_thread(_sha256_file, abs_path)
                if checksum and current == checksum:
                    await self._index.upsert_state(
                        file_id, rel_path=rel, local_state="synced",
                        pending_op=None, local_hash=current, local_size=size,
                        local_mtime=abs_path.stat().st_mtime,
                        last_synced_hash=checksum,
                    )
                    return "applied"
                await self._index.upsert_state(
                    file_id, rel_path=rel, local_state="pending_push",
                    local_hash=current, local_size=size,
                    last_synced_hash=None,
                )
                await self._capture_conflict(
                    file_id,
                    {"file_id": file_id, "rel_path": rel, "local_state": "pending_push"},
                    entry,
                )
                return "conflict"

        # Clean adoption: nothing (or an empty file) on disk — a pointer.
        await self._ensure_placeholder(rel)
        await self._index.upsert_state(
            file_id,
            rel_path=rel,
            local_state="pointer",
            pending_op=None,
            local_hash=EMPTY_SHA256,
            local_size=0,
            last_synced_hash=None,
        )
        return "applied"

    async def _apply_remote_tombstone(
        self, file_id: str, rel: str, state: dict[str, Any] | None
    ) -> str:
        if state is None:
            return "tombstone"
        abs_path = self.root / state["rel_path"]
        if abs_path.exists():
            if await self._is_locally_modified(state, abs_path):
                # Edit-vs-remote-delete: the local edit survives as a fresh
                # local file pending upload (notes-engine resurrect rule).
                logger.warning(
                    "[file_sync] %s deleted in cloud but modified locally — "
                    "keeping local copy and re-uploading (edit wins over delete)",
                    state["rel_path"],
                )
                new_id = new_local_id()
                await self._index.delete_state(file_id)
                local_hash, size = await asyncio.to_thread(_sha256_file, abs_path)
                await self._index.upsert_state(
                    new_id,
                    rel_path=state["rel_path"],
                    local_state="pending_push",
                    pending_op="upload",
                    local_hash=local_hash,
                    local_size=size,
                )
                return "conflict"
            try:
                from send2trash import send2trash

                await asyncio.to_thread(send2trash, str(abs_path))
            except Exception as exc:
                logger.error(
                    "[file_sync] could not trash %s for a remote tombstone: %s — "
                    "file left in place, state kept for retry", abs_path, exc,
                )
                return "tombstone"
        await self._index.delete_state(file_id)
        return "tombstone"

    async def _is_locally_modified(self, state: dict[str, Any], abs_path: Path) -> bool:
        """Provably-unmodified check (doctrine: unprovable ⇒ modified)."""
        if not abs_path.exists():
            return False
        if state["local_state"] == "pointer":
            # A placeholder that gained content is a local edit.
            try:
                return abs_path.stat().st_size > 0
            except OSError:
                return True
        last = state.get("last_synced_hash")
        if not last:
            return True
        current, _ = await asyncio.to_thread(_sha256_file, abs_path)
        return current != last

    async def _relocate_local(self, state: dict[str, Any], new_rel: str) -> None:
        old_abs = self.root / state["rel_path"]
        new_abs = self.root / new_rel
        try:
            if old_abs.exists():
                new_abs.parent.mkdir(parents=True, exist_ok=True)
                if new_abs.exists():
                    logger.error(
                        "[file_sync] remote move target %s already exists locally — "
                        "leaving both; index now points at the target", new_rel,
                    )
                else:
                    await asyncio.to_thread(os.replace, str(old_abs), str(new_abs))
        except OSError as exc:
            logger.error(
                "[file_sync] could not relocate %s -> %s: %s",
                state["rel_path"], new_rel, exc,
            )
            return
        await self._index.upsert_state(state["file_id"], rel_path=new_rel)

    async def _capture_conflict(
        self, file_id: str, state: dict[str, Any], entry: dict[str, Any]
    ) -> None:
        """Both sides changed: keep the local copy, land the remote copy in
        .sync/conflicts/<file_id>/, mark the state row."""
        conflicts_dir = self.root / _SYNC_ADMIN_DIR / "conflicts" / file_id
        conflicts_dir.mkdir(parents=True, exist_ok=True)
        remote_name = entry.get("file_name") or posixpath.basename(entry.get("file_path") or file_id)
        # Snapshot the LOCAL copy too (first-capture-wins, like the notes
        # engine) so a later keep_remote can never erase the local edit.
        local_abs = self.root / state["rel_path"]
        local_copy = conflicts_dir / f"local_{remote_name}"
        if local_abs.exists() and not local_copy.exists():
            try:
                import shutil

                await asyncio.to_thread(shutil.copy2, str(local_abs), str(local_copy))
            except OSError as exc:
                logger.error(
                    "[file_sync] could not snapshot the local copy of %s into the "
                    "conflict capture: %s", state["rel_path"], exc,
                )
        remote_copy = conflicts_dir / f"remote_{remote_name}"
        try:
            await self._download_to(file_id, remote_copy, expected_checksum=entry.get("checksum"))
        except Exception as exc:
            logger.error(
                "[file_sync] CONFLICT on %s but the remote copy could not be fetched: %s "
                "— conflict recorded; remote copy will be retried next cycle",
                state["rel_path"], exc,
            )
        # pending_op MUST clear: a queued upload draining after this would be
        # a silent keep_local (last-writer-wins on user content — forbidden).
        await self._index.upsert_state(
            file_id, local_state="conflict", pending_op=None,
            error="remote_and_local_both_changed",
        )
        logger.warning(
            "[file_sync] CONFLICT: %s changed locally AND in the cloud — both copies "
            "preserved (%s)", state["rel_path"], conflicts_dir,
        )

    async def _retry_conflict_captures(self, limit: int = 5) -> int:
        """A conflict whose remote copy failed to download retries here each
        cycle until the capture exists (the 'retried next cycle' promise)."""
        conflicts = await self._index.list_by_state("conflict", limit=100)
        retried = 0
        for state in conflicts:
            file_id = state["file_id"]
            conflicts_dir = self.root / _SYNC_ADMIN_DIR / "conflicts" / file_id
            has_remote = conflicts_dir.exists() and any(
                p.name.startswith("remote_") for p in conflicts_dir.iterdir()
            )
            if has_remote:
                continue
            if retried >= limit:
                break
            remote = await self._index.get_remote_file(file_id)
            if not remote or remote.get("deleted_at"):
                continue
            name = remote.get("file_name") or posixpath.basename(remote.get("file_path") or file_id)
            try:
                conflicts_dir.mkdir(parents=True, exist_ok=True)
                await self._download_to(
                    file_id, conflicts_dir / f"remote_{name}",
                    expected_checksum=remote.get("checksum"),
                )
                retried += 1
            except Exception as exc:
                logger.error(
                    "[file_sync] conflict remote-capture retry failed for %s: %s",
                    state["rel_path"], exc,
                )
        return retried

    # ------------------------------------------------------------------
    # Placeholders + hydration
    # ------------------------------------------------------------------

    async def _ensure_placeholder(self, rel: str) -> None:
        abs_path = self.root / rel
        if abs_path.exists():
            return
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(abs_path.touch)

    async def _download_to(
        self, file_id: str, dest: Path, *, expected_checksum: str | None
    ) -> None:
        """Fetch one file's bytes through the DownloadManager into ``dest``."""
        from app.services.downloads.manager import get_download_manager

        envelope = await self._client.get_url_envelope(file_id)
        url = envelope.get("download_url") or envelope.get("url") or envelope.get("cdn_url")
        if not url:
            raise RuntimeError(f"matrx-files returned no usable URL for {file_id}")
        manager = get_download_manager()
        dest.parent.mkdir(parents=True, exist_ok=True)
        # The checksum is part of the idempotency key: the manager treats a
        # completed entry with an existing dest as done, so re-hydrating an
        # UPDATED file must present a new key or it would keep stale bytes.
        content_key = (expected_checksum or "na")[:12]
        entry = await manager.enqueue(
            category="file_sync",
            filename=f"{file_id}:{content_key}:{dest.name}",
            display_name=str(dest.relative_to(self.root) if dest.is_relative_to(self.root) else dest.name),
            urls=[url],
            metadata={"dest_dir": str(dest.parent), "dest_filename": dest.name, "file_id": file_id},
            priority=10,
        )
        deadline = asyncio.get_event_loop().time() + _HYDRATE_TIMEOUT_SECONDS
        while True:
            current = next((e for e in manager.get_all() if e.id == entry.id), None)
            status = current.status if current else "failed"
            if status == "completed":
                break
            if status in ("failed", "cancelled"):
                raise RuntimeError(
                    f"download of {file_id} {status}: {getattr(current, 'error_msg', None)}"
                )
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"download of {file_id} did not finish in {_HYDRATE_TIMEOUT_SECONDS:.0f}s")
            await asyncio.sleep(0.3)
        if expected_checksum:
            got, _ = await asyncio.to_thread(_sha256_file, dest)
            if got != expected_checksum:
                raise RuntimeError(
                    f"checksum mismatch after download of {file_id}: expected "
                    f"{expected_checksum[:12]}…, got {got[:12]}…"
                )

    async def hydrate(self, file_id_or_path: str, *, priority: int = 10) -> Path:
        """Public entry point (tools, REST): serialized against the sync
        cycle so a pull can never interleave with an on-demand fetch."""
        async with self._sync_lock:
            return await self._hydrate(file_id_or_path, priority=priority)

    async def _hydrate(
        self, file_id_or_path: str, *, priority: int = 10, force: bool = False
    ) -> Path:
        """Fetch real bytes for a pointer (or stale) file and return its
        absolute path. Safe to call on an already-hydrated file (no-op).
        NEVER overwrites local modifications — a pointer that gained content
        (or a hydrated file that diverged) becomes a conflict, not a loss.
        Callers must hold ``_sync_lock``."""
        if not self.is_configured:
            await self._configure_from_token()
        state = await self._index.get_state(file_id_or_path)
        if state is None:
            state = await self._index.get_state_by_path(file_id_or_path)
        if state is None:
            raise FileNotFoundError(f"no synced file matches {file_id_or_path!r}")
        file_id = state["file_id"]
        if file_id.startswith(LOCAL_ID_PREFIX):
            return self.root / state["rel_path"]  # born local; bytes already here
        remote = await self._index.get_remote_file(file_id)
        checksum = (remote or {}).get("checksum")
        abs_path = self.root / state["rel_path"]
        if not force and state["local_state"] in ("synced", "pending_push", "conflict") and abs_path.exists():
            if state.get("last_synced_hash") == checksum or state["local_state"] != "synced":
                return abs_path
        # The download replaces abs_path atomically — so anything the user
        # put there must be conflict-captured FIRST, never overwritten.
        if not force and await self._is_locally_modified(state, abs_path):
            await self._capture_conflict(
                file_id, state, remote or {"file_path": state["rel_path"]}
            )
            await get_db().commit()
            raise RuntimeError(
                f"{state['rel_path']} has local changes AND newer cloud content — "
                "captured as a conflict (.sync/conflicts); resolve it instead of hydrating"
            )
        await self._download_to(file_id, abs_path, expected_checksum=checksum)
        local_hash, size = await asyncio.to_thread(_sha256_file, abs_path)
        await self._index.upsert_state(
            file_id,
            local_state="synced",
            pending_op=None,
            local_hash=local_hash,
            local_size=size,
            local_mtime=abs_path.stat().st_mtime,
            last_synced_hash=checksum or local_hash,
            error=None,
        )
        await get_db().commit()
        return abs_path

    async def _backfill_hydration(self) -> int:
        """Full mode: steadily hydrate pointer entries, bounded per cycle."""
        pointers = await self._index.list_by_state("pointer", limit=_HYDRATE_BACKFILL_PER_CYCLE)
        count = 0
        for state in pointers:
            try:
                await self._hydrate(state["file_id"])
                count += 1
            except Exception as exc:
                logger.error(
                    "[file_sync] backfill hydration failed for %s: %s",
                    state["rel_path"], exc,
                )
                await self._index.upsert_state(state["file_id"], error=str(exc)[:300])
        await get_db().commit()
        return count

    # ------------------------------------------------------------------
    # Push — drain pending local operations
    # ------------------------------------------------------------------

    async def _drain_pending(self) -> dict[str, Any]:
        pending = await self._index.list_pending()
        sent = failed = 0
        for state in pending:
            op = state["pending_op"]
            if state["local_state"] == "conflict":
                # Defense in depth: a conflicted row must never auto-push —
                # that would be silent last-writer-wins on user content.
                logger.error(
                    "[file_sync] conflicted row %s still had pending_op=%r — "
                    "clearing; the user resolves conflicts", state["rel_path"], op,
                )
                await self._index.upsert_state(state["file_id"], pending_op=None)
                continue
            try:
                if op == "upload":
                    await self._push_upload(state)
                elif op == "delete":
                    await self._push_delete(state)
                elif op == "move":
                    await self._push_move(state)
                else:
                    logger.error(
                        "[file_sync] unknown pending_op %r on %s — clearing",
                        op, state["rel_path"],
                    )
                    await self._index.upsert_state(state["file_id"], pending_op=None)
                sent += 1
            except FileSyncHTTPError as exc:
                failed += 1
                await self._index.upsert_state(state["file_id"], error=str(exc)[:300])
                if exc.is_auth:
                    logger.error(
                        "[file_sync] PUSH ABORTED — cloud rejected our JWT (HTTP %s). "
                        "Waiting for a fresh token.", exc.status_code,
                    )
                    break
                logger.error("[file_sync] PUSH FAILED %s %s: %s", op, state["rel_path"], exc)
            except Exception as exc:
                failed += 1
                await self._index.upsert_state(state["file_id"], error=str(exc)[:300])
                logger.error("[file_sync] PUSH FAILED %s %s: %s", op, state["rel_path"], exc)
        await get_db().commit()
        return {"sent": sent, "failed": failed}

    async def _push_upload(self, state: dict[str, Any]) -> None:
        abs_path = self.root / state["rel_path"]
        if not abs_path.exists():
            logger.warning(
                "[file_sync] pending upload for %s but the file is gone — "
                "converting to delete", state["rel_path"],
            )
            if state["file_id"].startswith(LOCAL_ID_PREFIX):
                await self._index.delete_state(state["file_id"])
            else:
                await self._index.upsert_state(state["file_id"], pending_op="delete")
            return
        size = abs_path.stat().st_size
        if size > _UPLOAD_MAX_BYTES:
            raise RuntimeError(
                f"{state['rel_path']} is {size / 1e9:.2f} GB — beyond the buffered-upload "
                f"ceiling ({_UPLOAD_MAX_BYTES / 1e9:.1f} GB); presigned uploads are a "
                "planned follow-up"
            )
        def _read_and_hash() -> tuple[bytes, str]:
            data = abs_path.read_bytes()
            return data, hashlib.sha256(data).hexdigest()

        content, local_hash = await asyncio.to_thread(_read_and_hash)
        resp = await self._client.upload(
            file_path=state["rel_path"],
            content=content,
            filename=abs_path.name,
        )
        cloud_id = str(resp.get("file_id") or "")
        checksum = resp.get("checksum") or local_hash
        if not cloud_id:
            raise RuntimeError(f"upload of {state['rel_path']} returned no file_id")
        if state["file_id"] != cloud_id:
            await self._index.rekey_state(state["file_id"], cloud_id)
        # Echo the cloud record into the mirror so the next pull doesn't see
        # our own push as a foreign change.
        try:
            record = await self._client.get_record(cloud_id)
            await self._index.upsert_remote_file(
                {
                    "file_id": record.get("file_id"),
                    "file_path": record.get("file_path"),
                    "file_name": record.get("file_name"),
                    "mime_type": record.get("mime_type"),
                    "size_bytes": record.get("size_bytes"),
                    "checksum": checksum,
                    "visibility": record.get("visibility"),
                    "version": record.get("version"),
                    "folder_id": record.get("folder_id"),
                    "created_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                    "deleted_at": record.get("deleted_at"),
                }
            )
        except FileSyncHTTPError as exc:
            logger.warning("[file_sync] post-upload record fetch failed (%s) — the next pull realigns", exc)
        # Guarded completion: if the file changed again locally while the
        # upload was in flight (the watcher re-stamped local_hash), the fresh
        # edit must stay pending — an unconditional flip to 'synced' would
        # orphan it as never-pushed.
        finalized = await self._index.finalize_if_hash_unchanged(
            cloud_id,
            expected_local_hash=local_hash,
            local_state="synced",
            pending_op=None,
            local_size=size,
            local_mtime=abs_path.stat().st_mtime,
            last_synced_hash=checksum,
            error=None,
        )
        if not finalized:
            # The row moved on mid-upload; record what DID land so the next
            # drain pushes only the delta.
            await self._index.upsert_state(cloud_id, last_synced_hash=checksum)
            logger.info(
                "[file_sync] %s changed again during upload — the newer edit stays queued",
                state["rel_path"],
            )

    async def _push_delete(self, state: dict[str, Any]) -> None:
        if state["file_id"].startswith(LOCAL_ID_PREFIX):
            await self._index.delete_state(state["file_id"])
            return
        try:
            await self._client.soft_delete(state["file_id"])
        except FileSyncHTTPError as exc:
            if exc.status_code == 404:
                logger.info("[file_sync] delete of %s: already gone in cloud", state["rel_path"])
            else:
                raise
        await self._index.delete_state(state["file_id"])

    async def _push_move(self, state: dict[str, Any]) -> None:
        if state["file_id"].startswith(LOCAL_ID_PREFIX):
            # Never uploaded — a move is irrelevant; upload at the new path.
            await self._index.upsert_state(state["file_id"], pending_op="upload")
            return
        rel = state["rel_path"]
        folder = posixpath.dirname(rel)
        name = posixpath.basename(rel)
        await self._client.patch(state["file_id"], name=name, folder=folder)
        abs_path = self.root / rel
        local_hash = state.get("local_hash")
        await self._index.upsert_state(
            state["file_id"],
            pending_op=None,
            local_state="synced" if abs_path.exists() and (abs_path.stat().st_size or 0) > 0 else state["local_state"],
            error=None,
        )
        # Refresh the mirror row (path/updated_at changed cloud-side).
        try:
            record = await self._client.get_record(state["file_id"])
            await self._index.upsert_remote_file(
                {**record, "version": record.get("version"), "folder_id": record.get("folder_id")}
            )
        except FileSyncHTTPError:
            pass  # next pull realigns

    # ------------------------------------------------------------------
    # Watcher — local changes become push intents
    # ------------------------------------------------------------------

    @property
    def watcher_active(self) -> bool:
        return self._watch_task is not None and not self._watch_task.done()

    async def start_watcher(self) -> None:
        if self.watcher_active:
            return
        self._watch_task = asyncio.create_task(self._watch_loop(), name="file-sync-watcher")

    async def stop_watcher(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

    async def _watch_loop(self) -> None:
        try:
            import watchfiles
        except ImportError:
            logger.error("[file_sync] watchfiles missing — local edits will only be "
                         "detected by full rescans (none scheduled); fix the environment")
            return
        root = self.root
        root.mkdir(parents=True, exist_ok=True)
        logger.info("[file_sync] watcher started on %s", root)
        try:
            async for changes in watchfiles.awatch(
                str(root), recursive=True, stop_event=self._stop_event
            ):
                # Debounce editor save patterns (delete-then-rename).
                await asyncio.sleep(0.5)
                # Deletes first so a same-batch delete+add pair is seen as a
                # rename (find_pending_delete_by_hash needs the delete row).
                ordered = sorted(
                    changes,
                    key=lambda c: 0 if c[0] == watchfiles.Change.deleted else 1,
                )
                for change_type, change_path in ordered:
                    try:
                        await self._handle_watch_event(change_type, change_path)
                    except Exception:
                        logger.error(
                            "[file_sync] watcher event crashed for %s", change_path,
                            exc_info=True,
                        )
                await get_db().commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("[file_sync] watcher crashed — local edits are no longer "
                         "being detected", exc_info=True)

    async def _handle_watch_event(self, change_type: Any, change_path: str) -> None:
        import watchfiles

        path = Path(change_path)
        try:
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            return
        if rel.startswith(f"{_SYNC_ADMIN_DIR}/") or rel == _SYNC_ADMIN_DIR:
            return
        if path.is_dir():
            return
        # The DownloadManager streams to "<name>.part" then renames — a
        # partial download must never classify as a local edit/new file.
        if path.name.endswith(".part"):
            return

        state = await self._index.get_state_by_path(rel)

        if change_type == watchfiles.Change.deleted:
            if state is None:
                return
            if state["local_state"] == "conflict":
                # The unseen remote copy must survive a local delete of a
                # conflicted file — the conflict stays open for the user.
                logger.warning(
                    "[file_sync] conflicted file %s was deleted locally — the "
                    "conflict stays open (.sync/conflicts holds both copies)",
                    rel,
                )
                return
            if state["file_id"].startswith(LOCAL_ID_PREFIX):
                await self._index.delete_state(state["file_id"])
                return
            # Deleting the placeholder/file locally = deleting it in the tree.
            await self._index.upsert_state(
                state["file_id"], local_state="pending_push", pending_op="delete"
            )
            return

        if not path.exists():
            return
        try:
            local_hash, size = await asyncio.to_thread(_sha256_file, path)
            mtime = path.stat().st_mtime
        except OSError:
            return

        if state is not None:
            remote = await self._index.get_remote_file(state["file_id"]) if not state[
                "file_id"
            ].startswith(LOCAL_ID_PREFIX) else None
            if remote and remote.get("checksum") == local_hash:
                # Our own pull landing (or content already in the cloud) —
                # never a push.
                await self._index.upsert_state(
                    state["file_id"],
                    local_state="synced",
                    pending_op=None,
                    local_hash=local_hash,
                    local_size=size,
                    local_mtime=mtime,
                    last_synced_hash=remote.get("checksum"),
                )
                return
            if state["local_state"] == "pointer" and size == 0:
                return  # placeholder churn
            if local_hash == state.get("local_hash") and state.get("pending_op") is None:
                return  # no real change
            if state["local_state"] == "conflict":
                # Track the new bytes but never re-arm a push — a conflicted
                # row only moves through resolve_conflict.
                await self._index.upsert_state(
                    state["file_id"],
                    local_hash=local_hash,
                    local_size=size,
                    local_mtime=mtime,
                )
                return
            await self._index.upsert_state(
                state["file_id"],
                local_state="pending_push",
                pending_op="upload",
                local_hash=local_hash,
                local_size=size,
                local_mtime=mtime,
            )
            return

        # Unknown path: rename detection first, else a brand-new local file.
        moved = await self._index.find_pending_delete_by_hash(local_hash)
        if moved is not None:
            await self._index.upsert_state(
                moved["file_id"],
                rel_path=rel,
                local_state="pending_push",
                pending_op="move",
                local_hash=local_hash,
                local_size=size,
                local_mtime=mtime,
            )
            return
        await self._index.upsert_state(
            new_local_id(),
            rel_path=rel,
            local_state="pending_push",
            pending_op="upload",
            local_hash=local_hash,
            local_size=size,
            local_mtime=mtime,
        )

    # ------------------------------------------------------------------
    # Conflicts — resolution
    # ------------------------------------------------------------------

    async def list_conflicts(self) -> list[dict[str, Any]]:
        return await self._index.list_by_state("conflict")

    async def resolve_conflict(self, file_id: str, resolution: str) -> dict[str, Any]:
        """keep_local → push the local copy; keep_remote → hydrate the cloud
        copy over it. Both leave the .sync/conflicts capture in place until
        it succeeds."""
        async with self._sync_lock:
            state = await self._index.get_state(file_id)
            if state is None or state["local_state"] != "conflict":
                raise FileNotFoundError(f"no open conflict for {file_id}")
            if resolution == "keep_local":
                abs_path = self.root / state["rel_path"]
                if not abs_path.exists():
                    raise FileNotFoundError(f"local copy of {state['rel_path']} is gone")
                await self._index.upsert_state(
                    file_id, local_state="pending_push", pending_op="upload", error=None
                )
                result = await self._drain_pending()
            elif resolution == "keep_remote":
                # No pre-flip: the row stays 'conflict' until the forced
                # hydration SUCCEEDS (its success path writes synced +
                # last_synced_hash). A failed download leaves the conflict
                # open instead of stranding a mislabeled 'synced' row.
                await self._hydrate(file_id, force=True)
                result = {"hydrated": True}
            else:
                raise ValueError(f"unknown resolution {resolution!r} (keep_local|keep_remote)")
            await get_db().commit()
            return {"file_id": file_id, "resolution": resolution, **result}

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    @property
    def auto_sync_active(self) -> bool:
        return self._auto_task is not None and not self._auto_task.done()

    async def start_background_sync(self, interval_seconds: int | None = None) -> None:
        if self.auto_sync_active:
            return
        self._interval = interval_seconds or DEFAULT_INTERVAL
        self._stop_event.clear()
        self._auto_task = asyncio.create_task(
            self._auto_sync_loop(self._interval), name="file-sync-auto"
        )
        logger.info("[file_sync] auto-sync started (interval=%ss)", self._interval)

    async def stop_background_sync(self) -> None:
        self._stop_event.set()
        await self.stop_watcher()
        if self._auto_task:
            self._auto_task.cancel()
            try:
                await self._auto_task
            except asyncio.CancelledError:
                pass
            self._auto_task = None
        logger.info("[file_sync] auto-sync stopped")

    async def _auto_sync_loop(self, interval_seconds: int) -> None:
        while not self._stop_event.is_set():
            try:
                await self._auto_sync_tick()
            except Exception:
                logger.warning("[file_sync] auto-sync tick crashed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _configure_from_token(self) -> bool:
        repo = TokenRepo()
        row = await repo.get()
        if not row or not row.get("access_token") or not row.get("user_id"):
            return False
        if repo.is_expired(row):
            return False
        self.configure(row["user_id"], row["access_token"])
        return True

    async def _auto_sync_tick(self) -> None:
        mode = self.mode
        if mode == "off":
            if self.watcher_active:
                await self.stop_watcher()
            if self._auto_last_skip_reason != "off":
                logger.info("[file_sync] idle — file_sync_mode is 'off'")
                self._auto_last_skip_reason = "off"
            return
        if not await self._configure_from_token():
            reason = "no_token"
            if self._auto_last_skip_reason != reason:
                logger.info("[file_sync] idle — no valid signed-in user (will retry each tick)")
                self._auto_last_skip_reason = reason
            return
        self._auto_last_skip_reason = None
        if not self.watcher_active:
            await self.start_watcher()
        await self.sync_cycle()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        counts = await self._index.counts()
        meta = await self._meta.get_last_sync(_CURSOR_ENTITY)
        return {
            "mode": self.mode,
            "root": str(self.root),
            "configured": self.is_configured,
            "auto_sync_active": self.auto_sync_active,
            "watcher_active": self.watcher_active,
            "interval_seconds": self._interval,
            "counts": counts,
            "cursor": (meta or {}).get("last_hash"),
            "last_sync_status": (meta or {}).get("status"),
            "last_sync_error": (meta or {}).get("error_message"),
            "last_cycle": self._last_cycle,
        }


_ENGINE: FileSyncEngine | None = None


def get_file_sync_engine() -> FileSyncEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = FileSyncEngine()
    return _ENGINE
