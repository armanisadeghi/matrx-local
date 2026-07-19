"""Resource-bounded, mutation-stable direct directory paging."""

from __future__ import annotations

import json
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


DIRECTORY_PAGE_SCAN_BUDGET = 20_000
MAX_DIRECTORY_SNAPSHOT_ENTRIES = 1_000_000
MAX_DIRECTORY_LIST_SESSIONS = 8
MAX_DIRECTORY_LIST_TOTAL_ENTRIES = 2_000_000
DIRECTORY_LIST_SESSION_TTL_SECONDS = 300.0


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

    def page(self, limit: int) -> tuple[tuple[FileEntry, ...], bool, int]:
        """Return entries, whether more work/results exist, and current total."""
        self._scan(DIRECTORY_PAGE_SCAN_BUDGET)
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
        self._temporary_directory.cleanup()


class _DirectoryListSession:
    def __init__(self, path: str, *, show_hidden: bool, scope: tuple[str, bool]) -> None:
        self.scope = scope
        self.snapshot = _DirectorySnapshot(path, show_hidden=show_hidden)
        self.expires_at = 0.0

    @property
    def entry_count(self) -> int:
        return self.snapshot.entry_count

    def close(self) -> None:
        self.snapshot.close()


class DirectoryListSessionRegistry:
    """Own expiring, single-use paging sessions for one filesystem service."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, _DirectoryListSession] = {}
        self._active: set[_DirectoryListSession] = set()
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
            session.close()

    def reap_expired(self) -> None:
        with self._lock:
            expired = self._discard_expired_locked(self._clock())
        self._close_all(expired)

    def _take_or_create(
        self, path: str, cursor: str | None, show_hidden: bool
    ) -> _DirectoryListSession:
        expected_scope = self._scope(path, show_hidden)
        with self._lock:
            if not self._accepting:
                raise DirectoryPagingError("Filesystem service is stopping")
            expired = self._discard_expired_locked(self._clock())
            session = self._sessions.pop(cursor, None) if cursor is not None else None
            if session is not None:
                self._active.add(session)
        self._close_all(expired)

        if cursor is not None:
            if session is None:
                raise DirectoryPagingError(
                    "Invalid, expired, or already-used cursor for this directory listing"
                )
            if session.scope != expected_scope:
                with self._lock:
                    self._active.discard(session)
                session.close()
                raise DirectoryPagingError(
                    "Cursor belongs to a different directory listing"
                )
            return session

        session = _DirectoryListSession(
            path, show_hidden=show_hidden, scope=expected_scope
        )
        with self._lock:
            if not self._accepting:
                session.close()
                raise DirectoryPagingError("Filesystem service is stopping")
            self._active.add(session)
        return session

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
        try:
            entries, has_more, total = session.snapshot.page(limit)
        except BaseException:
            with self._lock:
                self._active.discard(session)
            session.close()
            raise

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
