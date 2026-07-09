"""Notes sync engine — bidirectional sync between local .md files and Supabase.

Architecture: LOCAL FIRST. Always.

- Local file is always written first and is the source of truth.
- Supabase sync is best-effort — a failed network never blocks or fails a request.
- Sync is MANUALLY TRIGGERED ONLY — no automatic background sync.
- Three modes: push, pull, bidirectional.
- Conflict detection uses content hashes and SQLite sync metadata.
- Conflict resolution supports: keep_local, keep_remote, merge, split, exclude.

SQLite tracks per-note sync status:
  never_synced | synced | pending_push | failed | excluded
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.platform_ctx import PLATFORM

from app.services.documents.file_manager import (
    DocumentFileManager,
    NotesAccessError,
    content_hash,
    file_manager,
    notes_access_guard,
)
from app.services.documents.supabase_client import SupabaseDocClient, supabase_docs

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _note_id_for_path(file_path: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"matrx-note:{file_path}"))


class SyncEngine:
    """Coordinates sync between local documents and Supabase."""

    def __init__(
        self,
        fm: DocumentFileManager | None = None,
        sb: SupabaseDocClient | None = None,
    ) -> None:
        self.fm = fm or file_manager
        self.sb = sb or supabase_docs
        self._device_id: str | None = None
        self._user_id: str | None = None
        self._watch_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._sync_lock = asyncio.Lock()
        self._last_push_hashes: dict[str, str] = {}

    @property
    def device_id(self) -> str:
        if not self._device_id:
            state = self.fm.load_sync_state()
            if state.get("device_id"):
                self._device_id = state["device_id"]
            else:
                self._device_id = str(uuid.uuid4())[:12]
                state["device_id"] = self._device_id
                try:
                    self.fm.save_sync_state(state)
                except NotesAccessError:
                    # Notes dir not writable (e.g. macOS Full Disk Access not
                    # granted). Use an ephemeral device id for this run rather
                    # than crashing the caller (the /sync/status 500).
                    logger.debug(
                        "Could not persist device_id — notes dir not accessible; "
                        "using ephemeral id this run"
                    )
        return self._device_id

    def configure(self, user_id: str, jwt: str) -> None:
        self._user_id = user_id
        self.sb.set_jwt(jwt)

    @property
    def is_configured(self) -> bool:
        return bool(self._user_id and self.sb.available)

    def _get_notes_repo(self):
        from app.services.local_db.repositories import NotesRepo
        return NotesRepo()

    @staticmethod
    def _access_skipped() -> dict[str, Any] | None:
        """Return a structured 'skipped' result if the notes dir is access-degraded.

        Every bulk sync cycle scans local files and rewrites .sync/state.json;
        while the OS is denying access those all fail. Rather than re-hit the
        wall (and re-spam) on each trigger, short-circuit with a structured
        result carrying the actionable reason. The guard lets one probe op
        through every recheck interval so restored access clears the state.
        """
        if notes_access_guard.should_skip_sync():
            return {
                "sync_skipped": True,
                "reason": notes_access_guard.reason,
                "notes_access_degraded": True,
            }
        return None

    # ── Push: local → Supabase ───────────────────────────────────────────────

    async def push_note(
        self,
        note_id: str,
        label: str,
        content: str,
        folder_name: str = "General",
        folder_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        is_new_note: bool = False,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """Push a single note to Supabase (public entry — serialized).

        Routes fire this directly, concurrently with full_sync/push_all; all
        of them read-modify-write the shared .sync/state.json, so the public
        wrapper must take the same lock the bulk operations hold.
        """
        async with self._sync_lock:
            return await self._push_note(
                note_id=note_id,
                label=label,
                content=content,
                folder_name=folder_name,
                folder_id=folder_id,
                tags=tags,
                metadata=metadata,
                is_new_note=is_new_note,
                file_path=file_path,
            )

    async def _push_note(
        self,
        note_id: str,
        label: str,
        content: str,
        folder_name: str = "General",
        folder_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        is_new_note: bool = False,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """Push a single note to Supabase. Local file must already be written.

        ``file_path`` MUST be the note's actual on-disk path when the caller
        knows it. Rederiving the path from folder+label here silently targets
        ``<folder>/<label>.md`` — which, for notes created with a
        collision-suffixed path (``label_2.md``), is a DIFFERENT note's file
        and would overwrite it on disk.
        """
        if not self._user_id:
            logger.debug("push_note skipped — no user_id configured")
            return {"id": note_id, "label": label, "_synced_to_cloud": False}

        if file_path is None:
            # Best effort: recover the real path from SQLite before falling
            # back to the folder+label derivation.
            try:
                row = await self._get_notes_repo().get(note_id)
                if row and row.get("file_path"):
                    file_path = row["file_path"]
            except Exception:
                pass

        file_path = self.fm.write_note(folder_name, label, content, file_path)
        c_hash = content_hash(content)

        self._last_push_hashes[file_path] = c_hash

        state = self.fm.load_sync_state()
        state["note_hashes"][file_path] = c_hash
        self.fm.save_sync_state(state)

        result: dict[str, Any] = {
            "id": note_id,
            "label": label,
            "content": content,
            "folder_name": folder_name,
            "folder_id": folder_id,
            "file_path": file_path,
            "tags": tags or [],
            "metadata": metadata or {},
            "_synced_to_cloud": False,
        }

        if self.sb.available:
            try:
                if is_new_note:
                    result = await self.sb.upsert_note(
                        note_id=note_id,
                        user_id=self._user_id,
                        label=label,
                        content=content,
                        folder_name=folder_name,
                        folder_id=folder_id,
                        file_path=file_path,
                        tags=tags,
                        metadata=metadata,
                        device_id=self.device_id,
                    )
                else:
                    existing = await self.sb.get_note(note_id)
                    if existing:
                        versions = await self.sb.list_versions(note_id)
                        next_version = (
                            (versions[0]["version_number"] + 1) if versions else 1
                        )
                        if existing.get("content") and existing["content"] != content:
                            try:
                                await self.sb.create_version(
                                    note_id=note_id,
                                    user_id=self._user_id,
                                    content=existing["content"],
                                    label=existing.get("label", label),
                                    version_number=next_version,
                                    change_source="desktop",
                                    change_type="edit",
                                )
                            except Exception:
                                logger.debug("Version snapshot failed (non-critical)")
                        result = await self.sb.update_note(
                            note_id,
                            {
                                "label": label,
                                "content": content,
                                "folder_name": folder_name,
                                "folder_id": folder_id,
                                "file_path": file_path,
                                "tags": tags or [],
                                "metadata": metadata or {},
                            },
                            device_id=self.device_id,
                        )
                    else:
                        result = await self.sb.upsert_note(
                            note_id=note_id,
                            user_id=self._user_id,
                            label=label,
                            content=content,
                            folder_name=folder_name,
                            folder_id=folder_id,
                            file_path=file_path,
                            tags=tags,
                            metadata=metadata,
                            device_id=self.device_id,
                        )

                result["_synced_to_cloud"] = True

                sv = result.get("sync_version", 0)
                if sv and sv > state.get("last_sync_version", 0):
                    state["last_sync_version"] = sv
                    self.fm.save_sync_state(state)

                try:
                    await self.sb.log_sync(
                        user_id=self._user_id,
                        device_id=self.device_id,
                        action="push",
                        note_id=note_id,
                        sync_version=result.get("sync_version"),
                        content_hash=c_hash,
                    )
                except Exception:
                    pass

                repo = self._get_notes_repo()
                await repo.set_sync_status(note_id, "synced", remote_hash=c_hash)

            except Exception:
                logger.warning(
                    "Supabase push failed for note %s — saved locally only; "
                    "marking sync_status=failed (will retry on next push).",
                    note_id,
                    exc_info=True,
                )
                # Make the failure visible in state, not just the logs, so the
                # note is picked up again by list_pending_push. Best-effort —
                # a failing status write must not mask the original push error.
                try:
                    await self._get_notes_repo().set_sync_status(note_id, "failed")
                except Exception:
                    logger.warning(
                        "Could not record sync_status=failed for note %s",
                        note_id,
                        exc_info=True,
                    )

        await self._sync_mappings(file_path, folder_id)
        return result

    # ── Pull: Supabase → local ───────────────────────────────────────────────

    async def pull_note(self, note_id: str) -> dict[str, Any] | None:
        """Pull a single note (public entry — serialized, see push_note)."""
        async with self._sync_lock:
            return await self._pull_note(note_id)

    async def _pull_note(self, note_id: str) -> dict[str, Any] | None:
        if not self._user_id:
            return None

        try:
            note = await self.sb.get_note(note_id)
        except Exception:
            logger.warning(
                "Failed to pull note %s from Supabase", note_id, exc_info=True
            )
            return None

        if not note:
            return None

        # A remote soft-delete must propagate as a deletion — writing the
        # deleted row's content back to disk resurrected notes the user had
        # deleted on another device.
        if note.get("is_deleted"):
            fp = note.get("file_path")
            if fp:
                try:
                    self.fm.delete_note(fp)
                except Exception:
                    logger.debug("Could not remove local file for deleted note %s", note_id)
                state = self.fm.load_sync_state()
                state.get("note_hashes", {}).pop(fp, None)
                self.fm.save_sync_state(state)
                self._last_push_hashes.pop(fp, None)
            try:
                await self._get_notes_repo().soft_delete(note_id)
            except Exception:
                pass
            return {**note, "_deleted": True}

        content = note.get("content", "")
        label = note.get("label", "Untitled")
        folder_name = note.get("folder_name", "General")
        file_path = note.get("file_path")

        if file_path:
            local_hash = self.fm.note_hash(file_path)
            remote_hash = note.get("content_hash")
            state = self.fm.load_sync_state()
            last_known_hash = state.get("note_hashes", {}).get(file_path)

            # A local file that differs from the incoming remote content may
            # only be overwritten when we can PROVE it carries no unsynced
            # local edit — i.e. its hash matches what we recorded at the last
            # successful sync. If we cannot prove that (last_known_hash is
            # missing/None because sync state was reset or corrupted, OR the
            # local hash has moved away from last-known, meaning the user
            # edited it), we must NOT clobber it. Treat it as a conflict and
            # preserve both copies. Previously the guard required
            # last_known_hash to be truthy, so a None/corrupted sync state
            # silently fell through to an unconditional overwrite and ate the
            # local edit.
            if local_hash and remote_hash and local_hash != remote_hash:
                local_unchanged_since_sync = (
                    last_known_hash is not None and local_hash == last_known_hash
                )
                if not local_unchanged_since_sync:
                    local_content = self.fm.read_note(file_path) or ""
                    self.fm.save_conflict(file_path, local_content, content, note_id)
                    logger.warning(
                        "Sync conflict detected for %s (note %s) — local edit "
                        "preserved (last_known_hash=%s)",
                        file_path,
                        note_id,
                        "missing" if last_known_hash is None else "stale",
                    )
                    return {**note, "_conflict": True}

        file_path = self.fm.write_note(folder_name, label, content, file_path)
        c_hash = content_hash(content)

        self._last_push_hashes[file_path] = c_hash

        state = self.fm.load_sync_state()
        state["note_hashes"][file_path] = c_hash
        sv = note.get("sync_version", 0)
        if sv and sv > state.get("last_sync_version", 0):
            state["last_sync_version"] = sv
        self.fm.save_sync_state(state)

        repo = self._get_notes_repo()
        await repo.upsert({
            "id": note_id,
            "user_id": self._user_id or "",
            "folder_id": note.get("folder_id"),
            "title": label,
            "label": label,
            "content": content,
            "content_hash": c_hash,
            "file_path": file_path,
            "folder_name": folder_name,
            "tags": note.get("tags", []),
            "metadata": note.get("metadata", {}),
            "sync_status": "synced",
            "last_synced_at": _now(),
            "sync_enabled": True,
            "remote_content_hash": c_hash,
            "sync_version": sv,
        })

        await self._sync_mappings(file_path, note.get("folder_id"))
        return note

    async def pull_changes(self) -> dict[str, Any]:
        if not self._user_id:
            return {"pulled": 0, "conflicts": 0}

        skipped = self._access_skipped()
        if skipped is not None:
            return {"pulled": 0, "conflicts": 0, **skipped}

        state = self.fm.load_sync_state()
        last_version = state.get("last_sync_version", 0)

        try:
            notes = await self.sb.get_notes_since(self._user_id, last_version)
        except Exception:
            logger.warning("Failed to pull changes from Supabase", exc_info=True)
            return {"pulled": 0, "conflicts": 0, "error": "network_error"}

        pulled = 0
        conflicts = 0
        for note in notes:
            fp = note.get("file_path")
            if fp and self._last_push_hashes.get(fp) == note.get("content_hash"):
                continue

            result = await self._pull_note(note["id"])
            if result and not result.get("_deleted"):
                pulled += 1
                if result.get("_conflict"):
                    conflicts += 1

        return {"pulled": pulled, "conflicts": conflicts}

    # ── Push all: bulk push local-only notes ─────────────────────────────────

    async def push_all(self) -> dict[str, Any]:
        """Push all notes that have pending local changes to Supabase."""
        async with self._sync_lock:
            if not self._user_id:
                return {"error": "Not configured"}

            skipped = self._access_skipped()
            if skipped is not None:
                return {"pushed": 0, "failed": 0, "skipped": 0, **skipped}

            repo = self._get_notes_repo()
            pending = await repo.list_pending_push()
            local_files = self.fm.scan_all()
            local_by_path = {f["file_path"]: f for f in local_files}

            stats = {"pushed": 0, "failed": 0, "skipped": 0}

            for note in pending:
                if not note.get("sync_enabled", True):
                    stats["skipped"] += 1
                    continue

                fp = note.get("file_path")
                if not fp or fp not in local_by_path:
                    stats["skipped"] += 1
                    continue

                content = self.fm.read_note(fp)
                if content is None:
                    stats["skipped"] += 1
                    continue

                try:
                    is_new = note.get("sync_status") == "never_synced"
                    await self._push_note(
                        note_id=note["id"],
                        label=note.get("label", note.get("title", "")),
                        content=content,
                        folder_name=note.get("folder_name", "General"),
                        folder_id=note.get("folder_id"),
                        tags=note.get("tags", []),
                        metadata=note.get("metadata", {}),
                        is_new_note=is_new,
                        file_path=fp,
                    )
                    stats["pushed"] += 1
                except Exception:
                    stats["failed"] += 1

            return stats

    # ── Pull all: import all server notes ────────────────────────────────────

    async def pull_all(self) -> dict[str, Any]:
        """Pull all notes from Supabase. New server-only notes auto-import (Decision 4: Option A)."""
        async with self._sync_lock:
            if not self._user_id:
                return {"error": "Not configured"}

            skipped = self._access_skipped()
            if skipped is not None:
                return {"pulled": 0, "conflicts": 0, "skipped": 0, **skipped}

            try:
                remote_notes = await self.sb.get_all_notes_with_hashes(self._user_id)
            except Exception:
                return {"pulled": 0, "conflicts": 0, "error": "network_error"}

            stats = {"pulled": 0, "conflicts": 0, "skipped": 0}
            repo = self._get_notes_repo()

            for remote in remote_notes:
                note_id = remote["id"]
                fp = remote.get("file_path")

                local_note = await repo.get(note_id)
                if local_note and not local_note.get("sync_enabled", True):
                    stats["skipped"] += 1
                    continue

                result = await self._pull_note(note_id)
                if result and not result.get("_deleted"):
                    stats["pulled"] += 1
                    if result.get("_conflict"):
                        stats["conflicts"] += 1

            return stats

    # ── Full reconciliation ──────────────────────────────────────────────────

    async def full_sync(self) -> dict[str, Any]:
        """Full bidirectional sync with conflict detection."""
        async with self._sync_lock:
            if not self._user_id:
                return {"error": "Not configured"}

            stats = {
                "pushed": 0,
                "pulled": 0,
                "conflicts": 0,
                "unchanged": 0,
                "deleted_local": 0,
            }

            skipped = self._access_skipped()
            if skipped is not None:
                return {**stats, **skipped}

            try:
                remote_notes = await self.sb.get_all_notes_with_hashes(self._user_id)
            except Exception:
                return {**stats, "error": "network_error"}

            remote_by_path: dict[str, dict] = {}
            remote_by_id: dict[str, dict] = {}
            for n in remote_notes:
                if n.get("file_path"):
                    remote_by_path[n["file_path"]] = n
                remote_by_id[n["id"]] = n

            local_files = self.fm.scan_all()
            local_by_path: dict[str, dict] = {f["file_path"]: f for f in local_files}

            state = self.fm.load_sync_state()
            known_hashes = state.get("note_hashes", {})
            repo = self._get_notes_repo()

            for fp, remote in remote_by_path.items():
                note_id = remote["id"]
                local_note = await repo.get(note_id)
                if local_note and not local_note.get("sync_enabled", True):
                    continue

                local = local_by_path.get(fp)

                if local is None:
                    # Local file is gone. If our SQLite row carries a delete
                    # tombstone, the user deleted it HERE — propagate the
                    # deletion instead of resurrecting the note from the cloud.
                    if local_note and local_note.get("is_deleted"):
                        try:
                            await self.sb.soft_delete_note(note_id)
                            stats["deleted_local"] += 1
                        except Exception:
                            logger.debug(
                                "Could not propagate local delete for %s", note_id
                            )
                        continue
                    await self._pull_note(remote["id"])
                    stats["pulled"] += 1

                elif local["content_hash"] == remote.get("content_hash"):
                    stats["unchanged"] += 1
                    # NOTE: must be the remote/SQLite row id — notes created via
                    # the API use uuid4 ids, so the old path-derived uuid5 here
                    # matched zero rows and pending_push rows never converged.
                    await repo.set_sync_status(
                        note_id, "synced",
                        remote_hash=remote.get("content_hash")
                    )

                elif known_hashes.get(fp) == local["content_hash"]:
                    await self._pull_note(remote["id"])
                    stats["pulled"] += 1

                elif known_hashes.get(fp) == remote.get("content_hash"):
                    content = self.fm.read_note(fp)
                    if content is not None:
                        await self._push_note(
                            note_id=remote["id"],
                            label=remote.get("label", local["label"]),
                            content=content,
                            folder_name=remote.get("folder_name", "General"),
                            folder_id=remote.get("folder_id"),
                            file_path=fp,
                        )
                        stats["pushed"] += 1
                else:
                    local_content = self.fm.read_note(fp) or ""
                    try:
                        full_note = await self.sb.get_note(remote["id"])
                        remote_content = full_note.get("content", "") if full_note else ""
                    except Exception:
                        remote_content = ""
                    self.fm.save_conflict(
                        fp, local_content, remote_content, remote["id"]
                    )
                    stats["conflicts"] += 1

            for fp, local in local_by_path.items():
                if fp not in remote_by_path:
                    # Resolve the REAL SQLite row for this path — API-created
                    # notes use uuid4 ids, so deriving a fresh uuid5/uuid4 here
                    # split the identity between SQLite and the cloud and made
                    # push_note's set_sync_status update zero rows (the note
                    # then re-pushed forever).
                    local_note = await repo.get_by_file_path(fp)
                    if local_note is None:
                        local_note = await repo.get(_note_id_for_path(fp))
                    if local_note and not local_note.get("sync_enabled", True):
                        continue
                    if local_note and local_note.get("is_deleted"):
                        continue

                    content = self.fm.read_note(fp)
                    if content is not None:
                        parts = Path(fp).parts
                        folder = parts[0] if len(parts) > 1 else "General"
                        push_id = (
                            local_note["id"] if local_note else _note_id_for_path(fp)
                        )
                        await self._push_note(
                            note_id=push_id,
                            label=local["label"],
                            content=content,
                            folder_name=(local_note or {}).get("folder_name") or folder,
                            folder_id=(local_note or {}).get("folder_id"),
                            is_new_note=True,
                            file_path=fp,
                        )
                        stats["pushed"] += 1

            state["last_full_sync"] = time.time()
            max_sv = max(
                (n.get("sync_version", 0) for n in remote_notes),
                default=state.get("last_sync_version", 0),
            )
            state["last_sync_version"] = max_sv
            self.fm.save_sync_state(state)

            try:
                await self.sb.log_sync(
                    user_id=self._user_id,
                    device_id=self.device_id,
                    action="full_sync",
                    details=stats,
                )
            except Exception:
                pass

            return stats

    # ── Directory mapping sync ───────────────────────────────────────────────

    async def _sync_mappings(self, file_path: str, folder_id: str | None) -> None:
        if not folder_id:
            return
        local_mappings = self.fm.load_local_mappings()
        mapped_paths = local_mappings.get(folder_id, [])
        if mapped_paths:
            self.fm.sync_to_mapped_dirs(file_path, mapped_paths)

    # ── Device registration ──────────────────────────────────────────────────

    async def register_device(self) -> dict[str, Any]:
        if not self._user_id or not self.sb.available:
            return {}

        try:
            return await self.sb.register_device(
                user_id=self._user_id,
                device_id=self.device_id,
                device_name=PLATFORM["hostname"] or "Unknown",
                platform=PLATFORM["system"],
                base_path=str(self.fm.base_dir),
            )
        except Exception:
            return {}

    # ── File watcher integration ─────────────────────────────────────────────

    async def start_watcher(self) -> None:
        if self._watch_task and not self._watch_task.done():
            return
        self._stop_event.clear()
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info("Document file watcher started: %s", self.fm.base_dir)

    async def stop_watcher(self) -> None:
        self._stop_event.set()
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None
        logger.info("Document file watcher stopped")

    async def _watch_loop(self) -> None:
        try:
            import watchfiles

            async for changes in watchfiles.awatch(
                str(self.fm.base_dir),
                recursive=True,
                stop_event=self._stop_event,
            ):
                for change_type, change_path in changes:
                    path = Path(change_path)
                    if not path.suffix == ".md":
                        continue
                    if ".sync" in path.parts:
                        continue

                    try:
                        rel_path = self.fm.relative_path(path)
                    except ValueError:
                        continue

                    # Debounce FIRST, then read/hash — hashing before the sleep
                    # evaluated echo-suppression against content that may have
                    # changed again by the time we process the event.
                    await asyncio.sleep(0.5)

                    if change_type == watchfiles.Change.deleted:
                        logger.info("External delete detected: %s", rel_path)
                        await self._handle_external_delete(rel_path)
                    else:
                        if path.is_file():
                            try:
                                current_hash = content_hash(
                                    path.read_text(encoding="utf-8")
                                )
                            except OSError:
                                continue
                            if self._last_push_hashes.get(rel_path) == current_hash:
                                continue
                        logger.info("External change detected: %s", rel_path)
                        await self._handle_external_change(rel_path)

        except ImportError:
            logger.info("watchfiles not available, using polling for document watch")
            state = self.fm.load_sync_state()
            known = dict(state.get("note_hashes", {}))

            while not self._stop_event.is_set():
                await asyncio.sleep(5)
                current_files = self.fm.scan_all()
                for f in current_files:
                    fp = f["file_path"]
                    if f["content_hash"] != known.get(fp):
                        if self._last_push_hashes.get(fp) == f["content_hash"]:
                            known[fp] = f["content_hash"]
                            continue
                        logger.info("Polling detected change: %s", fp)
                        await self._handle_external_change(fp)
                        known[fp] = f["content_hash"]

        except asyncio.CancelledError:
            pass

    async def _handle_external_delete(self, file_path: str) -> None:
        """Handle an externally deleted .md file.

        Records a delete tombstone in SQLite and drops the sync-state hash so
        the next full_sync propagates the deletion instead of re-pulling the
        note from the cloud (the old behavior only logged the event, so every
        locally deleted file came back on the next sync).
        """
        async with self._sync_lock:
            state = self.fm.load_sync_state()
            state.get("note_hashes", {}).pop(file_path, None)
            self.fm.save_sync_state(state)
            self._last_push_hashes.pop(file_path, None)

            repo = self._get_notes_repo()
            row = await repo.get_by_file_path(file_path)
            if row is None:
                row = await repo.get(_note_id_for_path(file_path))
            if row is None:
                return
            try:
                await repo.soft_delete(row["id"])
            except Exception:
                logger.debug("Could not tombstone deleted note %s", file_path)
                return

            if self.is_configured and self._user_id:
                try:
                    await self.sb.soft_delete_note(row["id"])
                except Exception:
                    logger.debug(
                        "Could not propagate delete for %s (will retry on full_sync)",
                        file_path,
                    )

    async def _handle_external_change(self, file_path: str) -> None:
        """Handle an externally modified .md file — update SQLite metadata."""
        async with self._sync_lock:
            content = self.fm.read_note(file_path)
            if content is None:
                return

            c_hash = content_hash(content)
            state = self.fm.load_sync_state()
            state["note_hashes"][file_path] = c_hash
            self.fm.save_sync_state(state)

            # Resolve the existing row by PATH first — notes created through the
            # API have uuid4 ids, so deriving a uuid5 here created a duplicate
            # SQLite row for the same file.
            repo = self._get_notes_repo()
            existing = await repo.get_by_file_path(file_path)
            if existing is None:
                existing = await repo.get(_note_id_for_path(file_path))
            note_id = existing["id"] if existing else _note_id_for_path(file_path)

            parts = Path(file_path).parts
            folder = parts[0] if len(parts) > 1 else "General"

            await repo.upsert({
                "id": note_id,
                "user_id": existing.get("user_id", "") if existing else "",
                "title": existing.get("title") if existing else Path(file_path).stem,
                "label": existing.get("label") if existing else Path(file_path).stem,
                "content": content,
                "content_hash": c_hash,
                "file_path": file_path,
                "folder_name": (existing.get("folder_name") if existing else None) or folder,
                "sync_status": "pending_push" if (existing and existing.get("sync_status") == "synced") else (existing.get("sync_status", "never_synced") if existing else "never_synced"),
                "sync_enabled": existing.get("sync_enabled", True) if existing else True,
                "last_synced_at": existing.get("last_synced_at") if existing else None,
                "remote_content_hash": existing.get("remote_content_hash") if existing else None,
                "created_at": existing.get("created_at") if existing else _now(),
                "updated_at": _now(),
            })

            if self.is_configured and self._user_id:
                try:
                    all_notes = await self.sb.get_all_notes_with_hashes(self._user_id)
                    matching = [n for n in all_notes if n.get("file_path") == file_path]

                    if matching:
                        note = matching[0]
                        if note.get("content_hash") == c_hash:
                            return
                        await self._push_note(
                            note_id=note["id"],
                            label=note.get("label", Path(file_path).stem),
                            content=content,
                            folder_name=note.get("folder_name", "General"),
                            folder_id=note.get("folder_id"),
                            file_path=file_path,
                        )
                    else:
                        # Push under the SAME id as the SQLite row created
                        # above — a fresh uuid4 here split the identity and
                        # made the post-push set_sync_status a no-op.
                        await self._push_note(
                            note_id=note_id,
                            label=Path(file_path).stem,
                            content=content,
                            folder_name=folder,
                            is_new_note=True,
                            file_path=file_path,
                        )
                except Exception:
                    logger.debug(
                        "Failed to push external change for %s (queued locally, non-critical)",
                        file_path,
                        exc_info=True,
                    )

    # ── Conflict resolution ──────────────────────────────────────────────────

    async def resolve_conflict(
        self,
        note_id: str,
        resolution: str = "keep_remote",
        merged_content: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a sync conflict.

        Resolutions:
          keep_local  — Use the local version, push to cloud.
          keep_remote — Use the cloud version, overwrite local.
          merge       — Use the provided merged_content.
          append      — Combine both versions into one (local first, then cloud).
          split       — Keep both as separate notes.
          exclude     — Mark this note as excluded from sync.
        """
        async with self._sync_lock:
            return await self._resolve_conflict(note_id, resolution, merged_content)

    async def _resolve_conflict(
        self,
        note_id: str,
        resolution: str,
        merged_content: str | None,
    ) -> dict[str, Any] | None:
        conflict_dir = self.fm.base_dir / ".sync" / "conflicts" / note_id
        if not conflict_dir.exists():
            return None

        local_content = ""
        remote_content = ""
        local_file = conflict_dir / "local.md"
        remote_file = conflict_dir / "remote.md"
        if local_file.exists():
            local_content = local_file.read_text(encoding="utf-8")
        if remote_file.exists():
            remote_content = remote_file.read_text(encoding="utf-8")

        repo = self._get_notes_repo()
        sqlite_note = await repo.get(note_id)
        label = (sqlite_note.get("label") or sqlite_note.get("title", "Untitled")) if sqlite_note else "Untitled"
        folder_name = (sqlite_note.get("folder_name") or "General") if sqlite_note else "General"
        folder_id = sqlite_note.get("folder_id") if sqlite_note else None
        note_path = sqlite_note.get("file_path") if sqlite_note else None

        result: dict[str, Any] = {"id": note_id, "resolution": resolution}

        if resolution == "keep_local":
            self.fm.write_note(folder_name, label, local_content, note_path)
            if self.is_configured and self._user_id:
                try:
                    await self._push_note(
                        note_id=note_id,
                        label=label,
                        content=local_content,
                        folder_name=folder_name,
                        folder_id=folder_id,
                        file_path=note_path,
                    )
                except Exception:
                    pass
            result["content"] = local_content

        elif resolution == "keep_remote":
            self.fm.write_note(folder_name, label, remote_content, note_path)
            await repo.set_sync_status(note_id, "synced", remote_hash=content_hash(remote_content))
            result["content"] = remote_content

        elif resolution == "merge":
            if not merged_content:
                return None
            self.fm.write_note(folder_name, label, merged_content, note_path)
            if self.is_configured and self._user_id:
                try:
                    await self._push_note(
                        note_id=note_id,
                        label=label,
                        content=merged_content,
                        folder_name=folder_name,
                        folder_id=folder_id,
                        file_path=note_path,
                    )
                except Exception:
                    pass
            result["content"] = merged_content

        elif resolution == "append":
            # Combine both versions: local first, then cloud, with separator
            separator = "\n\n---\n\n*— Appended from cloud sync —*\n\n"
            combined = local_content.rstrip() + separator + remote_content.lstrip()
            self.fm.write_note(folder_name, label, combined, note_path)
            if self.is_configured and self._user_id:
                try:
                    await self._push_note(
                        note_id=note_id,
                        label=label,
                        content=combined,
                        folder_name=folder_name,
                        folder_id=folder_id,
                        file_path=note_path,
                    )
                except Exception:
                    pass
            result["content"] = combined

        elif resolution == "split":
            self.fm.write_note(folder_name, label, local_content, note_path)
            new_label = f"{label} (cloud copy)"
            self.fm.write_note(folder_name, new_label, remote_content)
            if self.is_configured and self._user_id:
                try:
                    new_id = str(uuid.uuid4())
                    await self._push_note(
                        note_id=new_id,
                        label=new_label,
                        content=remote_content,
                        folder_name=folder_name,
                        folder_id=folder_id,
                        is_new_note=True,
                    )
                except Exception:
                    pass
            result["content"] = local_content
            result["split_note_label"] = new_label

        elif resolution == "exclude":
            self.fm.write_note(folder_name, label, local_content, note_path)
            await repo.set_excluded(note_id, True)
            result["content"] = local_content

        else:
            return None

        self.fm.resolve_conflict(note_id)

        if self.is_configured and self._user_id:
            try:
                await self.sb.log_sync(
                    user_id=self._user_id,
                    device_id=self.device_id,
                    action="conflict_resolved",
                    note_id=note_id,
                    details={"resolution": resolution},
                )
            except Exception:
                pass

        return result

    # ── Status ───────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        state = self.fm.load_sync_state()
        conflicts = self.fm.list_conflicts()
        return {
            "configured": self.is_configured,
            "device_id": self.device_id,
            "last_sync_version": state.get("last_sync_version", 0),
            "last_full_sync": state.get("last_full_sync"),
            "tracked_files": len(state.get("note_hashes", {})),
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "watcher_active": self._watch_task is not None
            and not self._watch_task.done(),
            "base_dir": str(self.fm.base_dir),
            # Surface the macOS Full Disk Access / permission-denied condition
            # so the UI can prompt the user with an actionable message instead
            # of silently showing an empty, non-syncing notes list.
            "notes_access_degraded": notes_access_guard.is_degraded,
            "notes_access_reason": notes_access_guard.reason,
        }


# Module-level singleton
sync_engine = SyncEngine()
