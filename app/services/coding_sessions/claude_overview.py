"""Everything the Claude Code screen shows, in one read.

The screen's job is to answer three questions without being asked twice:
what is on this Mac, has it reached the cloud, and what is broken. So this
returns accounts, conversations and per-conversation state together — one
request, no scan to kick off first, no operation to preview and then apply.

Nothing is hidden and nothing is sampled: it reads every account's records, so
a conversation that exists anywhere on this Mac appears. Claude keeps one index
record per account, so eight accounts means eight copies of every conversation
— 46,034 files for 1,806 conversations here, about 21s to parse. Reading only
the signed-in account would take 1.5s but lose 380 conversations, so instead the
result is cached against a stat-only fingerprint of the tree: repeat opens are
~0.2s, and a new or changed session invalidates it on its own.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.coding_sessions.claude_session_index import (
    default_sessions_root,
    read_session_index,
)
from app.services.local_db.database import get_db

_MAX_CONVERSATIONS = 5000


def _claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _desktop_support_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/Claude"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else home / "AppData/Roaming") / "Claude"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    return (Path(config_home) if config_home else home / ".config") / "Claude"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def active_account() -> str | None:
    """The account Claude Desktop is signed into right now."""
    value = _read_json(_desktop_support_dir() / "config.json").get(
        "lastKnownAccountUuid"
    )
    return value if isinstance(value, str) and value else None


def _account_names() -> dict[str, str]:
    """uuid -> email, when the user has named the account.

    The email is not recoverable from disk: the desktop app keeps it inside an
    encrypted token cache. This optional map is the only source of a real name.
    """
    names = _read_json(_claude_config_dir() / "claude-code-accounts.json")
    return {k: v for k, v in names.items() if isinstance(v, str)}


def list_accounts() -> list[dict[str, Any]]:
    root = default_sessions_root()
    names = _account_names()
    current = active_account()
    accounts: list[dict[str, Any]] = []
    if not root.is_dir():
        return accounts
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        records = sum(1 for _ in child.rglob("local_*.json"))
        try:
            stat = child.stat()
            born = getattr(stat, "st_birthtime", None) or stat.st_mtime
            first_seen = datetime.fromtimestamp(born, timezone.utc).isoformat()
        except OSError:
            first_seen = None
        accounts.append(
            {
                "account_id": child.name,
                "name": names.get(child.name),
                "conversations": records,
                "first_seen": first_seen,
                "active": child.name == current,
            }
        )
    return accounts


def _transcripts() -> dict[str, tuple[int, int]]:
    """session id -> (bytes, mtime_ns) for every transcript on disk."""
    found: dict[str, tuple[int, int]] = {}
    root = _claude_config_dir() / "projects"
    if not root.is_dir():
        return found
    for path in root.glob("*/*.jsonl"):
        try:
            info = path.stat()
        except OSError:
            continue
        found[path.stem] = (info.st_size, info.st_mtime_ns)
    return found


# Reading every account's copy of every conversation costs ~10s here (46,034
# files). Reading only the active account is ~1.5s but LOSES 380 conversations,
# because a scope is only as complete as the last cross-account sync — and the
# screen's whole purpose is that nothing is hidden. So: read everything, then
# cache it against a cheap fingerprint of the tree (file count + newest mtime,
# a stat-only walk) so repeat opens are instant and a new session still lands.
_INDEX_CACHE: tuple[tuple[int, int], dict[str, Any], dict[str, int]] | None = None


def _tree_fingerprint(root: Path) -> tuple[int, int]:
    count = 0
    newest = 0
    for path in root.rglob("local_*.json"):
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            continue
        count += 1
        if mtime > newest:
            newest = mtime
    return count, newest


def _session_index(root: Path) -> tuple[dict[str, Any], dict[str, int]]:
    global _INDEX_CACHE
    fingerprint = _tree_fingerprint(root)
    if _INDEX_CACHE is not None and _INDEX_CACHE[0] == fingerprint:
        return _INDEX_CACHE[1], _INDEX_CACHE[2]
    entries, totals = read_session_index(root)
    _INDEX_CACHE = (fingerprint, entries, totals)
    return entries, totals


async def _delivery_state() -> tuple[dict[str, int], int, int]:
    """(session -> last ack ns, waiting count, failed count)."""
    db = get_db()
    acked: dict[str, int] = {}
    try:
        rows = await db.fetchall(
            "SELECT provider_session_id, last_synced_at FROM claude_session_synced"
        )
    except Exception:
        rows = []
    for row in rows:
        raw = str(row["last_synced_at"])
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        acked[str(row["provider_session_id"])] = int(stamp.timestamp() * 1_000_000_000)

    async def _count(table: str) -> int:
        try:
            row = await db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
        except Exception:
            return 0
        return int(row["n"]) if row else 0

    return (
        acked,
        await _count("coding_session_bridge_outbox"),
        await _count("coding_session_bridge_quarantine"),
    )


async def overview(limit: int = _MAX_CONVERSATIONS) -> dict[str, Any]:
    """Accounts, conversations and sync state — the whole screen in one call."""
    current = active_account()
    # 46,034 files and ~25s on a cold cache. On the event loop that freezes
    # every other request in the engine for the whole scan, so it runs in a
    # thread; the UI shows its loading state and nothing else stalls.
    entries, totals = await asyncio.to_thread(
        _session_index, default_sessions_root()
    )
    transcripts = await asyncio.to_thread(_transcripts)
    acked, waiting, failed = await _delivery_state()

    conversations: list[dict[str, Any]] = []
    counts = {"synced": 0, "behind": 0, "not_synced": 0}
    for session_id, entry in entries.items():
        size, mtime_ns = transcripts.get(session_id, (0, 0))
        ack_ns = acked.get(session_id)
        if ack_ns is None:
            state = "not_synced"
        elif mtime_ns and mtime_ns > ack_ns:
            state = "behind"
        else:
            state = "synced"
        counts[state] += 1
        conversations.append(
            {
                "session_id": session_id,
                "title": entry.title or "Untitled",
                "project": entry.workspace_name,
                "last_activity_at": entry.last_activity_at,
                "bytes": size,
                "on_disk": size > 0,
                "state": state,
                "pinned": bool(entry.is_pinned),
                "archived": bool(entry.is_archived),
            }
        )
    conversations.sort(key=lambda item: item["last_activity_at"], reverse=True)

    return {
        "account_id": current,
        "accounts": list_accounts(),
        "conversations": conversations[:limit],
        "totals": {
            "conversations": len(conversations),
            "index_files_read": totals.get("files", 0),
            "unreadable": totals.get("unreadable", 0),
            **counts,
            "waiting": waiting,
            "failed": failed,
        },
    }
