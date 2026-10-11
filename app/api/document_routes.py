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

from app.services.access_health import get_access_health
from app.services.documents.access_resources import (
    register_mapping,
    unregister_mapping,
)
from app.services.documents.file_manager import (
    content_hash,
    file_manager,
)
from app.services.documents.async_io import offload_read_only
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

class CreateNoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    label: str = Field("New Note", validation_alias=AliasChoices("label", "title"))
    content: str = ""
    # No home on create: a note carries the active organization and the
    # database places it in that organization's General.
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateNoteRequest(BaseModel):
    label: str | None = None
    content: str | None = None
    # Moving a note = choosing another home (a scope id the person can see).
    home_scope_id: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    position: int | None = None


class RevertRequest(BaseModel):
    version_number: int


class ShareRequest(BaseModel):
    note_id: str | None = None
    shared_with_id: str | None = None
    permission: str = "read"
    is_public: bool = False


class UpdateShareRequest(BaseModel):
    permission: str | None = None
    is_public: bool | None = None


class MoveHomeRequest(BaseModel):
    home_scope_id: str


class MappingRequest(BaseModel):
    home_scope_id: str
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

def _note_id_for_path(file_path: str) -> str:
    """Legacy: derive a deterministic UUID5 from a file path.

    Only used as a fallback to look up pre-existing notes that were created
    before the UUID4 migration. New notes always get a random UUID4 stored in
    SQLite, so their ID never changes when the file is renamed.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"matrx-note:{file_path}"))


def _unique_file_path(home_dir: str, label: str) -> str:
    """Collision-free relative path — canonical impl lives in file_manager."""
    return file_manager.unique_file_path(home_dir, label)


def _home_scope_for(sqlite_note: dict[str, Any] | None, home_dir: str) -> str | None:
    """A note's home scope id: the one SQLite holds, else the visible home
    whose directory this is (None while homes are unknown)."""
    if sqlite_note and sqlite_note.get("home_scope_id"):
        return sqlite_note["home_scope_id"]
    home = sync_engine.homes_cached.home_for_dir(home_dir)
    return home.id if home else None


def _local_homes_tree() -> dict[str, Any]:
    """The homes (one directory each) with how many local notes each holds.

    Directories that match no home are listed too, flagged ``unmatched``:
    they are plain local folders, never homes.
    """
    note_paths = file_manager.list_note_paths()
    counts: dict[str, int] = {}
    for file_path in note_paths:
        parts = Path(file_path).parts
        key = parts[0] if len(parts) > 1 else "__root__"
        counts[key] = counts.get(key, 0) + 1

    directory = sync_engine.homes_cached
    homes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in directory.as_list():
        seen.add(h["dir"].casefold())
        homes.append({
            "id": h["id"],
            "name": h["dir"],
            "home_name": h["name"],
            "organization_id": h["organization_id"],
            "organization_name": h["organization_name"],
            "note_count": counts.get(h["dir"], 0),
            "unmatched": False,
            "_source": "local",
        })
    for name in file_manager.list_folders():
        if name.casefold() in seen:
            continue
        homes.append({
            "id": None,
            "name": name,
            "home_name": name,
            "organization_id": None,
            "organization_name": "",
            "note_count": counts.get(name, 0),
            "unmatched": True,
            "_source": "local",
        })
    return {
        "homes": homes,
        "total_notes": len(note_paths),
        "unfiled_notes": counts.get("__root__", 0),
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


def _build_note_record(
    file_path: str,
    home_dir: str | None = None,
    sqlite_note: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    content = file_manager.read_note(file_path)
    if content is None:
        return None
    p = Path(file_path)
    folder = home_dir or (p.parts[0] if len(p.parts) > 1 else "General")
    home_scope = _home_scope_for(sqlite_note, folder)
    created_at, updated_at = _file_timestamps(file_path)
    return {
        "id": _note_id_for_path(file_path),
        "label": p.stem,
        "content": content,
        "home_dir": folder,
        "home_scope_id": home_scope,
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
    home_dir: str,
    home_scope_id: str | None,
    file_path: str,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    is_new: bool = False,
    user_id: str | None = None,
    organization_id: str | None = None,
) -> None:
    """Persist note metadata to SQLite for sync tracking.

    Also creates a local version snapshot when content changes, so version
    history works fully offline without Supabase.
    """
    repo = _get_notes_repo()
    c_hash = content_hash(content)

    existing = await repo.get(note_id)
    existing_owner = str((existing or {}).get("user_id") or "")
    requested_owner = str(user_id or "")
    if existing_owner and existing_owner != requested_owner:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
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
        # New authenticated notes retain their owner.  An older local-only
        # row with no owner stays unowned: assigning it to whoever is signed
        # in now would silently adopt data from a prior account.
        "user_id": existing_owner if existing is not None else requested_owner,
        "home_scope_id": home_scope_id,
        "title": label,
        "label": label,
        "content": content,
        "content_hash": c_hash,
        "file_path": file_path,
        "is_deleted": False,
        "is_pinned": existing.get("is_pinned", False) if existing else False,
        "tags": tags or (existing.get("tags", []) if existing else []),
        "sync_version": existing.get("sync_version", 0) if existing else 0,
        "home_dir": home_dir,
        "organization_id": organization_id or (existing or {}).get("organization_id"),
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
# Home endpoints — a note's home is a scope; its directory is the home's name
# ---------------------------------------------------------------------------

@router.get("/tree")
async def get_homes_tree(request: Request) -> dict[str, Any]:
    _configure_sync(request)
    if sync_engine.is_configured:
        try:
            await asyncio.wait_for(sync_engine.get_homes(), timeout=5.0)
        except Exception:
            pass
    return await offload_read_only(_local_homes_tree)


@router.get("/homes")
async def list_homes(request: Request) -> dict[str, Any]:
    """Every home the person can see (each organization's General + scopes),
    with the local directory each one maps to."""
    _configure_sync(request)
    directory = await sync_engine.get_homes()
    return {"homes": directory.as_list()}


@router.get("/home-notices")
async def home_notices(request: Request) -> dict[str, Any]:
    """Moves the app could not honour (e.g. a file dragged into a directory
    that matches no home) — the note stayed in its current home."""
    return {"notices": list(sync_engine.home_notices)}


@router.post("/notes/{note_id}/home")
async def move_note_home(
    note_id: str, req: MoveHomeRequest, request: Request
) -> dict[str, Any]:
    _configure_sync(request)
    try:
        return await sync_engine.move_note_home(note_id, req.home_scope_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not move the note ({type(exc).__name__})."
        )


# ---------------------------------------------------------------------------
# Note endpoints
# ---------------------------------------------------------------------------

@router.get("/notes")
async def list_notes(
    request: Request,
    home_scope_id: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    _configure_sync(request)

    all_files = await offload_read_only(file_manager.scan_all)
    results: list[dict[str, Any]] = []
    repo = _get_notes_repo()

    for f in all_files:
        if search:
            file_content = await offload_read_only(
                lambda: file_manager.read_note(f["file_path"]) or ""
            )
            label = f.get("label", "")
            if search.lower() not in file_content.lower() and search.lower() not in label.lower():
                continue

        p = Path(f["file_path"])
        home_dir = p.parts[0] if len(p.parts) > 1 else "General"

        # SQLite is the canonical ID store for new notes (UUID4).  For legacy
        # notes (created before the UUID4 migration) the SQLite row may not exist
        # yet — fall back to the deterministic UUID5 so old IDs stay stable.
        sqlite_note = await repo.get_by_file_path(f["file_path"])
        if sqlite_note is None:
            legacy_id = _note_id_for_path(f["file_path"])
            sqlite_note = await repo.get(legacy_id)
        note_home_scope = _home_scope_for(sqlite_note, home_dir)
        if home_scope_id and note_home_scope != home_scope_id:
            continue
        note_id = sqlite_note["id"] if sqlite_note else _note_id_for_path(f["file_path"])

        # Use the label stored in SQLite (reflects renames) when available;
        # otherwise fall back to the filename stem.
        label = (sqlite_note.get("label") or sqlite_note.get("title") or f["label"]) if sqlite_note else f["label"]

        created_at, updated_at = await offload_read_only(
            lambda: _file_timestamps(f["file_path"])
        )

        results.append({
            "id": note_id,
            "label": label,
            "home_dir": home_dir,
            "home_scope_id": note_home_scope,
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
        record = await offload_read_only(
            lambda: _build_note_record(
                sqlite_note["file_path"], sqlite_note=sqlite_note
            )
        )
        if record:
            record["id"] = note_id  # preserve the canonical ID, not the UUID5 from the file path
            return _enrich_record_from_sqlite(record, sqlite_note)

    # Secondary: filesystem scan — catches legacy notes (UUID5 IDs) not yet in SQLite.
    for file_path in await offload_read_only(file_manager.list_note_paths):
        if _note_id_for_path(file_path) == note_id:
            record = await offload_read_only(
                lambda: _build_note_record(file_path)
            )
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
    # No home on create: the note carries the active organization and the
    # database places it in that organization's General. Until then it lives
    # in the active organization's General directory.
    home_dir = await sync_engine.default_home_dir()
    organization_id = await sync_engine.active_organization_id()
    # Resolve a collision-free file path before writing.
    file_path = _unique_file_path(home_dir, req.label)
    file_manager.write_note(home_dir, req.label, req.content, file_path)
    c_hash = content_hash(req.content)
    now = _now()

    result: dict[str, Any] = {
        "id": note_id,
        "label": req.label,
        "content": req.content,
        "home_dir": home_dir,
        "home_scope_id": None,
        "organization_id": organization_id,
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
        home_dir=home_dir,
        home_scope_id=None,
        file_path=file_path,
        tags=req.tags,
        metadata=req.metadata,
        is_new=True,
        user_id=user_id,
        organization_id=organization_id,
    )

    logger.info(
        "[create_note] Created note id=%s file_path=%s home=%s label=%r",
        note_id, file_path, home_dir, req.label,
    )

    if sync_engine.is_configured and user_id:
        _fire_and_forget(sync_engine.push_note(
            note_id=note_id,
            label=req.label,
            content=req.content,
            home_dir=home_dir,
            home_scope_id=None,
            tags=req.tags,
            metadata=req.metadata,
            file_path=file_path,
            owner_user_id=user_id,
        ))

    return result


@router.put("/notes/{note_id}")
async def update_note(
    note_id: str, req: UpdateNoteRequest, request: Request
) -> dict[str, Any]:
    _configure_sync(request)
    user_id = _get_user_id_optional(request)

    existing_record: dict[str, Any] | None = None

    # Fast path: SQLite is the cheap O(1) lookup — try it first.
    repo = _get_notes_repo()
    sqlite_note = await repo.get(note_id)
    sqlite_owner = str((sqlite_note or {}).get("user_id") or "")
    if sqlite_owner and sqlite_owner != str(user_id or ""):
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    if sqlite_note and sqlite_note.get("file_path"):
        fp = sqlite_note["file_path"]
        if file_manager.note_path_from_file_path(fp).is_file():
            existing_record = await offload_read_only(lambda: _build_note_record(fp))

    # Slow path: walk the filesystem — only needed when SQLite doesn't have a
    # record yet (e.g. first save after engine restart or db corruption).
    if existing_record is None:
        for file_path in await offload_read_only(file_manager.list_note_paths):
            candidate_id = _note_id_for_path(file_path)
            if candidate_id == note_id:
                existing_record = await offload_read_only(
                    lambda: _build_note_record(file_path)
                )
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
    # A save never changes the home: moving is the explicit set_home step
    # below (req.home_scope_id), done after the content is safe on disk.
    home_dir = existing_record.get("home_dir", "General")
    home_scope_id = existing_record.get("home_scope_id")
    tags = req.tags if req.tags is not None else existing_record.get("tags", [])
    metadata = req.metadata if req.metadata is not None else existing_record.get("metadata", {})

    old_file_path = existing_record.get("file_path", "")
    old_label = existing_record.get("label", "")

    # If the label changed AND we have a valid file on disk, rename the
    # file atomically so the filename stays in sync with the user-facing label.
    # The note_id is unchanged — only the on-disk path moves.
    label_changed = label != old_label

    if old_file_path and label_changed:
        # rename_note does os.rename (atomic on same filesystem).
        try:
            file_path = file_manager.rename_note(old_file_path, home_dir, label)
        except Exception:
            # If rename fails (e.g. target already exists), fall back to writing
            # to the old path so content is never lost.
            logger.warning(
                "[update_note] rename_note failed for %s → %s/%s; writing in place",
                old_file_path, home_dir, label,
            )
            file_path = file_manager.write_note(home_dir, label, content, old_file_path)
        else:
            # Rename moved the file; now write the (possibly updated) content.
            file_manager.write_note(home_dir, label, content, file_path)
    elif old_file_path:
        # Content-only save — preserve the existing file path.
        file_path = file_manager.write_note(home_dir, label, content, old_file_path)
    else:
        # No existing file path — shouldn't happen for a PUT, but safe fallback.
        file_path = file_manager.write_note(home_dir, label, content)

    c_hash = content_hash(content)
    now = _now()

    await _sync_note_to_sqlite(
        note_id=note_id,
        label=label,
        content=content,
        home_dir=home_dir,
        home_scope_id=home_scope_id,
        file_path=file_path,
        tags=tags,
        metadata=metadata,
        user_id=user_id,
    )

    if req.home_scope_id and req.home_scope_id != home_scope_id:
        # Move = set_home as the signed-in person. The cloud must hold the
        # note first, so push the saved content, then move.
        if not (sync_engine.is_configured and user_id):
            raise HTTPException(
                status_code=409,
                detail="Moving a note to another home needs you to be signed in and online.",
            )
        await sync_engine.push_note(
            note_id=note_id, label=label, content=content, home_dir=home_dir,
            home_scope_id=home_scope_id, tags=tags, metadata=metadata,
            file_path=file_path, owner_user_id=user_id,
        )
        try:
            moved = await sync_engine.move_note_home(note_id, req.home_scope_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not move the note ({type(exc).__name__}).",
            )
        file_path = moved.get("file_path") or file_path
        home_dir = moved.get("home_dir") or home_dir
        home_scope_id = moved.get("home_scope_id") or home_scope_id

    result: dict[str, Any] = {
        "id": note_id,
        "label": label,
        "content": content,
        "home_dir": home_dir,
        "home_scope_id": home_scope_id,
        "file_path": file_path,
        "tags": tags,
        "metadata": metadata,
        "content_hash": c_hash,
        "created_at": existing_record.get("created_at", now),
        "updated_at": now,
        "_synced_to_cloud": False,
        "_source": "local",
    }

    if sync_engine.is_configured and user_id:
        _fire_and_forget(sync_engine.push_note(
            note_id=note_id,
            label=label,
            content=content,
            home_dir=home_dir,
            home_scope_id=home_scope_id,
            tags=tags,
            metadata=metadata,
            file_path=file_path,
            owner_user_id=user_id,
        ))

    return result


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str, request: Request) -> dict[str, str]:
    _configure_sync(request)
    user_id = _get_user_id_optional(request)
    repo = _get_notes_repo()

    # Sync lock: file removal + tombstone must not interleave with an
    # in-flight full_sync/pull holding a pre-delete snapshot.
    async with sync_engine.sync_lock:
        # Primary: SQLite knows the exact file_path — no scan needed.
        sqlite_note = await repo.get(note_id)
        owner = str((sqlite_note or {}).get("user_id") or "")
        if owner and owner != str(user_id or ""):
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
        if sqlite_note and sqlite_note.get("file_path"):
            file_manager.delete_note(sqlite_note["file_path"])
        else:
            # Legacy fallback: scan for a UUID5-matched file.
            for file_path in await offload_read_only(file_manager.list_note_paths):
                if _note_id_for_path(file_path) == note_id:
                    file_manager.delete_note(file_path)
                    break

        if sqlite_note and owner:
            await repo.soft_delete(note_id, user_id=owner)

    # Breaker-gated: single deletes are normal, but a caller looping this
    # endpoint is indistinguishable from a mass wipe — every cloud delete
    # spends the same budget. A blocked delete keeps its SQLite tombstone
    # and propagates after explicit confirmation.
    if owner and owner == str(user_id or "") and sync_engine.is_configured and sync_engine.allow_cloud_delete(note_id):
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
    user_id = _get_user_id_optional(request)
    note = await repo.get(note_id)
    owner = str((note or {}).get("user_id") or "")
    if owner and owner != str(user_id or ""):
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    if not owner:
        return {"id": note_id, "sync_status": note.get("sync_status", "never_synced") if note else "never_synced", "deferred_account": True}
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

    Cloud `note_versions` was deprecated (2026-07); cloud-side history is now
    captured by the `platform._version_capture` trigger into
    `history.row_versions` and is not surfaced here.
    """
    versions_repo = _get_versions_repo()
    return await versions_repo.list_for_note(note_id)


@router.post("/notes/{note_id}/revert")
async def revert_note(note_id: str, req: RevertRequest, request: Request) -> dict[str, Any]:
    """Revert a note to a previous version. Works locally; no cloud required."""
    _configure_sync(request)
    user_id = _get_user_id_optional(request)

    versions_repo = _get_versions_repo()
    version = await versions_repo.get_version(note_id, req.version_number)

    if not version:
        raise HTTPException(status_code=404, detail=f"Version {req.version_number} not found")

    # Get current note metadata from SQLite
    repo = _get_notes_repo()
    existing = await repo.get(note_id)
    owner = str((existing or {}).get("user_id") or "")
    if owner and owner != str(user_id or ""):
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    home_dir = (existing.get("home_dir") or "General") if existing else "General"
    home_scope_id = existing.get("home_scope_id") if existing else None
    label = version.get("label") or (existing.get("label", "Untitled") if existing else "Untitled")

    # Write the reverted content locally — to the note's REAL stored path.
    # Deriving from home+label forked collision-suffixed notes into a new
    # file and orphaned the original (which then reappeared as a duplicate
    # legacy note in list_notes).
    file_path = file_manager.write_note(
        home_dir, label, version["content"],
        existing.get("file_path") if existing else None,
    )

    # Update SQLite (this also creates a version snapshot of the current content)
    await _sync_note_to_sqlite(
        note_id=note_id,
        label=label,
        content=version["content"],
        home_dir=home_dir,
        home_scope_id=home_scope_id,
        file_path=file_path,
        user_id=user_id,
    )

    result: dict[str, Any] = {
        "id": note_id,
        "label": label,
        "content": version["content"],
        "home_dir": home_dir,
        "home_scope_id": home_scope_id,
        "file_path": file_path,
        "_source": "local",
    }

    # Fire-and-forget push to cloud if configured
    if sync_engine.is_configured:
        _fire_and_forget(sync_engine.push_note(
            note_id=note_id,
            label=label,
            content=version["content"],
            home_dir=home_dir,
            home_scope_id=home_scope_id,
            file_path=file_path,
        ))

    return result


# ---------------------------------------------------------------------------
# Sync endpoints — explicit user-triggered sync operations
# ---------------------------------------------------------------------------

# Notes access health lives in the canonical access-health surface:
# GET /access/health, POST /access/recheck, POST /access/reset
# (app/api/access_routes.py). The old /notes/access* adapters were deleted
# once the frontend migrated — do not reintroduce a notes-only access view.


@router.get("/sync/status")
async def sync_status(request: Request) -> dict[str, Any]:
    _configure_sync(request)
    base = sync_engine.get_status()
    repo = _get_notes_repo()
    user_id = _get_user_id_optional(request)
    pending = await repo.list_pending_push(user_id) if user_id else []
    excluded = await repo.list_excluded(user_id) if user_id else []
    base["pending_push_count"] = len(pending)
    base["excluded_count"] = len(excluded)
    return base


@router.post("/sync/delete-breaker/reset")
async def reset_delete_breaker(request: Request) -> dict[str, Any]:
    """Explicit human confirmation of a halted mass deletion.

    The mass-delete circuit breaker trips when this device tries to
    propagate an abnormal number of cloud note deletions (see
    SyncEngine.allow_cloud_delete). While tripped, cloud deletes are
    blocked and local tombstones wait. This endpoint is the ONLY way to
    clear it — deliberately a separate, explicit call so no background
    loop or retry can un-trip the breaker.
    """
    _configure_sync(request)
    return sync_engine.reset_delete_breaker()


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
    await sync_engine.prune_stale_conflicts()
    conflict_ids = file_manager.list_conflicts()
    details: list[dict[str, Any]] = []
    for note_id in conflict_ids:
        conflict_dir = file_manager.base_dir / ".sync" / "conflicts" / note_id
        local_file = conflict_dir / "local.md"
        remote_file = conflict_dir / "remote.md"
        entry: dict[str, Any] = {"note_id": note_id}
        def read_conflict_snapshots() -> tuple[str | None, str | None]:
            local_content = (
                local_file.read_text(encoding="utf-8") if local_file.exists() else None
            )
            remote_content = (
                remote_file.read_text(encoding="utf-8") if remote_file.exists() else None
            )
            return local_content, remote_content

        local_content, remote_content = await offload_read_only(
            read_conflict_snapshots
        )
        if local_content is not None:
            entry["local_content"] = local_content
        if remote_content is not None:
            entry["remote_content"] = remote_content
        repo = _get_notes_repo()
        sqlite_note = await repo.get(note_id)
        if sqlite_note:
            entry["label"] = sqlite_note.get("label", sqlite_note.get("title", ""))
            entry["home_dir"] = sqlite_note.get("home_dir", "General")
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
    if result.get("_deferred_revision"):
        raise HTTPException(
            status_code=409,
            detail="Conflict changed while it was being resolved; refresh and try again",
        )
    if result.get("_deferred_account"):
        raise HTTPException(status_code=409, detail="Conflict ownership changed; refresh and try again")
    return {"status": "resolved", "resolution": req.resolution}


# ---------------------------------------------------------------------------
# Share endpoints — RETIRED (2026-07)
#
# The cloud `note_shares` table was deprecated in the cloud DB reorganization;
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

# Directory mappings are LOCAL-ONLY: they map a home's scope id to extra local
# directories on THIS machine, so a cloud copy never made sense — and the
# `note_directory_mappings` table it used to mirror into was deprecated.

@router.get("/mappings")
async def list_mappings(request: Request) -> dict[str, Any]:
    return {
        "local_mappings": file_manager.load_local_mappings(),
        "device_id": sync_engine.device_id,
    }


@router.post("/mappings")
async def create_mapping(req: MappingRequest, request: Request) -> dict[str, Any]:
    local_mappings = file_manager.load_local_mappings()
    if req.home_scope_id not in local_mappings:
        local_mappings[req.home_scope_id] = []
    if req.local_path not in local_mappings[req.home_scope_id]:
        local_mappings[req.home_scope_id].append(req.local_path)
    file_manager.save_local_mappings(local_mappings)
    # Each mapped dir is its own access-health resource — probe it right away
    # so a bad mapping surfaces as ITS OWN degraded entry, never as a global
    # notes/FDA claim.
    resource_id = register_mapping(req.home_scope_id, req.local_path)
    await asyncio.to_thread(get_access_health().recheck, [resource_id])
    return {"home_scope_id": req.home_scope_id, "local_path": req.local_path}


@router.delete("/mappings/{mapping_id}")
async def delete_mapping(
    mapping_id: str,
    request: Request,
    home_scope_id: str | None = None,
    local_path: str | None = None,
) -> dict[str, str]:
    if home_scope_id and local_path:
        local_mappings = file_manager.load_local_mappings()
        if home_scope_id in local_mappings:
            local_mappings[home_scope_id] = [p for p in local_mappings[home_scope_id] if p != local_path]
            if not local_mappings[home_scope_id]:
                del local_mappings[home_scope_id]
            file_manager.save_local_mappings(local_mappings)
        unregister_mapping(home_scope_id, local_path)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Local file operations (for tools / AI agent integration)
# ---------------------------------------------------------------------------

@router.get("/local/folders")
async def list_local_folders() -> list[str]:
    return await offload_read_only(file_manager.list_folders)


@router.get("/local/files")
async def scan_local_files() -> list[dict[str, str]]:
    return await offload_read_only(file_manager.scan_all)


@router.get("/local/files/{file_path:path}")
async def read_local_file(file_path: str) -> dict[str, Any]:
    def read() -> dict[str, Any] | None:
        content = file_manager.read_note(file_path)
        if content is None:
            return None
        return {
            "file_path": file_path,
            "content": content,
            "content_hash": content_hash(content),
        }

    result = await offload_read_only(read)
    if result is None:
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    return result
