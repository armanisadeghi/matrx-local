"""Reader for Claude Code's own per-account session index.

Claude's desktop app stores the EXACT label it shows in its sidebar in one
small JSON record per session:

``<app support>/Claude/claude-code-sessions/<accountUuid>/<orgUuid>/local_<id>.json``

Every record carries ``cliSessionId`` — the UUID that names the transcript at
``~/.claude/projects/<cwd-slug>/<cliSessionId>.jsonl`` — which is the same
identity the bridge already binds. That join is what lets AI Matrx show the
same title Claude shows: a rename in Claude Code lands here, so the next sync
pass carries it to the platform.

Only display labels are read. Raw paths (``cwd``, ``worktreePath``) never leave
this module as themselves — callers take the last path segment as a workspace
label, exactly as the server does for hook observations.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_INDEX_FILES = 50_000
MAX_INDEX_FILE_BYTES = 8_388_608
_TITLE_MAX_CHARS = 160


@dataclass(frozen=True)
class ClaudeSessionIndexEntry:
    """One Claude desktop session-index record, reduced to display labels."""

    cli_session_id: str
    title: str | None
    title_source: str | None
    workspace_name: str | None
    git_branch: str | None
    worktree_name: str | None
    is_archived: bool | None
    last_activity_at: int

    def metadata_payload(self) -> dict[str, Any]:
        """The SessionMetadata hook payload for this record (labels only)."""
        payload: dict[str, Any] = {}
        if self.title:
            payload["title"] = self.title
        if self.workspace_name:
            payload["project_name"] = self.workspace_name
        if self.git_branch:
            payload["git_branch"] = self.git_branch
        if self.worktree_name:
            payload["worktree_name"] = self.worktree_name
        if self.is_archived is not None:
            payload["is_archived"] = self.is_archived
        return payload


def default_sessions_root() -> Path:
    """Claude desktop's session-index root for this platform."""
    configured = os.environ.get("CLAUDE_DESKTOP_SESSIONS_DIR")
    if configured:
        return Path(configured).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/Claude/claude-code-sessions"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData/Roaming"
        return base / "Claude/claude-code-sessions"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else home / ".config"
    return base / "Claude/claude-code-sessions"


def _clean_text(value: object, *, limit: int = _TITLE_MAX_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned[:limit] if cleaned else None


def _workspace_label(record: dict[str, Any]) -> str | None:
    """Display-only workspace name: the last segment of the session's cwd."""
    for key in ("cwd", "originCwd"):
        raw = record.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        segments = [part for part in raw.replace("\\", "/").split("/") if part]
        if segments:
            return _clean_text(segments[-1])
    return None


def _entry_from_record(record: dict[str, Any]) -> ClaudeSessionIndexEntry | None:
    cli_session_id = record.get("cliSessionId")
    if not isinstance(cli_session_id, str) or not cli_session_id.strip():
        return None
    archived = record.get("isArchived")
    activity = record.get("lastActivityAt")
    return ClaudeSessionIndexEntry(
        cli_session_id=cli_session_id.strip(),
        title=_clean_text(record.get("title")),
        title_source=_clean_text(record.get("titleSource"), limit=32),
        workspace_name=_workspace_label(record),
        git_branch=_clean_text(record.get("branch")),
        worktree_name=_clean_text(record.get("worktreeName")),
        is_archived=archived if isinstance(archived, bool) else None,
        last_activity_at=activity if isinstance(activity, int) else 0,
    )


def read_session_index(
    root: Path | None = None,
) -> tuple[dict[str, ClaudeSessionIndexEntry], dict[str, int]]:
    """Read every ``local_*.json`` record, newest activity wins per session.

    A sync script unions these index files across Claude accounts, so the same
    ``cliSessionId`` commonly appears once per account folder. The record with
    the latest ``lastActivityAt`` is the current one; ties keep the first read.
    """
    sessions_root = root or default_sessions_root()
    totals = {"files": 0, "records": 0, "unreadable": 0}
    entries: dict[str, ClaudeSessionIndexEntry] = {}
    if not sessions_root.exists() or not sessions_root.is_dir():
        return entries, totals
    for path in sorted(sessions_root.rglob("local_*.json")):
        if totals["files"] >= MAX_INDEX_FILES:
            break
        try:
            info = path.lstat()
        except OSError:
            totals["unreadable"] += 1
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INDEX_FILE_BYTES:
            totals["unreadable"] += 1
            continue
        totals["files"] += 1
        try:
            record = json.loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            totals["unreadable"] += 1
            continue
        if not isinstance(record, dict):
            totals["unreadable"] += 1
            continue
        entry = _entry_from_record(record)
        if entry is None:
            continue
        existing = entries.get(entry.cli_session_id)
        if existing is None or entry.last_activity_at > existing.last_activity_at:
            entries[entry.cli_session_id] = entry
    totals["records"] = len(entries)
    return entries, totals


__all__ = [
    "MAX_INDEX_FILES",
    "MAX_INDEX_FILE_BYTES",
    "ClaudeSessionIndexEntry",
    "default_sessions_root",
    "read_session_index",
]
