"""Durable, queryable evidence for local coding-session history reviews."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.services.local_db.write_gate import write_gate
from app.services.local_db.database import LocalDatabase, get_db

HistoryChangeType = Literal[
    "new", "content_changed", "metadata_changed", "missing", "unchanged"
]
_CHANGE_TYPES: tuple[HistoryChangeType, ...] = (
    "new",
    "content_changed",
    "metadata_changed",
    "missing",
    "unchanged",
)

# THE ARCHIVED-ITEMS LAW (Arman, 2026-09-09 —
# ../common-docs/policies/archived-items.md): every list over an entity that
# can be archived carries an archive filter, the default hides archived, and
# revealing them is one or two clicks. Three states and no more, named the way
# the whole platform names them (`ArchivedFilter` in matrx-frontend's
# lib/entity-list, `p_archived` in the agx_*/wfx_* RPCs, `ArchiveFilter` in
# @ai-matrx/design-system) so a person meets ONE control, not one per app.
#
# This replaced an `archived: bool | None` parameter whose default was None —
# "no clause", i.e. archived and active rows mixed on every first open, which
# is what the Claude History Inventory shipped. A boolean also cannot express
# "archived only", which is why the law names three states.
ArchiveFilter = Literal["active", "archived", "all"]
ARCHIVE_FILTERS: tuple[ArchiveFilter, ...] = ("active", "archived", "all")
ARCHIVE_FILTER_DEFAULT: ArchiveFilter = "active"

# `is_archived` is nullable in the scan rows (Claude's own index does not always
# say), and in SQLite `NULL = 0` is NULL, not true — so "active" MUST be written
# as an IS NOT / IS NULL pair or every row Claude never labelled silently
# vanishes from the default view.
_ARCHIVE_CLAUSES: dict[str, str] = {
    "active": "(is_archived IS NULL OR is_archived = 0)",
    "archived": "is_archived = 1",
    "all": "",
}

_SORT_COLUMNS = {
    "modified": "last_modified_ns",
    "title": "title COLLATE NOCASE",
    "project": "project_name COLLATE NOCASE",
    "bytes": "payload_bytes",
    "change": "change_type",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded))
        offset = value["offset"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid history page cursor") from exc
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("Invalid history page cursor")
    return offset


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class HistoryInventoryStore:
    """Local-only scan journal and table query facade.

    A scan is immutable once completed. Incomplete scans are never selected as
    comparison baselines, so a crash cannot replace the last trustworthy view.
    """

    def __init__(self, db: LocalDatabase | None = None) -> None:
        self._db = db or get_db()

    async def latest_completed_scan(
        self, provider: str, provider_account_key: str | None
    ) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            """SELECT * FROM coding_session_history_scans
               WHERE provider = ? AND provider_account_key IS ? AND status = 'completed'
               ORDER BY started_at DESC LIMIT 1""",
            (provider, provider_account_key),
        )
        return dict(row) if row else None

    async def comparison_rows(self, scan_id: str | None) -> dict[tuple[str, str], dict[str, Any]]:
        if scan_id is None:
            return {}
        rows = await self._db.fetchall(
            """SELECT * FROM coding_session_history_scan_rows
               WHERE scan_id = ? AND present = 1""",
            (scan_id,),
        )
        return {(row["session_id"], row["project_key"]): dict(row) for row in rows}

    async def begin_scan(
        self,
        *,
        provider: str,
        provider_account_key: str | None,
        previous_scan_id: str | None,
    ) -> str:
        scan_id = str(uuid4())
        # Top-level write span. The hook bridge commits every hook on its own
        # BEGIN IMMEDIATE connection; racing it here is how "Sync everything"
        # died "database is locked" after 42s on 2026-09-12. Take the gate.
        async with write_gate():
            await self._db.execute(
                """INSERT INTO coding_session_history_scans (
                       scan_id, provider, provider_account_key, previous_scan_id,
                       status, started_at
                   ) VALUES (?, ?, ?, ?, 'scanning', ?)""",
                (scan_id, provider, provider_account_key, previous_scan_id, _now()),
            )
            await self._db.commit()
        return scan_id

    async def complete_scan(
        self,
        scan_id: str,
        *,
        rows: list[dict[str, Any]],
        totals: dict[str, int],
    ) -> dict[str, Any]:
        # The bridge publisher commits every hook on its own BEGIN IMMEDIATE
        # connection. Racing it here is exactly what turned "Sync everything"
        # into a bare HTTP 500 ("database is locked", 2026-09-08 09:30 and
        # 16:30) after a 27s scan — take the engine-wide write gate instead.
        async with write_gate():
            return await self._complete_scan_locked(scan_id, rows=rows, totals=totals)

    async def _complete_scan_locked(
        self,
        scan_id: str,
        *,
        rows: list[dict[str, Any]],
        totals: dict[str, int],
    ) -> dict[str, Any]:
        await self._db.executemany(
            """INSERT INTO coding_session_history_scan_rows (
                   scan_id, session_id, project_key, present, change_type,
                   source_state, source_revision, title, title_source,
                   project_name, git_branch, worktree_name, is_archived,
                   is_pinned, pinned_rank, category, payload_bytes, file_count,
                   subagent_count, last_modified_ns, import_available,
                   import_blocked_reason
               ) VALUES (
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
               )""",
            [
                (
                    scan_id,
                    row["session_id"],
                    row["project_key"],
                    int(row.get("present", True)),
                    row["change_type"],
                    row.get("source_state"),
                    row.get("source_revision"),
                    row.get("title") or "Untitled session",
                    row.get("title_source"),
                    row.get("project_name") or "Local project",
                    row.get("git_branch"),
                    row.get("worktree_name"),
                    None
                    if row.get("is_archived") is None
                    else int(bool(row["is_archived"])),
                    None
                    if row.get("is_pinned") is None
                    else int(bool(row["is_pinned"])),
                    row.get("pinned_rank"),
                    row.get("category"),
                    int(row.get("bytes", 0)),
                    int(row.get("file_count", 0)),
                    int(row.get("subagent_count", 0)),
                    int(row.get("last_modified_ns", 0)),
                    int(bool(row.get("import_available", False))),
                    row.get("import_blocked_reason"),
                )
                for row in rows
            ],
        )
        counts = {name: 0 for name in _CHANGE_TYPES}
        for row in rows:
            counts[row["change_type"]] += 1
        present_count = sum(bool(row.get("present", True)) for row in rows)
        blocked_count = sum(
            bool(row.get("present", True)) and not row.get("import_available", False)
            for row in rows
        )
        await self._db.execute(
            """UPDATE coding_session_history_scans SET
                   status = 'completed', completed_at = ?, session_count = ?,
                   present_count = ?, new_count = ?, content_changed_count = ?,
                   metadata_changed_count = ?, missing_count = ?, unchanged_count = ?,
                   blocked_count = ?, file_count = ?, project_count = ?, total_bytes = ?
               WHERE scan_id = ?""",
            (
                _now(),
                len(rows),
                present_count,
                counts["new"],
                counts["content_changed"],
                counts["metadata_changed"],
                counts["missing"],
                counts["unchanged"],
                blocked_count,
                int(totals.get("files", 0)),
                int(totals.get("projects", 0)),
                int(totals.get("bytes", 0)),
                scan_id,
            ),
        )
        await self._db.commit()
        await self._prune_completed(keep=10)
        summary = await self.get_scan(scan_id)
        if summary is None:  # pragma: no cover - defensive database invariant
            raise RuntimeError("Completed history scan disappeared")
        return summary

    async def fail_scan(self, scan_id: str, error: str) -> None:
        # Sibling of begin_scan: same top-level write span, same gate.
        async with write_gate():
            await self._db.execute(
                """UPDATE coding_session_history_scans
                   SET status = 'failed', completed_at = ?, error_message = ?
                   WHERE scan_id = ?""",
                (_now(), error[:500], scan_id),
            )
            await self._db.commit()

    async def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM coding_session_history_scans WHERE scan_id = ?", (scan_id,)
        )
        return dict(row) if row else None

    async def list_rows(
        self,
        scan_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        search: str | None = None,
        change_types: tuple[HistoryChangeType, ...] = (),
        project: str | None = None,
        branch: str | None = None,
        archived: str = ARCHIVE_FILTER_DEFAULT,
        importable: bool | None = None,
        include_missing: bool = False,
        sort: str = "modified",
        direction: Literal["asc", "desc"] = "desc",
    ) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise ValueError("History page size must be between 1 and 200")
        sort_column = _SORT_COLUMNS.get(sort)
        if sort_column is None:
            raise ValueError("Unsupported history sort")
        offset = _decode_cursor(cursor)
        clauses = ["scan_id = ?"]
        params: list[Any] = [scan_id]
        if not include_missing:
            clauses.append("present = 1")
        if search and search.strip():
            needle = f"%{_escape_like(search.strip())}%"
            clauses.append(
                "(title LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                "project_name LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                "COALESCE(git_branch, '') LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                "session_id LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend([needle, needle, needle, needle])
        if change_types:
            placeholders = ",".join("?" for _ in change_types)
            clauses.append(f"change_type IN ({placeholders})")
            params.extend(change_types)
        if project:
            clauses.append("project_name = ?")
            params.append(project)
        if branch:
            clauses.append("git_branch = ?")
            params.append(branch)
        if importable is not None:
            clauses.append("import_available = ?")
            params.append(int(importable))

        # THE ARCHIVED-ITEMS LAW (Arman, 2026-09-09 —
        # ../common-docs/policies/archived-items.md). The archive predicate is
        # applied LAST and kept separate from the others on purpose: the honest
        # per-state counts below must be counted under every OTHER filter this
        # request carries, and differ only in this one clause. A count taken
        # over the whole scan would be a number the list cannot produce.
        if archived not in ARCHIVE_FILTERS:
            raise ValueError(
                "Unsupported archive filter; expected one of "
                + ", ".join(sorted(ARCHIVE_FILTERS))
            )
        unarchived_clauses = list(clauses)
        unarchived_params = list(params)
        archive_clause = _ARCHIVE_CLAUSES[archived]
        if archive_clause:
            clauses.append(archive_clause)

        base_where = " AND ".join(unarchived_clauses)
        archive_counts: dict[str, int] = {}
        for state in ARCHIVE_FILTERS:
            state_clause = _ARCHIVE_CLAUSES[state]
            state_where = f"{base_where} AND {state_clause}" if state_clause else base_where
            state_row = await self._db.fetchone(
                "SELECT COUNT(*) AS count FROM coding_session_history_scan_rows "
                f"WHERE {state_where}",
                tuple(unarchived_params),
            )
            archive_counts[state] = int(state_row["count"]) if state_row else 0

        where = " AND ".join(clauses)
        total = archive_counts[archived]
        order = "ASC" if direction == "asc" else "DESC"
        page = await self._db.fetchall(
            f"""SELECT * FROM coding_session_history_scan_rows
                WHERE {where}
                ORDER BY {sort_column} {order}, session_id ASC, project_key ASC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        )
        items = [self._deserialize_row(dict(row)) for row in page]
        next_offset = offset + len(items)
        return {
            "scan_id": scan_id,
            "items": items,
            # What the reader was ASKED for, echoed back, plus what each state
            # would render under the same other filters. A control that prints
            # a number the list cannot produce is a screen that lies.
            "archived": archived,
            "archive_counts": archive_counts,
            "page": {
                "returned": len(items),
                "total": total,
                "has_more": next_offset < total,
                "next_cursor": _encode_cursor(next_offset)
                if next_offset < total
                else None,
            },
        }

    async def facets(self, scan_id: str) -> dict[str, list[str]]:
        projects = await self._db.fetchall(
            """SELECT DISTINCT project_name FROM coding_session_history_scan_rows
               WHERE scan_id = ? AND present = 1 ORDER BY project_name COLLATE NOCASE""",
            (scan_id,),
        )
        branches = await self._db.fetchall(
            """SELECT DISTINCT git_branch FROM coding_session_history_scan_rows
               WHERE scan_id = ? AND present = 1 AND git_branch IS NOT NULL
               ORDER BY git_branch COLLATE NOCASE""",
            (scan_id,),
        )
        return {
            "projects": [row["project_name"] for row in projects],
            "branches": [row["git_branch"] for row in branches],
        }

    async def set_source_revisions(
        self,
        scan_id: str,
        revisions: list[dict[str, str]],
    ) -> None:
        """Attach lazily prepared content revisions to an immutable inventory.

        ``source_state`` is part of the update predicate. A preparation racing a
        file change therefore cannot bless a revision for the wrong inventory
        row; the caller must review again.
        """
        # One write transaction from the first UPDATE to the COMMIT; the gate
        # spans all of it (a sub-step gate lets another writer interleave).
        async with write_gate():
            for item in revisions:
                cursor = await self._db.execute(
                    """UPDATE coding_session_history_scan_rows
                       SET source_revision = ?
                       WHERE scan_id = ? AND session_id = ? AND project_key = ?
                         AND source_state = ? AND present = 1""",
                    (
                        item["source_revision"],
                        scan_id,
                        item["session_id"],
                        item["project_key"],
                        item["source_state"],
                    ),
                )
                if cursor.rowcount != 1:
                    await self._db.db.rollback()
                    raise ValueError("History inventory changed; review again")
            await self._db.commit()

    async def _prune_completed(self, *, keep: int) -> None:
        await self._db.execute(
            """DELETE FROM coding_session_history_scans
               WHERE status = 'completed' AND scan_id NOT IN (
                   SELECT scan_id FROM coding_session_history_scans
                   WHERE status = 'completed'
                   ORDER BY started_at DESC LIMIT ?
               )""",
            (keep,),
        )
        await self._db.commit()

    @staticmethod
    def _deserialize_row(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("present", "import_available"):
            row[key] = bool(row[key])
        for key in ("is_archived", "is_pinned"):
            row[key] = None if row[key] is None else bool(row[key])
        row["bytes"] = row.pop("payload_bytes")
        return row


__all__ = ["HistoryChangeType", "HistoryInventoryStore"]
