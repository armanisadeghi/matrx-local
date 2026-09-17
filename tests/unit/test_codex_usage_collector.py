import asyncio
import datetime as dt
import json
import sqlite3
import threading

import pytest

from app.services.codex_usage import collector


UTC = dt.timezone.utc


def _state(home, rows):
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("create table projects (id text, name text)")
        db.execute("create table threads (id text, name text, cwd text, rollout_path text, created_at real, updated_at real, project_id text)")
        db.execute("create table thread_spawn_edges (child_thread_id text, parent_thread_id text)")
        for row in rows:
            db.execute("insert into threads values (?, ?, ?, ?, ?, ?, ?)", row)


def _rollout(path, response_id, thread_id="thread"):
    stamp = "2026-09-12T12:00:00Z"
    path.write_text("\n".join(json.dumps(item) for item in [
        {"type": "turn_context", "timestamp": stamp, "payload": {"turn_id": "turn", "model": "gpt-5.6-terra", "reasoning_effort": "medium"}},
        {"type": "token_usage_record", "timestamp": stamp, "payload": {"thread_id": thread_id, "response_id": response_id, "turn_id": "turn", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 2, "total_tokens": 15}}},
    ]) + "\n")


def test_resumes_frozen_candidates_with_global_response_dedup(tmp_path, monkeypatch):
    first, second = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    _rollout(first, "same-response"); _rollout(second, "same-response", "thread-two")
    now = dt.datetime(2026, 9, 12, 12, tzinfo=UTC).timestamp()
    _state(tmp_path, [("thread", "One", str(tmp_path), str(first), now - 10, now + 10, None), ("thread-two", "Two", str(tmp_path), str(second), now - 10, now + 10, None)])
    monkeypatch.setattr(collector, "MAX_FILES", 1)
    scan = collector.create_scan(dt.datetime(2026, 9, 12, tzinfo=UTC), dt.datetime(2026, 9, 13, tzinfo=UTC), tmp_path)
    collector.advance_scan(scan)
    assert collector.snapshot(scan)["coverage"]["can_resume"]
    collector.advance_scan(scan)
    result = collector.snapshot(scan)
    assert result["coverage"]["scan_exhausted"]
    assert result["coverage"]["complete"]
    assert result["totals"]["response_count"] == 1
    assert result["totals"]["total_tokens"] == 15


def test_missing_frozen_candidate_exhausts_but_is_not_complete(tmp_path):
    now = dt.datetime(2026, 9, 12, 12, tzinfo=UTC).timestamp()
    _state(tmp_path, [("thread", "Missing", str(tmp_path), str(tmp_path / "gone.jsonl"), now - 10, now + 10, None)])
    scan = collector.create_scan(dt.datetime(2026, 9, 12, tzinfo=UTC), dt.datetime(2026, 9, 13, tzinfo=UTC), tmp_path)
    collector.advance_scan(scan)
    coverage = collector.snapshot(scan)["coverage"]
    assert coverage["scan_exhausted"] and not coverage["complete"] and not coverage["can_resume"]
    assert coverage["missing_files"] == 1


def test_one_shared_scan_survives_waiter_cancellation_and_rejects_other_range(monkeypatch):
    started, release = threading.Event(), threading.Event()
    scan = collector.UsageScan(dt.datetime(2026, 9, 12, tzinfo=UTC), dt.datetime(2026, 9, 13, tzinfo=UTC), dt.datetime.now(UTC), {}, {}, [])
    monkeypatch.setattr(collector, "create_scan", lambda *_: scan)
    def slow_advance(value):
        started.set(); release.wait(2); value.last_scan_at = dt.datetime.now(UTC)
    monkeypatch.setattr(collector, "advance_scan", slow_advance)

    async def exercise():
        service = collector.CodexUsageSnapshotService()
        first = asyncio.create_task(service.read(scan.start, scan.end, refresh=True))
        await asyncio.to_thread(started.wait, 1)
        same = asyncio.create_task(service.read(scan.start, scan.end, refresh=True))
        with pytest.raises(collector.CollectionBusyError):
            await service.read(scan.start + dt.timedelta(days=2), scan.end + dt.timedelta(days=2), refresh=True)
        first.cancel()
        with pytest.raises(asyncio.CancelledError): await first
        release.set()
        resolved = await same
        assert resolved["collection"]["state"] == "refreshed"
    asyncio.run(exercise())


def test_collaboration_transport_requires_exact_structured_namespace_and_name():
    assert collector._is_collaboration_send_message({"namespace": "collaboration"}, "send_message")
    assert not collector._is_collaboration_send_message({}, "send_message")
    assert not collector._is_collaboration_send_message({"namespace": "tools"}, "send_message")
    assert not collector._is_collaboration_send_message({"namespace": "collaboration"}, "send_message_to_thread")


def test_snapshot_estimates_cells_bins_and_mixed_projects_without_fabricating_unknowns():
    """The 77-response local window must keep estimates on every scoped row."""
    start = dt.datetime(2026, 9, 14, 13, 31, tzinfo=UTC)
    scan = collector.UsageScan(start, start + dt.timedelta(minutes=10), dt.datetime.now(UTC), {
        "sol": {"title": "AI Dream Releases", "project": "priced"},
        "terra": {"title": "Release worker", "project": "priced"},
        "unknown": {"title": "Unknown model", "project": "unknown-rate"},
    }, {"terra": "sol"}, [])
    scan.cells[("sol", "gpt-5.6-sol", "medium")].update(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000, response_count=77)
    scan.cells[("sol", "unknown", "unknown")].update(peer_message_call_ids=1, peer_message_invocations=1)
    scan.cells[("terra", "gpt-5.6-terra", "low")].update(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000, response_count=1)
    scan.cells[("unknown", "future-model", "medium")].update(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000, response_count=1)
    scan.bins[(start, "sol", "gpt-5.6-sol", "medium")].update(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000, response_count=77)

    result = collector.snapshot(scan)
    by_model = {row["model"]: row for row in result["cells"]}
    assert by_model["gpt-5.6-sol"]["estimated_standard_credits"] == 600
    assert by_model["gpt-5.6-sol"]["credit_rate_known"] is True
    assert result["bins"][0]["estimated_standard_credits"] == 600
    assert result["conversations"][0]["estimated_standard_credits"] == 600
    assert result["workers"][0]["estimated_standard_credits"] == 350
    priced_project = next(row for row in result["projects"] if row["project"] == "priced")
    assert priced_project["estimated_standard_credits"] == 950
    assert priced_project["credit_rate_known"] is True
    activity = by_model["unknown"]
    assert activity["estimated_standard_credits"] is None
    assert activity["credit_rate_known"] is False
    unknown = by_model["future-model"]
    assert unknown["estimated_standard_credits"] is None
    assert unknown["credit_rate_known"] is False
    unknown_project = next(row for row in result["projects"] if row["project"] == "unknown-rate")
    assert unknown_project["estimated_standard_credits"] is None
    assert unknown_project["credit_rate_known"] is False
