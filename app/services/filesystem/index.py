"""Durable SQLite metadata index and persistent progressive crawl queue."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import math
from array import array
from pathlib import Path
from typing import Iterable

from app.services.filesystem.models import FileEntry, Place, entry_kind, is_hidden
from app.services.filesystem.roots import normalize_path_key

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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(filesystem_scan_queue)")}
            if "claimed_at" not in columns:
                db.execute("ALTER TABLE filesystem_scan_queue ADD COLUMN claimed_at REAL")
            if "attempts" not in columns:
                db.execute("ALTER TABLE filesystem_scan_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
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
            entry_columns = {row["name"] for row in db.execute("PRAGMA table_info(filesystem_entries)")}
            if "path_key" not in entry_columns:
                db.execute("ALTER TABLE filesystem_entries ADD COLUMN path_key TEXT")
            if "parent_key" not in entry_columns:
                db.execute("ALTER TABLE filesystem_entries ADD COLUMN parent_key TEXT")
            for row in db.execute("SELECT rowid,path,parent_path FROM filesystem_entries").fetchall():
                db.execute(
                    "UPDATE filesystem_entries SET path_key=?,parent_key=? WHERE rowid=?",
                    (_path_key(row["path"]), _path_key(row["parent_path"]), row["rowid"]),
                )
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
            db.execute("UPDATE filesystem_content SET path_key=COALESCE(path_key,path)")
            for row in db.execute("SELECT path FROM filesystem_content").fetchall():
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
            for row in db.execute("SELECT rowid,path FROM filesystem_embeddings").fetchall():
                db.execute(
                    "UPDATE filesystem_embeddings SET path_key=? WHERE rowid=?",
                    (_path_key(row["path"]), row["rowid"]),
                )
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_filesystem_embeddings_key_model ON filesystem_embeddings(path_key,model)")
            # Directory leases belong to this single local engine process. If
            # it exits while scanning, no worker remains that can complete the
            # persisted claim, so make it immediately available on startup.
            # Retry deadlines and failure diagnostics remain intact.
            db.execute(
                "UPDATE filesystem_scan_queue SET claimed_at=NULL "
                "WHERE claimed_at IS NOT NULL"
            )
            # A process may have exited during optional enrichment. Leases are
            # process-local, so return those rows to the durable queue.
            db.execute("UPDATE filesystem_entries SET content_state='not_indexed' WHERE content_state='indexing'")
            db.execute(
                "UPDATE filesystem_entries SET embedding_state='not_indexed' "
                "WHERE embedding_state IN ('indexing','unavailable')"
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

    def sync_roots(self, roots: Iterable[Place]) -> None:
        roots = list(roots)
        now = time.time()
        values = [
            (root.id, root.label, root.path, root.category, root.priority, root.available, root.configured, now)
            for root in roots
        ]
        with self._connect() as db:
            db.execute("DELETE FROM filesystem_roots")
            active_ids = [root.id for root in roots]
            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                db.execute(
                    f"DELETE FROM filesystem_scan_queue WHERE root_id NOT IN ({placeholders})",
                    active_ids,
                )
            else:
                db.execute("DELETE FROM filesystem_scan_queue")
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
                if root.available:
                    db.execute(
                        """INSERT INTO filesystem_scan_queue(path,root_id,depth,priority,updated_at)
                           VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                           root_id=excluded.root_id, priority=MAX(priority,excluded.priority),
                           updated_at=excluded.updated_at""",
                        (root.path, root.id, 0, root.priority * 1000, now),
                    )

    def pop_next_directory(self, *, fair: bool = False) -> tuple[str, str, int, int] | None:
        lease_cutoff = time.time() - 300.0
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
            db.execute(
                "UPDATE filesystem_scan_queue SET claimed_at=?,attempts=attempts+1 WHERE path=?",
                (time.time(), row["path"]),
            )
            db.execute(
                "UPDATE filesystem_roots SET last_claimed_at=? WHERE id=?",
                (time.time(), row["root_id"]),
            )
            return str(row["path"]), str(row["root_id"]), int(row["depth"]), int(row["priority"])

    def index_directory(
        self,
        path: str,
        root_id: str,
        depth: int,
        priority: int,
        stop_event: threading.Event | None = None,
    ) -> int:
        now = time.time()
        rows: list[tuple[object, ...]] = []
        children: list[tuple[str, str, int, int, float]] = []
        try:
            parent_device = os.stat(path, follow_symlinks=False).st_dev
        except OSError:
            parent_device = None
        iterator = os.scandir(path)
        with iterator:
            for item in iterator:
                if stop_event is not None and stop_event.is_set():
                    self.release_directory(path)
                    return 0
                try:
                    info = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                kind = entry_kind(info.st_mode)
                absolute = os.path.abspath(item.path)
                suffix = Path(item.name).suffix.lower() or None
                rows.append(
                    (
                        absolute, _path_key(absolute), os.path.abspath(path),
                        _path_key(path), root_id, item.name, kind,
                        info.st_size if kind == "file" else 0, info.st_mtime,
                        is_hidden(item.name, info), suffix, now,
                    )
                )
                if (
                    kind == "dir"
                    and not _should_skip_directory(item.name)
                    and (parent_device is None or info.st_dev == parent_device)
                ):
                    children.append((absolute, root_id, depth + 1, priority - 1, now))
        self._delete_missing_children(path, {str(row[1]) for row in rows}, now)
        with self._connect() as db:
            root_active = db.execute(
                "SELECT 1 FROM filesystem_roots WHERE id=?", (root_id,)
            ).fetchone() is not None
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
                     THEN 'not_indexed' ELSE filesystem_entries.content_state END,
                   embedding_state=CASE WHEN filesystem_entries.modified_at IS NOT excluded.modified_at
                     THEN 'not_indexed' ELSE filesystem_entries.embedding_state END""",
                rows,
            )
            if root_active:
                db.executemany(
                        """INSERT INTO filesystem_scan_queue(path,root_id,depth,priority,updated_at)
                       SELECT ?,?,?,?,? WHERE NOT EXISTS
                       (SELECT 1 FROM filesystem_scanned_dirs WHERE path=? AND scanned_at>=?)
                       ON CONFLICT(path) DO UPDATE SET
                       priority=MAX(priority,excluded.priority),updated_at=excluded.updated_at""",
                    [(*child, child[0], now - 21600.0) for child in children],
                )
            db.execute(
                "INSERT OR REPLACE INTO filesystem_scanned_dirs(path,scanned_at) VALUES(?,?)",
                (os.path.abspath(path), now),
            )
            db.execute("DELETE FROM filesystem_scan_queue WHERE path=?", (os.path.abspath(path),))
            db.execute(
                "INSERT OR REPLACE INTO filesystem_index_state(key,value) VALUES('last_scan_at',?)",
                (str(now),),
            )
        return len(rows)

    def complete_directory(self, path: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO filesystem_scanned_dirs(path,scanned_at) VALUES(?,?)",
                (os.path.abspath(path), time.time()),
            )
            db.execute("DELETE FROM filesystem_scan_queue WHERE path=?", (os.path.abspath(path),))

    def release_directory(self, path: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE filesystem_scan_queue SET claimed_at=NULL,updated_at=? WHERE path=?",
                (time.time(), os.path.abspath(path)),
            )

    def fail_directory(self, path: str, error: OSError) -> float:
        """Release a failed scan with bounded retry and durable diagnostics."""
        absolute = os.path.abspath(path)
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                "SELECT consecutive_failures FROM filesystem_scan_queue WHERE path=?",
                (absolute,),
            ).fetchone()
            consecutive_failures = (
                max(0, int(row["consecutive_failures"])) + 1 if row is not None else 1
            )
            retry_delay = min(
                6 * 60 * 60,
                30 * (2 ** min(consecutive_failures - 1, 10)),
            )
            db.execute(
                """UPDATE filesystem_scan_queue SET claimed_at=NULL,
                   consecutive_failures=?,
                   last_error_kind=?,last_error=?,last_failed_at=?,next_retry_at=?,updated_at=?
                   WHERE path=?""",
                (
                    consecutive_failures,
                    type(error).__name__,
                    str(error)[:500],
                    now,
                    now + retry_delay,
                    now,
                    absolute,
                ),
            )
        return float(retry_delay)

    def _delete_missing_children(self, parent: str, current_keys: set[str], scan_started: float) -> None:
        parent_key = _path_key(parent)
        with self._connect() as db:
            existing = {
                str(row["path_key"]): str(row["path"])
                for row in db.execute(
                    "SELECT path,path_key FROM filesystem_entries WHERE parent_key=? AND indexed_at<?",
                    (parent_key, scan_started),
                )
            }
        for stale_key in set(existing) - current_keys:
            self.delete_path(existing[stale_key])

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
                     THEN 'not_indexed' ELSE filesystem_entries.content_state END,
                   embedding_state=CASE WHEN filesystem_entries.modified_at IS NOT excluded.modified_at
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
        key = _path_key(path)
        with self._connect() as db:
            rows = db.execute(
                """WITH RECURSIVE descendants(path_key) AS (
                     SELECT ? UNION ALL
                     SELECT e.path_key FROM filesystem_entries e
                     JOIN descendants d ON e.parent_key=d.path_key
                   )
                   SELECT e.path,e.path_key FROM filesystem_entries e
                   JOIN descendants d ON d.path_key=e.path_key""",
                (key,),
            ).fetchall()
            paths = [str(row["path"]) for row in rows]
            keys = [str(row["path_key"]) for row in rows]
            if paths:
                db.executemany("DELETE FROM filesystem_content_fts WHERE path=?", [(value,) for value in paths])
                db.executemany("DELETE FROM filesystem_content WHERE path_key=?", [(value,) for value in keys])
                db.executemany("DELETE FROM filesystem_embeddings WHERE path_key=?", [(value,) for value in keys])
                db.executemany("DELETE FROM filesystem_entries WHERE path_key=?", [(value,) for value in reversed(keys)])
            queued = [str(row["path"]) for row in db.execute("SELECT path FROM filesystem_scan_queue")]
            stale_queue = [queued_path for queued_path in queued if _is_same_or_descendant(queued_path, path)]
            db.executemany("DELETE FROM filesystem_scan_queue WHERE path=?", [(value,) for value in stale_queue])
            scanned = [str(row["path"]) for row in db.execute("SELECT path FROM filesystem_scanned_dirs")]
            stale_scanned = [scanned_path for scanned_path in scanned if _is_same_or_descendant(scanned_path, path)]
            db.executemany("DELETE FROM filesystem_scanned_dirs WHERE path=?", [(value,) for value in stale_scanned])

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
                    + root_join + "WHERE filesystem_entries_fts MATCH ?" +
                    " ORDER BY bm25(filesystem_entries_fts), length(e.path) LIMIT ? OFFSET ?"
                )
                params = [*params_root, _fts_query(query), limit, offset]
            else:
                sql = (
                    root_cte + "SELECT e.* FROM filesystem_entries e " + root_join + "WHERE "
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

    def scan_status(self, *, failure_limit: int = 25) -> dict[str, object]:
        """Return queue progress without hiding inaccessible/failed locations."""
        now = time.time()
        lease_cutoff = now - 300.0
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
            row = db.execute("SELECT COUNT(*) AS count FROM filesystem_entries").fetchone()
            return int(row["count"])

    def next_content_candidate(
        self, extensions: tuple[str, ...], max_content_bytes: int
    ) -> tuple[str, float | None, int] | None:
        if not extensions:
            return None
        placeholders = ",".join("?" for _ in extensions)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute(
                "SELECT COALESCE(SUM(length(CAST(content AS BLOB))),0) AS bytes FROM filesystem_content"
            ).fetchone()
            if int(used["bytes"]) >= max_content_bytes:
                return None
            remaining = max_content_bytes - int(used["bytes"])
            row = db.execute(
                f"""SELECT path,modified_at,size FROM filesystem_entries
                    WHERE kind='file' AND content_state='not_indexed'
                    AND extension IN ({placeholders}) AND size BETWEEN 1 AND 1048576
                    AND size <= ?
                    ORDER BY indexed_at LIMIT 1""",
                (*extensions, remaining),
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
                    "UPDATE filesystem_entries SET content_state='not_indexed' WHERE path_key=?", (key,)
                )
                return False
            old = db.execute(
                "SELECT path,length(CAST(content AS BLOB)) AS bytes FROM filesystem_content WHERE path_key=?",
                (key,),
            ).fetchone()
            used = db.execute(
                "SELECT COALESCE(SUM(length(CAST(content AS BLOB))),0) AS bytes FROM filesystem_content"
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
            return True

    def mark_content_skipped(self, path: str, state: str = "skipped") -> None:
        with self._connect() as db:
            db.execute("UPDATE filesystem_entries SET content_state=? WHERE path_key=?", (state, _path_key(path)))

    def reset_quota_candidates(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE filesystem_entries SET content_state='not_indexed' WHERE content_state='quota'")

    def next_embedding_candidate(
        self, model: str, max_entries: int
    ) -> tuple[str, str, float | None, int] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) AS count FROM filesystem_embeddings WHERE model=?", (model,)
            ).fetchone()
            if int(count["count"]) >= max_entries:
                return None
            row = db.execute(
                """SELECT e.path,c.content,c.source_modified_at,c.source_size FROM filesystem_entries e
                   JOIN filesystem_content c ON c.path_key=e.path_key
                   LEFT JOIN filesystem_embeddings v ON v.path_key=e.path_key AND v.model=?
                   WHERE e.embedding_state='not_indexed'
                      OR (e.embedding_state='indexed' AND v.path IS NULL)
                   ORDER BY c.indexed_at LIMIT 1""",
                (model,),
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
                    "UPDATE filesystem_entries SET embedding_state='not_indexed' WHERE path_key=?", (key,)
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
            return True

    def mark_embedding_unavailable(self, path: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE filesystem_entries SET embedding_state='unavailable' WHERE path_key=?", (_path_key(path),)
            )

    def enrichment_counts(self) -> dict[str, int]:
        with self._connect() as db:
            content = db.execute("SELECT COUNT(*) AS count FROM filesystem_content").fetchone()
            embeddings = db.execute("SELECT COUNT(*) AS count FROM filesystem_embeddings").fetchone()
            content_bytes = db.execute(
                "SELECT COALESCE(SUM(length(CAST(content AS BLOB))),0) AS bytes FROM filesystem_content"
            ).fetchone()
        return {
            "content_entries": int(content["count"]),
            "embedding_entries": int(embeddings["count"]),
            "content_bytes": int(content_bytes["bytes"]),
        }

    def search_content(self, query: str, limit: int = 50) -> list[dict[str, object]]:
        expression = _fts_query(query)
        if not expression:
            return []
        with self._connect() as db:
            rows = db.execute(
                """SELECT path,snippet(filesystem_content_fts,1,'[',']',' … ',24) AS snippet
                   FROM filesystem_content_fts WHERE filesystem_content_fts MATCH ?
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
                   WHERE v.model=? AND v.dimensions=?
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
