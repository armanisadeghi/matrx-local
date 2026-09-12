"""Everything the Claude Code screen shows, in one read — against CLOUD truth.

The screen's job is to answer three questions without being asked twice:
what is on this Mac, is it in AI Matrx, and what is broken. So this returns
accounts, conversations and per-conversation state together — one request, no
scan to kick off first, no operation to preview and then apply.

THE STATUS IS THE CLOUD'S, NOT THIS MAC'S. Until 2026-09-08 a session read
"synced" only when THIS engine had uploaded it (a row in
``claude_session_synced``). Nearly every Claude Code session reaches AI Matrx
through the Claude Code plugin hook instead — straight from the CLI to the
server, never through this engine — so the screen said "Not synced" for 1,836
conversations while the cloud held 1,671 of them. Now the engine asks the
server which sessions it actually holds (the same identity inventory the
title reconciler already uses) and reports against that; local delivery
ledgers only explain HOW a session got there or why it has not.

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
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.services.coding_sessions.claude_session_index import (
    ClaudeSessionIndexEntry,
    default_sessions_root,
    read_session_index,
)
from app.services.coding_sessions.identity_client import (
    IdentityInventoryBlocked,
    fetch_complete_identity_inventory,
)
from app.services.session_freshness import request_ui_session_refresh
from app.services.local_db.database import get_db
from app.services.local_db.repositories import TokenRepo

logger = get_logger()

_MAX_CONVERSATIONS = 5000

# A session is "changed" when Claude's OWN last-activity stamp for it (the
# sidebar index record's lastActivityAt) is newer than the server's last
# delivery. The hook that mirrors a turn lands seconds after it, so inside this
# window "local newer than cloud" is the ordinary shape of a live session.
#
# NOT the transcript file's mtime: measured 2026-09-08, a bulk rewrite had
# stamped 2026-09-07T22:44 on hundreds of untouched transcripts and 1,389 of
# 1,432 cloud-held sessions read "changed" while their last entry matched the
# server's last delivery to the second.
_CHANGED_GRACE_SECONDS = 5 * 60

# The server inventory is one paged read of every bound session (1,671 here).
# Cache it briefly so Refresh is instant and a 15s publisher tick cannot turn
# the screen into a load test on the server.
_CLOUD_CACHE_SECONDS = 45.0

# ── Session states ──────────────────────────────────────────────────────────
#
#   in_cloud      the server holds this session and it is not behind
#   changed       the server holds it, but the transcript here is newer than
#                 the server's last delivery by more than the grace window
#   queued        not on the server yet; events for it are waiting in the
#                 local delivery queue
#   failed        delivery for this session was refused and is preserved
#                 locally (quarantine) — it needs a decision
#   not_in_cloud  nothing on the server, nothing queued: it has never been
#                 mirrored or imported
#   unknown       the server could not be asked (offline, signed out, paused);
#                 the local ledgers alone cannot say whether it is in the cloud
#
SESSION_STATES = (
    "in_cloud",
    "changed",
    "queued",
    "failed",
    "not_in_cloud",
    "unknown",
)


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


# ── Identity: the two spellings of one session ──────────────────────────────
#
# A hook-mirrored session is bound on the server under its raw Claude session
# UUID. A history-imported one is bound under the SDK identity
# ``claude-sdk:<sha256(project_key)>:<urlsafe-b64(session uuid)>`` (see
# claude_history._bridge_provider_session_id). Both name the same transcript,
# so every cloud or queue lookup here reduces a key to the raw UUID first.


def raw_session_id(provider_session_id: str) -> str:
    value = str(provider_session_id or "")
    if not value.startswith("claude-sdk:"):
        return value
    parts = value.split(":", 2)
    if len(parts) != 3 or not parts[2]:
        return value
    encoded = parts[2]
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value


def _parse_iso_ns(raw: object) -> int | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1_000_000_000)


# ── Cloud truth ─────────────────────────────────────────────────────────────

_CLOUD_CACHE: tuple[float, dict[str, dict[str, Any]], dict[str, Any]] | None = None


async def cloud_inventory(*, force: bool = False) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """(raw session id -> server binding, meta) for every Claude session the
    server holds for the signed-in AI Matrx user.

    ``meta.checked`` is False when the server could not be asked; ``reason``
    then names exactly why (no signed-in user, server unconfigured, offline,
    or the server's own refusal) so the screen can say it instead of guessing.
    """
    global _CLOUD_CACHE
    now = time.monotonic()
    if (
        not force
        and _CLOUD_CACHE is not None
        and now - _CLOUD_CACHE[0] < _CLOUD_CACHE_SECONDS
    ):
        return _CLOUD_CACHE[1], _CLOUD_CACHE[2]

    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta: dict[str, Any] = {
        "checked": False,
        "reason": None,
        "detail": None,
        "sessions": 0,
        "checked_at": checked_at,
    }
    tokens = TokenRepo(get_db())
    token_row = await tokens.get()
    if (
        not token_row
        or not token_row.get("access_token")
        or not token_row.get("user_id")
        or tokens.is_expired(token_row)
    ):
        meta["reason"] = "no_active_user_jwt"
        meta["detail"] = (
            "This Mac has no valid signed-in session; Matrx Local is asking the desktop "
            "for a fresh one. If this stays, sign out and back in to AI Matrx in Matrx Local."
        )
        _CLOUD_CACHE = (now, {}, meta)
        # The stored token is the engine's, the session is the desktop's: ask
        # the owner for a fresh copy instead of waiting for the next hour.
        await request_ui_session_refresh(
            lane="claude_overview", reason="stored access token missing or expired"
        )
        return {}, meta

    from app.services.aidream.client import get_aidream_client

    client = get_aidream_client()
    if client is None:
        meta["reason"] = "aidream_server_unconfigured"
        meta["detail"] = "No AI Dream server is configured for this engine."
        _CLOUD_CACHE = (now, {}, meta)
        return {}, meta

    try:
        rows = await fetch_complete_identity_inventory(
            client=client,
            jwt=str(token_row["access_token"]),
            provider="claude_code",
        )
    except IdentityInventoryBlocked as exc:
        meta["reason"] = exc.reason
        meta["detail"] = _explain_inventory_block(exc.reason)
        _CLOUD_CACHE = (now, {}, meta)
        return {}, meta

    by_session: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider_session_id = str(row.get("provider_session_id") or "")
        if not provider_session_id:
            continue
        key = raw_session_id(provider_session_id)
        binding = {
            "provider_session_id": provider_session_id,
            "conversation_id": row.get("conversation_id"),
            "fidelity": row.get("fidelity"),
            "last_seen_at": row.get("last_seen_at"),
            "conversation_title": row.get("conversation_title"),
            "title_source": row.get("title_source"),
        }
        previous = by_session.get(key)
        # Two bindings for one transcript (hook + import): keep the one the
        # server saw most recently; the other is still listed in a diagnosis.
        if previous is None or (
            (_parse_iso_ns(binding["last_seen_at"]) or 0)
            > (_parse_iso_ns(previous["last_seen_at"]) or 0)
        ):
            by_session[key] = binding
    meta["checked"] = True
    meta["sessions"] = len(by_session)
    _CLOUD_CACHE = (now, by_session, meta)
    return by_session, meta


def _explain_inventory_block(reason: str) -> str:
    if reason == "aidream_unreachable":
        return "AI Matrx could not be reached from this Mac. Check the connection; the list below shows local facts only."
    if reason.startswith("aidream_error:"):
        detail = reason.split(":", 1)[1]
        if "Cannot name an organization" in detail:
            return (
                "AI Matrx needs to know which organization to answer for, and this "
                "Mac has no default organization chosen. Choose your organization "
                "in Matrx Local, then refresh."
            )
        if "HTTP 400" in detail:
            return (
                "AI Matrx refused the session list (HTTP 400). The server gates that "
                "read on a named organization until the release that reads it as "
                "owner-scoped is live; until then this Mac cannot say which "
                "conversations the cloud holds. Refresh after the next server release."
            )
        if "HTTP 401" in detail or "HTTP 403" in detail:
            return "AI Matrx rejected this Mac's sign-in. Sign in again in Matrx Local, then refresh."
        return f"AI Matrx refused the session list: {detail}"
    return reason


# ── Local delivery ledgers ──────────────────────────────────────────────────


async def _queue_by_session() -> dict[str, dict[str, int]]:
    """raw session id -> {pending, quarantined} for Claude Code envelopes."""
    db = get_db()
    counts: dict[str, dict[str, int]] = {}
    try:
        rows = await db.fetchall(
            """SELECT queue_state, session_key, COUNT(*) AS n
               FROM coding_session_bridge_queue_metadata
               WHERE provider = 'claude_code' AND session_key IS NOT NULL
               GROUP BY queue_state, session_key"""
        )
    except Exception:
        return counts
    for row in rows:
        key = raw_session_id(str(row["session_key"]))
        bucket = counts.setdefault(key, {"pending": 0, "quarantined": 0})
        if str(row["queue_state"]) == "quarantine":
            bucket["quarantined"] += int(row["n"])
        else:
            bucket["pending"] += int(row["n"])
    return counts


async def _delivered_by_this_mac() -> dict[str, int]:
    """raw session id -> ns of the last acknowledgement THIS engine recorded.

    Supplementary: it explains how an import got there, it does not decide
    whether a session is in the cloud (the server decides that).
    """
    db = get_db()
    acked: dict[str, int] = {}
    try:
        rows = await db.fetchall(
            "SELECT provider_session_id, last_synced_at FROM claude_session_synced"
        )
    except Exception:
        rows = []
    for row in rows:
        stamp = _parse_iso_ns(row["last_synced_at"])
        if stamp is not None:
            acked[raw_session_id(str(row["provider_session_id"]))] = stamp
    return acked


async def _queue_totals() -> tuple[int, int]:
    db = get_db()

    async def _count(table: str) -> int:
        try:
            row = await db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
        except Exception:
            return 0
        return int(row["n"]) if row else 0

    return (
        await _count("coding_session_bridge_outbox"),
        await _count("coding_session_bridge_quarantine"),
    )


def _session_state(
    *,
    cloud_checked: bool,
    binding: dict[str, Any] | None,
    activity_ns: int,
    queue: dict[str, int],
) -> str:
    if queue.get("quarantined", 0) > 0:
        return "failed"
    if binding is not None:
        seen_ns = _parse_iso_ns(binding.get("last_seen_at"))
        if (
            seen_ns is not None
            and activity_ns
            and activity_ns > seen_ns + _CHANGED_GRACE_SECONDS * 1_000_000_000
        ):
            return "changed"
        return "in_cloud"
    if queue.get("pending", 0) > 0:
        return "queued"
    if not cloud_checked:
        return "unknown"
    return "not_in_cloud"


def _transcript_only_rows(
    orphan_ids: list[str],
    transcripts: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    """Rows for transcripts that have NO Claude sidebar record anywhere.

    A conversation only appeared on this screen if Claude Desktop had written
    a sidebar index record for it. Sessions started from the plain `claude`
    CLI never get one, so they sat on disk — readable, syncable, 80 of them and
    113 MB on 2026-09-11 — and the screen simply never listed them. The
    importer's own source walk already reaches them (it reads the projects
    tree, not the index); only the screen was hiding them.

    Title and project come from the transcript itself through the importer's
    bounded reader (first 40 lines + last 256 KB), only for the orphans, so the
    cost is ~80 small reads rather than 1,600.
    """
    from app.services.coding_sessions.claude_history import _read_summary

    root = _claude_config_dir() / "projects"
    wanted = set(orphan_ids)
    by_id: dict[str, Path] = {}
    for path in root.glob("*/*.jsonl"):
        if path.stem in wanted:
            by_id[path.stem] = path
    rows: list[dict[str, Any]] = []
    for session_id in orphan_ids:
        path = by_id.get(session_id)
        if path is None:
            continue
        size, mtime_ns = transcripts.get(session_id, (0, 0))
        title = f"Claude session {session_id[:8]}"
        project: str | None = None
        try:
            title, project, _branch = _read_summary(root, path, session_id)
        except Exception:  # noqa: BLE001 — an unreadable summary still lists the row
            logger.debug("[claude_overview] summary unreadable for %s", session_id, exc_info=True)
        rows.append(
            {
                "session_id": session_id,
                "title": title or "Untitled",
                "project": project,
                "bytes": size,
                "mtime_ns": mtime_ns,
            }
        )
    return rows


async def overview(limit: int = _MAX_CONVERSATIONS) -> dict[str, Any]:
    """Accounts, conversations and cloud state — the whole screen in one call."""
    current = active_account()
    # 46,034 files and ~25s on a cold cache. On the event loop that freezes
    # every other request in the engine for the whole scan, so it runs in a
    # thread; the UI shows its loading state and nothing else stalls.
    entries, totals = await asyncio.to_thread(
        _session_index, default_sessions_root()
    )
    transcripts = await asyncio.to_thread(_transcripts)
    cloud, cloud_meta = await cloud_inventory()
    queue = await _queue_by_session()
    waiting, quarantined = await _queue_totals()

    conversations: list[dict[str, Any]] = []
    counts = {state: 0 for state in SESSION_STATES}
    pinned_total = 0
    for session_id, entry in entries.items():
        size, mtime_ns = transcripts.get(session_id, (0, 0))
        binding = cloud.get(session_id)
        session_queue = queue.get(session_id, {"pending": 0, "quarantined": 0})
        state = _session_state(
            cloud_checked=bool(cloud_meta["checked"]),
            binding=binding,
            activity_ns=int(entry.last_activity_at or 0) * 1_000_000,
            queue=session_queue,
        )
        counts[state] += 1
        if entry.is_pinned:
            pinned_total += 1
        conversations.append(
            {
                "session_id": session_id,
                "title": entry.title or "Untitled",
                "title_source": entry.title_source,
                "project": entry.workspace_name,
                "last_activity_at": entry.last_activity_at,
                "bytes": size,
                "on_disk": size > 0,
                "state": state,
                "pinned": bool(entry.is_pinned),
                "pinned_rank": entry.pinned_rank,
                "category": entry.category,
                "archived": bool(entry.is_archived),
                "in_claude_sidebar": True,
                "cloud": (
                    {
                        "conversation_id": binding.get("conversation_id"),
                        "fidelity": binding.get("fidelity"),
                        "last_seen_at": binding.get("last_seen_at"),
                    }
                    if binding is not None
                    else None
                ),
                "delivery": {
                    "pending": int(session_queue.get("pending", 0)),
                    "quarantined": int(session_queue.get("quarantined", 0)),
                },
            }
        )
    # Everything on disk that Claude never indexed. Same state judgement as
    # every other row — the cloud does not care whether the sidebar knew.
    orphan_ids = sorted(set(transcripts) - set(entries))
    transcript_only = 0
    for row in await asyncio.to_thread(_transcript_only_rows, orphan_ids, transcripts):
        session_id = row["session_id"]
        binding = cloud.get(session_id)
        session_queue = queue.get(session_id, {"pending": 0, "quarantined": 0})
        state = _session_state(
            cloud_checked=bool(cloud_meta["checked"]),
            binding=binding,
            activity_ns=int(row["mtime_ns"]),
            queue=session_queue,
        )
        counts[state] += 1
        transcript_only += 1
        conversations.append(
            {
                "session_id": session_id,
                "title": row["title"],
                "title_source": None,
                "project": row["project"],
                "last_activity_at": int(row["mtime_ns"] // 1_000_000),
                "bytes": row["bytes"],
                "on_disk": row["bytes"] > 0,
                "state": state,
                "pinned": False,
                "pinned_rank": None,
                "category": None,
                "archived": False,
                "in_claude_sidebar": False,
                "cloud": (
                    {
                        "conversation_id": binding.get("conversation_id"),
                        "fidelity": binding.get("fidelity"),
                        "last_seen_at": binding.get("last_seen_at"),
                    }
                    if binding is not None
                    else None
                ),
                "delivery": {
                    "pending": int(session_queue.get("pending", 0)),
                    "quarantined": int(session_queue.get("quarantined", 0)),
                },
            }
        )
    conversations.sort(key=lambda item: item["last_activity_at"], reverse=True)

    return {
        "schema_version": 2,
        "account_id": current,
        "accounts": list_accounts(),
        "cloud": cloud_meta,
        "conversations": conversations[:limit],
        "totals": {
            "conversations": len(conversations),
            "transcript_only": transcript_only,
            "transcripts_on_disk": len(transcripts),
            "pinned": pinned_total,
            "index_files_read": totals.get("files", 0),
            "unreadable": totals.get("unreadable", 0),
            **counts,
            # Whole-queue facts, every provider: what the bridge still has to
            # send, and what it is preserving because the server refused it.
            "waiting": waiting,
            "quarantined": quarantined,
        },
    }


# ── One session, fully explained ────────────────────────────────────────────


def _index_facts(entry: ClaudeSessionIndexEntry) -> dict[str, Any]:
    accounts: list[str] = []
    for path in entry.record_paths:
        # <root>/<accountUuid>/<orgUuid>/local_<id>.json
        try:
            accounts.append(path.parent.parent.name)
        except Exception:  # noqa: BLE001 — a malformed path is not worth failing on
            continue
    return {
        "title": entry.title,
        "title_source": entry.title_source,
        "project": entry.workspace_name,
        "git_branch": entry.git_branch,
        "worktree_name": entry.worktree_name,
        "pinned": bool(entry.is_pinned),
        "pinned_rank": entry.pinned_rank,
        "category": entry.category,
        "archived": bool(entry.is_archived),
        "last_activity_at": entry.last_activity_at,
        "record_count": len(entry.record_paths),
        "accounts": sorted(set(accounts)),
    }


async def _session_envelopes(session_id: str) -> list[dict[str, Any]]:
    """Every queued or preserved envelope whose session reduces to this id."""
    from app.services.coding_sessions.service import _safe_delivery_error

    db = get_db()
    try:
        keys = await db.fetchall(
            """SELECT DISTINCT session_key FROM coding_session_bridge_queue_metadata
               WHERE provider = 'claude_code' AND session_key IS NOT NULL"""
        )
    except Exception:
        return []
    matching = [
        str(row["session_key"])
        for row in keys
        if raw_session_id(str(row["session_key"])) == session_id
    ]
    if not matching:
        return []
    placeholders = ",".join("?" for _ in matching)
    items: list[dict[str, Any]] = []
    try:
        pending = await db.fetchall(
            f"""SELECT o.id, o.attempts, o.next_attempt_at, o.last_error,
                       o.created_at, m.action, m.source, m.enqueue_origin,
                       m.item_count, m.payload_bytes
                FROM coding_session_bridge_outbox AS o
                JOIN coding_session_bridge_queue_metadata AS m ON m.receipt_id = o.id
                WHERE m.session_key IN ({placeholders})
                ORDER BY o.id""",
            tuple(matching),
        )
        preserved = await db.fetchall(
            f"""SELECT q.id, q.attempts, q.http_status, q.last_error,
                       q.original_created_at, q.quarantined_at, m.action,
                       m.source, m.enqueue_origin, m.item_count, m.payload_bytes
                FROM coding_session_bridge_quarantine AS q
                JOIN coding_session_bridge_queue_metadata AS m ON m.receipt_id = q.id
                WHERE m.session_key IN ({placeholders})
                ORDER BY q.id""",
            tuple(matching),
        )
    except Exception:
        logger.exception("[claude_overview] could not read envelopes for %s", session_id)
        return []
    now = time.time()
    for row in pending:
        next_attempt = float(row["next_attempt_at"] or 0)
        items.append(
            {
                "receipt_id": int(row["id"]),
                "state": "pending",
                "action": row["action"],
                "source": row["source"],
                "enqueue_origin": row["enqueue_origin"],
                "item_count": int(row["item_count"] or 0),
                "payload_bytes": int(row["payload_bytes"] or 0),
                "created_at": row["created_at"],
                "attempts": int(row["attempts"] or 0),
                "retry_in_seconds": max(0.0, next_attempt - now),
                "http_status": None,
                "quarantined_at": None,
                "error": _safe_delivery_error(row["last_error"]),
            }
        )
    for row in preserved:
        items.append(
            {
                "receipt_id": int(row["id"]),
                "state": "quarantine",
                "action": row["action"],
                "source": row["source"],
                "enqueue_origin": row["enqueue_origin"],
                "item_count": int(row["item_count"] or 0),
                "payload_bytes": int(row["payload_bytes"] or 0),
                "created_at": row["original_created_at"],
                "attempts": int(row["attempts"] or 0),
                "retry_in_seconds": 0.0,
                "http_status": row["http_status"],
                "quarantined_at": row["quarantined_at"],
                "error": _safe_delivery_error(row["last_error"]),
            }
        )
    return items


async def _capture_facts(session_id: str) -> list[dict[str, Any]]:
    """The capture reconciler's attempts to import this transcript."""
    db = get_db()
    try:
        rows = await db.fetchall(
            """SELECT session_key, attempts, last_error, enqueued_at, updated_at
               FROM claude_capture_backfill
               WHERE session_key LIKE ?
               ORDER BY updated_at DESC LIMIT 5""",
            (f"%:{session_id}",),
        )
    except Exception:
        return []
    return [
        {
            "session_key": row["session_key"],
            "attempts": int(row["attempts"] or 0),
            "last_error": row["last_error"],
            "enqueued_at": row["enqueued_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


async def _label_facts(session_id: str) -> dict[str, Any]:
    db = get_db()
    facts: dict[str, Any] = {"metadata_sent": None, "title_pushed": None}
    try:
        row = await db.fetchone(
            "SELECT * FROM claude_session_metadata_sent WHERE provider_session_id = ?",
            (session_id,),
        )
        if row:
            facts["metadata_sent"] = {k: row[k] for k in row.keys()}
    except Exception:
        pass
    try:
        row = await db.fetchone(
            "SELECT * FROM claude_session_title_pushed WHERE provider_session_id = ?",
            (session_id,),
        )
        if row:
            facts["title_pushed"] = {k: row[k] for k in row.keys()}
    except Exception:
        pass
    return facts


def _verdict(
    *,
    state: str,
    cloud_meta: dict[str, Any],
    binding: dict[str, Any] | None,
    envelopes: list[dict[str, Any]],
    capture: list[dict[str, Any]],
    publisher_blocker: dict[str, Any] | None,
    on_disk: bool,
) -> dict[str, str | None]:
    """One plain sentence on what is true, and one on what to do."""
    if state == "failed":
        errors = [
            item["error"]["message"]
            for item in envelopes
            if item["state"] == "quarantine" and item.get("error")
        ]
        reason = errors[0] if errors else "AI Matrx refused at least one delivery for this session."
        return {
            "summary": f"Delivery for this session was refused and is preserved on this Mac: {reason}",
            "remedy": (
                "Open the preserved envelopes below. Retry if the cause was transient; "
                "discard only if the event is genuinely already in AI Matrx with different content."
            ),
        }
    if state == "changed":
        return {
            "summary": (
                "AI Matrx holds this conversation, but Claude's last activity on it here is newer "
                f"than the server's last delivery ({binding.get('last_seen_at') if binding else 'unknown'})."
            ),
            "remedy": (
                "If Claude Code is still open on it, the next prompt delivers the rest through the hook. "
                "Otherwise Sync everything imports the newer transcript."
            ),
        }
    if state == "in_cloud":
        return {
            "summary": f"AI Matrx holds this conversation (fidelity: {binding.get('fidelity') if binding else 'unknown'}).",
            "remedy": None,
        }
    if state == "queued":
        if publisher_blocker is not None:
            return {
                "summary": (
                    "Events for this session are queued on this Mac, and delivery is paused for everything: "
                    f"{publisher_blocker.get('message')}"
                ),
                "remedy": publisher_blocker.get("remedy"),
            }
        pending = [item for item in envelopes if item["state"] == "pending"]
        first = pending[0] if pending else None
        detail = (
            f" Last attempt: {first['error']['message']}"
            if first and first.get("error")
            else ""
        )
        return {
            "summary": f"{len(pending)} envelope(s) for this session are waiting to be delivered.{detail}",
            "remedy": "Delivery retries on its own. Open the envelopes below to retry now or see each attempt.",
        }
    if state == "unknown":
        return {
            "summary": f"AI Matrx could not be asked whether it holds this conversation: {cloud_meta.get('detail') or cloud_meta.get('reason')}",
            "remedy": "Fix the connection or sign-in named above, then refresh.",
        }
    # not_in_cloud
    exhausted = [item for item in capture if item.get("last_error")]
    if exhausted:
        return {
            "summary": (
                "AI Matrx does not hold this conversation. The automatic import tried and stopped: "
                f"{exhausted[0]['last_error']}"
            ),
            "remedy": "Sync everything retries the import; a transcript over the size budget stays local by design.",
        }
    if not on_disk:
        return {
            "summary": "AI Matrx does not hold this conversation, and its transcript is no longer on this Mac — only the sidebar record remains.",
            "remedy": "Nothing can be imported without the transcript. It may exist on another machine.",
        }
    return {
        "summary": "AI Matrx does not hold this conversation: it was never mirrored by the Claude Code hook and has not been imported.",
        "remedy": "Sync everything imports it now; future sessions mirror live when the AI Matrx plugin is connected in Claude Code.",
    }


async def session_diagnosis(session_id: str) -> dict[str, Any] | None:
    """Every fact behind one row's status, from every system that touched it."""
    from app.services.coding_sessions.service import get_coding_session_bridge_outbox

    entries, _totals = await asyncio.to_thread(_session_index, default_sessions_root())
    entry = entries.get(session_id)
    if entry is None:
        return None
    transcripts = await asyncio.to_thread(_transcripts)
    size, mtime_ns = transcripts.get(session_id, (0, 0))
    cloud, cloud_meta = await cloud_inventory()
    binding = cloud.get(session_id)
    envelopes = await _session_envelopes(session_id)
    queue = {
        "pending": sum(1 for item in envelopes if item["state"] == "pending"),
        "quarantined": sum(1 for item in envelopes if item["state"] == "quarantine"),
    }
    state = _session_state(
        cloud_checked=bool(cloud_meta["checked"]),
        binding=binding,
        activity_ns=int(entry.last_activity_at or 0) * 1_000_000,
        queue=queue,
    )
    capture = await _capture_facts(session_id)
    labels = await _label_facts(session_id)
    delivered = await _delivered_by_this_mac()
    publisher_blocker = get_coding_session_bridge_outbox().publisher_blocker
    delivered_ns = delivered.get(session_id)
    return {
        "schema_version": 1,
        "session_id": session_id,
        "state": state,
        "verdict": _verdict(
            state=state,
            cloud_meta=cloud_meta,
            binding=binding,
            envelopes=envelopes,
            capture=capture,
            publisher_blocker=publisher_blocker,
            on_disk=size > 0,
        ),
        "index": _index_facts(entry),
        "transcript": {
            "on_disk": size > 0,
            "bytes": size,
            "modified_at": (
                datetime.fromtimestamp(mtime_ns / 1_000_000_000, timezone.utc).isoformat(
                    timespec="seconds"
                )
                if mtime_ns
                else None
            ),
        },
        "cloud": {**cloud_meta, "binding": binding},
        "delivery": {
            "publisher_blocker": publisher_blocker,
            "envelopes": envelopes,
            "delivered_by_this_mac_at": (
                datetime.fromtimestamp(delivered_ns / 1_000_000_000, timezone.utc).isoformat(
                    timespec="seconds"
                )
                if delivered_ns
                else None
            ),
        },
        "capture": capture,
        "labels": labels,
    }
