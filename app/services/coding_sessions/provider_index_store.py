"""The persisted, incremental session index — for ANY provider.

WHY THIS IS GENERIC. The Claude Code index
(:mod:`app.services.coding_sessions.claude_index_store`) exists because
listing sessions by reading them cost minutes and did it inside the request:
31.76 s and 58.96 s measured on the installed app, 1,209 s on a fresh engine.
Codex is bigger, not smaller — **5,041 rollout files and 30.33 GB on this Mac,
measured 2026-09-17** — so the second provider must not re-learn that lesson,
and the third must not re-implement the fix. Everything that made the Claude
store safe is here once, with the provider as data:

* one row per SOURCE FILE, keyed by that file's ``(mtime_ns, size)``, so a
  refresh re-reads only what moved and a cold engine start re-reads ~0;
* a row is kept even for a file that could not be reduced, so an unreadable
  source is counted honestly and is not re-read every pass;
* WAL, in its own file per provider, never in ``matrx.db``: the engine reads,
  exactly one refresh writes, and none of it touches the shared-SQLite
  contention class;
* a ``revision`` that changes only when a refresh COMPLETES, so a reader
  either sees the whole previous refresh or the whole new one.

WHAT IT DOES NOT KNOW. The store persists a provider's reduced facts as the
provider's own JSON and never interprets them. Merging file rows into one row
per session is the adapter's rule (Codex forks a thread when its context rolls
over; Claude keeps one record per account), and a rule that differs per
provider does not belong in shared storage.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

SCHEMA_VERSION = 1

# One chunk of source files is read, reduced and written under a single worker
# thread hop, and the loop awaits between chunks. This is what keeps the event
# loop answering /health while a cold build runs.
DEFAULT_CHUNK_SIZE = 64


def default_store_path(provider: str) -> Path:
    """Where one provider's persisted index lives (its own file)."""
    configured = os.environ.get(f"MATRX_{provider.upper()}_INDEX_DB")
    if configured:
        return Path(configured).expanduser()
    from app.config import MATRX_HOME_DIR

    return Path(MATRX_HOME_DIR) / f"{provider.replace('_', '-')}-session-index.sqlite3"


@dataclass(frozen=True)
class ProviderIndexSnapshot:
    """Everything an adapter needs about its index, with no disk walk."""

    provider: str = ""
    # One entry per SOURCE FILE: (path, reduced facts). The adapter merges.
    files: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    updated_at: str | None = None
    revision: int = 0
    complete: bool = False
    last_duration_seconds: float | None = None
    last_changed_files: int | None = None
    error: str | None = None


class ProviderIndexStore:
    """SQLite home of one provider's reduced file rows. Cheap to construct."""

    def __init__(self, provider: str, path: Path | None = None) -> None:
        self.provider = provider
        self.path = Path(path) if path is not None else default_store_path(provider)

    # ── connection ──────────────────────────────────────────────────────
    def connect(self, *, write: bool = False) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=NORMAL")
        if write:
            self._migrate(connection)
        return connection

    def _migrate(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                session_id TEXT,
                last_activity_at INTEGER NOT NULL DEFAULT 0,
                unreadable INTEGER NOT NULL DEFAULT 0,
                facts TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS files_session
                ON files (session_id, last_activity_at);
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def reset(self) -> None:
        """Drop everything (a schema change; never a routine path)."""
        with self.connect(write=True) as connection:
            connection.executescript(
                "DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS meta;"
            )
            self._migrate(connection)

    # ── reads ───────────────────────────────────────────────────────────
    def revision(self) -> int:
        """Bumped only when a refresh COMPLETES. 0 = never completed one."""
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'revision'"
                ).fetchone()
        except sqlite3.Error:
            return 0
        try:
            return int(row["value"]) if row else 0
        except (TypeError, ValueError):
            return 0

    def stamps(self) -> dict[str, tuple[int, int]]:
        """``{path: (mtime_ns, size)}`` for every row already reduced."""
        out: dict[str, tuple[int, int]] = {}
        try:
            with self.connect() as connection:
                for row in connection.execute("SELECT path, mtime_ns, size FROM files"):
                    out[str(row["path"])] = (int(row["mtime_ns"]), int(row["size"]))
        except sqlite3.Error:
            return {}
        return out

    def load(self) -> ProviderIndexSnapshot:
        """Every reduced row plus the last refresh's own account of itself."""
        files: list[tuple[str, dict[str, Any]]] = []
        meta: dict[str, str] = {}
        unreadable = 0
        try:
            with self.connect() as connection:
                for row in connection.execute(
                    "SELECT path, facts, unreadable FROM files"
                ):
                    if int(row["unreadable"] or 0):
                        unreadable += 1
                    try:
                        facts = json.loads(row["facts"])
                    except (TypeError, ValueError):
                        facts = {}
                    if isinstance(facts, dict):
                        files.append((str(row["path"]), facts))
                for row in connection.execute("SELECT key, value FROM meta"):
                    meta[str(row["key"])] = row["value"]
        except sqlite3.Error as exc:
            return ProviderIndexSnapshot(
                provider=self.provider, error=f"{type(exc).__name__}: {exc}"
            )
        return ProviderIndexSnapshot(
            provider=self.provider,
            files=files,
            totals={
                "files": len(files),
                "unreadable": unreadable,
                "truncated": _int(meta.get("truncated")) or 0,
            },
            updated_at=meta.get("updated_at"),
            revision=_int(meta.get("revision")) or 0,
            complete=bool(_int(meta.get("revision"))),
            last_duration_seconds=_float(meta.get("last_duration_seconds")),
            last_changed_files=_int(meta.get("last_changed_files")),
        )

    # ── writes ──────────────────────────────────────────────────────────
    def upsert(self, rows: Sequence[dict[str, Any]]) -> None:
        """Write one chunk of reduced file rows."""
        if not rows:
            return
        with self.connect(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                """INSERT INTO files
                     (path, mtime_ns, size, session_id, last_activity_at,
                      unreadable, facts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                     mtime_ns = excluded.mtime_ns,
                     size = excluded.size,
                     session_id = excluded.session_id,
                     last_activity_at = excluded.last_activity_at,
                     unreadable = excluded.unreadable,
                     facts = excluded.facts""",
                [
                    (
                        str(row["path"]),
                        int(row.get("mtime_ns") or 0),
                        int(row.get("size") or 0),
                        row.get("session_id"),
                        int(row.get("last_activity_at") or 0),
                        1 if row.get("unreadable") else 0,
                        json.dumps(row.get("facts") or {}, separators=(",", ":")),
                    )
                    for row in rows
                ],
            )
            connection.execute("COMMIT")

    def prune(self, alive: Iterable[str]) -> int:
        """Drop rows for source files that no longer exist. Returns removed."""
        keep = set(alive)
        with self.connect(write=True) as connection:
            existing = {
                str(row["path"])
                for row in connection.execute("SELECT path FROM files")
            }
            gone = sorted(existing - keep)
            if not gone:
                return 0
            connection.execute("BEGIN IMMEDIATE")
            for start in range(0, len(gone), 500):
                batch = gone[start : start + 500]
                connection.execute(
                    f"DELETE FROM files WHERE path IN ({','.join('?' * len(batch))})",
                    batch,
                )
            connection.execute("COMMIT")
        return len(gone)

    def finalize(
        self,
        *,
        changed: int,
        removed: int,
        truncated: bool,
        duration: float,
    ) -> dict[str, Any]:
        """Publish the refresh: bump the revision and record what it did."""
        from datetime import datetime, timezone

        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.connect(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            revision = (
                _int(
                    (
                        connection.execute(
                            "SELECT value FROM meta WHERE key = 'revision'"
                        ).fetchone()
                        or {"value": None}
                    )["value"]
                )
                or 0
            ) + 1
            connection.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [
                    ("revision", str(revision)),
                    ("updated_at", updated_at),
                    ("last_changed_files", str(int(changed))),
                    ("last_duration_seconds", f"{float(duration):.3f}"),
                    ("truncated", "1" if truncated else "0"),
                ],
            )
            connection.execute("COMMIT")
        return {
            "provider": self.provider,
            "revision": revision,
            "changed_files": int(changed),
            "removed_files": int(removed),
            "truncated": bool(truncated),
            "duration_seconds": round(float(duration), 3),
            "updated_at": updated_at,
        }


def plan_refresh(
    found: dict[str, tuple[int, int]], store: ProviderIndexStore
) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Which source files must be re-read, and which rows stay alive.

    ``found`` is the caller's stat walk — the store never walks a provider's
    tree itself, because where a provider keeps its sessions is the adapter's
    knowledge, not storage's.
    """
    known = store.stamps()
    changed: list[tuple[str, int, int]] = []
    for path, (mtime_ns, size) in found.items():
        if known.get(path) != (mtime_ns, size):
            changed.append((path, mtime_ns, size))
    return changed, list(found)


def chunks(
    items: Sequence[tuple[str, int, int]], size: int = DEFAULT_CHUNK_SIZE
) -> Iterator[Sequence[tuple[str, int, int]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def index_report(
    snapshot: ProviderIndexSnapshot, *, refreshing: bool, error: str | None = None
) -> dict[str, Any]:
    """What the screen says about the index behind the rows it is showing.

    Deliberately the SAME three states and the same field names the Claude
    index reports (``claude_overview.index_report``), because the screen must
    not need to know which provider a cold index belongs to.
    """
    if not snapshot.complete:
        state = "cold"
    elif refreshing:
        state = "refreshing"
    else:
        state = "fresh"
    return {
        "state": state,
        "refreshing": bool(refreshing),
        "files_read": int(snapshot.totals.get("files", 0)),
        "updated_at": snapshot.updated_at,
        "changed_files": snapshot.last_changed_files,
        "duration_seconds": snapshot.last_duration_seconds,
        "limit_reached": bool(snapshot.totals.get("truncated")),
        "unreadable": int(snapshot.totals.get("unreadable", 0)),
        "error": error or snapshot.error,
    }


def _int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "SCHEMA_VERSION",
    "ProviderIndexSnapshot",
    "ProviderIndexStore",
    "chunks",
    "default_store_path",
    "index_report",
    "plan_refresh",
]
