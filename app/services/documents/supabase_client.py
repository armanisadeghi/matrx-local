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
        return await self._request("GET", "notes", params=params, schema=_WORKBENCH)

    async def get_note(self, note_id: str) -> dict[str, Any] | None:
        try:
            rows = await self._request(
                "GET", "notes", params={"id": f"eq.{note_id}"}, schema=_WORKBENCH
            )
            return rows[0] if rows else None
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

    async def soft_delete_note(self, note_id: str) -> None:
        await self._request(
            "PATCH",
            "notes",
            params={"id": f"eq.{note_id}"},
            json_body={"deleted_at": _utcnow_iso()},
            schema=_WORKBENCH,
        )

    async def hard_delete_note(self, note_id: str) -> None:
        await self._request(
            "DELETE", "notes", params={"id": f"eq.{note_id}"}, schema=_WORKBENCH
        )

    # ── Versions ─────────────────────────────────────────────────────────────

    async def list_versions(self, note_id: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "note_versions",
            params={
                "note_id": f"eq.{note_id}",
                "order": "version_number.desc",
            },
        )

    async def create_version(
        self,
        note_id: str,
        user_id: str,
        content: str,
        label: str,
        version_number: int,
        change_source: str = "desktop",
        change_type: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "id": str(uuid4()),
            "note_id": note_id,
            "user_id": user_id,
            "content": content,
            "label": label,
            "version_number": version_number,
            "change_source": change_source,
            "change_type": change_type,
        }
        rows = await self._request("POST", "note_versions", json_body=body)
        return rows[0] if rows else body

    async def get_version(
        self, note_id: str, version_number: int
    ) -> dict[str, Any] | None:
        rows = await self._request(
            "GET",
            "note_versions",
            params={
                "note_id": f"eq.{note_id}",
                "version_number": f"eq.{version_number}",
            },
        )
        return rows[0] if rows else None

    # ── Shares ───────────────────────────────────────────────────────────────

    async def list_shares(
        self, owner_id: str | None = None, shared_with_id: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"order": "created_at.desc"}
        if owner_id:
            params["owner_id"] = f"eq.{owner_id}"
        if shared_with_id:
            params["shared_with_id"] = f"eq.{shared_with_id}"
        return await self._request("GET", "note_shares", params=params)

    async def create_share(
        self,
        owner_id: str,
        permission: str = "read",
        note_id: str | None = None,
        folder_id: str | None = None,
        shared_with_id: str | None = None,
        is_public: bool = False,
    ) -> dict[str, Any]:
        import secrets

        body: dict[str, Any] = {
            "id": str(uuid4()),
            "owner_id": owner_id,
            "note_id": note_id,
            "folder_id": folder_id,
            "shared_with_id": shared_with_id,
            "permission": permission,
            "is_public": is_public,
        }
        if is_public:
            body["public_token"] = secrets.token_urlsafe(32)
        rows = await self._request("POST", "note_shares", json_body=body)
        return rows[0] if rows else body

    async def update_share(
        self, share_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        rows = await self._request(
            "PATCH",
            "note_shares",
            params={"id": f"eq.{share_id}"},
            json_body=updates,
        )
        return rows[0] if rows else {}

    async def delete_share(self, share_id: str) -> None:
        await self._request("DELETE", "note_shares", params={"id": f"eq.{share_id}"})

    # ── Devices ──────────────────────────────────────────────────────────────

    async def register_device(
        self,
        user_id: str,
        device_id: str,
        device_name: str,
        platform: str,
        base_path: str,
    ) -> dict[str, Any]:
        body = {
            "user_id": user_id,
            "device_id": device_id,
            "device_name": device_name,
            "platform": platform,
            "base_path": base_path,
            "last_seen": "now()",
            "is_active": True,
        }
        # Upsert on (user_id, device_id)
        rows = await self._request(
            "POST",
            "note_devices",
            json_body=body,
            extra_headers={
                "Prefer": "return=representation,resolution=merge-duplicates"
            },
        )
        return rows[0] if rows else body

    async def update_device_seen(self, user_id: str, device_id: str) -> None:
        await self._request(
            "PATCH",
            "note_devices",
            params={
                "user_id": f"eq.{user_id}",
                "device_id": f"eq.{device_id}",
            },
            json_body={"last_seen": "now()"},
        )

    async def list_devices(self, user_id: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "note_devices",
            params={
                "user_id": f"eq.{user_id}",
                "is_active": "eq.true",
                "order": "last_seen.desc",
            },
        )

    # ── Directory Mappings ───────────────────────────────────────────────────

    async def list_mappings(self, user_id: str, device_id: str) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "note_directory_mappings",
            params={
                "user_id": f"eq.{user_id}",
                "device_id": f"eq.{device_id}",
                "is_active": "eq.true",
            },
        )

    async def create_mapping(
        self,
        user_id: str,
        device_id: str,
        folder_id: str,
        local_path: str,
    ) -> dict[str, Any]:
        body = {
            "id": str(uuid4()),
            "user_id": user_id,
            "device_id": device_id,
            "folder_id": folder_id,
            "local_path": local_path,
        }
        rows = await self._request("POST", "note_directory_mappings", json_body=body)
        return rows[0] if rows else body

    async def delete_mapping(self, mapping_id: str) -> None:
        await self._request(
            "DELETE",
            "note_directory_mappings",
            params={"id": f"eq.{mapping_id}"},
        )

    # ── Sync Log ─────────────────────────────────────────────────────────────

    async def log_sync(
        self,
        user_id: str,
        device_id: str,
        action: str,
        note_id: str | None = None,
        folder_id: str | None = None,
        sync_version: int | None = None,
        content_hash: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        body = {
            "user_id": user_id,
            "device_id": device_id,
            "action": action,
            "note_id": note_id,
            "folder_id": folder_id,
            "sync_version": sync_version,
            "content_hash": content_hash,
            "details": details or {},
        }
        try:
            await self._request(
                "POST",
                "note_sync_log",
                json_body=body,
                extra_headers={"Prefer": "return=minimal"},
            )
        except Exception:
            logger.warning("Failed to write sync log", exc_info=True)

    # ── Bulk fetch for sync ──────────────────────────────────────────────────

    async def get_notes_since(
        self, user_id: str, since_version: int
    ) -> list[dict[str, Any]]:
        """Get all notes updated since a given sync_version."""
        return await self._request(
            "GET",
            "notes",
            params={
                "created_by": f"eq.{user_id}",
                "sync_version": f"gt.{since_version}",
                "order": "sync_version.asc",
            },
            schema=_WORKBENCH,
        )

    async def get_all_notes_with_hashes(self, user_id: str) -> list[dict[str, Any]]:
        """Get id, file_path, content_hash, sync_version for all notes."""
        return await self._request(
            "GET",
            "notes",
            params={
                "created_by": f"eq.{user_id}",
                "deleted_at": "is.null",
                "select": "id,file_path,content_hash,sync_version,label,"
                "folder_name,folder_id,updated_at",
            },
            schema=_WORKBENCH,
        )


# Module-level singleton
supabase_docs = SupabaseDocClient()
