"""File watch tools — monitor directories for file system changes."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.common.platform_ctx import CAPABILITIES
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

# Global watch registry (shared across sessions, keyed by watch_id)
_watches: dict[str, FileWatch] = {}
_watch_counter = 0


def shutdown_file_watches() -> None:
    """Cancel all active file watches. Called during app shutdown."""
    cancelled = 0
    for watch in list(_watches.values()):
        if watch._task and not watch._task.done():
            watch._task.cancel()
            cancelled += 1
        watch.is_active = False
        watch._stop_event.set()
    _watches.clear()
    if cancelled:
        logger.info("Cancelled %d file watch(es) during shutdown", cancelled)


@dataclass
class FileEvent:
    timestamp: float
    event_type: str  # created, modified, deleted, moved
    path: str
    is_directory: bool = False

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "path": self.path,
            "is_directory": self.is_directory,
        }


@dataclass
class FileWatch:
    watch_id: str
    path: str
    recursive: bool
    patterns: list[str]
    events: list[FileEvent] = field(default_factory=list)
    is_active: bool = True
    _task: asyncio.Task | None = field(default=None, repr=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    max_events: int = 1000

    def add_event(self, event: FileEvent) -> None:
        self.events.append(event)
        # Trim old events if buffer gets too large
        if len(self.events) > self.max_events:
            self.events = self.events[-self.max_events:]


async def tool_watch_directory(
    session: ToolSession,
    path: str,
    recursive: bool = True,
    patterns: list[str] | None = None,
) -> ToolResult:
    """Start watching a directory for file changes (create, modify, delete, move).

    Returns a watch_id to use with WatchEvents and StopWatch.
    Patterns filter by filename glob (e.g., ['*.py', '*.js']).
    """
    global _watch_counter

    resolved = session.resolve_path(path)
    if not os.path.isdir(resolved):
        return ToolResult(type=ToolResultType.ERROR, output=f"Not a directory: {resolved}")

    _watch_counter += 1
    watch_id = f"watch_{_watch_counter}"
    patterns = patterns or ["*"]

    watch = FileWatch(
        watch_id=watch_id,
        path=resolved,
        recursive=recursive,
        patterns=patterns,
    )

    if CAPABILITIES["has_watchfiles"]:
        import watchfiles

        async def _watcher():
            try:
                async for changes in watchfiles.awatch(
                    resolved,
                    recursive=recursive,
                    stop_event=watch._stop_event,
                ):
                    for change_type, change_path in changes:
                        # Filter by patterns
                        filename = os.path.basename(change_path)
                        if patterns != ["*"]:
                            import fnmatch
                            if not any(fnmatch.fnmatch(filename, p) for p in patterns):
                                continue

                        event_map = {
                            watchfiles.Change.added: "created",
                            watchfiles.Change.modified: "modified",
                            watchfiles.Change.deleted: "deleted",
                        }
                        watch.add_event(FileEvent(
                            timestamp=time.time(),
                            event_type=event_map.get(change_type, "unknown"),
                            path=change_path,
                            is_directory=os.path.isdir(change_path) if os.path.exists(change_path) else False,
                        ))
            except asyncio.CancelledError:
                pass
            except Exception:
                # watchfiles.awatch died unexpectedly — surface it instead of
                # letting the watch go quietly inactive.
                logger.warning(
                    "File watcher %s failed", watch_id, exc_info=True
                )
            finally:
                watch.is_active = False

        watch._task = asyncio.create_task(_watcher())
        _watches[watch_id] = watch

        return ToolResult(
            output=f"Watching {resolved} (id: {watch_id}, recursive: {recursive}, patterns: {patterns})",
            metadata={"watch_id": watch_id, "path": resolved},
        )

    else:
        # Fallback: polling-based watcher
        async def _poll_watcher():
            snapshot: dict[str, float] = {}
            # Initial snapshot
            for root, dirs, files in os.walk(resolved) if recursive else [(resolved, [], os.listdir(resolved))]:
                if not recursive:
                    root = resolved
                    files = [f for f in files if os.path.isfile(os.path.join(resolved, f))]
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        snapshot[fp] = os.path.getmtime(fp)
                    except OSError:
                        pass

            try:
                while not watch._stop_event.is_set():
                    try:
                        await asyncio.sleep(2)  # Poll every 2 seconds
                        current: dict[str, float] = {}
                        walker = os.walk(resolved) if recursive else [(resolved, [], os.listdir(resolved))]
                        for root, dirs, files in walker:
                            if not recursive:
                                root = resolved
                                files = [f for f in files if os.path.isfile(os.path.join(resolved, f))]
                            for f in files:
                                fp = os.path.join(root, f)
                                try:
                                    current[fp] = os.path.getmtime(fp)
                                except OSError:
                                    pass

                        # Check for new/modified files
                        for fp, mtime in current.items():
                            if fp not in snapshot:
                                watch.add_event(FileEvent(
                                    timestamp=time.time(), event_type="created",
                                    path=fp, is_directory=False,
                                ))
                            elif mtime != snapshot[fp]:
                                watch.add_event(FileEvent(
                                    timestamp=time.time(), event_type="modified",
                                    path=fp, is_directory=False,
                                ))

                        # Check for deleted files
                        for fp in snapshot:
                            if fp not in current:
                                watch.add_event(FileEvent(
                                    timestamp=time.time(), event_type="deleted",
                                    path=fp, is_directory=False,
                                ))

                        snapshot = current
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # A transient walk/stat error must not silently kill the
                        # watcher — log it and keep polling.
                        logger.warning(
                            "Polling watcher %s hit an error; continuing",
                            watch_id,
                            exc_info=True,
                        )
            finally:
                watch.is_active = False

        watch._task = asyncio.create_task(_poll_watcher())
        _watches[watch_id] = watch

        return ToolResult(
            output=f"Watching {resolved} via polling (id: {watch_id}, recursive: {recursive}). Install 'watchfiles' for better performance.",
            metadata={"watch_id": watch_id, "path": resolved},
        )


async def tool_watch_events(
    session: ToolSession,
    watch_id: str,
    since_seconds: float | None = None,
    limit: int = 100,
) -> ToolResult:
    """Get accumulated file change events from a directory watch.

    Optionally filter to events from the last N seconds.
    """
    watch = _watches.get(watch_id)
    if watch is None:
        active = [w.watch_id for w in _watches.values() if w.is_active]
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"Watch not found: {watch_id}. Active watches: {active or 'none'}",
        )

    events = watch.events
    if since_seconds is not None:
        cutoff = time.time() - since_seconds
        events = [e for e in events if e.timestamp >= cutoff]

    events = events[-limit:]

    if not events:
        status = "active" if watch.is_active else "stopped"
        return ToolResult(
            output=f"No events for {watch_id} ({status}, watching {watch.path})",
            metadata={"watch_id": watch_id, "status": status, "events": []},
        )

    lines = [f"Events for {watch_id} ({len(events)} events, watching {watch.path}):"]
    for event in events:
        ts = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
        lines.append(f"  [{ts}] {event.event_type:>10}  {event.path}")

    return ToolResult(
        output="\n".join(lines),
        metadata={
            "watch_id": watch_id,
            "events": [e.to_dict() for e in events],
            "total_events": len(watch.events),
        },
    )


async def tool_stop_watch(
    session: ToolSession,
    watch_id: str,
) -> ToolResult:
    """Stop watching a directory."""
    watch = _watches.get(watch_id)
    if watch is None:
        return ToolResult(type=ToolResultType.ERROR, output=f"Watch not found: {watch_id}")

    watch._stop_event.set()
    if watch._task and not watch._task.done():
        watch._task.cancel()
        try:
            await watch._task
        except asyncio.CancelledError:
            pass

    watch.is_active = False
    total_events = len(watch.events)
    del _watches[watch_id]

    return ToolResult(
        output=f"Stopped watching {watch.path} ({watch_id}). {total_events} events captured.",
    )
