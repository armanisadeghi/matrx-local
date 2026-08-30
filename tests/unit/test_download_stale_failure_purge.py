"""Startup history restore purges ancient zero-progress failures.

2026-08-30: three GGUF validation failures from 2026-03-29 were still being
restored as boot warnings five months later — the records were immortal unless
manually dismissed. `_load_history` now deletes failed/cancelled rows with no
downloaded bytes and no activity for FAILURE_HISTORY_RETENTION_DAYS; recent
failures and anything with partial progress survive untouched.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.downloads import manager as downloads_manager
from app.services.downloads.manager import DownloadManager


_COLUMNS = (
    "id TEXT PRIMARY KEY, category TEXT, filename TEXT, display_name TEXT, "
    "urls TEXT, total_bytes INTEGER, bytes_done INTEGER, status TEXT, "
    "error_msg TEXT, priority INTEGER, part_current INTEGER, part_total INTEGER, "
    "created_at TEXT, updated_at TEXT, completed_at TEXT, metadata TEXT"
)


class _StubDb:
    """The minimal get_db() surface `_load_history` touches, on a tmp file."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"CREATE TABLE downloads ({_COLUMNS})")
        self._conn.commit()

    async def execute(self, sql: str, params: tuple = ()):  # noqa: ANN201
        return self._conn.execute(sql, params)

    async def fetchall(self, sql: str, params: tuple = ()):  # noqa: ANN201
        return self._conn.execute(sql, params).fetchall()

    async def commit(self) -> None:
        self._conn.commit()

    def seed(self, row_id: str, *, status: str, bytes_done: int, updated_at: str) -> None:
        self._conn.execute(
            "INSERT INTO downloads (id, category, filename, display_name, urls, "
            "total_bytes, bytes_done, status, error_msg, priority, part_current, "
            "part_total, created_at, updated_at, completed_at, metadata) VALUES "
            "(?, 'llm', ?, ?, '[]', 0, ?, ?, 'validation failed', 0, 0, 0, ?, ?, NULL, NULL)",
            (row_id, f"{row_id}.gguf", row_id, bytes_done, status, updated_at, updated_at),
        )
        self._conn.commit()

    def ids(self) -> set[str]:
        return {r["id"] for r in self._conn.execute("SELECT id FROM downloads")}


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def test_ancient_zero_progress_failures_are_purged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubDb(tmp_path / "downloads.db")
    stub.seed("ancient-failure", status="failed", bytes_done=0, updated_at=_days_ago(150))
    stub.seed("ancient-cancel", status="cancelled", bytes_done=0, updated_at=_days_ago(90))
    stub.seed("recent-failure", status="failed", bytes_done=0, updated_at=_days_ago(2))
    stub.seed("partial-failure", status="failed", bytes_done=999, updated_at=_days_ago(150))
    stub.seed("old-completed", status="completed", bytes_done=42, updated_at=_days_ago(300))

    monkeypatch.setattr(downloads_manager, "get_db", lambda: stub)
    monkeypatch.setattr(
        "app.services.media_gen.paths.read_hf_token", lambda: None
    )
    monkeypatch.setattr(
        "app.services.media_gen.paths.read_civitai_key", lambda: None
    )

    mgr = DownloadManager()
    asyncio.run(mgr._load_history())

    assert stub.ids() == {"recent-failure", "partial-failure", "old-completed"}
    assert "ancient-failure" not in mgr._entries
    assert "recent-failure" in mgr._entries
