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
from dataclasses import dataclass, replace
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
    # Pins come from every account's observed starred list (:class:`LivePins`),
    # categories from the canonical sidebar ledger — never from the index
    # records. None = no opinion (never observed), so nothing is sent.
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
    """The record's ``isStarred`` flag. A DIAGNOSTIC — it is NOT the pin.

    Measured 2026-09-18: the signed-in scope carried ``isStarred: true`` on 206
    unarchived conversations while the app's sidebar showed ~48 pinned, and the
    session-sync agent spreads the flag by copying whole records between the
    account/org folders. The pin is the app's starred list (see
    :class:`LivePins`).
    """
    value = record.get("isStarred")
    return value if isinstance(value, bool) else None


MAX_OBSERVATIONS_BYTES = 8_388_608


def default_pin_observations_path() -> Path:
    """Every account's last-observed starred list (the session-sync agent's)."""
    configured = os.environ.get("CLAUDE_PIN_OBSERVATIONS")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude/claude-code-pin-observations.json"


def read_pin_master(path: Path | None = None) -> tuple[dict[str, int], frozenset[str]] | None:
    """THE master pin verdict: ``(pinned name -> rank, unpinned names)``.

    The file is written by ``~/.claude/sync-claude-code-sessions.py``::

        {"accounts": {...each account's last starred list...},
         "master": {"pinned": ["local_<id>.json", ...],   # ordered: rank = index
                    "unpinned": ["local_<id>.json", ...], # empty until coverage
                    "coverage": {...}, "computed_at": "..."}}

    The engine applies ONLY ``master`` — it never re-derives anything from the
    raw per-account lists, so the sidebar ledger and AI Matrx cannot disagree.
    The sync puts a conversation in ``unpinned`` only when every known account
    was observed within 14 days and none of them pins it. Absent, unreadable,
    or no ``master`` -> ``None``: the pin is UNKNOWN and the ledger stands.
    """
    source = path or default_pin_observations_path()
    try:
        if source.stat().st_size > MAX_OBSERVATIONS_BYTES:
            return None
        data = json.loads(source.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    master = data.get("master") if isinstance(data, dict) else None
    if not isinstance(master, dict):
        return None
    pinned_list = master.get("pinned")
    unpinned_list = master.get("unpinned")
    if not isinstance(pinned_list, list) or not isinstance(unpinned_list, list):
        return None
    pinned: dict[str, int] = {}
    for rank, name in enumerate(pinned_list):
        if isinstance(name, str) and name not in pinned:
            pinned[name] = rank
    unpinned = frozenset(
        name for name in unpinned_list if isinstance(name, str) and name not in pinned
    )
    return pinned, unpinned


@dataclass(frozen=True)
class LivePins:
    """The master pin verdict, per conversation.

    THE pin rule, held in one object because it has two callers that must not
    drift: the full disk scan (:func:`read_session_index`) and the persisted
    incremental store (:mod:`app.services.coding_sessions.claude_index_store`),
    which is what production actually reads.

    ``pinned`` maps a record filename (``local_<id>.json`` — the key the app's
    starred list, the sidebar ledger and the index records share) to its rank;
    ``unpinned`` holds the names the sync has PROVED unpinned in every account.
    Anything in neither is UNKNOWN and the sidebar ledger's value stands — so a
    machine with one of eight accounts observed can add pins but never remove
    one. ``None`` pinned = no master at all: the ledger stands everywhere.
    """

    pinned: dict[str, int] | None = None
    unpinned: frozenset[str] = frozenset()

    @classmethod
    def from_observations(cls, path: Path | None = None) -> "LivePins":
        master = read_pin_master(path)
        if master is None:
            return cls()
        return cls(master[0], master[1])

    @property
    def speaks(self) -> bool:
        return self.pinned is not None

    def resolve(
        self,
        record_names: Iterable[str],
        ledger_pinned: object,
        ledger_rank: object,
    ) -> tuple[bool | None, int | None]:
        """``(is_pinned, pinned_rank)`` for one conversation."""
        names = set(record_names)
        if self.pinned is not None:
            ranks = [self.pinned[name] for name in names if name in self.pinned]
            if ranks:
                return True, min(ranks)
            if names & self.unpinned:
                return False, None
        rank = ledger_rank if isinstance(ledger_rank, int) else None
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
        candidates_seen.append((path, info.st_mtime_ns, entry))
    entries = merge_entries(
        candidates_seen,
        ledger=read_sidebar_ledger(ledger_path),
        live_pins=LivePins.from_observations(),
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
    pins = live_pins if live_pins is not None else LivePins.from_observations()
    enriched: dict[str, ClaudeSessionIndexEntry] = {}
    for session_id, entry in entries.items():
        record_paths = tuple(paths[session_id])
        fields = ledger.get(record_paths[0].name, {}) if record_paths else {}
        # THE MASTER VERDICT WINS where it speaks: pinned in any account's
        # starred list -> pinned; proved unpinned in EVERY account -> an unpin
        # AI Matrx must hear about. Anything else is UNKNOWN and the ledger
        # stands. The rule lives in :meth:`LivePins.resolve` so the
        # store path shares it exactly.
        is_pinned, rank = pins.resolve(
            {path.name for path in record_paths},
            fields.get("isPinned"),
            fields.get("pinnedRank"),
        )
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
    "default_pin_observations_path",
    "record_focused_at",
    "record_is_starred",
    "ScopeResolution",
    "resolve_active_scope",
    "default_app_support_dir",
    "default_sessions_root",
    "entry_from_record",
    "merge_entries",
    "read_session_index",
    "read_pin_master",
    "read_sidebar_ledger",
]
