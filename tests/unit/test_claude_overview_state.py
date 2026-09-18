"""The screen judges a session against the CLOUD, not this Mac.

The judgement moved to ``cloud_state`` on 2026-09-17 when Codex, Cursor and
VS Code sessions joined the same list: there is ONE ``_session_state`` for
every provider, so these guards now bind all four.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.coding_sessions import claude_overview
from app.services.coding_sessions.cloud_state import (
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


# ── The startup warm-up ─────────────────────────────────────────────────────
#
# Until 2026-09-11 the first open of the Coding Sessions screen after an engine
# start paid the whole cold read while a person watched a spinner, because the
# index was only built when something asked for it. The engine now refreshes
# the PERSISTED index at startup — and after the first ever build that refresh
# re-reads nothing, so the screen is instant on a cold engine too.


def _write_index_record(root: Path, *, session_id: str, title: str) -> None:
    folder = root / "acct-1" / "org-1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"local_{uuid4()}.json").write_text(
        json.dumps(
            {
                "sessionId": f"local_{uuid4()}",
                "cliSessionId": session_id,
                "title": title,
                "titleSource": "user",
                "cwd": "/Users/someone/code/matrx-local",
                "lastActivityAt": 1_788_868_800_000,
            }
        )
    )


@pytest.fixture()
def isolated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A throwaway persisted index, and no transcript phase to worry about."""
    from app.services.coding_sessions.claude_index_store import ClaudeIndexStore

    store = ClaudeIndexStore(tmp_path / "store" / "index.sqlite3")
    claude_overview._reset_index_state_for_tests(store)
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(tmp_path / "absent-ledger.json"))

    async def _no_transcripts(_store):
        return {}

    monkeypatch.setattr(claude_overview, "_refresh_transcripts", _no_transcripts)
    yield store
    claude_overview._reset_index_state_for_tests(None)


def test_warm_index_cache_persists_the_index_so_the_first_open_is_free(
    tmp_path: Path, isolated_index
) -> None:
    root = tmp_path / "claude-code-sessions"
    _write_index_record(root, session_id="11111111-1111-4111-8111-111111111111", title="One")
    _write_index_record(root, session_id="22222222-2222-4222-8222-222222222222", title="Two")

    asyncio.run(claude_overview.warm_index_cache(root))

    snapshot = asyncio.run(claude_overview.index_snapshot())
    assert snapshot.complete is True, "warm_index_cache left the index cold"
    assert snapshot.totals["files"] == 2
    assert not snapshot.totals.get("truncated")
    assert {entry.title for entry in snapshot.entries.values()} == {"One", "Two"}
    assert claude_overview.index_report(snapshot)["state"] == "fresh"


def test_a_restarted_engine_answers_from_the_persisted_index_without_reading_a_file(
    tmp_path: Path, isolated_index, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE fix for the 31.76 s / 58.96 s / 1,209 s reads: a cold engine that
    has an index on disk must answer from it, having opened no record file."""
    root = tmp_path / "claude-code-sessions"
    _write_index_record(root, session_id="11111111-1111-4111-8111-111111111111", title="One")
    asyncio.run(claude_overview.warm_index_cache(root))

    # The engine restarts: in-memory state is gone, the store is not.
    claude_overview._reset_index_state_for_tests(isolated_index)

    def _must_not_read(_stamps):
        raise AssertionError(
            "a record file was parsed while answering — the cold read is back"
        )

    monkeypatch.setattr(claude_overview, "read_record_rows", _must_not_read)
    monkeypatch.setattr(
        claude_overview.claude_index_helper,
        "helper_command",
        lambda *_a, **_k: ["/nonexistent/helper"],
    )

    snapshot = asyncio.run(claude_overview.index_snapshot())
    assert {entry.title for entry in snapshot.entries.values()} == {"One"}
    assert snapshot.complete is True
