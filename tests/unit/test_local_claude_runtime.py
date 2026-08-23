"""The LOCAL Claude Code runtime — allowlist, approval, resume truth, mirror.

These are forcing-function tests: the mirror test drives the REAL importer and
the REAL durable outbox schema against a synthetic Claude transcript, so it
only passes when a runtime-launched session genuinely becomes exact
`append_native` envelopes under the certified bridge identity.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

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
    LocalRuntimeFolderRequest,
    LocalRuntimeRefused,
    LocalRuntimeStartRequest,
    _LocalRun,
)
from app.services.coding_sessions.workspace_discovery import WorkspaceDiscoveryNode
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
        True,
        "a" * 64,
        "a" * 12,
        "2.1.228",
        None,
        account_label="a***n@t***.com",
        local_display_identity="arman@test.com",
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
    monkeypatch.setattr(runtime_module, "resolve_claude_executable", lambda: fake_cli)
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


async def test_capabilities_show_full_account_only_in_local_display_field(env) -> None:
    runtime, *_ = env

    capabilities = await runtime.capabilities()

    assert capabilities["claude_account_display_identity"] == "arman@test.com"
    assert capabilities["claude_account_label"] == "a***n@t***.com"


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


async def test_workspace_root_management_is_explicit_minimal_and_preserves_approvals(
    env, tmp_path: Path
) -> None:
    runtime, _outbox, _config, workspace_root, settings, _db = env
    other_root = tmp_path / "other-code"
    project = other_root / "team" / "service"
    project.mkdir(parents=True)

    added = runtime.add_workspace_root(str(other_root))
    assert added.workspace_roots == [
        str(workspace_root.resolve()),
        str(other_root.resolve()),
    ]
    # Adding a nested project does not create redundant filesystem authority.
    nested = runtime.add_workspace_root(str(project))
    assert nested.workspace_roots == added.workspace_roots

    runtime.approve_folder(str(project))
    removed = runtime.remove_workspace_root(str(other_root))
    assert removed.workspace_roots == [str(workspace_root.resolve())]
    assert removed.approved_folders == [str(project.resolve())]
    assert removed.affected_approvals == [str(project.resolve())]
    # Removing authority never silently deletes approval history, but the
    # execution gate still refuses the now-out-of-root project.
    assert settings.values[APPROVED_FOLDERS_SETTING] == [str(project.resolve())]
    with pytest.raises(LocalRuntimeRefused) as excinfo:
        runtime._require_approved_workspace(str(project))
    assert excinfo.value.code == "workspace_outside_allowlist"


async def test_removing_last_root_does_not_reenable_default(env) -> None:
    runtime, _outbox, _config, workspace_root, settings, _db = env
    removed = runtime.remove_workspace_root(str(workspace_root))
    assert removed.workspace_roots == []
    assert settings.values[WORKSPACE_ROOTS_SETTING] == []
    assert runtime.list_approved()["workspace_roots"] == []


async def test_workspace_folder_request_is_strict() -> None:
    with pytest.raises(ValidationError):
        LocalRuntimeFolderRequest.model_validate(
            {"folder": "/tmp/project", "unexpected": True}
        )


def _flatten_discovery(
    nodes: list[WorkspaceDiscoveryNode],
) -> list[WorkspaceDiscoveryNode]:
    flattened: list[WorkspaceDiscoveryNode] = []
    pending = list(nodes)
    while pending:
        node = pending.pop(0)
        flattened.append(node)
        pending[0:0] = node.children
    return flattened


async def test_workspace_discovery_returns_project_aware_privacy_minimal_tree(
    env, tmp_path: Path
) -> None:
    runtime, _outbox, _config, workspace_root, _settings, _db = env
    git_repo = workspace_root / "backend"
    (git_repo / ".git").mkdir(parents=True)
    (git_repo / "pyproject.toml").write_text("[project]\nname='private'\n")
    (git_repo / "secret-customer-name.txt").write_text("must not be returned")
    (git_repo / "src" / "internal").mkdir(parents=True)
    web_project = workspace_root / "frontend"
    web_project.mkdir()
    (web_project / "package.json").write_text("{}")
    ordinary = workspace_root / "notes"
    ordinary.mkdir()
    monorepo_project = workspace_root / "portfolio" / "services" / "billing"
    monorepo_project.mkdir(parents=True)
    (monorepo_project / "go.mod").write_text("module example.invalid/billing\n")
    hidden = workspace_root / ".private-repo"
    (hidden / ".git").mkdir(parents=True)
    vendor = workspace_root / "node_modules" / "dependency"
    (vendor / "package.json").parent.mkdir(parents=True)
    (vendor / "package.json").write_text("{}")
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    symlink = workspace_root / "escaped-link"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    result = await runtime.discover_workspaces()
    assert result.parent is None
    assert result.workspace_roots == [str(workspace_root.resolve())]
    assert result.approved_folders == []
    nodes = _flatten_discovery(result.roots)
    by_path = {node.path: node for node in nodes}
    assert by_path[str(git_repo.resolve())].kind == "git_repository"
    assert by_path[str(git_repo.resolve())].project_kinds == ["git", "python"]
    assert by_path[str(web_project.resolve())].kind == "project"
    assert by_path[str(web_project.resolve())].project_kinds == ["javascript"]
    assert by_path[str(ordinary.resolve())].kind == "directory"
    assert by_path[str(monorepo_project.resolve())].project_kinds == ["go"]
    assert str((git_repo / "src").resolve()) not in by_path
    assert str(hidden.resolve()) not in by_path
    assert str(vendor.resolve()) not in by_path
    assert str(outside.resolve()) not in by_path
    assert result.project_count == 3
    assert result.skipped >= 3
    # No filenames or file contents cross the discovery response.
    payload = result.model_dump_json()
    assert "secret-customer-name" not in payload
    assert "must not be returned" not in payload


async def test_workspace_discovery_selected_parent_must_be_under_configured_root(
    env, tmp_path: Path
) -> None:
    runtime, *_ = env
    outside = tmp_path / "outside-root"
    outside.mkdir()
    with pytest.raises(LocalRuntimeRefused) as excinfo:
        await runtime.discover_workspaces(str(outside))
    assert excinfo.value.code == "workspace_outside_allowlist"


async def test_workspace_discovery_selected_parent_is_stable_and_bounded(env) -> None:
    runtime, _outbox, _config, workspace_root, _settings, _db = env
    parent = workspace_root / "portfolio"
    cursor = parent
    for index in range(11):
        cursor = cursor / f"level-{index}"
        cursor.mkdir(parents=True)
    (cursor / "Cargo.toml").write_text("[package]\n")

    result = await runtime.discover_workspaces(str(parent))
    assert result.parent == str(parent.resolve())
    assert [node.path for node in result.roots] == [str(parent.resolve())]
    assert result.truncated is True
    assert any(node.truncated for node in _flatten_discovery(result.roots))


async def test_workspace_discovery_calls_do_not_overlap(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, *_ = env
    entered = threading.Event()
    release = threading.Event()
    real_discover = runtime_module.discover_workspace_tree

    def blocked_discover(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return real_discover(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "discover_workspace_tree", blocked_discover)
    first = asyncio.create_task(runtime.discover_workspaces())
    assert await asyncio.to_thread(entered.wait, 5)
    with pytest.raises(LocalRuntimeRefused) as excinfo:
        await runtime.discover_workspaces()
    assert excinfo.value.code == "workspace_discovery_in_progress"
    release.set()
    await first


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
        "claude-sdk:"
        + "0" * 64
        + ":"
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
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
            },
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

    project_key = (
        "claude-local:"
        + __import__("hashlib")
        .sha256(str(transcript.parent.resolve()).encode())
        .hexdigest()
    )
    expected_provider_session_id = _bridge_provider_session_id(project_key, session_id)
    assert run.provider_session_id == expected_provider_session_id
    assert run.conversation_id == _conversation_id(
        USER_ID, expected_provider_session_id, project_key
    )

    rows = await db.fetchall(
        """SELECT outbox.envelope_json, metadata.enqueue_origin
           FROM coding_session_bridge_outbox AS outbox
           JOIN coding_session_bridge_queue_metadata AS metadata
             ON metadata.receipt_id = outbox.id
           ORDER BY outbox.id"""
    )
    assert {row["enqueue_origin"] for row in rows} == {"local_runtime"}
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


async def test_final_mirror_failure_cannot_hide_execution_terminal(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _outbox, _config, workspace_root, _settings, _db = env
    run = _LocalRun(
        runtime_id="rt-terminal",
        session_id=str(uuid4()),
        workspace=workspace_root,
        action="start",
        status="completed",
    )

    async def _crash(_run: _LocalRun, *, final: bool = False) -> None:
        assert final is True
        raise RuntimeError("mirror settlement unavailable")

    monkeypatch.setattr(runtime, "_mirror", _crash)
    await runtime._settle_run(run)

    terminal = run.events[-1]
    assert terminal["event"] == "runtime_finished"
    assert terminal["execution"] == {"status": "completed", "error": None}
    assert terminal["mirror"] == {
        "status": "failed",
        "passes": 0,
        "error": "mirror settlement unavailable",
    }
    assert terminal["sequence"] == 1
    assert run.public_status()["execution"]["status"] == "completed"
    assert run.public_status()["mirror"]["status"] == "failed"


async def test_runtime_stream_reports_bounded_replay_gap(env) -> None:
    runtime, _outbox, _config, workspace_root, _settings, _db = env
    run = _LocalRun(
        runtime_id="rt-gap",
        session_id=str(uuid4()),
        workspace=workspace_root,
        action="start",
        status="completed",
    )
    run.events = deque(maxlen=2)
    runtime._runs[run.runtime_id] = run
    runtime._emit(run, {"event": "one"})
    runtime._emit(run, {"event": "two"})
    runtime._emit(run, {"event": "three"})

    replay = [
        event async for event in runtime.subscribe(run.runtime_id, after_sequence=0)
    ]

    assert replay[0] == {
        "event": "stream_gap",
        "requested_after": 0,
        "earliest_available": 2,
        "latest_available": 3,
        "resync_required": True,
    }
    assert [event["sequence"] for event in replay[1:]] == [2, 3]


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
