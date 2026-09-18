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
label, exactly as the server does for hook observations. The one path an entry
does carry, ``record_paths``, is the location of the record files themselves:
it never enters a payload and exists so the RETURN direction
(:mod:`app.services.coding_sessions.claude_label_writer`) can write a rename
made in AI Matrx back into the same records without a second full scan.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from app.services.coding_sessions.claude_scope import (
    MAX_INDEX_FILE_BYTES,
    MAX_INDEX_FILES,
    ScopeResolution,
    active_index_scope,
    default_app_support_dir,
    default_sessions_root,
    record_focused_at,
    resolve_active_scope,
)

# ``MAX_INDEX_FILES`` / ``MAX_INDEX_FILE_BYTES`` are re-exported from
# :mod:`app.services.coding_sessions.claude_scope`, which owns the bounded-read
# caps because it owns the scope scan. Claude keeps one index record per account
# per conversation, so the file cap is multiplied by however many accounts are
# on the machine: this Mac already holds 79,000+ records (eight accounts x
# ~1,900 conversations) and was hitting an old 50,000 ceiling, which silently
# dropped conversations off the end of the list. When the cap IS hit the
# overview reports ``index_limit_reached`` so the screen says so out loud.
MAX_LEDGER_BYTES = 33_554_432
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
    # Pins + categories come from the canonical sidebar ledger, not the index
    # records — the desktop app keeps them in its localStorage, which the
    # machine's session-sync agent extracts into the ledger. None = the ledger
    # has no opinion (never observed), so nothing is sent for that field.
    is_pinned: bool | None = None
    pinned_rank: int | None = None
    category: str | None = None
    # Exact local-only workspace path for native resume. It is deliberately
    # excluded from ``metadata_payload`` and every bridge envelope.
    local_cwd: Path | None = None
    # Every record file carrying this ``cliSessionId``, across accounts. Local
    # only — never part of any payload. See the module docstring.
    record_paths: tuple[Path, ...] = ()

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
        if self.is_pinned is not None:
            payload["is_pinned"] = self.is_pinned
            if self.is_pinned and self.pinned_rank is not None:
                payload["pinned_rank"] = self.pinned_rank
            # The ledger had an opinion on this session, so its category state
            # is an OBSERVATION either way: the key is always present, and an
            # explicit null tells the server "no category" — the server only
            # clears a stored category when the key is present (absent key =
            # no knowledge, clear nothing).
            payload["category"] = self.category
        elif self.category:
            payload["category"] = self.category
        return payload


def default_ledger_path() -> Path:
    """The canonical sidebar ledger the session-sync agent maintains."""
    configured = os.environ.get("CLAUDE_SIDEBAR_LEDGER")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude/claude-code-sidebar-state.json"


def read_sidebar_ledger(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Map record filename ('local_<id>.json') -> ledger fields. {} if absent."""
    ledger_path = path or default_ledger_path()
    try:
        if ledger_path.stat().st_size > MAX_LEDGER_BYTES:
            return {}
        data = json.loads(ledger_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        name: fields for name, fields in data.items() if isinstance(fields, dict)
    }


def record_is_starred(record: dict[str, Any]) -> bool | None:
    """The app's pin field on one record. ``None`` = no pin key at all."""
    value = record.get("isStarred")
    return value if isinstance(value, bool) else None


@dataclass(frozen=True)
class LivePins:
    """The app's own pin opinion, per conversation, from the signed-in scope.

    This is THE pin rule, held in one object because it has two callers that
    must not drift: the full disk scan (:func:`read_session_index`) and the
    persisted incremental store
    (:mod:`app.services.coding_sessions.claude_index_store`), which is what
    production actually reads. A fix applied to only one of them is invisible
    in the app — the shape of this bug on 2026-09-17.

    ``by_session`` maps ``cliSessionId`` -> the ``isStarred`` on that
    conversation's record IN THE ACTIVE SCOPE: ``True``/``False`` explicitly,
    or ``None`` when the record carries no pin key. A conversation with no
    record in the active scope is absent from the mapping entirely — the
    signed-in account has never seen it and cannot speak to its pin.
    """

    by_session: dict[str, bool | None] = field(default_factory=dict)

    @property
    def speaks(self) -> bool:
        """Whether this scope may be believed when it says "not pinned".

        A scope carrying no pin opinion at all is a freshly signed-in account,
        not a person who unpinned everything: believing it would clear the
        whole sidebar on the server in one pass.
        """
        return any(isinstance(value, bool) for value in self.by_session.values())

    def resolve(
        self,
        session_id: str,
        ledger_pinned: object,
        ledger_rank: object,
    ) -> tuple[bool | None, int | None]:
        """``(is_pinned, pinned_rank)`` for one conversation.

        The app's own field wins where it speaks; an absent pin key in a
        live scope is an honest "not pinned" (the app documents an absent pin
        record that way, and 14 of 14 sampled absences matched on
        2026-09-17). Otherwise the sidebar ledger stands, so a machine whose
        scope cannot be read keeps the pins it already had.
        """
        rank = ledger_rank if isinstance(ledger_rank, int) else None
        if self.speaks and session_id in self.by_session:
            pinned = self.by_session[session_id] is True
            return pinned, (rank if pinned else None)
        pinned_from_ledger = ledger_pinned if isinstance(ledger_pinned, bool) else None
        if not pinned_from_ledger:
            rank = None
        return pinned_from_ledger, rank


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


def _local_cwd(record: dict[str, Any]) -> Path | None:
    for key in ("cwd", "originCwd"):
        raw = record.get(key)
        if isinstance(raw, str) and raw.strip():
            return Path(raw).expanduser()
    return None


def entry_from_record(record: dict[str, Any]) -> ClaudeSessionIndexEntry | None:
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
        local_cwd=_local_cwd(record),
    )


def read_session_index(
    root: Path | None = None,
    *,
    ledger_path: Path | None = None,
) -> tuple[dict[str, ClaudeSessionIndexEntry], dict[str, int]]:
    """Read every ``local_*.json`` record, freshest record wins per session.

    A sync script unions these index files across Claude accounts, so the same
    ``cliSessionId`` commonly appears once per account folder — five, on this
    machine. Freshness is ``lastActivityAt``, then the record file's own mtime.

    **The mtime tie-break is load-bearing, not a nicety.** Observed 2026-08-16
    against the real app: renaming a session in Claude Code rewrites ONLY the
    active account's copy and does NOT bump ``lastActivityAt``, so every copy
    ties and a first-read tie-break selects a stale sibling's old title purely
    by directory sort order. mtime is the only signal that separates them.
    """
    sessions_root = root or default_sessions_root()
    totals = {"files": 0, "records": 0, "unreadable": 0}
    scope = active_index_scope(sessions_root)
    observed: dict[str, bool | None] = {}
    candidates_seen: list[tuple[Path, int, ClaudeSessionIndexEntry]] = []
    if not sessions_root.exists() or not sessions_root.is_dir():
        return {}, totals
    candidates = sorted(sessions_root.rglob("local_*.json"))
    if len(candidates) > MAX_INDEX_FILES:
        totals["truncated"] = 1
    for path in candidates[:MAX_INDEX_FILES]:
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
        entry = entry_from_record(record)
        if entry is None:
            continue
        if scope is not None and path.parent == scope:
            observed[entry.cli_session_id] = record_is_starred(record)
        candidates_seen.append((path, info.st_mtime_ns, entry))
    entries = merge_entries(
        candidates_seen,
        ledger=read_sidebar_ledger(ledger_path),
        live_pins=LivePins(observed),
    )
    totals["records"] = len(entries)
    return entries, totals


def merge_entries(
    candidates: Iterable[tuple[Path, int, ClaudeSessionIndexEntry]],
    *,
    ledger: dict[str, dict[str, Any]] | None = None,
    live_pins: LivePins | None = None,
) -> dict[str, ClaudeSessionIndexEntry]:
    """Collapse per-record rows into one entry per conversation.

    ``candidates`` is ``(record path, record mtime_ns, entry)`` for every
    record file, in any order. This is THE merge: the full disk scan and the
    persisted incremental index
    (:mod:`app.services.coding_sessions.claude_index_store`) both come through
    here, so the freshness rule and the sidebar-ledger rules below cannot drift
    apart between the two.
    """
    entries: dict[str, ClaudeSessionIndexEntry] = {}
    freshness: dict[str, tuple[int, int]] = {}
    paths: dict[str, list[Path]] = {}
    for path, mtime_ns, entry in candidates:
        paths.setdefault(entry.cli_session_id, []).append(path)
        rank = (entry.last_activity_at, mtime_ns)
        if entry.cli_session_id not in entries or rank > freshness[entry.cli_session_id]:
            entries[entry.cli_session_id] = entry
            freshness[entry.cli_session_id] = rank
    for session_id in paths:
        paths[session_id].sort()
    ledger = read_sidebar_ledger() if ledger is None else ledger
    pins = live_pins if live_pins is not None else LivePins()
    enriched: dict[str, ClaudeSessionIndexEntry] = {}
    for session_id, entry in entries.items():
        record_paths = tuple(paths[session_id])
        fields = ledger.get(record_paths[0].name, {}) if record_paths else {}
        is_pinned, rank = pins.resolve(
            session_id, fields.get("isPinned"), fields.get("pinnedRank")
        )
        # THE APP'S OWN PIN FIELD WINS. ``isStarred`` in the scope the app is
        # signed into is the same state the app reports for the conversation,
        # and its absence means "never pinned here" — the app documents an
        # absent pin record as not pinned, and 14 of 14 sampled absences
        # matched that on 2026-09-17. So an UNPIN is finally representable:
        # the ledger's ``pinnedOrder`` source could only ever add.
        #
        # Unknown stays unknown, in the two cases where it genuinely is: no
        # scope could be identified, or the signed-in account has no record of
        # this conversation at all and cannot speak to its pin. The rule lives
        # in :meth:`LivePins.resolve` so the store path shares it exactly.
        category = _clean_text(fields.get("categoryName"))
        # THE LEDGER WINS for the sidebar labels it carries. The machine's
        # session-sync agent (~/.claude/sync-claude-code-sessions.py) merges
        # every account's copy of a session into one canonical title /
        # titleSource / isArchived, with a rule the raw records cannot express:
        # a title the person typed is never replaced by an auto-generated one
        # from a sibling account. Reading only the freshest record file sent
        # AI Matrx the sibling's auto title while Claude's own sidebar showed
        # the person's rename (observed 2026-09-07). A record wins only when
        # the ledger has no opinion on that field.
        ledger_title = _clean_text(fields.get("title"))
        ledger_title_source = _clean_text(fields.get("titleSource"), limit=32)
        ledger_archived = fields.get("isArchived")
        enriched[session_id] = replace(
            entry,
            record_paths=record_paths,
            title=ledger_title or entry.title,
            title_source=(
                ledger_title_source if ledger_title else entry.title_source
            ),
            is_archived=(
                ledger_archived if isinstance(ledger_archived, bool) else entry.is_archived
            ),
            is_pinned=is_pinned,
            pinned_rank=rank,
            category=category,
        )
    return enriched


__all__ = [
    "MAX_INDEX_FILES",
    "MAX_INDEX_FILE_BYTES",
    "ClaudeSessionIndexEntry",
    "LivePins",
    "active_index_scope",
    "default_ledger_path",
    "record_focused_at",
    "record_is_starred",
    "ScopeResolution",
    "resolve_active_scope",
    "default_app_support_dir",
    "default_sessions_root",
    "entry_from_record",
    "merge_entries",
    "read_session_index",
    "read_sidebar_ledger",
]
