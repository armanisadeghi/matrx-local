"""Every transcript on disk is on the screen — including the ones Claude never indexed.

The 2026-09-11 defect: the overview iterated Claude's sidebar index, so a
session started from the plain `claude` CLI (which never gets an index record)
was invisible — 80 of them, 113 MB, growing. The importer already reached them;
only the screen hid them. This test builds a tiny real tree — one indexed
session, one CLI-only transcript — and fails if either is missing, if the
CLI-only row is not marked, or if its title is a placeholder when the
transcript plainly opens with a human message.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _write_transcript(projects: Path, project_slug: str, session_id: str, first_prompt: str) -> None:
    folder = projects / project_slug
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "isMeta": False, "cwd": "/Users/x/code/demo",
         "message": {"role": "user", "content": first_prompt}},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "Sure."}]}},
    ]
    (folder / f"{session_id}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines)
    )


def _write_index_record(sessions_root: Path, session_id: str, title: str) -> None:
    scope = sessions_root / "acct-1" / "org-1"
    scope.mkdir(parents=True, exist_ok=True)
    (scope / f"local_{session_id}.json").write_text(json.dumps({
        "sessionId": session_id,
        "cliSessionId": session_id,
        "title": title,
        "titleSource": "user",
        "cwd": "/Users/x/code/demo",
        "isArchived": False,
        "lastActivityAt": 1_788_868_800_000,
    }))


@pytest.fixture()
def claude_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    config_dir = tmp_path / "claude"
    projects = config_dir / "projects"
    sessions_root = tmp_path / "desktop-sessions"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("CLAUDE_DESKTOP_SESSIONS_DIR", str(sessions_root))

    indexed = "11111111-1111-4111-8111-111111111111"
    cli_only = "22222222-2222-4222-8222-222222222222"
    _write_transcript(projects, "-Users-x-code-demo", indexed, "Build the thing")
    _write_index_record(sessions_root, indexed, "Build the thing (renamed)")
    _write_transcript(projects, "-Users-x-code-demo", cli_only,
                      "Please audit the billing module for double charges")

    # Fresh tree, fresh cache: the fingerprint differs from any earlier run.
    import app.services.coding_sessions.claude_overview as overview_module
    monkeypatch.setattr(overview_module, "_INDEX_CACHE", None)
    return {"indexed": indexed, "cli_only": cli_only}


def _run_overview(monkeypatch: pytest.MonkeyPatch) -> dict:
    import app.services.coding_sessions.claude_overview as overview_module

    # No cloud, no queue: this test is about what is LISTED, not its state.
    async def _no_cloud():
        return {}, {"checked": False, "reason": "test"}

    async def _no_queue():
        return {}

    async def _no_totals():
        return 0, 0

    monkeypatch.setattr(overview_module, "cloud_inventory", _no_cloud)
    monkeypatch.setattr(overview_module, "_queue_by_session", _no_queue)
    monkeypatch.setattr(overview_module, "_queue_totals", _no_totals)
    return asyncio.run(overview_module.overview())


def test_every_transcript_on_disk_is_listed(claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    out = _run_overview(monkeypatch)
    listed = {row["session_id"]: row for row in out["conversations"]}

    assert claude_tree["indexed"] in listed
    assert claude_tree["cli_only"] in listed, "a CLI-only transcript must never be hidden"

    assert out["totals"]["transcripts_on_disk"] == 2
    assert out["totals"]["transcript_only"] == 1
    assert out["totals"]["conversations"] == 2


def test_cli_only_rows_are_marked_and_titled_by_their_opening_message(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run_overview(monkeypatch)
    listed = {row["session_id"]: row for row in out["conversations"]}

    indexed = listed[claude_tree["indexed"]]
    assert indexed["in_claude_sidebar"] is True
    assert indexed["title"] == "Build the thing (renamed)"  # the sidebar label wins

    cli_only = listed[claude_tree["cli_only"]]
    assert cli_only["in_claude_sidebar"] is False
    assert cli_only["title"] == "Please audit the billing module for double charges"
    assert cli_only["project"] == "demo"
    assert cli_only["on_disk"] is True
    assert cli_only["pinned"] is False


def test_warm_index_cache_fills_the_cache_so_the_first_screen_open_is_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine warms the index at startup; the first open must not re-parse."""
    import app.services.coding_sessions.claude_overview as claude_overview

    root = tmp_path / "claude-code-sessions"
    _write_index_record(root, "11111111-1111-4111-8111-111111111111", "One")
    _write_index_record(root, "22222222-2222-4222-8222-222222222222", "Two")
    monkeypatch.setattr(claude_overview, "_INDEX_CACHE", None)
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(tmp_path / "absent-ledger.json"))

    asyncio.run(claude_overview.warm_index_cache(root))

    cached = claude_overview._INDEX_CACHE
    assert cached is not None, "warm_index_cache left the cache cold"
    fingerprint, entries, totals = cached
    assert fingerprint[0] == 2
    assert totals["files"] == 2
    assert not totals.get("truncated")
    assert {entry.title for entry in entries.values()} == {"One", "Two"}

    # The warmed cache is what the screen's own read returns — the very same
    # objects. Re-parsing would build new dicts; identity is the proof.
    screen_entries, screen_totals = claude_overview._session_index(root)
    assert screen_entries is entries
    assert screen_totals is totals


def test_a_cli_only_session_opens_a_diagnosis_instead_of_a_404(
    claude_tree: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every listed row must open. A CLI-only row used to 404 because the
    diagnosis looked the session up in the sidebar index alone."""
    import app.services.coding_sessions.claude_overview as overview_module

    async def _no_cloud():
        return {}, {"checked": False, "reason": "test"}

    async def _none(*_a, **_k):
        return []

    async def _empty(*_a, **_k):
        return {}

    monkeypatch.setattr(overview_module, "cloud_inventory", _no_cloud)
    monkeypatch.setattr(overview_module, "_session_envelopes", _none)
    monkeypatch.setattr(overview_module, "_capture_facts", _empty)
    monkeypatch.setattr(overview_module, "_label_facts", _empty)
    monkeypatch.setattr(overview_module, "_delivered_by_this_mac", _empty)

    class _Outbox:
        publisher_blocker = None

    import app.services.coding_sessions.service as service_module
    monkeypatch.setattr(service_module, "get_coding_session_bridge_outbox", lambda: _Outbox())

    out = asyncio.run(overview_module.session_diagnosis(claude_tree["cli_only"]))
    assert out is not None, "a listed CLI-only session must open a diagnosis"
    assert out["index"]["in_claude_sidebar"] is False
    assert out["index"]["title"] == "Please audit the billing module for double charges"
    assert out["index"]["accounts"] == []  # the dialog's row must not crash
    assert out["transcript"]["on_disk"] is True

    # And a session that exists nowhere is still unknown.
    missing = asyncio.run(overview_module.session_diagnosis("99999999-9999-4999-8999-999999999999"))
    assert missing is None
