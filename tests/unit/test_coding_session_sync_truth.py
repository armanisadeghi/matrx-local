"""Guards for per-conversation sync truth (CS-25).

Every test below names the production change that turns it red. The expected
numbers come from OUTSIDE the code under test: the transcript fixtures are two
real Claude Code sessions copied off Arman's Mac (message text replaced, every
structural field and every line kept), and their entry counts were established
independently with `jq -s 'length'` -- 384 and 9 -- not by running the counter.

The sentences are typed by hand here. Nothing in this file imports a formatter
and compares it to itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.coding_sessions.claude_history import transcript_census
from app.services.coding_sessions.sync_truth import (
    COUNT_LABELS,
    PRECEDENCE,
    VERDICT_CODES,
    CloudFacts,
    DeliveryFacts,
    MirrorFacts,
    SyncFacts,
    TranscriptFacts,
    ago,
    as_payload,
    decide,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude_transcripts"

NOW = datetime(2026, 9, 17, 22, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# The transcript census. Red if anyone changes what counts as an entry --
# which is the number a person is shown next to "Delivered".
# --------------------------------------------------------------------------


def test_census_counts_every_entry_of_a_real_transcript() -> None:
    """384 is `jq -s 'length'` on this file, not this code's own opinion."""
    census = transcript_census(FIXTURES / "behind_local.jsonl")
    assert census.entries == 384
    assert census.unreadable_lines == 0


def test_census_counts_a_short_real_transcript() -> None:
    census = transcript_census(FIXTURES / "tiny.jsonl")
    assert census.entries == 9
    assert census.unreadable_lines == 0


def test_census_separates_unreadable_lines_from_entries() -> None:
    """9 good lines and 3 that are not JSON objects, appended by hand.

    Red if a malformed line is ever counted as a delivered entry (which would
    make a transcript look complete when part of it cannot be sent) or if it
    silently vanishes from `unreadable_lines`.
    """
    census = transcript_census(FIXTURES / "with_corrupt_lines.jsonl")
    assert census.entries == 9
    assert census.unreadable_lines == 3


def test_census_reports_the_last_entry_identity() -> None:
    """Red if the census stops tracking which entry was last -- the only way
    to tell a stalled conversation from a finished one."""
    census = transcript_census(FIXTURES / "tiny.jsonl")
    assert census.last_entry_id == "914c711c-27ba-4b20-8bbb-c1ad4ac243f5"
    assert census.last_entry_at == "2026-09-15T17:08:55.293Z"


# --------------------------------------------------------------------------
# Fact builders. Deliberately explicit: a test that shares a builder with the
# code under test proves nothing.
# --------------------------------------------------------------------------


def _facts(
    *,
    transcript: TranscriptFacts | None = None,
    delivery: DeliveryFacts | None = None,
    cloud: CloudFacts | None = None,
    mirror: MirrorFacts | None = None,
) -> SyncFacts:
    return SyncFacts(
        session_id="460c5cf5-39a9-4f93-af06-9bb196509561",
        transcript=transcript
        or TranscriptFacts(on_disk=True, entries=384, checked=True),
        delivery=delivery or DeliveryFacts(checked=True, accepted_entries=384),
        cloud=cloud
        or CloudFacts(
            checked=True,
            session_present=True,
            conversation_id="cf1d62fc-0000-0000-0000-000000000000",
            fidelity="native",
            entries=384,
            projected_entries=384,
            skipped_entries=0,
            pending_entries=0,
            error_entries=0,
            messages=35,
        ),
        mirror=mirror or MirrorFacts(checked=True, conversation_row=True, messages=35),
    )


# --------------------------------------------------------------------------
# THE ANTI-DEAD-FISH GUARDS. These are the reason this feature exists.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "broken,layer",
    [
        ("transcript", "this Mac's transcript"),
        ("cloud", "AI Matrx"),
        ("delivery", "this Mac's delivery ledger"),
        ("mirror", "this Mac's local copy"),
    ],
)
def test_an_unreadable_layer_is_unknown_never_agreement(broken: str, layer: str) -> None:
    """Red the moment any layer's failure is allowed to read as "in sync".

    This is the exact defect Arman hit: the screen claimed AI Matrx held the
    conversation while it had no idea what was in it.
    """
    kwargs = {}
    if broken == "transcript":
        kwargs["transcript"] = TranscriptFacts(
            on_disk=True, entries=None, checked=False, reason="Permission denied"
        )
    elif broken == "cloud":
        kwargs["cloud"] = CloudFacts(checked=False, reason="server unreachable")
    elif broken == "delivery":
        kwargs["delivery"] = DeliveryFacts(checked=False, reason="ledger locked")
    else:
        kwargs["mirror"] = MirrorFacts(checked=False, reason="mirror not attached")
    verdict = decide(_facts(**kwargs), now=NOW)
    assert verdict.code == "unknown"
    assert layer in verdict.sentence
    assert verdict.remedy is not None


@pytest.mark.parametrize("broken", ["transcript", "cloud", "delivery", "mirror"])
def test_an_unreadable_layer_shows_no_number_at_all(broken: str) -> None:
    """A count of `None`, never 0. Red if an unreadable layer is ever reported
    as an empty one -- "0 entries" is a claim, and it would be a false one."""
    field = {
        "transcript": "transcript",
        "cloud": "cloud_messages",
        "delivery": "delivered",
        "mirror": "mirror_messages",
    }[broken]
    kwargs = {}
    if broken == "transcript":
        kwargs["transcript"] = TranscriptFacts(
            on_disk=True, entries=None, checked=False, reason="Permission denied"
        )
    elif broken == "cloud":
        kwargs["cloud"] = CloudFacts(checked=False, reason="server unreachable")
    elif broken == "delivery":
        kwargs["delivery"] = DeliveryFacts(checked=False, reason="ledger locked")
    else:
        kwargs["mirror"] = MirrorFacts(checked=False, reason="mirror not attached")
    verdict = decide(_facts(**kwargs), now=NOW)
    assert verdict.counts[field] is None


# --------------------------------------------------------------------------
# One case per verdict code, sentences typed by hand.
# --------------------------------------------------------------------------


def test_in_sync() -> None:
    verdict = decide(
        _facts(
            mirror=MirrorFacts(
                checked=True,
                conversation_row=True,
                messages=35,
                last_pulled_at="2026-09-17T21:00:00+00:00",
            )
        ),
        now=NOW,
    )
    assert verdict.code == "in_sync"
    assert verdict.sentence == (
        "In sync. All 384 entries are in AI Matrx as 35 messages; this Mac's "
        "copy was last pulled 1 hour ago."
    )
    assert verdict.remedy is None
    assert verdict.reconcilable is False


def test_behind_local_undelivered_names_the_missing_count() -> None:
    """The 860-conversation class measured on Arman's Mac. Red if the shortfall
    is ever rounded away or the verdict softened to `in_sync`."""
    verdict = decide(
        _facts(
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                fidelity="native",
                entries=73,
                projected_entries=73,
                error_entries=0,
                pending_entries=0,
                messages=34,
            ),
            delivery=DeliveryFacts(
                checked=True,
                accepted_entries=73,
                last_receipt_at="2026-09-17T18:00:00+00:00",
            ),
        ),
        now=NOW,
    )
    assert verdict.code == "behind_local"
    assert verdict.reason == "undelivered"
    assert verdict.sentence == (
        "311 of this conversation's 384 entries have never reached AI Matrx. "
        "The last one that did arrived 4 hours ago."
    )
    assert verdict.remedy == "Reconcile to deliver them now."
    assert verdict.reconcilable is True


def test_behind_local_no_delivery_ledger_in_cloud() -> None:
    """The 741-conversation class: a conversation row exists, nothing was ever
    delivered, and the old screen called that "held"."""
    verdict = decide(
        _facts(
            cloud=CloudFacts(
                checked=True,
                session_present=False,
                conversation_id="bec9eb0b-4804-56f4-8a85-14b18e0887b7",
                fidelity=None,
                entries=0,
                messages=100,
            )
        ),
        now=NOW,
    )
    assert verdict.code == "behind_local"
    assert verdict.reason == "no_delivery_ledger_in_cloud"
    assert verdict.sentence == (
        "AI Matrx has a conversation for this session but no record of a single "
        "delivered entry, so none of the 384 entries in your local transcript "
        "are in it. The 100 messages you see there came from the run itself, "
        "not from this transcript."
    )
    assert verdict.remedy == "Reconcile to deliver the transcript."


def test_behind_local_publisher_blocked_names_the_pause() -> None:
    verdict = decide(
        _facts(
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                entries=100,
                messages=10,
            ),
            delivery=DeliveryFacts(
                checked=True,
                accepted_entries=100,
                publisher_blocker="No organization is chosen yet.",
            ),
        ),
        now=NOW,
    )
    assert verdict.code == "behind_local"
    assert verdict.reason == "publisher_blocked"
    assert verdict.sentence == (
        "284 entries are waiting to be sent and delivery is paused: No "
        "organization is chosen yet."
    )
    assert verdict.reconcilable is False


def test_behind_cloud_projection_error_quotes_the_server_reason() -> None:
    """The real reason string recorded in chat.coding_session_entry on
    2026-09-17. Red if the server's own explanation is ever replaced by a
    generic "something went wrong"."""
    verdict = decide(
        _facts(
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                fidelity="native",
                entries=384,
                projected_entries=373,
                error_entries=11,
                projection_errors=(
                    {
                        "code": "unsupported_event",
                        "detail": (
                            "No safe canonical projection exists for "
                            "'file-history-delta'; raw entry retained."
                        ),
                        "count": 11,
                    },
                ),
                messages=35,
            )
        ),
        now=NOW,
    )
    assert verdict.code == "behind_cloud"
    assert verdict.reason == "projection_error"
    assert verdict.sentence == (
        "AI Matrx received all 384 entries but 11 never became messages: No "
        "safe canonical projection exists for 'file-history-delta'; raw entry "
        "retained."
    )
    assert verdict.remedy == "Reconcile to ask the server to project them again."


def test_behind_cloud_awaiting_projection() -> None:
    verdict = decide(
        _facts(
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                fidelity="native",
                entries=384,
                projected_entries=380,
                pending_entries=4,
                error_entries=0,
                messages=35,
            )
        ),
        now=NOW,
    )
    assert verdict.code == "behind_cloud"
    assert verdict.reason == "awaiting_projection"
    assert verdict.sentence == (
        "AI Matrx received all 384 entries but 4 are still waiting to become "
        "messages."
    )


def test_a_complete_hook_lane_session_is_in_sync_not_partial() -> None:
    """Red if `partial_by_design` starts firing on a hook-lane session that
    delivered everything it captures -- a false alarm on a working lane."""
    verdict = decide(
        _facts(
            transcript=TranscriptFacts(on_disk=True, entries=55, checked=True),
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                fidelity="event_mirror",
                entries=55,
                projected_entries=55,
                error_entries=0,
                pending_entries=0,
                messages=2,
            ),
            delivery=DeliveryFacts(checked=True, accepted_entries=55),
            mirror=MirrorFacts(checked=True, conversation_row=True, messages=2),
        ),
        now=NOW,
    )
    assert verdict.code == "in_sync"


def test_partial_by_design_tells_the_truth_about_hook_capture() -> None:
    """The 655-conversation class, measured with the real numbers of session
    8231a1ef on Arman's Mac: 3,526 transcript entries, 55 delivered, 2
    messages. The old screen said "AI Matrx holds this conversation (fidelity:
    event_mirror)" -- true, and the reason he could not tell a working hook
    lane from a broken one.

    Red two ways, both of which were live bugs caught by this guard:
    (1) if the hook lane's expected shortfall is reported as `behind_local`,
        655 healthy conversations start crying wolf;
    (2) if this verdict stops naming BOTH numbers, it goes back to being the
        reassuring sentence that hid the gap.
    """
    verdict = decide(
        _facts(
            transcript=TranscriptFacts(on_disk=True, entries=3526, checked=True),
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                fidelity="event_mirror",
                entries=55,
                projected_entries=55,
                error_entries=0,
                pending_entries=0,
                messages=2,
            ),
            delivery=DeliveryFacts(checked=True, accepted_entries=55),
            mirror=MirrorFacts(checked=True, conversation_row=True, messages=2),
        ),
        now=NOW,
    )
    assert verdict.code == "partial_by_design"
    assert verdict.reason == "hook_lane"
    assert verdict.sentence == (
        "AI Matrx has the shape of this conversation — the 55 prompts and tool "
        "calls its hooks recorded, projected into 2 messages — not the "
        "3526-entry transcript. Hook capture is a summary by design."
    )
    assert verdict.remedy == (
        "Reconcile to import the full transcript file into AI Matrx."
    )
    assert verdict.reconcilable is True


def test_a_native_lane_shortfall_is_a_fault_not_a_design() -> None:
    """The 205-conversation class. Same shortfall, different lane, different
    verdict. Red if the hook-lane excuse ever leaks onto the native lane and
    hides a real delivery failure."""
    verdict = decide(
        _facts(
            transcript=TranscriptFacts(on_disk=True, entries=3526, checked=True),
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                fidelity="native",
                entries=55,
                projected_entries=55,
                error_entries=0,
                pending_entries=0,
                messages=2,
            ),
            delivery=DeliveryFacts(
                checked=True,
                accepted_entries=55,
                last_receipt_at="2026-09-17T21:00:00+00:00",
            ),
        ),
        now=NOW,
    )
    assert verdict.code == "behind_local"
    assert verdict.reason == "undelivered"
    assert verdict.sentence == (
        "3471 of this conversation's 3526 entries have never reached AI Matrx. "
        "The last one that did arrived 1 hour ago."
    )


def test_a_hook_lane_session_with_nothing_delivered_is_not_excused() -> None:
    """Zero entries means the hooks are not firing at all. Red if that is ever
    softened into "partial by design"."""
    verdict = decide(
        _facts(
            transcript=TranscriptFacts(on_disk=True, entries=3526, checked=True),
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                fidelity="event_mirror",
                entries=0,
                messages=0,
            ),
        ),
        now=NOW,
    )
    assert verdict.code == "behind_local"
    assert verdict.reason == "no_delivery_ledger_in_cloud"


def test_mirror_stale_names_both_copies_and_when_it_last_pulled() -> None:
    """Red if the local copy's staleness is ever hidden -- this is the "when
    was it synced?" question Arman asked for by name."""
    verdict = decide(
        _facts(
            mirror=MirrorFacts(
                checked=True,
                conversation_row=True,
                messages=12,
                last_pulled_at="2026-09-15T22:00:00+00:00",
            )
        ),
        now=NOW,
    )
    assert verdict.code == "mirror_stale"
    assert verdict.sentence == (
        "AI Matrx has 35 messages for this conversation; this Mac's local copy "
        "has 12 and was last pulled 2 days ago."
    )
    assert verdict.remedy == "Reconcile to pull the rest onto this Mac."


def test_diverged_explains_compaction_instead_of_alarming() -> None:
    """The 26-conversation class, including the session Arman was sitting in
    (4,363 local lines against 4,938 cloud entries). Red if a rewritten
    transcript is ever reported as data loss."""
    verdict = decide(
        _facts(
            transcript=TranscriptFacts(on_disk=True, entries=4363, checked=True),
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="543c43e5-d93c-5932-a4ee-9fa4505b3595",
                fidelity="event_mirror",
                entries=4938,
                projected_entries=4934,
                error_entries=0,
                pending_entries=0,
                messages=108,
            ),
            delivery=DeliveryFacts(checked=True, accepted_entries=4938),
        ),
        now=NOW,
    )
    assert verdict.code == "diverged"
    assert verdict.reason == "transcript_rewritten"
    assert verdict.sentence == (
        "AI Matrx holds 575 entries that are no longer in your local transcript "
        "— Claude Code rewrote the file, which compaction does. AI Matrx has "
        "the longer, older record; the local file is now the short one."
    )
    assert verdict.remedy is None
    assert verdict.reconcilable is False


def test_quarantined_account_conflict_is_never_offered_a_retry() -> None:
    """148 of the 265 quarantined envelopes on Arman's Mac are this. Red if the
    UI is ever offered a Reconcile that would re-send them forever."""
    verdict = decide(
        _facts(
            delivery=DeliveryFacts(
                checked=True,
                accepted_entries=0,
                quarantined_entries=14820,
                quarantine_reasons=(
                    {
                        "code": "provider_account_conflict",
                        "message": (
                            "AI Matrx already holds this session bound to a "
                            "DIFFERENT Claude account, so a delivery from this "
                            "account cannot replace it. The conversation is in "
                            "AI Matrx; discard this delivery."
                        ),
                        "count": 14820,
                    },
                ),
            )
        ),
        now=NOW,
    )
    assert verdict.code == "quarantined"
    assert verdict.reason == "provider_account_conflict"
    assert verdict.sentence == (
        "14820 entries are held back permanently: AI Matrx already has this "
        "session under a different Claude account, and a delivery from this "
        "account cannot replace it."
    )
    assert verdict.reconcilable is False


def test_quarantined_other_reason_offers_a_retry() -> None:
    verdict = decide(
        _facts(
            delivery=DeliveryFacts(
                checked=True,
                accepted_entries=0,
                quarantined_entries=88,
                quarantine_reasons=(
                    {
                        "code": "entry_mutated",
                        "message": (
                            "The cloud already has this event identity with "
                            "different content."
                        ),
                        "count": 88,
                    },
                ),
            )
        ),
        now=NOW,
    )
    assert verdict.code == "quarantined"
    assert verdict.reason == "entry_mutated"
    assert verdict.reconcilable is True
    assert "entry_mutated" not in verdict.sentence  # the MESSAGE, not the code
    assert verdict.sentence == (
        "88 entries are stuck and not being retried: The cloud already has "
        "this event identity with different content."
    )


def test_not_in_cloud() -> None:
    """The 406-conversation class."""
    verdict = decide(
        _facts(
            cloud=CloudFacts(checked=True, session_present=False, conversation_id=None),
            mirror=MirrorFacts(checked=True, conversation_row=False, messages=0),
        ),
        now=NOW,
    )
    assert verdict.code == "not_in_cloud"
    assert verdict.sentence == (
        "This conversation is not in AI Matrx at all — no session, no messages. "
        "Its 384 local entries have never been delivered."
    )
    assert verdict.remedy == "Reconcile to send it."


# --------------------------------------------------------------------------
# Precedence and vocabulary.
# --------------------------------------------------------------------------


def test_a_real_fault_outranks_partial_by_design() -> None:
    """Red if a hook-lane session with stuck entries is ever excused as
    "partial by design" -- that would hide a genuine failure behind a
    reassuring sentence."""
    verdict = decide(
        _facts(
            transcript=TranscriptFacts(on_disk=True, entries=3526, checked=True),
            cloud=CloudFacts(
                checked=True,
                session_present=True,
                conversation_id="cf1d62fc-0000-0000-0000-000000000000",
                fidelity="event_mirror",
                entries=55,
                projected_entries=55,
                messages=2,
            ),
            delivery=DeliveryFacts(
                checked=True,
                accepted_entries=55,
                quarantined_entries=12,
                quarantine_reasons=(
                    {"code": "cloud_http_409", "message": "Conflict.", "count": 12},
                ),
            ),
        ),
        now=NOW,
    )
    assert verdict.code == "quarantined"


def test_unknown_outranks_everything() -> None:
    verdict = decide(
        _facts(
            cloud=CloudFacts(checked=False, reason="offline"),
            delivery=DeliveryFacts(
                checked=True,
                quarantined_entries=5,
                quarantine_reasons=(
                    {"code": "cloud_http_409", "message": "Conflict.", "count": 5},
                ),
            ),
        ),
        now=NOW,
    )
    assert verdict.code == "unknown"


def test_the_verdict_vocabulary_is_closed_and_complete() -> None:
    """Red if a code is added to one list and not the other, which is how a
    surface ends up rendering a verdict it has no sentence for."""
    assert set(VERDICT_CODES) == set(PRECEDENCE)
    assert len(VERDICT_CODES) == len(PRECEDENCE) == 9


def test_the_four_count_labels_are_fixed() -> None:
    """Both surfaces show these four words. Red if either repo renames one."""
    assert COUNT_LABELS == ("Transcript", "Delivered", "In AI Matrx", "On this Mac")


# --------------------------------------------------------------------------
# Plain English for time. Pure, so it is testable at all.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "then,expected",
    [
        (None, "never"),
        ("2026-09-17T21:59:30+00:00", "seconds ago"),
        ("2026-09-17T21:30:00+00:00", "30 minutes ago"),
        ("2026-09-17T21:00:00+00:00", "1 hour ago"),
        ("2026-09-17T14:00:00+00:00", "8 hours ago"),
        ("2026-09-16T20:00:00+00:00", "yesterday"),
        ("2026-09-15T22:00:00+00:00", "2 days ago"),
        ("2026-07-01T10:00:00+00:00", "on 2026-07-01"),
        ("2026-09-17T21:00:00Z", "1 hour ago"),
        ("not a timestamp", "at an unknown time"),
    ],
)
def test_ago_speaks_english(then: str | None, expected: str) -> None:
    assert ago(then, NOW) == expected


# --------------------------------------------------------------------------
# The wire shape both surfaces render.
# --------------------------------------------------------------------------


def test_payload_carries_every_layer_and_the_four_counts() -> None:
    facts = _facts()
    payload = as_payload(facts, decide(facts, now=NOW), now=NOW)
    assert payload["schema_version"] == 1
    assert payload["count_labels"] == list(COUNT_LABELS)
    assert set(payload["counts"]) == {
        "transcript",
        "delivered",
        "cloud_messages",
        "mirror_messages",
    }
    for layer in ("transcript", "delivered", "cloud", "mirror"):
        assert "checked" in payload[layer]
    assert payload["computed_at"] == "2026-09-17T22:00:00+00:00"
