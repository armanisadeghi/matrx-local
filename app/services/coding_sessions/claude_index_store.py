"""The persisted, incremental index of Claude Code's session records.

WHY THIS EXISTS. Claude keeps one sidebar record per account per conversation,
and on this Mac that is **67,224 files and 3.1 GB** for ~1,900 conversations
(measured 2026-09-15). Reading all of them costs minutes, and until now the
Coding Sessions screen did exactly that on a cold cache — measured by lane
V-ML at 31.76 s and 58.96 s on the installed app against a hard 60 s client
ceiling, and 1,209 s on a fresh engine. Worse, the read happened while the
request was open, so the screen's only data source was also the thing that
made it time out.

WHAT CHANGED. The reduced form of every record (the display labels the screen
actually shows) is persisted here, one row per record FILE, keyed by that
file's ``(mtime_ns, size)``. A refresh stats the tree and re-reads only the
files whose stamp moved. So:

* the screen never waits for a walk — it loads rows from SQLite and answers;
* a cold engine start after the first run re-reads ~0 files, not 67,224;
* the 3.1 GB read happens once, in the background, while nobody is waiting.

A row is kept even for a file that could not be parsed, so an unreadable
record is counted honestly and is not re-read on every pass.

ONE WRITER, MANY READERS. The database is WAL and lives in its own file (never
in ``matrx.db``): the engine only ever reads it, and exactly one refresh writes
at a time — enforced by the single-flight lock in
:mod:`app.services.coding_sessions.claude_overview`. That is what keeps this
off the shared-SQLite contention class.

The merge from record rows to one entry per conversation is NOT duplicated
here: it is :func:`claude_session_index.merge_entries`, the same function the
full scan uses, so the sidebar-ledger rules (a person's rename never loses to
a sibling account's auto title) hold on both paths.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from app.services.coding_sessions.claude_session_index import (
    MAX_INDEX_FILE_BYTES,
    MAX_INDEX_FILES,
    ClaudeSessionIndexEntry,
    entry_from_record,
    merge_entries,
)

SCHEMA_VERSION = 1

# One chunk of record files is read, parsed and written under a single worker
# thread hop. 256 × ~46 KB is ~12 MB and tens of milliseconds, which is what
# keeps the event loop answering /health while a full build runs (T2).
DEFAULT_CHUNK_SIZE = 256


def default_store_path() -> Path:
    """Where the persisted index lives (its own file, never ``matrx.db``)."""
    configured = os.environ.get("MATRX_CLAUDE_INDEX_DB")
    if configured:
        return Path(configured).expanduser()
    from app.config import MATRX_HOME_DIR

    return Path(MATRX_HOME_DIR) / "claude-session-index.sqlite3"


@dataclass(frozen=True)
class IndexSnapshot:
    """Everything the screen needs about the index, with no disk walk."""

    entries: dict[str, ClaudeSessionIndexEntry] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    accounts: dict[str, int] = field(default_factory=dict)
    # session id -> (bytes, mtime_ns) for every transcript on disk.
    transcripts: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Transcripts with no sidebar record anywhere -> (title, project).
    orphan_summaries: dict[str, tuple[str | None, str | None]] = field(
        default_factory=dict
    )
    record_counts: dict[str, int] = field(default_factory=dict)
    updated_at: str | None = None
    revision: int = 0
    complete: bool = False
    last_duration_seconds: float | None = None
    last_changed_files: int | None = None


_RECORD_COLUMNS = (
    "path",
    "mtime_ns",
    "size",
    "account",
    "cli_session_id",
    "last_activity_at",
    "title",
    "title_source",
    "workspace_name",
    "git_branch",
    "worktree_name",
    "is_archived",
    "local_cwd",
    "unreadable",
)


class ClaudeIndexStore:
    """SQLite home of the reduced record rows. Cheap to construct."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_store_path()

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
            CREATE TABLE IF NOT EXISTS records (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                account TEXT,
                cli_session_id TEXT,
                last_activity_at INTEGER NOT NULL DEFAULT 0,
                title TEXT,
                title_source TEXT,
                workspace_name TEXT,
                git_branch TEXT,
                worktree_name TEXT,
                is_archived INTEGER,
                local_cwd TEXT,
                unreadable INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS records_session
                ON records (cli_session_id, last_activity_at, mtime_ns);
            -- The winner per conversation, materialized at the END of every
            -- refresh. Picking it at load time with a window function over
            -- 67,224 rows measured 477-565 ms; reading 1,934 finished rows is
            -- ~25 ms, and the work belongs in the refresh nobody waits for.
            CREATE TABLE IF NOT EXISTS sessions (
                cli_session_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                record_count INTEGER NOT NULL DEFAULT 1,
                last_activity_at INTEGER NOT NULL DEFAULT 0,
                title TEXT,
                title_source TEXT,
                workspace_name TEXT,
                git_branch TEXT,
                worktree_name TEXT,
                is_archived INTEGER,
                local_cwd TEXT
            );
            CREATE TABLE IF NOT EXISTS transcripts (
                session_id TEXT PRIMARY KEY,
                size INTEGER NOT NULL DEFAULT 0,
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                orphan INTEGER NOT NULL DEFAULT 0,
                title TEXT,
                project TEXT,
                summary_mtime_ns INTEGER
            );
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
                "DROP TABLE IF EXISTS records;"
                "DROP TABLE IF EXISTS sessions;"
                "DROP TABLE IF EXISTS transcripts;"
                "DROP TABLE IF EXISTS meta;"
            )
            self._migrate(connection)

    def ensure_ready(self) -> None:
        """Create the file/schema, or rebuild it when the schema moved."""
        with self.connect(write=True) as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        stored = int(row["value"]) if row and str(row["value"]).isdigit() else SCHEMA_VERSION
        if stored != SCHEMA_VERSION:
            self.reset()

    # ── reads ───────────────────────────────────────────────────────────
    def _meta(self, connection: sqlite3.Connection) -> dict[str, str]:
        try:
            rows = connection.execute("SELECT key, value FROM meta").fetchall()
        except sqlite3.DatabaseError:
            return {}
        return {str(row["key"]): str(row["value"]) for row in rows}

    def revision(self) -> int:
        """The completed-refresh counter. One tiny query; safe to call often."""
        if not self.path.exists():
            return 0
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'revision'"
                ).fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row["value"]) if row and str(row["value"]).isdigit() else 0

    def known_stamps(self) -> dict[str, tuple[int, int]]:
        """path -> (mtime_ns, size) for every row already stored."""
        if not self.path.exists():
            return {}
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT path, mtime_ns, size FROM records"
                ).fetchall()
        except sqlite3.DatabaseError:
            return {}
        return {str(r["path"]): (int(r["mtime_ns"]), int(r["size"])) for r in rows}

    # The freshest record wins, and the tie-break on the record file's own
    # mtime is load-bearing — a rename in Claude Code rewrites only the active
    # account's copy WITHOUT bumping lastActivityAt, so every copy ties and
    # only mtime separates them (observed 2026-08-16). The ORDER BY inside
    # :meth:`load` is the same rule as
    # :func:`claude_session_index.merge_entries`, and
    # ``test_store_and_scan_pick_the_same_record`` holds the two together.
    def load(
        self,
        *,
        ledger: dict[str, dict[str, Any]] | None = None,
        record_paths: bool = False,
    ) -> IndexSnapshot:
        """The complete index, rebuilt from rows. No filesystem walk at all.

        The screen's path reads ~3,600 finished rows (the materialized winner
        per conversation plus the transcripts) and nothing else.
        ``record_paths=True`` additionally loads every account's copy of every
        conversation from ``records``; only the RETURN direction (writing a
        rename back into Claude's own record files) and one session's
        diagnosis need that, never the screen.
        """
        empty = {"files": 0, "records": 0, "unreadable": 0}
        if not self.path.exists():
            return IndexSnapshot(totals=dict(empty))
        try:
            with self.connect() as connection:
                meta = self._meta(connection)
                rows = connection.execute(
                    "SELECT cli_session_id, path, mtime_ns, record_count, "
                    "last_activity_at, title, title_source, workspace_name, "
                    "git_branch, worktree_name, is_archived, local_cwd FROM sessions"
                ).fetchall()
                paths_by_session: dict[str, list[Path]] = {}
                if record_paths:
                    for row in connection.execute(
                        "SELECT cli_session_id, path FROM records "
                        "WHERE cli_session_id IS NOT NULL AND unreadable = 0 "
                        "ORDER BY path"
                    ):
                        paths_by_session.setdefault(
                            str(row["cli_session_id"]), []
                        ).append(Path(str(row["path"])))
                transcripts, orphan_summaries = self._load_transcripts(connection)
        except sqlite3.DatabaseError:
            return IndexSnapshot(totals=dict(empty))

        record_counts: dict[str, int] = {}
        candidates: list[tuple[Path, int, ClaudeSessionIndexEntry]] = []
        for row in rows:
            session_id = str(row["cli_session_id"])
            record_counts[session_id] = int(row["record_count"] or 1)
            local_cwd = row["local_cwd"]
            archived = row["is_archived"]
            candidates.append(
                (
                    Path(str(row["path"])),
                    int(row["mtime_ns"] or 0),
                    ClaudeSessionIndexEntry(
                        cli_session_id=session_id,
                        title=row["title"],
                        title_source=row["title_source"],
                        workspace_name=row["workspace_name"],
                        git_branch=row["git_branch"],
                        worktree_name=row["worktree_name"],
                        is_archived=None if archived is None else bool(archived),
                        last_activity_at=int(row["last_activity_at"] or 0),
                        local_cwd=Path(str(local_cwd)) if local_cwd else None,
                    ),
                )
            )
        # The ledger is read at LOAD time, not refresh time: it is one small
        # JSON file, and a pin or a rename the sync agent lands there must
        # reach the screen without waiting for 67,224 files to be re-stated.
        entries = merge_entries(candidates, ledger=ledger)
        if record_paths:
            entries = {
                session_id: replace(
                    entry, record_paths=tuple(paths_by_session.get(session_id, ()))
                )
                for session_id, entry in entries.items()
            }
        accounts: dict[str, int] = {}
        try:
            stored_accounts = json.loads(meta.get("accounts") or "{}")
            if isinstance(stored_accounts, dict):
                accounts = {
                    str(key): int(value) for key, value in stored_accounts.items()
                }
        except (ValueError, TypeError):
            accounts = {}
        totals: dict[str, int] = {
            "files": int(meta["files"]) if meta.get("files", "").isdigit() else 0,
            "records": len(entries),
            "unreadable": (
                int(meta["unreadable"]) if meta.get("unreadable", "").isdigit() else 0
            ),
        }
        if meta.get("truncated") == "1":
            totals["truncated"] = 1
        duration = meta.get("last_duration_seconds")
        changed = meta.get("last_changed_files")
        return IndexSnapshot(
            entries=entries,
            totals=totals,
            accounts=accounts,
            transcripts=transcripts,
            orphan_summaries=orphan_summaries,
            record_counts=record_counts,
            updated_at=meta.get("updated_at"),
            revision=int(meta["revision"]) if meta.get("revision", "").isdigit() else 0,
            complete=meta.get("complete") == "1",
            last_duration_seconds=float(duration) if duration else None,
            last_changed_files=int(changed) if changed and changed.isdigit() else None,
        )

    def rebuild_sessions(self) -> dict[str, Any]:
        """Materialize the winner per conversation, plus the counts in meta.

        The freshest record wins, and the tie-break on the record file's own
        mtime is load-bearing: a rename in Claude Code rewrites only the active
        account's copy WITHOUT bumping ``lastActivityAt``, so every copy ties
        and only mtime separates them (observed 2026-08-16). This ORDER BY is
        the same rule as :func:`claude_session_index.merge_entries`, and
        ``test_store_and_scan_agree_on_the_winning_record`` holds them together.
        """
        with self.connect(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM sessions")
            connection.execute(
                """
                INSERT INTO sessions (
                    cli_session_id, path, mtime_ns, record_count,
                    last_activity_at, title, title_source, workspace_name,
                    git_branch, worktree_name, is_archived, local_cwd
                )
                SELECT cli_session_id, path, mtime_ns, record_count,
                       last_activity_at, title, title_source, workspace_name,
                       git_branch, worktree_name, is_archived, local_cwd
                FROM (
                  SELECT *, ROW_NUMBER() OVER (
                      PARTITION BY cli_session_id
                      ORDER BY last_activity_at DESC, mtime_ns DESC, path ASC
                  ) AS rank_in_session,
                  COUNT(*) OVER (PARTITION BY cli_session_id) AS record_count
                  FROM records
                  WHERE cli_session_id IS NOT NULL AND unreadable = 0
                )
                WHERE rank_in_session = 1
                """
            )
            accounts = {
                str(row["account"]): int(row["n"])
                for row in connection.execute(
                    "SELECT account, COUNT(*) AS n FROM records GROUP BY account"
                )
                if row["account"]
            }
            totals = connection.execute(
                "SELECT COUNT(*) AS files, "
                "COALESCE(SUM(unreadable), 0) AS unreadable FROM records"
            ).fetchone()
            conversations = connection.execute(
                "SELECT COUNT(*) AS n FROM sessions"
            ).fetchone()
            for key, value in (
                ("accounts", json.dumps(accounts, sort_keys=True)),
                ("files", int(totals["files"] or 0)),
                ("unreadable", int(totals["unreadable"] or 0)),
                ("conversations", int(conversations["n"] or 0)),
            ):
                connection.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (key, str(value)),
                )
            connection.execute("COMMIT")
        return {
            "files": int(totals["files"] or 0),
            "unreadable": int(totals["unreadable"] or 0),
            "conversations": int(conversations["n"] or 0),
        }

    # ── writes ──────────────────────────────────────────────────────────
    def upsert(self, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        placeholders = ", ".join(f":{name}" for name in _RECORD_COLUMNS)
        with self.connect(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                f"INSERT OR REPLACE INTO records ({', '.join(_RECORD_COLUMNS)}) "
                f"VALUES ({placeholders})",
                rows,
            )
            connection.execute("COMMIT")

    def prune(self, alive: Iterable[str]) -> int:
        """Delete rows for record files that no longer exist. Returns the count."""
        alive_set = set(alive)
        with self.connect(write=True) as connection:
            stored = [
                str(row["path"])
                for row in connection.execute("SELECT path FROM records").fetchall()
            ]
            gone = [path for path in stored if path not in alive_set]
            if gone:
                connection.execute("BEGIN IMMEDIATE")
                for start in range(0, len(gone), 500):
                    batch = gone[start : start + 500]
                    connection.execute(
                        f"DELETE FROM records WHERE path IN ({','.join('?' * len(batch))})",
                        batch,
                    )
                connection.execute("COMMIT")
        return len(gone)

    # ── transcripts ─────────────────────────────────────────────────────
    #
    # The screen also shows each conversation's transcript size, and lists
    # transcripts Claude never indexed (CLI-started sessions: 80 of them and
    # 113 MB on 2026-09-11) with a title read from the transcript itself. Both
    # were done inside the request — the walk on every call, and ~80 bounded
    # transcript reads with it. They live here for the same reason the records
    # do: the request reads rows, the refresh reads disk.

    def _load_transcripts(
        self, connection: sqlite3.Connection
    ) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[str | None, str | None]]]:
        try:
            rows = connection.execute(
                "SELECT session_id, size, mtime_ns, orphan, title, project FROM transcripts"
            ).fetchall()
        except sqlite3.DatabaseError:
            return {}, {}
        found: dict[str, tuple[int, int]] = {}
        orphans: dict[str, tuple[str | None, str | None]] = {}
        for row in rows:
            session_id = str(row["session_id"])
            found[session_id] = (int(row["size"] or 0), int(row["mtime_ns"] or 0))
            if int(row["orphan"] or 0):
                orphans[session_id] = (row["title"], row["project"])
        return found, orphans

    def known_transcripts(self) -> dict[str, tuple[int, int, int, int | None]]:
        """session id -> (mtime_ns, size, orphan, summary_mtime_ns)."""
        if not self.path.exists():
            return {}
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT session_id, mtime_ns, size, orphan, summary_mtime_ns "
                    "FROM transcripts"
                ).fetchall()
        except sqlite3.DatabaseError:
            return {}
        return {
            str(row["session_id"]): (
                int(row["mtime_ns"] or 0),
                int(row["size"] or 0),
                int(row["orphan"] or 0),
                None if row["summary_mtime_ns"] is None else int(row["summary_mtime_ns"]),
            )
            for row in rows
        }

    def sidebar_session_ids(self) -> set[str]:
        """Every conversation id Claude's sidebar knows about. One query."""
        if not self.path.exists():
            return set()
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT DISTINCT cli_session_id FROM records "
                    "WHERE cli_session_id IS NOT NULL AND unreadable = 0"
                ).fetchall()
        except sqlite3.DatabaseError:
            return set()
        return {str(row["cli_session_id"]) for row in rows}

    def orphan_summary(self, session_id: str) -> tuple[str | None, str | None]:
        """The stored (title, project) for one transcript, if any."""
        if not self.path.exists():
            return None, None
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT title, project FROM transcripts WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None, None
        return (row["title"], row["project"]) if row else (None, None)

    def upsert_transcripts(self, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = (
            "session_id",
            "size",
            "mtime_ns",
            "orphan",
            "title",
            "project",
            "summary_mtime_ns",
        )
        with self.connect(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                f"INSERT OR REPLACE INTO transcripts ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)})",
                rows,
            )
            connection.execute("COMMIT")

    def prune_transcripts(self, alive: Iterable[str]) -> int:
        alive_set = set(alive)
        with self.connect(write=True) as connection:
            stored = [
                str(row["session_id"])
                for row in connection.execute("SELECT session_id FROM transcripts")
            ]
            gone = [value for value in stored if value not in alive_set]
            if gone:
                connection.execute("BEGIN IMMEDIATE")
                for start in range(0, len(gone), 500):
                    batch = gone[start : start + 500]
                    connection.execute(
                        f"DELETE FROM transcripts WHERE session_id IN "
                        f"({','.join('?' * len(batch))})",
                        batch,
                    )
                connection.execute("COMMIT")
        return len(gone)

    def write_meta(self, values: dict[str, Any]) -> None:
        with self.connect(write=True) as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                [(key, "" if value is None else str(value)) for key, value in values.items()],
            )

    def bump_revision(self) -> int:
        with self.connect(write=True) as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'revision'"
            ).fetchone()
            current = int(row["value"]) if row and str(row["value"]).isdigit() else 0
            nxt = current + 1
            connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('revision', ?)",
                (str(nxt),),
            )
        return nxt


# ── the walk and the read ───────────────────────────────────────────────────


def walk_records(
    root: Path, *, limit: int = MAX_INDEX_FILES
) -> tuple[list[tuple[str, int, int]], bool]:
    """Stat-only walk: [(path, mtime_ns, size)], and whether the cap was hit.

    Stat releases the GIL, so this is safe in a worker thread — it is the one
    part that must still touch the filesystem on every refresh (its whole job
    is deciding what changed). Measured 3.58 s for 67,224 files.
    """
    found: list[tuple[str, int, int]] = []
    truncated = False
    if not root.exists() or not root.is_dir():
        return found, truncated
    for path in root.rglob("local_*.json"):
        if len(found) >= limit:
            truncated = True
            break
        try:
            info = path.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        found.append((str(path), int(info.st_mtime_ns), int(info.st_size)))
    return found, truncated


def _account_of(path: str) -> str | None:
    parts = Path(path).parts
    return parts[-3] if len(parts) >= 3 else None


def read_record_rows(
    stamps: Sequence[tuple[str, int, int]]
) -> list[dict[str, Any]]:
    """Parse a chunk of record files into storable rows. Never raises."""
    rows: list[dict[str, Any]] = []
    for path, mtime_ns, size in stamps:
        row: dict[str, Any] = {
            "path": path,
            "mtime_ns": mtime_ns,
            "size": size,
            "account": _account_of(path),
            "cli_session_id": None,
            "last_activity_at": 0,
            "title": None,
            "title_source": None,
            "workspace_name": None,
            "git_branch": None,
            "worktree_name": None,
            "is_archived": None,
            "local_cwd": None,
            "unreadable": 0,
        }
        if size > MAX_INDEX_FILE_BYTES:
            row["unreadable"] = 1
            rows.append(row)
            continue
        try:
            record = json.loads(Path(path).read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            row["unreadable"] = 1
            rows.append(row)
            continue
        if not isinstance(record, dict):
            row["unreadable"] = 1
            rows.append(row)
            continue
        entry = entry_from_record(record)
        if entry is None:
            # Readable, but no cliSessionId: it names no conversation. Stored
            # so it is not re-read every pass, and not counted as unreadable.
            rows.append(row)
            continue
        row.update(
            {
                "cli_session_id": entry.cli_session_id,
                "last_activity_at": entry.last_activity_at,
                "title": entry.title,
                "title_source": entry.title_source,
                "workspace_name": entry.workspace_name,
                "git_branch": entry.git_branch,
                "worktree_name": entry.worktree_name,
                "is_archived": None if entry.is_archived is None else int(entry.is_archived),
                "local_cwd": str(entry.local_cwd) if entry.local_cwd else None,
            }
        )
        rows.append(row)
    return rows


def _chunks(
    values: Sequence[tuple[str, int, int]], size: int
) -> Iterator[Sequence[tuple[str, int, int]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def plan_refresh(
    root: Path, store: ClaudeIndexStore, *, limit: int = MAX_INDEX_FILES
) -> tuple[list[tuple[str, int, int]], list[str], bool, int]:
    """(files to re-read, every live path, cap hit, total files seen)."""
    store.ensure_ready()
    known = store.known_stamps()
    stamps, truncated = walk_records(root, limit=limit)
    changed = [
        stamp
        for stamp in stamps
        if known.get(stamp[0]) != (stamp[1], stamp[2])
    ]
    return changed, [stamp[0] for stamp in stamps], truncated, len(stamps)


def refresh_store_sync(
    root: Path,
    store: ClaudeIndexStore,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    limit: int = MAX_INDEX_FILES,
) -> dict[str, Any]:
    """One complete incremental refresh, synchronously (helper process path)."""
    started = time.monotonic()
    changed, alive, truncated, total = plan_refresh(root, store, limit=limit)
    for chunk in _chunks(changed, chunk_size):
        store.upsert(read_record_rows(chunk))
    removed = store.prune(alive)
    return finalize_refresh(
        store,
        changed=len(changed),
        removed=removed,
        truncated=truncated,
        duration=time.monotonic() - started,
    )


def finalize_refresh(
    store: ClaudeIndexStore,
    *,
    changed: int,
    removed: int,
    truncated: bool,
    duration: float,
) -> dict[str, Any]:
    """Close one refresh: materialize the winners, stamp meta, bump revision.

    Both refresh paths (helper process and in-engine chunks) end here, so the
    engine can never serve a snapshot whose winners were computed differently
    from the ones the helper would have written.
    """
    counts = store.rebuild_sessions()
    store.write_meta(
        {
            "updated_at": _now_iso(),
            "complete": "1",
            "truncated": "1" if truncated else "0",
            "last_changed_files": changed,
            "last_removed_files": removed,
            "last_duration_seconds": round(duration, 3),
        }
    )
    revision = store.bump_revision()
    return {
        "files": counts["files"],
        "conversations": counts["conversations"],
        "unreadable": counts["unreadable"],
        "changed": changed,
        "removed": removed,
        "truncated": truncated,
        "duration_seconds": round(duration, 3),
        "revision": revision,
    }


def default_transcripts_root() -> Path:
    """Where Claude Code keeps the transcripts themselves."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / ".claude"
    return base / "projects"


def walk_transcripts(root: Path) -> dict[str, tuple[int, int, Path]]:
    """session id -> (size, mtime_ns, path) for every transcript on disk."""
    found: dict[str, tuple[int, int, Path]] = {}
    if not root.is_dir():
        return found
    for path in root.glob("*/*.jsonl"):
        try:
            info = path.stat()
        except OSError:
            continue
        found[path.stem] = (int(info.st_size), int(info.st_mtime_ns), path)
    return found


def refresh_transcripts_sync(
    store: ClaudeIndexStore,
    *,
    sidebar_ids: set[str],
    root: Path | None = None,
    read_summary: Any = None,
) -> dict[str, Any]:
    """Persist transcript sizes, and titles for the ones Claude never indexed.

    ``read_summary(root, path, session_id) -> (title, project, branch)`` is the
    importer's own bounded reader, injected so this module stays importable in
    the lightweight helper process. It is called ONLY for an orphan whose
    transcript stamp moved since the last refresh — ~80 files once, then none.
    """
    started = time.monotonic()
    transcripts_root = root or default_transcripts_root()
    store.ensure_ready()
    known = store.known_transcripts()
    found = walk_transcripts(transcripts_root)
    rows: list[dict[str, Any]] = []
    summaries_read = 0
    for session_id, (size, mtime_ns, path) in found.items():
        orphan = session_id not in sidebar_ids
        previous = known.get(session_id)
        summary_mtime = previous[3] if previous is not None else None
        stamp_moved = previous is None or previous[0] != mtime_ns or previous[1] != size
        orphan_changed = previous is not None and bool(previous[2]) != orphan
        needs_summary = orphan and (stamp_moved or orphan_changed or summary_mtime is None)
        if not stamp_moved and not orphan_changed and not needs_summary:
            continue
        title: str | None = None
        project: str | None = None
        if orphan:
            title, project = store.orphan_summary(session_id)
            if needs_summary and read_summary is not None:
                try:
                    title, project, _branch = read_summary(
                        transcripts_root, path, session_id
                    )
                    summary_mtime = mtime_ns
                    summaries_read += 1
                except Exception:  # noqa: BLE001 — an unreadable one still lists
                    pass
        else:
            summary_mtime = None
        rows.append(
            {
                "session_id": session_id,
                "size": size,
                "mtime_ns": mtime_ns,
                "orphan": 1 if orphan else 0,
                "title": title,
                "project": project,
                "summary_mtime_ns": summary_mtime,
            }
        )
    store.upsert_transcripts(rows)
    removed = store.prune_transcripts(found.keys())
    duration = time.monotonic() - started
    store.write_meta(
        {
            "transcripts_updated_at": _now_iso(),
            "transcripts": len(found),
            "transcript_summaries_read": summaries_read,
            "transcripts_duration_seconds": round(duration, 3),
        }
    )
    return {
        "transcripts": len(found),
        "changed": len(rows),
        "summaries_read": summaries_read,
        "removed": removed,
        "duration_seconds": round(duration, 3),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "finalize_refresh",
    "default_transcripts_root",
    "refresh_transcripts_sync",
    "walk_transcripts",
    "SCHEMA_VERSION",
    "ClaudeIndexStore",
    "IndexSnapshot",
    "default_store_path",
    "plan_refresh",
    "read_record_rows",
    "refresh_store_sync",
    "walk_records",
]
