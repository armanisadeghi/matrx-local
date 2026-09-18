"""Everything the Coding Sessions screen shows, for EVERY provider, in one read.

Arman, 2026-09-17: *"coding sessions is one feature"*. So this is one request
that answers three questions about four providers without being asked twice:
what is on this Mac, is it in AI Matrx, and what is broken. Until today the
same screen answered them for Claude Code alone while the server already held
**2,814 Codex, 4 Cursor and 2 VS Code sessions** that no local screen would
ever show.

HOW IT COMPOSES. Each provider's adapter
(:mod:`app.services.coding_sessions.session_providers`) returns its sessions in
the ONE row shape and its own index state. This module does the rest exactly
once, for all of them: ask the server which sessions it holds (per provider —
the inventory endpoint takes one provider at a time), read the local delivery
queue, and judge every row's state with the single
:func:`cloud_state._session_state`. Four providers, one judgement; a row's
state cannot mean different things depending on who wrote the session.

NOTHING HERE WALKS A DISK OR WAITS ON A SERVER. Every adapter answers from its
persisted index and kicks its own refresh behind the response; the cloud check
is the last completed inventory with its age on it. ``providers[].index`` and
``providers[].cloud`` say how current each half of each provider is, so a fast
answer is never a silent one — and a provider whose first index is still
building says "cold" in the same word the screen already knows.

A PROVIDER THAT CANNOT LIST LOCALLY IS STILL LISTED. VS Code keeps no local
record of a session on a Mac (measured 2026-09-17: no AI Matrx extension, no
spool, and a delivered outbox row is deleted by design), so its rows come from
the server's inventory and are marked ``on_disk: false`` with the provider's
own sentence explaining why. An empty tab with no reason would read as a bug;
this reads as the truth.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.common.system_logger import get_logger
from app.services.coding_sessions.cloud_state import (
    SESSION_STATES,
    _delivery_ledger_meta,
    _queue_by_session,
    _queue_totals,
    _session_state,
    cloud_inventory,
)
from app.services.coding_sessions.continuation import continuation_hint
from app.services.coding_sessions.session_providers import (
    ProviderListing,
    SessionSummary,
    session_provider,
    session_providers,
)

logger = get_logger()

SCHEMA_VERSION = 3

_MAX_CONVERSATIONS = 5000


async def overview(limit: int = _MAX_CONVERSATIONS) -> dict[str, Any]:
    """Every provider's sessions, with cloud state — the whole screen."""
    adapters = session_providers()
    listings = await asyncio.gather(
        *(_listing_or_failure(adapter) for adapter in adapters)
    )
    clouds = await asyncio.gather(
        *(cloud_inventory(listing.provider) for listing in listings)
    )
    queues = await asyncio.gather(
        *(_queue_by_session(listing.provider) for listing in listings)
    )
    queue_totals, totals_meta = await _queue_totals()
    # A COUNT AND ITS PER-SESSION EVIDENCE ARE ONE CLAIM. If either half of
    # the local delivery ledger could not be read — the whole-queue totals, or
    # any provider's per-session grouping — the screen keeps its independent
    # cloud and list facts but reports every delivery number as unavailable.
    # It never mixes known values with unknown ones, and never turns an
    # unreadable ledger into a reassuring zero.
    delivery_checked = bool(totals_meta["checked"]) and all(
        bool(meta["checked"]) for _rows, meta in queues
    )

    conversations: list[dict[str, Any]] = []
    counts = {state: 0 for state in SESSION_STATES}
    provider_blocks: list[dict[str, Any]] = []
    pinned_total = 0
    accounts: list[dict[str, Any]] = []
    transcript_only = 0
    transcripts_on_disk = 0
    index_files_read = 0
    unreadable = 0
    index_limit_reached = False

    for listing, (cloud, cloud_meta), (queue, queue_meta) in zip(
        listings, clouds, queues
    ):
        provider = listing.provider
        if not delivery_checked:
            queue = None
        provider_counts = {state: 0 for state in SESSION_STATES}
        seen: set[str] = set()
        for row in listing.rows:
            binding = _binding_for(row, cloud)
            seen.add(row.session_id)
            seen.update(row.alias_ids)
            session_queue = _queue_for(row, queue)
            state = _session_state(
                cloud_checked=bool(cloud_meta["checked"]),
                binding=binding,
                activity_ns=int(row.last_activity_at or 0) * 1_000_000,
                queue=session_queue,
            )
            counts[state] += 1
            provider_counts[state] += 1
            if row.pinned:
                pinned_total += 1
            conversations.append(
                _payload_row(
                    row,
                    state=state,
                    binding=binding,
                    session_queue=session_queue,
                )
            )
        # A provider with no local listing still shows what AI Matrx holds.
        if not listing.lists_locally and cloud_meta["checked"]:
            for session_id, binding in cloud.items():
                if session_id in seen:
                    continue
                session_queue = (
                    queue.get(session_id, {"pending": 0, "quarantined": 0})
                    if queue is not None
                    else None
                )
                row = _cloud_only_row(provider, session_id, binding)
                counts["in_cloud"] += 1
                provider_counts["in_cloud"] += 1
                conversations.append(
                    _payload_row(
                        row,
                        state="in_cloud",
                        binding=binding,
                        session_queue=session_queue,
                    )
                )
        if not delivery_checked:
            provider_counts["queued"] = None
            provider_counts["failed"] = None
        provider_blocks.append(
            {
                "provider": provider,
                "index": listing.index,
                "cloud": cloud_meta,
                "totals": {**listing.totals, **provider_counts},
                "note": listing.note,
                "supports_pins": listing.supports_pins,
                "supports_resume": listing.supports_resume,
                "lists_locally": listing.lists_locally,
                "continuation": continuation_hint(None, provider),
            }
        )
        # The Claude Code block still carries the account facts the screen
        # shows above the list; no other provider has accounts.
        provider_accounts = listing.totals.get("accounts")
        if isinstance(provider_accounts, list):
            accounts = provider_accounts
        transcript_only += int(listing.totals.get("transcript_only", 0) or 0)
        transcripts_on_disk += int(listing.totals.get("transcripts_on_disk", 0) or 0)
        index_files_read += int(
            listing.totals.get("index_files_read", listing.totals.get("rollout_files", 0))
            or 0
        )
        unreadable += int(listing.totals.get("unreadable", 0) or 0)
        index_limit_reached = index_limit_reached or bool(
            listing.index.get("limit_reached")
        )

    conversations.sort(key=lambda item: item["last_activity_at"] or 0, reverse=True)
    if not delivery_checked:
        counts["queued"] = None
        counts["failed"] = None
        waiting = quarantined = None
    else:
        assert queue_totals is not None
        waiting, quarantined = queue_totals

    from app.services.coding_sessions.claude_overview import active_account

    return {
        "schema_version": SCHEMA_VERSION,
        "account_id": active_account(),
        # Which providers this payload actually LISTS sessions for. The screen
        # reads this instead of assuming Claude Code, so the filter grows with
        # the engine and never with a hard-coded name.
        "listed_providers": [listing.provider for listing in listings],
        "accounts": accounts,
        # Per provider: its index state, its cloud freshness, its counts, and
        # the one sentence about what it cannot show.
        "providers": provider_blocks,
        "cloud": _aggregate_cloud(provider_blocks),
        "delivery_ledger": _delivery_ledger_meta(checked=delivery_checked),
        "index": _aggregate_index(provider_blocks),
        "conversations": conversations[:limit],
        "totals": {
            "conversations": len(conversations),
            "transcript_only": transcript_only,
            "transcripts_on_disk": transcripts_on_disk,
            "pinned": pinned_total,
            "index_files_read": index_files_read,
            "unreadable": unreadable,
            # A cap never lies: when any provider's reader stopped at its file
            # cap the list below is incomplete. Say so on the screen rather
            # than quietly showing a short list.
            "index_limit_reached": index_limit_reached,
            **counts,
            # Whole-queue facts, every provider: what the bridge still has to
            # send, and what it is preserving because the server refused it.
            "waiting": waiting,
            "quarantined": quarantined,
        },
    }


async def _listing_or_failure(adapter: Any) -> ProviderListing:
    """One provider's listing; a provider that throws does not empty the screen.

    A broken adapter is that provider's state, not everybody's: the other
    three still list, and this one says what went wrong on its own block.
    """
    try:
        return await adapter.listing()
    except Exception as exc:  # noqa: BLE001 — one provider's failure is a STATE
        logger.exception(
            "[overview] provider %s could not list its sessions", adapter.provider
        )
        return ProviderListing(
            provider=adapter.provider,
            rows=[],
            index={
                "state": "cold",
                "refreshing": False,
                "files_read": 0,
                "updated_at": None,
                "changed_files": None,
                "duration_seconds": None,
                "limit_reached": False,
                "unreadable": 0,
                "error": f"{type(exc).__name__}: {exc}",
            },
            totals={"sessions": 0},
            note=(
                "AI Matrx could not read this provider's sessions on this Mac "
                f"({type(exc).__name__}: {exc}). Refresh to try again; the other "
                "providers above are unaffected."
            ),
        )


def _binding_for(
    row: SessionSummary, cloud: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """The server's binding for this row, under any id it is known by.

    Codex forks a thread when its context rolls over, so the rollout's file id
    and the session id inside it can both name the same session on the server.
    Checking the row's own id first keeps the primary identity authoritative.
    """
    binding = cloud.get(row.session_id)
    if binding is not None:
        return binding
    for alias in row.alias_ids:
        binding = cloud.get(alias)
        if binding is not None:
            return binding
    return None


def _queue_for(
    row: SessionSummary, queue: dict[str, dict[str, int]] | None
) -> dict[str, int] | None:
    if queue is None:
        return None
    for key in (row.session_id, *row.alias_ids):
        found = queue.get(key)
        if found is not None:
            return found
    return {"pending": 0, "quarantined": 0}


def _cloud_only_row(
    provider: str, session_id: str, binding: dict[str, Any]
) -> SessionSummary:
    """A session AI Matrx holds that this Mac keeps no local copy of."""
    from app.services.coding_sessions.cloud_state import _parse_iso_ns

    seen_ns = _parse_iso_ns(binding.get("last_seen_at")) or 0
    title = binding.get("conversation_title")
    return SessionSummary(
        provider=provider,
        session_id=session_id,
        title=str(title) if isinstance(title, str) and title else f"Session {session_id[:8]}",
        title_source=binding.get("title_source"),
        project=None,
        last_activity_at=seen_ns // 1_000_000,
        bytes=None,
        on_disk=False,
        pinned=None,
        pinned_rank=None,
        category=None,
        archived=False,
        in_claude_sidebar=None,
        facts={"local_copy": False},
    )


def _payload_row(
    row: SessionSummary,
    *,
    state: str,
    binding: dict[str, Any] | None,
    session_queue: dict[str, int] | None,
) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "provider": row.provider,
        "continuation": continuation_hint(row.session_id, row.provider),
        "title": row.title or "Untitled",
        "title_source": row.title_source,
        "project": row.project,
        "last_activity_at": row.last_activity_at,
        "bytes": row.bytes,
        "on_disk": row.on_disk,
        "state": state,
        "pinned": row.pinned,
        "pinned_rank": row.pinned_rank,
        "category": row.category,
        "archived": row.archived,
        "in_claude_sidebar": row.in_claude_sidebar,
        "facts": row.facts,
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
            "pending": (
                int(session_queue.get("pending", 0)) if session_queue is not None else None
            ),
            "quarantined": (
                int(session_queue.get("quarantined", 0))
                if session_queue is not None
                else None
            ),
        },
    }


def _aggregate_cloud(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """One cloud fact for the whole screen, and it is the WEAKEST one.

    "Checked" across four providers can only mean all four were checked. One
    provider the server could not be asked about makes the aggregate unchecked
    and carries that provider's own reason, because a partial inventory is not
    a smaller truth — it is unsafe input.
    """
    metas = [block["cloud"] for block in blocks]
    unchecked = [meta for meta in metas if not meta.get("checked")]
    sessions = sum(int(meta.get("sessions") or 0) for meta in metas)
    ages = [meta.get("age_seconds") for meta in metas if meta.get("age_seconds") is not None]
    refreshing = any(bool(meta.get("refreshing")) for meta in metas)
    if not unchecked:
        return {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": sessions,
            "checked_at": min(
                (meta.get("checked_at") for meta in metas if meta.get("checked_at")),
                default=None,
            ),
            "refreshing": refreshing,
            "age_seconds": max(ages) if ages else None,
        }
    first = unchecked[0]
    providers = ", ".join(
        block["provider"] for block in blocks if not block["cloud"].get("checked")
    )
    return {
        "checked": False,
        "reason": first.get("reason"),
        "detail": (
            f"{first.get('detail') or first.get('reason')} "
            f"(providers not checked: {providers})"
        ),
        "sessions": sessions,
        "checked_at": first.get("checked_at"),
        "refreshing": refreshing,
        "age_seconds": max(ages) if ages else None,
    }


def _aggregate_index(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """One index state for the screen: the least finished of them all.

    Any provider still building its first index makes the screen "cold", which
    is what makes the existing cold-index polling keep working the moment a
    second provider joins the list.
    """
    indexes = [block["index"] for block in blocks]
    states = {index.get("state") for index in indexes}
    if "cold" in states:
        state = "cold"
    elif any(index.get("refreshing") for index in indexes):
        state = "refreshing"
    else:
        state = "fresh"
    errors = [index.get("error") for index in indexes if index.get("error")]
    return {
        "state": state,
        "refreshing": any(bool(index.get("refreshing")) for index in indexes),
        "files_read": sum(int(index.get("files_read") or 0) for index in indexes),
        "conversations": sum(
            int(block["totals"].get("sessions") or 0) for block in blocks
        ),
        "updated_at": min(
            (index.get("updated_at") for index in indexes if index.get("updated_at")),
            default=None,
        ),
        "changed_files": sum(
            int(index.get("changed_files") or 0)
            for index in indexes
            if index.get("changed_files") is not None
        )
        or None,
        "duration_seconds": max(
            (
                float(index["duration_seconds"])
                for index in indexes
                if index.get("duration_seconds") is not None
            ),
            default=None,
        ),
        "limit_reached": any(bool(index.get("limit_reached")) for index in indexes),
        "unreadable": sum(int(index.get("unreadable") or 0) for index in indexes),
        "error": "; ".join(errors) if errors else None,
    }


async def session_diagnosis(
    session_id: str, provider: str = "claude_code"
) -> dict[str, Any] | None:
    """Every fact behind one row's status, whichever provider wrote it.

    Claude Code's diagnosis is the deepest because Claude Code is the only
    provider with a sidebar ledger, a label writer and an import reconciler to
    account for; it keeps its own module. Every other provider is diagnosed
    from the facts that exist for it — its adapter's row, the server's binding,
    and the same queued/preserved envelopes and the same verdict — so the
    dialog never opens on a row and says nothing.
    """
    if provider == "claude_code":
        from app.services.coding_sessions.claude_overview import (
            session_diagnosis as claude_diagnosis,
        )

        return await claude_diagnosis(session_id)

    adapter = session_provider(provider)
    if adapter is None:
        return None
    from app.services.coding_sessions.claude_overview import (
        _capture_facts,
        _session_envelopes,
        _verdict,
    )
    from app.services.coding_sessions.service import get_coding_session_bridge_outbox

    listing = await _listing_or_failure(adapter)
    row = next((item for item in listing.rows if item.session_id == session_id), None)
    cloud, cloud_meta = await cloud_inventory(provider)
    binding = cloud.get(session_id)
    if row is None and binding is None and listing.lists_locally:
        return None
    queue, queue_meta = await _queue_by_session(provider)
    envelopes, envelope_meta = await _session_envelopes(session_id)
    capture, capture_meta = await _capture_facts(session_id)
    session_queue = (
        queue.get(session_id, {"pending": 0, "quarantined": 0})
        if queue is not None and queue_meta["checked"]
        else None
    )
    activity_ns = int((row.last_activity_at if row else 0) or 0) * 1_000_000
    state = _session_state(
        cloud_checked=bool(cloud_meta["checked"]),
        binding=binding,
        activity_ns=activity_ns,
        queue=session_queue,
    )
    publisher_blocker = await get_coding_session_bridge_outbox().blocker()
    return {
        "schema_version": SCHEMA_VERSION,
        "provider": provider,
        "session_id": session_id,
        "state": state,
        "verdict": _verdict(
            state=state,
            cloud_meta=cloud_meta,
            binding=binding,
            envelopes=envelopes,
            delivery_checked=bool(queue_meta["checked"]),
            capture=capture,
            publisher_blocker=publisher_blocker,
            on_disk=bool(row.on_disk) if row is not None else False,
        ),
        "continuation": continuation_hint(session_id, provider),
        "local": (
            {
                "title": row.title,
                "title_source": row.title_source,
                "project": row.project,
                "last_activity_at": row.last_activity_at,
                "bytes": row.bytes,
                "on_disk": row.on_disk,
                "archived": row.archived,
                "facts": row.facts,
            }
            if row is not None
            else None
        ),
        "local_note": listing.note,
        "cloud": {"meta": cloud_meta, "binding": binding},
        "delivery": {
            "meta": envelope_meta,
            "envelopes": envelopes,
            "queue": session_queue,
            "publisher_blocker": publisher_blocker,
        },
        "capture": {"meta": capture_meta, "attempts": capture},
    }


__all__ = ["SCHEMA_VERSION", "overview", "session_diagnosis"]
