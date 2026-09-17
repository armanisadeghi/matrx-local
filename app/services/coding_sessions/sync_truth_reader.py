"""Gather the four layers of one conversation's truth, and repair the gap.

`sync_truth.py` holds the pure verdict. This module does the I/O: it reads the
transcript, this engine's delivery ledger, the server's typed per-session
diagnosis, and this Mac's local mirror -- then hands all four to `decide()`.

`reconcile()` is the action beside the diagnosis: deliver what never arrived,
ask the server to project what it received but never turned into messages, and
pull the conversation onto this Mac. It honours quarantine: an envelope the
server refused with `provider_account_conflict` is NEVER re-sent, and the
result says so in words instead of failing quietly.

Both are idempotent. Running reconcile on a conversation that is already in
sync changes nothing and returns the same verdict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.coding_sessions.sync_truth import (
    CloudFacts,
    DeliveryFacts,
    MirrorFacts,
    SyncFacts,
    TranscriptFacts,
    as_payload,
    decide,
)

_ACCOUNT_CONFLICT = "provider_account_conflict"

# The server actions this engine consumes. They live behind the ONE bridge door
# (`POST /api/coding-sessions/bridge`) -- this engine never queries the cloud
# tables itself, per "clients consume, never reimplement".
_BRIDGE_PATH = "/coding-sessions/bridge"
_DIAGNOSE = "diagnose"
_REPROJECT = "reproject"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- transcript


def transcript_paths(session_id: str) -> list[Path]:
    """The main transcript plus any sidechain streams, for this session only.

    Targeted on purpose: a full walk of ~76,000 transcript files is a refresh
    job, not something a person's click should wait for.
    """
    from app.services.coding_sessions.claude_index_store import (
        default_transcripts_root,
    )

    root = default_transcripts_root()
    if not root.is_dir():
        return []
    found: list[Path] = []
    for project in root.iterdir():
        if not project.is_dir():
            continue
        main = project / f"{session_id}.jsonl"
        if main.is_file():
            found.append(main)
        sidechains = project / session_id
        if sidechains.is_dir():
            found.extend(sorted(p for p in sidechains.glob("*.jsonl") if p.is_file()))
    return found


def read_transcript(session_id: str) -> TranscriptFacts:
    from app.services.coding_sessions.claude_history import transcript_census

    paths = transcript_paths(session_id)
    if not paths:
        return TranscriptFacts(on_disk=False, entries=0, bytes=0, checked=True)
    entries = 0
    unreadable = 0
    total_bytes = 0
    last_entry_id: str | None = None
    last_entry_at: str | None = None
    newest_mtime = 0.0
    try:
        for path in paths:
            census = transcript_census(path)
            entries += census.entries
            unreadable += census.unreadable_lines
            total_bytes += census.bytes
            if census.last_entry_id:
                last_entry_id = census.last_entry_id
            if census.last_entry_at and (
                last_entry_at is None or census.last_entry_at > last_entry_at
            ):
                last_entry_at = census.last_entry_at
            newest_mtime = max(newest_mtime, path.stat().st_mtime)
    except OSError as exc:
        # An unreadable file is NOT an empty conversation. Say so.
        return TranscriptFacts(
            on_disk=True,
            entries=None,
            checked=False,
            reason=f"{type(exc).__name__} while reading the transcript file",
        )
    return TranscriptFacts(
        on_disk=True,
        entries=entries,
        last_entry_at=last_entry_at,
        last_entry_id=last_entry_id,
        unreadable_lines=unreadable,
        bytes=total_bytes,
        modified_at=_iso(datetime.fromtimestamp(newest_mtime, timezone.utc))
        if newest_mtime
        else None,
    )


# ------------------------------------------------------------------ delivery


async def read_delivery(session_id: str, *, cloud: CloudFacts) -> DeliveryFacts:
    """This engine's own view: what is queued, what is stuck, and why.

    "Delivered" is deliberately the server's own entry count, not a local
    tally: the local ledger records deliveries per lane, and the only honest
    answer to "did it arrive?" is the receiver's.
    """
    from app.services.coding_sessions.claude_overview import (
        _delivered_by_this_mac,
        _session_envelopes,
    )
    from app.services.coding_sessions.service import get_coding_session_bridge_outbox

    envelopes, meta = await _session_envelopes(session_id)
    if envelopes is None:
        return DeliveryFacts(
            checked=False,
            reason=str(meta.get("detail") or "the delivery ledger could not be read"),
        )
    pending = sum(
        int(item.get("item_count") or 0)
        for item in envelopes
        if item.get("state") == "pending"
    )
    quarantined = sum(
        int(item.get("item_count") or 0)
        for item in envelopes
        if item.get("state") == "quarantine"
    )
    reasons: dict[tuple[str, str], int] = {}
    for item in envelopes:
        if item.get("state") != "quarantine":
            continue
        error = item.get("error") or {}
        key = (
            str(error.get("code") or "preserved_delivery_failure"),
            str(
                error.get("message")
                or "The event was preserved after delivery could not complete."
            ),
        )
        reasons[key] = reasons.get(key, 0) + int(item.get("item_count") or 0)
    delivered, delivered_meta = await _delivered_by_this_mac()
    last_receipt_at: str | None = None
    if delivered is not None and bool(delivered_meta.get("checked")):
        stamp_ns = delivered.get(session_id)
        if stamp_ns:
            last_receipt_at = _iso(
                datetime.fromtimestamp(stamp_ns / 1_000_000_000, timezone.utc)
            )
    return DeliveryFacts(
        checked=True,
        accepted_entries=cloud.entries if cloud.checked else None,
        last_receipt_at=last_receipt_at,
        pending_entries=pending,
        quarantined_entries=quarantined,
        quarantine_reasons=tuple(
            {"code": code, "message": message, "count": count}
            for (code, message), count in sorted(reasons.items())
        ),
        publisher_blocker=get_coding_session_bridge_outbox().publisher_blocker,
    )


# --------------------------------------------------------------------- cloud


async def _bridge(action: str, session_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """One typed call to the server's bridge. Returns (payload, why_not)."""
    from app.services.aidream.client import (
        AIDreamError,
        AIDreamOfflineError,
        get_aidream_client,
    )

    client = get_aidream_client()
    if client is None:
        return None, "this Mac is not signed in to AI Matrx"
    try:
        response = await client.post(
            _BRIDGE_PATH,
            {
                "schema_version": 1,
                "action": action,
                "provider": "claude_code",
                "provider_session_id": session_id,
            },
            timeout=45.0,
        )
    except AIDreamOfflineError:
        return None, "AI Matrx could not be reached from this Mac"
    except AIDreamError as exc:
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if status in (400, 404, 422):
            # A server that does not yet know this action must announce itself,
            # not be mistaken for a conversation that is in sync.
            return None, (
                f"this AI Matrx server does not answer the '{action}' request yet "
                f"(HTTP {status})"
            )
        return None, f"AI Matrx refused the request (HTTP {status})"
    if not isinstance(response, dict):
        return None, "AI Matrx returned an answer this app could not read"
    return response, None


async def read_cloud(session_id: str) -> CloudFacts:
    response, why_not = await _bridge(_DIAGNOSE, session_id)
    if response is None:
        return CloudFacts(checked=False, reason=why_not)
    diagnosis = response.get("diagnosis")
    if not isinstance(diagnosis, dict):
        return CloudFacts(
            checked=False,
            reason="AI Matrx answered without a diagnosis for this conversation",
        )
    errors = diagnosis.get("projection_errors")
    return CloudFacts(
        checked=True,
        session_present=bool(diagnosis.get("session_present")),
        conversation_id=diagnosis.get("conversation_id"),
        fidelity=diagnosis.get("fidelity"),
        entries=diagnosis.get("entries"),
        projected_entries=diagnosis.get("projected_entries"),
        skipped_entries=diagnosis.get("skipped_entries"),
        pending_entries=diagnosis.get("pending_entries"),
        error_entries=diagnosis.get("error_entries"),
        projection_errors=tuple(errors) if isinstance(errors, list) else (),
        last_entry_at=diagnosis.get("last_entry_at"),
        last_entry_id=diagnosis.get("last_entry_id"),
        messages=diagnosis.get("messages"),
        last_position=diagnosis.get("last_position"),
        last_message_at=diagnosis.get("last_message_at"),
    )


# -------------------------------------------------------------------- mirror


async def read_mirror(conversation_id: str | None) -> MirrorFacts:
    if not conversation_id:
        return MirrorFacts(
            checked=False,
            reason="this conversation has no AI Matrx id yet, so there is nothing to copy",
        )
    from app.services.local_db.database import get_db

    try:
        db = get_db()
        row = await db.fetchone(
            'SELECT id FROM "chat".conversation WHERE id = ?', (conversation_id,)
        )
        counted = await db.fetchone(
            'SELECT COUNT(*) AS n FROM "chat".message WHERE conversation_id = ?',
            (conversation_id,),
        )
        pulled = await db.fetchone(
            'SELECT MAX(updated_at) AS at FROM "chat".message WHERE conversation_id = ?',
            (conversation_id,),
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable mirror is not an empty one
        return MirrorFacts(
            checked=False,
            reason=f"{type(exc).__name__} while reading this Mac's local copy",
        )
    return MirrorFacts(
        checked=True,
        conversation_row=row is not None,
        messages=int(counted["n"] or 0) if counted is not None else 0,
        last_pulled_at=str(pulled["at"]) if pulled is not None and pulled["at"] else None,
    )


# ---------------------------------------------------------------------- read


async def sync_truth(session_id: str) -> dict[str, Any] | None:
    """The whole truth about one conversation, in four layers and one sentence."""
    transcript = read_transcript(session_id)
    cloud = await read_cloud(session_id)
    if not transcript.on_disk and not cloud.checked:
        # Nothing on disk and nothing we can confirm in the cloud: the caller
        # gets a 404 rather than a confident-looking empty answer.
        return None
    delivery = await read_delivery(session_id, cloud=cloud)
    mirror = await read_mirror(cloud.conversation_id)
    facts = SyncFacts(
        session_id=session_id,
        transcript=transcript,
        delivery=delivery,
        cloud=cloud,
        mirror=mirror,
    )
    now = _now()
    return as_payload(facts, decide(facts, now=now), now=now)


# ----------------------------------------------------------------- reconcile


def _blocking_conflict(truth: dict[str, Any]) -> dict[str, Any] | None:
    for reason in truth.get("delivered", {}).get("quarantine_reasons", []) or []:
        if str(reason.get("code")) == _ACCOUNT_CONFLICT:
            return reason
    return None


async def reconcile(session_id: str) -> dict[str, Any] | None:
    """Close the gap this diagnosis just named, then report the new truth.

    Idempotent by construction: every step is a no-op when its own precondition
    is already satisfied, and the answer is always the freshly re-read truth --
    never an optimistic claim about what the steps should have achieved.
    """
    before = await sync_truth(session_id)
    if before is None:
        return None
    actions: list[dict[str, Any]] = []

    conflict = _blocking_conflict(before)
    if conflict is not None:
        actions.append(
            {
                "step": "deliver",
                "outcome": "refused",
                "detail": (
                    "Not re-sent on purpose: AI Matrx already holds this session "
                    "under a different Claude account, so a delivery from this "
                    "account cannot replace it. Re-sending would fail every time."
                ),
            }
        )
    else:
        actions.append(await _redeliver(session_id, before))

    actions.append(await _reproject(session_id, before))
    actions.append(await _pull(before))

    after = await sync_truth(session_id)
    return {
        "schema_version": 1,
        "session_id": session_id,
        "actions": actions,
        "truth": after if after is not None else before,
    }


async def _redeliver(session_id: str, truth: dict[str, Any]) -> dict[str, Any]:
    transcript_entries = int(truth.get("counts", {}).get("transcript") or 0)
    cloud_entries = truth.get("cloud", {}).get("entries")
    if not truth.get("transcript", {}).get("on_disk"):
        return {
            "step": "deliver",
            "outcome": "skipped",
            "detail": "There is no transcript file on this Mac to deliver.",
        }
    if cloud_entries is not None and int(cloud_entries) >= transcript_entries:
        return {
            "step": "deliver",
            "outcome": "nothing_to_do",
            "detail": "AI Matrx already has every entry in the transcript.",
        }
    from app.services.coding_sessions.capture_reconciler import (
        get_claude_capture_reconciler,
    )

    try:
        result = await get_claude_capture_reconciler().reconcile_session(session_id)
    except Exception as exc:  # noqa: BLE001 - report the failure, never swallow it
        return {
            "step": "deliver",
            "outcome": "failed",
            "detail": f"The transcript import could not start: {type(exc).__name__}.",
        }
    return {
        "step": "deliver",
        "outcome": "queued",
        "detail": (
            "The transcript was queued for delivery to AI Matrx. Large "
            "conversations finish in the background."
        ),
        "result": result,
    }


async def _reproject(session_id: str, truth: dict[str, Any]) -> dict[str, Any]:
    cloud = truth.get("cloud", {})
    stuck = int(cloud.get("error_entries") or 0) + int(cloud.get("pending_entries") or 0)
    if not cloud.get("checked"):
        return {
            "step": "reproject",
            "outcome": "skipped",
            "detail": "AI Matrx could not be asked; nothing was changed there.",
        }
    if stuck == 0:
        return {
            "step": "reproject",
            "outcome": "nothing_to_do",
            "detail": "Every entry AI Matrx holds has already become a message.",
        }
    response, why_not = await _bridge(_REPROJECT, session_id)
    if response is None:
        return {"step": "reproject", "outcome": "failed", "detail": f"{why_not}."}
    report = response.get("reprojection") or {}
    return {
        "step": "reproject",
        "outcome": "done",
        "detail": (
            f"AI Matrx re-examined {report.get('examined', stuck)} entries and "
            f"turned {report.get('projected', 0)} of them into messages."
        ),
        "result": report,
    }


async def _pull(truth: dict[str, Any]) -> dict[str, Any]:
    conversation_id = truth.get("cloud", {}).get("conversation_id")
    if not conversation_id:
        return {
            "step": "pull",
            "outcome": "skipped",
            "detail": "There is no AI Matrx conversation to copy onto this Mac yet.",
        }
    from app.services.chat_sync.engine import get_chat_sync_engine

    try:
        pulled = await get_chat_sync_engine().hydrate_conversation(str(conversation_id))
    except Exception as exc:  # noqa: BLE001 - a failed pull must be visible
        return {
            "step": "pull",
            "outcome": "failed",
            "detail": f"The local copy could not be refreshed: {type(exc).__name__}.",
        }
    if not pulled:
        return {
            "step": "pull",
            "outcome": "nothing_to_do",
            "detail": "AI Matrx had nothing newer for this conversation.",
        }
    return {
        "step": "pull",
        "outcome": "done",
        "detail": "This Mac's copy of the conversation was refreshed from AI Matrx.",
    }
