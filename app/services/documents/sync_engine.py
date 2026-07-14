"""Notes sync engine — bidirectional sync between local .md files and Supabase.

Architecture (see docs/SYNC_CONTRACT.md — the ratified sync contract)
---------------------------------------------------------------------
The local files are the WORKING COPY — a full first-access replica with
complete offline read/write; the cloud (workbench.notes) is the durable
source of truth the replica converges to.

- Local file is always written first; cloud propagation follows.
- Supabase sync is best-effort — a failed network never blocks or fails a
  request; failed pushes are marked sync_status=failed and retried.
- Sync runs AUTOMATICALLY: the engine-owned auto-sync loop (see
  start_background_sync) does an incremental pull + pending push every tick
  and a full reconcile daily, the file watcher catches external edits and
  deletes, Supabase Realtime (frontend hook) pulls remote edits live, and
  manual push/pull/bidirectional triggers remain available on top.
- Three modes: push, pull, bidirectional.
- Conflict detection uses content hashes and SQLite sync metadata.
- Conflicts are NEVER destructive: both versions are preserved under
  .sync/conflicts/<note_id>/ until the user resolves with one of
  keep_local, keep_remote, merge, append, split, exclude.
- Deletions are tombstones on both sides (SQLite is_deleted, cloud
  deleted_at) and propagate in both directions.

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
        self._auto_task: asyncio.Task | None = None
        self._auto_stop = asyncio.Event()
        self._auto_last_skip_reason: str | None = None
        self._auto_last_token: str | None = None

    @property
    def device_id(self) -> str:
        """Machine-local sync identity.

        Persisted under ``~/.matrx`` (genuinely machine-local), NOT inside the
        notes dir: the notes dir defaults to a user-visible Documents location
        that macOS iCloud "Desktop & Documents" (or a user-pointed Dropbox
        path) can replicate across machines — two engines sharing one
        device_id makes the own-push guard suppress every cross-machine pull
        and turns sync into silent last-writer-wins ping-pong.

        Migration: an existing id in ``.sync/state.json`` is adopted once into
        the machine-local file (preserving identity for the single-machine
        case) and ignored thereafter.
        """
        if self._device_id:
            return self._device_id

        from app.config import MATRX_HOME_DIR

        id_file = MATRX_HOME_DIR / "notes_device_id"
        try:
            if id_file.is_file():
                stored = id_file.read_text(encoding="utf-8").strip()
                if stored:
                    self._device_id = stored
                    return self._device_id
        except OSError:
            pass

        legacy: str | None = None
        try:
            legacy = self.fm.load_sync_state().get("device_id")
        except Exception:
            legacy = None

        self._device_id = legacy or str(uuid.uuid4())[:12]
        try:
            id_file.parent.mkdir(parents=True, exist_ok=True)
            id_file.write_text(self._device_id, encoding="utf-8")
        except OSError:
            logger.warning(
                "Could not persist notes device_id to %s — using ephemeral id "
                "this run",
                id_file,
            )
        return self._device_id

    def configure(self, user_id: str, jwt: str) -> None:
        self._user_id = user_id
        self.sb.set_jwt(jwt)

    @property
    def is_configured(self) -> bool:
        return bool(self._user_id and self.sb.available)

    @property
    def sync_lock(self) -> asyncio.Lock:
        """The engine-wide sync mutex, for callers (routes) whose file/tombstone
        mutations must not interleave with an in-flight bulk sync."""
        return self._sync_lock

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
        file_path: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Push a single note to Supabase (public entry — serialized).

        Routes fire this directly, concurrently with full_sync/push_all; all
        of them read-modify-write the shared .sync/state.json, so the public
        wrapper must take the same lock the bulk operations hold.

        ``force=True`` skips the optimistic-concurrency precondition — used
        ONLY by conflict resolution, where the user has explicitly chosen a
        winner.
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
                file_path=file_path,
                force=force,
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
        file_path: str | None = None,
        force: bool = False,
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

        # Echo-suppression marker only — safe to set optimistically because it
        # is only ever compared for EQUALITY against incoming remote content
        # (matching hash → skip a redundant pull of identical bytes).
        self._last_push_hashes[file_path] = c_hash

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
                # Cloud-side version history is captured by the
                # `platform._version_capture` trigger on workbench.notes
                # (history.row_versions); local history by SQLite note_versions.
                #
                # Concurrency: when this device has synced the note before
                # (note_hashes carries the hash at last successful sync), the
                # push is a CONDITIONAL update — it only lands if the remote
                # row still carries that hash and is live. An unconditional
                # upsert here silently last-writer-wins over a concurrent edit
                # from another device (SYNC_CONTRACT violation).
                state = self.fm.load_sync_state()
                last_synced_hash = (
                    state.get("note_hashes", {}).get(file_path)
                    if not force
                    else None
                )

                upsert_body = dict(
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

                pushed_row: dict[str, Any] | None = None
                if last_synced_hash:
                    pushed_row = await self.sb.update_note_if_unchanged(
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
                        expected_content_hash=last_synced_hash,
                        device_id=self.device_id,
                    )
                    if pushed_row is None:
                        # Precondition failed — remote moved since our last
                        # sync, or was soft-deleted. Decide what that means.
                        remote = await self.sb.get_note(note_id)
                        if (
                            remote is not None
                            and not remote.get("is_deleted")
                            and remote.get("last_device_id") != self.device_id
                            and remote.get("content_hash") != c_hash
                        ):
                            # Genuine cross-device divergence: preserve BOTH
                            # sides and stop — never overwrite.
                            self.fm.save_conflict(
                                file_path,
                                content,
                                remote.get("content", ""),
                                note_id,
                            )
                            logger.warning(
                                "Push conflict for note %s — remote changed on "
                                "another device since our last sync; both "
                                "versions preserved for resolution",
                                note_id,
                            )
                            return {**result, "_conflict": True}
                        # Otherwise safe to take the slot: row missing, our
                        # own earlier write, identical content, or a remote
                        # soft-delete being revived by this edit.

                if pushed_row is None:
                    pushed_row = await self.sb.upsert_note(**upsert_body)

                result = pushed_row
                result["_synced_to_cloud"] = True

                # Record the last-SUCCESSFULLY-synced hash only now. Writing it
                # before the push succeeded made a failed/offline push look
                # synced, and the next pull would clobber the newer local file
                # with older cloud content (contract: note_hashes == "hash at
                # last successful sync", docs/SYNC_CONTRACT.md).
                state = self.fm.load_sync_state()
                state["note_hashes"][file_path] = c_hash
                self.fm.save_sync_state(state)

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

        repo = self._get_notes_repo()

        # A remote soft-delete must propagate as a deletion — writing the
        # deleted row's content back to disk resurrected notes the user had
        # deleted on another device.
        if note.get("is_deleted"):
            fp = note.get("file_path")
            if fp:
                # Path reuse guard: delete the file ONLY if it isn't currently
                # owned by a DIFFERENT live note (user deleted "Foo", then
                # created a new "Foo" that was handed the freed path — the
                # old row's tombstone must not eat the new note's file).
                owner = None
                try:
                    owner = await repo.get_by_file_path(fp)
                except Exception:
                    pass
                if owner and owner["id"] != note_id and not owner.get("is_deleted"):
                    logger.info(
                        "Tombstone for note %s skipped file removal — %s now "
                        "belongs to live note %s",
                        note_id, fp, owner["id"],
                    )
                else:
                    try:
                        self.fm.delete_note(fp)
                    except Exception:
                        logger.debug("Could not remove local file for deleted note %s", note_id)
                    state = self.fm.load_sync_state()
                    state.get("note_hashes", {}).pop(fp, None)
                    self.fm.save_sync_state(state)
                    self._last_push_hashes.pop(fp, None)
            try:
                await repo.soft_delete(note_id)
            except Exception:
                pass
            return {**note, "_deleted": True}

        # Values from other clients can be present-but-null — dict-get
        # defaults don't cover that and None crashes the path builder.
        content = note.get("content") or ""
        label = note.get("label") or "Untitled"
        folder_name = note.get("folder_name") or "General"
        file_path = note.get("file_path")

        local_row = await repo.get(note_id)

        # Local tombstone guard: this device deleted the note; don't let a pull
        # of the (not-yet-tombstoned) remote row resurrect it unless the remote
        # copy was genuinely written AFTER the local deletion.
        if local_row and local_row.get("is_deleted") and not note.get("is_deleted"):
            remote_ts = note.get("updated_at") or ""
            local_ts = local_row.get("updated_at") or ""
            if remote_ts <= local_ts:
                return {**note, "_skipped_local_tombstone": True}

        if not file_path:
            # Cloud notes created by other clients (web) carry no file_path.
            # NEVER derive <folder>/<label>.md directly — that silently
            # overwrites whichever local note already owns that filename and
            # funnels duplicate-labeled cloud notes into one file.
            if local_row and local_row.get("file_path"):
                file_path = local_row["file_path"]
            else:
                file_path = self.fm.unique_file_path(folder_name, label)
                note["_allocated_path"] = True
        else:
            owner = await repo.get_by_file_path(file_path)
            if owner and owner["id"] != note_id and not owner.get("is_deleted"):
                # Two distinct notes claim one path (duplicate labels across
                # clients). Reroute this one instead of clobbering.
                file_path = self.fm.unique_file_path(folder_name, label)
                note["_allocated_path"] = True

        if file_path and not note.get("_allocated_path"):
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
                # Own-push echo guard: if the remote row was last written by
                # THIS device, the cloud holds nothing this machine doesn't
                # already have — the local divergence is just a newer unsynced
                # edit. Pulling would clobber it and conflicting would spam the
                # user with a "conflict" against their own earlier save (the
                # false-conflict storm of 2026-07). Skip; the edit propagates
                # on its own push.
                if note.get("last_device_id") == self.device_id:
                    logger.debug(
                        "Skipping pull of %s — remote is this device's own push",
                        note_id,
                    )
                    return {**note, "_skipped_own_push": True}
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
        self.fm.save_sync_state(state)

        if note.get("_allocated_path"):
            # Write the allocated path back so every device (and the cloud row
            # itself) converges on one canonical location for this note.
            try:
                await self.sb.update_note(note_id, {"file_path": file_path})
            except Exception:
                logger.debug("Could not write allocated file_path back to cloud for %s", note_id)

        sv = note.get("sync_version", 0)
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
        """Incremental pull, checkpointed on the cloud ``updated_at`` stamp.

        The cursor is the max ``updated_at`` of processed rows (server clock,
        stamped by ``platform._touch_row`` on every write including
        soft-deletes) — NEVER ``sync_version``, which is a per-row edit
        counter and goes blind to less-edited notes and to all deletions when
        used as a global watermark.

        Holds ``_sync_lock`` like every other bulk operation: ``_pull_note``
        and the checkpoint update below read-modify-write the shared
        ``.sync/state.json``, and the auto-sync loop runs this in the
        background concurrently with request-driven ``push_note`` — unlocked,
        the interleaved state saves clobber each other's ``note_hashes``
        entries and manufacture false conflicts.
        """
        if not self._user_id:
            return {"pulled": 0, "conflicts": 0}

        skipped = self._access_skipped()
        if skipped is not None:
            return {"pulled": 0, "conflicts": 0, **skipped}

        async with self._sync_lock:
            state = self.fm.load_sync_state()
            last_pull_at = state.get("last_pull_at")

            try:
                notes = await self.sb.get_notes_since(self._user_id, last_pull_at)
            except Exception:
                logger.warning("Failed to pull changes from Supabase", exc_info=True)
                return {"pulled": 0, "conflicts": 0, "error": "network_error"}

            stats = {"pulled": 0, "conflicts": 0, "deleted": 0, "skipped": 0}
            max_ts = last_pull_at or ""
            for note in notes:
                ts = note.get("updated_at") or ""
                if ts > max_ts:
                    max_ts = ts

                fp = note.get("file_path")
                # Own-echo shortcut — but NEVER for tombstones: a soft-delete
                # changes deleted_at, not content_hash, so a matching hash must
                # not suppress the deletion.
                if (
                    fp
                    and not note.get("is_deleted")
                    and self._last_push_hashes.get(fp) == note.get("content_hash")
                ):
                    stats["skipped"] += 1
                    continue

                result = await self._pull_note(note["id"])
                if not result:
                    continue
                if result.get("_deleted"):
                    stats["deleted"] += 1
                elif result.get("_conflict"):
                    stats["conflicts"] += 1
                elif result.get("_skipped_own_push") or result.get("_skipped_local_tombstone"):
                    stats["skipped"] += 1
                else:
                    stats["pulled"] += 1

            if max_ts and max_ts != last_pull_at:
                # Reload — _pull_note calls above rewrote sync state.
                state = self.fm.load_sync_state()
                state["last_pull_at"] = max_ts
                self.fm.save_sync_state(state)

            return stats

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

            stats = {"pushed": 0, "failed": 0, "skipped": 0, "conflicts": 0}
            open_conflicts = set(self.fm.list_conflicts())

            for note in pending:
                if not note.get("sync_enabled", True):
                    stats["skipped"] += 1
                    continue

                # A note with an unresolved conflict must wait for the user —
                # retrying the push every tick just re-fails the precondition.
                if note["id"] in open_conflicts:
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
                    result = await self._push_note(
                        note_id=note["id"],
                        label=note.get("label", note.get("title", "")),
                        content=content,
                        folder_name=note.get("folder_name", "General"),
                        folder_id=note.get("folder_id"),
                        tags=note.get("tags", []),
                        metadata=note.get("metadata", {}),
                        file_path=fp,
                    )
                    if result.get("_conflict"):
                        stats["conflicts"] += 1
                    else:
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
                elif remote.get("last_device_id") == self.device_id:
                    # Divergence, but the cloud row was last written by THIS
                    # device — no other device has contributed since our last
                    # push, so the local file is simply newer unsynced work.
                    # Raising a conflict here made the app accuse the user of
                    # conflicting with their own saves. Push instead.
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
                            file_path=fp,
                        )
                        stats["pushed"] += 1

            # Remote notes WITHOUT a file_path — created by other clients (the
            # web app writes no file_path). remote_by_path walks right past
            # them, so without this pass the full replica never receives
            # web-authored notes at all. _pull_note allocates a collision-free
            # local path and writes it back to the cloud row, so each note
            # goes through this branch at most once.
            imported = 0
            for note_id, remote in remote_by_id.items():
                if remote.get("file_path"):
                    continue
                local_note = await repo.get(note_id)
                if local_note and not local_note.get("sync_enabled", True):
                    continue
                if local_note and local_note.get("is_deleted"):
                    continue
                if (
                    local_note
                    and local_note.get("file_path")
                    and local_note.get("content_hash") == remote.get("content_hash")
                ):
                    stats["unchanged"] += 1
                    continue
                result = await self._pull_note(note_id)
                if result and not result.get("_deleted"):
                    if result.get("_conflict"):
                        stats["conflicts"] += 1
                    else:
                        stats["pulled"] += 1
                        imported += 1
                        if imported % 100 == 0:
                            logger.info(
                                "full_sync: imported %d pathless cloud notes so far…",
                                imported,
                            )

            # Reload — the pull/push calls above rewrote sync state.
            state = self.fm.load_sync_state()
            state["last_full_sync"] = time.time()
            self.fm.save_sync_state(state)

            return stats

    # ── Directory mapping sync ───────────────────────────────────────────────

    async def _sync_mappings(self, file_path: str, folder_id: str | None) -> None:
        if not folder_id:
            return
        local_mappings = self.fm.load_local_mappings()
        mapped_paths = local_mappings.get(folder_id, [])
        if mapped_paths:
            self.fm.sync_to_mapped_dirs(file_path, mapped_paths)

    # ── Device identity ──────────────────────────────────────────────────────

    async def register_device(self) -> dict[str, Any]:
        """Device identity is local-only.

        The cloud `note_devices` registry was graveyarded in the 2026-07 cloud
        DB reorganization; device_id lives in `.sync/state.json` and rides on
        every push as `last_device_id` (own-echo detection).
        """
        return {
            "device_id": self.device_id,
            "device_name": PLATFORM["hostname"] or "Unknown",
            "platform": PLATFORM["system"],
            "base_path": str(self.fm.base_dir),
            "cloud_registry": "retired",
        }

    # ── File watcher integration ─────────────────────────────────────────────

    @property
    def watcher_active(self) -> bool:
        return self._watch_task is not None and not self._watch_task.done()

    async def start_watcher(self) -> None:
        if self.watcher_active:
            return
        if notes_access_guard.is_degraded or not self.fm.base_dir.exists():
            logger.warning(
                "Document file watcher NOT started — notes dir inaccessible (%s)",
                notes_access_guard.reason or str(self.fm.base_dir),
            )
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

    # ── Engine-owned background auto-sync ────────────────────────────────────
    #
    # Sync must not depend on the user opening the Notes UI. Historically the
    # engine only configured itself (JWT + user_id) inside request handlers,
    # so a user who never visited the Documents page got NO pulls, NO watcher,
    # and pending pushes stranded for months. This loop makes the engine
    # self-sufficient: credentials come from the persisted auth_tokens row
    # (kept fresh by POST /auth/token on every login/refresh), the watcher is
    # ensured, and an incremental pull + pending push runs every tick, with a
    # full reconcile when the last one is older than _FULL_SYNC_MAX_AGE_S.

    _FULL_SYNC_MAX_AGE_S = 24 * 3600

    @property
    def auto_sync_active(self) -> bool:
        return self._auto_task is not None and not self._auto_task.done()

    async def start_background_sync(self, interval_seconds: int = 600) -> None:
        if self.auto_sync_active:
            return
        self._auto_stop.clear()
        self._auto_task = asyncio.create_task(
            self._auto_sync_loop(interval_seconds), name="notes-auto-sync"
        )
        logger.info(
            "Notes auto-sync started (interval=%ss, full reconcile when older than %sh)",
            interval_seconds,
            self._FULL_SYNC_MAX_AGE_S // 3600,
        )

    async def stop_background_sync(self) -> None:
        self._auto_stop.set()
        if self._auto_task:
            self._auto_task.cancel()
            try:
                await self._auto_task
            except asyncio.CancelledError:
                pass
            self._auto_task = None
        logger.info("Notes auto-sync stopped")

    async def _auto_sync_loop(self, interval_seconds: int) -> None:
        # First tick runs immediately so a fresh boot converges without
        # waiting a full interval (matches local_db sync_engine behavior).
        while not self._auto_stop.is_set():
            try:
                await self._auto_sync_tick()
            except Exception:
                logger.warning("Notes auto-sync tick crashed", exc_info=True)
            try:
                await asyncio.wait_for(
                    self._auto_stop.wait(), timeout=interval_seconds
                )
            except asyncio.TimeoutError:
                continue

    async def _auto_sync_tick(self) -> None:
        from app.services.local_db.repositories import TokenRepo

        repo = TokenRepo()
        row = await repo.get()
        if not row or not row.get("access_token") or not row.get("user_id"):
            if self._auto_last_skip_reason != "no_token":
                logger.info(
                    "Notes auto-sync idle — no signed-in user (will retry each tick)"
                )
                self._auto_last_skip_reason = "no_token"
            return
        if repo.is_expired(row):
            if self._auto_last_skip_reason != "expired":
                logger.warning(
                    "Notes auto-sync idle — stored JWT is expired; waiting for the "
                    "frontend to refresh it via POST /auth/token"
                )
                self._auto_last_skip_reason = "expired"
            return
        self._auto_last_skip_reason = None

        # Configure only when the persisted credentials actually changed:
        # request handlers configure this same singleton with the request's
        # JWT, and near a token refresh the request token can be FRESHER than
        # the persisted row — an unconditional background re-configure could
        # briefly downgrade the client to the staler token mid-request.
        if (
            self._user_id != row["user_id"]
            or self._auto_last_token != row["access_token"]
        ):
            self.configure(row["user_id"], row["access_token"])
            self._auto_last_token = row["access_token"]

        if not self.watcher_active:
            await self.start_watcher()

        pull = await self.pull_changes()
        push = await self.push_all()

        state = self.fm.load_sync_state()
        last_full = state.get("last_full_sync") or 0
        full: dict[str, Any] | None = None
        if (time.time() - last_full) > self._FULL_SYNC_MAX_AGE_S:
            full = await self.full_sync()

        moved = (
            (pull.get("pulled") or 0)
            + (pull.get("conflicts") or 0)
            + (push.get("pushed") or 0)
            + (push.get("failed") or 0)
            + ((full or {}).get("pushed") or 0)
            + ((full or {}).get("pulled") or 0)
        )
        if moved or (pull.get("error") or push.get("error") or (full or {}).get("error")):
            logger.info(
                "Notes auto-sync tick: pull=%s push=%s full=%s", pull, push, full
            )

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
        except Exception:
            # A dead watcher must be loud — the UI shows "Watching" based on
            # task liveness, and a silent crash here means external edits stop
            # syncing with no signal to anyone.
            logger.error(
                "Document file watcher crashed — external-edit detection is OFF "
                "until restart",
                exc_info=True,
            )

    async def _handle_external_delete(self, file_path: str) -> None:
        """Handle an externally deleted .md file.

        Records a delete tombstone in SQLite and drops the sync-state hash so
        the next full_sync propagates the deletion instead of re-pulling the
        note from the cloud (the old behavior only logged the event, so every
        locally deleted file came back on the next sync).
        """
        async with self._sync_lock:
            # Recheck after the watcher debounce — editors that save via
            # delete-then-rename briefly look like a deletion.
            if self.fm.note_path_from_file_path(file_path).is_file():
                return

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
        """Handle an externally modified .md file — update SQLite metadata.

        Deliberately does NOT touch ``note_hashes`` here: that key means
        "hash at last SUCCESSFUL cloud sync" and is written by
        ``_push_note``/``_pull_note`` only. Recording the external edit's hash
        optimistically made an offline external edit look already-synced, and
        the next pull clobbered it with older cloud content.
        """
        async with self._sync_lock:
            content = self.fm.read_note(file_path)
            if content is None:
                return

            c_hash = content_hash(content)

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
            # Prefer the CURRENT file over the conflict-time snapshot — the
            # user may have kept editing after the conflict was filed, and
            # reverting to the snapshot would eat those keystrokes.
            current = self.fm.read_note(note_path) if note_path else None
            keep = current if current is not None else local_content
            self.fm.write_note(folder_name, label, keep, note_path)
            if self.is_configured and self._user_id:
                try:
                    await self._push_note(
                        note_id=note_id,
                        label=label,
                        content=keep,
                        folder_name=folder_name,
                        folder_id=folder_id,
                        file_path=note_path,
                        force=True,
                    )
                except Exception:
                    pass
            result["content"] = keep

        elif resolution == "keep_remote":
            written_path = self.fm.write_note(
                folder_name, label, remote_content, note_path
            )
            await repo.set_sync_status(note_id, "synced", remote_hash=content_hash(remote_content))
            # The file now matches the cloud — record the synced hash so pulls
            # treat it as clean (and pushes precondition on the right value).
            state = self.fm.load_sync_state()
            state["note_hashes"][written_path] = content_hash(remote_content)
            self.fm.save_sync_state(state)
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
                        force=True,
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
                        force=True,
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

        return result

    def prune_stale_conflicts(self) -> int:
        """Drop conflicts whose local and remote snapshots are identical.

        A conflict with byte-identical sides carries no decision for the user —
        it is residue from an earlier detection bug (e.g. the pre-2026-07
        own-push false conflicts). Removing it is lossless by definition.
        Returns the number of conflicts pruned.
        """
        pruned = 0
        for note_id in self.fm.list_conflicts():
            conflict_dir = self.fm.base_dir / ".sync" / "conflicts" / note_id
            local_file = conflict_dir / "local.md"
            remote_file = conflict_dir / "remote.md"
            try:
                local_content = (
                    local_file.read_text(encoding="utf-8") if local_file.exists() else None
                )
                remote_content = (
                    remote_file.read_text(encoding="utf-8") if remote_file.exists() else None
                )
            except OSError:
                continue
            if local_content is not None and local_content == remote_content:
                self.fm.resolve_conflict(note_id)
                pruned += 1
        if pruned:
            logger.info("Pruned %d stale identical-content conflicts", pruned)
        return pruned

    # ── Status ───────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        state = self.fm.load_sync_state()
        conflicts = self.fm.list_conflicts()
        return {
            "configured": self.is_configured,
            "device_id": self.device_id,
            "last_pull_at": state.get("last_pull_at"),
            "last_full_sync": state.get("last_full_sync"),
            "tracked_files": len(state.get("note_hashes", {})),
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "watcher_active": self.watcher_active,
            "base_dir": str(self.fm.base_dir),
            # Surface the macOS Full Disk Access / permission-denied condition
            # so the UI can prompt the user with an actionable message instead
            # of silently showing an empty, non-syncing notes list.
            "notes_access_degraded": notes_access_guard.is_degraded,
            "notes_access_reason": notes_access_guard.reason,
        }


# Module-level singleton
sync_engine = SyncEngine()
