"""Resource-bounded, mutation-stable direct directory paging."""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from app.services.filesystem.models import DirectoryPage, FileEntry
from app.services.filesystem.roots import normalize_path_key

logger = logging.getLogger(__name__)

DIRECTORY_PAGE_SCAN_BUDGET = 20_000
MAX_DIRECTORY_SNAPSHOT_ENTRIES = 1_000_000
MAX_DIRECTORY_LIST_SESSIONS = 4
MAX_DIRECTORY_LIST_TOTAL_ENTRIES = 2_000_000
MAX_DIRECTORY_SNAPSHOT_DISK_BYTES = 512 * 1024 * 1024
MAX_DIRECTORY_LIST_TOTAL_DISK_BYTES = (
    MAX_DIRECTORY_LIST_SESSIONS * MAX_DIRECTORY_SNAPSHOT_DISK_BYTES
)
DIRECTORY_LIST_SESSION_TTL_SECONDS = 300.0
SEARCH_SESSION_TTL_SECONDS = 300.0
MAX_SEARCH_SESSIONS = 4
MAX_SEARCH_SNAPSHOT_ENTRIES = 50_000
MAX_SEARCH_TOTAL_ENTRIES = 100_000


class DirectoryPagingError(ValueError):
    """A cursor or resource limit made a directory page impossible."""


def _text_key(value: str) -> bytes:
    """Make every OS-provided filename SQLite-safe, including surrogate escapes."""
    return value.encode("utf-8", "surrogatepass")


class _DirectorySnapshot:
    """Incrementally snapshot one directory, then keyset-page its stable order."""

    def __init__(self, path: str, *, show_hidden: bool) -> None:
        self.path = path
        self.show_hidden = show_hidden
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="matrx-directory-page-"
        )
        self.snapshot_path = Path(self._temporary_directory.name) / "snapshot.sqlite3"
        self._db = sqlite3.connect(self.snapshot_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=OFF")
        self._db.execute("PRAGMA synchronous=OFF")
        self._db.execute("PRAGMA temp_store=FILE")
        page_size = int(self._db.execute("PRAGMA page_size").fetchone()[0])
        self._db.execute(
            f"PRAGMA max_page_count={MAX_DIRECTORY_SNAPSHOT_DISK_BYTES // page_size}"
        )
        self._db.execute(
            """CREATE TABLE entries (
                   sort_kind INTEGER NOT NULL,
                   sort_name BLOB NOT NULL,
                   name BLOB NOT NULL,
                   path BLOB NOT NULL,
                   payload TEXT NOT NULL,
                   PRIMARY KEY (sort_kind, sort_name, name, path)
               ) WITHOUT ROWID"""
        )
        self._iterator: os.ScandirIterator[str] | None = None
        self._scan_complete = False
        self._entry_count = 0
        self._after: tuple[int, bytes, bytes, bytes] | None = None
        self._closed = False

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def scan_complete(self) -> bool:
        return self._scan_complete

    @property
    def disk_bytes(self) -> int:
        try:
            return sum(
                item.stat().st_size
                for item in Path(self._temporary_directory.name).iterdir()
            )
        except OSError:
            return 0

    def _open_iterator(self) -> os.ScandirIterator[str]:
        if self._iterator is None:
            self._iterator = os.scandir(self.path)
        return self._iterator

    def _scan(self, budget: int) -> None:
        if self._scan_complete:
            return
        iterator = self._open_iterator()
        work = 0
        try:
            while work < budget:
                try:
                    raw_entry = next(iterator)
                except StopIteration:
                    iterator.close()
                    self._iterator = None
                    self._scan_complete = True
                    break
                work += 1
                entry = FileEntry.from_dir_entry(raw_entry)
                if not self.show_hidden and entry.hidden:
                    continue
                if self._entry_count >= MAX_DIRECTORY_SNAPSHOT_ENTRIES:
                    raise DirectoryPagingError(
                        "Directory contains more than "
                        f"{MAX_DIRECTORY_SNAPSHOT_ENTRIES:,} visible entries; "
                        "browse a narrower directory."
                    )
                values = (
                    0 if entry.kind == "dir" else 1,
                    _text_key(entry.name.casefold()),
                    _text_key(entry.name),
                    _text_key(entry.path),
                    json.dumps(entry.to_dict(), separators=(",", ":")),
                )
                self._db.execute(
                    "INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?)", values
                )
                self._entry_count += 1
        finally:
            self._db.commit()

    def page(
        self, limit: int, *, scan_budget: int
    ) -> tuple[tuple[FileEntry, ...], bool, int]:
        """Return entries, whether more work/results exist, and current total."""
        try:
            self._scan(scan_budget)
        except sqlite3.OperationalError as exc:
            if "full" in str(exc).lower():
                raise DirectoryPagingError(
                    "Directory snapshot exceeds its "
                    f"{MAX_DIRECTORY_SNAPSHOT_DISK_BYTES // (1024 * 1024)} MiB "
                    "disk limit; browse a narrower directory."
                ) from exc
            raise
        if not self._scan_complete:
            # Sorting cannot be honest until every sibling has been observed.
            return (), True, self._entry_count

        if self._after is None:
            rows = self._db.execute(
                """SELECT sort_kind,sort_name,name,path,payload
                     FROM entries
                 ORDER BY sort_kind,sort_name,name,path
                    LIMIT ?""",
                (limit + 1,),
            ).fetchall()
        else:
            rows = self._db.execute(
                """SELECT sort_kind,sort_name,name,path,payload
                     FROM entries
                    WHERE (sort_kind,sort_name,name,path) > (?, ?, ?, ?)
                 ORDER BY sort_kind,sort_name,name,path
                    LIMIT ?""",
                (*self._after, limit + 1),
            ).fetchall()

        visible_rows = rows[:limit]
        if visible_rows:
            last = visible_rows[-1]
            self._after = (last[0], last[1], last[2], last[3])
        entries = tuple(FileEntry(**json.loads(row[4])) for row in visible_rows)
        return entries, len(rows) > limit, self._entry_count

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._iterator is not None:
            self._iterator.close()
            self._iterator = None
        self._db.close()
        last_error: OSError | None = None
        for _attempt in range(3):
            try:
                self._temporary_directory.cleanup()
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.05)
        logger.warning(
            "Could not remove filesystem directory snapshot %s after retries: %s",
            self.snapshot_path.parent,
            last_error,
        )


class _DirectoryListSession:
    def __init__(self, path: str, *, show_hidden: bool, scope: tuple[str, bool]) -> None:
        self.scope = scope
        self.snapshot = _DirectorySnapshot(path, show_hidden=show_hidden)
        self.expires_at = 0.0

    @property
    def entry_count(self) -> int:
        return self.snapshot.entry_count

    @property
    def disk_bytes(self) -> int:
        return self.snapshot.disk_bytes

    def close(self) -> None:
        self.snapshot.close()


class DirectoryListSessionRegistry:
    """Own expiring, single-use paging sessions for one filesystem service."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, _DirectoryListSession] = {}
        self._active: set[_DirectoryListSession] = set()
        self._reserved_entries = 0
        self._accepting = True

    @staticmethod
    def _scope(path: str, show_hidden: bool) -> tuple[str, bool]:
        return normalize_path_key(path), show_hidden

    def start(self) -> None:
        with self._lock:
            self._accepting = True

    def _discard_expired_locked(self, now: float) -> list[_DirectoryListSession]:
        expired_tokens = [
            token
            for token, session in self._sessions.items()
            if session.expires_at <= now
        ]
        return [self._sessions.pop(token) for token in expired_tokens]

    @staticmethod
    def _close_all(sessions: list[_DirectoryListSession]) -> None:
        for session in sessions:
            try:
                session.close()
            except Exception:
                logger.warning(
                    "Filesystem directory snapshot cleanup failed; continuing",
                    exc_info=True,
                )

    def reap_expired(self) -> None:
        with self._lock:
            expired = self._discard_expired_locked(self._clock())
        self._close_all(expired)

    def _take_or_create(
        self, path: str, cursor: str | None, show_hidden: bool
    ) -> _DirectoryListSession:
        expected_scope = self._scope(path, show_hidden)
        retired: list[_DirectoryListSession] = []
        with self._lock:
            if not self._accepting:
                raise DirectoryPagingError("Filesystem service is stopping")
            retired.extend(self._discard_expired_locked(self._clock()))
            session = self._sessions.pop(cursor, None) if cursor is not None else None
            if session is not None:
                self._active.add(session)
            elif cursor is None:
                while self._sessions and (
                    len(self._sessions) + len(self._active)
                    >= MAX_DIRECTORY_LIST_SESSIONS
                ):
                    oldest = min(
                        self._sessions,
                        key=lambda token: self._sessions[token].expires_at,
                    )
                    retired.append(self._sessions.pop(oldest))
                if len(self._active) >= MAX_DIRECTORY_LIST_SESSIONS:
                    session = None
                else:
                    # Construct under the registry lock so concurrent first
                    # pages cannot all pass the session-cap check together.
                    session = _DirectoryListSession(
                        path, show_hidden=show_hidden, scope=expected_scope
                    )
                    self._active.add(session)
        self._close_all(retired)

        if session is None:
            if cursor is not None:
                raise DirectoryPagingError(
                    "Invalid, expired, or already-used cursor for this directory listing"
                )
            raise DirectoryPagingError(
                "Too many concurrent directory listings; finish an existing "
                "listing or let its cursor expire."
            )
        if session.scope != expected_scope:
            with self._lock:
                self._active.discard(session)
            session.close()
            raise DirectoryPagingError(
                "Cursor belongs to a different directory listing"
            )
        return session

    def _reserve_scan_capacity(
        self, session: _DirectoryListSession
    ) -> tuple[int, list[_DirectoryListSession]]:
        if session.snapshot.scan_complete:
            return 0, []
        retired: list[_DirectoryListSession] = []
        with self._lock:
            while self._sessions:
                current_entries = sum(
                    item.entry_count for item in self._sessions.values()
                ) + sum(item.entry_count for item in self._active)
                available = (
                    MAX_DIRECTORY_LIST_TOTAL_ENTRIES
                    - current_entries
                    - self._reserved_entries
                )
                if available > 0:
                    break
                oldest = min(
                    self._sessions,
                    key=lambda token: self._sessions[token].expires_at,
                )
                retired.append(self._sessions.pop(oldest))
            current_entries = sum(
                item.entry_count for item in self._sessions.values()
            ) + sum(item.entry_count for item in self._active)
            available = (
                MAX_DIRECTORY_LIST_TOTAL_ENTRIES
                - current_entries
                - self._reserved_entries
            )
            reservation = min(DIRECTORY_PAGE_SCAN_BUDGET, max(0, available))
            self._reserved_entries += reservation
        if reservation == 0:
            self._close_all(retired)
            raise DirectoryPagingError(
                "Concurrent directory snapshots reached the service-wide "
                f"{MAX_DIRECTORY_LIST_TOTAL_ENTRIES:,}-entry limit; finish "
                "another listing or let its cursor expire."
            )
        return reservation, retired

    def _release_scan_capacity(self, reservation: int) -> None:
        with self._lock:
            self._reserved_entries -= reservation

    def _store(self, session: _DirectoryListSession) -> str | None:
        evicted: list[_DirectoryListSession] = []
        with self._lock:
            self._active.discard(session)
            if not self._accepting:
                should_store = False
            else:
                should_store = True
                now = self._clock()
                evicted.extend(self._discard_expired_locked(now))
                while self._sessions and (
                    len(self._sessions) >= MAX_DIRECTORY_LIST_SESSIONS
                    or sum(item.entry_count for item in self._sessions.values())
                    + session.entry_count
                    > MAX_DIRECTORY_LIST_TOTAL_ENTRIES
                    or sum(item.disk_bytes for item in self._sessions.values())
                    + session.disk_bytes
                    > MAX_DIRECTORY_LIST_TOTAL_DISK_BYTES
                ):
                    oldest = min(
                        self._sessions,
                        key=lambda token: self._sessions[token].expires_at,
                    )
                    evicted.append(self._sessions.pop(oldest))
                session.expires_at = now + DIRECTORY_LIST_SESSION_TTL_SECONDS
                token = secrets.token_urlsafe(24)
                while token in self._sessions:
                    token = secrets.token_urlsafe(24)
                self._sessions[token] = session
        self._close_all(evicted)
        if not should_store:
            session.close()
            return None
        return token

    def page(
        self,
        path: str,
        *,
        cursor: str | None,
        limit: int,
        show_hidden: bool,
    ) -> DirectoryPage:
        session = self._take_or_create(path, cursor, show_hidden)
        reservation = 0
        try:
            reservation, retired = self._reserve_scan_capacity(session)
            self._close_all(retired)
            entries, has_more, total = session.snapshot.page(
                limit, scan_budget=reservation
            )
        except BaseException:
            if reservation:
                self._release_scan_capacity(reservation)
            with self._lock:
                self._active.discard(session)
            session.close()
            raise
        if reservation:
            self._release_scan_capacity(reservation)

        if has_more:
            next_cursor = self._store(session)
            if next_cursor is None:
                raise DirectoryPagingError("Filesystem service stopped during listing")
        else:
            with self._lock:
                self._active.discard(session)
            session.close()
            next_cursor = None
        return DirectoryPage(path, entries, next_cursor, total)

    def close(self) -> None:
        """Stop accepting work and close every idle snapshot.

        An in-flight worker owns its snapshot exclusively. It observes the
        stopped state before attempting to publish a new cursor and closes the
        snapshot itself, avoiding a cross-thread close race during shutdown.
        """
        with self._lock:
            self._accepting = False
            sessions = list(self._sessions.values())
            self._sessions.clear()
        self._close_all(sessions)

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)


class _SearchSession:
    def __init__(
        self,
        scope: tuple[str, str],
        entries: tuple[FileEntry, ...],
        *,
        source: str,
        index_complete: bool,
        truncated: bool,
    ) -> None:
        self.scope = scope
        self.entries = entries
        self.source = source
        self.index_complete = index_complete
        self.truncated = truncated
        self.offset = 0
        self.expires_at = 0.0


class SearchSessionRegistry:
    """Bounded, opaque, single-use snapshots for mutation-stable search paging."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, _SearchSession] = {}
        self._accepting = True

    def start(self) -> None:
        with self._lock:
            self._accepting = True

    def _discard_expired_locked(self, now: float) -> None:
        for token in [
            token
            for token, session in self._sessions.items()
            if session.expires_at <= now
        ]:
            self._sessions.pop(token, None)

    def reap_expired(self) -> None:
        with self._lock:
            self._discard_expired_locked(self._clock())

    def page(
        self,
        scope: tuple[str, str],
        *,
        cursor: str | None,
        limit: int,
        entries: tuple[FileEntry, ...] | None = None,
        source: str = "index",
        index_complete: bool = False,
        truncated: bool = False,
    ) -> tuple[tuple[FileEntry, ...], str | None, str, bool, bool]:
        with self._lock:
            if not self._accepting:
                raise ValueError("Filesystem service is stopping")
            now = self._clock()
            self._discard_expired_locked(now)
            if cursor is not None:
                session = self._sessions.pop(cursor, None)
                if session is None:
                    raise ValueError("Invalid, expired, or already-used search cursor")
                if session.scope != scope:
                    raise ValueError("Cursor belongs to a different filesystem search")
            else:
                if entries is None:
                    raise ValueError("Search entries are required for a new paging session")
                was_truncated = truncated or len(entries) > MAX_SEARCH_SNAPSHOT_ENTRIES
                session = _SearchSession(
                    scope,
                    entries[:MAX_SEARCH_SNAPSHOT_ENTRIES],
                    source=source,
                    index_complete=index_complete,
                    truncated=was_truncated,
                )

            page_entries = session.entries[session.offset : session.offset + limit]
            session.offset += len(page_entries)
            next_cursor: str | None = None
            if session.offset < len(session.entries):
                while self._sessions and (
                    len(self._sessions) >= MAX_SEARCH_SESSIONS
                    or sum(len(item.entries) for item in self._sessions.values())
                    + len(session.entries)
                    > MAX_SEARCH_TOTAL_ENTRIES
                ):
                    oldest = min(
                        self._sessions,
                        key=lambda token: self._sessions[token].expires_at,
                    )
                    self._sessions.pop(oldest, None)
                if len(session.entries) > MAX_SEARCH_TOTAL_ENTRIES:
                    raise ValueError("Filesystem search snapshot exceeds the service limit")
                session.expires_at = now + SEARCH_SESSION_TTL_SECONDS
                next_cursor = secrets.token_urlsafe(24)
                while next_cursor in self._sessions:
                    next_cursor = secrets.token_urlsafe(24)
                self._sessions[next_cursor] = session
            return (
                page_entries,
                next_cursor,
                session.source,
                session.index_complete,
                session.truncated,
            )

    def close(self) -> None:
        with self._lock:
            self._accepting = False
            self._sessions.clear()

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)
