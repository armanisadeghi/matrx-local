"""Claude Code's own session index — the Claude half of ONE feature.

The screen is no longer this module's: the provider-neutral list, the cloud
join and the state judgement moved to
:mod:`app.services.coding_sessions.overview` and
:mod:`app.services.coding_sessions.cloud_state` on 2026-09-17, when Codex,
Cursor and VS Code sessions joined the same list (Arman: *"coding sessions is
one feature"*). What stays here is everything that is true only of Claude
Code: its accounts, its per-account sidebar records, the persisted incremental
index over them, and one session's full diagnosis.

Nothing is hidden and nothing is sampled: every account's records are indexed,
so a conversation that exists anywhere on this Mac appears. Claude keeps one
index record per account, so eight accounts means eight copies of every
conversation — 67,224 files and 3.1 GB here.

THE REQUEST NEVER DOES THAT READ. Until 2026-09-15 it did, and lane V-ML
measured the consequences on the installed app: 31.76 s and 58.96 s for one
call against a hard 60 s client ceiling, 1,209 s on a fresh engine, and
/health answering nothing at all while it ran. Now the reduced records are
persisted incrementally
(:mod:`app.services.coding_sessions.claude_index_store`), the response is built
from the last completed refresh, and the refresh — like the cloud check —
happens behind it. ``index`` and ``cloud`` in the payload say how current each
half is, so a fast answer is never a silent one.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common import claude_index_helper
from app.common.system_logger import get_logger
from app.services.coding_sessions.claude_index_store import (
    DEFAULT_CHUNK_SIZE,
    ClaudeIndexStore,
    IndexSnapshot,
    finalize_refresh,
    plan_refresh,
    read_record_rows,
    refresh_transcripts_sync,
)
from app.services.coding_sessions.claude_session_index import (
    ClaudeSessionIndexEntry,
    default_sessions_root,
)
from app.services.coding_sessions.cloud_state import (
    _delivered_by_this_mac,
    _delivery_ledger_meta,
    _log_delivery_ledger_failure,
    _session_state,
    cloud_inventory,
    raw_session_id,
)
from app.services.local_db.database import get_db

logger = get_logger()

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


def list_accounts(record_counts: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """One row per Claude account on this Mac.

    ``record_counts`` comes from the persisted index. It is not optional for
    performance reasons — it IS the fix: counting with ``rglob`` here walked
    all 67,224 record files ON THE EVENT LOOP inside every overview response,
    which is why /health answered nothing for the whole read (measured by lane
    V-ML, 2026-09-15: 2,182 of 2,261 samples of the asyncio thread inside
    ``os_open``). This function now stats only the eight account directories.
    """
    root = default_sessions_root()
    names = _account_names()
    current = active_account()
    counts = record_counts or {}
    accounts: list[dict[str, Any]] = []
    if not root.is_dir():
        return accounts
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        records = int(counts.get(child.name, 0))
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


# ── The index: persisted, incremental, never on the request path ────────────
#
# Reading every account's copy of every conversation is 67,224 files and 3.1 GB
# on this Mac (measured 2026-09-15). Reading only the active account would be
# ~1.5 s but LOSES 380 conversations, and the screen's whole purpose is that
# nothing is hidden. So the reduced form of every record FILE is persisted in
# its own SQLite database, keyed by that file's (mtime_ns, size), and a refresh
# re-reads only what changed:
#
#   * the request loads rows and answers — it never walks the disk;
#   * a cold engine start after the first run re-reads ~0 of 67,224 files;
#   * the walk and the reads happen in a background refresh nobody waits for.
#
# Before this, the request itself did the scan: 31.76 s and 58.96 s measured on
# the installed app against a hard 60 s client ceiling, 1,209 s on a fresh
# engine, and the engine answered nothing else while it ran.

# How long a loaded snapshot is served before a background refresh is kicked.
# It is not a staleness ceiling on the data — the answer is always the last
# completed refresh; this only rate-limits how often a refresh may start.
_INDEX_REFRESH_INTERVAL_SECONDS = 20.0

# The in-memory snapshot, keyed by the store revision it was built from, so a
# completed refresh invalidates it with one tiny query instead of a walk.
_SNAPSHOT: tuple[int, IndexSnapshot] | None = None
_SNAPSHOT_LOCK = asyncio.Lock()

# One refresh at a time, for the whole engine. The startup warm-up, a screen
# open and the title reconciler all ask for one, and two concurrent refreshes
# would read the same 3.1 GB twice.
_REFRESH_LOCK = asyncio.Lock()
_REFRESH_TASK: asyncio.Task[Any] | None = None
# When a refresh last ran (set at its start AND at its end, so the rate limit
# below measures from completion — a 44 s first build must not be followed by
# another walk the moment it lands).
_LAST_REFRESH_AT: float = 0.0
_LAST_REFRESH_ERROR: str | None = None

_STORE: ClaudeIndexStore | None = None


def index_store() -> ClaudeIndexStore:
    global _STORE
    if _STORE is None:
        _STORE = ClaudeIndexStore()
    return _STORE


def _reset_index_state_for_tests(store: ClaudeIndexStore | None = None) -> None:
    """Point the module at a different store and forget what it loaded."""
    global _STORE, _SNAPSHOT, _REFRESH_TASK, _LAST_REFRESH_AT, _LAST_REFRESH_ERROR
    _STORE = store
    _SNAPSHOT = None
    _REFRESH_TASK = None
    _LAST_REFRESH_AT = 0.0
    _LAST_REFRESH_ERROR = None


async def index_facts() -> dict[str, Any]:
    """Counts + the resolved pin scope, without materialising the index.

    For pollable status surfaces. See
    :meth:`ClaudeIndexStore.index_facts` for why it exists.
    """
    return await asyncio.to_thread(index_store().index_facts)


async def index_snapshot(*, record_paths: bool = False) -> IndexSnapshot:
    """The persisted index, loaded from SQLite. No filesystem walk, ever.

    ``record_paths=True`` also loads every account's copy of each conversation
    — the RETURN direction (writing a rename back into Claude's own records)
    and one session's diagnosis need it; the screen never does, and it is the
    difference between a ~60 ms load and a ~700 ms one. It is not cached: both
    callers are background or single-row paths.
    """
    global _SNAPSHOT
    store = index_store()
    if record_paths:
        return await asyncio.to_thread(store.load, record_paths=True)
    revision = await asyncio.to_thread(store.revision)
    cached = _SNAPSHOT
    if cached is not None and cached[0] == revision:
        return cached[1]
    async with _SNAPSHOT_LOCK:
        cached = _SNAPSHOT
        if cached is not None and cached[0] == revision:
            return cached[1]
        snapshot = await asyncio.to_thread(store.load)
        _SNAPSHOT = (snapshot.revision, snapshot)
        return snapshot


async def _refresh_in_helper(root: Path, store: ClaudeIndexStore) -> dict[str, Any]:
    """Run the refresh in a short-lived helper PROCESS. Raises on any failure."""
    command = claude_index_helper.helper_command(root, store.path)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=claude_index_helper.HELPER_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        await _reap_index_helper(process)
        raise TimeoutError(
            "session-index helper exceeded "
            f"{claude_index_helper.HELPER_TIMEOUT_SECONDS:.0f}s"
        ) from exc
    except BaseException:
        # Cancellation during shutdown must not orphan a scanner.
        await _reap_index_helper(process)
        raise
    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace").strip()[:500]
        raise RuntimeError(detail or f"exit status {process.returncode}")
    return claude_index_helper.decode_payload(stdout)


async def _reap_index_helper(process: asyncio.subprocess.Process) -> None:
    """Stop a helper that cannot finish its refresh."""
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _refresh_in_threads(
    root: Path, store: ClaudeIndexStore, *, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> dict[str, Any]:
    """The same refresh, chunk by chunk, in worker threads.

    The stat walk releases the GIL; each chunk is 256 records (~12 MB) and the
    loop awaits between chunks, so the event loop keeps answering /health for
    the whole build. This is the announced fallback when the helper process
    cannot be spawned — never a silent second implementation: it calls the same
    store functions the helper does.
    """
    started = time.monotonic()
    changed, alive, truncated, _total = await asyncio.to_thread(plan_refresh, root, store)
    for start in range(0, len(changed), chunk_size):
        chunk = changed[start : start + chunk_size]
        rows = await asyncio.to_thread(read_record_rows, chunk)
        await asyncio.to_thread(store.upsert, rows)
        await asyncio.sleep(0)
    removed = await asyncio.to_thread(store.prune, alive)
    return await asyncio.to_thread(
        finalize_refresh,
        store,
        changed=len(changed),
        removed=removed,
        truncated=truncated,
        duration=time.monotonic() - started,
    )


async def refresh_index(root: Path | None = None) -> dict[str, Any]:
    """One incremental refresh of the persisted index. Single-flight."""
    global _LAST_REFRESH_ERROR, _LAST_REFRESH_AT
    sessions_root = root or default_sessions_root()
    store = index_store()
    async with _REFRESH_LOCK:
        _LAST_REFRESH_AT = time.monotonic()
        try:
            result = await _refresh_in_helper(sessions_root, store)
        except Exception as exc:
            logger.warning(
                "[claude_overview] Session-index helper process unavailable (%s) — "
                "refreshing in this engine instead, in bounded chunks. The screen "
                "is correct either way; remedy: check that the engine executable "
                "can spawn itself with %s.",
                exc,
                claude_index_helper.HELPER_ARGUMENT,
            )
            try:
                result = await _refresh_in_threads(sessions_root, store)
            except Exception as inner:  # noqa: BLE001 — a failed refresh is a STATE
                _LAST_REFRESH_ERROR = f"{type(inner).__name__}: {inner}"
                logger.exception("[claude_overview] index refresh failed")
                raise
        result.update(await _refresh_transcripts(store))
        _LAST_REFRESH_AT = time.monotonic()
        _LAST_REFRESH_ERROR = None
        return result


async def _refresh_transcripts(store: ClaudeIndexStore) -> dict[str, Any]:
    """The transcripts half of a refresh: sizes, and titles for the orphans.

    It stays in the engine (in a thread) rather than the helper process: it is
    1,638 stats plus the handful of orphan summaries whose transcript moved,
    and the importer's bounded summary reader lives in a module the helper
    deliberately never imports.
    """
    from app.services.coding_sessions.claude_history import _read_summary

    sidebar_ids = set(await asyncio.to_thread(store.sidebar_session_ids))
    try:
        return await asyncio.to_thread(
            refresh_transcripts_sync,
            store,
            sidebar_ids=sidebar_ids,
            root=_claude_config_dir() / "projects",
            read_summary=_read_summary,
        )
    except Exception as exc:  # noqa: BLE001 — the records half still stands
        logger.warning(
            "[claude_overview] transcript refresh failed (%s) — conversation "
            "sizes and CLI-only sessions may be one refresh behind",
            exc,
        )
        return {"transcripts_error": f"{type(exc).__name__}: {exc}"}


def index_refreshing() -> bool:
    """Is a refresh in flight — by ANY route?

    The lock, not the background task: the startup warm-up and the title
    reconciler await ``refresh_index`` directly, and a screen opened during
    one of those must still read "refreshing" rather than a bare "cold".
    """
    if _REFRESH_LOCK.locked():
        return True
    return _REFRESH_TASK is not None and not _REFRESH_TASK.done()


def start_index_refresh(root: Path | None = None, *, minimum_interval: float | None = None) -> bool:
    """Kick a background refresh unless one is running or one just ran.

    Returns whether a refresh is now in flight. Nothing awaits it: the screen
    is answered from the persisted index and the next open shows the newer one.
    """
    global _REFRESH_TASK
    if index_refreshing():
        return True
    interval = (
        _INDEX_REFRESH_INTERVAL_SECONDS if minimum_interval is None else minimum_interval
    )
    if _LAST_REFRESH_AT and time.monotonic() - _LAST_REFRESH_AT < interval:
        return False

    async def _run() -> None:
        try:
            await refresh_index(root)
        except Exception:  # noqa: BLE001 — refresh_index already logged it
            return

    try:
        _REFRESH_TASK = asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        return False
    _REFRESH_TASK.add_done_callback(lambda _: None)
    return True


def index_report(snapshot: IndexSnapshot) -> dict[str, Any]:
    """What the screen says about the index behind the rows it is showing."""
    refreshing = index_refreshing()
    if not snapshot.complete:
        state = "cold"
    elif refreshing:
        state = "refreshing"
    else:
        state = "fresh"
    return {
        "state": state,
        "refreshing": refreshing,
        "files_read": int(snapshot.totals.get("files", 0)),
        "conversations": int(snapshot.totals.get("records", 0)),
        "updated_at": snapshot.updated_at,
        "changed_files": snapshot.last_changed_files,
        "duration_seconds": snapshot.last_duration_seconds,
        "limit_reached": bool(snapshot.totals.get("truncated")),
        "unreadable": int(snapshot.totals.get("unreadable", 0)),
        "error": _LAST_REFRESH_ERROR,
    }


async def read_session_index_async(
    root: Path | None = None,
    *,
    refresh: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    """The complete index for a background reconciler, refreshed first.

    Unlike the screen, the title reconciler has nobody waiting on it and its
    whole job is to notice a rename, so it pays for a refresh before reading.
    """
    if refresh:
        try:
            await refresh_index(root)
        except Exception:  # noqa: BLE001 — refresh_index logged it; read what we have
            pass
    # WITH record paths: the reconciler writes a rename back into every
    # account's copy of the record, so it needs to know where they all are.
    snapshot = await index_snapshot(record_paths=True)
    return snapshot.entries, snapshot.totals


async def warm_index_cache(root: Path | None = None) -> None:
    """Refresh the persisted index at engine start, before anyone asks.

    After the first ever build this is a stat walk plus the handful of records
    that changed while the app was closed — seconds, not the 25 s (or, on a
    fresh engine, 1,209 s) full read the first screen open used to pay.
    """
    await refresh_index(root)
    await index_snapshot()

def _transcript_only_rows(
    orphan_ids: list[str],
    transcripts: dict[str, tuple[int, int]],
    summaries: dict[str, tuple[str | None, str | None]],
) -> list[dict[str, Any]]:
    """Rows for transcripts that have NO Claude sidebar record anywhere.

    A conversation only appeared on this screen if Claude Desktop had written
    a sidebar index record for it. Sessions started from the plain `claude`
    CLI never get one, so they sat on disk — readable, syncable, 80 of them and
    113 MB on 2026-09-11 — and the screen simply never listed them. The
    importer's own source walk already reaches them (it reads the projects
    tree, not the index); only the screen was hiding them.

    Title and project were read from the transcripts here, on every request:
    ~80 bounded reads (first 40 lines + last 256 KB each) inside the response.
    They are now read once per changed transcript by the background refresh and
    come in from the persisted index, so this function touches no disk at all.
    """
    rows: list[dict[str, Any]] = []
    for session_id in orphan_ids:
        size, mtime_ns = transcripts.get(session_id, (0, 0))
        stored_title, project = summaries.get(session_id, (None, None))
        title = stored_title or f"Claude session {session_id[:8]}"
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




def _index_facts(entry: ClaudeSessionIndexEntry) -> dict[str, Any]:
    accounts: list[str] = []
    for path in entry.record_paths:
        # <root>/<accountUuid>/<orgUuid>/local_<id>.json
        try:
            accounts.append(path.parent.parent.name)
        except Exception:  # noqa: BLE001 — a malformed path is not worth failing on
            continue
    return {
        "in_claude_sidebar": True,
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


async def _session_envelopes(
    session_id: str,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Every queued or preserved envelope whose session reduces to this id."""
    from app.services.coding_sessions.service import _safe_delivery_error

    try:
        db = get_db()
        keys = await db.fetchall(
            """SELECT DISTINCT session_key FROM coding_session_bridge_queue_metadata
               WHERE provider = 'claude_code' AND session_key IS NOT NULL"""
        )
    except Exception:  # noqa: BLE001 — unavailable evidence is not an empty queue
        _log_delivery_ledger_failure("session-envelope-keys")
        return None, _delivery_ledger_meta(checked=False)
    try:
        matching = [
            str(row["session_key"])
            for row in keys
            if raw_session_id(str(row["session_key"])) == session_id
        ]
    except Exception:  # noqa: BLE001 — malformed metadata is unavailable evidence
        _log_delivery_ledger_failure("session-envelope-keys-decode")
        return None, _delivery_ledger_meta(checked=False)
    if not matching:
        return [], _delivery_ledger_meta(checked=True)
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
    except Exception:  # noqa: BLE001 — do not log the session id or error body
        _log_delivery_ledger_failure("session-envelopes")
        return None, _delivery_ledger_meta(checked=False)
    try:
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
    except Exception:  # noqa: BLE001 — malformed envelope rows are unavailable
        _log_delivery_ledger_failure("session-envelopes-decode")
        return None, _delivery_ledger_meta(checked=False)
    return items, _delivery_ledger_meta(checked=True)


def _diagnostic_facts_meta(*, checked: bool) -> dict[str, Any]:
    if checked:
        return {"checked": True, "reason": None, "detail": None}
    return {
        "checked": False,
        "reason": "local_diagnostic_facts_unavailable",
        "detail": "AI Matrx could not read this Mac's session diagnostics. Refresh to try again.",
    }


async def _capture_facts(
    session_id: str,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """The capture reconciler's attempts to import this transcript."""
    try:
        db = get_db()
        rows = await db.fetchall(
            """SELECT session_key, attempts, last_error, enqueued_at, updated_at
               FROM claude_capture_backfill
               WHERE session_key LIKE ?
               ORDER BY updated_at DESC LIMIT 5""",
            (f"%:{session_id}",),
        )
        facts = [
            {
                "session_key": row["session_key"],
                "attempts": int(row["attempts"] or 0),
                "last_error": row["last_error"],
                "enqueued_at": row["enqueued_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    except Exception:  # noqa: BLE001 — unavailable attempts are not no attempts
        _log_delivery_ledger_failure("capture-facts")
        return None, _diagnostic_facts_meta(checked=False)
    return facts, _diagnostic_facts_meta(checked=True)


async def _label_facts(session_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    facts: dict[str, Any] = {"metadata_sent": None, "title_pushed": None}
    try:
        db = get_db()
        row = await db.fetchone(
            "SELECT * FROM claude_session_metadata_sent WHERE provider_session_id = ?",
            (session_id,),
        )
        if row:
            facts["metadata_sent"] = {k: row[k] for k in row.keys()}
        row = await db.fetchone(
            "SELECT * FROM claude_session_title_pushed WHERE provider_session_id = ?",
            (session_id,),
        )
        if row:
            facts["title_pushed"] = {k: row[k] for k in row.keys()}
    except Exception:  # noqa: BLE001 — no label evidence is not no label activity
        _log_delivery_ledger_failure("label-facts")
        return None, _diagnostic_facts_meta(checked=False)
    return facts, _diagnostic_facts_meta(checked=True)


def _verdict(
    *,
    state: str,
    cloud_meta: dict[str, Any],
    binding: dict[str, Any] | None,
    envelopes: list[dict[str, Any]] | None,
    delivery_checked: bool,
    capture: list[dict[str, Any]] | None,
    publisher_blocker: dict[str, Any] | None,
    on_disk: bool,
) -> dict[str, str | None]:
    """One plain sentence on what is true, and one on what to do."""
    if state == "failed":
        errors = [
            item["error"]["message"]
            for item in (envelopes or [])
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
        pending = [item for item in (envelopes or []) if item["state"] == "pending"]
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
        if cloud_meta.get("checked") and not delivery_checked:
            return {
                "summary": (
                    "AI Matrx could not read this Mac's delivery ledger, so it cannot tell "
                    "whether a delivery is waiting or preserved for this conversation."
                ),
                "remedy": "Refresh after the local database is available again.",
            }
        return {
            "summary": f"AI Matrx could not be asked whether it holds this conversation: {cloud_meta.get('detail') or cloud_meta.get('reason')}",
            "remedy": "Fix the connection or sign-in named above, then refresh.",
        }
    # not_in_cloud
    if capture is None:
        return {
            "summary": (
                "AI Matrx does not hold this conversation, but this Mac could not read "
                "the automatic import record."
            ),
            "remedy": "Refresh after the local database is available again.",
        }
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

    # With record paths: this view reports which accounts hold a copy.
    snapshot = await index_snapshot(record_paths=True)
    start_index_refresh()
    entry = snapshot.entries.get(session_id)
    transcripts = snapshot.transcripts
    size, mtime_ns = transcripts.get(session_id, (0, 0))
    # A CLI-only session has a transcript and no sidebar record. It is listed
    # on the screen, so its diagnosis must open too — a row that opens into a
    # 404 is a screen that lies. Only "no record AND no transcript" is unknown.
    if entry is None and size == 0:
        return None
    if entry is None:
        transcript_row = _transcript_only_rows(
            [session_id], transcripts, snapshot.orphan_summaries
        )
        summary = transcript_row[0] if transcript_row else None
    else:
        summary = None
    cloud, cloud_meta = await cloud_inventory()
    binding = cloud.get(session_id)
    envelopes, envelopes_meta = await _session_envelopes(session_id)
    queue = {
        "pending": sum(1 for item in envelopes if item["state"] == "pending"),
        "quarantined": sum(1 for item in envelopes if item["state"] == "quarantine"),
    } if envelopes is not None else None
    state = _session_state(
        cloud_checked=bool(cloud_meta["checked"]),
        binding=binding,
        activity_ns=(
            int(entry.last_activity_at or 0) * 1_000_000 if entry is not None else int(mtime_ns)
        ),
        queue=queue,
    )
    capture, capture_meta = await _capture_facts(session_id)
    labels, labels_meta = await _label_facts(session_id)
    delivered, delivered_meta = await _delivered_by_this_mac()
    delivery_meta = _delivery_ledger_meta(
        checked=bool(envelopes_meta["checked"]) and bool(delivered_meta["checked"])
    )
    publisher_blocker = get_coding_session_bridge_outbox().publisher_blocker
    delivered_ns = delivered.get(session_id) if delivered is not None else None
    return {
        "schema_version": 1,
        "session_id": session_id,
        "state": state,
        "verdict": _verdict(
            state=state,
            cloud_meta=cloud_meta,
            binding=binding,
            envelopes=envelopes,
            delivery_checked=bool(delivery_meta["checked"]),
            capture=capture,
            publisher_blocker=publisher_blocker,
            on_disk=size > 0,
        ),
        "index": (
            _index_facts(entry)
            if entry is not None
            else {
                # Same shape as an indexed session so the dialog renders every
                # row; the values are honest ("none", not invented).
                "in_claude_sidebar": False,
                "title": summary["title"] if summary else None,
                "title_source": None,
                "project": summary["project"] if summary else None,
                "git_branch": None,
                "worktree_name": None,
                "pinned": False,
                "pinned_rank": None,
                "category": None,
                "archived": False,
                "last_activity_at": int(mtime_ns // 1_000_000),
                "record_count": 0,
                "accounts": [],
                "note": (
                    "Claude's sidebar has no record of this session — it was "
                    "started from the command line. It is on this Mac and syncs "
                    "like any other."
                ),
            }
        ),
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
            "ledger": delivery_meta,
            "publisher_blocker": publisher_blocker,
            "envelopes": envelopes,
            "delivered_by_this_mac_at": (
                datetime.fromtimestamp(delivered_ns / 1_000_000_000, timezone.utc).isoformat(
                    timespec="seconds"
                )
                if delivered_ns and delivered is not None
                else None
            ),
        },
        "capture": capture,
        "capture_ledger": capture_meta,
        "labels": labels,
        "labels_ledger": labels_meta,
    }
