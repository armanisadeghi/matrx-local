"""The LOCAL Claude Code runtime — allowlist, approval, resume truth, mirror.

These are forcing-function tests: the mirror test drives the REAL importer and
the REAL durable outbox schema against a synthetic Claude transcript, so it
only passes when a runtime-launched session genuinely becomes exact
`append_native` envelopes under the certified bridge identity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.services.coding_sessions.claude_history import (
    ClaudeHistoryImporter,
    _AccountSnapshot,
    _bridge_provider_session_id,
    _conversation_id,
)
from app.services.coding_sessions.local_runtime import (
    APPROVED_FOLDERS_SETTING,
    WORKSPACE_ROOTS_SETTING,
    LocalClaudeRuntime,
    LocalRuntimeRefused,
    LocalRuntimeStartRequest,
    _LocalRun,
)
from app.services.coding_sessions import local_runtime as runtime_module
from app.services.coding_sessions.service import CodingSessionBridgeOutbox
from app.services.local_db.database import LocalDatabase

USER_ID = "00000000-0000-4000-8000-000000000001"

pytestmark = pytest.mark.anyio


class _FakeSettings:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value


async def _account() -> _AccountSnapshot:
    return _AccountSnapshot(
        True, "a" * 64, "a" * 12, "2.1.228", None, account_label="a***n@t***.com"
    )


async def _no_account() -> _AccountSnapshot:
    return _AccountSnapshot(False, None, None, None, "claude_not_signed_in")


def _line(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def _write_transcript(
    config_dir: Path, session_id: str, records: list[dict[str, Any]]
) -> Path:
    project = config_dir / "projects" / "-Users-someone-code-scratch"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    with path.open("wb") as handle:
        for record in records:
            handle.write(_line(record))
    return path


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    await db.execute(
        """INSERT INTO auth_tokens (key, access_token, user_id, updated_at)
           VALUES ('current_user', 'test-token', ?, datetime('now'))""",
        (USER_ID,),
    )
    await db.commit()
    outbox = CodingSessionBridgeOutbox(db=db, cloud_enabled=False)
    config_dir = tmp_path / ".claude"
    workspace_root = tmp_path / "code"
    workspace_root.mkdir()
    settings = _FakeSettings(
        {
            WORKSPACE_ROOTS_SETTING: [str(workspace_root)],
            APPROVED_FOLDERS_SETTING: [],
        }
    )
    monkeypatch.setattr(runtime_module, "_settings", lambda: settings)
    # CI machines have no installed `claude`; these tests exercise the gates
    # BEFORE any launch, so a fake CLI path keeps the capability probe from
    # short-circuiting refusal reasons that come later in the ladder.
    fake_cli = tmp_path / "claude"
    fake_cli.write_text("#!/bin/sh\nexit 0\n")
    fake_cli.chmod(0o700)
    monkeypatch.setattr(
        runtime_module, "resolve_claude_executable", lambda: fake_cli
    )
    importer = ClaudeHistoryImporter(
        db=db,
        outbox=outbox,
        config_dir=config_dir,
        sessions_dir=tmp_path / "claude-sessions",
        account_reader=_account,
    )
    runtime = LocalClaudeRuntime(importer=importer, db=db, account_reader=_account)
    try:
        yield runtime, outbox, config_dir, workspace_root, settings, db
    finally:
        await db.close()


# --------------------------------------------------------------------- gates


async def test_workspace_outside_allowlist_is_refused(env, tmp_path: Path) -> None:
    runtime, *_ = env
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(LocalRuntimeRefused) as excinfo:
        runtime._require_approved_workspace(str(outside))
    assert excinfo.value.code == "workspace_outside_allowlist"


async def test_unapproved_workspace_is_refused_until_one_click_approval(env) -> None:
    runtime, _outbox, _config, workspace_root, _settings, _db = env
    project = workspace_root / "myrepo"
    project.mkdir()
    with pytest.raises(LocalRuntimeRefused) as excinfo:
        runtime._require_approved_workspace(str(project))
    assert excinfo.value.code == "workspace_not_approved"
    approved = runtime.approve_folder(str(project))
    assert str(project.resolve()) in approved["approved_folders"]
    assert runtime._require_approved_workspace(str(project)) == project.resolve()
    # Approval persists through the settings store, not process memory.
    assert _settings.values[APPROVED_FOLDERS_SETTING] == [str(project.resolve())]


async def test_approving_folder_outside_roots_is_refused(env, tmp_path: Path) -> None:
    runtime, *_ = env
    outside = tmp_path / "not-code"
    outside.mkdir()
    with pytest.raises(LocalRuntimeRefused) as excinfo:
        runtime.approve_folder(str(outside))
    assert excinfo.value.code == "workspace_outside_allowlist"


async def test_start_refuses_when_capabilities_unavailable(env) -> None:
    runtime, _outbox, _config, workspace_root, _settings, db = env
    runtime._account_reader = _no_account
    project = workspace_root / "repo"
    project.mkdir()
    runtime.approve_folder(str(project))
    with pytest.raises(LocalRuntimeRefused) as excinfo:
        await runtime.start(
            LocalRuntimeStartRequest(workspace=str(project), prompt="hello")
        )
    assert excinfo.value.code == "runtime_unavailable"
    assert "claude_not_signed_in" in excinfo.value.detail


# -------------------------------------------------------------------- resume


async def test_resumable_is_false_without_local_transcript(env) -> None:
    runtime, *_ = env
    verdict = await runtime.resumable(str(uuid4()))
    assert verdict == {
        "resumable": False,
        "reason": "transcript_not_on_this_machine",
    }


async def test_resumable_reads_claude_own_store_and_decodes_composite(env) -> None:
    runtime, _outbox, config_dir, workspace_root, _settings, _db = env
    project = workspace_root / "repo"
    project.mkdir()
    session_id = str(uuid4())
    _write_transcript(
        config_dir,
        session_id,
        [{"type": "user", "uuid": str(uuid4()), "cwd": str(project)}],
    )
    verdict = await runtime.resumable(session_id)
    assert verdict["resumable"] is True
    assert verdict["workspace"] == str(project)
    # The composite claude-sdk identity decodes to the same raw session.
    import base64

    composite = (
        "claude-sdk:" + "0" * 64 + ":"
        + base64.urlsafe_b64encode(session_id.encode()).decode().rstrip("=")
    )
    assert LocalClaudeRuntime.decode_provider_session_id(composite) == session_id


async def test_resume_start_refused_without_transcript(env) -> None:
    runtime, _outbox, _config, workspace_root, _settings, _db = env
    project = workspace_root / "repo"
    project.mkdir()
    runtime.approve_folder(str(project))
    with pytest.raises(LocalRuntimeRefused) as excinfo:
        await runtime.start(
            LocalRuntimeStartRequest(
                workspace=str(project),
                prompt="continue",
                resume_session_id=str(uuid4()),
            )
        )
    assert excinfo.value.code == "transcript_not_on_this_machine"


# -------------------------------------------------------------------- mirror


async def test_mirror_pass_lands_exact_append_native_batches_in_outbox(env) -> None:
    """The live-session mirror IS the certified import, targeted at one session."""
    runtime, outbox, config_dir, workspace_root, _settings, db = env
    project = workspace_root / "repo"
    project.mkdir()
    session_id = str(uuid4())
    records = [
        {
            "type": "user",
            "uuid": str(uuid4()),
            "cwd": str(project),
            "message": {"role": "user", "content": "write a haiku"},
            "timestamp": "2026-08-17T01:00:00Z",
        },
        {
            "type": "assistant",
            "uuid": str(uuid4()),
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            "timestamp": "2026-08-17T01:00:05Z",
        },
    ]
    transcript = _write_transcript(config_dir, session_id, records)
    run = _LocalRun(
        runtime_id="rt-1",
        session_id=session_id,
        workspace=project,
        action="start",
    )
    run.transcript_path = transcript
    await runtime._mirror(run)
    assert run.mirror_error is None
    assert run.mirror_passes == 1

    project_key = "claude-local:" + __import__("hashlib").sha256(
        str(transcript.parent.resolve()).encode()
    ).hexdigest()
    expected_provider_session_id = _bridge_provider_session_id(project_key, session_id)
    assert run.provider_session_id == expected_provider_session_id
    assert run.conversation_id == _conversation_id(
        USER_ID, expected_provider_session_id, project_key
    )

    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    envelopes = [json.loads(str(row["envelope_json"])) for row in rows]
    appends = [e for e in envelopes if e["action"] == "append_native"]
    assert len(appends) == 1
    envelope = appends[0]
    assert envelope["provider"] == "claude_code"
    assert envelope["provider_session_id"] == expected_provider_session_id
    assert envelope["conversation"]["conversation_id"] == run.conversation_id
    assert envelope["origin"] == "matrx_local"
    assert [entry["payload"] for entry in envelope["entries"]] == records
    assert [entry["source_sequence"] for entry in envelope["entries"]] == [0, 1]

    # A second pass over a GROWN transcript reconciles instead of conflicting.
    with transcript.open("ab") as handle:
        handle.write(_line({"type": "assistant", "uuid": str(uuid4())}))
    await runtime._mirror(run)
    assert run.mirror_passes == 2
    assert run.mirror_error is None


# ------------------------------------------------------------------ registry


def test_runtime_commands_are_registered_for_the_broadcast_relay() -> None:
    from app.api.extension_handlers import HANDLERS

    for command in (
        "coding_runtime.capabilities",
        "coding_runtime.start",
        "coding_runtime.status",
        "coding_runtime.cancel",
        "coding_runtime.resumable",
    ):
        assert command in HANDLERS
