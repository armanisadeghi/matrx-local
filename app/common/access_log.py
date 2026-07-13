"""Structured access logger for the Matrx Local engine.

Writes one JSON record per request to ``system/logs/access.log``.
Each record has:
    timestamp   – ISO-8601 UTC
    method      – HTTP verb
    path        – URL path (no query string)
    query       – query string (may be empty)
    origin      – value of the Origin header (where the call came from)
    user_agent  – abbreviated UA string
    status      – HTTP response status code
    duration_ms – round-trip time in milliseconds

The file is consumed by:
    GET /logs/access        – last-N snapshot (JSON)
    GET /logs/access/stream – SSE live-push stream
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque

from app.config import LOG_DIR, MAX_LOG_FILE_SIZE

ACCESS_LOG_PATH = Path(LOG_DIR) / "access.log"
os.makedirs(ACCESS_LOG_PATH.parent, exist_ok=True)

# Size-based rotation: access.log grew to 560 MB in the field (it had NO
# rotation while system.log rotates at 10 MB) and contributed to a macOS
# disk-writes resource exception against the engine. Same size cap as
# system.log, two backups (access.log.1, access.log.2) ≈ 30 MB worst case.
_MAX_BYTES = MAX_LOG_FILE_SIZE
_BACKUPS = 2

# Cached current file size so rotation doesn't stat() on every request.
# Seeded from disk at import; drift (e.g. external truncation) only delays
# or advances a rotation by one cycle — harmless.
try:
    _size = ACCESS_LOG_PATH.stat().st_size
except OSError:
    _size = 0

# In-memory ring buffer so the SSE stream can push entries without a file read.
_RING: Deque[dict] = deque(maxlen=500)

# Subscribers waiting for new entries (each is an asyncio.Queue).
_SUBSCRIBERS: list[asyncio.Queue] = []


def _rotate() -> None:
    """access.log → access.log.1 → access.log.2 (oldest dropped).

    A file far above the cap predates rotation entirely (the field one was
    560 MB). Archiving it as .1 would RETAIN the disk pressure for weeks —
    it only ages out after two more full 10 MB cycles — so oversized legacy
    files are deleted outright instead of archived.
    """
    try:
        legacy_oversized = ACCESS_LOG_PATH.stat().st_size > 2 * _MAX_BYTES
    except OSError:
        legacy_oversized = False
    if legacy_oversized:
        ACCESS_LOG_PATH.unlink(missing_ok=True)
        return
    for i in range(_BACKUPS, 1, -1):
        src = ACCESS_LOG_PATH.with_name(f"{ACCESS_LOG_PATH.name}.{i - 1}")
        if src.exists():
            os.replace(src, ACCESS_LOG_PATH.with_name(f"{ACCESS_LOG_PATH.name}.{i}"))
    if ACCESS_LOG_PATH.exists():
        os.replace(ACCESS_LOG_PATH, ACCESS_LOG_PATH.with_name(f"{ACCESS_LOG_PATH.name}.1"))


def _write_entry(entry: dict) -> None:
    """Append one JSON-line to access.log (rotating at the size cap) and
    notify SSE subscribers."""
    global _size
    _RING.append(entry)
    line = json.dumps(entry, default=str)
    try:
        if _size >= _MAX_BYTES:
            _rotate()
            _size = 0
        with open(ACCESS_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        _size += len(line) + 1
    except OSError:
        pass  # never crash the request pipeline over a log write

    dead: list[asyncio.Queue] = []
    for q in _SUBSCRIBERS:
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _SUBSCRIBERS.remove(q)


def record(
    *,
    method: str,
    path: str,
    query: str,
    origin: str,
    user_agent: str,
    status: int,
    duration_ms: float,
) -> None:
    """Build an access entry and persist it."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "query": query,
        "origin": origin or "—",
        "user_agent": user_agent[:120] if user_agent else "—",
        "status": status,
        "duration_ms": round(duration_ms, 1),
    }
    _write_entry(entry)


def recent(n: int = 100) -> list[dict]:
    """Return the last *n* entries from the in-memory ring (fast path).

    Falls back to reading the file if the ring is empty (e.g. first boot).
    """
    if _RING:
        entries = list(_RING)
        return entries[-n:]

    # Cold-start: parse tail of the file.
    try:
        with open(ACCESS_LOG_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        parsed = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return parsed
    except FileNotFoundError:
        return []


def subscribe() -> asyncio.Queue:
    """Return a new asyncio.Queue that receives future access entries."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _SUBSCRIBERS.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    """Remove a subscriber queue."""
    try:
        _SUBSCRIBERS.remove(q)
    except ValueError:
        pass
