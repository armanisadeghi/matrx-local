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
