"""The Codex adapter: Codex threads as coding sessions, behind ONE interface.

It owns exactly two things — Codex's on-disk shape
(:mod:`app.services.coding_sessions.codex_session_index`) and the persisted
incremental index that keeps a 30 GB tree off the request path
(:mod:`app.services.coding_sessions.provider_index_store`). It asks the server
nothing: whether AI Matrx holds a thread is one join done for every provider in
:mod:`app.services.coding_sessions.overview`.

WHAT IT IS HONEST ABOUT. Codex has no pins and no archive, so those columns
are ``None`` rather than a fabricated ``false``. Its subagent threads — the
ones Codex starts for itself — are listed like any other session but marked,
because hiding them would make the count disagree with the server's.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.services.coding_sessions import codex_session_index as codex
from app.services.coding_sessions.continuation import continuation_hint
from app.services.coding_sessions.provider_index_store import (
    DEFAULT_CHUNK_SIZE,
    ProviderIndexSnapshot,
    ProviderIndexStore,
    chunks,
    index_report,
    plan_refresh,
)
from app.services.coding_sessions.session_providers import (
    ProviderListing,
    SessionSummary,
)

logger = get_logger()

PROVIDER = "codex"

# The same interval the Claude index uses: a screen open kicks a refresh, and
# a person hammering Refresh does not turn a 30 GB tree into a load test.
REFRESH_INTERVAL_SECONDS = 60.0


class CodexSessionProvider:
    """Codex's sessions, listed from Codex's own rollout files."""

    provider = PROVIDER

    def __init__(
        self,
        *,
        store: ProviderIndexStore | None = None,
        rollout_root: Path | None = None,
        thread_index: Path | None = None,
    ) -> None:
        self._store = store
        self._rollout_root = rollout_root
        self._thread_index = thread_index
        self._snapshot: tuple[int, ProviderIndexSnapshot] | None = None
        self._snapshot_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[Any] | None = None
        self._last_refresh_at = 0.0
        self._last_error: str | None = None

    # ── plumbing ────────────────────────────────────────────────────────
    @property
    def store(self) -> ProviderIndexStore:
        if self._store is None:
            self._store = ProviderIndexStore(PROVIDER)
        return self._store

    @property
    def rollout_root(self) -> Path:
        return (
            self._rollout_root
            if self._rollout_root is not None
            else codex.default_rollout_root()
        )

    @property
    def thread_index_path(self) -> Path:
        return (
            self._thread_index
            if self._thread_index is not None
            else codex.default_thread_index_path()
        )

    def refreshing(self) -> bool:
        if self._refresh_lock.locked():
            return True
        return self._refresh_task is not None and not self._refresh_task.done()

    # ── the interface ───────────────────────────────────────────────────
    async def listing(self) -> ProviderListing:
        """Codex's sessions from the persisted index. No disk walk, ever."""
        snapshot = await self.snapshot()
        self.start_refresh()
        entries = await asyncio.to_thread(self._merge, snapshot)
        rows = [self._row(entry) for entry in entries.values()]
        unreadable = sum(1 for entry in entries.values() if entry.unreadable)
        subagent = sum(1 for entry in entries.values() if entry.is_subagent)
        unnamed = sum(1 for entry in entries.values() if not entry.title)
        return ProviderListing(
            provider=PROVIDER,
            rows=rows,
            index=index_report(
                snapshot, refreshing=self.refreshing(), error=self._last_error
            ),
            totals={
                "sessions": len(rows),
                "rollout_files": int(snapshot.totals.get("files", 0)),
                "unreadable": unreadable,
                "subagent_threads": subagent,
                "unnamed": unnamed,
            },
            note=self._note(snapshot, unreadable=unreadable, unnamed=unnamed),
            supports_pins=False,
            supports_resume=True,
        )

    def start_refresh(self, *, minimum_interval: float | None = None) -> bool:
        """Kick a background refresh unless one is running or one just ran."""
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
            except Exception:  # noqa: BLE001 — refresh() already recorded it
                return

        try:
            self._refresh_task = asyncio.get_running_loop().create_task(_run())
        except RuntimeError:
            return False
        self._refresh_task.add_done_callback(lambda _: None)
        return True

    # ── index ───────────────────────────────────────────────────────────
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

    async def refresh(self, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict[str, Any]:
        """One incremental refresh. Single-flight, chunked, never blocking.

        The stat walk releases the GIL and each chunk of rollouts is reduced in
        a worker thread with an await between chunks, so the event loop keeps
        answering /health for the whole of a cold 30 GB build.
        """
        store = self.store
        async with self._refresh_lock:
            self._last_refresh_at = time.monotonic()
            started = time.monotonic()
            try:
                found, truncated = await asyncio.to_thread(
                    codex.walk_rollouts, self.rollout_root
                )
                changed, alive = await asyncio.to_thread(plan_refresh, found, store)
                for chunk in chunks(changed, chunk_size):
                    rows = await asyncio.to_thread(self._reduce, chunk)
                    await asyncio.to_thread(store.upsert, rows)
                    await asyncio.sleep(0)
                removed = await asyncio.to_thread(store.prune, alive)
                result = await asyncio.to_thread(
                    store.finalize,
                    changed=len(changed),
                    removed=removed,
                    truncated=truncated,
                    duration=time.monotonic() - started,
                )
            except Exception as exc:  # noqa: BLE001 — a failed refresh is a STATE
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("[codex_provider] index refresh failed")
                raise
            self._last_refresh_at = time.monotonic()
            self._last_error = None
            return result

    async def warm(self) -> None:
        """Refresh at engine start, before anyone asks."""
        await self.refresh()
        await self.snapshot()

    # ── reduction ───────────────────────────────────────────────────────
    @staticmethod
    def _reduce(chunk: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path, mtime_ns, size in chunk:
            entry = codex.read_rollout(Path(path), mtime_ns=mtime_ns, size=size)
            rows.append(
                {
                    "path": path,
                    "mtime_ns": mtime_ns,
                    "size": size,
                    "session_id": entry.session_id,
                    "last_activity_at": entry.last_activity_at,
                    "unreadable": entry.unreadable,
                    "facts": {
                        "session_id": entry.session_id,
                        "thread_id": entry.thread_id,
                        "title": entry.title,
                        "title_source": entry.title_source,
                        "cwd": entry.cwd,
                        "project": entry.project,
                        "started_at": entry.started_at,
                        "last_activity_at": entry.last_activity_at,
                        "entries": entry.entries,
                        "bytes": entry.bytes,
                        "originator": entry.originator,
                        "thread_source": entry.thread_source,
                        "cli_version": entry.cli_version,
                        "unreadable": entry.unreadable,
                        "unreadable_reason": entry.unreadable_reason,
                    },
                }
            )
        return rows

    def _merge(
        self, snapshot: ProviderIndexSnapshot
    ) -> dict[str, codex.CodexSessionEntry]:
        rows = [
            codex.CodexSessionEntry(
                session_id=str(facts.get("session_id") or ""),
                thread_id=str(facts.get("thread_id") or ""),
                path=Path(path),
                title=facts.get("title"),
                title_source=facts.get("title_source"),
                cwd=facts.get("cwd"),
                project=facts.get("project"),
                started_at=facts.get("started_at"),
                last_activity_at=int(facts.get("last_activity_at") or 0),
                entries=int(facts.get("entries") or 0),
                bytes=int(facts.get("bytes") or 0),
                originator=facts.get("originator"),
                thread_source=facts.get("thread_source"),
                cli_version=facts.get("cli_version"),
                unreadable=bool(facts.get("unreadable")),
                unreadable_reason=facts.get("unreadable_reason"),
            )
            for path, facts in snapshot.files
            if facts.get("session_id")
        ]
        names = codex.read_thread_names(self.thread_index_path)
        return codex.merge_entries(rows, names)

    @staticmethod
    def _row(entry: codex.CodexSessionEntry) -> SessionSummary:
        title = entry.title or f"Codex thread {entry.session_id[:8]}"
        return SessionSummary(
            provider=PROVIDER,
            session_id=entry.session_id,
            title=title,
            title_source=entry.title_source,
            project=entry.project,
            last_activity_at=entry.last_activity_at,
            bytes=entry.bytes,
            on_disk=entry.bytes > 0,
            # Codex has neither pins nor an archive: an absent concept is
            # None, never a false that reads as "not pinned".
            pinned=None,
            pinned_rank=None,
            category=None,
            archived=False,
            in_claude_sidebar=None,
            alias_ids=(entry.thread_id,) if entry.thread_id != entry.session_id else (),
            facts={
                "entries": entry.entries,
                "cwd": entry.cwd,
                "originator": entry.originator,
                "thread_source": entry.thread_source,
                "cli_version": entry.cli_version,
                "subagent": entry.is_subagent,
                "unreadable": entry.unreadable,
                "unreadable_reason": entry.unreadable_reason,
                "rollout_path": str(entry.path),
            },
        )

    def _note(
        self,
        snapshot: ProviderIndexSnapshot,
        *,
        unreadable: int,
        unnamed: int,
    ) -> str | None:
        parts: list[str] = []
        if snapshot.totals.get("truncated"):
            parts.append(
                f"Codex has more than {codex.MAX_ROLLOUT_FILES:,} rollout files on "
                "this Mac, so this list stops there and is incomplete."
            )
        if unreadable:
            parts.append(
                f"{unreadable:,} Codex rollout file(s) could not be read; each one's "
                "reason is on its row. They are counted here, never dropped."
            )
        if unnamed:
            # Measured on this Mac 2026-09-17: 1,831 sessions, of which 1,786
            # carry the name Codex itself recorded and 12 fall back to their
            # first prompt. The rest have neither, and are listed by id — said
            # out loud rather than left looking like sessions about nothing.
            parts.append(
                f"{unnamed:,} Codex thread(s) have no name in Codex and no readable "
                "first prompt, so they are listed by id."
            )
        return " ".join(parts) if parts else None


def continuation(session_id: str) -> dict[str, Any]:
    return continuation_hint(session_id, PROVIDER)


_PROVIDER: CodexSessionProvider | None = None


def get_codex_provider() -> CodexSessionProvider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = CodexSessionProvider()
    return _PROVIDER


__all__ = [
    "PROVIDER",
    "REFRESH_INTERVAL_SECONDS",
    "CodexSessionProvider",
    "get_codex_provider",
]
