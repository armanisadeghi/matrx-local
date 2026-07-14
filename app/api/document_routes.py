"""Notes management API routes.

Architecture: LOCAL FIRST. Always.

Every CRUD operation reads/writes the local filesystem immediately, then
persists structured metadata to the local SQLite database. Both operations
are synchronous from the caller's perspective and never require network.

Cloud sync (Supabase) is a completely separate, user-triggered concern.
No cloud operation ever blocks, fails, or degrades a local operation.
Cloud connection errors in non-sync code paths are silent.

Route prefix: /notes  (was /documents — kept as alias in main.py for compatibility)
"""

from __future__ import annotations

import asyncio
import base64
import json as _json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.services.documents.file_manager import file_manager, content_hash
from app.services.documents.supabase_client import supabase_docs
from app.services.documents.sync_engine import sync_engine
from app.services.local_db.repositories import NotesRepo, NoteVersionsRepo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["notes"])

_notes_repo: NotesRepo | None = None
_versions_repo: NoteVersionsRepo | None = None


def _get_notes_repo() -> NotesRepo:
    global _notes_repo
    if _notes_repo is None:
        _notes_repo = NotesRepo()
    return _notes_repo


def _get_versions_repo() -> NoteVersionsRepo:
    global _versions_repo
    if _versions_repo is None:
        _versions_repo = NoteVersionsRepo()
    return _versions_repo


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_user_id(request: Request) -> str:
    uid = _get_user_id_optional(request)
    if uid:
        return uid
    raise HTTPException(
        status_code=401,
        detail="Could not determine user identity — provide Authorization header or X-User-Id",
    )


def _get_user_id_optional(request: Request) -> str | None:
    explicit = request.headers.get("X-User-Id")
    if explicit and explicit != "local":
        return explicit

    token = getattr(request.state, "user_token", None)
    if token:
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
            sub = payload.get("sub")
            if sub:
                return sub
        except Exception:
            pass

    return None


def _configure_sync(request: Request) -> None:
    try:
        user_id = _get_user_id(request)
        token = getattr(request.state, "user_token", None)
        if token:
            sync_engine.configure(user_id, token)
            supabase_docs.set_jwt(token)
    except HTTPException:
        pass

    # Auto-start the external-edit file watcher on first documents traffic.
    # It previously existed behind POST /sync/start-watcher, which no surface
    # ever called — external edits and deletes were never detected. Idempotent
    # (start_watcher no-ops while a task is live) and access-guarded.
    if not sync_engine.watcher_active:
        _fire_and_forget(sync_engine.start_watcher())


def _fire_and_forget(coro) -> None:
    # Delegates to the shared retained helper — bare create_task results are
    # only weakly referenced by the loop and can be GC'd mid-flight.
    from app.common.background_tasks import fire_and_forget

    fire_and_forget(coro, name="documents-sync")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CreateFolderRequest(BaseModel):
    name: str
    parent_id: str | None = None


class UpdateFolderRequest(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    path: str | None = None
    position: int | None = None


class CreateNoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    label: str = Field("New Note", validation_alias=AliasChoices("label", "title"))
    content: str = ""
    folder_name: str = "General"
    folder_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateNoteRequest(BaseModel):
    label: str | None = None
    content: str | None = None
    folder_name: str | None = None
    folder_id: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    position: int | None = None


class RevertRequest(BaseModel):
    version_number: int


class ShareRequest(BaseModel):
    note_id: str | None = None
    folder_id: str | None = None
    shared_with_id: str | None = None
    permission: str = "read"
    is_public: bool = False


class UpdateShareRequest(BaseModel):
    permission: str | None = None
    is_public: bool | None = None


class MappingRequest(BaseModel):
    folder_id: str
    local_path: str


class ConflictResolveRequest(BaseModel):
    resolution: str = "keep_remote"  # keep_local | keep_remote | merge | append | split | exclude
    merged_content: str | None = None


class PullRemoteChangeRequest(BaseModel):
    note_id: str


class SyncTriggerRequest(BaseModel):
    mode: str = "bidirectional"


class NoteExcludeRequest(BaseModel):
    excluded: bool = True


# ---------------------------------------------------------------------------
# Folder helpers — local filesystem is the source of truth
# ---------------------------------------------------------------------------

def _folder_id_for_name(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"matrx-notes-folder:{name}"))


def _note_id_for_path(file_path: str) -> str:
    """Legacy: derive a deterministic UUID5 from a file path.

    Only used as a fallback to look up pre-existing notes that were created
    before the UUID4 migration. New notes always get a random UUID4 stored in
    SQLite, so their ID never changes when the file is renamed.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"matrx-note:{file_path}"))


def _unique_file_path(folder_name: str, label: str) -> str:
    """Collision-free relative path — canonical impl lives in file_manager."""
    return file_manager.unique_file_path(folder_name, label)


def _local_folder_tree() -> dict[str, Any]:
    folders_raw = file_manager.list_folders()
    all_files = file_manager.scan_all()

    folder_counts: dict[str, int] = {}
    for f in all_files:
        parts = Path(f["file_path"]).parts
        folder_name = parts[0] if len(parts) > 1 else "__root__"
        folder_counts[folder_name] = folder_counts.get(folder_name, 0) + 1

    folders: list[dict[str, Any]] = []
    for name in folders_raw:
        folder_id = _folder_id_for_name(name)
        folders.append({
            "id": folder_id,
            "name": name,
            "path": name,
            "parent_id": None,
            "position": 0,
            "is_deleted": False,
            "note_count": folder_counts.get(name, 0),
            "children": [],
            "_source": "local",
        })

    return {
        "folders": folders,
        "total_notes": len(all_files),
        "unfiled_notes": folder_counts.get("__root__", 0),
    }


def _file_timestamps(file_path: str) -> tuple[str, str]:
    """Get created_at and updated_at from file system metadata."""
    abs_path = file_manager.note_path_from_file_path(file_path)
    if abs_path.exists():
        stat = abs_path.stat()
        created = datetime.fromtimestamp(stat.st_birthtime if hasattr(stat, 'st_birthtime') else stat.st_ctime, tz=timezone.utc)
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        return (
            created.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            modified.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
    now = _now()
    return now, now


def _build_note_record(file_path: str, folder_name: str | None = None) -> dict[str, Any] | None:
    content = file_manager.read_note(file_path)
    if content is None:
        return None
    p = Path(file_path)
    folder = folder_name or (p.parts[0] if len(p.parts) > 1 else "General")
    created_at, updated_at = _file_timestamps(file_path)
    return {
        "id": _note_id_for_path(file_path),
        "label": p.stem,
        "content": content,
        "folder_name": folder,
        "folder_id": _folder_id_for_name(folder),
        "file_path": file_path,
        "tags": [],
        "metadata": {},
        "content_hash": content_hash(content),
        "sync_version": 0,
        "is_deleted": False,
        "created_at": created_at,
        "updated_at": updated_at,
        "_source": "local",
    }


async def _sync_note_to_sqlite(
    note_id: str,
    label: str,
    content: str,
    folder_name: str,
    folder_id: str | None,
    file_path: str,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    is_new: bool = False,
) -> None:
    """Persist note metadata to SQLite for sync tracking.

    Also creates a local version snapshot when content changes, so version
    history works fully offline without Supabase.
    """
    repo = _get_notes_repo()
    c_hash = content_hash(content)

    existing = await repo.get(note_id)
    now = _now()

    # Create a local version snapshot if content actually changed
    if existing and existing.get("content") and existing.get("content_hash") != c_hash:
        try:
            versions_repo = _get_versions_repo()
            await versions_repo.create_snapshot(
                note_id=note_id,
                content=existing["content"],
                label=existing.get("label", existing.get("title", "")),
                user_id=existing.get("user_id", ""),
                change_source="local",
            )
            # Keep version history manageable
            await versions_repo.prune(note_id, keep=100)
        except Exception:
            logger.debug("Local version snapshot failed (non-critical)", exc_info=True)

    note_data: dict[str, Any] = {
        "id": note_id,
        "user_id": "",
        "folder_id": folder_id,
        "title": label,
        "label": label,
        "content": content,
        "content_hash": c_hash,
        "file_path": file_path,
        "is_deleted": False,
        "is_pinned": existing.get("is_pinned", False) if existing else False,
        "tags": tags or (existing.get("tags", []) if existing else []),
        "sync_version": existing.get("sync_version", 0) if existing else 0,
        "folder_name": folder_name,
        "metadata": metadata or (existing.get("metadata", {}) if existing else {}),
        "updated_at": now,
    }

    if existing:
        if existing.get("sync_status") == "synced":
            note_data["sync_status"] = "pending_push"
        elif existing.get("sync_status") == "excluded":
            note_data["sync_status"] = "excluded"
        else:
            note_data["sync_status"] = existing.get("sync_status", "never_synced")
        note_data["sync_enabled"] = existing.get("sync_enabled", True)
        note_data["last_synced_at"] = existing.get("last_synced_at")
        note_data["remote_content_hash"] = existing.get("remote_content_hash")
        note_data["created_at"] = existing.get("created_at", now)
    else:
        note_data["sync_status"] = "never_synced"
        note_data["sync_enabled"] = True
        note_data["created_at"] = now

    await repo.upsert(note_data)


# ---------------------------------------------------------------------------
# Folder endpoints
# ---------------------------------------------------------------------------

@router.get("/tree")
async def get_folder_tree(request: Request) -> dict[str, Any]:
    _configure_sync(request)
    return _local_folder_tree()


@router.post("/folders")
async def create_folder(req: CreateFolderRequest, request: Request) -> dict[str, Any]:
    _configure_sync(request)
    user_id = _get_user_id_optional(request)

    path = req.name
    if req.parent_id:
        folders = file_manager.list_folders()
        for f in folders:
            fid = _folder_id_for_name(f)
            if fid == req.parent_id:
                path = f"{f}/{req.name}"
                break

    file_manager.create_folder(path)
    folder_id = _folder_id_for_name(path)

    result: dict[str, Any] = {
        "id": folder_id,
        "name": req.name,
        "path": path,
        "parent_id": req.parent_id,
        "position": 0,
        "is_deleted": False,
        "_source": "local",
    }

    if sync_engine.is_configured and user_id:
        async def _sync_folder():
            await supabase_docs.create_folder(
                user_id=user_id,
                name=req.name,
                parent_id=req.parent_id,
                path=path,
            )
        _fire_and_forget(_sync_folder())

    return result


@router.put("/folders/{folder_id}")
async def update_folder(
    folder_id: str, req: UpdateFolderRequest, request: Request
) -> dict[str, Any]:
    _configure_sync(request)

    if req.name:
        for f in file_manager.list_folders():
            fid = _folder_id_for_name(f)
            if fid == folder_id:
                file_manager.rename_folder(f, req.name)
                break

    updates: dict[str, Any] = {}
    if req.name is not None:
        updates["name"] = req.name
    if req.parent_id is not None:
        updates["parent_id"] = req.parent_id
    if req.path is not None:
        updates["path"] = req.path
    if req.position is not None:
        updates["position"] = req.position

    if updates and sync_engine.is_configured:
        _fire_and_forget(supabase_docs.update_folder(folder_id, updates))

    return {"id": folder_id, **updates, "_source": "local"}


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, request: Request) -> dict[str, str]:
    _configure_sync(request)
    repo = _get_notes_repo()

    # Hold the sync lock: deleting files + writing tombstones must not
    # interleave with a full_sync/pull that snapshotted the old state — the
    # race re-pulled just-deleted notes and erased the fresh tombstones.
    async with sync_engine.sync_lock:
        await _delete_folder_locked(folder_id, repo)

    if sync_engine.is_configured:
        _fire_and_forget(supabase_docs.delete_folder(folder_id))

    return {"status": "deleted"}


async def _delete_folder_locked(folder_id: str, repo: NotesRepo) -> None:
    for f in file_manager.list_folders():
        fid = _folder_id_for_name(f)
        if fid == folder_id:
            # Tombstone every contained note BEFORE removing the directory
            # (MXL-D-002). Without SQLite tombstones, an offline folder delete
            # left full_sync seeing "local file gone, no tombstone" and it
            # RESURRECTED every contained note from the cloud. With the
            # tombstones, the existing full_sync branch propagates the deletes
            # when connectivity returns; the per-note cloud soft-deletes below
            # cover the online case immediately.
            contained = [
                nf for nf in file_manager.scan_all()
                if Path(nf["file_path"]).parts[:1] == (f,)
            ]
            for nf in contained:
                row = await repo.get_by_file_path(nf["file_path"])
                if row is None:
                    row = await repo.get(_note_id_for_path(nf["file_path"]))
                del_id = row["id"] if row else _note_id_for_path(nf["file_path"])
                try:
                    await repo.soft_delete(del_id)
                except Exception:
                    logger.warning(
                        "Could not tombstone note %s during folder delete",
                        nf["file_path"], exc_info=True,
                    )
                if sync_engine.is_configured:
                    _fire_and_forget(
                        supabase_docs.soft_delete_note(del_id, sync_engine.device_id)
                    )

            file_manager.delete_folder(f)

            # Drop the last-synced hashes for the removed files so sync state
            # doesn't keep tracking paths that no longer exist.
            try:
                state = file_manager.load_sync_state()
                hashes = state.get("note_hashes", {})
                removed = [nf["file_path"] for nf in contained]
                if any(fp in hashes for fp in removed):
                    for fp in removed:
                        hashes.pop(fp, None)
                    file_manager.save_sync_state(state)
            except Exception:
                logger.warning("Could not prune sync-state hashes on folder delete", exc_info=True)
            break


# ---------------------------------------------------------------------------
# Note endpoints
# ---------------------------------------------------------------------------

@router.get("/notes")
async def list_notes(
    request: Request,
    folder_id: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    _configure_sync(request)

    all_files = file_manager.scan_all()
    results: list[dict[str, Any]] = []
    repo = _get_notes_repo()

    for f in all_files:
        if folder_id:
            p = Path(f["file_path"])
            folder_name = p.parts[0] if len(p.parts) > 1 else "General"
            fid = _folder_id_for_name(folder_name)
            if fid != folder_id:
                continue

        if search:
            file_content = file_manager.read_note(f["file_path"]) or ""
            label = f.get("label", "")
            if search.lower() not in file_content.lower() and search.lower() not in label.lower():
                continue

        p = Path(f["file_path"])
        folder_name = p.parts[0] if len(p.parts) > 1 else "General"

        # SQLite is the canonical ID store for new notes (UUID4).  For legacy
        # notes (created before the UUID4 migration) the SQLite row may not exist
        # yet — fall back to the deterministic UUID5 so old IDs stay stable.
        sqlite_note = await repo.get_by_file_path(f["file_path"])
        if sqlite_note is None:
            legacy_id = _note_id_for_path(f["file_path"])
            sqlite_note = await repo.get(legacy_id)
        note_id = sqlite_note["id"] if sqlite_note else _note_id_for_path(f["file_path"])

        # Use the label stored in SQLite (reflects renames) when available;
        # otherwise fall back to the filename stem.
        label = (sqlite_note.get("label") or sqlite_note.get("title") or f["label"]) if sqlite_note else f["label"]

        created_at, updated_at = _file_timestamps(f["file_path"])

        results.append({
            "id": note_id,
            "label": label,
            "folder_name": folder_name,
            "folder_id": _folder_id_for_name(folder_name),
            "file_path": f["file_path"],
            "content_hash": f["content_hash"],
            "tags": sqlite_note.get("tags", []) if sqlite_note else [],
            "metadata": sqlite_note.get("metadata", {}) if sqlite_note else {},
            "is_deleted": False,
            "sync_status": sqlite_note.get("sync_status", "never_synced") if sqlite_note else "never_synced",
            "sync_enabled": sqlite_note.get("sync_enabled", True) if sqlite_note else True,
            "created_at": sqlite_note.get("created_at", created_at) if sqlite_note else created_at,
            "updated_at": updated_at,
            "_source": "local",
        })

    return results


def _enrich_record_from_sqlite(record: dict[str, Any], sqlite_note: dict[str, Any]) -> dict[str, Any]:
    """Overlay SQLite metadata (tags, sync status, canonical label) onto a file record."""
    record["tags"] = sqlite_note.get("tags", [])
    record["metadata"] = sqlite_note.get("metadata", {})
    record["sync_status"] = sqlite_note.get("sync_status", "never_synced")
    record["sync_enabled"] = sqlite_note.get("sync_enabled", True)
    record["created_at"] = sqlite_note.get("created_at", record.get("created_at", ""))
    # Use the SQLite label (reflects renames) rather than the filename stem.
    stored_label = sqlite_note.get("label") or sqlite_note.get("title")
    if stored_label:
        record["label"] = stored_label
    return record


@router.get("/notes/{note_id}")
async def get_note(note_id: str, request: Request) -> dict[str, Any]:
    _configure_sync(request)
    repo = _get_notes_repo()

    # Primary: SQLite has the canonical note_id → file_path mapping for new notes.
    sqlite_note = await repo.get(note_id)
    if sqlite_note and sqlite_note.get("file_path"):
        record = _build_note_record(sqlite_note["file_path"])
        if record:
            record["id"] = note_id  # preserve the canonical ID, not the UUID5 from the file path
            return _enrich_record_from_sqlite(record, sqlite_note)

    # Secondary: filesystem scan — catches legacy notes (UUID5 IDs) not yet in SQLite.
    for f in file_manager.scan_all():
        if _note_id_for_path(f["file_path"]) == note_id:
            record = _build_note_record(f["file_path"])
            if record:
                sqlite_note2 = await repo.get(note_id)
                if sqlite_note2:
                    return _enrich_record_from_sqlite(record, sqlite_note2)
                return record

    # Tertiary: non-blocking cloud fallback with short timeout. A soft-deleted
    # remote row is NOT a note — returning it kept remotely-deleted notes open
    # in the editor, and the next keystroke recreated them.
    if sync_engine.is_configured:
        try:
            remote = await asyncio.wait_for(supabase_docs.get_note(note_id), timeout=5.0)
            if remote and not remote.get("is_deleted"):
                _fire_and_forget(sync_engine.pull_note(note_id))
                return remote
        except (asyncio.TimeoutError, Exception):
            pass

    raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")


@router.post("/notes")
async def create_note(req: CreateNoteRequest, request: Request) -> dict[str, Any]:
    _configure_sync(request)
    user_id = _get_user_id_optional(request)

    # UUID4 — stable identifier that never changes when the file is renamed.
    note_id = str(uuid.uuid4())
    # Resolve a collision-free file path before writing.
    file_path = _unique_file_path(req.folder_name, req.label)
    file_manager.write_note(req.folder_name, req.label, req.content, file_path)
    c_hash = content_hash(req.content)
    now = _now()

    result: dict[str, Any] = {
        "id": note_id,
        "label": req.label,
        "content": req.content,
        "folder_name": req.folder_name,
        "folder_id": req.folder_id,
        "file_path": file_path,
        "tags": req.tags,
        "metadata": req.metadata,
        "content_hash": c_hash,
        "sync_status": "never_synced",
        "sync_enabled": True,
        "created_at": now,
        "updated_at": now,
        "_synced_to_cloud": False,
        "_source": "local",
    }

    await _sync_note_to_sqlite(
        note_id=note_id,
        label=req.label,
        content=req.content,
        folder_name=req.folder_name,
        folder_id=req.folder_id,
        file_path=file_path,
        tags=req.tags,
        metadata=req.metadata,
        is_new=True,
    )

    logger.info(
        "[create_note] Created note id=%s file_path=%s folder=%s label=%r",
        note_id, file_path, req.folder_name, req.label,
    )

    if sync_engine.is_configured and user_id:
        _fire_and_forget(sync_engine.push_note(
            note_id=note_id,
            label=req.label,
            content=req.content,
            folder_name=req.folder_name,
            folder_id=req.folder_id,
            tags=req.tags,
            metadata=req.metadata,
            file_path=file_path,
        ))

    return result


@router.put("/notes/{note_id}")
async def update_note(
    note_id: str, req: UpdateNoteRequest, request: Request
) -> dict[str, Any]:
    _configure_sync(request)

    existing_record: dict[str, Any] | None = None

    # Fast path: SQLite is the cheap O(1) lookup — try it first.
    repo = _get_notes_repo()
    sqlite_note = await repo.get(note_id)
    if sqlite_note and sqlite_note.get("file_path"):
        fp = sqlite_note["file_path"]
        if file_manager.note_path_from_file_path(fp).is_file():
            existing_record = _build_note_record(fp)

    # Slow path: walk the filesystem — only needed when SQLite doesn't have a
    # record yet (e.g. first save after engine restart or db corruption).
    if existing_record is None:
        all_files = file_manager.scan_all()
        for f in all_files:
            candidate_id = _note_id_for_path(f["file_path"])
            if candidate_id == note_id:
                existing_record = _build_note_record(f["file_path"])
                break

    if existing_record is None:
        # Non-blocking cloud fallback with short timeout — never blocks local.
        # Tombstoned remote rows are treated as not-found: resurrecting them
        # from a PUT recreated notes the user deleted on another device.
        if sync_engine.is_configured:
            try:
                existing_record = await asyncio.wait_for(
                    supabase_docs.get_note(note_id), timeout=5.0
                )
                if existing_record and existing_record.get("is_deleted"):
                    existing_record = None
            except (asyncio.TimeoutError, Exception):
                pass
        if existing_record is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")

    label = req.label if req.label is not None else existing_record.get("label", "")
    content = req.content if req.content is not None else existing_record.get("content", "")
    folder_name = req.folder_name if req.folder_name is not None else existing_record.get("folder_name", "General")
    folder_id = req.folder_id if req.folder_id is not None else existing_record.get("folder_id")
    tags = req.tags if req.tags is not None else existing_record.get("tags", [])
    metadata = req.metadata if req.metadata is not None else existing_record.get("metadata", {})

    old_file_path = existing_record.get("file_path", "")
    old_label = existing_record.get("label", "")

    # If the label or folder changed AND we have a valid file on disk, rename the
    # file atomically so the filename stays in sync with the user-facing label.
    # The note_id is unchanged — only the on-disk path moves.
    label_changed = label != old_label
    folder_changed = (
        req.folder_name is not None
        and req.folder_name != existing_record.get("folder_name", "General")
    )

    if old_file_path and (label_changed or folder_changed):
        # rename_note does os.rename (atomic on same filesystem).
        try:
            file_path = file_manager.rename_note(old_file_path, folder_name, label)
        except Exception:
            # If rename fails (e.g. target already exists), fall back to writing
            # to the old path so content is never lost.
            logger.warning(
                "[update_note] rename_note failed for %s → %s/%s; writing in place",
                old_file_path, folder_name, label,
            )
            file_path = file_manager.write_note(folder_name, label, content, old_file_path)
        else:
            # Rename moved the file; now write the (possibly updated) content.
            file_manager.write_note(folder_name, label, content, file_path)
    elif old_file_path:
        # Content-only save — preserve the existing file path.
        file_path = file_manager.write_note(folder_name, label, content, old_file_path)
    else:
        # No existing file path — shouldn't happen for a PUT, but safe fallback.
        file_path = file_manager.write_note(folder_name, label, content)

    c_hash = content_hash(content)
    now = _now()

    await _sync_note_to_sqlite(
        note_id=note_id,
        label=label,
        content=content,
        folder_name=folder_name,
        folder_id=folder_id,
        file_path=file_path,
        tags=tags,
        metadata=metadata,
    )

    result: dict[str, Any] = {
        "id": note_id,
        "label": label,
        "content": content,
        "folder_name": folder_name,
        "folder_id": folder_id,
        "file_path": file_path,
        "tags": tags,
        "metadata": metadata,
        "content_hash": c_hash,
        "created_at": existing_record.get("created_at", now),
        "updated_at": now,
        "_synced_to_cloud": False,
        "_source": "local",
    }

    if sync_engine.is_configured:
        _fire_and_forget(sync_engine.push_note(
            note_id=note_id,
            label=label,
            content=content,
            folder_name=folder_name,
            folder_id=folder_id,
            tags=tags,
            metadata=metadata,
            file_path=file_path,
        ))

    return result


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str, request: Request) -> dict[str, str]:
    _configure_sync(request)
    repo = _get_notes_repo()

    # Sync lock: file removal + tombstone must not interleave with an
    # in-flight full_sync/pull holding a pre-delete snapshot.
    async with sync_engine.sync_lock:
        # Primary: SQLite knows the exact file_path — no scan needed.
        sqlite_note = await repo.get(note_id)
        if sqlite_note and sqlite_note.get("file_path"):
            file_manager.delete_note(sqlite_note["file_path"])
        else:
            # Legacy fallback: scan for a UUID5-matched file.
            for f in file_manager.scan_all():
                if _note_id_for_path(f["file_path"]) == note_id:
                    file_manager.delete_note(f["file_path"])
                    break

        await repo.soft_delete(note_id)

    if sync_engine.is_configured:
        _fire_and_forget(
            supabase_docs.soft_delete_note(note_id, sync_engine.device_id)
        )

    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Note sync metadata endpoints
# ---------------------------------------------------------------------------

@router.post("/notes/{note_id}/exclude")
async def set_note_excluded(
    note_id: str, req: NoteExcludeRequest, request: Request
) -> dict[str, Any]:
    """Mark a note as excluded from sync (or re-include it)."""
    repo = _get_notes_repo()
    await repo.set_excluded(note_id, req.excluded)
    return {"id": note_id, "sync_status": "excluded" if req.excluded else "never_synced"}


@router.get("/notes/{note_id}/sync-status")
async def get_note_sync_status(note_id: str, request: Request) -> dict[str, Any]:
    repo = _get_notes_repo()
    note = await repo.get(note_id)
    if not note:
        return {
            "id": note_id,
            "sync_status": "never_synced",
            "sync_enabled": True,
            "last_synced_at": None,
        }
    return {
        "id": note_id,
        "sync_status": note.get("sync_status", "never_synced"),
        "sync_enabled": note.get("sync_enabled", True),
        "last_synced_at": note.get("last_synced_at"),
        "remote_content_hash": note.get("remote_content_hash"),
    }


# ---------------------------------------------------------------------------
# Version endpoints — local first, cloud as optional enrichment
# ---------------------------------------------------------------------------

@router.get("/notes/{note_id}/versions")
async def list_versions(note_id: str, request: Request) -> list[dict[str, Any]]:
    """List version history for a note — local SQLite snapshots.

    Cloud `note_versions` was graveyarded (2026-07); cloud-side history is now
    captured by the `platform._version_capture` trigger into
    `history.row_versions` and is not surfaced here.
    """
    versions_repo = _get_versions_repo()
    return await versions_repo.list_for_note(note_id)


@router.post("/notes/{note_id}/revert")
async def revert_note(note_id: str, req: RevertRequest, request: Request) -> dict[str, Any]:
    """Revert a note to a previous version. Works locally; no cloud required."""
    _configure_sync(request)

    versions_repo = _get_versions_repo()
    version = await versions_repo.get_version(note_id, req.version_number)

    if not version:
        raise HTTPException(status_code=404, detail=f"Version {req.version_number} not found")

    # Get current note metadata from SQLite
    repo = _get_notes_repo()
    existing = await repo.get(note_id)
    folder_name = (existing.get("folder_name") or "General") if existing else "General"
    folder_id = existing.get("folder_id") if existing else None
    label = version.get("label") or (existing.get("label", "Untitled") if existing else "Untitled")

    # Write the reverted content locally — to the note's REAL stored path.
    # Deriving from folder+label forked collision-suffixed notes into a new
    # file and orphaned the original (which then reappeared as a duplicate
    # legacy note in list_notes).
    file_path = file_manager.write_note(
        folder_name, label, version["content"],
        existing.get("file_path") if existing else None,
    )

    # Update SQLite (this also creates a version snapshot of the current content)
    await _sync_note_to_sqlite(
        note_id=note_id,
        label=label,
        content=version["content"],
        folder_name=folder_name,
        folder_id=folder_id,
        file_path=file_path,
    )

    result: dict[str, Any] = {
        "id": note_id,
        "label": label,
        "content": version["content"],
        "folder_name": folder_name,
        "folder_id": folder_id,
        "file_path": file_path,
        "_source": "local",
    }

    # Fire-and-forget push to cloud if configured
    if sync_engine.is_configured:
        _fire_and_forget(sync_engine.push_note(
            note_id=note_id,
            label=label,
            content=version["content"],
            folder_name=folder_name,
            folder_id=folder_id,
            file_path=file_path,
        ))

    return result


# ---------------------------------------------------------------------------
# Sync endpoints — explicit user-triggered sync operations
# ---------------------------------------------------------------------------

@router.get("/sync/status")
async def sync_status(request: Request) -> dict[str, Any]:
    _configure_sync(request)
    base = sync_engine.get_status()
    repo = _get_notes_repo()
    pending = await repo.list_pending_push()
    excluded = await repo.list_excluded()
    base["pending_push_count"] = len(pending)
    base["excluded_count"] = len(excluded)
    return base


@router.post("/sync/trigger")
async def trigger_sync(req: SyncTriggerRequest, request: Request) -> dict[str, Any]:
    """Manual sync — user-triggered only. Supports push, pull, and bidirectional modes."""
    _configure_sync(request)
    if not sync_engine.is_configured:
        raise HTTPException(status_code=400, detail="Sync not configured — sign in to enable cloud sync")

    mode = req.mode
    if mode == "push":
        return await sync_engine.push_all()
    elif mode == "pull":
        return await sync_engine.pull_all()
    elif mode == "bidirectional":
        return await sync_engine.full_sync()
    else:
        raise HTTPException(status_code=400, detail=f"Invalid sync mode: {mode}. Use push, pull, or bidirectional.")


@router.post("/sync/pull")
async def pull_changes(request: Request) -> dict[str, Any]:
    _configure_sync(request)
    if not sync_engine.is_configured:
        return {"pulled": 0, "conflicts": 0, "reason": "not_configured"}
    return await sync_engine.pull_changes()


@router.post("/sync/pull-note")
async def pull_single_note(req: PullRemoteChangeRequest, request: Request) -> dict[str, Any]:
    _configure_sync(request)
    if not sync_engine.is_configured:
        raise HTTPException(status_code=400, detail="Sync not configured")
    result = await sync_engine.pull_note(req.note_id)
    if not result:
        raise HTTPException(status_code=404, detail="Note not found in Supabase")
    return result


@router.post("/sync/register-device")
async def register_device(request: Request) -> dict[str, Any]:
    _configure_sync(request)
    if not sync_engine.is_configured:
        return {"status": "not_configured"}
    return await sync_engine.register_device()


@router.post("/sync/start-watcher")
async def start_watcher(request: Request) -> dict[str, str]:
    _configure_sync(request)
    await sync_engine.start_watcher()
    return {"status": "watcher started"}


@router.post("/sync/stop-watcher")
async def stop_watcher(request: Request) -> dict[str, str]:
    await sync_engine.stop_watcher()
    return {"status": "watcher stopped"}


# ---------------------------------------------------------------------------
# Conflict endpoints
# ---------------------------------------------------------------------------

@router.get("/conflicts")
async def list_conflicts(request: Request) -> dict[str, Any]:
    # Drop residue conflicts whose two sides are byte-identical (lossless by
    # definition) before presenting anything to the user.
    sync_engine.prune_stale_conflicts()
    conflict_ids = file_manager.list_conflicts()
    details: list[dict[str, Any]] = []
    for note_id in conflict_ids:
        conflict_dir = file_manager.base_dir / ".sync" / "conflicts" / note_id
        local_file = conflict_dir / "local.md"
        remote_file = conflict_dir / "remote.md"
        entry: dict[str, Any] = {"note_id": note_id}
        if local_file.exists():
            entry["local_content"] = local_file.read_text(encoding="utf-8")
        if remote_file.exists():
            entry["remote_content"] = remote_file.read_text(encoding="utf-8")
        repo = _get_notes_repo()
        sqlite_note = await repo.get(note_id)
        if sqlite_note:
            entry["label"] = sqlite_note.get("label", sqlite_note.get("title", ""))
            entry["folder_name"] = sqlite_note.get("folder_name", "General")
        details.append(entry)
    return {"conflicts": details, "count": len(details)}


@router.post("/conflicts/{note_id}/resolve")
async def resolve_conflict(
    note_id: str, req: ConflictResolveRequest, request: Request
) -> dict[str, Any]:
    _configure_sync(request)
    result = await sync_engine.resolve_conflict(
        note_id, req.resolution, merged_content=req.merged_content
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Conflict not found")
    return {"status": "resolved", "resolution": req.resolution}


# ---------------------------------------------------------------------------
# Share endpoints — RETIRED (2026-07)
#
# The cloud `note_shares` table was graveyarded in the cloud DB reorganization;
# the platform now models sharing through iam grants (see the RLS policies on
# workbench.notes: iam.has_access('note', id, ...)). These endpoints returned
# silent 404-backed failures for weeks. They now fail loudly until a proper
# iam-backed sharing flow is built (tracked in .matrx/ARMAN_TASKS.md).
# ---------------------------------------------------------------------------

_SHARES_RETIRED_DETAIL = (
    "Note sharing is temporarily unavailable: the legacy note_shares table was "
    "retired. Sharing will return via the platform iam permission system."
)


@router.get("/shares")
async def list_shares(request: Request) -> list[dict[str, Any]]:
    # Listing returns empty (nothing is shared) rather than erroring so the
    # Documents page load path stays clean.
    return []


@router.post("/shares")
async def create_share(req: ShareRequest, request: Request) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=_SHARES_RETIRED_DETAIL)


@router.put("/shares/{share_id}")
async def update_share(share_id: str, req: UpdateShareRequest, request: Request) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=_SHARES_RETIRED_DETAIL)


@router.delete("/shares/{share_id}")
async def delete_share(share_id: str, request: Request) -> dict[str, str]:
    raise HTTPException(status_code=501, detail=_SHARES_RETIRED_DETAIL)


# ---------------------------------------------------------------------------
# Directory mapping endpoints
# ---------------------------------------------------------------------------

# Directory mappings are LOCAL-ONLY: they map cloud folder ids to extra local
# directories on THIS machine, so a cloud copy never made sense — and the
# `note_directory_mappings` table it used to mirror into was graveyarded.

@router.get("/mappings")
async def list_mappings(request: Request) -> dict[str, Any]:
    return {
        "local_mappings": file_manager.load_local_mappings(),
        "device_id": sync_engine.device_id,
    }


@router.post("/mappings")
async def create_mapping(req: MappingRequest, request: Request) -> dict[str, Any]:
    local_mappings = file_manager.load_local_mappings()
    if req.folder_id not in local_mappings:
        local_mappings[req.folder_id] = []
    if req.local_path not in local_mappings[req.folder_id]:
        local_mappings[req.folder_id].append(req.local_path)
    file_manager.save_local_mappings(local_mappings)
    return {"folder_id": req.folder_id, "local_path": req.local_path}


@router.delete("/mappings/{mapping_id}")
async def delete_mapping(
    mapping_id: str,
    request: Request,
    folder_id: str | None = None,
    local_path: str | None = None,
) -> dict[str, str]:
    if folder_id and local_path:
        local_mappings = file_manager.load_local_mappings()
        if folder_id in local_mappings:
            local_mappings[folder_id] = [p for p in local_mappings[folder_id] if p != local_path]
            if not local_mappings[folder_id]:
                del local_mappings[folder_id]
            file_manager.save_local_mappings(local_mappings)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Local file operations (for tools / AI agent integration)
# ---------------------------------------------------------------------------

@router.get("/local/folders")
async def list_local_folders() -> list[str]:
    return file_manager.list_folders()


@router.get("/local/files")
async def scan_local_files() -> list[dict[str, str]]:
    return file_manager.scan_all()


@router.get("/local/files/{file_path:path}")
async def read_local_file(file_path: str) -> dict[str, Any]:
    content = file_manager.read_note(file_path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    return {
        "file_path": file_path,
        "content": content,
        "content_hash": file_manager.note_hash(file_path),
    }
