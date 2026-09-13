"""The session-index scan must cross a process boundary, intact and inert.

Reading Claude's index is ~63,000 ``json.loads`` calls — about a minute of work
that used to run inside the engine on a thread. On 2026-09-13 the browser pool's
30s launch bound expired in exactly that window, at the same millisecond as the
warm-up finished, and the Dashboard told the user "Chromium is installed but
would not start" while the very same browser launched in 3.7s from a terminal.
A helper process takes that minute of GIL, allocator and disk pressure out of
the engine entirely.

The fix is a short-lived helper process. These pin what makes it a fix:

  1. The helper really is spawned the way production spawns it (the dev-mode
     ``run.py`` bootstrap), and its result is EQUAL to the in-process read —
     entries, titles, paths, totals. A faster scan that loses a conversation
     would be a worse bug than the one it replaces.
  2. It stays inert: no engine, no port, no discovery file, no home directory
     written into the world it was pointed at (Hard Rule 9).
  3. A framed payload survives unrelated chatter on the child's stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.common.claude_index_helper import (
    HELPER_ARGUMENT,
    decode_payload,
    encode_payload,
    helper_command,
)
from app.services.coding_sessions.claude_session_index import read_session_index


def _write_record(root: Path, account: str, org: str, record: dict) -> None:
    folder = root / account / org
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"local_{record['cliSessionId']}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )


@pytest.fixture
def sessions_root(tmp_path, monkeypatch) -> Path:
    """A real index tree: two accounts, one of them holding a stale duplicate."""
    root = tmp_path / "claude-code-sessions"
    _write_record(
        root,
        "account-a",
        "org-1",
        {
            "cliSessionId": "11111111-1111-4111-8111-111111111111",
            "title": "Fix the browser pool",
            "titleSource": "user",
            "cwd": "/Users/someone/code/matrx-local",
            "branch": "main",
            "isArchived": False,
            "lastActivityAt": 1_757_700_000_000,
        },
    )
    _write_record(
        root,
        "account-b",
        "org-2",
        {
            "cliSessionId": "11111111-1111-4111-8111-111111111111",
            "title": "Older copy of the same conversation",
            "titleSource": "auto",
            "cwd": "/Users/someone/code/matrx-local",
            "lastActivityAt": 1_757_600_000_000,
        },
    )
    _write_record(
        root,
        "account-b",
        "org-2",
        {
            "cliSessionId": "22222222-2222-4222-8222-222222222222",
            "title": "A second conversation",
            "cwd": "/Users/someone/code/aidream",
            "lastActivityAt": 1_757_690_000_000,
        },
    )
    # Both the child and this process must agree on the ledger, and neither may
    # read the developer's real ~/.claude while doing it.
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(tmp_path / "no-ledger.json"))
    return root


def _run_helper(root: Path, home: Path) -> subprocess.CompletedProcess[bytes]:
    """Spawn the helper EXACTLY as the engine does in a source run."""
    command = helper_command(root)
    assert command[1].endswith("run.py"), command
    assert command[2] == HELPER_ARGUMENT
    env = os.environ.copy()
    env["MATRX_HOME_DIR"] = str(home)
    return subprocess.run(command, capture_output=True, env=env, timeout=120)


def test_helper_round_trips_the_real_index(sessions_root, tmp_path):
    home = tmp_path / "matrx-home"
    completed = _run_helper(sessions_root, home)

    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    entries, totals = decode_payload(completed.stdout)
    expected_entries, expected_totals = read_session_index(sessions_root)

    assert totals == expected_totals
    assert totals["records"] == 2
    assert totals["files"] == 3
    assert entries == expected_entries
    # Not just equal dicts: the real frozen dataclass, with its local-only
    # fields, arrived — the freshest record won and its paths came with it.
    winner = entries["11111111-1111-4111-8111-111111111111"]
    assert winner.title == "Fix the browser pool"
    assert winner.workspace_name == "matrx-local"
    assert winner.local_cwd == Path("/Users/someone/code/matrx-local")
    assert len(winner.record_paths) == 2
    assert all(isinstance(path, Path) for path in winner.record_paths)


def test_helper_boots_no_engine_and_writes_nothing(sessions_root, tmp_path):
    """A scan is a scan: no second engine, no port, no home directory."""
    home = tmp_path / "matrx-home"
    completed = _run_helper(sessions_root, home)

    assert completed.returncode == 0
    assert not home.exists(), sorted(p.name for p in home.iterdir())
    assert b"Traceback" not in completed.stderr


def test_missing_root_is_an_empty_result_not_a_crash(tmp_path):
    home = tmp_path / "matrx-home"
    completed = _run_helper(tmp_path / "nothing-here", home)

    assert completed.returncode == 0
    entries, totals = decode_payload(completed.stdout)
    assert entries == {}
    assert totals["records"] == 0


def test_helper_without_a_root_fails_loudly(tmp_path):
    completed = subprocess.run(
        helper_command(tmp_path)[:-1],  # every argument except the root
        capture_output=True,
        timeout=120,
    )
    assert completed.returncode == 1
    assert b"claude index helper failed" in completed.stderr


def test_framed_payload_survives_chatter_on_the_pipe():
    """A warning printed by some import must never corrupt the result."""
    payload = encode_payload(({"a": 1}, {"records": 1}))
    noisy = b"WARNING: something printed to stdout\n" + payload + b"\ntrailing\n"

    assert decode_payload(noisy) == ({"a": 1}, {"records": 1})


def test_a_truncated_payload_is_refused_rather_than_half_read():
    payload = encode_payload(({"a": 1}, {"records": 1}))

    with pytest.raises(ValueError):
        decode_payload(payload[:-5])
    with pytest.raises(ValueError):
        decode_payload(b"no payload at all")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_the_engine_path_uses_the_subprocess_and_caches_it(
    sessions_root, monkeypatch
):
    """The production async read must spawn the helper, not fall back to a thread."""
    from app.services.coding_sessions import claude_overview

    def _must_not_run(_root):
        raise AssertionError(
            "the in-process scan ran — the helper path was skipped, and this is the "
            "GIL-holding read that starved the event loop"
        )

    monkeypatch.setattr(claude_overview, "_session_index", _must_not_run)
    monkeypatch.setattr(claude_overview, "_INDEX_CACHE", None, raising=False)

    entries, totals = await claude_overview._session_index_async(sessions_root)

    expected_entries, expected_totals = read_session_index(sessions_root)
    assert entries == expected_entries
    assert totals == expected_totals

    # Second read is the cache, not a second process.
    def _no_second_spawn(_root):
        raise AssertionError("the helper was spawned again for an unchanged tree")

    monkeypatch.setattr(claude_overview, "helper_command", _no_second_spawn, raising=False)
    monkeypatch.setattr(
        claude_overview.claude_index_helper, "helper_command", _no_second_spawn
    )
    again_entries, _ = await claude_overview._session_index_async(sessions_root)
    assert again_entries is entries


@pytest.mark.anyio
async def test_a_broken_helper_falls_back_loudly_instead_of_losing_the_index(
    sessions_root, monkeypatch
):
    """Nothing fails silently: the fallback still answers, and it says why."""
    import io
    import logging

    from app.services.coding_sessions import claude_overview

    monkeypatch.setattr(
        claude_overview.claude_index_helper,
        "helper_command",
        lambda root: ["/nonexistent/matrx-helper", str(root)],
    )
    monkeypatch.setattr(claude_overview, "_INDEX_CACHE", None, raising=False)

    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    claude_overview.logger.logger.addHandler(handler)
    try:
        entries, totals = await claude_overview._session_index_async(sessions_root)
    finally:
        claude_overview.logger.logger.removeHandler(handler)

    expected_entries, expected_totals = read_session_index(sessions_root)
    assert entries == expected_entries
    assert totals == expected_totals
    logged = captured.getvalue()
    assert "Session-index helper process unavailable" in logged
    # A stand-in that does not name its remedy is a silent failure with extra
    # words (CLAUDE.md § nothing fails silently).
    assert "Remedy" in logged
