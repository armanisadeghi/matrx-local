"""The Claude Code screen judges a session against the CLOUD, not this Mac."""

from __future__ import annotations

import base64

from app.services.coding_sessions.claude_overview import (
    _CHANGED_GRACE_SECONDS,
    _session_state,
    raw_session_id,
)


def _sdk_key(session_id: str) -> str:
    encoded = base64.urlsafe_b64encode(session_id.encode()).decode().rstrip("=")
    return f"claude-sdk:{'a' * 64}:{encoded}"


def test_both_spellings_of_a_session_reduce_to_the_raw_uuid() -> None:
    session_id = "68eb3328-e4d2-4ac5-9cf7-0691d8cb973a"
    assert raw_session_id(session_id) == session_id
    assert raw_session_id(_sdk_key(session_id)) == session_id
    assert raw_session_id("claude-sdk:broken") == "claude-sdk:broken"


def test_a_hook_mirrored_session_is_in_cloud_even_if_this_mac_never_uploaded_it() -> None:
    # The 2026-09-08 defect: 1,671 sessions on the server, 0 "synced" here.
    binding = {"last_seen_at": "2026-09-08T12:00:00+00:00", "fidelity": "event_mirror"}
    state = _session_state(
        cloud_checked=True,
        binding=binding,
        activity_ns=1_788_868_800 * 1_000_000_000,  # 2026-09-08T12:00:00Z (Claude's lastActivityAt)
        queue={"pending": 0, "quarantined": 0},
    )
    assert state == "in_cloud"


def test_local_transcript_newer_than_cloud_beyond_grace_is_changed() -> None:
    binding = {"last_seen_at": "2026-09-08T12:00:00+00:00"}
    seen_ns = 1_788_868_800 * 1_000_000_000
    inside_grace = seen_ns + (_CHANGED_GRACE_SECONDS - 1) * 1_000_000_000
    beyond_grace = seen_ns + (_CHANGED_GRACE_SECONDS + 1) * 1_000_000_000
    queue = {"pending": 0, "quarantined": 0}
    assert _session_state(cloud_checked=True, binding=binding, activity_ns=inside_grace, queue=queue) == "in_cloud"
    assert _session_state(cloud_checked=True, binding=binding, activity_ns=beyond_grace, queue=queue) == "changed"


def test_queue_and_quarantine_outrank_absence_and_failure_outranks_everything() -> None:
    queue_only = {"pending": 3, "quarantined": 0}
    assert _session_state(cloud_checked=True, binding=None, activity_ns=1, queue=queue_only) == "queued"
    failed = {"pending": 3, "quarantined": 1}
    assert _session_state(cloud_checked=True, binding={"last_seen_at": None}, activity_ns=1, queue=failed) == "failed"
    none = {"pending": 0, "quarantined": 0}
    assert _session_state(cloud_checked=True, binding=None, activity_ns=1, queue=none) == "not_in_cloud"


def test_an_unanswered_server_is_unknown_never_not_in_cloud() -> None:
    none = {"pending": 0, "quarantined": 0}
    assert _session_state(cloud_checked=False, binding=None, activity_ns=1, queue=none) == "unknown"
