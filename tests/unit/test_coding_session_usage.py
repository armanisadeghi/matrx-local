"""Usage for every coding-agent provider, one shape (CS-24).

The Claude Code guards run on REAL transcripts (tests/unit/fixtures/claude_usage,
copied from ~/.claude/projects with long strings redacted — see its README):
the expected numbers are computed here by the plain rule, one count per
(message id, request id), independently of the reader under test.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from app.services.codex_usage import collector
from app.services.coding_sessions import claude_index_store as store_module
from app.services.coding_sessions.claude_index_store import (
    ClaudeIndexStore,
    refresh_transcripts_sync,
    refresh_usage_sync,
    walk_transcripts,
)
from app.services.coding_sessions.claude_usage import (
    USAGE_FIELDS,
    UsageCursor,
    read_usage_increment,
)
from app.services.coding_sessions import usage_report
from app.services.coding_sessions.usage_report import (
    UsageReport,
    claude_limits,
    claude_report,
    codex_report,
    cursor_report,
    match_price,
    vscode_report,
)

FIXTURES = Path(__file__).parent / "fixtures" / "claude_usage"
UTC = dt.timezone.utc


def expected_usage(path: Path) -> tuple[Counter[str], int, set[str], set[str]]:
    """The plain rule: every assistant line, counted once per message.

    Returns (totals incl. requests, assistant LINE count, models, days).
    """
    seen: set[str] = set()
    totals: Counter[str] = Counter()
    lines = 0
    models: set[str] = set()
    days: set[str] = set()
    for raw in path.read_text().splitlines():
        record = json.loads(raw)
        if record.get("type") != "assistant":
            continue
        lines += 1
        message = record["message"]
        if message.get("model") == "<synthetic>":
            continue
        key = f"{message.get('id')}|{record.get('requestId')}"
        if key in seen:
            continue
        seen.add(key)
        usage = message["usage"]
        totals["requests"] += 1
        totals["input_tokens"] += usage.get("input_tokens", 0)
        totals["output_tokens"] += usage.get("output_tokens", 0)
        totals["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0)
        totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)
        models.add(message["model"])
        days.add(record["timestamp"][:10])
    return totals, lines, models, days


def _fixture_root(tmp_path: Path) -> Path:
    """The three real transcripts laid out as ~/.claude/projects/<project>/."""
    root = tmp_path / "projects" / "-Users-someone-code-common-docs"
    root.mkdir(parents=True)
    for source in sorted(FIXTURES.glob("*.jsonl")):
        (root / source.name).write_bytes(source.read_bytes())
    return tmp_path / "projects"


SUBAGENT_FIXTURES = FIXTURES / "subagents"


def _subagent_sources() -> list[tuple[str, Path]]:
    """(parent session id, transcript) for every real sub-agent fixture.

    The fixture tree mirrors the real layout exactly, so the parent id is the
    directory Claude nests the streams under — the same id the records' own
    ``sessionId`` field carries (asserted below, never assumed).
    """
    found: list[tuple[str, Path]] = []
    for path in sorted(SUBAGENT_FIXTURES.rglob("*.jsonl")):
        found.append((path.relative_to(SUBAGENT_FIXTURES).parts[0], path))
    return found


def _nested_fixture_root(tmp_path: Path) -> Path:
    """The real layout: main transcripts PLUS the sub-agent streams under them.

    ``<project>/<session>.jsonl`` for the main turn stream and
    ``<project>/<session>/subagents/[workflows/<wf>/]agent-<id>.jsonl`` for
    every sub-agent turn the session spent tokens on (6,447 of this Mac's
    8,147 transcripts on 2026-09-18).
    """
    root = _fixture_root(tmp_path)
    project = next(root.iterdir())
    for _parent, source in _subagent_sources():
        target = project / source.relative_to(SUBAGENT_FIXTURES)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return root


def _totals_of(cells: dict) -> Counter[str]:
    total: Counter[str] = Counter()
    for counter in cells.values():
        total.update(counter)
    return total


# ── the reader, on real records ─────────────────────────────────────────────


@pytest.mark.parametrize("source", sorted(FIXTURES.glob("*.jsonl")), ids=lambda p: p.stem[:8])
def test_real_transcript_counts_each_message_once(source: Path) -> None:
    expected, lines, models, days = expected_usage(source)
    # The fixture must contain the streamed duplicates the reader exists for.
    assert lines > expected["requests"], "fixture has no duplicate lines; it proves nothing"
    stat = source.stat()
    increment = read_usage_increment(
        source, None, size=stat.st_size, mtime_ns=stat.st_mtime_ns, byte_budget=1 << 30
    )
    assert not increment.truncated
    got = _totals_of(increment.cells)
    assert got["requests"] == expected["requests"]
    for name in USAGE_FIELDS:
        assert got[name] == expected[name], name
    assert {model for _hour, model in increment.cells} == models
    assert {hour[:10] for hour, _model in increment.cells} == days
    assert increment.cursor.offset == stat.st_size
    assert (increment.cursor.size, increment.cursor.mtime_ns) == (stat.st_size, stat.st_mtime_ns)


def test_incremental_tail_reads_equal_one_whole_read(tmp_path: Path) -> None:
    """Append-only growth: two partial reads land the same numbers as one."""
    source = max(FIXTURES.glob("*.jsonl"), key=lambda p: p.stat().st_size)
    expected, _lines, _models, _days = expected_usage(source)
    lines = source.read_bytes().splitlines(keepends=True)
    # Split INSIDE a run of duplicate lines so the dedupe must survive the boundary.
    assistant_at = [index for index, raw in enumerate(lines) if b'"type":"assistant"' in raw]
    cut = assistant_at[len(assistant_at) // 2] + 1
    growing = tmp_path / source.name
    growing.write_bytes(b"".join(lines[:cut]) + lines[cut][: len(lines[cut]) // 2])  # a half-written line
    stat = growing.stat()
    first = read_usage_increment(growing, None, size=stat.st_size, mtime_ns=stat.st_mtime_ns, byte_budget=1 << 30)
    assert first.cursor.offset == len(b"".join(lines[:cut])), "the half-written line must wait"
    growing.write_bytes(b"".join(lines))
    stat = growing.stat()
    second = read_usage_increment(
        growing,
        first.cursor,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        byte_budget=1 << 30,
        seen_keys=first.new_keys,  # what the store persisted for this session
    )
    assert second.cursor.offset == stat.st_size
    combined = _totals_of(first.cells)
    combined.update(_totals_of(second.cells))
    assert combined["requests"] == expected["requests"]
    for name in USAGE_FIELDS:
        assert combined[name] == expected[name], name


def _repeat_keys(path: Path) -> set[str]:
    """Every (message id, request id) a transcript carries."""
    keys: set[str] = set()
    for raw in path.read_text().splitlines():
        record = json.loads(raw)
        if record.get("type") != "assistant":
            continue
        keys.add(f"{record['message'].get('id')}|{record.get('requestId')}")
    return keys


def _repeats_beyond(path: Path, *, window: int = 64) -> list[int]:
    """Repeats of one message with more than ``window`` other messages between.

    Claude Code re-writes an earlier assistant message much later in a long
    session (measured 2026-09-18 on the real 52.6 MB transcript 97ce06fb…:
    205 messages whose repeat has more than 64 other messages in between). A
    dedupe that remembers only the last N messages counts every one of those
    a second time, so a fixture without such a pair proves nothing.
    """
    keys: list[str] = []
    for raw in path.read_text().splitlines():
        record = json.loads(raw)
        if record.get("type") != "assistant":
            continue
        message = record["message"]
        usage = message.get("usage")
        model = message.get("model")
        if not isinstance(usage, dict) or not model or model == "<synthetic>":
            continue
        keys.append(f"{message.get('id')}|{record.get('requestId')}")
    distinct_by_line: list[int] = []
    first_seen: dict[str, int] = {}
    places: dict[str, list[int]] = {}
    for index, key in enumerate(keys):
        first_seen.setdefault(key, len(first_seen))
        distinct_by_line.append(len(first_seen))
        places.setdefault(key, []).append(index)
    return [
        distinct_by_line[later] - distinct_by_line[earlier]
        for spots in places.values()
        for earlier, later in zip(spots, spots[1:])
        if distinct_by_line[later] - distinct_by_line[earlier] > window
    ]


def test_a_message_repeated_far_later_is_still_counted_once() -> None:
    """Break this catches: bounding the dedupe by a count of recent keys.

    Until 2026-09-18 the reader remembered only the last 64 keys, so the
    repeats Claude writes hundreds of lines later were counted twice — on the
    real 52.6 MB transcript that inflated requests by 208 and cache reads by
    105 million tokens.
    """
    source = FIXTURES / "97ce06fb-fe43-496b-8c0d-08baed25bfa7.jsonl"
    assert _repeats_beyond(source), (
        "fixture has no repeat with more than 64 other messages in between; it proves nothing"
    )
    expected, lines, _models, _days = expected_usage(source)
    assert lines > expected["requests"]
    stat = source.stat()
    increment = read_usage_increment(
        source, None, size=stat.st_size, mtime_ns=stat.st_mtime_ns, byte_budget=1 << 30
    )
    got = _totals_of(increment.cells)
    assert got["requests"] == expected["requests"]
    for name in USAGE_FIELDS:
        assert got[name] == expected[name], name


def test_dedupe_survives_a_resume_between_the_two_occurrences(tmp_path: Path) -> None:
    """The same, split across refreshes: the persisted key set must carry it."""
    source = FIXTURES / "97ce06fb-fe43-496b-8c0d-08baed25bfa7.jsonl"
    expected, _lines, _models, _days = expected_usage(source)
    lines = source.read_bytes().splitlines(keepends=True)
    assistant_at = [index for index, raw in enumerate(lines) if b'"type":"assistant"' in raw]
    # Cut well past the first occurrence and well before the repeat, so the
    # first read ends more than 64 assistant lines before the duplicate.
    cut = assistant_at[len(assistant_at) // 2] + 1
    growing = tmp_path / source.name
    growing.write_bytes(b"".join(lines[:cut]))
    stat = growing.stat()
    first = read_usage_increment(
        growing, None, size=stat.st_size, mtime_ns=stat.st_mtime_ns, byte_budget=1 << 30
    )
    growing.write_bytes(b"".join(lines))
    stat = growing.stat()
    second = read_usage_increment(
        growing,
        first.cursor,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        byte_budget=1 << 30,
        seen_keys=first.new_keys,  # what the store persisted for this session
    )
    combined = _totals_of(first.cells)
    combined.update(_totals_of(second.cells))
    assert combined["requests"] == expected["requests"]
    for name in USAGE_FIELDS:
        assert combined[name] == expected[name], name


def test_a_rewritten_transcript_restarts_from_zero(tmp_path: Path) -> None:
    """A shorter file replaces the session's usage, so its keys start over."""
    source = min(FIXTURES.glob("*.jsonl"), key=lambda p: p.stat().st_size)
    shrunk = tmp_path / source.name
    shrunk.write_bytes(source.read_bytes()[: source.stat().st_size // 2].rsplit(b"\n", 1)[0] + b"\n")
    stat = shrunk.stat()
    expected, _lines, _models, _days = expected_usage(shrunk)
    previous = UsageCursor(offset=source.stat().st_size, size=source.stat().st_size, mtime_ns=1)
    # Every key of the old read is offered back: a restart must ignore them,
    # or the replaced rows would come back empty.
    stale = {key for key in _repeat_keys(shrunk)} | {"x|y"}
    increment = read_usage_increment(
        shrunk, previous, size=stat.st_size, mtime_ns=stat.st_mtime_ns, byte_budget=1 << 30, seen_keys=stale
    )
    assert increment.restarted
    assert increment.cursor.offset == stat.st_size
    assert _totals_of(increment.cells)["requests"] == expected["requests"]
    assert len(increment.new_keys) == expected["requests"]
    assert "x|y" not in increment.new_keys



# ── sub-agent turns are the session's spend (CS-33/F5) ─────────────────────
#
# "sub-agent turns ARE the session's spend. Count every transcript under a
# session (the nested */*/… .jsonl), attribute it to the parent session, and
# show the split (main vs sub-agents) as a column so nobody mistakes it"
# (Arman, 2026-09-18). Until this lane the walk globbed ``*/*.jsonl`` — one
# directory level — and reached 1,665 of this Mac's 8,147 transcripts.


def test_the_fixture_subagents_are_real_streams_of_their_parent() -> None:
    """The attribution comes from the files, not from a guessed mapping."""
    sources = _subagent_sources()
    assert sources, "no sub-agent fixture; the nesting guards below prove nothing"
    depths = {len(path.relative_to(SUBAGENT_FIXTURES).parts) for _parent, path in sources}
    assert max(depths) > 3, f"no fixture deeper than <session>/subagents/<file>: {depths}"
    for parent, path in sources:
        ids = {
            json.loads(raw).get("sessionId")
            for raw in path.read_text().splitlines()
            if raw.strip()
        } - {None}
        assert ids == {parent}, f"{path.name} says {ids}, nested under {parent}"
        expected, lines, _models, _days = expected_usage(path)
        assert expected["requests"], f"{path.name} has no priced turn"
        assert lines > expected["requests"], f"{path.name} has no duplicate lines"


def test_the_walk_reaches_every_transcript_under_a_session(tmp_path: Path) -> None:
    root = _nested_fixture_root(tmp_path)
    found = walk_transcripts(root)
    on_disk = {path.resolve() for path in root.rglob("*.jsonl")}
    assert {entry.path.resolve() for entry in found.values()} == on_disk
    nested = {
        entry.session_id
        for entry in found.values()
        if entry.kind == "subagent"
    }
    assert nested == {parent for parent, _path in _subagent_sources()}


def test_a_sessions_usage_includes_its_subagent_turns(tmp_path: Path) -> None:
    root = _nested_fixture_root(tmp_path)
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    # The plain rule, per session: every transcript nested under it counts.
    expected: dict[str, Counter[str]] = {}
    for source in FIXTURES.glob("*.jsonl"):
        expected.setdefault(source.stem, Counter()).update(expected_usage(source)[0])
    subagent_expected: dict[str, Counter[str]] = {}
    for parent, source in _subagent_sources():
        counts = expected_usage(source)[0]
        expected.setdefault(parent, Counter()).update(counts)
        subagent_expected.setdefault(parent, Counter()).update(counts)
    stored = store.usage_rows("0000-00-00T00", "9999-12-31T23")
    got: dict[str, Counter[str]] = {}
    for row in stored:
        bucket = got.setdefault(row["session_id"], Counter())
        for name in (*USAGE_FIELDS, "requests"):
            bucket[name] += row[name]
    # The numbers first: a walk that stops one level down misses these.
    assert got == expected
    # Then the split, per session, on the same rows.
    got_subagent: dict[str, Counter[str]] = {}
    for row in stored:
        if row["lane"] != "subagent":
            continue
        lane = got_subagent.setdefault(row["session_id"], Counter())
        for name in (*USAGE_FIELDS, "requests"):
            lane[name] += row[name]
    assert got_subagent == subagent_expected


def test_the_report_splits_main_from_subagent_spend(tmp_path: Path) -> None:
    root = _nested_fixture_root(tmp_path)
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    cells = store.usage_rows("0000-00-00T00", "9999-12-31T23")
    report = claude_report(
        cells,
        start=dt.datetime(2026, 8, 1, tzinfo=UTC),
        end=dt.datetime(2026, 10, 1, tzinfo=UTC),
        tz_offset_minutes=0,
        status=store.usage_status(),
        prices={},
        limits=claude_limits(_limits_fixture(tmp_path)),
    )
    UsageReport.model_validate(report.model_dump())
    assert report.metrics.subagents is True
    sub_expected: Counter[str] = Counter()
    for _parent, source in _subagent_sources():
        sub_expected.update(expected_usage(source)[0])
    sub_tokens = sum(
        sub_expected[name] for name in USAGE_FIELDS
    )
    # The totals and EVERY grouping expose the split, and it adds up.
    assert report.totals.subagent_requests == sub_expected["requests"]
    assert report.totals.subagent_total_tokens == sub_tokens
    assert report.totals.main_requests == report.totals.requests - sub_expected["requests"]
    assert report.totals.main_total_tokens == report.totals.total_tokens - sub_tokens
    for grouping in (report.by_day, report.by_model, report.by_session, report.by_project):
        assert sum(row.subagent_requests for row in grouping) == sub_expected["requests"]
        for row in grouping:
            assert row.main_requests + row.subagent_requests == row.requests
            assert row.main_total_tokens + row.subagent_total_tokens == row.total_tokens
    # A session whose sub-agents did the spending says so on its own row.
    parents = {parent for parent, _path in _subagent_sources()}
    rows = {row.key: row for row in report.by_session}
    for parent in parents:
        assert rows[parent].subagent_requests > 0, parent


def test_a_subagent_turn_is_deduped_in_the_parents_key_space(tmp_path: Path) -> None:
    """The same stream copied to a second path under the parent counts ONCE.

    Claude re-writes a sub-agent's turns, and a per-file key space would count
    every repeat again. The keys belong to the PARENT session.
    """
    root = _nested_fixture_root(tmp_path)
    project = next(root.iterdir())
    parent, source = next(
        (parent, path) for parent, path in _subagent_sources() if (project / parent).is_dir()
    )
    twin = project / parent / "subagents" / "workflows" / "wf_twin" / source.name
    twin.parent.mkdir(parents=True, exist_ok=True)
    twin.write_bytes(source.read_bytes())
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    counted: Counter[str] = Counter()
    for row in store.usage_rows("0000-00-00T00", "9999-12-31T23"):
        if row["session_id"] != parent:
            continue
        for name in (*USAGE_FIELDS, "requests"):
            counted[name] += row[name]
    once: Counter[str] = Counter()
    for path in [FIXTURES / f"{parent}.jsonl", *(p for q, p in _subagent_sources() if q == parent)]:
        if path.exists():
            once.update(expected_usage(path)[0])
    for name in (*USAGE_FIELDS, "requests"):
        assert counted[name] == once[name], name


# ── the store and the refresh: no second walk, bounded, resumable ───────────


def test_refresh_transcripts_lands_usage_in_the_same_pass(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    result = refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    assert result["usage"]["pending_sessions"] == 0
    assert result["usage"]["sessions_read"] == len(list(FIXTURES.glob("*.jsonl")))
    rows = store.usage_rows("0000-00-00T00", "9999-12-31T23")
    by_session: Counter[str] = Counter()
    for row in rows:
        by_session[row["session_id"]] += row["requests"]
    for source in FIXTURES.glob("*.jsonl"):
        expected, _lines, _models, _days = expected_usage(source)
        assert by_session[source.stem] == expected["requests"], source.stem
    status = store.usage_status()
    assert status["built"] and status["pending_sessions"] == 0
    # A second refresh with nothing changed reads nothing.
    again = refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    assert again["usage"]["bytes_read"] == 0
    assert again["usage"]["sessions_read"] == 0


def test_usage_read_is_bounded_and_resumes_next_refresh(tmp_path: Path) -> None:
    root = _nested_fixture_root(tmp_path)
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    store.ensure_ready()
    found = walk_transcripts(root)
    smallest = min(entry.size for entry in found.values())
    first = refresh_usage_sync(store, found, byte_budget=smallest + 10)
    assert first["budget_exhausted"]
    assert first["pending_sessions"] > 0
    assert store.usage_status()["pending_sessions"] == first["pending_sessions"]
    # Keep refreshing with the same small budget until it settles: every
    # refresh makes progress and the final numbers are the whole truth.
    for _ in range(64):
        step = refresh_usage_sync(store, found, byte_budget=smallest + 10)
        if step["pending_sessions"] == 0:
            break
    assert store.usage_status()["pending_sessions"] == 0
    got: Counter[str] = Counter()
    for row in store.usage_rows("0000-00-00T00", "9999-12-31T23"):
        got["requests"] += row["requests"]
        for name in USAGE_FIELDS:
            got[name] += row[name]
    expected: Counter[str] = Counter()
    for source in FIXTURES.glob("*.jsonl"):
        expected.update(expected_usage(source)[0])
    for _parent, source in _subagent_sources():
        expected.update(expected_usage(source)[0])
    assert got == expected


def test_usage_rows_carry_the_session_tab_labels(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    store.ensure_ready()
    session_id = next(FIXTURES.glob("*.jsonl")).stem
    record = {column: None for column in store_module._RECORD_COLUMNS}  # noqa: SLF001 — same package
    record.update(
        {
            "path": str(tmp_path / "record.json"),
            "mtime_ns": 1,
            "size": 1,
            "account": "acct",
            "cli_session_id": session_id,
            "last_activity_at": 1,
            "title": "The title the Sessions tab shows",
            "title_source": "user",
            "workspace_name": "common-docs",
            "is_archived": 0,
            "unreadable": 0,
        }
    )
    store.upsert([record])
    store.rebuild_sessions()
    refresh_transcripts_sync(store, sidebar_ids={session_id}, root=root)
    rows = [row for row in store.usage_rows("0000-00-00T00", "9999-12-31T23") if row["session_id"] == session_id]
    assert rows and rows[0]["title"] == "The title the Sessions tab shows"
    assert rows[0]["project"] == "common-docs"


# ── one shape for every provider ────────────────────────────────────────────


def _keys(value, prefix="") -> set[str]:
    """Every key path in a payload, lists descending into their first item."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(f"{prefix}{key}")
            found |= _keys(item, f"{prefix}{key}.")
    elif isinstance(value, list) and value:
        found |= _keys(value[0], f"{prefix}[].")
    return found


def _codex_snapshot(tmp_path: Path) -> dict:
    """A real collector snapshot over a two-model rollout, not a hand-typed dict."""
    home = tmp_path / "codex"
    home.mkdir()
    rollout = home / "rollout.jsonl"
    stamp = "2026-09-12T12:00:00Z"
    rollout.write_text(
        "\n".join(
            json.dumps(item)
            for item in [
                {"type": "turn_context", "timestamp": stamp, "payload": {"turn_id": "t1", "model": "gpt-5.6-terra", "reasoning_effort": "medium"}},
                {"type": "token_usage_record", "timestamp": stamp, "payload": {"thread_id": "thread", "response_id": "r1", "turn_id": "t1", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 7, "reasoning_output_tokens": 2, "total_tokens": 107}}},
                {"type": "turn_context", "timestamp": stamp, "payload": {"turn_id": "t2", "model": "gpt-unknown", "reasoning_effort": "low"}},
                {"type": "token_usage_record", "timestamp": stamp, "payload": {"thread_id": "thread", "response_id": "r2", "turn_id": "t2", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 1, "reasoning_output_tokens": 0, "total_tokens": 11}}},
            ]
        )
        + "\n"
    )
    now = dt.datetime(2026, 9, 12, 12, tzinfo=UTC).timestamp()
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("create table projects (id text, name text)")
        db.execute("create table threads (id text, name text, cwd text, rollout_path text, created_at real, updated_at real, project_id text)")
        db.execute("create table thread_spawn_edges (child_thread_id text, parent_thread_id text)")
        db.execute("insert into threads values (?, ?, ?, ?, ?, ?, ?)", ("thread", "A thread", str(home), str(rollout), now - 10, now + 10, None))
    scan = collector.create_scan(dt.datetime(2026, 9, 12, tzinfo=UTC), dt.datetime(2026, 9, 13, tzinfo=UTC), home)
    collector.advance_scan(scan)
    return collector.snapshot(scan)


def _claude_cells(tmp_path: Path) -> tuple[ClaudeIndexStore, list[dict]]:
    root = _fixture_root(tmp_path)
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    return store, store.usage_rows("0000-00-00T00", "9999-12-31T23")


def test_codex_and_claude_reports_are_one_shape(tmp_path: Path) -> None:
    codex = codex_report(
        _codex_snapshot(tmp_path),
        {"status": "available", "observed_at": "2026-09-12T12:00:00+00:00", "limits": [{"bucket": "primary", "used_percent": 10.0, "remaining_percent": 90.0, "window_minutes": 300, "resets_at": 1_789_000_000}]},
        tz_offset_minutes=0,
    ).model_dump()
    store, cells = _claude_cells(tmp_path)
    claude = claude_report(
        cells,
        start=dt.datetime(2026, 8, 1, tzinfo=UTC),
        end=dt.datetime(2026, 10, 1, tzinfo=UTC),
        tz_offset_minutes=0,
        status=store.usage_status(),
        prices={"claude-fable-5": (10.0, 50.0, 1.0)},
        limits=claude_limits(_limits_fixture(tmp_path)),
    ).model_dump()
    for payload in (codex, claude):
        UsageReport.model_validate(payload)
    assert _keys(codex) == _keys(claude)
    # One shape, and each provider says what IT records: only Claude Code
    # keeps sub-agent turns on this Mac, so only its column exists.
    assert codex["metrics"] == {"tokens": True, "requests": True, "cost": True, "lines": False, "subagents": False}
    assert claude["metrics"] == {"tokens": True, "requests": True, "cost": True, "lines": False, "subagents": True}
    # Same token semantics on both sides: input excludes cache reads, total includes everything.
    for payload in (codex, claude):
        row = payload["totals"]
        assert row["total_tokens"] == row["input_tokens"] + row["output_tokens"] + row["cache_read_tokens"] + row["cache_creation_tokens"]
        assert sum(day["requests"] for day in payload["by_day"]) == row["requests"]
        assert sum(model["requests"] for model in payload["by_model"]) == row["requests"]
        assert sum(session["requests"] for session in payload["by_session"]) == row["requests"]
    # A Codex activity-only row (model "unknown", no tokens, no responses) is not a grid line.
    assert all(row["total_tokens"] or row["requests"] for row in codex["by_model"])
    # Codex: the cached 60 of 100 input tokens are cache reads, never double-counted.
    terra = next(row for row in codex["by_model"] if row["model"] == "gpt-5.6-terra")
    assert (terra["input_tokens"], terra["cache_read_tokens"], terra["output_tokens"]) == (40, 60, 7)
    # An unpriced model makes the TOTAL cost unknown on both sides, never a smaller number.
    assert codex["cost"]["unpriced_models"] == ["gpt-unknown"] and codex["totals"]["cost"] is None
    assert "claude-opus-5" in claude["cost"]["unpriced_models"] and claude["totals"]["cost"] is None
    fable = next(row for row in claude["by_model"] if row["model"] == "claude-fable-5")
    assert fable["cost"] == pytest.approx(
        (fable["input_tokens"] * 10 + fable["cache_read_tokens"] * 1 + fable["cache_creation_tokens"] * 12.5 + fable["output_tokens"] * 50) / 1e6
    )


def test_claude_day_rows_follow_the_viewers_timezone(tmp_path: Path) -> None:
    _store, cells = _claude_cells(tmp_path)
    late = [cell for cell in cells if cell["hour"].endswith("T23") or cell["hour"].endswith("T00")]
    assert late, "fixture has no turn near midnight UTC; the timezone shift is untested"
    utc = claude_report(cells, start=dt.datetime(2026, 8, 1, tzinfo=UTC), end=dt.datetime(2026, 10, 1, tzinfo=UTC), tz_offset_minutes=0, status={"built": True, "pending_sessions": 0}, prices={}, limits=vscode_report(start=dt.datetime(2026, 8, 1, tzinfo=UTC), end=dt.datetime(2026, 10, 1, tzinfo=UTC), tz_offset_minutes=0, extension_detected=False).limits)
    pacific = claude_report(cells, start=dt.datetime(2026, 8, 1, tzinfo=UTC), end=dt.datetime(2026, 10, 1, tzinfo=UTC), tz_offset_minutes=-420, status={"built": True, "pending_sessions": 0}, prices={}, limits=utc.limits)
    assert [row.key for row in utc.by_day] != [row.key for row in pacific.by_day]
    assert utc.totals.requests == pacific.totals.requests
    assert utc.cost.available is False and "catalog has not synced" in (utc.cost.reason or "")


def test_price_matching_never_guesses_loosely() -> None:
    prices = {"claude-fable-5": (10.0, 50.0, 1.0), "claude-opus-4-8": (5.0, 25.0, 6.25)}
    assert match_price("claude-fable-5-1", prices) == (10.0, 50.0, 1.0)
    assert match_price("claude-fable-5", prices) == (10.0, 50.0, 1.0)
    assert match_price("claude-opus-5", prices) is None
    assert match_price("claude-fable-50", prices) is None


# ── limits, Cursor, VS Code: what each provider really exposes ──────────────


def _limits_fixture(tmp_path: Path) -> Path:
    """The shape Claude Code writes to .claude.json (observed 2026-09-17), with no account identity."""
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {
                "oauthAccount": {"emailAddress": "never@copied.example", "billingType": "stripe_subscription", "userRateLimitTier": "default_claude_max_20x"},
                "cachedUsageUtilization": {
                    "fetchedAtMs": 1_787_530_274_221,
                    "utilization": {
                        "five_hour": {"utilization": 4, "resets_at": "2026-08-24T03:50:00+00:00"},
                        "seven_day": {"utilization": 0, "resets_at": "2026-08-30T23:00:00+00:00"},
                        "nimbus_quill": {"utilization": 0, "resets_at": None},
                        "extra_usage": {"is_enabled": False, "monthly_limit": 35000, "used_credits": 35091, "utilization": 100, "currency": "USD", "decimal_places": 2, "disabled_reason": "org_level_disabled_until"},
                        "limits": [
                            {"kind": "session", "group": "session", "percent": 4, "resets_at": "2026-08-24T03:50:00+00:00", "scope": None, "is_active": True},
                            {"kind": "weekly_all", "group": "weekly", "percent": 0, "resets_at": "2026-08-30T23:00:00+00:00", "scope": None, "is_active": False},
                            {"kind": "weekly_scoped", "group": "weekly", "percent": 0, "resets_at": None, "scope": {"model": {"id": None, "display_name": "Fable"}}, "is_active": False},
                        ],
                    },
                },
            }
        )
    )
    return path


def test_claude_limits_read_the_cache_and_never_the_identity(tmp_path: Path) -> None:
    limits = claude_limits(_limits_fixture(tmp_path))
    assert limits.status == "available"
    assert limits.plan == "stripe_subscription · default_claude_max_20x"
    assert limits.observed_at == "2026-08-24T00:11:14+00:00"
    labels = [window.label for window in limits.windows]
    assert labels[:3] == ["Session (5-hour)", "Weekly (all models)", "Weekly (Fable)"]
    assert labels[3].startswith("Extra usage credits (monthly) · 350.91 of 350.00 USD · disabled")
    assert not any("nimbus" in label for label in labels), "an experiment key is not a limit"
    assert "never@copied.example" not in json.dumps(limits.model_dump())


def test_claude_limits_without_a_cache_are_a_state(tmp_path: Path) -> None:
    (tmp_path / ".claude.json").write_text(json.dumps({"oauthAccount": {"billingType": "stripe_subscription"}}))
    limits = claude_limits(tmp_path / ".claude.json")
    assert limits.status == "unavailable" and limits.plan == "stripe_subscription"
    assert "not cached a utilization reading" in (limits.reason or "")


def test_cursor_reports_lines_and_plan_and_says_tokens_live_elsewhere(tmp_path: Path) -> None:
    db = tmp_path / "state.vscdb"
    with sqlite3.connect(db) as connection:
        connection.execute("create table ItemTable (key text primary key, value blob)")
        connection.executemany(
            "insert into ItemTable values (?, ?)",
            [
                ("aiCodeTracking.dailyStats.v1.5.2026-09-13", json.dumps({"date": "2026-09-13", "tabSuggestedLines": 0, "tabAcceptedLines": 0, "composerSuggestedLines": 169, "composerAcceptedLines": 131})),
                ("aiCodeTracking.dailyStats.v1.5.2026-08-30", json.dumps({"date": "2026-08-30", "tabSuggestedLines": 3, "tabAcceptedLines": 1, "composerSuggestedLines": 0, "composerAcceptedLines": 2842})),
                ("cursorAuth/stripeMembershipType", "ultra"),
                ("cursorAuth/cachedEmail", "never@copied.example"),
            ],
        )
    report = cursor_report(start=dt.datetime(2026, 9, 1, tzinfo=UTC), end=dt.datetime(2026, 10, 1, tzinfo=UTC), tz_offset_minutes=0, db_path=db)
    UsageReport.model_validate(report.model_dump())
    assert report.metrics.model_dump() == {"tokens": False, "requests": False, "cost": False, "lines": True, "subagents": False}
    assert [row.key for row in report.by_day] == ["2026-09-13"]
    assert report.by_day[0].extra["accepted_lines"] == 131 and report.totals.extra["suggested_lines"] == 169
    assert report.limits.plan == "ultra" and report.limits.status == "unavailable"
    assert "cursor.com" in (report.cost.reason or "")
    assert "never@copied.example" not in json.dumps(report.model_dump())
    missing = cursor_report(start=dt.datetime(2026, 9, 1, tzinfo=UTC), end=dt.datetime(2026, 10, 1, tzinfo=UTC), tz_offset_minutes=0, db_path=tmp_path / "absent.vscdb")
    assert missing.source.complete is False and any("not found" in note for note in missing.source.notes)


def test_vscode_says_exactly_what_is_missing() -> None:
    report = vscode_report(start=dt.datetime(2026, 9, 1, tzinfo=UTC), end=dt.datetime(2026, 10, 1, tzinfo=UTC), tz_offset_minutes=0, extension_detected=False)
    UsageReport.model_validate(report.model_dump())
    assert report.source.kind == "none"
    assert "not installed" in (report.cost.reason or "") and report.by_day == []
    assert report.metrics.model_dump() == {"tokens": False, "requests": False, "cost": False, "lines": False, "subagents": False}


def test_build_report_rejects_a_bad_provider_and_range() -> None:
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(usage_report.build_report("copilot", start=dt.datetime(2026, 9, 1, tzinfo=UTC), end=dt.datetime(2026, 9, 2, tzinfo=UTC), refresh=False, tz_offset_minutes=0))
    with pytest.raises(ValueError):
        asyncio.run(usage_report.build_report("cursor", start=dt.datetime(2026, 9, 2, tzinfo=UTC), end=dt.datetime(2026, 9, 1, tzinfo=UTC), refresh=False, tz_offset_minutes=0))


def test_helper_process_module_stays_light() -> None:
    """The index helper imports the store; the store's new import must stay stdlib-only."""
    import app.services.coding_sessions.claude_usage as module

    assert not any(name.startswith("app.") for name in vars(module) if name != "__name__"), vars(module).keys()
    assert store_module.DEFAULT_USAGE_BYTE_BUDGET > 0
