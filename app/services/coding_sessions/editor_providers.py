"""Cursor and VS Code — the two providers whose answers are partly "no".

Both are editors rather than CLIs, so neither keeps a transcript file the way
Claude Code and Codex do. What each one actually exposes on a Mac was measured
on 2026-09-17, and the difference between them is the whole content of this
module: Cursor keeps a real, cheap, listable chat ledger; VS Code keeps
nothing that belongs to AI Matrx at all. Law 4 binds the second case as much as
the first — a provider that lists nothing must say WHY, with the reason on the
screen, not leave an empty tab that reads like a bug.

CURSOR — LISTABLE, WITH NAMED GAPS.
``~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`` has a
real relational table, ``composerHeaders`` (4,570 rows here), with one row per
chat: ``composerId``, ``workspaceId``, ``createdAt``, ``recency``,
``isArchived``, ``isSubagent``, and a small ``value`` JSON whose
``workspaceIdentifier.uri.fsPath`` is the folder the chat ran in. Listing all
of it costs **0.397 s**. Titles are NOT in that table; they are in Cursor's own
search index, ``conversation-search.db`` table ``conversations`` (``id``,
``title``, ``updated_at``, ``is_archived``), so the list is the header table
left-joined to that.

What Cursor does not give, and this module therefore does not claim:

* **Last activity is ``recency``, not ``lastUpdatedAt``** — the latter is NULL
  in 1,726 of 4,570 rows, so using it would silently date a third of the list
  to nothing.
* **No message count.** Only the big ``composerData:`` blobs know it, and
  reading all 4,735 of them measured **3 m 36 s** against a 27 GB file. A
  count that costs three and a half minutes is not a column; it is absent and
  said to be absent.
* **No size.** A Cursor chat is rows in a shared database, not a file, so
  ``bytes`` is None — never a 0 that reads as "empty".
* **No pins.** A whole-``ItemTable`` scan for pin/star/favourite keys returned
  only VS Code panel layout. Cursor has no chat pin concept, so the column is
  None rather than a false that reads as "not pinned".
* **No folder for some chats.** 253 of 4,570 are empty-window chats whose
  ``workspaceId`` is a bare timestamp and whose workspace folder does not
  exist. Those rows say so instead of borrowing a neighbour's folder.
* **No proven native resume.** ``cursor --help`` has no resume flag. The
  separate ``cursor-agent`` binary does (``--resume [chatId]``) but keeps its
  own store at ``~/.cursor/chats`` with 4 chats — a different id space from
  the 4,735 IDE chats — so this module does not offer a resume command it
  cannot stand behind.

The per-workspace ``workspaceStorage/<hash>/state.vscdb`` files are
deliberately NOT read: under the read-only rule 7 of 28 are WAL-only and
answer "no such table", and ``composerHeaders.workspaceId`` already carries
the same mapping from one file.

VS CODE — NOT LISTABLE, AND THAT IS THE FINDING.
``~/Library/Application Support/Code`` exists, but it holds no AI Matrx record
of any kind: zero paths matching ``matrx`` anywhere under it, no AI Matrx
extension in ``~/.vscode/extensions``, and no VS Code spool in this engine.
The VS Code sessions AI Matrx holds arrived through the provider-neutral hook
straight from the editor, and a delivered outbox row is deleted by design — so
this Mac keeps no local copy to list. The adapter says exactly that, and the
overview fills the tab from the SERVER's inventory instead, marked as not on
this Mac. What VS Code does keep is other vendors' chat state (Copilot's
``globalStorage/github.copilot-chat``, its own ``chatSessions`` folders); that
is not ours and is not read.

EVERY DATABASE HERE IS OPENED READ-ONLY, BY URI. Arman's editors' state is
his: the connections are ``file:...?mode=ro``, nothing is ever written, and no
PRAGMA that could touch the file is issued.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.common.system_logger import get_logger
from app.services.coding_sessions.provider_index_store import (
    ProviderIndexSnapshot,
    ProviderIndexStore,
    index_report,
)
from app.services.coding_sessions.session_providers import (
    ProviderListing,
    SessionSummary,
)

logger = get_logger()

CURSOR_PROVIDER = "cursor"
VSCODE_PROVIDER = "vscode"

REFRESH_INTERVAL_SECONDS = 60.0

# Cursor's chats are rows, not files, so "how big is it" has no answer and the
# count of turns costs 3 m 36 s. Both are reported as absent, by name.
CURSOR_ABSENT_FACTS = (
    "Cursor does not expose a size or a message count for a chat without "
    "reading its whole 27 GB store, so those two columns are blank for every "
    "Cursor row rather than guessed."
)


def _app_support_dir(app: str, env: str) -> Path:
    """Where an editor keeps its user state, per platform."""
    configured = os.environ.get(env)
    if configured:
        return Path(configured).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support" / app
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else home / "AppData/Roaming") / app
    config_home = os.environ.get("XDG_CONFIG_HOME")
    return (Path(config_home) if config_home else home / ".config") / app


def cursor_state_dir() -> Path:
    return _app_support_dir("Cursor", "MATRX_CURSOR_STATE_DIR")


def vscode_state_dir() -> Path:
    return _app_support_dir("Code", "MATRX_VSCODE_STATE_DIR")


def _read_only_connection(path: Path) -> sqlite3.Connection:
    """Open somebody else's database WITHOUT being able to change it.

    ``mode=ro`` is the guarantee, not a convention: SQLite refuses every write
    on the connection, so a bug here cannot damage Arman's editor state.
    """
    uri = f"file:{quote(str(path))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


# ── Cursor ──────────────────────────────────────────────────────────────────


class CursorSessionProvider:
    """Cursor's chats, from Cursor's own header table and search index."""

    provider = CURSOR_PROVIDER

    def __init__(
        self,
        *,
        store: ProviderIndexStore | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._state_dir = state_dir
        self._snapshot: tuple[int, ProviderIndexSnapshot] | None = None
        self._snapshot_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[Any] | None = None
        self._last_refresh_at = 0.0
        self._last_error: str | None = None
        self._last_blocker: str | None = None

    @property
    def store(self) -> ProviderIndexStore:
        if self._store is None:
            self._store = ProviderIndexStore(CURSOR_PROVIDER)
        return self._store

    @property
    def state_dir(self) -> Path:
        return self._state_dir if self._state_dir is not None else cursor_state_dir()

    @property
    def headers_db(self) -> Path:
        return self.state_dir / "User/globalStorage/state.vscdb"

    @property
    def search_db(self) -> Path:
        return self.state_dir / "User/globalStorage/conversation-search.db"

    def refreshing(self) -> bool:
        if self._refresh_lock.locked():
            return True
        return self._refresh_task is not None and not self._refresh_task.done()

    async def listing(self) -> ProviderListing:
        snapshot = await self.snapshot()
        self.start_refresh()
        rows = [self._row(facts) for _path, facts in snapshot.files if facts.get("id")]
        untitled = sum(1 for _p, f in snapshot.files if not f.get("title"))
        no_folder = sum(1 for _p, f in snapshot.files if not f.get("project_path"))
        subagent = sum(1 for _p, f in snapshot.files if f.get("subagent"))
        return ProviderListing(
            provider=CURSOR_PROVIDER,
            rows=rows,
            index=index_report(
                snapshot, refreshing=self.refreshing(), error=self._last_error
            ),
            totals={
                "sessions": len(rows),
                "untitled": untitled,
                "without_folder": no_folder,
                "subagent_chats": subagent,
            },
            note=self._note(snapshot, untitled=untitled, no_folder=no_folder),
            supports_pins=False,
            # cursor-agent's --resume exists but addresses a different store
            # (4 chats) than the IDE chats listed here. Offering it would be a
            # command that does not reopen the row a person clicked.
            supports_resume=False,
        )

    def start_refresh(self, *, minimum_interval: float | None = None) -> bool:
        if self.refreshing():
            return True
        interval = (
            REFRESH_INTERVAL_SECONDS if minimum_interval is None else minimum_interval
        )
        if self._last_refresh_at and time.monotonic() - self._last_refresh_at < interval:
            return False

        async def _run() -> None:
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001 — refresh() recorded it
                return

        try:
            self._refresh_task = asyncio.get_running_loop().create_task(_run())
        except RuntimeError:
            return False
        self._refresh_task.add_done_callback(lambda _: None)
        return True

    async def snapshot(self) -> ProviderIndexSnapshot:
        store = self.store
        revision = await asyncio.to_thread(store.revision)
        cached = self._snapshot
        if cached is not None and cached[0] == revision:
            return cached[1]
        async with self._snapshot_lock:
            cached = self._snapshot
            if cached is not None and cached[0] == revision:
                return cached[1]
            snapshot = await asyncio.to_thread(store.load)
            self._snapshot = (snapshot.revision, snapshot)
            return snapshot

    async def refresh(self) -> dict[str, Any]:
        """One read of Cursor's ledger into the persisted index.

        The read is 0.4 s and happens in a worker thread, so the event loop
        keeps answering while it runs and the request path only ever reads
        SQLite rows this engine wrote.
        """
        store = self.store
        async with self._refresh_lock:
            self._last_refresh_at = time.monotonic()
            started = time.monotonic()
            try:
                rows, blocker = await asyncio.to_thread(self._read_chats)
                self._last_blocker = blocker
                if blocker is not None:
                    # Nothing readable: publish the state, never an empty list
                    # that looks like "Cursor has no chats".
                    result = await asyncio.to_thread(
                        store.finalize,
                        changed=0,
                        removed=0,
                        truncated=False,
                        duration=time.monotonic() - started,
                    )
                    result["blocked"] = blocker
                    self._last_refresh_at = time.monotonic()
                    return result
                await asyncio.to_thread(store.upsert, rows)
                removed = await asyncio.to_thread(
                    store.prune, [row["path"] for row in rows]
                )
                result = await asyncio.to_thread(
                    store.finalize,
                    changed=len(rows),
                    removed=removed,
                    truncated=False,
                    duration=time.monotonic() - started,
                )
            except Exception as exc:  # noqa: BLE001 — a failed refresh is a STATE
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("[cursor_provider] chat index refresh failed")
                raise
            self._last_refresh_at = time.monotonic()
            self._last_error = None
            return result

    def _read_chats(self) -> tuple[list[dict[str, Any]], str | None]:
        """Cursor's header table left-joined to its own title index.

        Returns ``(rows, blocker)``; a blocker is a sentence naming what could
        not be read, because "Cursor is not installed" and "Cursor has no
        chats" must never render the same.
        """
        headers = self.headers_db
        if not headers.is_file():
            return [], (
                "Cursor's chat store is not on this Mac "
                f"({headers}), so there is nothing to list."
            )
        titles = self._read_titles()
        rows: list[dict[str, Any]] = []
        try:
            with _read_only_connection(headers) as connection:
                cursor = connection.execute(
                    """SELECT composerId, workspaceId, createdAt, recency,
                              isArchived, isSubagent,
                              json_extract(value, '$.workspaceIdentifier.uri.fsPath')
                                AS folder
                       FROM composerHeaders"""
                )
                for row in cursor:
                    composer_id = row["composerId"]
                    if not isinstance(composer_id, str) or not composer_id:
                        continue
                    folder = row["folder"] if isinstance(row["folder"], str) else None
                    title, title_updated, title_archived = titles.get(
                        composer_id, (None, None, None)
                    )
                    recency = _int(row["recency"]) or _int(row["createdAt"]) or 0
                    archived = bool(_int(row["isArchived"])) or bool(title_archived)
                    rows.append(
                        {
                            "path": f"composer:{composer_id}",
                            "mtime_ns": recency * 1_000_000,
                            "size": 0,
                            "session_id": composer_id,
                            "last_activity_at": max(recency, title_updated or 0),
                            "unreadable": False,
                            "facts": {
                                "id": composer_id,
                                "title": title,
                                "created_at": _int(row["createdAt"]),
                                "last_activity_at": max(recency, title_updated or 0),
                                "project_path": folder,
                                "workspace_id": row["workspaceId"],
                                "archived": archived,
                                "subagent": bool(_int(row["isSubagent"])),
                            },
                        }
                    )
        except sqlite3.Error as exc:
            return [], (
                "Cursor's chat store could not be read "
                f"({type(exc).__name__}: {exc}). It is opened read-only; a "
                "locked or in-migration store resolves once Cursor settles."
            )
        return rows, None

    def _read_titles(self) -> dict[str, tuple[str | None, int | None, bool | None]]:
        """Cursor's own titles. Absent is absent — never a borrowed name."""
        path = self.search_db
        out: dict[str, tuple[str | None, int | None, bool | None]] = {}
        if not path.is_file():
            return out
        try:
            with _read_only_connection(path) as connection:
                for row in connection.execute(
                    "SELECT id, title, updated_at, is_archived FROM conversations"
                ):
                    identity = row["id"]
                    if not isinstance(identity, str) or not identity:
                        continue
                    title = row["title"]
                    out[identity] = (
                        title.strip() if isinstance(title, str) and title.strip() else None,
                        _int(row["updated_at"]),
                        bool(_int(row["is_archived"])),
                    )
        except sqlite3.Error as exc:
            logger.warning(
                "[cursor_provider] Cursor's title index could not be read (%s) — "
                "chats are listed by id until it can be. Remedy: none needed; it "
                "is rebuilt by Cursor itself.",
                exc,
            )
        return out

    @staticmethod
    def _row(facts: dict[str, Any]) -> SessionSummary:
        identity = str(facts.get("id") or "")
        title = facts.get("title") or f"Cursor chat {identity[:8]}"
        folder = facts.get("project_path")
        return SessionSummary(
            provider=CURSOR_PROVIDER,
            session_id=identity,
            title=title,
            title_source="cursor_title" if facts.get("title") else None,
            project=Path(folder).name if isinstance(folder, str) and folder else None,
            last_activity_at=int(facts.get("last_activity_at") or 0),
            # A chat is rows in a shared database: there is no size to report.
            bytes=None,
            on_disk=True,
            pinned=None,
            pinned_rank=None,
            category=None,
            archived=bool(facts.get("archived")),
            in_claude_sidebar=None,
            facts={
                "entries": None,
                "cwd": folder,
                "workspace_id": facts.get("workspace_id"),
                "subagent": bool(facts.get("subagent")),
                "created_at": facts.get("created_at"),
            },
        )

    def _note(
        self, snapshot: ProviderIndexSnapshot, *, untitled: int, no_folder: int
    ) -> str:
        if self._last_blocker:
            return self._last_blocker
        parts = [CURSOR_ABSENT_FACTS]
        if untitled:
            parts.append(
                f"{untitled:,} Cursor chat(s) have no name in Cursor itself and are "
                "listed by id."
            )
        if no_folder:
            parts.append(
                f"{no_folder:,} were started in an empty Cursor window, so Cursor "
                "records no folder for them."
            )
        parts.append(
            "Cursor has no command that reopens one chat, so Continue offers the "
            "mirrored conversation in AI Matrx instead of a command."
        )
        return " ".join(parts)


# ── VS Code ─────────────────────────────────────────────────────────────────


class VSCodeSessionProvider:
    """VS Code, which keeps no AI Matrx session record on this Mac.

    This is a state, not a gap in the code: the adapter says what it looked
    for, what it found, and what would change the answer. The overview then
    fills the tab from the server's own inventory, marked as not on this Mac,
    so a VS Code session AI Matrx holds is still visible and still openable.
    """

    provider = VSCODE_PROVIDER

    def __init__(self, *, state_dir: Path | None = None, extensions_dir: Path | None = None) -> None:
        self._state_dir = state_dir
        self._extensions_dir = extensions_dir

    @property
    def state_dir(self) -> Path:
        return self._state_dir if self._state_dir is not None else vscode_state_dir()

    @property
    def extensions_dir(self) -> Path:
        return (
            self._extensions_dir
            if self._extensions_dir is not None
            else Path.home() / ".vscode/extensions"
        )

    def extension_present(self) -> bool:
        """Is the AI Matrx extension installed in VS Code at all?"""
        root = self.extensions_dir
        if not root.is_dir():
            return False
        try:
            return any(
                path.is_dir() and "aimatrx" in path.name.lower()
                for path in root.iterdir()
            )
        except OSError:
            return False

    async def listing(self) -> ProviderListing:
        installed = await asyncio.to_thread(self.extension_present)
        editor_present = await asyncio.to_thread(self.state_dir.is_dir)
        if installed:
            note = (
                "The AI Matrx extension is installed in VS Code, but it keeps no "
                "local record of a session: every turn goes straight to AI Matrx "
                "through the hook, and this Mac's copy of a delivered event is "
                "deleted once AI Matrx has it. The VS Code sessions below are the "
                "ones AI Matrx holds, not a scan of this Mac."
            )
        elif editor_present:
            note = (
                "VS Code is on this Mac but the AI Matrx extension is not installed "
                "in it, so nothing here records a VS Code session locally. The VS "
                "Code sessions below are the ones AI Matrx already holds. Install "
                "the AI Matrx extension in VS Code to mirror new ones."
            )
        else:
            note = (
                "VS Code is not installed on this Mac, so there is nothing local to "
                "list. The VS Code sessions below are the ones AI Matrx holds from "
                "another machine."
            )
        return ProviderListing(
            provider=VSCODE_PROVIDER,
            rows=[],
            # Not "cold": there is no index building behind this and never
            # will be. ``fresh`` with zero files read and a note is the honest
            # pair — a permanent "cold" would read as a load that never ends.
            index={
                "state": "fresh",
                "refreshing": False,
                "files_read": 0,
                "updated_at": None,
                "changed_files": 0,
                "duration_seconds": None,
                "limit_reached": False,
                "unreadable": 0,
                "error": None,
            },
            totals={"sessions": 0},
            note=note,
            supports_pins=False,
            supports_resume=False,
            lists_locally=False,
        )

    def start_refresh(self) -> bool:
        return False


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


_CURSOR: CursorSessionProvider | None = None
_VSCODE: VSCodeSessionProvider | None = None


def get_cursor_provider() -> CursorSessionProvider:
    global _CURSOR
    if _CURSOR is None:
        _CURSOR = CursorSessionProvider()
    return _CURSOR


def get_vscode_provider() -> VSCodeSessionProvider:
    global _VSCODE
    if _VSCODE is None:
        _VSCODE = VSCodeSessionProvider()
    return _VSCODE


__all__ = [
    "CURSOR_PROVIDER",
    "VSCODE_PROVIDER",
    "CursorSessionProvider",
    "VSCodeSessionProvider",
    "cursor_state_dir",
    "get_cursor_provider",
    "get_vscode_provider",
    "vscode_state_dir",
]
