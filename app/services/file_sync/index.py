"""The local file index — mirror rows + on-disk state.

Cloud truth lives in the ATTACHed structural mirror (``files.files`` /
``files.folders``, schema_mirror/snapshot.json is the spec). The local half
— what is actually on this disk and what still needs to move — lives in the
``file_sync_state`` sidecar (schema V11). Together they ARE the virtual
mapping: pointer mode is "index only, bytes on demand".

All timestamps stored as ISO-8601 TEXT (PostgREST JSON form), matching the
mirror convention.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.common.system_logger import get_logger
from app.services.local_db.database import get_db

logger = get_logger()

# sha256 of zero bytes — a pointer placeholder's content hash.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# The change-feed field subset we mirror (wire name -> files.files column).
_FILE_FEED_TO_MIRROR = {
    "file_id": "id",
    "file_path": "file_path",
    "file_name": "file_name",
    "mime_type": "mime_type",
    "size_bytes": "size_bytes",
    "checksum": "checksum",
    "visibility": "visibility",
    "version": "current_version",
    "folder_id": "parent_folder_id",
    "created_at": "created_at",
    "updated_at": "updated_at",
    "deleted_at": "deleted_at",
}

_FOLDER_FEED_TO_MIRROR = {
    "folder_id": "id",
    "folder_path": "folder_path",
    "folder_name": "folder_name",
    "parent_id": "parent_id",
    "visibility": "visibility",
    "is_system": "is_system",
    "created_at": "created_at",
    "updated_at": "updated_at",
    "deleted_at": "deleted_at",
}

LOCAL_ID_PREFIX = "local:"


def new_local_id() -> str:
    return f"{LOCAL_ID_PREFIX}{uuid.uuid4()}"


class FileSyncIndex:
    """Typed access to the files.* mirror + file_sync_state sidecar."""

    # ------------------------------------------------------------------
    # Mirror upserts (cloud rows, from the change feed / API echoes)
    # ------------------------------------------------------------------

    async def upsert_remote_file(self, entry: dict[str, Any]) -> None:
        await self._upsert_mirror("files", _FILE_FEED_TO_MIRROR, entry)

    async def upsert_remote_folder(self, entry: dict[str, Any]) -> None:
        await self._upsert_mirror("folders", _FOLDER_FEED_TO_MIRROR, entry)

    async def _upsert_mirror(
        self, table: str, mapping: dict[str, str], entry: dict[str, Any]
    ) -> None:
        cols: dict[str, Any] = {}
        for wire, col in mapping.items():
            if wire in entry:
                value = entry[wire]
                if isinstance(value, bool):
                    value = int(value)
                cols[col] = value
        if "id" not in cols or cols["id"] is None:
            logger.error("[file_sync] %s feed entry missing id — skipped: %r", table, entry)
            return
        db = get_db()
        col_sql = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        update_sql = ", ".join(f'"{c}" = excluded."{c}"' for c in cols if c != "id")
        await db.execute(
            f'INSERT INTO "files"."{table}" ({col_sql}) VALUES ({placeholders}) '
            f'ON CONFLICT("id") DO UPDATE SET {update_sql}',
            tuple(cols[c] for c in cols),
        )

    async def get_remote_file(self, file_id: str) -> dict[str, Any] | None:
        row = await get_db().fetchone(
            'SELECT * FROM "files"."files" WHERE id = ?', (file_id,)
        )
        return dict(row) if row else None

    async def get_remote_file_by_path(self, file_path: str) -> dict[str, Any] | None:
        row = await get_db().fetchone(
            'SELECT * FROM "files"."files" WHERE file_path = ? AND deleted_at IS NULL',
            (file_path,),
        )
        return dict(row) if row else None

    async def list_remote_folders(self) -> list[dict[str, Any]]:
        rows = await get_db().fetchall(
            'SELECT * FROM "files"."folders" WHERE deleted_at IS NULL ORDER BY folder_path'
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Sidecar state
    # ------------------------------------------------------------------

    async def get_state(self, file_id: str) -> dict[str, Any] | None:
        row = await get_db().fetchone(
            "SELECT * FROM file_sync_state WHERE file_id = ?", (file_id,)
        )
        return dict(row) if row else None

    async def get_state_by_path(self, rel_path: str) -> dict[str, Any] | None:
        row = await get_db().fetchone(
            "SELECT * FROM file_sync_state WHERE rel_path = ?", (rel_path,)
        )
        return dict(row) if row else None

    async def upsert_state(self, file_id: str, **fields: Any) -> None:
        """Insert or update a state row. ``rel_path`` is required on insert."""
        db = get_db()
        existing = await self.get_state(file_id)
        fields["updated_at"] = _now_iso()
        if existing is None:
            if "rel_path" not in fields:
                raise ValueError(f"new state row for {file_id} requires rel_path")
            cols = {"file_id": file_id, **fields}
            col_sql = ", ".join(cols)
            placeholders = ", ".join("?" for _ in cols)
            await db.execute(
                f"INSERT INTO file_sync_state ({col_sql}) VALUES ({placeholders})",
                tuple(cols.values()),
            )
        else:
            sets = ", ".join(f"{c} = ?" for c in fields)
            await db.execute(
                f"UPDATE file_sync_state SET {sets} WHERE file_id = ?",
                (*fields.values(), file_id),
            )

    async def finalize_if_hash_unchanged(
        self, file_id: str, *, expected_local_hash: str, **fields: Any
    ) -> bool:
        """Optimistic completion write: apply ``fields`` only when the row's
        local_hash still equals what was pushed. Returns False when a newer
        local edit landed mid-push (the caller leaves it queued)."""
        db = get_db()
        fields["updated_at"] = _now_iso()
        sets = ", ".join(f"{c} = ?" for c in fields)
        cursor = await db.execute(
            f"UPDATE file_sync_state SET {sets} WHERE file_id = ? AND local_hash = ?",
            (*fields.values(), file_id, expected_local_hash),
        )
        return bool(getattr(cursor, "rowcount", 0))

    async def delete_state(self, file_id: str) -> None:
        await get_db().execute(
            "DELETE FROM file_sync_state WHERE file_id = ?", (file_id,)
        )

    async def rekey_state(self, old_id: str, new_id: str) -> None:
        """A local-only file landed in the cloud — adopt the cloud id."""
        db = get_db()
        existing = await self.get_state(new_id)
        if existing is not None:
            # The cloud id is already tracked (e.g. feed arrived first) —
            # merge by dropping the provisional local row.
            await self.delete_state(old_id)
            return
        await db.execute(
            "UPDATE file_sync_state SET file_id = ?, updated_at = ? WHERE file_id = ?",
            (new_id, _now_iso(), old_id),
        )

    async def list_pending(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = await get_db().fetchall(
            "SELECT * FROM file_sync_state WHERE pending_op IS NOT NULL "
            "ORDER BY updated_at LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    async def list_by_state(self, state: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = await get_db().fetchall(
            "SELECT * FROM file_sync_state WHERE local_state = ? "
            "ORDER BY updated_at LIMIT ?",
            (state, limit),
        )
        return [dict(r) for r in rows]

    async def list_by_state_under_path(
        self, state: str, rel_path: str
    ) -> list[dict[str, Any]]:
        """Return every tracked descendant in ``state`` under a local path.

        Directory copy/move needs this unbounded-by-page correctness query:
        silently copying pointer placeholders would create corrupt zero-byte
        replicas. The caller controls concurrency while hydrating the rows.
        """
        if not rel_path or rel_path == ".":
            rows = await get_db().fetchall(
                "SELECT * FROM file_sync_state WHERE local_state = ? ORDER BY rel_path",
                (state,),
            )
            return [dict(r) for r in rows]
        escaped = rel_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        prefix = escaped.rstrip("/") + "/%"
        rows = await get_db().fetchall(
            "SELECT * FROM file_sync_state WHERE local_state = ? "
            "AND (rel_path = ? OR rel_path LIKE ? ESCAPE '\\') ORDER BY rel_path",
            (state, rel_path, prefix),
        )
        return [dict(r) for r in rows]

    async def counts(self) -> dict[str, int]:
        db = get_db()
        out: dict[str, int] = {}
        rows = await db.fetchall(
            "SELECT local_state, COUNT(*) AS cnt FROM file_sync_state GROUP BY local_state"
        )
        for r in rows:
            out[r["local_state"]] = r["cnt"]
        pending = await db.fetchone(
            "SELECT COUNT(*) AS cnt FROM file_sync_state WHERE pending_op IS NOT NULL"
        )
        out["pending_ops"] = pending["cnt"] if pending else 0
        tracked = await db.fetchone("SELECT COUNT(*) AS cnt FROM file_sync_state")
        out["tracked"] = tracked["cnt"] if tracked else 0
        return out

    async def find_pending_delete_by_hash(self, local_hash: str) -> dict[str, Any] | None:
        """Rename detection: a just-deleted entry whose content reappeared
        elsewhere is a move, not a delete+create."""
        if not local_hash or local_hash == EMPTY_SHA256:
            return None
        row = await get_db().fetchone(
            "SELECT * FROM file_sync_state WHERE pending_op = 'delete' AND local_hash = ? "
            "LIMIT 1",
            (local_hash,),
        )
        return dict(row) if row else None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
