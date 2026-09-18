"""Zero-authorship verification of CS-33, landed as the standing guards.

Written by a VERIFIER, not by the builder, and landed verbatim by CS-33/R2:
every assertion and every comment below is the verifier's. Deliberately NOT
reusing the builder's fixtures or helpers — every tree here is built from
scratch so a fixture that encodes the builder's assumption cannot launder it.

Two of these were RED when they were written, and each named a real defect:

* ``test_both_readers_agree_when_the_stated_org_has_no_focus_stamp`` — the two
  readers of THE one scope rule enumerated organisations differently, so one
  machine produced two different sidebars (Defect A);
* ``test_iso_stamp_with_fractional_seconds_is_not_ranked_below_a_whole_second``
  — the app's ISO stamps were ordered as strings, so ``.500Z`` sorted below
  ``Z`` (Defect B.ii).

The verifier's third red test is about ``index_writable`` in
``title_sync.py``, which is a different owner's defect; it lives on its own in
``test_claude_index_writable_verdict.py`` so the two are never edited
together.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from app.services.coding_sessions.claude_index_store import (
    ClaudeIndexStore,
    refresh_store_sync,
    refresh_transcripts_sync,
    walk_transcripts,
)
from app.services.coding_sessions.claude_scope import (
    decide_scope,
    resolve_active_scope,
)
from app.services.coding_sessions.usage_report import (
    UsageLimits,
    claude_report,
)

ACCOUNT = "acct-aaaaaaaa"
ORG_A = "org-aaaaaaaa"
ORG_B = "org-bbbbbbbb"
ORG_C = "org-cccccccc"


# ── helpers (mine, not the builder's) ───────────────────────────────────────


def _record(session: str, *, focus: int | None, starred: bool) -> dict:
    record = {
        "sessionId": session,
        "cliSessionId": session.replace("local_", ""),
        "isStarred": starred,
        "title": "t",
    }
    if focus is not None:
        record["lastFocusedAt"] = focus
    return record


def _write_record(root: Path, account: str, org: str, session: str, **kw) -> None:
    folder = root / account / org
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session}.json").write_text(json.dumps(_record(session, **kw)))


def _state(support: Path, *, account: str, org_stamps: dict[str, str] | None = None) -> None:
    support.mkdir(parents=True, exist_ok=True)
    config: dict[str, object] = {"lastKnownAccountUuid": account}
    for org, stamp in (org_stamps or {}).items():
        config[f"dxt:allowlistLastUpdated:{org}"] = stamp
    (support / "config.json").write_text(json.dumps(config))
    (support / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": account})
    )


def _engine_scope(tmp_path: Path, root: Path) -> tuple[str | None, str]:
    """What the ENGINE (persisted SQLite index) concludes, in its own words."""
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    refresh_store_sync(root, store)
    snap = store.load()
    return snap.active_scope, snap.active_scope_reason or ""


# ── CLAIM 1: the account + the app's own org statement decide the scope ─────


def test_equal_focus_stamps_across_orgs_with_no_statement_is_unknown(tmp_path: Path) -> None:
    """Plant the measured coin flip: identical stamps, different star counts."""
    support = tmp_path / "Claude"
    root = support / "claude-code-sessions"
    stamp = 1789718916078
    for org in (ORG_A, ORG_B, ORG_C):
        _write_record(root, ACCOUNT, org, "local_s1", focus=stamp, starred=(org == ORG_C))
    _state(support, account=ACCOUNT)  # the app names NO organisation

    fs = resolve_active_scope(root, app_support=support)
    assert fs.scope is None, f"coin flip: filesystem reader picked {fs.scope}"
    assert "UNKNOWN" in fs.reason and str(stamp) in fs.reason

    engine_scope, engine_reason = _engine_scope(tmp_path, root)
    assert engine_scope is None, f"coin flip: engine picked {engine_scope}"
    assert "UNKNOWN" in engine_reason


def test_stated_org_beats_a_newer_focus_stamp(tmp_path: Path) -> None:
    """lastFocusedAt must never outvote the app's own statement."""
    support = tmp_path / "Claude"
    root = support / "claude-code-sessions"
    _write_record(root, ACCOUNT, ORG_A, "local_a", focus=9_000_000_000, starred=False)
    _write_record(root, ACCOUNT, ORG_B, "local_b", focus=1, starred=True)
    _state(support, account=ACCOUNT, org_stamps={
        ORG_A: "2026-09-01T00:00:00Z",
        ORG_B: "2026-09-18T08:33:08Z",
    })

    fs = resolve_active_scope(root, app_support=support)
    assert fs.org == ORG_B, fs.reason
    engine_scope, engine_reason = _engine_scope(tmp_path, root)
    assert engine_scope is not None and engine_scope.endswith(ORG_B), engine_reason


def test_both_readers_agree_when_the_stated_org_has_no_focus_stamp(tmp_path: Path) -> None:
    """THE two-gatherer invariant the module docstring promises.

    The extractor lists org DIRECTORIES; the engine lists records whose
    ``lastrecord_focused_at > 0``. An org the app NAMES whose records carry no
    focus stamp is therefore visible to one gatherer and invisible to the
    other, and ``decide_scope`` only honours a statement about an org it can
    see. If they drift, the engine and the ledger publish different sidebars.
    """
    support = tmp_path / "Claude"
    root = support / "claude-code-sessions"
    _write_record(root, ACCOUNT, ORG_A, "local_a", focus=5_000, starred=False)
    _write_record(root, ACCOUNT, ORG_B, "local_b", focus=None, starred=True)
    _state(support, account=ACCOUNT, org_stamps={ORG_B: "2026-09-18T08:33:08Z"})

    fs = resolve_active_scope(root, app_support=support)
    engine_scope, engine_reason = _engine_scope(tmp_path, root)
    engine_org = Path(engine_scope).name if engine_scope else None
    assert fs.org == engine_org, (
        "the two readers of THE one rule disagree: extractor says "
        f"{fs.org!r} ({fs.reason}); engine says {engine_org!r} ({engine_reason})"
    )


def test_disagreeing_account_signals_are_unknown_not_a_majority(tmp_path: Path) -> None:
    support = tmp_path / "Claude"
    root = support / "claude-code-sessions"
    _write_record(root, ACCOUNT, ORG_A, "local_a", focus=5, starred=True)
    _state(support, account=ACCOUNT)
    (support / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": "acct-zzzzzzzz"})
    )
    fs = resolve_active_scope(root, app_support=support)
    assert fs.scope is None and "disagree" in fs.reason


def test_stated_org_tie_is_unknown_even_with_distinct_focus(tmp_path: Path) -> None:
    resolution = decide_scope(
        ACCOUNT,
        {"config.json:lastKnownAccountUuid": ACCOUNT},
        "stated",
        {ORG_A: 10, ORG_B: 20},
        org_stated={ORG_A: "2026-09-18T08:00:00Z", ORG_B: "2026-09-18T08:00:00Z"},
        account_dir=Path("/x") / ACCOUNT,
    )
    assert resolution.scope is None and "UNKNOWN" in resolution.reason


def test_iso_stamp_with_fractional_seconds_is_not_ranked_below_a_whole_second(tmp_path: Path) -> None:
    """String ordering of ISO stamps: ``.123Z`` sorts BEFORE ``Z``.

    ``stated_org_stamps`` accepts both forms and ``decide_scope`` compares them
    as strings, so a later fractional stamp loses to an earlier whole-second
    one in the same second.
    """
    later = "2026-09-18T08:33:08.500Z"   # half a second LATER
    earlier = "2026-09-18T08:33:08Z"
    resolution = decide_scope(
        ACCOUNT,
        {},
        "stated",
        {ORG_A: 1, ORG_B: 1},
        org_stated={ORG_A: earlier, ORG_B: later},
        account_dir=Path("/x") / ACCOUNT,
    )
    assert resolution.org == ORG_B, (
        "string comparison of ISO stamps put the earlier whole-second stamp "
        f"ahead of the later fractional one: picked {resolution.org!r}"
    )


# ── CLAIM 2: every sub-agent transcript, once per session ───────────────────


def _assistant(msg_id: str, req: str, hour: str, model: str, tokens: tuple[int, int, int, int]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "requestId": req,
            "uuid": f"u-{msg_id}-{req}",
            "timestamp": f"{hour}:00:00.000Z",
            "message": {
                "id": msg_id,
                "model": model,
                "usage": {
                    "input_tokens": tokens[0],
                    "output_tokens": tokens[1],
                    "cache_creation_input_tokens": tokens[2],
                    "cache_read_input_tokens": tokens[3],
                },
            },
        },
        separators=(",", ":"),
    )


SESSION = "11111111-2222-3333-4444-555555555555"
OTHER_SESSION = "66666666-7777-8888-9999-aaaaaaaaaaaa"
T = (10, 20, 30, 40)  # 100 tokens per counted turn
MODEL = "claude-opus-5"


def _transcript_tree(root: Path) -> None:
    project = root / "-Users-x-proj"
    project.mkdir(parents=True, exist_ok=True)
    # Main: one real turn, plus the SAME message streamed as two blocks.
    (project / f"{SESSION}.jsonl").write_text(
        "\n".join(
            [
                _assistant("msg-main-1", "req-1", "2026-09-18T10", MODEL, T),
                _assistant("msg-main-1", "req-1", "2026-09-18T10", MODEL, T),  # streamed dup
                _assistant("msg-main-2", "req-2", "2026-09-18T11", MODEL, T),
            ]
        )
        + "\n"
    )
    # A flat sub-agent stream, whose second line repeats the PARENT's message —
    # Claude mirrors a parent turn into the sidechain file.
    subagents = project / SESSION / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "agent-aaaa1111.jsonl").write_text(
        "\n".join(
            [
                _assistant("msg-sub-1", "req-3", "2026-09-18T10", MODEL, T),
                _assistant("msg-main-2", "req-2", "2026-09-18T11", MODEL, T),  # parent's turn
            ]
        )
        + "\n"
    )
    # A NESTED workflow sub-agent stream, one level deeper again.
    wf = subagents / "workflows" / "wf_deadbeef"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "agent-bbbb2222.jsonl").write_text(
        _assistant("msg-sub-2", "req-4", "2026-09-18T12", MODEL, T) + "\n"
    )
    # Plugin state Claude keeps beside the transcripts must stay out.
    plugin = project / "vercel-plugin"
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "skill-injections.jsonl").write_text(
        _assistant("msg-plugin", "req-9", "2026-09-18T10", MODEL, (999, 999, 999, 999)) + "\n"
    )
    # A second session, so per-session dedupe cannot be per-tree dedupe.
    (project / f"{OTHER_SESSION}.jsonl").write_text(
        _assistant("msg-main-1", "req-1", "2026-09-18T10", MODEL, T) + "\n"
    )


def _cells(tmp_path: Path, root: Path) -> tuple[list[dict], ClaudeIndexStore]:
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    return store.usage_rows("2026-09-18T00", "2026-09-18T23"), store


def test_the_walk_reaches_nested_workflow_streams_and_excludes_plugin_state(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _transcript_tree(root)
    found = walk_transcripts(root)
    ids = sorted(found)
    assert any("workflows/wf_deadbeef/agent-bbbb2222.jsonl" in i for i in ids), ids
    assert not any("vercel-plugin" in i for i in ids), ids
    lanes = {i: f.lane for i, f in found.items()}
    assert lanes[f"-Users-x-proj/{SESSION}.jsonl"] == "main"
    for i, lane in lanes.items():
        if SESSION in i and i.endswith("agent-bbbb2222.jsonl"):
            assert lane == "subagent"
            assert found[i].session_id == SESSION


def test_a_turn_is_counted_once_per_session_across_its_transcripts(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _transcript_tree(root)
    cells, _ = _cells(tmp_path, root)
    by_session: dict[str, int] = {}
    for cell in cells:
        by_session[cell["session_id"]] = by_session.get(cell["session_id"], 0) + int(cell["requests"])
    # SESSION's distinct keys: msg-main-1, msg-main-2, msg-sub-1, msg-sub-2 = 4.
    # (the streamed dup and the parent turn mirrored into the sub-agent file
    # must NOT add a fifth)
    assert by_session.get(SESSION) == 4, by_session
    # The other session shares msg-main-1|req-1 and must still be counted:
    # dedupe is per SESSION, never per machine.
    assert by_session.get(OTHER_SESSION) == 1, by_session


def test_main_plus_subagent_equals_the_total_on_every_grouping(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _transcript_tree(root)
    cells, store = _cells(tmp_path, root)
    report = claude_report(
        cells,
        start=dt.datetime(2026, 9, 18, tzinfo=dt.timezone.utc),
        end=dt.datetime(2026, 9, 19, tzinfo=dt.timezone.utc),
        tz_offset_minutes=0,
        status=store.usage_status(),
        prices={},
        limits=UsageLimits(status="unavailable", reason="test"),
    )
    groupings = {
        "totals": [report.totals],
        "by_day": report.by_day,
        "by_model": report.by_model,
        "by_session": report.by_session,
        "by_project": report.by_project,
    }
    for name, rows in groupings.items():
        assert rows, f"{name} is empty — nothing was measured"
        for row in rows:
            assert row.main_requests + row.subagent_requests == row.requests, (
                f"{name}/{row.key}: {row.main_requests}+{row.subagent_requests}"
                f" != {row.requests}"
            )
            assert row.main_total_tokens + row.subagent_total_tokens == row.total_tokens, (
                f"{name}/{row.key}: tokens split does not add up"
            )
    # And the sub-agent share is actually non-zero, or this proves nothing.
    assert report.totals.subagent_requests > 0, "no sub-agent spend was counted at all"
    assert report.totals.requests == 5, report.totals  # 4 + 1
    assert report.totals.total_tokens == 500, report.totals


def test_a_rewritten_subagent_transcript_drops_only_its_own_keys(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    _transcript_tree(root)
    store = ClaudeIndexStore(tmp_path / "index.sqlite3")
    refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    before = store.usage_keys(SESSION)
    assert len(before) == 4, before
    # Shrink the flat sub-agent stream to a DIFFERENT single turn.
    target = root / "-Users-x-proj" / SESSION / "subagents" / "agent-aaaa1111.jsonl"
    target.write_text(_assistant("msg-sub-9", "req-99", "2026-09-18T10", MODEL, T) + "\n")
    refresh_transcripts_sync(store, sidebar_ids=set(), root=root)
    after = store.usage_keys(SESSION)
    assert "msg-sub-9|req-99" in after, after
    assert "msg-sub-1|req-3" not in after, "a rewritten file kept a key it no longer holds"
    assert "msg-main-1|req-1" in after, "a rewrite took a SIBLING transcript's key with it"
