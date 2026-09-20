"""Cloud truth, the local delivery ledger, and the ONE state judgement.

WHY IT IS NOT IN A PROVIDER'S MODULE. "Does AI Matrx hold this session?" has
exactly one right answer and one way to reach it, whoever wrote the session.
Until 2026-09-17 this lived inside the Claude Code screen's module, so the day
Codex, Cursor and VS Code sessions joined the list there would have been four
copies of the same judgement and four chances for them to disagree about the
same row. There is one: every provider's rows are joined against the server's
own inventory here, and every row's state comes out of :func:`_session_state`.

THE STATUS IS THE CLOUD'S, NOT THIS MAC'S. Until 2026-09-08 a session read
"synced" only when THIS engine had uploaded it. Nearly every session reaches AI
Matrx through its provider's own hook instead — straight from the CLI to the
server, never through this engine — so the screen said "Not synced" for 1,836
conversations while the cloud held 1,671 of them. The engine asks the server
which sessions it actually holds and reports against that; the local delivery
ledgers only explain HOW a session got there, or why it has not.

ONE INVENTORY PER PROVIDER. The server answers
``GET /coding-sessions/sessions?provider=<name>`` for one provider at a time,
and a Codex answer is no evidence about Claude Code, so each provider gets its
own cache slot, its own in-flight task and its own age. A screen showing four
providers therefore reports four independent freshness facts and never presents
one provider's silence as another's confirmation.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.services.coding_sessions.identity_client import (
    IdentityInventoryBlocked,
    fetch_complete_identity_inventory,
)
from app.services.local_db.database import get_db
from app.services.local_db.repositories import TokenRepo
from app.services.session_freshness import (
    request_session_grant,
    session_blocker,
)

logger = get_logger()

# A session is "changed" when the PROVIDER's OWN last-activity stamp for it is
# newer than the server's last delivery. The hook that mirrors a turn lands
# seconds after it, so inside this window "local newer than cloud" is the
# ordinary shape of a live session.
#
# NOT the transcript file's mtime: measured 2026-09-08, a bulk rewrite had
# stamped 2026-09-07T22:44 on hundreds of untouched transcripts and 1,389 of
# 1,432 cloud-held sessions read "changed" while their last entry matched the
# server's last delivery to the second.
_CHANGED_GRACE_SECONDS = 5 * 60

# The server inventory is one paged read of every bound session (1,671 Claude
# Code and 2,814 Codex here). Cache it briefly so Refresh is instant and a 15 s
# publisher tick cannot turn the screen into a load test on the server.
_CLOUD_CACHE_SECONDS = 45.0

# ── Session states ──────────────────────────────────────────────────────────
#
#   in_cloud      the server holds this session and it is not behind
#   changed       the server holds it, but the copy here is newer than the
#                 server's last delivery by more than the grace window
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


def _reset_cloud_state_for_tests() -> None:
    """Forget every provider's inventory (tests only)."""
    _CLOUD_CACHE.clear()
    _CLOUD_TASK.clear()


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

# One cache and one in-flight task PER PROVIDER. The server's inventory is
# asked for one provider at a time (``GET /coding-sessions/sessions?provider=``)
# and a Codex answer is not evidence about Claude Code, so the two can never
# share a slot. Keyed by provider name; absent = never asked.
_CLOUD_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]], dict[str, Any]]] = {}
_CLOUD_TASK: dict[str, asyncio.Task[Any]] = {}
_CLOUD_LOCK = asyncio.Lock()

# With nothing cached at all there is no honest answer to give yet, so the
# first caller waits this long for the server — long enough that a healthy
# round trip lands inside it, short enough that the whole response still beats
# the one-second bar. Past it the screen says the check is in flight and the
# next read has it.
_CLOUD_COLD_WAIT_SECONDS = 0.75


async def cloud_inventory(
    provider: str = "claude_code", *, force: bool = False
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """The cached server inventory, refreshed in the background.

    The read itself is one paged pass over every bound session (1,671 on this
    Mac), so it is never done on the request path: a cached answer is returned
    immediately and a refresh is kicked behind it. ``meta`` always says how old
    the answer is (``age_seconds``) and whether a newer one is being fetched
    (``refreshing``), so the screen never presents a stale count as live truth.
    """
    now = time.monotonic()
    cached = _CLOUD_CACHE.get(provider)
    fresh = cached is not None and now - cached[0] < _CLOUD_CACHE_SECONDS
    if fresh and not force:
        return cached[1], _cloud_meta_with_age(provider, cached)

    running = provider in _CLOUD_TASK and not _CLOUD_TASK[provider].done()
    if not running:
        try:
            _CLOUD_TASK[provider] = asyncio.get_running_loop().create_task(
                _refresh_cloud_inventory(provider)
            )
        except RuntimeError:
            return await _fetch_cloud_inventory(provider)
        _CLOUD_TASK[provider].add_done_callback(lambda _: None)

    if cached is not None and not force:
        # A stale answer with its age on it beats making the screen wait.
        return cached[1], _cloud_meta_with_age(provider, cached)

    task = _CLOUD_TASK.get(provider)
    assert task is not None
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=_CLOUD_COLD_WAIT_SECONDS)
    except Exception:  # noqa: BLE001 — timeout or failure: there is no answer yet
        pass
    cached = _CLOUD_CACHE.get(provider)
    if cached is not None:
        return cached[1], _cloud_meta_with_age(provider, cached)
    return {}, {
        "checked": False,
        "reason": "cloud_check_in_flight",
        "detail": (
            "AI Matrx is being asked which of these conversations it holds. "
            "The answer lands within a few seconds — refresh to see it."
        ),
        "sessions": 0,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "refreshing": True,
        "age_seconds": None,
    }


def _cloud_meta_with_age(
    provider: str, cached: tuple[float, dict[str, dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    meta = dict(cached[2])
    # A refresh failure keeps confirmed rows for diagnosis, but its tuple time
    # is the failed attempt. Carry the already elapsed age forward so retained
    # rows never become fresh merely because the failed check was recent.
    if meta.get("checked_at") is None:
        meta["age_seconds"] = None
    else:
        base_age = meta.get("age_seconds")
        try:
            retained_age = float(base_age) if base_age is not None else 0.0
        except (TypeError, ValueError):
            retained_age = 0.0
        meta["age_seconds"] = round(
            retained_age + max(0.0, time.monotonic() - cached[0]), 1
        )
    task = _CLOUD_TASK.get(provider)
    meta["refreshing"] = task is not None and not task.done()
    return meta


def _cache_cloud_inventory_refresh_failure(provider: str) -> None:
    """Record an unexpected inventory failure without mislabeling old rows.

    A successful inventory remains useful evidence after a later transport or
    parsing failure, but it cannot establish present cloud state. Its original
    check time and accumulated age remain visible while the 45-second cache TTL
    prevents every request from retrying the same failing call.
    """
    now = time.monotonic()
    cached = _CLOUD_CACHE.get(provider)
    if cached is None:
        _CLOUD_CACHE[provider] = (
            now,
            {},
            {
                "checked": False,
                "reason": "cloud_inventory_refresh_failed",
                "detail": "AI Matrx could not complete the cloud inventory check.",
                "sessions": 0,
                "checked_at": None,
                "age_seconds": None,
            },
        )
        return

    prior_meta = cached[2]
    # Only a success, or a prior failure explicitly retaining that success,
    # has a truthful last-confirmed check time. Expected blocked states do not
    # acquire one just because a later refresh also fails.
    had_success = bool(prior_meta.get("checked")) or (
        prior_meta.get("reason") == "cloud_inventory_refresh_failed"
        and prior_meta.get("checked_at") is not None
    )
    checked_at = prior_meta.get("checked_at") if had_success else None
    if checked_at is None:
        retained_age: float | None = None
    else:
        try:
            base_age = float(prior_meta.get("age_seconds") or 0.0)
        except (TypeError, ValueError):
            base_age = 0.0
        retained_age = round(base_age + max(0.0, now - cached[0]), 1)

    _CLOUD_CACHE[provider] = (
        now,
        cached[1],
        {
            "checked": False,
            "reason": "cloud_inventory_refresh_failed",
            "detail": (
                "AI Matrx could not complete the cloud inventory check. "
                "Previous cloud results are retained but may be out of date."
            ),
            "sessions": len(cached[1]),
            "checked_at": checked_at,
            "age_seconds": retained_age,
        },
    )


async def _refresh_cloud_inventory(provider: str = "claude_code") -> None:
    """Fetch and cache, alone. Failures are cached too — they are answers."""
    async with _CLOUD_LOCK:
        try:
            # THE PROVIDER MUST TRAVEL. Without it this cached every provider's
            # answer under claude_code, so Codex, Cursor and VS Code reported
            # "the cloud check is in flight" for ever — a state that never
            # resolves, which is exactly the silent failure the per-provider
            # cache exists to prevent. Found live 2026-09-18 on a signed-in
            # engine: Codex had 163 bound sessions and the screen never said so.
            await _fetch_cloud_inventory(provider)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the screen keeps its previous answer
            _cache_cloud_inventory_refresh_failure(provider)
            # Exception text can contain an HTTP body, token, or user data.
            # Preserve an actionable ERROR cause without emitting it: the tail
            # is function, basename, and line only, with no source or locals.
            exception = sys.exception()
            frames = traceback.extract_tb(exception.__traceback__) if exception else []
            trace = " > ".join(
                f"{frame.name}@{Path(frame.filename).name}:{frame.lineno}"
                for frame in frames[-6:]
            ) or "no_traceback"
            logger.error(
                "[cloud_state] cloud inventory refresh failed (%s; %s)",
                type(exception).__name__,
                trace,
            )


async def _fetch_cloud_inventory(
    provider: str = "claude_code",
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """One real read of the server's inventory. Always writes the cache."""
    now = time.monotonic()
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
        # The stored token is the engine's, the session is the desktop's: ask
        # the owner for a fresh copy instead of waiting for the next hour — and
        # ask BEFORE describing the gap, so one the desktop is already filling
        # is reported as a refresh in progress and not as a signed-out Mac.
        await request_session_grant(
            lane="coding_session_overview",
            reason="stored access token missing or expired",
        )
        state = session_blocker(lane="coding_session_overview")
        meta["reason"] = state["code"]
        meta["detail"] = " ".join(
            part for part in (state["message"], state.get("remedy")) if part
        )
        _CLOUD_CACHE[provider] = (now, {}, meta)
        return {}, meta

    from app.services.aidream.client import get_aidream_client

    client = get_aidream_client()
    if client is None:
        meta["reason"] = "aidream_server_unconfigured"
        meta["detail"] = "No AI Dream server is configured for this engine."
        _CLOUD_CACHE[provider] = (now, {}, meta)
        return {}, meta

    try:
        rows = await fetch_complete_identity_inventory(
            client=client,
            jwt=str(token_row["access_token"]),
            provider=provider,
        )
    except IdentityInventoryBlocked as exc:
        meta["reason"] = exc.reason
        meta["detail"] = _explain_inventory_block(exc.reason)
        _CLOUD_CACHE[provider] = (now, {}, meta)
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
    _CLOUD_CACHE[provider] = (now, by_session, meta)
    return by_session, meta


def _explain_inventory_block(reason: str) -> str:
    if reason == "aidream_unreachable":
        return "AI Matrx could not be reached from this Mac. Check the connection; the list below shows local facts only."
    if reason.startswith("aidream_error:"):
        detail = reason.split(":", 1)[1]
        if "Cannot name an organization" in detail:
            return (
                "AI Matrx needs to know which organization to answer for, and "
                "nobody has told this Mac which one to use yet. Choose your "
                "organization in Matrx Local and this list will fill in."
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
    if reason.startswith("identity_list_"):
        # These are the identity client's FAIL-CLOSED verdicts: it returns rows
        # only after the server proves the snapshot is complete, because a
        # partial inventory is not a smaller truth, it is unsafe input. Seen on
        # screen 2026-09-18 for cursor and vscode, which the server answers with
        # an inventory that does not satisfy its own completeness contract, so
        # the raw code reached the person. A code is not a sentence.
        return (
            "AI Matrx answered this provider's session list with a snapshot it "
            "could not prove complete, so this Mac will not treat it as the "
            "truth about what the cloud holds. Every row for that provider "
            "reads \"Unknown\" rather than guessing. Nothing here is lost — it "
            "clears on the next server release that answers the list "
            f"completely. (Server reason: {reason}.)"
        )
    return reason


# ── Local delivery ledgers ──────────────────────────────────────────────────


def _delivery_ledger_meta(*, checked: bool) -> dict[str, Any]:
    if checked:
        return {"checked": True, "reason": None, "detail": None}
    return {
        "checked": False,
        "reason": "local_delivery_ledger_unavailable",
        "detail": "AI Matrx could not read this Mac's delivery ledger. Refresh to try again.",
    }


def _log_delivery_ledger_failure(operation: str) -> None:
    """Report an actionable local-ledger failure without exposing its contents."""
    exception = sys.exception()
    frames = traceback.extract_tb(exception.__traceback__) if exception else []
    trace = " > ".join(
        f"{frame.name}@{Path(frame.filename).name}:{frame.lineno}"
        for frame in frames[-6:]
    ) or "no_traceback"
    logger.error(
        "[cloud_state] local delivery ledger %s failed (%s; %s)",
        operation,
        type(exception).__name__,
        trace,
    )


async def _queue_by_session(
    provider: str = "claude_code",
) -> tuple[dict[str, dict[str, int]] | None, dict[str, Any]]:
    """raw session id -> {pending, quarantined} for ONE provider's envelopes.

    Per provider, not across them: the queue records the provider on every row
    (``coding_session_bridge_queue_metadata.provider``), and a Codex envelope
    waiting to be sent says nothing about a Claude Code session that happens
    to share an id prefix.
    """
    counts: dict[str, dict[str, int]] = {}
    try:
        db = get_db()
        rows = await db.fetchall(
            """SELECT queue_state, session_key, COUNT(*) AS n
               FROM coding_session_bridge_queue_metadata
               WHERE provider = ? AND session_key IS NOT NULL
               GROUP BY queue_state, session_key""",
            (provider,),
        )
    except Exception:  # noqa: BLE001 — report an unavailable ledger, never zeroes
        _log_delivery_ledger_failure("queue-by-session")
        return None, _delivery_ledger_meta(checked=False)
    try:
        for row in rows:
            key = raw_session_id(str(row["session_key"]))
            bucket = counts.setdefault(key, {"pending": 0, "quarantined": 0})
            if str(row["queue_state"]) == "quarantine":
                bucket["quarantined"] += int(row["n"])
            else:
                bucket["pending"] += int(row["n"])
    except Exception:  # noqa: BLE001 — malformed rows are unavailable evidence
        _log_delivery_ledger_failure("queue-by-session-decode")
        return None, _delivery_ledger_meta(checked=False)
    return counts, _delivery_ledger_meta(checked=True)


async def _delivered_by_this_mac() -> tuple[dict[str, int] | None, dict[str, Any]]:
    """raw session id -> ns of the last acknowledgement THIS engine recorded.

    Supplementary: it explains how an import got there, it does not decide
    whether a session is in the cloud (the server decides that).
    """
    acked: dict[str, int] = {}
    try:
        db = get_db()
        rows = await db.fetchall(
            "SELECT provider_session_id, last_synced_at FROM claude_session_synced"
        )
    except Exception:  # noqa: BLE001 — an acknowledgement gap is not "never"
        _log_delivery_ledger_failure("acknowledgements")
        return None, _delivery_ledger_meta(checked=False)
    try:
        for row in rows:
            stamp = _parse_iso_ns(row["last_synced_at"])
            if stamp is not None:
                acked[raw_session_id(str(row["provider_session_id"]))] = stamp
    except Exception:  # noqa: BLE001 — malformed rows are not absent acknowledgements
        _log_delivery_ledger_failure("acknowledgements-decode")
        return None, _delivery_ledger_meta(checked=False)
    return acked, _delivery_ledger_meta(checked=True)


async def _queue_totals() -> tuple[tuple[int, int] | None, dict[str, Any]]:
    try:
        db = get_db()
        waiting = await db.fetchone("SELECT COUNT(*) AS n FROM coding_session_bridge_outbox")
        quarantined = await db.fetchone(
            "SELECT COUNT(*) AS n FROM coding_session_bridge_quarantine"
        )
    except Exception:  # noqa: BLE001 — a partial total cannot safely become zero
        _log_delivery_ledger_failure("queue-totals")
        return None, _delivery_ledger_meta(checked=False)
    try:
        counts = (
            int(waiting["n"]) if waiting else 0,
            int(quarantined["n"]) if quarantined else 0,
        )
    except Exception:  # noqa: BLE001 — malformed totals are not zero
        _log_delivery_ledger_failure("queue-totals-decode")
        return None, _delivery_ledger_meta(checked=False)
    return counts, _delivery_ledger_meta(checked=True)


def _session_state(
    *,
    cloud_checked: bool,
    binding: dict[str, Any] | None,
    activity_ns: int,
    queue: dict[str, int] | None,
) -> str:
    if queue is not None and queue.get("quarantined", 0) > 0:
        return "failed"
    if not cloud_checked:
        return "queued" if queue is not None and queue.get("pending", 0) > 0 else "unknown"
    if binding is not None:
        seen_ns = _parse_iso_ns(binding.get("last_seen_at"))
        if (
            seen_ns is not None
            and activity_ns
            and activity_ns > seen_ns + _CHANGED_GRACE_SECONDS * 1_000_000_000
        ):
            return "changed"
        return "in_cloud"
    if queue is None:
        return "unknown"
    if queue.get("pending", 0) > 0:
        return "queued"
    return "not_in_cloud"


