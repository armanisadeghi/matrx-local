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
import copy
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.platform_ctx import PLATFORM

from app.services.access_health import get_access_health
from app.services.documents.access_resources import NOTES_RESOURCE
from app.services.documents.async_io import offload_read_only
from app.services.documents.file_manager import (
    DocumentFileManager,
    content_hash,
    file_manager,
)
from app.services.documents.supabase_client import SupabaseDocClient, supabase_docs

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _note_id_for_path(file_path: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"matrx-note:{file_path}"))


def _contested_note_path(file_path: str, note_id: str, attempt: int = 0) -> str:
    """Return a stable sibling path for one cloud note identity.

    A contested cloud path needs a second local file, but a sequence allocated
    from arrival order (``_2``, then ``_3``) makes independent devices keep
    choosing different paths. The cloud note ID gives this replica a stable
    discriminator without changing either note's content or identity.
    """
    path = Path(file_path)
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, note_id).hex[:12]
    retry = "" if attempt == 0 else f"-{attempt + 1}"
    extension = path.suffix or ".md"
    return str(path.with_name(f"{path.stem}--{suffix}{retry}{extension}"))


# ── Mass-delete circuit breaker ──────────────────────────────────────────────
# A sync client must NEVER be able to silently erase a user's cloud corpus.
# 2026-08-06 → 08-08: this engine propagated local tombstones as sequential
# cloud soft-deletes in four waves totalling 2,600+ notes on one account
# (device-stamped, ~1/sec) — including notes the user was actively working
# with. Whatever emptied the local files, the CLOUD must not follow a
# mass-disappearance without explicit human confirmation.
#
# Policy: cloud-delete propagation is budgeted per rolling window. The budget
# is max(MASS_DELETE_MIN_ALLOWANCE, MASS_DELETE_MAX_FRACTION × live remote
# corpus). Exceeding it TRIPS the breaker: further cloud deletes are blocked
# (local tombstones are preserved — nothing is lost, propagation just waits),
# the tripped state persists across restarts in .sync/state.json, and it
# clears only via reset_delete_breaker() (an explicit user action), never
# silently. Every block is logged CRITICAL — a tripped breaker means either a
# real mass deletion (user must confirm) or a sync bug (must be fixed), and
# both deserve eyes.
MASS_DELETE_MIN_ALLOWANCE = 25
MASS_DELETE_MAX_FRACTION = 0.10
MASS_DELETE_WINDOW_SECONDS = 24 * 3600


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
        # The engine is a process singleton, but an HTTP request, the daemon,
        # and the file watcher can overlap.  Principal identity is task-local
        # so a later login cannot turn an already-running note operation into
        # a different user's request.
        self._user_id_context: ContextVar[str | None] = ContextVar(
            f"notes_sync_user_{id(self)}", default=None
        )
        self._watch_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._sync_lock = asyncio.Lock()
        self._last_push_hashes_by_user: dict[str, dict[str, str]] = {}
        self._auto_task: asyncio.Task | None = None
        self._auto_stop = asyncio.Event()
        self._auto_last_skip_reason: str | None = None
        self._auto_last_token: str | None = None
        # Remote folder resolution cache (id-set + name→id), short TTL. The
        # local folder_id is a deterministic uuid5 of the folder NAME (see
        # document_routes._folder_id_for_name) and almost never matches the
        # cloud's uuid4 note_folders.id, so pushing it verbatim violates
        # notes_folder_id_fkey → PostgREST 409. We resolve to a real remote
        # folder (by id-presence, else by name) or fall back to NULL — the
        # folder_name column carries the organization regardless.
        self._folder_cache_by_user: dict[str, tuple[set[str], dict[str, str], float]] = {}
        self._recovery_hook_installed = False

    @property
    def _user_id(self) -> str | None:
        return self._user_id_context.get()

    @_user_id.setter
    def _user_id(self, value: str | None) -> None:
        self._user_id_context.set(value)

    @property
    def _last_push_hashes(self) -> dict[str, str]:
        """Per-account echo state; paths alone are not an identity boundary."""
        user_id = self._user_id
        if not user_id:
            return {}
        return self._last_push_hashes_by_user.setdefault(user_id, {})

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

    def _load_sync_state(self) -> dict[str, Any]:
        """Return state isolated to the operation's authenticated account.

        The legacy top-level state is deliberately left intact: it has no
        principal marker, so assigning it to the next login would adopt an
        unknown account's cursor and hashes.
        """
        state = self.fm.load_sync_state()
        user_id = self._user_id
        if not user_id:
            return {"note_hashes": {}}
        accounts = state.get("accounts")
        if not isinstance(accounts, dict):
            return {"note_hashes": {}}
        account_state = accounts.get(user_id, {})
        result = copy.deepcopy(account_state) if isinstance(account_state, dict) else {}
        result.setdefault("note_hashes", {})
        return result

    def _save_sync_state(self, account_state: dict[str, Any]) -> None:
        user_id = self._user_id
        if not user_id:
            return
        state = self.fm.load_sync_state()
        accounts = state.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
        accounts[user_id] = copy.deepcopy(account_state)
        state["accounts"] = accounts
        self.fm.save_sync_state(state)

    @staticmethod
    def _is_owned_by(row: dict[str, Any] | None, user_id: str) -> bool:
        """Only a known matching owner may participate in account sync."""
        return bool(row and row.get("user_id") and row.get("user_id") == user_id)

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
        access = get_access_health()
        if not access.should_attempt(NOTES_RESOURCE):
            return {
                "sync_skipped": True,
                "reason": access.message(NOTES_RESOURCE),
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
        owner_user_id: str | None = None,
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
                owner_user_id=owner_user_id,
            )

    async def _resolve_remote_folder_id(
        self, folder_id: str | None, folder_name: str | None
    ) -> str | None:
        """Map a note's local folder to a folder_id that EXISTS in
        workbench.note_folders, or ``None``.

        The local folder_id is a uuid5 of the folder name and is not a real
        cloud folder row, so sending it verbatim in a push violates
        ``notes_folder_id_fkey`` (PostgREST → HTTP 409). Resolution order:
          1. If the given folder_id is already a live remote folder, keep it
             (notes created on the frontend carry a real uuid4).
          2. Otherwise match by folder name (case-insensitive) to a live
             remote folder and use its real id.
          3. Otherwise return ``None`` — push with a null folder_id; the
             ``folder_name`` text column preserves the organization and the FK
             is satisfied. No cloud folder is fabricated from the desktop.
        Best-effort: any lookup failure resolves to ``None`` so a folder
        service hiccup never blocks a note push.
        """
        if not folder_id and not folder_name:
            return None
        actor_user_id = self._user_id
        if not actor_user_id:
            return None

        user_id = self._user_id
        if not user_id:
            return None
        now = time.monotonic()
        cache_ids, cache_by_name, cache_at = self._folder_cache_by_user.get(
            user_id, (set(), {}, 0.0)
        )
        if now - cache_at > 60.0:
            try:
                remote_folders = await self.sb.list_folders(user_id)
            except Exception:
                logger.debug("Folder resolve: list_folders failed", exc_info=True)
                remote_folders = []
                # Keep any prior cache; only refresh the timestamp on success.
            else:
                cache_ids = {
                    f["id"] for f in remote_folders if f.get("id")
                }
                cache_by_name = {
                    str(f.get("name", "")).strip().lower(): f["id"]
                    for f in remote_folders
                    if f.get("id") and f.get("name")
                }
                cache_at = now
                self._folder_cache_by_user[user_id] = (
                    cache_ids, cache_by_name, cache_at
                )

        if folder_id and folder_id in cache_ids:
            return folder_id
        if folder_name:
            match = cache_by_name.get(folder_name.strip().lower())
            if match:
                return match
        return None

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
        owner_user_id: str | None = None,
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

        actor_user_id = self._user_id
        if owner_user_id is not None and owner_user_id != actor_user_id:
            return {
                "id": note_id,
                "label": label,
                "_synced_to_cloud": False,
                "_deferred_account": True,
                "reason": "account_changed",
            }

        # A retry/direct push is only eligible when its persisted owner agrees
        # with the task-bound authenticated principal.  Never turn a previous
        # account's local row (or an unknown legacy row) into this account's
        # cloud note.
        repo = self._get_notes_repo()
        local_row = await repo.get(note_id)
        local_owner = str((local_row or {}).get("user_id") or "")
        if local_owner != actor_user_id:
            return {
                "id": note_id,
                "label": label,
                "_synced_to_cloud": False,
                "_deferred_account": True,
                "reason": "foreign_or_unknown_owner",
            }

        if file_path is None:
            # Best effort: recover the real path from SQLite before falling
            # back to the folder+label derivation.
            try:
                row = local_row
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
                state = self._load_sync_state()
                last_synced_hash = (
                    state.get("note_hashes", {}).get(file_path)
                    if not force
                    else None
                )

                # Never push the local uuid5 folder_id verbatim — it is not a
                # real note_folders row and trips notes_folder_id_fkey (409).
                remote_folder_id = await self._resolve_remote_folder_id(
                    folder_id, folder_name
                )

                upsert_body = dict(
                    note_id=note_id,
                    user_id=actor_user_id,
                    label=label,
                    content=content,
                    folder_name=folder_name,
                    folder_id=remote_folder_id,
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
                            "folder_id": remote_folder_id,
                            "file_path": file_path,
                            "tags": tags or [],
                            "metadata": metadata or {},
                        },
                        expected_content_hash=last_synced_hash,
                        device_id=self.device_id,
                        # DD-131 (B-44): this loop is the engine's own
                        # reconciliation, never a live keystroke — a job, not
                        # a person.
                        actor_tier="code",
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
                    # DD-131 (B-44): the sync loop's own push, not a person's.
                    pushed_row = await self.sb.upsert_note(
                        **upsert_body, actor_tier="code"
                    )

                result = pushed_row
                result["_synced_to_cloud"] = True

                # Record the last-SUCCESSFULLY-synced hash only now. Writing it
                # before the push succeeded made a failed/offline push look
                # synced, and the next pull would clobber the newer local file
                # with older cloud content (contract: note_hashes == "hash at
                # last successful sync", docs/SYNC_CONTRACT.md).
                state = self._load_sync_state()
                state.setdefault("note_hashes", {})[file_path] = c_hash
                self._save_sync_state(state)

                await repo.set_sync_status(
                    note_id, "synced", remote_hash=c_hash, user_id=actor_user_id
                )

            except Exception as exc:
                # Expected, actionable cloud rejections (HTTP 4xx: auth,
                # constraint, validation) are STATES, not crashes — one concise
                # line, no traceback. The note stays sync_status=failed and
                # retries silently; a full stack trace on every boot is noise.
                # Reserve tracebacks (DEBUG) for genuinely unexpected failures.
                import httpx as _httpx

                if isinstance(exc, _httpx.HTTPStatusError):
                    logger.warning(
                        "Supabase push for note %s rejected (HTTP %s) — saved "
                        "locally, will retry.",
                        note_id,
                        exc.response.status_code,
                    )
                    logger.debug("push rejection detail for %s", note_id, exc_info=True)
                else:
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
                    await repo.set_sync_status(note_id, "failed", user_id=actor_user_id)
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

    async def _resolve_path_collision_owner(
        self, file_path: str, actor_user_id: str
    ) -> dict[str, Any]:
        """Resolve a live local path collision only when ownership is proven.

        A path can be shared by stale replica rows.  Its current bytes may be
        unsynced user work, so SQLite row order is never evidence of ownership.
        The only safe keeper is a sole local identity, or the unique identity
        whose last remote hash matches the last successful path sync.  When
        clean identical candidates remain, a stable ID breaks the tie.  Every
        other case is deliberately deferred as a conflict.
        """
        repo = self._get_notes_repo()
        owners = await repo.list_live_by_file_path(file_path)
        if any(not self._is_owned_by(owner, actor_user_id) for owner in owners):
            return {"status": "deferred", "owners": owners}
        if len(owners) <= 1:
            return {
                "status": "keeper" if owners else "none",
                "keeper_id": owners[0]["id"] if owners else None,
                "owners": owners,
            }

        known_hash = self._load_sync_state().get("note_hashes", {}).get(file_path)
        matching = [
            owner
            for owner in owners
            if known_hash is not None and owner.get("remote_content_hash") == known_hash
        ]
        if len(matching) == 1:
            return {"status": "keeper", "keeper_id": matching[0]["id"], "owners": owners}
        if len(matching) > 1 and self.fm.note_hash(file_path) == known_hash:
            keeper = min(matching, key=lambda owner: owner["id"])
            return {"status": "keeper", "keeper_id": keeper["id"], "owners": owners}
        return {"status": "ambiguous", "owners": owners}

    @staticmethod
    def _is_nonmaterialized_pull(result: dict[str, Any] | None) -> bool:
        """Whether a pull result intentionally did not materialize remote bytes."""
        return not result or any(
            result.get(marker)
            for marker in (
                "_deleted",
                "_conflict",
                "_collision_conflict",
                "_deferred_account",
                "_skipped_own_push",
                "_skipped_local_tombstone",
                "_path_allocation_failed",
            )
        )

    async def _pull_note(
        self, note_id: str, note: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """``note`` may carry the already-fetched full row (pull_changes gets
        complete rows from get_notes_since) — skipping the refetch removes a
        second network failure point per row."""
        actor_user_id = self._user_id
        if not actor_user_id:
            return None

        if note is None:
            try:
                note = await self.sb.get_note(note_id)
            except Exception:
                logger.warning(
                    "Failed to pull note %s from Supabase", note_id, exc_info=True
                )
                return None

        if not note:
            return None

        remote_owner = note.get("created_by") or note.get("user_id")
        if remote_owner and remote_owner != actor_user_id:
            return {**note, "_deferred_account": True}

        repo = self._get_notes_repo()
        local_row = await repo.get(note_id)
        if local_row is not None and not self._is_owned_by(local_row, actor_user_id):
            return {**note, "_deferred_account": True}

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
                live_owners: list[dict[str, Any]] = []
                try:
                    live_owners = await repo.list_live_by_file_path(fp)
                except Exception:
                    pass
                if any(
                    not self._is_owned_by(owner, actor_user_id)
                    for owner in live_owners
                ):
                    return {**note, "_deferred_account": True}
                if any(owner["id"] != note_id for owner in live_owners):
                    logger.info(
                        "Tombstone for note %s skipped file removal — %s is "
                        "still claimed by another live identity",
                        note_id, fp,
                    )
                else:
                    try:
                        self.fm.delete_note(fp)
                    except Exception:
                        logger.debug("Could not remove local file for deleted note %s", note_id)
                    state = self._load_sync_state()
                    state.get("note_hashes", {}).pop(fp, None)
                    self._save_sync_state(state)
                    self._last_push_hashes.pop(fp, None)
            try:
                await repo.soft_delete(note_id, user_id=actor_user_id)
            except Exception:
                pass
            return {**note, "_deleted": True}

        # Values from other clients can be present-but-null — dict-get
        # defaults don't cover that and None crashes the path builder.
        content = note.get("content") or ""
        label = note.get("label") or "Untitled"
        folder_name = note.get("folder_name") or "General"
        file_path = note.get("file_path")

        # Local tombstone guard: this device deleted the note; don't let a pull
        # of the (not-yet-tombstoned) remote row resurrect it unless the remote
        # CONTENT changed since our last sync (someone genuinely edited it
        # after our delete). Hash comparison is clock-free — comparing the
        # local wall-clock tombstone stamp against the Postgres updated_at let
        # clock skew classify a newer remote edit as stale.
        if local_row and local_row.get("is_deleted") and not note.get("is_deleted"):
            if note.get("content_hash") == local_row.get("remote_content_hash"):
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
            resolution = await self._resolve_path_collision_owner(
                file_path, actor_user_id
            )
            if resolution["status"] == "deferred":
                return {**note, "_deferred_account": True}
            if resolution["status"] == "ambiguous":
                logger.warning(
                    "Deferring contested note path %s for %s — local ownership "
                    "is ambiguous and may contain unsynced work",
                    file_path,
                    note_id,
                )
                return {**note, "_collision_conflict": True}
            if resolution.get("keeper_id") not in (None, note_id):
                if (
                    local_row
                    and local_row.get("file_path") == file_path
                    and local_row.get("sync_status") in {"pending_push", "failed"}
                ):
                    # A sibling allocation would bypass the ordinary path-hash
                    # guard. Keep the incoming identity unmoved until this
                    # local row's pending work is resolved explicitly.
                    return {**note, "_collision_conflict": True}
                # Two same-account cloud identities claim one local path.
                # Preserve BOTH identities even when their bytes match: an
                # early return here hid the incoming cloud record altogether.
                # The per-ID path is deterministic, and the existing CAS
                # repoints only the incoming cloud row, so devices converge
                # without deleting or deduplicating either cloud note.
                contested_path = file_path
                for attempt in range(1_000):
                    candidate = _contested_note_path(contested_path, note_id, attempt)
                    candidate_owners = await repo.list_live_by_file_path(candidate)
                    candidate_content = self.fm.read_note(candidate)
                    if any(owner["id"] != note_id for owner in candidate_owners):
                        continue
                    candidate_is_owned_by_note = any(
                        owner["id"] == note_id for owner in candidate_owners
                    )
                    if candidate_content is not None and not (
                        candidate_is_owned_by_note
                    ):
                        # An unindexed local file is user work too. Never
                        # overwrite it merely to materialize a cloud sibling.
                        continue
                    file_path = candidate
                    note["_reroute_from"] = contested_path
                    if candidate_is_owned_by_note:
                        # This identity was already materialized locally. Let
                        # the normal hash/conflict guard protect a pending edit.
                        note["_existing_identity_path"] = True
                    else:
                        note["_allocated_path"] = True
                    break
                else:
                    logger.error(
                        "Could not allocate a safe local path for contested note %s",
                        note_id,
                    )
                    return {**note, "_path_allocation_failed": True}

        if file_path and not note.get("_allocated_path"):
            local_hash = self.fm.note_hash(file_path)
            remote_hash = note.get("content_hash")
            state = self._load_sync_state()
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

        state = self._load_sync_state()
        state.setdefault("note_hashes", {})[file_path] = c_hash

        # The note moved paths (cross-device rename, or a write-back race lost
        # to another device's allocation). Remove the old file only when this
        # identity still owns it and its bytes are known clean. A contested
        # path can still belong to another live note, or hold unindexed user
        # work; deleting either would turn a path reroute into data loss.
        old_fp = local_row.get("file_path") if local_row else None
        if old_fp and old_fp != file_path:
            old_path_owners = await repo.list_live_by_file_path(old_fp)
            other_old_owners = [
                owner for owner in old_path_owners if owner["id"] != note_id
            ]
            old_content = self.fm.read_note(old_fp)
            old_hash = content_hash(old_content) if old_content is not None else None
            last_synced_old_hash = state.get("note_hashes", {}).get(old_fp)
            can_remove_old_path = (
                old_content is None
                or (
                    not other_old_owners
                    and local_row is not None
                    and not local_row.get("is_deleted")
                    and local_row.get("file_path") == old_fp
                    and last_synced_old_hash is not None
                    and old_hash == last_synced_old_hash
                )
            )
            if can_remove_old_path:
                try:
                    self.fm.delete_note(old_fp)
                except Exception:
                    logger.debug("Could not remove old-path file %s after move", old_fp)
                state.get("note_hashes", {}).pop(old_fp, None)
                self._last_push_hashes.pop(old_fp, None)
            else:
                logger.info(
                    "Preserving old path %s while rerouting note %s — it is "
                    "owned by another identity or contains unproven local work",
                    old_fp,
                    note_id,
                )

        self._save_sync_state(state)

        if note.get("_allocated_path") or note.get("_reroute_from"):
            # Write the allocated path back so every device converges on one
            # canonical location. Conditional (file_path still NULL for fresh
            # allocations, CAS on the contested path for reroutes) so two
            # devices cannot ping-pong allocations, and device-stamped so the
            # resulting realtime UPDATE reads as our own echo.
            try:
                if note.get("_reroute_from"):
                    # DD-131 (B-44): the sync loop repointing its own
                    # bookkeeping column, never a person's edit.
                    won = await self.sb.set_file_path_if_matches(
                        note_id,
                        note["_reroute_from"],
                        file_path,
                        self.device_id,
                        actor_tier="code",
                    )
                else:
                    won = await self.sb.set_file_path_if_null(
                        note_id, file_path, self.device_id, actor_tier="code"
                    )
                if not won:
                    logger.debug(
                        "file_path write-back for %s lost to another device — "
                        "next pull adopts theirs",
                        note_id,
                    )
            except Exception:
                logger.debug("Could not write allocated file_path back to cloud for %s", note_id)

        sv = note.get("sync_version", 0)
        await repo.upsert({
            "id": note_id,
            "user_id": actor_user_id,
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
            state = self._load_sync_state()
            last_pull_at = state.get("last_pull_at")

            try:
                notes = await self.sb.get_notes_since(self._user_id, last_pull_at)
            except Exception:
                logger.warning("Failed to pull changes from Supabase", exc_info=True)
                return {"pulled": 0, "conflicts": 0, "error": "network_error"}

            stats = {"pulled": 0, "conflicts": 0, "deleted": 0, "skipped": 0, "deferred_account": 0}
            deferred = False
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
                    deferred = True
                    stats["failed"] = stats.get("failed", 0) + 1
                    continue
                if result.get("_deferred_account"):
                    stats["deferred_account"] += 1
                    deferred = True
                elif result.get("_deleted"):
                    stats["deleted"] += 1
                elif result.get("_conflict") or result.get("_collision_conflict"):
                    stats["conflicts"] += 1
                elif (
                    result.get("_skipped_own_push")
                    or result.get("_skipped_local_tombstone")
                    or result.get("_path_allocation_failed")
                ):
                    stats["skipped"] += 1
                else:
                    stats["pulled"] += 1

            if not deferred and max_ts and max_ts != last_pull_at:
                # Reload — _pull_note calls above rewrote sync state.
                state = self._load_sync_state()
                state["last_pull_at"] = max_ts
                self._save_sync_state(state)

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
            user_id = self._user_id
            if not user_id:
                return {"error": "Not configured"}
            stats = {"pushed": 0, "failed": 0, "skipped": 0, "conflicts": 0}
            pending = await repo.list_pending_push(user_id)
            if not pending:
                return stats

            open_conflicts = set(self.fm.list_conflicts())

            # Duplicate/resurrection guard (the push_all funnel). full_sync
            # got this guard in the 2026-08 duplicate-factory fix, but the
            # watcher → never_synced SQLite row → push_all path still minted
            # new cloud notes with NO content check — 34 Finder-style
            # "label 2.md" copies became 34 duplicate cloud notes on
            # 2026-08-07 through exactly this funnel. When any pending row
            # would CREATE (never synced) or might RESURRECT (synced before,
            # remote row now absent from the live snapshot), fetch the live
            # snapshot once and check bytes first.
            remote_by_id: dict[str, dict] = {}
            remote_hashes: set[str] = set()
            try:
                for r in await self.sb.get_all_notes_with_hashes(user_id):
                    remote_by_id[r["id"]] = r
                    if r.get("content_hash"):
                        remote_hashes.add(r["content_hash"])
            except Exception:
                logger.warning(
                    "push_all: could not fetch remote snapshot for the "
                    "duplicate guard — deferring pending pushes this tick",
                    exc_info=True,
                )
                return {**stats, "error": "network_error"}

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
                if not fp:
                    stats["skipped"] += 1
                    continue

                content = await offload_read_only(
                    lambda: self.fm.read_eligible_queued_note(fp)
                )
                if content is None:
                    stats["skipped"] += 1
                    continue

                c_hash = content_hash(content)
                remote_row = remote_by_id.get(note["id"])

                if remote_row is None:
                    ever_synced = bool(note.get("remote_content_hash"))
                    if not ever_synced and c_hash in remote_hashes:
                        # This push would CREATE a cloud note whose exact
                        # bytes already exist remotely — a duplicate copy
                        # (Finder "name 2.md", restored file, second engine).
                        # Never mint it; full_sync's adoption pass binds it
                        # to a pathless twin if one exists, and an edit
                        # (diverged hash) syncs it as a new note then.
                        stats["skipped_duplicate"] = (
                            stats.get("skipped_duplicate", 0) + 1
                        )
                        continue
                    if ever_synced:
                        # Synced before but absent from the LIVE snapshot →
                        # the remote row was soft-deleted. upsert_note would
                        # resurrect it; that is correct only for a local
                        # EDIT. For byte-unchanged content the deletion
                        # wins — propagate it locally.
                        if c_hash == note.get("remote_content_hash"):
                            try:
                                full_row = await self.sb.get_note(note["id"])
                            except Exception:
                                full_row = None
                            # Same rule as full_sync's tombstone branch: the
                            # REMOTE tombstone's content_hash must also match
                            # the local file. After a remote edit-then-delete
                            # this device never pulled, the local hash still
                            # equals the stale remote_content_hash while the
                            # tombstone carries the newer hash — deleting then
                            # would diverge from full_sync (which pushes and
                            # resurrects). Only delete what the cloud provably
                            # held when it was deleted; anything else falls
                            # through to the push (resurrect) path.
                            if (
                                full_row is not None
                                and full_row.get("is_deleted")
                                and full_row.get("content_hash") == c_hash
                            ):
                                await self._pull_note(
                                    note["id"], note=full_row
                                )
                                stats["deleted_local"] = (
                                    stats.get("deleted_local", 0) + 1
                                )
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
                    if result.get("_deferred_account"):
                        stats["deferred_account"] = stats.get("deferred_account", 0) + 1
                    elif result.get("_conflict"):
                        stats["conflicts"] += 1
                    elif result.get("_synced_to_cloud"):
                        stats["pushed"] += 1
                except Exception:
                    stats["failed"] += 1

            if stats.get("skipped_duplicate") or stats.get("deleted_local"):
                # Same doctrine as full_sync: recovery worked, but a firing
                # means identity was lost or a deletion raced a push — scream.
                logger.warning(
                    "push_all duplicate guard fired: skipped_duplicate=%d "
                    "deleted_local=%d — pending local files were NOT minted "
                    "as duplicate cloud notes / did not resurrect deletions",
                    stats.get("skipped_duplicate", 0),
                    stats.get("deleted_local", 0),
                )

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

            stats = {"pulled": 0, "conflicts": 0, "skipped": 0, "deferred_account": 0}
            repo = self._get_notes_repo()

            for remote in remote_notes:
                note_id = remote["id"]

                local_note = await repo.get(note_id)
                if local_note and not local_note.get("sync_enabled", True):
                    stats["skipped"] += 1
                    continue

                result = await self._pull_note(note_id)
                if result and result.get("_deferred_account"):
                    stats["deferred_account"] += 1
                elif result and result.get("_collision_conflict"):
                    stats["conflicts"] += 1
                elif result and result.get("_path_allocation_failed"):
                    stats["skipped"] += 1
                elif result and not self._is_nonmaterialized_pull(result):
                    stats["pulled"] += 1
                elif result and result.get("_conflict"):
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
                "skipped": 0,
                "deleted_local": 0,
                "deferred_account": 0,
            }

            skipped = self._access_skipped()
            if skipped is not None:
                return {**stats, **skipped}

            try:
                remote_notes = await self.sb.get_all_notes_with_hashes(self._user_id)
            except Exception:
                return {**stats, "error": "network_error"}

            remote_path_groups: dict[str, list[dict]] = {}
            remote_by_id: dict[str, dict] = {}
            remote_by_hash: dict[str, list[dict]] = {}
            for n in remote_notes:
                if n.get("file_path"):
                    remote_path_groups.setdefault(n["file_path"], []).append(n)
                remote_by_id[n["id"]] = n
                if n.get("content_hash"):
                    remote_by_hash.setdefault(n["content_hash"], []).append(n)

            local_files = await offload_read_only(self.fm.scan_all)
            local_by_path: dict[str, dict] = {f["file_path"]: f for f in local_files}

            state = self._load_sync_state()
            known_hashes = state.get("note_hashes", {})
            repo = self._get_notes_repo()

            # Resolve every contested cloud path before the ordinary
            # reconciliation loop.  A dict keyed by file path cannot choose a
            # primary from arrival order: it would bind unsynced bytes to the
            # wrong cloud identity.  Nonkeepers materialize first; only the
            # evidence-backed keeper reaches normal pull/push reconciliation.
            remote_by_path: dict[str, dict] = {}
            blocked_remote_paths: set[str] = set()
            processed_primary_ids: set[str] = set()

            async def materialize_collision_remote(remote: dict[str, Any]) -> None:
                local_note = await repo.get(remote["id"])
                if local_note and not local_note.get("sync_enabled", True):
                    stats["skipped"] += 1
                    return
                result = await self._pull_note(remote["id"], note=remote)
                if result and result.get("_deferred_account"):
                    stats["deferred_account"] += 1
                elif result and (
                    result.get("_conflict")
                    or result.get("_collision_conflict")
                    or result.get("_path_allocation_failed")
                ):
                    stats["conflicts"] += 1
                elif result and not self._is_nonmaterialized_pull(result):
                    stats["pulled"] += 1

            for fp, group in remote_path_groups.items():
                resolution = await self._resolve_path_collision_owner(
                    fp, self._user_id
                )
                if resolution["status"] == "deferred":
                    stats["deferred_account"] += len(group)
                    blocked_remote_paths.add(fp)
                    continue
                keeper_id = resolution.get("keeper_id")
                if resolution["status"] == "ambiguous":
                    stats["conflicts"] += len(group)
                    blocked_remote_paths.add(fp)
                    continue

                if keeper_id is None:
                    if len(group) == 1:
                        keeper_id = group[0]["id"]
                    elif self.fm.read_note(fp) is None:
                        # A clean initial import has no personal bytes to
                        # protect. A stable cloud ID selects the original-path
                        # keeper before siblings are materialized.
                        keeper_id = min(remote["id"] for remote in group)
                        keeper = next(remote for remote in group if remote["id"] == keeper_id)
                        await materialize_collision_remote(keeper)
                        processed_primary_ids.add(keeper_id)
                    else:
                        stats["conflicts"] += len(group)
                        blocked_remote_paths.add(fp)
                        continue

                group_ids = {remote["id"] for remote in group}
                if keeper_id not in group_ids:
                    # The local keeper has no matching live remote row. Keep
                    # its original bytes and map every incoming identity to a
                    # sibling; the original path is not local-only work.
                    for remote in group:
                        await materialize_collision_remote(remote)
                    blocked_remote_paths.add(fp)
                    continue

                if len(group) > 1:
                    for remote in group:
                        if remote["id"] != keeper_id:
                            await materialize_collision_remote(remote)
                remote_by_path[fp] = next(
                    remote for remote in group if remote["id"] == keeper_id
                )

            for fp, remote in remote_by_path.items():
                note_id = remote["id"]
                if note_id in processed_primary_ids:
                    continue
                resolution = await self._resolve_path_collision_owner(fp, self._user_id)
                if resolution["status"] == "deferred":
                    stats["deferred_account"] += 1
                    continue
                if resolution["status"] == "ambiguous":
                    stats["conflicts"] += 1
                    continue
                if resolution.get("keeper_id") not in (None, note_id):
                    result = await self._pull_note(note_id, note=remote)
                    if result and result.get("_deferred_account"):
                        stats["deferred_account"] += 1
                    elif result and (
                        result.get("_conflict")
                        or result.get("_collision_conflict")
                        or result.get("_path_allocation_failed")
                    ):
                        stats["conflicts"] += 1
                    elif result and not self._is_nonmaterialized_pull(result):
                        stats["pulled"] += 1
                    continue
                local_note = await repo.get(note_id)
                if local_note is not None and not self._is_owned_by(local_note, self._user_id):
                    stats["deferred_account"] += 1
                    continue
                if local_note and not local_note.get("sync_enabled", True):
                    continue

                local = local_by_path.get(fp)

                if local is None:
                    # Local file is gone. If our SQLite row carries a delete
                    # tombstone, the user deleted it HERE — propagate the
                    # deletion instead of resurrecting the note from the cloud.
                    # UNLESS the remote content changed since our last sync:
                    # someone edited the note after our delete, and deleting
                    # their edit would be destructive — resurrect it locally.
                    if local_note and local_note.get("is_deleted"):
                        if remote.get("content_hash") != local_note.get("remote_content_hash"):
                            result = await self._pull_note(note_id)
                            if result and result.get("_deferred_account"):
                                stats["deferred_account"] += 1
                            elif result and (
                                result.get("_conflict")
                                or result.get("_collision_conflict")
                                or result.get("_path_allocation_failed")
                            ):
                                stats["conflicts"] += 1
                            elif result and not self._is_nonmaterialized_pull(result):
                                stats["pulled"] += 1
                            continue
                        if not self.allow_cloud_delete(note_id):
                            stats["deletes_blocked"] = (
                                stats.get("deletes_blocked", 0) + 1
                            )
                            continue
                        try:
                            await self.sb.soft_delete_note(
                                note_id, self.device_id, actor_tier="code"
                            )
                            stats["deleted_local"] += 1
                        except Exception:
                            logger.debug(
                                "Could not propagate local delete for %s", note_id
                            )
                        continue
                    result = await self._pull_note(remote["id"])
                    if result and result.get("_deferred_account"):
                        stats["deferred_account"] += 1
                    elif result and not self._is_nonmaterialized_pull(result):
                        stats["pulled"] += 1

                elif local["content_hash"] == remote.get("content_hash"):
                    stats["unchanged"] += 1
                    # NOTE: must be the remote/SQLite row id — notes created via
                    # the API use uuid4 ids, so the old path-derived uuid5 here
                    # matched zero rows and pending_push rows never converged.
                    await repo.set_sync_status(
                        note_id, "synced",
                        remote_hash=remote.get("content_hash"), user_id=self._user_id
                    )

                elif known_hashes.get(fp) == local["content_hash"]:
                    result = await self._pull_note(remote["id"])
                    if result and result.get("_deferred_account"):
                        stats["deferred_account"] += 1
                    elif result and not self._is_nonmaterialized_pull(result):
                        stats["pulled"] += 1

                elif known_hashes.get(fp) == remote.get("content_hash"):
                    content = self.fm.read_note(fp)
                    if content is not None:
                        result = await self._push_note(
                            note_id=remote["id"],
                            label=remote.get("label", local["label"]),
                            content=content,
                            folder_name=remote.get("folder_name", "General"),
                            folder_id=remote.get("folder_id"),
                            file_path=fp,
                        )
                        if result.get("_deferred_account"):
                            stats["deferred_account"] += 1
                        elif result.get("_synced_to_cloud"):
                            stats["pushed"] += 1
                elif remote.get("last_device_id") == self.device_id:
                    # Divergence, but the cloud row was last written by THIS
                    # device — no other device has contributed since our last
                    # push, so the local file is simply newer unsynced work.
                    # Raising a conflict here made the app accuse the user of
                    # conflicting with their own saves. Push instead.
                    content = self.fm.read_note(fp)
                    if content is not None:
                        result = await self._push_note(
                            note_id=remote["id"],
                            label=remote.get("label", local["label"]),
                            content=content,
                            folder_name=remote.get("folder_name", "General"),
                            folder_id=remote.get("folder_id"),
                            file_path=fp,
                        )
                        if result.get("_deferred_account"):
                            stats["deferred_account"] += 1
                        elif result.get("_synced_to_cloud"):
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
                if fp in blocked_remote_paths:
                    # A collision/deferred group has remote identities at this
                    # path. It is not local-only and must never be pushed.
                    continue
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
                    if local_note is not None and not self._is_owned_by(local_note, self._user_id):
                        # Raw files and legacy/foreign rows stay available for
                        # local editing, but a signed-in account may not adopt
                        # them into its cloud replica.
                        stats["deferred_account"] += 1
                        continue

                    content = self.fm.read_note(fp)
                    if content is None:
                        continue
                    parts = Path(fp).parts
                    folder = parts[0] if len(parts) > 1 else "General"

                    if local_note is None:
                        # Identity loss (fresh SQLite, a second engine on the
                        # same notes dir, a lost path write-back): this file
                        # has no local row and no path match in the cloud.
                        # NEVER mint a new cloud note when its exact bytes
                        # already exist remotely — that is the 2026-07
                        # duplicate factory (764 corpus clones on 07-14,
                        # ~2,100 suffix-chained notes on 07-29/30). Adopt a
                        # pathless remote twin, or skip a bound one.
                        twins = remote_by_hash.get(local["content_hash"], [])
                        adopted = False
                        for twin in twins:
                            if twin.get("file_path"):
                                continue
                            # Pathless remote note with identical bytes:
                            # BIND the local file to it instead of creating.
                            await repo.upsert({
                                "id": twin["id"],
                                "user_id": self._user_id or "",
                                "folder_id": twin.get("folder_id"),
                                "title": local["label"],
                                "label": twin.get("label") or local["label"],
                                "content": content,
                                "content_hash": local["content_hash"],
                                "file_path": fp,
                                "folder_name": twin.get("folder_name") or folder,
                                "sync_status": "synced",
                                "last_synced_at": _now(),
                                "sync_enabled": True,
                                "remote_content_hash": local["content_hash"],
                                "sync_version": twin.get("sync_version", 0),
                            })
                            st = self._load_sync_state()
                            st.setdefault("note_hashes", {})[fp] = local["content_hash"]
                            self._save_sync_state(st)
                            self._last_push_hashes[fp] = local["content_hash"]
                            try:
                                await self.sb.set_file_path_if_null(
                                    twin["id"], fp, self.device_id, actor_tier="code"
                                )
                            except Exception:
                                logger.debug(
                                    "Could not write adopted file_path back "
                                    "for %s", twin["id"],
                                )
                            twin["file_path"] = fp
                            stats["adopted"] = stats.get("adopted", 0) + 1
                            adopted = True
                            break
                        if adopted:
                            continue
                        if twins:
                            # Identical bytes already live in the cloud under
                            # another path — this local file is a redundant
                            # copy. Do not push it as a new note; leave the
                            # file alone (if the user edits it, its hash
                            # diverges and it syncs as a new note then).
                            stats["skipped_duplicate"] = (
                                stats.get("skipped_duplicate", 0) + 1
                            )
                            continue

                        # No authoritative same-account cloud identity exists
                        # for this unindexed file. Keep it local; do not make
                        # its ownership depend on the current login.
                        stats["deferred_account"] += 1
                        continue

                    push_id = (
                        local_note["id"] if local_note else _note_id_for_path(fp)
                    )

                    # Remote-tombstone check before pushing a local-only file
                    # under an existing id: the cloud snapshot excludes
                    # soft-deleted rows, so a note deleted remotely (another
                    # device, or a cloud-side dedup) looks "local-only" here —
                    # and upsert_note deliberately resurrects (deleted_at:
                    # None). Resurrection is correct ONLY for a local EDIT;
                    # for unchanged content the deletion wins and propagates.
                    if local_note is not None:
                        try:
                            remote_row = await self.sb.get_note(push_id)
                        except Exception:
                            remote_row = None
                        if (
                            remote_row is not None
                            and remote_row.get("is_deleted")
                            and remote_row.get("content_hash")
                            == local["content_hash"]
                        ):
                            await self._pull_note(push_id, note=remote_row)
                            stats["deleted_local"] += 1
                            continue

                    result = await self._push_note(
                        note_id=push_id,
                        label=local["label"],
                        content=content,
                        folder_name=(local_note or {}).get("folder_name") or folder,
                        folder_id=(local_note or {}).get("folder_id"),
                        file_path=fp,
                    )
                    if result.get("_deferred_account"):
                        stats["deferred_account"] += 1
                    elif result.get("_synced_to_cloud"):
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
                if local_note is not None and not self._is_owned_by(local_note, self._user_id):
                    stats["deferred_account"] += 1
                    continue
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
                if result and result.get("_deferred_account"):
                    stats["deferred_account"] += 1
                elif result and not self._is_nonmaterialized_pull(result):
                    stats["pulled"] += 1
                    imported += 1
                    if imported % 100 == 0:
                        logger.info(
                            "full_sync: imported %d pathless cloud notes so far…",
                            imported,
                        )
                elif result and (
                    result.get("_conflict")
                    or result.get("_collision_conflict")
                    or result.get("_path_allocation_failed")
                ):
                    stats["conflicts"] += 1

            if stats.get("skipped_duplicate") or stats.get("adopted"):
                # A firing here means note identity was lost somewhere (fresh
                # SQLite, second engine, lost path write-back) and the old code
                # would have minted duplicate cloud notes. Recovery worked, but
                # the underlying identity loss deserves eyes — scream.
                logger.warning(
                    "full_sync duplicate guard fired: adopted=%d "
                    "skipped_duplicate=%d — local files whose bytes already "
                    "exist in the cloud were NOT re-minted as new notes",
                    stats.get("adopted", 0),
                    stats.get("skipped_duplicate", 0),
                )

            # Reload — the pull/push calls above rewrote sync state. Keep
            # this reload→save free of awaits: it must stay atomic on the
            # event loop so a concurrent reset_delete_breaker can never be
            # overwritten by a stale snapshot (see allow_cloud_delete).
            state = self._load_sync_state()
            state["last_full_sync"] = time.time()
            self._save_sync_state(state)
            # Live remote corpus size — scales the mass-delete breaker budget.
            root_state = self.fm.load_sync_state()
            root_state["remote_live_count"] = len(remote_notes)
            self.fm.save_sync_state(root_state)

            if stats.get("deletes_blocked"):
                logger.critical(
                    "full_sync: %d cloud deletions BLOCKED by the mass-delete "
                    "circuit breaker — local tombstones preserved, awaiting "
                    "explicit confirmation (reset_delete_breaker).",
                    stats["deletes_blocked"],
                )

            return stats

    # ── Directory mapping sync ───────────────────────────────────────────────

    async def _sync_mappings(self, file_path: str, folder_id: str | None) -> None:
        if not folder_id:
            return
        local_mappings = self.fm.load_local_mappings()
        mapped_paths = local_mappings.get(folder_id, [])
        if mapped_paths:
            self.fm.sync_to_mapped_dirs(file_path, mapped_paths, folder_id=folder_id)

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
        access = get_access_health()
        if access.is_degraded(NOTES_RESOURCE) or not self.fm.base_dir.exists():
            logger.warning(
                "Document file watcher NOT started — notes dir inaccessible (%s)",
                access.message(NOTES_RESOURCE),
            )
            return
        self._stop_event.clear()
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info("Document file watcher started: %s", self.fm.base_dir)

    def ensure_recovery_hook(self) -> None:
        """Restart the watcher THE MOMENT notes access recovers.

        Without this, a recovered denial left the watcher dead for up to a
        full auto-sync interval (600s) — the user granted access, the prompt
        cleared, and external edits still went unnoticed for ten minutes.
        """
        if self._recovery_hook_installed:
            return
        self._recovery_hook_installed = True
        get_access_health().on_transition(
            NOTES_RESOURCE, self._on_notes_access_transition
        )

    def _on_notes_access_transition(self, _resource_id: str, _old: str, new: str):
        if new != "ok":
            return None
        # Returned coroutine is scheduled on the engine loop by the access
        # service (transitions can originate on the watchfiles thread or a
        # to_thread probe worker).
        return self._resume_after_recovery()

    async def _resume_after_recovery(self) -> None:
        logger.info(
            "Notes access recovered — restarting file watcher immediately"
        )
        await self.start_watcher()

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
    # (kept fresh by the sync daemon's session grant, not a direct write
    # route — see app/services/session_freshness.py), the watcher is
    # ensured, and an incremental pull + pending push runs every tick, with a
    # full reconcile when the last one is older than _FULL_SYNC_MAX_AGE_S.

    _FULL_SYNC_MAX_AGE_S = 24 * 3600

    @property
    def auto_sync_active(self) -> bool:
        return self._auto_task is not None and not self._auto_task.done()

    async def start_background_sync(self, interval_seconds: int = 600) -> None:
        if self.auto_sync_active:
            return
        self.ensure_recovery_hook()
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
                from app.services.session_freshness import session_blocker

                blocker = session_blocker(lane="documents_sync")
                logger.warning(
                    "Notes auto-sync idle — stored JWT is expired; %s",
                    blocker["remedy"] or blocker["message"],
                )
                self._auto_last_skip_reason = "expired"
            return
        self._auto_last_skip_reason = None

        # This daemon has its own ContextVar context. Bind on every tick so it
        # cannot inherit a request principal, while request tasks retain theirs.
        self.configure(row["user_id"], row["access_token"])
        self._auto_last_token = row["access_token"]

        if not self.watcher_active:
            await self.start_watcher()

        pull = await self.pull_changes()
        push = await self.push_all()

        state = self._load_sync_state()
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
                                    await offload_read_only(
                                        lambda: path.read_text(encoding="utf-8")
                                    )
                                )
                            except OSError:
                                continue
                            if self._last_push_hashes.get(rel_path) == current_hash:
                                continue
                        logger.info("External change detected: %s", rel_path)
                        await self._handle_external_change(rel_path)

        except ImportError:
            logger.info("watchfiles not available, using polling for document watch")
            state = self._load_sync_state()
            known = dict(state.get("note_hashes", {}))

            while not self._stop_event.is_set():
                await asyncio.sleep(5)
                current_files = await offload_read_only(self.fm.scan_all)
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

    async def _bind_watcher_principal(self) -> str | None:
        """Bind the watcher task to the current persisted session, if any."""
        from app.services.local_db.repositories import TokenRepo

        try:
            row = await TokenRepo().get()
        except RuntimeError:
            # No durable session is a signed-out watcher, never permission to
            # reuse an inherited request ContextVar/JWT.
            return None
        if not row or not row.get("user_id") or not row.get("access_token"):
            return None
        self.configure(row["user_id"], row["access_token"])
        return row["user_id"]

    async def _handle_external_delete(self, file_path: str) -> None:
        """Handle an externally deleted .md file.

        Records a delete tombstone in SQLite and drops the sync-state hash so
        the next full_sync propagates the deletion instead of re-pulling the
        note from the cloud (the old behavior only logged the event, so every
        locally deleted file came back on the next sync).
        """
        async with self._sync_lock:
            actor_user_id = await self._bind_watcher_principal()
            if not actor_user_id:
                return
            # Recheck after the watcher debounce — editors that save via
            # delete-then-rename briefly look like a deletion.
            if self.fm.note_path_from_file_path(file_path).is_file():
                return

            state = self._load_sync_state()
            state.get("note_hashes", {}).pop(file_path, None)
            self._save_sync_state(state)
            self._last_push_hashes.pop(file_path, None)

            repo = self._get_notes_repo()
            row = await repo.get_by_file_path(file_path)
            if row is None:
                row = await repo.get(_note_id_for_path(file_path))
            if row is None:
                return
            if not self._is_owned_by(row, actor_user_id):
                return
            try:
                await repo.soft_delete(row["id"], user_id=actor_user_id)
            except Exception:
                logger.debug("Could not tombstone deleted note %s", file_path)
                return

            if self.is_configured and self._user_id:
                if not self.allow_cloud_delete(row["id"]):
                    # Breaker tripped (mass local file disappearance) — the
                    # local tombstone above is kept; cloud propagation waits
                    # for explicit confirmation.
                    return
                try:
                    await self.sb.soft_delete_note(
                        row["id"], self.device_id, actor_tier="code"
                    )
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
            actor_user_id = await self._bind_watcher_principal()
            if not actor_user_id:
                return
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
            # A watcher must not turn an arbitrary file into the account that
            # happened to be signed in when it observed the change.
            if not self._is_owned_by(existing, actor_user_id):
                return
            note_id = existing["id"]

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
                        # Same anti-duplicate guard as full_sync: if these
                        # exact bytes already live in the cloud under another
                        # note, this "new" file is a redundant copy (usually a
                        # sync artifact) — pushing it would mint a duplicate
                        # cloud note. full_sync's adoption pass will bind it
                        # if a pathless twin exists.
                        if any(
                            n.get("content_hash") == c_hash for n in all_notes
                        ):
                            logger.warning(
                                "Not pushing %s as a new note — identical "
                                "content already exists in the cloud "
                                "(duplicate guard)",
                                file_path,
                            )
                            return
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
        actor_user_id = self._user_id
        if not actor_user_id or not self._is_owned_by(sqlite_note, actor_user_id):
            return {"id": note_id, "_deferred_account": True}
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
            await repo.set_sync_status(
                note_id, "synced", remote_hash=content_hash(remote_content), user_id=actor_user_id
            )
            # The file now matches the cloud — record the synced hash so pulls
            # treat it as clean (and pushes precondition on the right value).
            state = self._load_sync_state()
            state.setdefault("note_hashes", {})[written_path] = content_hash(remote_content)
            self._save_sync_state(state)
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

    # ── Mass-delete circuit breaker ──────────────────────────────────────────

    def _delete_budget(self) -> int:
        """Cloud-deletes allowed per rolling window on this device.

        Scales with the last observed live remote corpus (recorded by
        full_sync) so a 30-note user and a 4,000-note user both get sane
        limits; falls back to the flat minimum before the first full_sync.
        """
        state = self.fm.load_sync_state()
        remote_count = int(state.get("remote_live_count") or 0)
        return max(
            MASS_DELETE_MIN_ALLOWANCE,
            int(remote_count * MASS_DELETE_MAX_FRACTION),
        )

    @property
    def delete_breaker_tripped(self) -> bool:
        return bool(self.fm.load_sync_state().get("delete_breaker"))

    def allow_cloud_delete(self, note_id: str) -> bool:
        """Gate EVERY cloud-delete propagation through the circuit breaker.

        Returns True and records the delete when within budget. Returns False
        when the breaker is (or just became) tripped — the caller must skip
        the cloud delete and leave the local tombstone in place for a later,
        human-confirmed propagation. Never raises.

        The window is keyed by NOTE ID, not by attempt: a failed propagation
        retried by full_sync or the watcher re-charges the same slot instead
        of burning fresh budget (a flaky network must not trip the breaker on
        25 pending tombstones), while 25 DISTINCT deletions always count as
        25 no matter how they interleave with retries.

        CONCURRENCY INVARIANT: this method (and reset_delete_breaker, and
        full_sync's final reload-and-save) performs its load→modify→save with
        NO await in between, so on the single asyncio loop each call is
        atomic — no lock is needed and none is taken. Do not introduce an
        await between load_sync_state and save_sync_state here.
        """
        state = self.fm.load_sync_state()
        if state.get("delete_breaker"):
            logger.critical(
                "[delete-breaker] BLOCKED cloud delete of note %s — breaker "
                "tripped at %s after %s deletes. Local tombstone kept; "
                "propagation resumes only after explicit reset "
                "(reset_delete_breaker).",
                note_id,
                state["delete_breaker"].get("tripped_at"),
                state["delete_breaker"].get("count"),
            )
            return False

        now = time.time()
        raw_window = state.get("delete_window", {})
        if isinstance(raw_window, list):
            # Legacy shape (pre note-id keying): timestamps only. Adopt them
            # under synthetic keys so recent spend is not forgotten.
            raw_window = {f"_legacy_{i}": t for i, t in enumerate(raw_window)}
        window: dict[str, float] = {
            nid: t
            for nid, t in raw_window.items()
            if now - t < MASS_DELETE_WINDOW_SECONDS
        }
        budget = self._delete_budget()
        if note_id not in window and len(window) >= budget:
            state["delete_breaker"] = {
                "tripped_at": _now(),
                "count": len(window),
                "budget": budget,
                "device_id": self.device_id,
            }
            state["delete_window"] = window
            self.fm.save_sync_state(state)
            logger.critical(
                "[delete-breaker] TRIPPED: this device attempted to propagate "
                "more than %d cloud note deletions inside %d hours. Cloud "
                "deletes are now HALTED (local tombstones preserved) until "
                "the user explicitly confirms via reset_delete_breaker(). If "
                "you did not intentionally delete this many notes, this is a "
                "sync defect — report it before resetting.",
                budget,
                MASS_DELETE_WINDOW_SECONDS // 3600,
            )
            return False

        window[note_id] = now
        state["delete_window"] = window
        self.fm.save_sync_state(state)
        return True

    def reset_delete_breaker(self) -> dict[str, Any]:
        """Explicit human confirmation that pending mass deletion is intended.

        Clears the tripped state AND the rolling window so confirmed bulk
        cleanups can proceed. Returns the state that was cleared.

        CONCURRENCY INVARIANT: load→modify→save with no await in between —
        atomic on the asyncio loop (see allow_cloud_delete). A reset issued
        while a full_sync is mid-run cannot be clobbered: every breaker
        reader reloads state fresh, and full_sync's final save also reloads
        immediately before writing.
        """
        state = self.fm.load_sync_state()
        cleared = state.pop("delete_breaker", None)
        state["delete_window"] = {}
        self.fm.save_sync_state(state)
        if cleared:
            # NB: str() — a lone dict arg would hit logging's mapping
            # special-case and raise "not all arguments converted".
            logger.warning(
                "[delete-breaker] reset by explicit request — cleared %s",
                str(cleared),
            )
        return {"cleared": cleared}

    # ── Status ───────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        state = self._load_sync_state()
        conflicts = self.fm.list_conflicts()
        # Access-degraded is surfaced so the UI can prompt with an actionable,
        # EVIDENCE-BASED message instead of silently showing an empty list.
        # Field names are a frontend contract — keep them stable.
        notes_health = get_access_health().health(NOTES_RESOURCE) or {}
        degraded = notes_health.get("status") == "degraded"
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
            "notes_access_degraded": degraded,
            "notes_access_reason": notes_health.get("message") if degraded else None,
            "notes_access_kind": notes_health.get("kind"),
            "delete_breaker": self.fm.load_sync_state().get("delete_breaker"),
        }


# Module-level singleton
sync_engine = SyncEngine()
