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
from uuid import UUID

from app.services.coding_sessions.claude_session_index import (
    MAX_INDEX_FILE_BYTES,
    MAX_INDEX_FILES,
    ClaudeSessionIndexEntry,
    LivePins,
    entry_from_record,
    merge_entries,
    record_focused_at,
    record_is_starred,
)
from app.services.coding_sessions.claude_scope import (
    app_support_for,
    decide_scope,
    default_sessions_root,
    org_focus_for,
    signed_in_account,
    stated_org_stamps,
)
from app.services.coding_sessions.claude_usage import (
    PENDING_STAMP,
    USAGE_FIELDS,
    UsageCursor,
    UsageIncrement,
    read_usage_increment,
)

# 2: is_starred (pin truth).
# 3: the usage tables are keyed by TRANSCRIPT, not by session (2026-09-18
#    CS-33/F5). A session's spend is spread over many files — its own turn
#    stream plus one per sub-agent — so the cursor, the counted-key set and
#    the usage cells all carry the transcript they came from, and each row
#    also carries the parent ``session_id`` and its ``lane`` (main /
#    subagent). The old per-session primary keys cannot hold that, so this is
#    a version bump: ``ensure_ready`` rebuilds, and the next refresh re-reads
#    the tree it had already read.
SCHEMA_VERSION = 3

# Transcript bytes one refresh may read for usage before handing the rest to
# the next refresh. 10 GB of transcripts on this Mac (2026-09-17) become
# complete over ~14 refreshes at the 20 s cadence, in a worker thread, while
# the screen already shows every session read so far and says how many are
# still pending.
DEFAULT_USAGE_BYTE_BUDGET = 768 * 1024 * 1024

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
    # The account+org scope the pins were read from, and — always — why, in
    # English. ``active_scope`` is None when the rule refused to guess, and
    # the reason then says what the app failed to state. See
    # :mod:`app.services.coding_sessions.claude_scope`.
    active_scope: str | None = None
    active_scope_reason: str | None = None


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
    # The app's own pin field, and the signal that identifies which
    # account+org scope the app is signed into. Both are per RECORD FILE:
    # pins are per scope, so the winning record alone cannot answer them.
    "is_starred",
    "lastrecord_focused_at",
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
                is_starred INTEGER,
                lastrecord_focused_at INTEGER NOT NULL DEFAULT 0,
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
                local_cwd TEXT,
                -- The pin, resolved at refresh time from the record in the
                -- scope the app is SIGNED INTO — not from the winning record,
                -- which is whichever account touched the conversation last.
                -- ``in_active_scope`` separates "the app says not pinned"
                -- from "the app has never seen this conversation": only the
                -- first is an unpin the server should be told about.
                in_active_scope INTEGER NOT NULL DEFAULT 0,
                active_is_starred INTEGER
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
            -- Per-turn usage, deduplicated per message, one row per
            -- (transcript, UTC hour, model), carrying the parent session and
            -- the lane it was spent in. Read by GET /coding-session/usage,
            -- which sums the transcripts of a session back together and
            -- keeps the main/sub-agent split as its own column.
            CREATE TABLE IF NOT EXISTS transcript_usage (
                transcript_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                lane TEXT NOT NULL,
                hour TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                requests INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (transcript_id, hour, model)
            );
            CREATE INDEX IF NOT EXISTS transcript_usage_hour
                ON transcript_usage (hour);
            CREATE INDEX IF NOT EXISTS transcript_usage_session
                ON transcript_usage (session_id);
            -- Where the usage reader stopped in each transcript: a stamp that
            -- differs from the walk's means the tail is still to be read. One
            -- row per FILE — a session has one per sub-agent as well as its
            -- own, and they grow independently.
            CREATE TABLE IF NOT EXISTS transcript_usage_cursor (
                transcript_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                offset INTEGER NOT NULL DEFAULT 0,
                size INTEGER NOT NULL DEFAULT -1,
                mtime_ns INTEGER NOT NULL DEFAULT -1
            );
            CREATE INDEX IF NOT EXISTS transcript_usage_cursor_session
                ON transcript_usage_cursor (session_id);
            -- Every (message id, request id) counted, against the transcript
            -- it was read from AND the session that spent it. The dedupe is
            -- bounded by the SESSION — every key of every transcript under it
            -- is handed to the reader — never by a tail of recent keys:
            -- Claude re-writes an earlier message hundreds of messages later
            -- (205 such messages in one real 52.6 MB transcript, 2026-09-18)
            -- and a windowed dedupe counted every one of them twice. The
            -- transcript half of the key is what lets ONE rewritten file
            -- start over without erasing its siblings' contribution.
            CREATE TABLE IF NOT EXISTS transcript_usage_key (
                transcript_id TEXT NOT NULL,
                key TEXT NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (transcript_id, key)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS transcript_usage_key_session
                ON transcript_usage_key (session_id);
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
                "DROP TABLE IF EXISTS transcript_usage;"
                "DROP TABLE IF EXISTS transcript_usage_cursor;"
                "DROP TABLE IF EXISTS transcript_usage_key;"
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
                    "git_branch, worktree_name, is_archived, local_cwd, "
                    "in_active_scope, active_is_starred FROM sessions"
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
        # The pin observation, rebuilt from the scope-resolved columns so the
        # store and the full scan apply ONE rule (LivePins) to one input.
        observed: dict[str, bool | None] = {}
        for row in rows:
            session_id = str(row["cli_session_id"])
            record_counts[session_id] = int(row["record_count"] or 1)
            if int(row["in_active_scope"] or 0):
                starred = row["active_is_starred"]
                observed[session_id] = None if starred is None else bool(starred)
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
        entries = merge_entries(
            candidates, ledger=ledger, live_pins=LivePins(observed)
        )
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
            active_scope=meta.get("active_scope") or None,
            active_scope_reason=meta.get("active_scope_reason") or None,
        )

    def rebuild_sessions(
        self, *, sessions_root: Path | None = None
    ) -> dict[str, Any]:
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
                    git_branch, worktree_name, is_archived, local_cwd,
                    in_active_scope, active_is_starred
                )
                SELECT cli_session_id, path, mtime_ns, record_count,
                       last_activity_at, title, title_source, workspace_name,
                       git_branch, worktree_name, is_archived, local_cwd,
                       0, NULL
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
            # Resolve the pin from the scope the app is signed into. The
            # winning record above is the one touched most recently by ANY
            # account; the pin belongs to one specific scope, so it is a
            # second pass — and the ACCOUNT half of that scope comes from the
            # app's own statement of who is signed in, never from a timestamp.
            #
            # This used to be one ``ORDER BY lastrecord_focused_at DESC LIMIT
            # 1`` over every record on the machine, which is how a stamp
            # copied into a scope the app is NOT signed into became the
            # engine's pin truth (2026-09-17: one stamp was the maximum in
            # nine scopes across five accounts). THE rule now lives once, in
            # :mod:`app.services.coding_sessions.claude_scope`; this path only
            # supplies the per-organisation stamps it already stores, so it
            # never re-walks the tree to answer the question.
            # WHICH organisations exist is NOT this reader's question to
            # answer: it comes from ``claude_scope.account_org_dirs`` through
            # ``org_focus_for``, the same listing the extractor uses. This
            # path used to build the set from ``WHERE lastrecord_focused_at >
            # 0``, so an org the app NAMES whose records carry no focus stamp
            # was invisible here, ``decide_scope`` ignored the app's statement
            # about it, and the engine fell back to focus ranking while the
            # extractor honoured the statement — one machine, two sidebars
            # (CS-33/R2, 2026-09-18). All this reader supplies now is the
            # per-org stamp it already stores, so it still never re-walks the
            # tree to answer the question.
            root = sessions_root or default_sessions_root()
            app_support = app_support_for(root)
            account, signals, account_reason = signed_in_account(app_support)
            org_focus: dict[str, int] = {}
            account_dir: Path | None = None
            if account is not None:
                account_dir = root / account
                stamps: dict[str, int] = {}
                for row in connection.execute(
                    "SELECT path, lastrecord_focused_at AS focus FROM records "
                    "WHERE account = ? AND unreadable = 0",
                    (account,),
                ):
                    org_name = Path(str(row["path"])).parent.name
                    focus = int(row["focus"] or 0)
                    if focus > stamps.get(org_name, 0):
                        stamps[org_name] = focus
                org_focus = org_focus_for(account_dir, stamps)
            resolution = decide_scope(
                account,
                signals,
                account_reason,
                org_focus,
                org_stated=stated_org_stamps(app_support),
                account_dir=account_dir,
            )
            active_scope = str(resolution.scope) if resolution.scope else None
            if active_scope:
                connection.execute(
                    """
                    UPDATE sessions SET
                        in_active_scope = 1,
                        active_is_starred = (
                            SELECT r.is_starred FROM records r
                            WHERE r.cli_session_id = sessions.cli_session_id
                              AND r.unreadable = 0
                              AND r.path LIKE ? || '/%'
                            ORDER BY r.path ASC LIMIT 1
                        )
                    WHERE EXISTS (
                        SELECT 1 FROM records r
                        WHERE r.cli_session_id = sessions.cli_session_id
                          AND r.unreadable = 0
                          AND r.path LIKE ? || '/%'
                    )
                    """,
                    (active_scope, active_scope),
                )
            connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES "
                "('active_scope', ?)",
                (active_scope or "",),
            )
            # Nothing fails silently: when the scope is UNKNOWN the pins stay
            # exactly as they were, and the reason is stored in English so the
            # screen and a diagnosis can say why rather than show a number.
            connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES "
                "('active_scope_reason', ?)",
                (resolution.reason,),
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

    # ── usage ───────────────────────────────────────────────────────────
    #
    # Token usage per (session, UTC hour, model), read from the transcripts
    # the walk above already stats. The reader keeps a byte cursor per
    # transcript, so after the first build a refresh reads only the tails
    # that grew — see claude_usage.py for the record shape and the dedupe.

    def usage_cursors(self) -> dict[str, UsageCursor]:
        """transcript id -> where the reader stopped in that ONE file."""
        if not self.path.exists():
            return {}
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT transcript_id, offset, size, mtime_ns "
                    "FROM transcript_usage_cursor"
                ).fetchall()
        except sqlite3.DatabaseError:
            return {}
        cursors: dict[str, UsageCursor] = {}
        for row in rows:
            cursors[str(row["transcript_id"])] = UsageCursor(
                offset=int(row["offset"] or 0),
                size=int(row["size"] if row["size"] is not None else PENDING_STAMP),
                mtime_ns=int(row["mtime_ns"] if row["mtime_ns"] is not None else PENDING_STAMP),
            )
        return cursors

    def usage_keys(self, session_id: str) -> set[str]:
        """Every message key this ONE session has already been counted for.

        Across every transcript under it — its own turn stream and each
        sub-agent's — because a sub-agent turn is the session's spend and must
        be counted once for the session, not once per file. One session at a
        time by design: the whole tree's keys are never in memory together.
        """
        if not self.path.exists():
            return set()
        try:
            with self.connect() as connection:
                return {
                    str(row["key"])
                    for row in connection.execute(
                        "SELECT key FROM transcript_usage_key WHERE session_id = ?",
                        (session_id,),
                    )
                }
        except sqlite3.DatabaseError:
            return set()

    def apply_usage_increments(
        self, increments: Sequence[tuple["TranscriptFile", UsageIncrement]]
    ) -> None:
        """Add each increment's cells to the store, in one transaction.

        Each increment belongs to ONE transcript, so a rewritten file replaces
        only its own rows and keys — the sibling sub-agent streams of the same
        session keep theirs.
        """
        if not increments:
            return
        with self.connect(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for entry, increment in increments:
                transcript_id = entry.transcript_id
                if increment.restarted:
                    connection.execute(
                        "DELETE FROM transcript_usage WHERE transcript_id = ?",
                        (transcript_id,),
                    )
                    connection.execute(
                        "DELETE FROM transcript_usage_key WHERE transcript_id = ?",
                        (transcript_id,),
                    )
                connection.executemany(
                    "INSERT OR IGNORE INTO transcript_usage_key "
                    "(transcript_id, key, session_id) VALUES (?, ?, ?)",
                    [(transcript_id, key, entry.session_id) for key in increment.new_keys],
                )
                for (hour, model), cell in increment.cells.items():
                    connection.execute(
                        """
                        INSERT INTO transcript_usage (
                            transcript_id, session_id, lane, hour, model,
                            input_tokens, output_tokens,
                            cache_creation_tokens, cache_read_tokens, requests
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (transcript_id, hour, model) DO UPDATE SET
                            input_tokens = input_tokens + excluded.input_tokens,
                            output_tokens = output_tokens + excluded.output_tokens,
                            cache_creation_tokens =
                                cache_creation_tokens + excluded.cache_creation_tokens,
                            cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens,
                            requests = requests + excluded.requests
                        """,
                        (
                            transcript_id,
                            entry.session_id,
                            entry.lane,
                            hour,
                            model,
                            int(cell["input_tokens"]),
                            int(cell["output_tokens"]),
                            int(cell["cache_creation_tokens"]),
                            int(cell["cache_read_tokens"]),
                            int(cell["requests"]),
                        ),
                    )
                cursor = increment.cursor
                connection.execute(
                    "INSERT OR REPLACE INTO transcript_usage_cursor "
                    "(transcript_id, session_id, offset, size, mtime_ns) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        transcript_id,
                        entry.session_id,
                        cursor.offset,
                        cursor.size,
                        cursor.mtime_ns,
                    ),
                )
            connection.execute("COMMIT")

    def prune_usage(self, alive: Iterable[str]) -> int:
        """Drop the usage of every transcript id no longer on disk."""
        alive_set = set(alive)
        with self.connect(write=True) as connection:
            stored = [
                str(row["transcript_id"])
                for row in connection.execute(
                    "SELECT transcript_id FROM transcript_usage_cursor"
                )
            ]
            gone = [value for value in stored if value not in alive_set]
            if gone:
                connection.execute("BEGIN IMMEDIATE")
                for start in range(0, len(gone), 500):
                    batch = gone[start : start + 500]
                    marks = ",".join("?" * len(batch))
                    connection.execute(
                        f"DELETE FROM transcript_usage WHERE transcript_id IN ({marks})",
                        batch,
                    )
                    connection.execute(
                        f"DELETE FROM transcript_usage_cursor WHERE transcript_id IN ({marks})",
                        batch,
                    )
                    connection.execute(
                        f"DELETE FROM transcript_usage_key WHERE transcript_id IN ({marks})",
                        batch,
                    )
                connection.execute("COMMIT")
        return len(gone)

    def usage_rows(self, start_hour: str, end_hour: str) -> list[dict[str, Any]]:
        """Every usage cell with ``start_hour <= hour < end_hour``, labelled.

        Hour keys are ``YYYY-MM-DDTHH`` (UTC), so the comparison is textual.
        The many transcripts of one session are summed back together HERE, by
        (session, lane, hour, model): the parent session owns the spend of
        every sub-agent it ran, and ``lane`` keeps that share visible instead
        of silently folded in. The session's title and project come from the
        same rows the Sessions tab shows, so the two tabs never name one
        conversation two ways.
        """
        if not self.path.exists():
            return []
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT u.session_id, u.lane, u.hour, u.model,
                           SUM(u.input_tokens) AS input_tokens,
                           SUM(u.output_tokens) AS output_tokens,
                           SUM(u.cache_creation_tokens) AS cache_creation_tokens,
                           SUM(u.cache_read_tokens) AS cache_read_tokens,
                           SUM(u.requests) AS requests,
                           COALESCE(s.title, t.title) AS title,
                           COALESCE(s.workspace_name, t.project) AS project
                    FROM transcript_usage u
                    LEFT JOIN sessions s ON s.cli_session_id = u.session_id
                    LEFT JOIN transcripts t ON t.session_id = u.session_id
                    WHERE u.hour >= ? AND u.hour < ?
                    GROUP BY u.session_id, u.lane, u.hour, u.model
                    """,
                    (start_hour, end_hour),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [
            {
                "session_id": str(row["session_id"]),
                "lane": str(row["lane"]),
                "hour": str(row["hour"]),
                "model": str(row["model"]),
                "input_tokens": int(row["input_tokens"] or 0),
                "output_tokens": int(row["output_tokens"] or 0),
                "cache_creation_tokens": int(row["cache_creation_tokens"] or 0),
                "cache_read_tokens": int(row["cache_read_tokens"] or 0),
                "requests": int(row["requests"] or 0),
                "title": row["title"],
                "project": row["project"],
            }
            for row in rows
        ]

    def usage_status(self) -> dict[str, Any]:
        """How far the usage read has got, from meta. No walk."""
        if not self.path.exists():
            return {"built": False, "pending_sessions": None, "updated_at": None}
        try:
            with self.connect() as connection:
                meta = self._meta(connection)
        except sqlite3.DatabaseError:
            return {"built": False, "pending_sessions": None, "updated_at": None}
        pending = meta.get("usage_pending_sessions", "")
        read = meta.get("usage_sessions_read", "")
        return {
            "built": meta.get("usage_updated_at") is not None,
            "pending_sessions": int(pending) if pending.isdigit() else None,
            "sessions_read": int(read) if read.isdigit() else None,
            "updated_at": meta.get("usage_updated_at"),
            "unreadable": meta.get("usage_unreadable", ""),
        }

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
            "is_starred": None,
            "lastrecord_focused_at": 0,
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
                "is_starred": (
                    None
                    if record_is_starred(record) is None
                    else int(record_is_starred(record))
                ),
                "lastrecord_focused_at": record_focused_at(record),
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
        sessions_root=root,
        changed=len(changed),
        removed=removed,
        truncated=truncated,
        duration=time.monotonic() - started,
    )


def finalize_refresh(
    store: ClaudeIndexStore,
    *,
    sessions_root: Path | None = None,
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
    counts = store.rebuild_sessions(sessions_root=sessions_root)
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


MAIN_LANE = "main"
SUBAGENT_LANE = "subagent"


@dataclass(frozen=True)
class TranscriptFile:
    """ONE transcript file on disk, and the session whose spend it is.

    Claude Code writes a session's own turns to
    ``<project>/<session>.jsonl`` and every sub-agent it runs to
    ``<project>/<session>/subagents/[workflows/<wf>/]agent-<id>.jsonl``. Both
    are the session's spend ("sub-agent turns ARE the session's spend" —
    Arman, 2026-09-18), so both are walked, both are attributed to the parent
    session, and ``lane`` keeps the share visible.
    """

    # Stable identity of the FILE: its path relative to the projects root.
    transcript_id: str
    # The PARENT session — the file's own name for a main transcript, the
    # directory Claude nested it under for a sub-agent stream. Claude writes
    # the same id into every record's ``sessionId`` field.
    session_id: str
    lane: str
    size: int
    mtime_ns: int
    path: Path

    @property
    def kind(self) -> str:
        return self.lane


def _session_dir_id(name: str) -> str | None:
    """``name`` if it is a session UUID, else None.

    A project directory also holds plugin state Claude keeps beside the
    transcripts (``vercel-plugin/skill-injections.jsonl``, ``memory/``) —
    those are not turn streams and are not a session's spend.
    """
    try:
        UUID(name)
    except (ValueError, AttributeError, TypeError):
        return None
    return name


def walk_transcripts(root: Path) -> dict[str, TranscriptFile]:
    """transcript id -> :class:`TranscriptFile` for EVERY transcript on disk.

    Every depth, not one level: the nested sub-agent streams are 6,447 of this
    Mac's 8,147 transcripts (2026-09-18), and a ``*/*.jsonl`` glob reached
    none of them — which is why the Usage screen reported roughly a quarter of
    the real spend.
    """
    found: dict[str, TranscriptFile] = {}
    if not root.is_dir():
        return found

    def add(path: Path, session_id: str, lane: str) -> None:
        try:
            info = path.stat()
        except OSError:
            return
        transcript_id = path.relative_to(root).as_posix()
        found[transcript_id] = TranscriptFile(
            transcript_id=transcript_id,
            session_id=session_id,
            lane=lane,
            size=int(info.st_size),
            mtime_ns=int(info.st_mtime_ns),
            path=path,
        )

    try:
        projects = sorted(root.iterdir())
    except OSError:
        return found
    for project in projects:
        if not project.is_dir():
            continue
        try:
            children = sorted(project.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_file() and child.suffix == ".jsonl":
                session_id = _session_dir_id(child.stem)
                if session_id is not None:
                    add(child, session_id, MAIN_LANE)
                continue
            if not child.is_dir():
                continue
            session_id = _session_dir_id(child.name)
            if session_id is None:
                continue
            # Any depth under the session directory: Claude nests a workflow's
            # sub-agents one level deeper again
            # (``subagents/workflows/wf_…/agent-….jsonl``).
            for nested in sorted(child.rglob("*.jsonl")):
                if nested.is_file():
                    add(nested, session_id, SUBAGENT_LANE)
    return found


def refresh_transcripts_sync(
    store: ClaudeIndexStore,
    *,
    sidebar_ids: set[str],
    root: Path | None = None,
    read_summary: Any = None,
    usage_byte_budget: int = DEFAULT_USAGE_BYTE_BUDGET,
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
    # The transcripts table is the SIDEBAR's half: one row per session, sized
    # and titled from the session's own turn stream. The usage half below
    # reads every file, sub-agent streams included.
    mains = {
        entry.session_id: entry for entry in found.values() if entry.lane == MAIN_LANE
    }
    rows: list[dict[str, Any]] = []
    summaries_read = 0
    for session_id, entry in mains.items():
        size, mtime_ns, path = entry.size, entry.mtime_ns, entry.path
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
    removed = store.prune_transcripts(mains.keys())
    usage = refresh_usage_sync(store, found, byte_budget=usage_byte_budget)
    duration = time.monotonic() - started
    store.write_meta(
        {
            "transcripts_updated_at": _now_iso(),
            "transcripts": len(mains),
            "transcript_files": len(found),
            "transcript_summaries_read": summaries_read,
            "transcripts_duration_seconds": round(duration, 3),
        }
    )
    return {
        "transcripts": len(mains),
        "transcript_files": len(found),
        "changed": len(rows),
        "summaries_read": summaries_read,
        "removed": removed,
        "duration_seconds": round(duration, 3),
        "usage": usage,
    }


def refresh_usage_sync(
    store: ClaudeIndexStore,
    found: dict[str, TranscriptFile],
    *,
    byte_budget: int = DEFAULT_USAGE_BYTE_BUDGET,
) -> dict[str, Any]:
    """The usage half of a transcript refresh: read the tails that grew.

    ``found`` is the walk :func:`refresh_transcripts_sync` already did — this
    never stats the tree again. A transcript is pending when its cursor stamp
    is not the walk's stamp; pending ones are read oldest-first within
    ``byte_budget`` and the rest wait for the next refresh, which the meta
    counters say plainly.

    One entry per FILE, so a session with 432 sub-agent streams is 433 units
    of work here. The ``usage_pending_sessions`` / ``usage_sessions_read``
    meta keys keep their names (they feed ``UsageSource.pending_sessions``,
    which every provider shares) but count TRANSCRIPTS — the report's note
    says "transcript(s)" for exactly that reason.
    """
    started = time.monotonic()
    cursors = store.usage_cursors()
    pending = [
        entry
        for transcript_id, entry in found.items()
        if (cursor := cursors.get(transcript_id)) is None
        or (cursor.size, cursor.mtime_ns) != (entry.size, entry.mtime_ns)
    ]
    # Oldest transcripts first: the first ever build then lands history in
    # order, and the sessions a person is working in right now are small
    # tails that fit any refresh.
    pending.sort(key=lambda entry: entry.mtime_ns)
    increments: list[tuple[TranscriptFile, UsageIncrement]] = []
    bytes_read = 0
    unreadable = 0
    read_sessions = 0
    exhausted = False
    # The counted keys of each session touched in THIS pass, loaded once and
    # extended as its transcripts are read: a session's sub-agent streams are
    # deduplicated in the PARENT's key space, so the same turn can never be
    # counted twice because it appeared in two files.
    session_keys: dict[str, set[str]] = {}

    def keys_for(session_id: str) -> set[str]:
        known = session_keys.get(session_id)
        if known is None:
            known = store.usage_keys(session_id)
            session_keys[session_id] = known
        return known

    for entry in pending:
        remaining = byte_budget - bytes_read
        if remaining <= 0:
            exhausted = True
            break
        try:
            cursor = cursors.get(entry.transcript_id)
            increment = read_usage_increment(
                entry.path,
                cursor,
                size=entry.size,
                mtime_ns=entry.mtime_ns,
                byte_budget=remaining,
                seen_keys=keys_for(entry.session_id),
            )
        except OSError:
            unreadable += 1
            continue
        bytes_read += increment.bytes_read
        increments.append((entry, increment))
        if increment.restarted:
            # This file's keys are REPLACED, so what the session still holds
            # is whatever its other transcripts contributed. Land the deletion
            # now, then forget the cached set: the next file of this session
            # re-reads the truth from the store.
            store.apply_usage_increments(increments)
            increments = []
            session_keys.pop(entry.session_id, None)
        else:
            keys_for(entry.session_id).update(increment.new_keys)
        if increment.truncated:
            exhausted = True
            break
        read_sessions += 1
        if len(increments) >= 64:
            store.apply_usage_increments(increments)
            increments = []
    store.apply_usage_increments(increments)
    removed = store.prune_usage(found.keys())
    still_pending = len(pending) - read_sessions
    duration = time.monotonic() - started
    store.write_meta(
        {
            "usage_updated_at": _now_iso(),
            "usage_pending_sessions": still_pending,
            "usage_sessions_read": len(found) - still_pending,
            "usage_unreadable": unreadable,
            "usage_bytes_read": bytes_read,
            "usage_duration_seconds": round(duration, 3),
        }
    )
    return {
        "pending_sessions": still_pending,
        "sessions_read": read_sessions,
        "bytes_read": bytes_read,
        "unreadable": unreadable,
        "removed": removed,
        "budget_exhausted": exhausted,
        "duration_seconds": round(duration, 3),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_USAGE_BYTE_BUDGET",
    "refresh_usage_sync",
    "finalize_refresh",
    "default_transcripts_root",
    "refresh_transcripts_sync",
    "walk_transcripts",
    "MAIN_LANE",
    "SUBAGENT_LANE",
    "TranscriptFile",
    "SCHEMA_VERSION",
    "ClaudeIndexStore",
    "IndexSnapshot",
    "default_store_path",
    "plan_refresh",
    "read_record_rows",
    "refresh_store_sync",
    "walk_records",
]
