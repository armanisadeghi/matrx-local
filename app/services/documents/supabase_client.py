"""Supabase PostgREST client for notes/documents CRUD.

All operations use the user's JWT (forwarded from the frontend) so that
Row Level Security policies are enforced server-side.

Schema note: the remote `notes` and `note_folders` tables live in the
`workbench` Postgres schema (NOT `public`). PostgREST targets a non-public
schema via the `Accept-Profile` (reads) / `Content-Profile` (writes) headers —
that is exactly what supabase-js's `.schema("workbench")` sends under the hood.
The notes/folders methods pass `schema=_WORKBENCH`; everything else
(`note_versions`/`note_shares` in `public`, the sync-log tables) keeps the
default `public` profile.

Column note (canonical base-entity shape on the remote side):
    - ownership   : `created_by` (uuid)         — replaced the old `user_id`.
    - visibility  : `visibility` (enum)         — replaced the old `is_public`.
    - soft-delete : `deleted_at` (timestamptz)  — null = live; replaced the old
                                                  `is_deleted` boolean.
The LOCAL SQLite mirror keeps its own column names; only the fields sent to and
read from Supabase are mapped here. Public method signatures still take
`user_id` so the sync engine is unchanged — it is mapped to `created_by` at the
wire boundary.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.config import SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string for `deleted_at` soft-delete marks."""
    return datetime.now(timezone.utc).isoformat()

_REST_BASE = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""

# The notes/note_folders tables live in this Postgres schema, reached over
# PostgREST via the Accept-Profile / Content-Profile headers.
_WORKBENCH = "workbench"
# HTTP methods that mutate state use Content-Profile; reads use Accept-Profile.
_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def _content_hash(content: str) -> str:
    """SHA-256 hash of note content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize_note_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map canonical remote columns back to the sync engine's vocabulary.

    The remote table soft-deletes via ``deleted_at`` (timestamptz, null = live)
    while the sync engine and local SQLite speak ``is_deleted`` (bool). The
    2026-06 workbench repoint mapped the WRITE side (soft_delete_note PATCHes
    ``deleted_at``) but not the READ side, so ``note.get("is_deleted")`` in
    SyncEngine._pull_note was always falsy against real remote rows — remote
    soft-deletes stopped propagating and pulled deleted notes back to disk as
    live content (the exact resurrection bug 9ca565245 fixed). Every read path
    that returns note rows to the engine must pass through here.
    See docs/SYNC_CONTRACT.md (tombstones).
    """
    if "deleted_at" in row and "is_deleted" not in row:
        row["is_deleted"] = row.get("deleted_at") is not None
    return row


class SupabaseDocClient:
    """Thin wrapper around Supabase PostgREST for the notes/documents tables."""

    def __init__(self) -> None:
        self._jwt: str | None = None

    def set_jwt(self, token: str | None) -> None:
        self._jwt = token

    @property
    def available(self) -> bool:
        return bool(_REST_BASE and self._jwt)

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        if self._jwt:
            h["Authorization"] = f"Bearer {self._jwt}"
        return h

    async def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[dict[str, Any]] | None = None,
        extra_headers: dict[str, str] | None = None,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        if not _REST_BASE:
            raise RuntimeError("SUPABASE_URL not configured")
        if not self._jwt:
            raise RuntimeError("No JWT set — user must be authenticated")

        headers = self._headers()
        # Target a non-public schema (e.g. `workbench`) via the PostgREST profile
        # headers. Accept-Profile selects the schema to read from; Content-Profile
        # selects the schema to write to.
        if schema:
            if method.upper() in _WRITE_METHODS:
                headers["Content-Profile"] = schema
            else:
                headers["Accept-Profile"] = schema
        if extra_headers:
            headers.update(extra_headers)

        url = f"{_REST_BASE}/{table}"
        # Short timeout: cloud operations must never block the local-first UX.
        # Connect timeout 5s, read timeout 10s, total 15s.
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.request(
                method, url, params=params, json=json_body, headers=headers
            )
            # A 404 on a GET simply means no rows matched — treat as empty.
            if resp.status_code == 404 and method.upper() == "GET":
                return []
            if resp.status_code == 204:
                return []
            resp.raise_for_status()
            return resp.json() if resp.text else []

    # ── Folders ──────────────────────────────────────────────────────────────

    async def list_folders(self, user_id: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "note_folders",
            params={
                "created_by": f"eq.{user_id}",
                "deleted_at": "is.null",
                "order": "path.asc,position.asc",
            },
            schema=_WORKBENCH,
        )

    async def create_folder(
        self,
        user_id: str,
        name: str,
        parent_id: str | None = None,
        path: str = "",
    ) -> dict[str, Any]:
        body = {
            "id": str(uuid4()),
            "created_by": user_id,
            "name": name,
            "parent_id": parent_id,
            "path": path,
        }
        rows = await self._request(
            "POST", "note_folders", json_body=body, schema=_WORKBENCH
        )
        return rows[0] if rows else body

    async def update_folder(
        self, folder_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        rows = await self._request(
            "PATCH",
            "note_folders",
            params={"id": f"eq.{folder_id}"},
            json_body=updates,
            schema=_WORKBENCH,
        )
        return rows[0] if rows else {}

    async def delete_folder(self, folder_id: str) -> None:
        await self._request(
            "PATCH",
            "note_folders",
            params={"id": f"eq.{folder_id}"},
            json_body={"deleted_at": _utcnow_iso()},
            schema=_WORKBENCH,
        )

    # ── Notes ────────────────────────────────────────────────────────────────

    async def list_notes(
        self,
        user_id: str,
        folder_id: str | None = None,
        search: str | None = None,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "created_by": f"eq.{user_id}",
            "order": "updated_at.desc",
            "select": "id,label,folder_name,folder_id,tags,file_path,content_hash,"
            "sync_version,position,deleted_at,created_at,updated_at,metadata",
        }
        if not include_deleted:
            params["deleted_at"] = "is.null"
        if folder_id:
            params["folder_id"] = f"eq.{folder_id}"
        if search:
            params["or"] = f"(label.ilike.%{search}%,content.ilike.%{search}%)"
        rows = await self._request("GET", "notes", params=params, schema=_WORKBENCH)
        return [_normalize_note_row(r) for r in rows]

    async def get_note(self, note_id: str) -> dict[str, Any] | None:
        try:
            rows = await self._request(
                "GET", "notes", params={"id": f"eq.{note_id}"}, schema=_WORKBENCH
            )
            return _normalize_note_row(rows[0]) if rows else None
        except Exception:
            logger.debug("get_note(%s) returned no result", note_id, exc_info=True)
            return None

    async def create_note(
        self,
        user_id: str,
        label: str,
        content: str = "",
        folder_name: str = "General",
        folder_id: str | None = None,
        file_path: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        note_id = str(uuid4())
        body: dict[str, Any] = {
            "id": note_id,
            "created_by": user_id,
            "label": label,
            "content": content,
            "folder_name": folder_name,
            "folder_id": folder_id,
            "file_path": file_path,
            "tags": tags or [],
            "metadata": metadata or {},
            "content_hash": _content_hash(content),
            "sync_version": 1,
            "last_device_id": device_id,
        }
        rows = await self._request("POST", "notes", json_body=body, schema=_WORKBENCH)
        return rows[0] if rows else body

    async def upsert_note(
        self,
        note_id: str,
        user_id: str,
        label: str,
        content: str = "",
        folder_name: str = "General",
        folder_id: str | None = None,
        file_path: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomic insert-or-update (upsert ON CONFLICT DO UPDATE on primary key).

        Use this instead of get_note → create_note/update_note when you know
        the full desired state and do NOT need the old content for versioning.
        """
        body: dict[str, Any] = {
            "id": note_id,
            "created_by": user_id,
            "label": label,
            "content": content,
            "folder_name": folder_name,
            "folder_id": folder_id,
            "file_path": file_path,
            "tags": tags or [],
            "metadata": metadata or {},
            "content_hash": _content_hash(content),
            "last_device_id": device_id,
            # Explicit resurrect-on-push: if the row was soft-deleted remotely
            # while this device kept editing the local file, pushing the edit
            # revives the note (edit-vs-delete resolves to keep-the-edit —
            # never destructive).
            "deleted_at": None,
        }
        rows = await self._request(
            "POST",
            "notes",
            json_body=body,
            extra_headers={
                "Prefer": "return=representation,resolution=merge-duplicates"
            },
            schema=_WORKBENCH,
        )
        return rows[0] if rows else body

    async def update_note(
        self,
        note_id: str,
        updates: dict[str, Any],
        device_id: str | None = None,
    ) -> dict[str, Any]:
        if "content" in updates:
            updates["content_hash"] = _content_hash(updates["content"])
        if device_id:
            updates["last_device_id"] = device_id
        rows = await self._request(
            "PATCH",
            "notes",
            params={"id": f"eq.{note_id}"},
            json_body=updates,
            schema=_WORKBENCH,
        )
        return rows[0] if rows else {}

    async def update_note_if_unchanged(
        self,
        note_id: str,
        updates: dict[str, Any],
        expected_content_hash: str,
        device_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Optimistic-concurrency update: PATCH only if the remote row still
        carries ``expected_content_hash`` and is live.

        Returns the updated row, or ``None`` when the precondition failed
        (remote content moved since our last sync, or the row was
        soft-deleted) — the caller must then run the pull/conflict path
        instead of overwriting. This is what prevents silent cross-device
        last-writer-wins on user content (SYNC_CONTRACT).
        """
        if "content" in updates:
            updates["content_hash"] = _content_hash(updates["content"])
        if device_id:
            updates["last_device_id"] = device_id
        rows = await self._request(
            "PATCH",
            "notes",
            params={
                "id": f"eq.{note_id}",
                "content_hash": f"eq.{expected_content_hash}",
                "deleted_at": "is.null",
            },
            json_body=updates,
            schema=_WORKBENCH,
        )
        return rows[0] if rows else None

    async def soft_delete_note(self, note_id: str, device_id: str | None = None) -> None:
        body: dict[str, Any] = {"deleted_at": _utcnow_iso()}
        # Stamp the deleting device so realtime consumers can classify the
        # event; without this a delete inherits the LAST CONTENT WRITER's id
        # and gets misread as that device's own echo.
        if device_id:
            body["last_device_id"] = device_id
        await self._request(
            "PATCH",
            "notes",
            params={"id": f"eq.{note_id}"},
            json_body=body,
            schema=_WORKBENCH,
        )

    async def hard_delete_note(self, note_id: str) -> None:
        await self._request(
            "DELETE", "notes", params={"id": f"eq.{note_id}"}, schema=_WORKBENCH
        )

    # NOTE (2026-07): the cloud aux tables this client used to talk to —
    # note_versions, note_shares, note_devices, note_directory_mappings and
    # note_sync_log — were moved to the `graveyard` schema during the cloud DB
    # reorganization. Every request against them 404'd silently on each save.
    # Their client methods are deleted, not stubbed:
    #   - version history  → local SQLite `note_versions` (offline-capable) +
    #     the cloud-side `platform._version_capture` trigger, which snapshots
    #     every workbench.notes write into `history.row_versions`.
    #   - devices / sync log → local-only (`.sync/state.json` device_id).
    #   - directory mappings → local-only (`.sync/mappings.json`).
    #   - shares → retired; the platform now models sharing via iam grants
    #     (see the RLS policies on workbench.notes). Routes return 501.

    # ── Bulk fetch for sync ──────────────────────────────────────────────────

    async def get_notes_since(
        self, user_id: str, since_iso: str | None
    ) -> list[dict[str, Any]]:
        """Get all notes touched since a given ``updated_at`` timestamp.

        The incremental cursor is ``updated_at`` (stamped by the cloud-side
        ``platform._touch_row`` BEFORE UPDATE trigger on EVERY write,
        including the ``deleted_at`` soft-delete PATCH). It must NOT be
        ``sync_version``: that column is a PER-ROW edit counter
        (``increment_sync_version`` bumps only on content/label change), so a
        global max-of-sync_version watermark goes blind to any note edited
        fewer times than the busiest note, and to ALL soft-deletes — the
        exact bug that made remote edits and deletions never reach this
        engine incrementally.

        Includes soft-deleted rows on purpose so deletions propagate via
        pull_changes → _pull_note's tombstone branch. ``since_iso`` None
        means "everything".
        """
        params: dict[str, str] = {
            "created_by": f"eq.{user_id}",
            "order": "updated_at.asc",
        }
        if since_iso:
            params["updated_at"] = f"gt.{since_iso}"
        rows = await self._request("GET", "notes", params=params, schema=_WORKBENCH)
        return [_normalize_note_row(r) for r in rows]

    async def get_all_notes_with_hashes(self, user_id: str) -> list[dict[str, Any]]:
        """Get id, file_path, content_hash, sync_version for all LIVE notes.

        Deliberately excludes soft-deleted rows (``deleted_at`` non-null), so
        full_sync's remote set never resurrects deleted notes. Remote deletions
        propagate through the incremental path (get_notes_since → _pull_note),
        not through this snapshot. See docs/SYNC_CONTRACT.md.
        """
        return await self._request(
            "GET",
            "notes",
            params={
                "created_by": f"eq.{user_id}",
                "deleted_at": "is.null",
                "select": "id,file_path,content_hash,sync_version,label,"
                "folder_name,folder_id,updated_at,last_device_id",
            },
            schema=_WORKBENCH,
        )


# Module-level singleton
supabase_docs = SupabaseDocClient()
