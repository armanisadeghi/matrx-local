"""Codex's own session rollouts, read the way the screen needs them.

Arman, 2026-09-17: *"coding sessions is one feature"*. A Codex thread is a
coding session exactly like a Claude Code one, so it belongs in the same list,
with the same columns and the same cloud state. This module is the Codex half
of that: it turns Codex's on-disk rollout files into the same reduced facts
the Claude sidebar index gives (id, title, project, last activity, size).

WHERE CODEX KEEPS IT. One append-only JSONL per thread at
``$CODEX_HOME/sessions/<YYYY>/<MM>/<DD>/rollout-<stamp>-<thread id>.jsonl``
(``CODEX_HOME`` defaults to ``~/.codex``). Line 1 is a ``session_meta`` entry
carrying the session id, the ``cwd`` the thread ran in, its start timestamp and
its originator; every later line carries a monotonically increasing
``ordinal`` and its own ``timestamp``.

WHY IT IS NOT READ WHOLE. Measured on this Mac 2026-09-17: **5,041 rollouts
and 30.33 GB**, median 1.7 MB. Reading them to list them would be the exact
mistake the Claude screen already paid for (31.76 s and 58.96 s inside one
request, 1,209 s on a fresh engine — see ``claude_overview``). So one rollout
costs two bounded reads and no parse of the middle:

* the first 64 KB — the ``session_meta`` line, and the first user message as
  the title fallback;
* the last 256 KB — the newest complete entry, whose ``timestamp`` is the
  session's last activity and whose ``ordinal`` + 1 is its exact entry count.

``ordinal`` is what makes the entry count exact without a full read: Codex
numbers every line it appends, so the last line knows how many there are.

TITLES COME FROM CODEX ITSELF. ``$CODEX_HOME/session_index.jsonl`` is Codex's
own thread list — ``{"id", "thread_name", "updated_at"}``, 3,251 rows here — so
a thread a person named reads with that name and not with a guess made from
its first prompt. Only a thread Codex never named falls back to the prompt,
and ``title_source`` always says which happened.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from datetime import datetime, timezone

PROVIDER = "codex"

# The head must hold the session_meta line plus the first user message. The
# meta line carries Codex's full base instructions on older CLI versions and
# reaches ~40 KB, so 64 KB is the smallest window that reliably contains both.
HEAD_BYTES = 64 * 1024
# The tail must hold at least one complete entry. Codex's largest single
# entries are tool outputs; 256 KB is the same window the Claude transcript
# reader uses for the same reason.
TAIL_BYTES = 256 * 1024

# A cap never lies: when the walk stops here the list is incomplete and the
# caller reports it, exactly as the Claude reader does.
MAX_ROLLOUT_FILES = 20_000


def codex_home() -> Path:
    """Codex's own home — ``CODEX_HOME`` if set, else ``~/.codex``."""
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def default_rollout_root() -> Path:
    """Where Codex writes one JSONL per thread."""
    return codex_home() / "sessions"


def default_thread_index_path() -> Path:
    """Codex's own thread list: id, the name a person gave it, updated_at."""
    return codex_home() / "session_index.jsonl"


@dataclass(frozen=True)
class CodexSessionEntry:
    """One Codex thread, reduced to what the screen shows."""

    session_id: str
    thread_id: str
    path: Path
    title: str | None = None
    title_source: str | None = None
    cwd: str | None = None
    project: str | None = None
    started_at: int | None = None
    last_activity_at: int = 0
    entries: int = 0
    bytes: int = 0
    originator: str | None = None
    thread_source: str | None = None
    cli_version: str | None = None
    unreadable: bool = False
    # Why this file could not be reduced, when it could not be. Never empty
    # while ``unreadable`` is true: an unreadable row explains itself.
    unreadable_reason: str | None = None

    @property
    def is_subagent(self) -> bool:
        """A thread Codex started for a subagent, not for the person."""
        return self.thread_source == "subagent"


def parse_iso_ms(raw: object) -> int | None:
    """Codex's ISO-8601 stamps in epoch milliseconds."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def walk_rollouts(root: Path | None = None) -> tuple[dict[str, tuple[int, int]], bool]:
    """Stat every rollout file. Returns ``{path: (mtime_ns, size)}``, truncated.

    A stat walk only: nothing is opened here, so a cold engine learns the shape
    of 5,041 files in milliseconds and only re-reads the ones whose stamp moved.
    """
    base = root if root is not None else default_rollout_root()
    found: dict[str, tuple[int, int]] = {}
    truncated = False
    if not base.is_dir():
        return found, truncated
    for path in sorted(base.rglob("rollout-*.jsonl")):
        if len(found) >= MAX_ROLLOUT_FILES:
            truncated = True
            break
        try:
            if path.is_symlink() or not path.is_file():
                continue
            stamp = path.stat()
        except OSError:
            continue
        found[str(path)] = (stamp.st_mtime_ns, stamp.st_size)
    return found, truncated


def read_thread_names(path: Path | None = None) -> dict[str, tuple[str, int | None]]:
    """Codex's own thread names: ``{thread id: (name, updated_at ms)}``.

    The file is append-only and a renamed thread gets a second line, so the
    LAST line for an id wins — the same rule Claude's sidebar ledger uses.
    """
    target = path if path is not None else default_thread_index_path()
    names: dict[str, tuple[str, int | None]] = {}
    try:
        raw = target.read_text(errors="replace")
    except OSError:
        return names
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        thread_id = row.get("id")
        name = row.get("thread_name")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        names[thread_id] = (name.strip(), parse_iso_ms(row.get("updated_at")))
    return names


def _head_lines(path: Path, limit: int = HEAD_BYTES) -> Iterator[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(limit)
    except OSError:
        return
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # The last line of a bounded read is very likely cut in half; a partial
    # line is not a parse failure, it is simply not this window's business.
    if len(chunk) == limit and lines:
        lines = lines[:-1]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            yield value


def _tail_entry(path: Path, size: int, limit: int = TAIL_BYTES) -> dict[str, Any] | None:
    """The newest COMPLETE entry in a rollout, read from its tail."""
    if size <= 0:
        return None
    try:
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            chunk = handle.read()
    except OSError:
        return None
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            # A half line at the head of the window, or a torn last write.
            continue
        if isinstance(value, dict):
            return value
    return None


def _first_prompt(entries: list[dict[str, Any]]) -> str | None:
    """The first thing the person actually typed, as a title fallback.

    Codex puts several synthetic ``user`` messages before it — the permissions
    block, the app context, and the repository's AGENTS.md — so the first user
    message is NOT the person's prompt. Those are recognisable by their opening
    marker, and a message that is only one of them is skipped rather than
    shown as somebody's request.
    """
    markers = (
        "<permissions instructions>",
        "<app-context>",
        "# AGENTS.md instructions",
        "<user_instructions>",
        "<environment_context>",
    )
    for entry in entries:
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "message" or payload.get("role") != "user":
            continue
        for part in payload.get("content") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if not isinstance(text, str):
                continue
            stripped = text.strip()
            if not stripped or stripped.startswith(markers):
                continue
            first = stripped.splitlines()[0].strip()
            return (first[:117] + "...") if len(first) > 120 else first
    return None


def read_rollout(path: Path, *, mtime_ns: int, size: int) -> CodexSessionEntry:
    """Reduce ONE rollout with two bounded reads and no full parse.

    An unreadable file is still an entry: it is counted and it says why, so a
    permission problem or a torn write is visible on the screen instead of
    quietly shortening the list.
    """
    thread_id = _thread_id_from_name(path.name)
    head = list(_head_lines(path))
    meta: dict[str, Any] | None = None
    for entry in head:
        if entry.get("type") == "session_meta":
            payload = entry.get("payload")
            if isinstance(payload, dict):
                meta = payload
            break
    if meta is None:
        return CodexSessionEntry(
            session_id=thread_id,
            thread_id=thread_id,
            path=path,
            bytes=size,
            last_activity_at=mtime_ns // 1_000_000,
            unreadable=True,
            unreadable_reason=(
                "This rollout has no session_meta line in its first "
                f"{HEAD_BYTES // 1024} KB, so Codex's own facts about it "
                "cannot be read."
            ),
        )
    session_id = str(meta.get("session_id") or thread_id)
    cwd = meta.get("cwd")
    cwd_text = str(cwd) if isinstance(cwd, str) and cwd else None
    started_at = parse_iso_ms(meta.get("timestamp"))
    prompt = _first_prompt(head)
    tail = _tail_entry(path, size)
    last_activity = parse_iso_ms((tail or {}).get("timestamp"))
    ordinal = (tail or {}).get("ordinal")
    entries = int(ordinal) + 1 if isinstance(ordinal, int) and ordinal >= 0 else 0
    return CodexSessionEntry(
        session_id=session_id,
        thread_id=thread_id,
        path=path,
        title=prompt,
        title_source="first_prompt" if prompt else None,
        cwd=cwd_text,
        project=Path(cwd_text).name if cwd_text else None,
        started_at=started_at,
        # mtime is the floor, never the claim: a rollout whose tail could not
        # be parsed still has a real last-write time.
        last_activity_at=last_activity or started_at or mtime_ns // 1_000_000,
        entries=entries,
        bytes=size,
        originator=_text(meta.get("originator")),
        thread_source=_text(meta.get("thread_source")),
        cli_version=_text(meta.get("cli_version")),
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _thread_id_from_name(name: str) -> str:
    """``rollout-2026-06-25T09-38-21-<uuid>.jsonl`` -> ``<uuid>``."""
    stem = name[len("rollout-") :] if name.startswith("rollout-") else name
    stem = stem[: -len(".jsonl")] if stem.endswith(".jsonl") else stem
    parts = stem.split("-")
    # A UUID is five dash-separated groups; the stamp in front contributes the
    # rest. Take the last five groups and let anything shorter fall through.
    if len(parts) >= 5:
        return "-".join(parts[-5:])
    return stem


def merge_entries(
    rows: list[CodexSessionEntry], names: dict[str, tuple[str, int | None]]
) -> dict[str, CodexSessionEntry]:
    """One entry per session id, with Codex's own thread name applied.

    Two rollouts can reduce to one session id (Codex forks a thread when a
    context window rolls over), so the newest activity wins and its size is
    the sum of every file that belongs to the session — the screen's "size"
    means "what this session occupies on this Mac".
    """
    merged: dict[str, CodexSessionEntry] = {}
    total_bytes: dict[str, int] = {}
    for row in rows:
        total_bytes[row.session_id] = total_bytes.get(row.session_id, 0) + row.bytes
        current = merged.get(row.session_id)
        if current is None or row.last_activity_at > current.last_activity_at:
            merged[row.session_id] = row
    out: dict[str, CodexSessionEntry] = {}
    for session_id, row in merged.items():
        named = names.get(row.thread_id) or names.get(session_id)
        if named is not None:
            row = _replace(row, title=named[0], title_source="codex_thread_name")
        out[session_id] = _replace(row, bytes=total_bytes[session_id])
    return out


def _replace(row: CodexSessionEntry, **changes: Any) -> CodexSessionEntry:
    from dataclasses import replace

    return replace(row, **changes)


__all__ = [
    "HEAD_BYTES",
    "MAX_ROLLOUT_FILES",
    "PROVIDER",
    "TAIL_BYTES",
    "CodexSessionEntry",
    "codex_home",
    "default_rollout_root",
    "default_thread_index_path",
    "merge_entries",
    "parse_iso_ms",
    "read_rollout",
    "read_thread_names",
    "walk_rollouts",
]
