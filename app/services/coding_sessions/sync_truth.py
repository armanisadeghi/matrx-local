"""Per-conversation sync truth: the four layers, one verdict, one remedy.

Arman, 2026-09-17: "I have a chat in Claude Code that simply doesn't match what
I see in AI Matrx. I cannot figure out what is wrong. A normal DB system would
show me that it can't sync, or when it was synced -- this thing is a dead fish."

The old per-session diagnosis answered "is there a row in AI Matrx for this
session?" and said "AI Matrx holds this conversation (fidelity: event_mirror)".
That sentence is true and useless: measured on Arman's Mac the same day, 860 of
2033 indexed conversations had FEWER entries in the cloud than in the local
transcript, and one of them had 3526 transcript entries against 55 cloud
entries and 2 messages -- while the screen said it was held.

So this module compares content, not presence, across all four layers a Claude
Code conversation lives in:

    Transcript   ~/.claude/projects/<project>/<session>.jsonl  (the truth on disk)
    Delivered    this engine's bridge outbox / quarantine / receipts
    In AI Matrx  chat.coding_session_entry -> chat.message (read through the
                 server's own typed `diagnose` bridge action, never guessed)
    On this Mac  the local mirror at <data-dir>/mirror/chat.db

THE LAW OF THIS FILE: a layer that could not be read is `unknown`. It is never
rendered as agreement. Every state carries one plain-English sentence with the
real numbers in it and, when there is something to do, one remedy.

`decide()` is pure: primitives in, verdict out, no I/O, no clock. That is what
makes the guards real -- they feed it facts recorded from Arman's own Mac and
assert sentences typed by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

SCHEMA_VERSION = 1

VerdictCode = Literal[
    "in_sync",
    "partial_by_design",
    "behind_local",
    "behind_cloud",
    "mirror_stale",
    "diverged",
    "quarantined",
    "not_in_cloud",
    "unknown",
]

# The closed set. A code that is not in here cannot be rendered, and the guard
# in tests/unit/test_coding_session_sync_truth.py fails if this list and the
# sentence table below ever disagree.
VERDICT_CODES: tuple[str, ...] = (
    "in_sync",
    "partial_by_design",
    "behind_local",
    "behind_cloud",
    "mirror_stale",
    "diverged",
    "quarantined",
    "not_in_cloud",
    "unknown",
)

# Precedence: first match wins. A real fault always outranks "partial by
# design" -- a hook-lane session with quarantined entries is quarantined.
PRECEDENCE: tuple[str, ...] = (
    "unknown",
    "quarantined",
    "not_in_cloud",
    "diverged",
    "behind_local",
    "partial_by_design",
    "behind_cloud",
    "mirror_stale",
    "in_sync",
)

# The four count labels. The same four words appear in Matrx Local and on the
# web; they are exported so neither surface can drift into its own vocabulary.
COUNT_LABELS: tuple[str, str, str, str] = (
    "Transcript",
    "Delivered",
    "In AI Matrx",
    "On this Mac",
)

_ACCOUNT_CONFLICT = "provider_account_conflict"


@dataclass(frozen=True)
class TranscriptFacts:
    """What the .jsonl file on this Mac actually contains."""

    on_disk: bool
    entries: int | None = None
    last_entry_at: str | None = None
    last_entry_id: str | None = None
    unreadable_lines: int = 0
    bytes: int = 0
    modified_at: str | None = None
    checked: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class DeliveryFacts:
    """What this engine has managed to hand to the server."""

    checked: bool
    reason: str | None = None
    accepted_entries: int | None = None
    last_receipt_at: str | None = None
    pending_entries: int = 0
    quarantined_entries: int = 0
    quarantine_reasons: tuple[dict[str, Any], ...] = ()
    publisher_blocker: str | None = None


@dataclass(frozen=True)
class CloudFacts:
    """What the server says it holds -- from its own typed diagnose action."""

    checked: bool
    reason: str | None = None
    session_present: bool = False
    conversation_id: str | None = None
    fidelity: str | None = None
    entries: int | None = None
    projected_entries: int | None = None
    skipped_entries: int | None = None
    pending_entries: int | None = None
    error_entries: int | None = None
    projection_errors: tuple[dict[str, Any], ...] = ()
    last_entry_at: str | None = None
    last_entry_id: str | None = None
    messages: int | None = None
    last_position: int | None = None
    last_message_at: str | None = None


@dataclass(frozen=True)
class MirrorFacts:
    """What this Mac's own local copy of the conversation contains."""

    checked: bool
    reason: str | None = None
    conversation_row: bool = False
    messages: int | None = None
    last_pulled_at: str | None = None


@dataclass(frozen=True)
class SyncFacts:
    session_id: str
    transcript: TranscriptFacts
    delivery: DeliveryFacts
    cloud: CloudFacts
    mirror: MirrorFacts
    provider: str = "claude_code"


@dataclass(frozen=True)
class Verdict:
    code: str
    reason: str | None
    sentence: str
    remedy: str | None
    reconcilable: bool
    counts: dict[str, int | None] = field(default_factory=dict)


def _n(value: int | None) -> int:
    return int(value) if value is not None else 0


def ago(then: str | None, now: datetime) -> str:
    """Plain English for when something happened. Pure: no hidden clock.

    Arman reads these sentences; "2026-09-17T20:57:30+00:00" is not English.
    """
    if not then:
        return "never"
    moment = _parse(then)
    if moment is None:
        return "at an unknown time"
    seconds = (now - moment).total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 90:
        return "seconds ago"
    minutes = seconds / 60
    if minutes < 60:
        count = int(round(minutes))
        return f"{count} minute{'s' if count != 1 else ''} ago"
    hours = minutes / 60
    if hours < 24:
        count = int(round(hours))
        return f"{count} hour{'s' if count != 1 else ''} ago"
    days = hours / 24
    if days < 2:
        return "yesterday"
    if days < 30:
        return f"{int(days)} days ago"
    return f"on {moment.date().isoformat()}"


def _parse(raw: str) -> datetime | None:
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _unreadable_layer(facts: SyncFacts) -> tuple[str, str] | None:
    """The first layer we could not read, in the order a person would ask."""
    if not facts.transcript.checked:
        return (
            "this Mac's transcript",
            facts.transcript.reason or "the file could not be read",
        )
    if not facts.cloud.checked:
        return "AI Matrx", facts.cloud.reason or "the server could not be reached"
    if not facts.delivery.checked:
        return (
            "this Mac's delivery ledger",
            facts.delivery.reason or "the ledger could not be read",
        )
    if not facts.mirror.checked:
        return (
            "this Mac's local copy",
            facts.mirror.reason or "the local copy could not be read",
        )
    return None


def _dominant_quarantine(facts: DeliveryFacts) -> dict[str, Any] | None:
    if not facts.quarantine_reasons:
        return None
    for reason in facts.quarantine_reasons:
        if str(reason.get("code")) == _ACCOUNT_CONFLICT:
            return dict(reason)
    return max(
        (dict(r) for r in facts.quarantine_reasons),
        key=lambda r: int(r.get("count") or 0),
    )


def _dominant_projection_error(facts: CloudFacts) -> dict[str, Any] | None:
    if not facts.projection_errors:
        return None
    return max(
        (dict(e) for e in facts.projection_errors),
        key=lambda e: int(e.get("count") or 0),
    )


def counts_of(facts: SyncFacts) -> dict[str, int | None]:
    """The four numbers, side by side. `None` means "this layer is unreadable"
    and MUST render as such -- never as zero."""
    return {
        "transcript": facts.transcript.entries if facts.transcript.checked else None,
        "delivered": facts.delivery.accepted_entries if facts.delivery.checked else None,
        "cloud_messages": facts.cloud.messages if facts.cloud.checked else None,
        "mirror_messages": facts.mirror.messages if facts.mirror.checked else None,
    }


def decide(facts: SyncFacts, *, now: datetime) -> Verdict:
    """The one verdict. Pure. Precedence is PRECEDENCE, first match wins."""
    counts = counts_of(facts)

    unreadable = _unreadable_layer(facts)
    if unreadable is not None:
        layer, reason = unreadable
        return Verdict(
            code="unknown",
            reason=None,
            sentence=(
                f"Cannot tell whether this conversation is in sync: {layer} "
                f"could not be read ({reason})."
            ),
            remedy=(
                "Try again in a moment; if it keeps failing, check that AI Matrx "
                "is reachable and you are signed in."
            ),
            reconcilable=False,
            counts=counts,
        )

    transcript_entries = _n(facts.transcript.entries)
    cloud_entries = _n(facts.cloud.entries)
    messages = _n(facts.cloud.messages)

    quarantine = _dominant_quarantine(facts.delivery)
    if quarantine is not None and facts.delivery.quarantined_entries > 0:
        held = facts.delivery.quarantined_entries
        code = str(quarantine.get("code"))
        if code == _ACCOUNT_CONFLICT:
            return Verdict(
                code="quarantined",
                reason=_ACCOUNT_CONFLICT,
                sentence=(
                    f"{held} entries are held back permanently: AI Matrx already "
                    "has this session under a different Claude account, and a "
                    "delivery from this account cannot replace it."
                ),
                remedy=(
                    "Nothing to do — the conversation is in AI Matrx under that "
                    "other account. Reconcile will not re-send these."
                ),
                reconcilable=False,
                counts=counts,
            )
        return Verdict(
            code="quarantined",
            reason=code,
            sentence=(
                f"{held} entries are stuck and not being retried: "
                f"{quarantine.get('message')}"
            ),
            remedy="Reconcile to retry them.",
            reconcilable=True,
            counts=counts,
        )

    if not facts.cloud.session_present and not facts.cloud.conversation_id:
        return Verdict(
            code="not_in_cloud",
            reason=None,
            sentence=(
                "This conversation is not in AI Matrx at all — no session, no "
                f"messages. Its {transcript_entries} local entries have never "
                "been delivered."
            ),
            remedy="Reconcile to send it.",
            reconcilable=True,
            counts=counts,
        )

    # The cloud holds entries the local file no longer has. Claude Code rewrites
    # the transcript on compaction, so the cloud legitimately becomes the longer
    # record. Measured on Arman's Mac: 26 conversations, including the one he
    # was sitting in (4363 local lines against 4938 cloud entries).
    if facts.transcript.on_disk and cloud_entries > transcript_entries:
        surplus = cloud_entries - transcript_entries
        return Verdict(
            code="diverged",
            reason="transcript_rewritten",
            sentence=(
                f"AI Matrx holds {surplus} entries that are no longer in your "
                "local transcript — Claude Code rewrote the file, which "
                "compaction does. AI Matrx has the longer, older record; the "
                "local file is now the short one."
            ),
            remedy=None,
            reconcilable=False,
            counts=counts,
        )

    if cloud_entries == 0 and facts.cloud.conversation_id and transcript_entries > 0:
        return Verdict(
            code="behind_local",
            reason="no_delivery_ledger_in_cloud",
            sentence=(
                "AI Matrx has a conversation for this session but no record of a "
                "single delivered entry, so none of the "
                f"{transcript_entries} entries in your local transcript are in "
                f"it. The {messages} messages you see there came from the run "
                "itself, not from this transcript."
            ),
            remedy="Reconcile to deliver the transcript.",
            reconcilable=True,
            counts=counts,
        )

    # THE HOOK-LANE RULE, and it must be judged BEFORE any shortfall is called
    # a fault. Hook capture sees prompts and tool calls, never transcript
    # lines, so the cloud legitimately holds fewer entries than the file --
    # 655 conversations on Arman's Mac sit here. Calling every one of them
    # "behind" would be a false alarm 655 times over; calling them "held", as
    # the old screen did, hid the 205 native-lane ones that really were behind.
    # A hook-lane session that is genuinely broken still surfaces: its stuck
    # envelopes are `quarantined` above, and hooks that stopped firing at all
    # leave zero entries, which is `no_delivery_ledger_in_cloud`.
    if (
        facts.cloud.fidelity == "event_mirror"
        and transcript_entries > cloud_entries
        and cloud_entries > 0
    ):
        return Verdict(
            code="partial_by_design",
            reason="hook_lane",
            sentence=(
                "AI Matrx has the shape of this conversation — the "
                f"{cloud_entries} prompts and tool calls its hooks recorded, "
                f"projected into {messages} messages — not the "
                f"{transcript_entries}-entry transcript. Hook capture is a "
                "summary by design."
            ),
            remedy="Reconcile to import the full transcript file into AI Matrx.",
            reconcilable=True,
            counts=counts,
        )

    undelivered = max(transcript_entries - cloud_entries, 0)
    if undelivered > 0:
        if facts.delivery.publisher_blocker:
            return Verdict(
                code="behind_local",
                reason="publisher_blocked",
                sentence=(
                    f"{undelivered} entries are waiting to be sent and delivery "
                    f"is paused: {facts.delivery.publisher_blocker}"
                ),
                remedy="Clear the pause, then Reconcile.",
                reconcilable=False,
                counts=counts,
            )
        return Verdict(
            code="behind_local",
            reason="undelivered",
            sentence=(
                f"{undelivered} of this conversation's {transcript_entries} "
                "entries have never reached AI Matrx. The last one that did "
                f"arrived {ago(facts.delivery.last_receipt_at, now)}."
            ),
            remedy="Reconcile to deliver them now.",
            reconcilable=True,
            counts=counts,
        )

    error_entries = _n(facts.cloud.error_entries)
    if error_entries > 0:
        error = _dominant_projection_error(facts.cloud) or {}
        detail = str(error.get("detail") or error.get("code") or "reason not recorded")
        return Verdict(
            code="behind_cloud",
            reason="projection_error",
            sentence=(
                f"AI Matrx received all {cloud_entries} entries but "
                f"{error_entries} never became messages: {detail}"
            ),
            remedy="Reconcile to ask the server to project them again.",
            reconcilable=True,
            counts=counts,
        )

    cloud_pending = _n(facts.cloud.pending_entries)
    if cloud_pending > 0:
        return Verdict(
            code="behind_cloud",
            reason="awaiting_projection",
            sentence=(
                f"AI Matrx received all {cloud_entries} entries but "
                f"{cloud_pending} are still waiting to become messages."
            ),
            remedy="Reconcile to ask the server to project them now.",
            reconcilable=True,
            counts=counts,
        )

    mirror_messages = _n(facts.mirror.messages)
    if messages > mirror_messages:
        return Verdict(
            code="mirror_stale",
            reason=None,
            sentence=(
                f"AI Matrx has {messages} messages for this conversation; this "
                f"Mac's local copy has {mirror_messages} and was last pulled "
                f"{ago(facts.mirror.last_pulled_at, now)}."
            ),
            remedy="Reconcile to pull the rest onto this Mac.",
            reconcilable=True,
            counts=counts,
        )

    return Verdict(
        code="in_sync",
        reason=None,
        sentence=(
            f"In sync. All {transcript_entries} entries are in AI Matrx as "
            f"{messages} messages; this Mac's copy was last pulled "
            f"{ago(facts.mirror.last_pulled_at, now)}."
        ),
        remedy=None,
        reconcilable=False,
        counts=counts,
    )


def as_payload(facts: SyncFacts, verdict: Verdict, *, now: datetime) -> dict[str, Any]:
    """The wire shape both surfaces render. Field names are the CS-25 contract."""
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": facts.session_id,
        "provider": facts.provider,
        "verdict": {
            "code": verdict.code,
            "reason": verdict.reason,
            "sentence": verdict.sentence,
            "remedy": verdict.remedy,
            "reconcilable": verdict.reconcilable,
        },
        "count_labels": list(COUNT_LABELS),
        "counts": verdict.counts,
        "transcript": {
            "checked": facts.transcript.checked,
            "reason": facts.transcript.reason,
            "on_disk": facts.transcript.on_disk,
            "entries": facts.transcript.entries,
            "last_entry_at": facts.transcript.last_entry_at,
            "last_entry_id": facts.transcript.last_entry_id,
            "unreadable_lines": facts.transcript.unreadable_lines,
            "bytes": facts.transcript.bytes,
            "modified_at": facts.transcript.modified_at,
        },
        "delivered": {
            "checked": facts.delivery.checked,
            "reason": facts.delivery.reason,
            "accepted_entries": facts.delivery.accepted_entries,
            "last_receipt_at": facts.delivery.last_receipt_at,
            "pending_entries": facts.delivery.pending_entries,
            "quarantined_entries": facts.delivery.quarantined_entries,
            "quarantine_reasons": [dict(r) for r in facts.delivery.quarantine_reasons],
            "publisher_blocker": facts.delivery.publisher_blocker,
        },
        "cloud": {
            "checked": facts.cloud.checked,
            "reason": facts.cloud.reason,
            "session_present": facts.cloud.session_present,
            "conversation_id": facts.cloud.conversation_id,
            "fidelity": facts.cloud.fidelity,
            "entries": facts.cloud.entries,
            "projected_entries": facts.cloud.projected_entries,
            "skipped_entries": facts.cloud.skipped_entries,
            "pending_entries": facts.cloud.pending_entries,
            "error_entries": facts.cloud.error_entries,
            "projection_errors": [dict(e) for e in facts.cloud.projection_errors],
            "last_entry_at": facts.cloud.last_entry_at,
            "last_entry_id": facts.cloud.last_entry_id,
            "messages": facts.cloud.messages,
            "last_position": facts.cloud.last_position,
            "last_message_at": facts.cloud.last_message_at,
        },
        "mirror": {
            "checked": facts.mirror.checked,
            "reason": facts.mirror.reason,
            "conversation_row": facts.mirror.conversation_row,
            "messages": facts.mirror.messages,
            "last_pulled_at": facts.mirror.last_pulled_at,
        },
        "computed_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
    }
