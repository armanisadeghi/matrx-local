"""Durable SQLite metadata index and persistent progressive crawl queue."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import math
import uuid
from array import array
from pathlib import Path
from typing import Iterable

import psutil

from app.services.filesystem.models import FileEntry, Place, entry_kind, is_hidden
from app.services.filesystem.roots import normalize_path_key

_DIRECTORY_LEASE_SECONDS = 300.0
_DIRECTORY_LEASE_REFRESH_SECONDS = 30.0

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS filesystem_roots (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    priority INTEGER NOT NULL,
    available INTEGER NOT NULL,
    configured INTEGER NOT NULL,
    last_claimed_at REAL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS filesystem_entries (
    path TEXT PRIMARY KEY,
    path_key TEXT NOT NULL UNIQUE,
    parent_path TEXT NOT NULL,
    parent_key TEXT NOT NULL,
    root_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    modified_at REAL,
    hidden INTEGER NOT NULL DEFAULT 0,
    extension TEXT,
    indexed_at REAL NOT NULL,
    content_state TEXT NOT NULL DEFAULT 'not_indexed',
    embedding_state TEXT NOT NULL DEFAULT 'not_indexed'
);
CREATE INDEX IF NOT EXISTS idx_filesystem_entries_parent ON filesystem_entries(parent_path);
CREATE INDEX IF NOT EXISTS idx_filesystem_entries_name ON filesystem_entries(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_filesystem_entries_root ON filesystem_entries(root_id);
CREATE TABLE IF NOT EXISTS filesystem_scan_queue (
    path TEXT PRIMARY KEY,
    root_id TEXT NOT NULL,
    depth INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    claimed_at REAL,
    claim_owner TEXT,
    claim_token TEXT,
    claim_pid INTEGER,
    claim_process_started_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error_kind TEXT,
    last_error TEXT,
    last_failed_at REAL,
    next_retry_at REAL
);
CREATE INDEX IF NOT EXISTS idx_filesystem_scan_queue_priority
    ON filesystem_scan_queue(priority DESC, depth ASC, updated_at ASC);
CREATE TABLE IF NOT EXISTS filesystem_scanned_dirs (
    path TEXT PRIMARY KEY,
    scanned_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS filesystem_path_mutations (
    path_key TEXT PRIMARY KEY,
    parent_key TEXT NOT NULL,
    observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS filesystem_index_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS filesystem_content (
    path TEXT PRIMARY KEY,
    path_key TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    source_modified_at REAL,
    source_size INTEGER,
    indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS filesystem_embeddings (
    path TEXT NOT NULL,
    path_key TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    source_modified_at REAL,
    source_size INTEGER,
    indexed_at REAL NOT NULL,
    PRIMARY KEY(path_key, model)
);
CREATE TABLE IF NOT EXISTS filesystem_enrichment_failures (
    path_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('content', 'embedding')),
    source_modified_at REAL,
    source_size INTEGER NOT NULL,
    model TEXT,
    attempts INTEGER NOT NULL,
    retry_at REAL NOT NULL,
    last_error TEXT NOT NULL,
    PRIMARY KEY(path_key, kind)
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS filesystem_entries_fts USING fts5(
    name, path, content='filesystem_entries', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS filesystem_entries_ai AFTER INSERT ON filesystem_entries BEGIN
  INSERT INTO filesystem_entries_fts(rowid, name, path) VALUES (new.rowid, new.name, new.path);
END;
CREATE TRIGGER IF NOT EXISTS filesystem_entries_ad AFTER DELETE ON filesystem_entries BEGIN
  INSERT INTO filesystem_entries_fts(filesystem_entries_fts, rowid, name, path)
  VALUES('delete', old.rowid, old.name, old.path);
END;
CREATE TRIGGER IF NOT EXISTS filesystem_entries_au AFTER UPDATE ON filesystem_entries BEGIN
  INSERT INTO filesystem_entries_fts(filesystem_entries_fts, rowid, name, path)
  VALUES('delete', old.rowid, old.name, old.path);
  INSERT INTO filesystem_entries_fts(rowid, name, path) VALUES (new.rowid, new.name, new.path);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS filesystem_content_fts USING fts5(
    path UNINDEXED, content, tokenize='unicode61 remove_diacritics 2'
);
"""


class FilesystemIndex:
    def __init__(self, db_path: Path) -> None:
        self.path = db_path
        self.fts_available = False
        self._claim_pid = os.getpid()
        self._claim_process_started_at = _process_started_at(self._claim_pid)
        self._claim_owner = uuid.uuid4().hex

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def initialize(self) -> None:
        self._ensure_claim_identity()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(filesystem_scan_queue)")}
            if "claimed_at" not in columns:
                db.execute("ALTER TABLE filesystem_scan_queue ADD COLUMN claimed_at REAL")
            if "attempts" not in columns:
                db.execute("ALTER TABLE filesystem_scan_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            for name, declaration in (
                ("claim_owner", "TEXT"),
                ("claim_token", "TEXT"),
                ("claim_pid", "INTEGER"),
                ("claim_process_started_at", "REAL"),
            ):
                if name not in columns:
                    db.execute(
                        f"ALTER TABLE filesystem_scan_queue ADD COLUMN {name} {declaration}"
                    )
            if "consecutive_failures" not in columns:
                db.execute(
                    "ALTER TABLE filesystem_scan_queue "
                    "ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0"
                )
            for name, declaration in (
                ("last_error_kind", "TEXT"),
                ("last_error", "TEXT"),
                ("last_failed_at", "REAL"),
                ("next_retry_at", "REAL"),
            ):
                if name not in columns:
                    db.execute(
                        f"ALTER TABLE filesystem_scan_queue ADD COLUMN {name} {declaration}"
                    )
            root_columns = {row["name"] for row in db.execute("PRAGMA table_info(filesystem_roots)")}
            if "last_claimed_at" not in root_columns:
                db.execute("ALTER TABLE filesystem_roots ADD COLUMN last_claimed_at REAL")
            mutation_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(filesystem_path_mutations)")
            }
            if "parent_key" not in mutation_columns:
                # This table is derived watcher coordination state. Rows from
                # the short-lived pre-parent schema cannot be scoped safely,
                # and startup runs before any new scan can own a lease.
                db.execute("DELETE FROM filesystem_path_mutations")
                db.execute(
                    "ALTER TABLE filesystem_path_mutations ADD COLUMN parent_key TEXT"
                )
            db.execute(
                """CREATE INDEX IF NOT EXISTS idx_filesystem_path_mutations_parent
                   ON filesystem_path_mutations(parent_key,observed_at)"""
            )
            entry_columns = {row["name"] for row in db.execute("PRAGMA table_info(filesystem_entries)")}
            entry_path_key_added = "path_key" not in entry_columns
            if "path_key" not in entry_columns:
                db.execute("ALTER TABLE filesystem_entries ADD COLUMN path_key TEXT")
            if "parent_key" not in entry_columns:
                db.execute("ALTER TABLE filesystem_entries ADD COLUMN parent_key TEXT")
            # Backfill only legacy/null rows. Re-normalizing every indexed path
            # on every boot caused one UPDATE + two FTS trigger writes per file;
            # a large durable index could block packaged startup indefinitely.
            for row in db.execute(
                "SELECT rowid,path,parent_path FROM filesystem_entries "
                "WHERE path_key IS NULL OR parent_key IS NULL"
            ).fetchall():
                db.execute(
                    "UPDATE filesystem_entries SET path_key=?,parent_key=? WHERE rowid=?",
                    (_path_key(row["path"]), _path_key(row["parent_path"]), row["rowid"]),
                )
            entry_indexes = {
                str(row["name"])
                for row in db.execute("PRAGMA index_list(filesystem_entries)")
            }
            if entry_path_key_added or "idx_filesystem_entries_path_key" not in entry_indexes:
                db.execute(
                    "DELETE FROM filesystem_entries WHERE rowid NOT IN "
                    "(SELECT MIN(rowid) FROM filesystem_entries GROUP BY path_key)"
                )
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_filesystem_entries_path_key ON filesystem_entries(path_key)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_filesystem_entries_parent_key ON filesystem_entries(parent_key)")

            content_columns = {row["name"] for row in db.execute("PRAGMA table_info(filesystem_content)")}
            if "path_key" not in content_columns:
                db.execute("ALTER TABLE filesystem_content ADD COLUMN path_key TEXT")
            if "source_size" not in content_columns:
                db.execute("ALTER TABLE filesystem_content ADD COLUMN source_size INTEGER")
            for row in db.execute(
                "SELECT path FROM filesystem_content WHERE path_key IS NULL"
            ).fetchall():
                db.execute(
                    "UPDATE filesystem_content SET path_key=? WHERE path=?",
                    (_path_key(row["path"]), row["path"]),
                )
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_filesystem_content_path_key ON filesystem_content(path_key)")

            embedding_columns = {row["name"] for row in db.execute("PRAGMA table_info(filesystem_embeddings)")}
            if "path_key" not in embedding_columns:
                db.execute("ALTER TABLE filesystem_embeddings ADD COLUMN path_key TEXT")
            if "source_size" not in embedding_columns:
                db.execute("ALTER TABLE filesystem_embeddings ADD COLUMN source_size INTEGER")
            for row in db.execute(
                "SELECT rowid,path FROM filesystem_embeddings WHERE path_key IS NULL"
            ).fetchall():
                db.execute(
                    "UPDATE filesystem_embeddings SET path_key=? WHERE rowid=?",
                    (_path_key(row["path"]), row["rowid"]),
                )
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_filesystem_embeddings_key_model ON filesystem_embeddings(path_key,model)")
            self._recover_orphaned_claims(db)
            # A process may have exited during optional enrichment. Leases are
            # process-local, so return those rows to the durable queue.
            db.execute("UPDATE filesystem_entries SET content_state='not_indexed' WHERE content_state='indexing'")
            db.execute(
                "UPDATE filesystem_entries SET embedding_state='not_indexed' "
                "WHERE embedding_state='indexing'"
            )
            try:
                db.executescript(_FTS_SCHEMA)
                self.fts_available = True
                version = db.execute(
                    "SELECT value FROM filesystem_index_state WHERE key='fts_schema_version'"
                ).fetchone()
                if version is None or version["value"] != "2":
                    db.execute("INSERT INTO filesystem_entries_fts(filesystem_entries_fts) VALUES('rebuild')")
                    db.execute("DELETE FROM filesystem_content_fts")
                    db.execute(
                        "INSERT INTO filesystem_content_fts(path,content) "
                        "SELECT path,content FROM filesystem_content"
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO filesystem_index_state(key,value) VALUES('fts_schema_version','2')"
                    )
            except sqlite3.OperationalError:
                self.fts_available = False

    def _ensure_claim_identity(self) -> None:
        """Regenerate the session identity if this object crossed a fork."""
        pid = os.getpid()
        if pid == self._claim_pid:
            return
        self._claim_pid = pid
        self._claim_process_started_at = _process_started_at(pid)
        self._claim_owner = uuid.uuid4().hex

    def _recover_orphaned_claims(self, db: sqlite3.Connection) -> None:
        """Release only leases whose owning process is no longer alive."""
        claims = db.execute(
            """SELECT DISTINCT claim_owner,claim_pid,claim_process_started_at
               FROM filesystem_scan_queue WHERE claimed_at IS NOT NULL"""
        ).fetchall()
        for claim in claims:
            owner = claim["claim_owner"]
            pid = claim["claim_pid"]
            started_at = claim["claim_process_started_at"]
            if owner and _process_identity_is_live(pid, started_at):
                continue
            if owner is None:
                predicate = "claim_owner IS NULL"
                params: tuple[object, ...] = ()
            else:
                predicate = "claim_owner=?"
                params = (owner,)
            db.execute(
                f"""UPDATE filesystem_scan_queue SET claimed_at=NULL,
                       claim_owner=NULL,claim_token=NULL,claim_pid=NULL,
                       claim_process_started_at=NULL
                    WHERE claimed_at IS NOT NULL AND {predicate}""",
                params,
            )

    def sync_roots(self, roots: Iterable[Place]) -> None:
        roots = list(roots)
        now = time.time()
        values = [
            (root.id, root.label, root.path, root.category, root.priority, root.available, root.configured, now)
            for root in roots
        ]
        with self._connect() as db:
            existing_paths = {
                str(row["id"]): str(row["path"])
                for row in db.execute("SELECT id,path FROM filesystem_roots")
            }
            active_ids = [root.id for root in roots]
            for root in roots:
                previous_path = existing_paths.get(root.id)
                if previous_path is None or previous_path == root.path:
                    continue
                # A stable configured ID can move to a different path. Cancel
                # all work owned by the old location; claim-token validation
                # prevents an in-flight old scan from committing afterward.
                db.execute(
                    "DELETE FROM filesystem_scan_queue WHERE root_id=?",
                    (root.id,),
                )
                self._delete_scanned_descendants(db, previous_path)
            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                db.execute(
                    f"DELETE FROM filesystem_scan_queue WHERE root_id NOT IN ({placeholders})",
                    active_ids,
                )
                db.execute(
                    f"DELETE FROM filesystem_roots WHERE id NOT IN ({placeholders})",
                    active_ids,
                )
            else:
                db.execute("DELETE FROM filesystem_scan_queue")
                db.execute("DELETE FROM filesystem_roots")
            db.executemany(
                """INSERT INTO filesystem_roots
                   (id,label,path,category,priority,available,configured,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET label=excluded.label,path=excluded.path,
                   category=excluded.category,priority=excluded.priority,
                   available=excluded.available,configured=excluded.configured,
                   updated_at=excluded.updated_at""",
                values,
            )
            for root in roots:
                # Priority is authored policy, not a monotonic score. Recompute
                # every outstanding row without disturbing its lease or retry.
                db.execute(
                    "UPDATE filesystem_scan_queue SET priority=?-depth WHERE root_id=?",
                    (root.priority * 1000, root.id),
                )
                if root.available:
                    db.execute(
                        """INSERT INTO filesystem_scan_queue(path,root_id,depth,priority,updated_at)
                           VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                           root_id=excluded.root_id,depth=excluded.depth,
                           priority=excluded.priority,
                           updated_at=excluded.updated_at""",
                        (root.path, root.id, 0, root.priority * 1000, now),
                    )

    def pop_next_directory(self, *, fair: bool = False) -> tuple[str, str, int, int, str] | None:
        self._ensure_claim_identity()
        lease_cutoff = time.time() - _DIRECTORY_LEASE_SECONDS
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            order = (
                "COALESCE(r.last_claimed_at,0),q.priority DESC,q.depth ASC,q.updated_at ASC"
                if fair else "q.priority DESC,q.depth ASC,q.updated_at ASC"
            )
            row = db.execute(
                f"""SELECT q.path,q.root_id,q.depth,q.priority
                    FROM filesystem_scan_queue q
                    LEFT JOIN filesystem_roots r ON r.id=q.root_id
                    WHERE (q.claimed_at IS NULL OR q.claimed_at < ?)
                      AND (q.next_retry_at IS NULL OR q.next_retry_at <= ?)
                    ORDER BY {order} LIMIT 1""",
                (lease_cutoff, time.time()),
            ).fetchone()
            if row is None:
                return None
            claimed_at = time.time()
            claim_token = uuid.uuid4().hex
            updated = db.execute(
                """UPDATE filesystem_scan_queue SET claimed_at=?,claim_owner=?,
                       claim_token=?,claim_pid=?,claim_process_started_at=?,
                       attempts=attempts+1 WHERE path=?
                       AND (claimed_at IS NULL OR claimed_at < ?)""",
                (
                    claimed_at,
                    self._claim_owner,
                    claim_token,
                    self._claim_pid,
                    self._claim_process_started_at,
                    row["path"],
                    lease_cutoff,
                ),
            )
            if updated.rowcount != 1:
                return None
            db.execute(
                "UPDATE filesystem_roots SET last_claimed_at=? WHERE id=?",
                (claimed_at, row["root_id"]),
            )
            return (
                str(row["path"]),
                str(row["root_id"]),
                int(row["depth"]),
                int(row["priority"]),
                claim_token,
            )

    def index_directory(
        self,
        path: str,
        root_id: str,
        depth: int,
        priority: int,
        claim_token: str,
        stop_event: threading.Event | None = None,
    ) -> int:
        del priority  # The current root policy is read transactionally at commit.
        scan_started = time.time()
        rows: list[tuple[object, ...]] = []
        children: list[tuple[str, int]] = []
        next_lease_refresh = time.monotonic() + _DIRECTORY_LEASE_REFRESH_SECONDS
        try:
            parent_device = os.stat(path, follow_symlinks=False).st_dev
        except OSError:
            parent_device = None
        iterator = os.scandir(path)
        with iterator:
            for item in iterator:
                if stop_event is not None and stop_event.is_set():
                    self.release_directory(path, claim_token)
                    return 0
                if time.monotonic() >= next_lease_refresh:
                    if not self._renew_directory(path, claim_token):
                        return 0
                    next_lease_refresh = (
                        time.monotonic() + _DIRECTORY_LEASE_REFRESH_SECONDS
                    )
                try:
                    info = item.stat(follow_symlinks=False)
                except FileNotFoundError:
                    # Concurrent deletion is a successfully observed absence.
                    continue
                except OSError:
                    # Permission, device, and network failures are incomplete
                    # observations. Preserve the prior index and retry.
                    raise
                kind = entry_kind(info.st_mode)
                absolute = os.path.abspath(item.path)
                suffix = Path(item.name).suffix.lower() or None
                rows.append(
                    (
                        absolute, _path_key(absolute), os.path.abspath(path),
                        _path_key(path), root_id, item.name, kind,
                        info.st_size if kind == "file" else 0, info.st_mtime,
                        is_hidden(item.name, info), suffix, scan_started,
                    )
                )
                if (
                    kind == "dir"
                    and not _should_skip_directory(item.name)
                    and (parent_device is None or info.st_dev == parent_device)
                ):
                    children.append((absolute, depth + 1))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owned_claim = db.execute(
                """SELECT 1 FROM filesystem_scan_queue
                   WHERE path=? AND claim_owner=? AND claim_token=?""",
                (os.path.abspath(path), self._claim_owner, claim_token),
            ).fetchone()
            if owned_claim is None:
                return 0
            newer_mutations = {
                str(row["path_key"])
                for row in db.execute(
                    """SELECT path_key FROM filesystem_path_mutations
                       WHERE parent_key=? AND observed_at>=?""",
                    (_path_key(path), scan_started),
                )
            }
            if newer_mutations:
                rows = [row for row in rows if str(row[1]) not in newer_mutations]
                children = [
                    child
                    for child in children
                    if _path_key(child[0]) not in newer_mutations
                ]
            root_active = db.execute(
                "SELECT priority,available FROM filesystem_roots WHERE id=?",
                (root_id,),
            ).fetchone()
            self._delete_missing_children(
                db,
                path,
                {str(row[1]) for row in rows},
                scan_started,
            )
            db.executemany(
                """INSERT INTO filesystem_entries
                   (path,path_key,parent_path,parent_key,root_id,name,kind,size,modified_at,hidden,extension,indexed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path_key) DO UPDATE SET path=excluded.path,
                   parent_path=excluded.parent_path,parent_key=excluded.parent_key,
                   root_id=excluded.root_id,name=excluded.name,kind=excluded.kind,
                   size=excluded.size,modified_at=excluded.modified_at,
                   hidden=excluded.hidden,extension=excluded.extension,indexed_at=excluded.indexed_at,
                   content_state=CASE WHEN filesystem_entries.modified_at IS NOT excluded.modified_at
                                          OR filesystem_entries.size<>excluded.size
                     THEN 'not_indexed' ELSE filesystem_entries.content_state END,
                   embedding_state=CASE WHEN filesystem_entries.modified_at IS NOT excluded.modified_at
                                            OR filesystem_entries.size<>excluded.size
                     THEN 'not_indexed' ELSE filesystem_entries.embedding_state END
                   WHERE filesystem_entries.indexed_at <= excluded.indexed_at""",
                rows,
            )
            if root_active is not None and bool(root_active["available"]):
                root_priority = int(root_active["priority"]) * 1000
                db.executemany(
                    """INSERT INTO filesystem_scan_queue(path,root_id,depth,priority,updated_at)
                       SELECT ?,?,?,?,? WHERE NOT EXISTS
                       (SELECT 1 FROM filesystem_scanned_dirs WHERE path=? AND scanned_at>=?)
                       ON CONFLICT(path) DO UPDATE SET
                       root_id=CASE WHEN excluded.priority > priority
                         THEN excluded.root_id ELSE root_id END,
                       depth=CASE WHEN excluded.priority > priority
                         THEN excluded.depth ELSE depth END,
                       priority=MAX(priority,excluded.priority),
                       updated_at=excluded.updated_at""",
                    [
                        (
                            child_path,
                            root_id,
                            child_depth,
                            root_priority - child_depth,
                            scan_started,
                            child_path,
                            scan_started - 21600.0,
                        )
                        for child_path, child_depth in children
                    ],
                )
            db.execute(
                "INSERT OR REPLACE INTO filesystem_scanned_dirs(path,scanned_at) VALUES(?,?)",
                (os.path.abspath(path), scan_started),
            )
            completed = db.execute(
                """DELETE FROM filesystem_scan_queue
                   WHERE path=? AND claim_owner=? AND claim_token=?""",
                (os.path.abspath(path), self._claim_owner, claim_token),
            )
            if completed.rowcount != 1:
                raise sqlite3.OperationalError("filesystem directory claim changed during commit")
            db.execute(
                "INSERT OR REPLACE INTO filesystem_index_state(key,value) VALUES('last_scan_at',?)",
                (str(scan_started),),
            )
            db.execute(
                """DELETE FROM filesystem_path_mutations
                   WHERE parent_key=? AND observed_at<?""",
                (_path_key(path), scan_started),
            )
        return len(rows)

    def complete_directory(self, path: str, claim_token: str) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owned = db.execute(
                """SELECT 1 FROM filesystem_scan_queue
                   WHERE path=? AND claim_owner=? AND claim_token=?""",
                (os.path.abspath(path), self._claim_owner, claim_token),
            ).fetchone()
            if owned is None:
                return False
            db.execute(
                "INSERT OR REPLACE INTO filesystem_scanned_dirs(path,scanned_at) VALUES(?,?)",
                (os.path.abspath(path), time.time()),
            )
            db.execute(
                """DELETE FROM filesystem_scan_queue
                   WHERE path=? AND claim_owner=? AND claim_token=?""",
                (os.path.abspath(path), self._claim_owner, claim_token),
            )
            return True

    def _renew_directory(self, path: str, claim_token: str) -> bool:
        with self._connect() as db:
            renewed = db.execute(
                """UPDATE filesystem_scan_queue SET claimed_at=?
                   WHERE path=? AND claim_owner=? AND claim_token=?""",
                (
                    time.time(),
                    os.path.abspath(path),
                    self._claim_owner,
                    claim_token,
                ),
            )
            return renewed.rowcount == 1

    def release_directory(self, path: str, claim_token: str) -> bool:
        with self._connect() as db:
            released = db.execute(
                """UPDATE filesystem_scan_queue SET claimed_at=NULL,
                       claim_owner=NULL,claim_token=NULL,claim_pid=NULL,
                       claim_process_started_at=NULL,updated_at=?
                   WHERE path=? AND claim_owner=? AND claim_token=?""",
                (
                    time.time(),
                    os.path.abspath(path),
                    self._claim_owner,
                    claim_token,
                ),
            )
            return released.rowcount == 1

    def fail_directory(self, path: str, error: OSError, claim_token: str) -> float | None:
        """Release a failed scan with bounded retry and durable diagnostics."""
        absolute = os.path.abspath(path)
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                """SELECT consecutive_failures FROM filesystem_scan_queue
                   WHERE path=? AND claim_owner=? AND claim_token=?""",
                (absolute, self._claim_owner, claim_token),
            ).fetchone()
            if row is None:
                return None
            consecutive_failures = max(0, int(row["consecutive_failures"])) + 1
            retry_delay = min(
                6 * 60 * 60,
                30 * (2 ** min(consecutive_failures - 1, 10)),
            )
            failed = db.execute(
                """UPDATE filesystem_scan_queue SET claimed_at=NULL,
                   claim_owner=NULL,claim_token=NULL,claim_pid=NULL,
                   claim_process_started_at=NULL,
                   consecutive_failures=?,
                   last_error_kind=?,last_error=?,last_failed_at=?,next_retry_at=?,updated_at=?
                   WHERE path=? AND claim_owner=? AND claim_token=?""",
                (
                    consecutive_failures,
                    type(error).__name__,
                    str(error)[:500],
                    now,
                    now + retry_delay,
                    now,
                    absolute,
                    self._claim_owner,
                    claim_token,
                ),
            )
            if failed.rowcount != 1:
                return None
        return float(retry_delay)

    def _delete_missing_children(
        self,
        db: sqlite3.Connection,
        parent: str,
        current_keys: set[str],
        scan_started: float,
    ) -> None:
        parent_key = _path_key(parent)
        existing = {
            str(row["path_key"]): str(row["path"])
            for row in db.execute(
                "SELECT path,path_key FROM filesystem_entries WHERE parent_key=? AND indexed_at<?",
                (parent_key, scan_started),
            )
        }
        for stale_key in set(existing) - current_keys:
            self._delete_path(db, existing[stale_key], freshness_cutoff=scan_started)

    def upsert_path(self, path: str, root_id: str = "watch") -> None:
        absolute = os.path.abspath(path)
        try:
            info = os.lstat(absolute)
        except OSError:
            self.delete_path(absolute)
            return
        kind = entry_kind(info.st_mode)
        name = os.path.basename(absolute) or absolute
        suffix = Path(name).suffix.lower() or None
        with self._connect() as db:
            db.execute(
                """INSERT INTO filesystem_entries
                   (path,path_key,parent_path,parent_key,root_id,name,kind,size,modified_at,hidden,extension,indexed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path_key) DO UPDATE SET path=excluded.path,parent_path=excluded.parent_path,
                   parent_key=excluded.parent_key,root_id=excluded.root_id,
                   name=excluded.name,kind=excluded.kind,size=excluded.size,
                   modified_at=excluded.modified_at,hidden=excluded.hidden,
                   extension=excluded.extension,indexed_at=excluded.indexed_at,
                   content_state=CASE WHEN filesystem_entries.modified_at IS NOT excluded.modified_at
                                          OR filesystem_entries.size<>excluded.size
                     THEN 'not_indexed' ELSE filesystem_entries.content_state END,
                   embedding_state=CASE WHEN filesystem_entries.modified_at IS NOT excluded.modified_at
                                            OR filesystem_entries.size<>excluded.size
                     THEN 'not_indexed' ELSE filesystem_entries.embedding_state END""",
                (
                    absolute, _path_key(absolute), os.path.dirname(absolute),
                    _path_key(os.path.dirname(absolute)), root_id, name, kind,
                    info.st_size if kind == "file" else 0, info.st_mtime,
                    is_hidden(name, info), suffix, time.time(),
                ),
            )
            if kind == "dir":
                root = db.execute(
                    "SELECT priority FROM filesystem_roots WHERE id=?", (root_id,)
                ).fetchone()
                crawl_priority = int(root["priority"]) * 1000 if root else 10_000
                db.execute(
                    """INSERT OR IGNORE INTO filesystem_scan_queue
                       (path,root_id,depth,priority,updated_at) VALUES(?,?,?,?,?)""",
                    (absolute, root_id, 0, crawl_priority, time.time()),
                )

    def delete_path(self, path: str) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO filesystem_path_mutations(path_key,parent_key,observed_at)
                   VALUES(?,?,?) ON CONFLICT(path_key) DO UPDATE SET
                   parent_key=excluded.parent_key,
                   observed_at=MAX(observed_at,excluded.observed_at)""",
                (
                    _path_key(path),
                    _path_key(os.path.dirname(os.path.abspath(path))),
                    time.time(),
                ),
            )
            self._delete_path(db, path)

    def _delete_path(
        self,
        db: sqlite3.Connection,
        path: str,
        *,
        freshness_cutoff: float | None = None,
    ) -> bool:
        """Delete a subtree on one transaction, unless a watcher made it newer."""
        key = _path_key(path)
        rows = db.execute(
            """WITH RECURSIVE descendants(path_key) AS (
                 SELECT ? UNION ALL
                 SELECT e.path_key FROM filesystem_entries e
                 JOIN descendants d ON e.parent_key=d.path_key
               )
               SELECT e.path,e.path_key,e.indexed_at FROM filesystem_entries e
               JOIN descendants d ON d.path_key=e.path_key""",
            (key,),
        ).fetchall()
        if freshness_cutoff is not None and any(
            float(row["indexed_at"]) >= freshness_cutoff for row in rows
        ):
            return False
        paths = [str(row["path"]) for row in rows]
        keys = [str(row["path_key"]) for row in rows]
        if paths:
            db.executemany(
                "DELETE FROM filesystem_content_fts WHERE path=?",
                [(value,) for value in paths],
            )
            db.executemany(
                "DELETE FROM filesystem_content WHERE path_key=?",
                [(value,) for value in keys],
            )
            db.executemany(
                "DELETE FROM filesystem_embeddings WHERE path_key=?",
                [(value,) for value in keys],
            )
            db.executemany(
                "DELETE FROM filesystem_enrichment_failures WHERE path_key=?",
                [(value,) for value in keys],
            )
            db.executemany(
                "DELETE FROM filesystem_entries WHERE path_key=?",
                [(value,) for value in reversed(keys)],
            )
        queued = [
            str(row["path"])
            for row in db.execute("SELECT path FROM filesystem_scan_queue")
        ]
        stale_queue = [
            queued_path
            for queued_path in queued
            if _is_same_or_descendant(queued_path, path)
        ]
        db.executemany(
            "DELETE FROM filesystem_scan_queue WHERE path=?",
            [(value,) for value in stale_queue],
        )
        self._delete_scanned_descendants(db, path)
        return True

    def _delete_scanned_descendants(
        self, db: sqlite3.Connection, path: str
    ) -> None:
        scanned = [
            str(row["path"])
            for row in db.execute("SELECT path FROM filesystem_scanned_dirs")
        ]
        stale_scanned = [
            scanned_path
            for scanned_path in scanned
            if _is_same_or_descendant(scanned_path, path)
        ]
        db.executemany(
            "DELETE FROM filesystem_scanned_dirs WHERE path=?",
            [(value,) for value in stale_scanned],
        )

    def search(self, query: str, *, limit: int, offset: int, root: str | None = None) -> list[FileEntry]:
        params: list[object]
        root_cte = ""
        root_join = ""
        if root:
            root_cte = (
                "WITH RECURSIVE subtree(path_key) AS ("
                "SELECT ? UNION ALL SELECT e.path_key FROM filesystem_entries e "
                "JOIN subtree s ON e.parent_key=s.path_key) "
            )
            root_join = " JOIN subtree s ON s.path_key=e.path_key "
            params_root: list[object] = [_path_key(root)]
        else:
            params_root = []
        with self._connect() as db:
            if self.fts_available and _fts_query(query):
                sql = (
                    root_cte + "SELECT e.* FROM filesystem_entries_fts f "
                    "JOIN filesystem_entries e ON e.rowid=f.rowid "
                    "JOIN filesystem_roots r ON r.id=e.root_id "
                    + root_join + "WHERE filesystem_entries_fts MATCH ?" +
                    " ORDER BY bm25(filesystem_entries_fts), length(e.path) LIMIT ? OFFSET ?"
                )
                params = [*params_root, _fts_query(query), limit, offset]
            else:
                sql = (
                    root_cte + "SELECT e.* FROM filesystem_entries e "
                    "JOIN filesystem_roots r ON r.id=e.root_id " + root_join + "WHERE "
                    "(e.name LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                    "e.path LIKE ? ESCAPE '\\' COLLATE NOCASE) "
                    "ORDER BY length(e.path),e.name COLLATE NOCASE LIMIT ? OFFSET ?"
                )
                needle = f"%{_escape_like(query)}%"
                params = [*params_root, needle, needle, limit, offset]
            rows = db.execute(sql, params).fetchall()
        return [_row_to_entry(row) for row in rows]

    def queue_count(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM filesystem_scan_queue").fetchone()
            return int(row["count"])

    def pending_root_paths(self) -> list[str]:
        """Return only roots that still own durable scan work."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT DISTINCT r.path FROM filesystem_scan_queue q
                   JOIN filesystem_roots r ON r.id=q.root_id
                   WHERE r.available=1 ORDER BY q.priority DESC,r.path"""
            ).fetchall()
        return [str(row["path"]) for row in rows]

    def clear_derived(self) -> None:
        """Delete the fully rebuildable local index without touching user files."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.fts_available:
                db.execute("DELETE FROM filesystem_content_fts")
            db.execute("DELETE FROM filesystem_content")
            db.execute("DELETE FROM filesystem_embeddings")
            db.execute("DELETE FROM filesystem_enrichment_failures")
            db.execute("DELETE FROM filesystem_entries")
            db.execute("DELETE FROM filesystem_scan_queue")
            db.execute("DELETE FROM filesystem_scanned_dirs")
            db.execute("DELETE FROM filesystem_path_mutations")
            db.execute("DELETE FROM filesystem_roots")
            db.execute("DELETE FROM filesystem_index_state")
        try:
            with self._connect() as db:
                db.execute("VACUUM")
        except sqlite3.OperationalError:
            # The data deletion is authoritative; a concurrent reader may
            # temporarily prevent optional file compaction.
            pass
        try:
            # WAL mode can otherwise leave the pre-clear database size on disk
            # even after VACUUM. Reported storage must reflect reclaimed bytes.
            with self._connect() as db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass

    def apply_enrichment_limits(
        self,
        max_content_bytes: int,
        max_embedding_entries: int,
        embedding_model: str,
        *,
        reset_content_quota: bool = False,
    ) -> None:
        """Enforce reduced quotas and keep only the active embedding model."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if reset_content_quota:
                db.execute(
                    "UPDATE filesystem_entries SET content_state='not_indexed' "
                    "WHERE content_state='quota'"
                )

            content_rows = db.execute(
                """SELECT path,path_key,length(CAST(content AS BLOB)) AS bytes
                   FROM filesystem_content ORDER BY indexed_at ASC"""
            ).fetchall()
            used = sum(int(row["bytes"] or 0) for row in content_rows)
            for row in content_rows:
                if used <= max(0, max_content_bytes):
                    break
                if self.fts_available:
                    db.execute(
                        "DELETE FROM filesystem_content_fts WHERE path=?",
                        (row["path"],),
                    )
                db.execute(
                    "DELETE FROM filesystem_content WHERE path_key=?",
                    (row["path_key"],),
                )
                db.execute(
                    "DELETE FROM filesystem_embeddings WHERE path_key=?",
                    (row["path_key"],),
                )
                db.execute(
                    """UPDATE filesystem_entries
                       SET content_state='quota',embedding_state='not_indexed'
                       WHERE path_key=?""",
                    (row["path_key"],),
                )
                used -= int(row["bytes"] or 0)

            db.execute(
                "DELETE FROM filesystem_embeddings WHERE model<>?",
                (embedding_model,),
            )
            db.execute(
                """UPDATE filesystem_entries SET embedding_state='not_indexed'
                   WHERE embedding_state='indexed' AND NOT EXISTS (
                     SELECT 1 FROM filesystem_embeddings v
                     WHERE v.path_key=filesystem_entries.path_key AND v.model=?
                   )""",
                (embedding_model,),
            )
            embedding_rows = db.execute(
                """SELECT path_key FROM filesystem_embeddings WHERE model=?
                   ORDER BY indexed_at DESC""",
                (embedding_model,),
            ).fetchall()
            for row in embedding_rows[max(0, max_embedding_entries):]:
                db.execute(
                    "DELETE FROM filesystem_embeddings WHERE path_key=? AND model=?",
                    (row["path_key"], embedding_model),
                )
                db.execute(
                    "UPDATE filesystem_entries SET embedding_state='not_indexed' WHERE path_key=?",
                    (row["path_key"],),
                )

    def last_scan_at(self) -> float | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM filesystem_index_state WHERE key='last_scan_at'"
            ).fetchone()
        if row is None:
            return None
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            return None

    def storage_bytes(self) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
            )
            if candidate.exists()
        )

    def scan_status(self, *, failure_limit: int = 25) -> dict[str, object]:
        """Return queue progress without hiding inaccessible/failed locations."""
        now = time.time()
        lease_cutoff = now - _DIRECTORY_LEASE_SECONDS
        with self._connect() as db:
            counts = db.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS failed,
                          SUM(CASE WHEN claimed_at >= ? THEN 1 ELSE 0 END) AS claimed,
                          SUM(CASE WHEN (claimed_at IS NULL OR claimed_at < ?)
                                    AND (next_retry_at IS NULL OR next_retry_at <= ?)
                                   THEN 1 ELSE 0 END) AS ready
                   FROM filesystem_scan_queue""",
                (lease_cutoff, lease_cutoff, now),
            ).fetchone()
            failures = db.execute(
                """SELECT path,root_id,attempts,consecutive_failures,
                          last_error_kind,last_error,last_failed_at,next_retry_at
                   FROM filesystem_scan_queue WHERE last_error IS NOT NULL
                   ORDER BY last_failed_at DESC LIMIT ?""",
                (max(0, min(failure_limit, 100)),),
            ).fetchall()
        return {
            "total": int(counts["total"] or 0),
            "failed": int(counts["failed"] or 0),
            "claimed": int(counts["claimed"] or 0),
            "ready": int(counts["ready"] or 0),
            "failures": [dict(row) for row in failures],
        }

    def entry_count(self) -> int:
        with self._connect() as db:
            row = db.execute(
                """SELECT COUNT(*) AS count FROM filesystem_entries e
                   JOIN filesystem_roots r ON r.id=e.root_id"""
            ).fetchone()
            return int(row["count"])

    def prune_orphaned_entries(self, *, limit: int = 250) -> int:
        """Incrementally reclaim rows whose discovered volume disappeared.

        Root synchronization only needs to remove the authority row to make
        stale results invisible. Physical cleanup stays bounded here so a
        large detached or formerly aliased volume cannot stall startup.
        """
        bounded_limit = min(max(1, limit), 5_000)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT e.path,e.path_key FROM filesystem_entries e
                   LEFT JOIN filesystem_roots r ON r.id=e.root_id
                   WHERE r.id IS NULL ORDER BY e.indexed_at LIMIT ?""",
                (bounded_limit,),
            ).fetchall()
            if not rows:
                return 0
            paths = [(str(row["path"]),) for row in rows]
            keys = [(str(row["path_key"]),) for row in rows]
            if self.fts_available:
                db.executemany(
                    "DELETE FROM filesystem_content_fts WHERE path=?", paths
                )
            db.executemany(
                "DELETE FROM filesystem_content WHERE path_key=?", keys
            )
            db.executemany(
                "DELETE FROM filesystem_embeddings WHERE path_key=?", keys
            )
            db.executemany(
                "DELETE FROM filesystem_enrichment_failures WHERE path_key=?", keys
            )
            db.executemany(
                "DELETE FROM filesystem_entries WHERE path_key=?", keys
            )
            return len(rows)

    def next_content_candidate(
        self, extensions: tuple[str, ...], max_content_bytes: int
    ) -> tuple[str, float | None, int] | None:
        if not extensions:
            return None
        placeholders = ",".join("?" for _ in extensions)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute(
                """SELECT COALESCE(SUM(length(CAST(c.content AS BLOB))),0) AS bytes
                   FROM filesystem_content c
                   JOIN filesystem_entries e ON e.path_key=c.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id"""
            ).fetchone()
            if int(used["bytes"]) >= max_content_bytes:
                return None
            remaining = max_content_bytes - int(used["bytes"])
            row = db.execute(
                f"""SELECT e.path,e.modified_at,e.size FROM filesystem_entries e
                    JOIN filesystem_roots r ON r.id=e.root_id
                    LEFT JOIN filesystem_enrichment_failures f
                      ON f.path_key=e.path_key AND f.kind='content'
                    WHERE e.kind='file'
                    AND (
                      e.content_state='not_indexed'
                      OR (e.content_state='error' AND (
                        f.retry_at IS NULL OR f.retry_at<=?
                        OR f.source_modified_at IS NOT e.modified_at
                        OR f.source_size<>e.size
                      ))
                    )
                    AND e.extension IN ({placeholders}) AND e.size BETWEEN 1 AND 1048576
                    AND e.size <= ?
                    ORDER BY e.indexed_at LIMIT 1""",
                (time.time(), *extensions, remaining),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE filesystem_entries SET content_state='indexing',indexed_at=? WHERE path=?",
                (time.time(), row["path"]),
            )
            return str(row["path"]), row["modified_at"], int(row["size"])

    def store_content(
        self,
        path: str,
        content: str,
        source_modified_at: float | None,
        source_size: int,
        max_content_bytes: int,
    ) -> bool:
        now = time.time()
        key = _path_key(path)
        try:
            disk_state = os.stat(path, follow_symlinks=False)
        except OSError:
            disk_state = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT path,modified_at,size FROM filesystem_entries WHERE path_key=?", (key,)
            ).fetchone()
            if (
                disk_state is None
                or disk_state.st_mtime != source_modified_at
                or disk_state.st_size != source_size
                or current is None
                or current["modified_at"] != source_modified_at
                or int(current["size"]) != source_size
            ):
                db.execute(
                    """UPDATE filesystem_entries SET content_state='not_indexed'
                       WHERE path_key=? AND content_state='indexing'
                       AND modified_at IS ? AND size=?""",
                    (key, source_modified_at, source_size),
                )
                return False
            old = db.execute(
                "SELECT path,length(CAST(content AS BLOB)) AS bytes FROM filesystem_content WHERE path_key=?",
                (key,),
            ).fetchone()
            used = db.execute(
                """SELECT COALESCE(SUM(length(CAST(c.content AS BLOB))),0) AS bytes
                   FROM filesystem_content c
                   JOIN filesystem_entries e ON e.path_key=c.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id"""
            ).fetchone()
            projected = int(used["bytes"]) - (int(old["bytes"]) if old else 0) + len(content.encode("utf-8"))
            if projected > max_content_bytes:
                db.execute("UPDATE filesystem_entries SET content_state='quota' WHERE path_key=?", (key,))
                return False
            if old:
                db.execute("DELETE FROM filesystem_content_fts WHERE path=?", (old["path"],))
            db.execute(
                "INSERT INTO filesystem_content_fts(path,content) VALUES(?,?)", (path, content)
            )
            db.execute(
                """INSERT INTO filesystem_content(path,path_key,content,source_modified_at,source_size,indexed_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(path_key) DO UPDATE SET
                   path=excluded.path,content=excluded.content,
                   source_modified_at=excluded.source_modified_at,source_size=excluded.source_size,
                   indexed_at=excluded.indexed_at""",
                (path, key, content, source_modified_at, source_size, now),
            )
            db.execute(
                "UPDATE filesystem_entries SET content_state='indexed', embedding_state='not_indexed' WHERE path_key=?",
                (key,),
            )
            db.execute(
                "DELETE FROM filesystem_enrichment_failures WHERE path_key=? AND kind='content'",
                (key,),
            )
            return True

    def mark_content_skipped(self, path: str, state: str = "skipped") -> None:
        with self._connect() as db:
            db.execute("UPDATE filesystem_entries SET content_state=? WHERE path_key=?", (state, _path_key(path)))

    def finish_enrichment_failure(
        self,
        path: str,
        kind: str,
        error: str,
        source_modified_at: float | None,
        source_size: int,
        *,
        model: str | None = None,
    ) -> bool:
        """Persist a bounded-backoff failure only for the candidate still claimed.

        Clear/rebuild and watcher mutations can replace a path while I/O or an
        embedding request is in flight. The source fingerprint and `indexing`
        state form the commit fence so that stale work cannot poison the new row.
        """
        if kind not in {"content", "embedding"}:
            raise ValueError(f"unsupported enrichment kind: {kind}")
        state_column = "content_state" if kind == "content" else "embedding_state"
        key = _path_key(path)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                f"SELECT modified_at,size,{state_column} AS state "
                "FROM filesystem_entries WHERE path_key=?",
                (key,),
            ).fetchone()
            if (
                current is None
                or current["state"] != "indexing"
                or current["modified_at"] != source_modified_at
                or int(current["size"]) != source_size
            ):
                return False
            previous = db.execute(
                "SELECT * FROM filesystem_enrichment_failures WHERE path_key=? AND kind=?",
                (key, kind),
            ).fetchone()
            same_source = bool(
                previous is not None
                and previous["source_modified_at"] == source_modified_at
                and int(previous["source_size"]) == source_size
                and previous["model"] == model
            )
            attempts = int(previous["attempts"]) + 1 if same_source else 1
            retry_delay = min(3_600.0, 5.0 * (2 ** min(attempts - 1, 10)))
            db.execute(
                """INSERT INTO filesystem_enrichment_failures
                   (path_key,kind,source_modified_at,source_size,model,attempts,retry_at,last_error)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path_key,kind) DO UPDATE SET
                   source_modified_at=excluded.source_modified_at,
                   source_size=excluded.source_size,model=excluded.model,
                   attempts=excluded.attempts,retry_at=excluded.retry_at,
                   last_error=excluded.last_error""",
                (
                    key,
                    kind,
                    source_modified_at,
                    source_size,
                    model,
                    attempts,
                    time.time() + retry_delay,
                    error[:2_000],
                ),
            )
            failure_state = "error" if kind == "content" else "unavailable"
            db.execute(
                f"UPDATE filesystem_entries SET {state_column}=? WHERE path_key=?",
                (failure_state, key),
            )
            return True

    def reset_enrichment_candidate(
        self,
        path: str,
        kind: str,
        source_modified_at: float | None,
        source_size: int,
    ) -> bool:
        """Release only the still-current enrichment claim back to the queue."""
        if kind not in {"content", "embedding"}:
            raise ValueError(f"unsupported enrichment kind: {kind}")
        state_column = "content_state" if kind == "content" else "embedding_state"
        with self._connect() as db:
            cursor = db.execute(
                f"""UPDATE filesystem_entries SET {state_column}='not_indexed'
                    WHERE path_key=? AND {state_column}='indexing'
                    AND modified_at IS ? AND size=?""",
                (_path_key(path), source_modified_at, source_size),
            )
            return bool(cursor.rowcount)

    def reset_quota_candidates(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE filesystem_entries SET content_state='not_indexed' WHERE content_state='quota'")

    def next_embedding_candidate(
        self, model: str, max_entries: int
    ) -> tuple[str, str, float | None, int] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                """SELECT COUNT(*) AS count FROM filesystem_embeddings v
                   JOIN filesystem_entries e ON e.path_key=v.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id
                   WHERE v.model=?""",
                (model,),
            ).fetchone()
            if int(count["count"]) >= max_entries:
                return None
            row = db.execute(
                """SELECT e.path,c.content,c.source_modified_at,c.source_size FROM filesystem_entries e
                   JOIN filesystem_roots r ON r.id=e.root_id
                   JOIN filesystem_content c ON c.path_key=e.path_key
                   LEFT JOIN filesystem_embeddings v ON v.path_key=e.path_key AND v.model=?
                   LEFT JOIN filesystem_enrichment_failures f
                     ON f.path_key=e.path_key AND f.kind='embedding'
                   WHERE e.content_state='indexed'
                   AND c.source_modified_at IS e.modified_at
                   AND c.source_size=e.size
                   AND (
                     e.embedding_state='not_indexed'
                     OR (e.embedding_state='indexed' AND v.path IS NULL)
                     OR (e.embedding_state='unavailable' AND (
                       f.retry_at IS NULL OR f.retry_at<=?
                       OR f.source_modified_at IS NOT c.source_modified_at
                       OR f.source_size<>c.source_size
                       OR f.model IS NOT ?
                     ))
                   )
                   ORDER BY c.indexed_at LIMIT 1""",
                (model, time.time(), model),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE filesystem_entries SET embedding_state='indexing' WHERE path=?",
                (row["path"],),
            )
            return str(row["path"]), str(row["content"]), row["source_modified_at"], int(row["source_size"])

    def store_embedding(
        self, path: str, model: str, vector: bytes, dimensions: int,
        source_modified_at: float | None, source_size: int,
        *, max_entries: int | None = None,
    ) -> bool:
        key = _path_key(path)
        try:
            disk_state = os.stat(path, follow_symlinks=False)
        except OSError:
            disk_state = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT modified_at,size FROM filesystem_entries WHERE path_key=?", (key,)
            ).fetchone()
            if (
                disk_state is None
                or disk_state.st_mtime != source_modified_at
                or disk_state.st_size != source_size
                or current is None
                or current["modified_at"] != source_modified_at
                or int(current["size"]) != source_size
            ):
                db.execute(
                    """UPDATE filesystem_entries SET embedding_state='not_indexed'
                       WHERE path_key=? AND embedding_state='indexing'
                       AND modified_at IS ? AND size=?""",
                    (key, source_modified_at, source_size),
                )
                return False
            existing = db.execute(
                "SELECT 1 FROM filesystem_embeddings WHERE path_key=? AND model=?",
                (key, model),
            ).fetchone()
            if existing is None and max_entries is not None:
                count = db.execute(
                    """SELECT COUNT(*) AS count FROM filesystem_embeddings v
                       JOIN filesystem_entries e ON e.path_key=v.path_key
                       JOIN filesystem_roots r ON r.id=e.root_id
                       WHERE v.model=?""",
                    (model,),
                ).fetchone()
                if int(count["count"]) >= max(0, max_entries):
                    db.execute(
                        "UPDATE filesystem_entries SET embedding_state='not_indexed' WHERE path_key=?",
                        (key,),
                    )
                    return False
            db.execute(
                """INSERT INTO filesystem_embeddings
                   (path,path_key,model,dimensions,vector,source_modified_at,source_size,indexed_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path_key,model) DO UPDATE SET
                   path=excluded.path,
                   dimensions=excluded.dimensions,vector=excluded.vector,
                   source_modified_at=excluded.source_modified_at,source_size=excluded.source_size,
                   indexed_at=excluded.indexed_at""",
                (path, key, model, dimensions, vector, source_modified_at, source_size, time.time()),
            )
            db.execute(
                "UPDATE filesystem_entries SET embedding_state='indexed' WHERE path_key=?", (key,)
            )
            db.execute(
                "DELETE FROM filesystem_enrichment_failures WHERE path_key=? AND kind='embedding'",
                (key,),
            )
            return True

    def reset_embedding_candidate(self, path: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE filesystem_entries SET embedding_state='not_indexed' WHERE path_key=?",
                (_path_key(path),),
            )

    def mark_embedding_unavailable(self, path: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE filesystem_entries SET embedding_state='unavailable' WHERE path_key=?", (_path_key(path),)
            )

    def enrichment_counts(self) -> dict[str, int]:
        with self._connect() as db:
            content = db.execute(
                """SELECT COUNT(*) AS count FROM filesystem_content c
                   JOIN filesystem_entries e ON e.path_key=c.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id"""
            ).fetchone()
            embeddings = db.execute(
                """SELECT COUNT(*) AS count FROM filesystem_embeddings v
                   JOIN filesystem_entries e ON e.path_key=v.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id"""
            ).fetchone()
            content_bytes = db.execute(
                """SELECT COALESCE(SUM(length(CAST(c.content AS BLOB))),0) AS bytes
                   FROM filesystem_content c
                   JOIN filesystem_entries e ON e.path_key=c.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id"""
            ).fetchone()
            failures = db.execute(
                """SELECT
                     SUM(CASE WHEN f.kind='content' THEN 1 ELSE 0 END) AS content_failures,
                     SUM(CASE WHEN f.kind='embedding' THEN 1 ELSE 0 END) AS embedding_failures
                   FROM filesystem_enrichment_failures f
                   JOIN filesystem_entries e ON e.path_key=f.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id"""
            ).fetchone()
        return {
            "content_entries": int(content["count"]),
            "embedding_entries": int(embeddings["count"]),
            "content_bytes": int(content_bytes["bytes"]),
            "content_failures": int(failures["content_failures"] or 0),
            "embedding_failures": int(failures["embedding_failures"] or 0),
        }

    def search_content(self, query: str, limit: int = 50) -> list[dict[str, object]]:
        expression = _fts_query(query)
        if not expression:
            return []
        with self._connect() as db:
            rows = db.execute(
                """SELECT c.path,
                          snippet(filesystem_content_fts,1,'[',']',' … ',24) AS snippet
                   FROM filesystem_content_fts
                   JOIN filesystem_content c ON c.path=filesystem_content_fts.path
                   JOIN filesystem_entries e ON e.path_key=c.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id
                   WHERE filesystem_content_fts MATCH ?
                   AND e.content_state='indexed'
                   AND c.source_modified_at IS e.modified_at
                   AND c.source_size=e.size
                   ORDER BY bm25(filesystem_content_fts) LIMIT ?""",
                (expression, min(max(1, limit), 200)),
            ).fetchall()
        return [{"path": str(row["path"]), "snippet": str(row["snippet"])} for row in rows]

    def semantic_search(
        self,
        query_vector: list[float],
        model: str,
        *,
        limit: int = 20,
        max_scan: int = 10_000,
    ) -> list[dict[str, object]]:
        if not query_vector:
            return []
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if query_norm == 0:
            return []
        with self._connect() as db:
            rows = db.execute(
                """SELECT v.path,v.vector,v.dimensions,e.kind,e.size,e.modified_at,
                          e.hidden,e.extension,e.name
                   FROM filesystem_embeddings v
                   JOIN filesystem_entries e ON e.path_key=v.path_key
                   JOIN filesystem_roots r ON r.id=e.root_id
                   WHERE v.model=? AND v.dimensions=?
                   AND e.embedding_state='indexed'
                   AND v.source_modified_at IS e.modified_at
                   AND v.source_size=e.size
                   ORDER BY v.indexed_at DESC LIMIT ?""",
                (model, len(query_vector), min(max(1, max_scan), 50_000)),
            ).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            values = array("f")
            values.frombytes(bytes(row["vector"]))
            vector_norm = math.sqrt(sum(value * value for value in values))
            if vector_norm == 0:
                continue
            score = sum(left * right for left, right in zip(query_vector, values, strict=True)) / (
                query_norm * vector_norm
            )
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "score": score,
                "entry": FileEntry(
                    name=str(row["name"]),
                    path=str(row["path"]),
                    kind=str(row["kind"]),
                    size=int(row["size"]),
                    modified_at=row["modified_at"],
                    hidden=bool(row["hidden"]),
                    extension=row["extension"],
                    indexed=True,
                ).to_dict(),
            }
            for score, row in scored[: min(max(1, limit), 100)]
        ]


def _should_skip_directory(name: str) -> bool:
    folded = name.casefold()
    return folded in {
        ".git", ".hg", ".svn", "__pycache__", "node_modules", ".cache",
        "$recycle.bin", "system volume information",
    }


def _process_started_at(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError):
        return None


def _process_identity_is_live(pid: object, started_at: object) -> bool:
    if not isinstance(pid, int) or not isinstance(started_at, (int, float)):
        return False
    try:
        process = psutil.Process(pid)
        return (
            process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
            and abs(float(process.create_time()) - float(started_at)) < 0.01
        )
    except psutil.AccessDenied:
        # Do not steal a lease merely because the OS hides another process.
        return True
    except (psutil.Error, OSError):
        return False


def _fts_query(query: str) -> str:
    tokens = [token for token in "".join(ch if ch.isalnum() else " " for ch in query).split() if token]
    return " AND ".join(f'"{token}"*' for token in tokens[:12])


def _path_key(path: str) -> str:
    return normalize_path_key(os.path.abspath(path))


def _is_same_or_descendant(candidate: str, parent: str) -> bool:
    candidate_key = _path_key(candidate)
    parent_key = _path_key(parent)
    if candidate_key == parent_key:
        return True
    try:
        return normalize_path_key(os.path.commonpath([candidate_key, parent_key])) == parent_key
    except ValueError:
        return False


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_entry(row: sqlite3.Row) -> FileEntry:
    return FileEntry(
        name=str(row["name"]), path=str(row["path"]), kind=str(row["kind"]),
        size=int(row["size"]), modified_at=row["modified_at"],
        hidden=bool(row["hidden"]), extension=row["extension"], indexed=True,
    )
