"""The session-index refresh must cross a process boundary, intact and inert.

Claude's index is 67,224 record files and 3.1 GB on this Mac (2026-09-15). The
refresh is incremental — it re-reads only what changed — but the first build
still reads all of it, and that read used to run inside the engine on a thread.
On 2026-09-13 the browser pool's 30 s launch bound expired in exactly that
window, at the same millisecond as the warm-up finished, and the Dashboard told
the user "Chromium is installed but would not start" while the very same
browser launched in 3.7 s from a terminal. A helper process takes that GIL,
allocator and disk pressure out of the engine entirely.

These pin what makes it a fix:

  1. The helper really is spawned the way production spawns it (the dev-mode
     ``run.py`` bootstrap), and the index it writes is EQUAL to the in-process
     full scan — entries, titles, paths, totals. A faster refresh that loses a
     conversation would be a worse bug than the one it replaces.
  2. It stays inert: no engine, no port, no discovery file, no home directory
     written into the world it was pointed at (Hard Rule 9).
  3. A framed payload survives unrelated chatter on the child's stdout.
  4. A broken helper falls back loudly, and the index still arrives.
"""

from __future__ import annotations

import asyncio
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
from app.services.coding_sessions.claude_index_store import ClaudeIndexStore
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


def _run_helper(
    root: Path, home: Path, store_path: Path
) -> subprocess.CompletedProcess[bytes]:
    """Spawn the helper EXACTLY as the engine does in a source run."""
    command = helper_command(root, store_path)
    assert command[1].endswith("run.py"), command
    assert command[2] == HELPER_ARGUMENT
    env = os.environ.copy()
    env["MATRX_HOME_DIR"] = str(home)
    return subprocess.run(command, capture_output=True, env=env, timeout=120)


def test_helper_writes_an_index_equal_to_the_full_scan(sessions_root, tmp_path):
    home = tmp_path / "matrx-home"
    store_path = tmp_path / "store" / "index.sqlite3"
    completed = _run_helper(sessions_root, home, store_path)

    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    result = decode_payload(completed.stdout)
    assert result["files"] == 3
    assert result["conversations"] == 2
    assert result["changed"] == 3

    snapshot = ClaudeIndexStore(store_path).load(record_paths=True)
    expected_entries, expected_totals = read_session_index(sessions_root)
    assert snapshot.entries == expected_entries
    assert snapshot.totals == expected_totals
    assert snapshot.complete is True
    # Not just equal dicts: the real frozen dataclass, with its local-only
    # fields — the freshest record won and its paths came with it.
    winner = snapshot.entries["11111111-1111-4111-8111-111111111111"]
    assert winner.title == "Fix the browser pool"
    assert winner.workspace_name == "matrx-local"
    assert winner.local_cwd == Path("/Users/someone/code/matrx-local")
    assert len(winner.record_paths) == 2
    assert all(isinstance(path, Path) for path in winner.record_paths)


def test_a_second_run_rereads_only_what_changed(sessions_root, tmp_path):
    """The whole point: 67,224 files stay unread when nothing moved."""
    home = tmp_path / "matrx-home"
    store_path = tmp_path / "store" / "index.sqlite3"
    assert decode_payload(_run_helper(sessions_root, home, store_path).stdout)["changed"] == 3

    again = decode_payload(_run_helper(sessions_root, home, store_path).stdout)
    assert again["changed"] == 0, "an unchanged tree must cost zero record reads"
    assert again["files"] == 3
    assert again["conversations"] == 2

    _write_record(
        sessions_root,
        "account-a",
        "org-1",
        {
            "cliSessionId": "33333333-3333-4333-8333-333333333333",
            "title": "A new conversation",
            "cwd": "/Users/someone/code/aidream",
            "lastActivityAt": 1_757_800_000_000,
        },
    )
    third = decode_payload(_run_helper(sessions_root, home, store_path).stdout)
    assert third["changed"] == 1, "only the new record may be read"
    assert third["conversations"] == 3


def test_a_deleted_record_leaves_the_index(sessions_root, tmp_path):
    home = tmp_path / "matrx-home"
    store_path = tmp_path / "store" / "index.sqlite3"
    _run_helper(sessions_root, home, store_path)
    (
        sessions_root
        / "account-b"
        / "org-2"
        / "local_22222222-2222-4222-8222-222222222222.json"
    ).unlink()

    result = decode_payload(_run_helper(sessions_root, home, store_path).stdout)
    assert result["removed"] == 1
    assert result["conversations"] == 1
    snapshot = ClaudeIndexStore(store_path).load()
    assert "22222222-2222-4222-8222-222222222222" not in snapshot.entries


def test_helper_boots_no_engine_and_writes_nothing(sessions_root, tmp_path):
    """A refresh is a refresh: no second engine, no port, no home directory."""
    home = tmp_path / "matrx-home"
    completed = _run_helper(sessions_root, home, tmp_path / "store" / "index.sqlite3")

    assert completed.returncode == 0
    assert not home.exists(), sorted(p.name for p in home.iterdir())
    assert b"Traceback" not in completed.stderr


def test_missing_root_is_an_empty_result_not_a_crash(tmp_path):
    home = tmp_path / "matrx-home"
    store_path = tmp_path / "store" / "index.sqlite3"
    completed = _run_helper(tmp_path / "nothing-here", home, store_path)

    assert completed.returncode == 0
    assert decode_payload(completed.stdout)["files"] == 0
    assert ClaudeIndexStore(store_path).load().entries == {}


def test_helper_without_a_store_path_fails_loudly(tmp_path):
    completed = subprocess.run(
        helper_command(tmp_path, tmp_path / "store.sqlite3")[:-1],  # no store path
        capture_output=True,
        timeout=120,
    )
    assert completed.returncode == 1
    assert b"claude index helper failed" in completed.stderr


def test_framed_payload_survives_chatter_on_the_pipe():
    """A warning printed by some import must never corrupt the result."""
    payload = encode_payload({"files": 1, "changed": 1})
    noisy = b"WARNING: something printed to stdout\n" + payload + b"\ntrailing\n"

    assert decode_payload(noisy) == {"files": 1, "changed": 1}


def test_a_truncated_payload_is_refused_rather_than_half_read():
    payload = encode_payload({"files": 1})

    with pytest.raises(ValueError):
        decode_payload(payload[:-5])
    with pytest.raises(ValueError):
        decode_payload(b"no payload at all")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated_store(tmp_path, monkeypatch) -> ClaudeIndexStore:
    """Point the overview module at a throwaway store for this test."""
    from app.services.coding_sessions import claude_overview

    store = ClaudeIndexStore(tmp_path / "store" / "index.sqlite3")
    claude_overview._reset_index_state_for_tests(store)
    monkeypatch.setattr(
        claude_overview, "_refresh_transcripts", _no_transcript_refresh
    )
    yield store
    claude_overview._reset_index_state_for_tests(None)


async def _no_transcript_refresh(_store):
    return {}


@pytest.mark.anyio
async def test_the_engine_refreshes_in_the_subprocess_not_in_itself(
    sessions_root, isolated_store, monkeypatch
):
    """The production refresh must spawn the helper, not fall back to threads."""
    from app.services.coding_sessions import claude_overview

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError(
            "the in-engine refresh ran — the helper path was skipped, and that is "
            "the GIL-holding read that starved the event loop"
        )

    monkeypatch.setattr(claude_overview, "_refresh_in_threads", _must_not_run)

    await claude_overview.refresh_index(sessions_root)
    full = await claude_overview.index_snapshot(record_paths=True)

    expected_entries, expected_totals = read_session_index(sessions_root)
    assert full.entries == expected_entries
    assert full.totals == expected_totals

    # A second load of an unchanged store is the in-memory snapshot, not a
    # second read of every row.
    snapshot = await claude_overview.index_snapshot()
    again = await claude_overview.index_snapshot()
    assert again is snapshot


@pytest.mark.anyio
async def test_a_broken_helper_falls_back_loudly_instead_of_losing_the_index(
    sessions_root, isolated_store, monkeypatch
):
    """Nothing fails silently: the fallback still answers, and it says why."""
    import io
    import logging

    from app.services.coding_sessions import claude_overview

    monkeypatch.setattr(
        claude_overview.claude_index_helper,
        "helper_command",
        lambda root, store_path: ["/nonexistent/matrx-helper", str(root)],
    )

    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    claude_overview.logger.logger.addHandler(handler)
    try:
        await claude_overview.refresh_index(sessions_root)
    finally:
        claude_overview.logger.logger.removeHandler(handler)

    snapshot = await claude_overview.index_snapshot(record_paths=True)
    expected_entries, expected_totals = read_session_index(sessions_root)
    assert snapshot.entries == expected_entries
    assert snapshot.totals == expected_totals
    logged = captured.getvalue()
    assert "Session-index helper process unavailable" in logged
    # A stand-in that does not name its remedy is a silent failure with extra
    # words (CLAUDE.md § nothing fails silently).
    assert "remedy" in logged.lower()


@pytest.mark.anyio
async def test_cancelled_refresh_reaps_its_child(
    sessions_root: Path, isolated_store: ClaudeIndexStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping the engine must not leave a Claude index helper behind."""
    from app.services.coding_sessions import claude_overview

    entered = asyncio.Event()

    class HangingProcess:
        returncode: int | None = None

        async def communicate(self):
            entered.set()
            await asyncio.Event().wait()
            return b"", b""

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            assert self.returncode is not None
            return self.returncode

    process = HangingProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    task = asyncio.create_task(
        claude_overview._refresh_in_helper(sessions_root, isolated_store)
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode == -15
