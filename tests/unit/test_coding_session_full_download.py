"""Full Download's this-computer half finds the provider's own file and nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.coding_sessions import raw_transcript

SESSION = "0f0e0d0c-aaaa-4bbb-8ccc-123456789abc"


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    claude = tmp_path / "claude" / "projects"
    (claude / "-Users-x-repo" / SESSION / "subagents").mkdir(parents=True)
    (claude / "-Users-x-repo" / f"{SESSION}.jsonl").write_text('{"type":"user"}\n')
    (claude / "-Users-x-repo" / SESSION / "subagents" / "agent-1.jsonl").write_text("{}\n")
    codex = tmp_path / "codex" / "sessions" / "2026" / "09" / "28"
    codex.mkdir(parents=True)
    (codex / f"rollout-2026-09-28T10-00-00-{SESSION}.jsonl").write_text('{"type":"x"}\n')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return tmp_path


def test_claude_main_file_and_subagent_streams_are_found(homes: Path) -> None:
    located = raw_transcript.locate("claude_code", SESSION)
    assert located is not None
    assert located.main.name == f"{SESSION}.jsonl"
    assert [p.name for p in located.sidechains] == ["agent-1.jsonl"]


def test_codex_rollout_is_found_by_thread_id(homes: Path) -> None:
    located = raw_transcript.locate("codex", SESSION)
    assert located is not None and located.main.name.endswith(f"{SESSION}.jsonl")


def test_a_path_hint_outside_the_provider_root_is_ignored(homes: Path) -> None:
    outside = homes / "secrets.jsonl"
    outside.write_text("private")
    located = raw_transcript.locate("claude_code", SESSION, str(outside))
    assert located is not None and located.main != outside


def test_an_unsafe_session_id_finds_nothing(homes: Path) -> None:
    assert raw_transcript.locate("claude_code", "../../etc/passwd") is None


def test_missing_session_is_none_and_save_copies_into_downloads(homes: Path) -> None:
    assert raw_transcript.locate("claude_code", "ffffffff-0000-4000-8000-000000000000") is None
    located = raw_transcript.locate("claude_code", SESSION)
    assert located is not None
    saved = raw_transcript.save_to_downloads(located, "claude_code", SESSION)
    assert Path(saved["saved_path"]).read_text() == '{"type":"user"}\n'
    assert Path(saved["saved_path"]).parent == homes / "home" / "Downloads"
    assert saved["subagent_streams"] == 1
    again = raw_transcript.save_to_downloads(located, "claude_code", SESSION)
    assert again["saved_path"] != saved["saved_path"]
