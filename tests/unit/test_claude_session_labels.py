"""Claude's own session labels must reach AI Matrx and stay identical.

Arman's ruling (2026-08-16): the label in Claude Code's sidebar and the title
in AI Matrx cannot differ, and a rename in Claude Code must reach us.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.services.aidream.client import AIDreamError, AIDreamOfflineError
from app.services.coding_sessions.claude_history import (
    ClaudeHistoryImporter,
    ClaudeHistoryImportRequest,
    _AccountSnapshot,
    _bridge_provider_session_id,
)
from app.services.coding_sessions.claude_session_index import read_session_index
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import (
    CodingSessionBridgeOutbox,
    _validate_upstream_acknowledgement,
)
from app.services.coding_sessions.title_sync import (
    ClaudeSessionMetadataReconciler,
    ClaudeTitleSyncBlocked,
    raw_session_id,
    session_metadata_request,
)
from app.services.local_db.database import LocalDatabase


def _write_index_record(
    root: Path,
    *,
    account: str = "acct-1",
    org: str = "org-1",
    cli_session_id: str,
    **fields: Any,
) -> Path:
    folder = root / account / org
    folder.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "sessionId": f"local_{uuid4()}",
        "cliSessionId": cli_session_id,
        **fields,
    }
    path = folder / f"local_{uuid4()}.json"
    path.write_text(json.dumps(record))
    return path


async def _account_a() -> _AccountSnapshot:
    return _AccountSnapshot(
        True, "a" * 64, "a" * 12, "2.1.228", None, account_label="a***n@t***.com"
    )


class _FakeClient:
    def __init__(self, sessions: list[dict[str, Any]] | Exception) -> None:
        self._sessions = sessions
        self.calls: list[str] = []

    async def get(self, path: str, jwt: str | None = None) -> Any:
        self.calls.append(path)
        if isinstance(self._sessions, Exception):
            raise self._sessions
        return {"provider": "claude_code", "sessions": self._sessions}


class _BridgeAckClient:
    async def post(
        self,
        _path: str,
        payload: dict[str, Any],
        *,
        jwt: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        assert jwt and timeout == 30.0
        return {
            "schema_version": 1,
            "action": payload["action"],
            "provider": payload["provider"],
            "fidelity": "event_mirror",
            "session_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "accepted": 1,
            "duplicates": 0,
            "conflicts": 0,
        }


@pytest.fixture
async def env(tmp_path: Path):
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    await db.execute(
        """INSERT INTO auth_tokens (key, access_token, user_id, updated_at)
           VALUES ('current_user', 'test-token', ?, datetime('now'))""",
        ("00000000-0000-4000-8000-000000000001",),
    )
    await db.commit()
    outbox = CodingSessionBridgeOutbox(db=db, cloud_enabled=False)
    try:
        yield db, outbox, tmp_path
    finally:
        await db.close()


def test_index_reader_keeps_the_newest_record_and_leaks_no_raw_path(
    tmp_path: Path,
) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    _write_index_record(
        root,
        account="acct-1",
        cli_session_id=session_id,
        title="Stale title",
        lastActivityAt=100,
        cwd="/Users/someone/code/matrx-frontend",
    )
    # The desktop sync script unions every account's index into every account
    # folder, so one session commonly has several records. Newest wins.
    _write_index_record(
        root,
        account="acct-2",
        cli_session_id=session_id,
        title="  Current   sidebar label ",
        titleSource="user",
        lastActivityAt=900,
        cwd="/Users/someone/code/matrx-frontend",
        branch="feature/labels",
        worktreeName="labels-wt",
        worktreePath="/Users/someone/worktrees/labels",
        isArchived=True,
    )
    entries, totals = read_session_index(root)

    assert totals == {"files": 2, "records": 1, "unreadable": 0}
    entry = entries[session_id]
    assert entry.title == "Current sidebar label"
    assert entry.title_source == "user"
    assert entry.git_branch == "feature/labels"
    assert entry.worktree_name == "labels-wt"
    assert entry.is_archived is True
    # Display label only — the raw cwd and worktree path never become payload.
    assert entry.workspace_name == "matrx-frontend"
    payload = entry.metadata_payload()
    assert payload == {
        "title": "Current sidebar label",
        "project_name": "matrx-frontend",
        "git_branch": "feature/labels",
        "worktree_name": "labels-wt",
        "is_archived": True,
    }
    assert "/Users/someone" not in json.dumps(payload)


def test_index_reader_joins_pins_and_categories_from_the_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    record_path = _write_index_record(
        root,
        cli_session_id=session_id,
        title="Pinned work",
        lastActivityAt=100,
        cwd="/Users/someone/code/aidream",
    )
    ledger = tmp_path / "claude-code-sidebar-state.json"
    ledger.write_text(
        json.dumps(
            {
                record_path.name: {
                    "title": "Pinned work",
                    "titleSource": "user",
                    "isArchived": False,
                    "isPinned": True,
                    "pinnedRank": 4,
                    "categoryName": "Outreach System",
                }
            }
        )
    )
    monkeypatch.setenv("CLAUDE_SIDEBAR_LEDGER", str(ledger))
    entries, _totals = read_session_index(root)
    entry = entries[session_id]
    assert entry.is_pinned is True
    assert entry.pinned_rank == 4
    assert entry.category == "Outreach System"
    payload = entry.metadata_payload()
    assert payload["is_pinned"] is True
    assert payload["pinned_rank"] == 4
    assert payload["category"] == "Outreach System"
    # A session the ledger has never observed sends no pin/category fields.
    other = str(uuid4())
    _write_index_record(root, cli_session_id=other, title="Plain", lastActivityAt=1)
    entries, _totals = read_session_index(root)
    assert "is_pinned" not in entries[other].metadata_payload()
    assert "category" not in entries[other].metadata_payload()


def test_index_reader_survives_corrupt_and_missing_roots(tmp_path: Path) -> None:
    root = tmp_path / "claude-code-sessions"
    assert read_session_index(root) == (
        {},
        {"files": 0, "records": 0, "unreadable": 0},
    )
    folder = root / "acct" / "org"
    folder.mkdir(parents=True)
    (folder / "local_bad.json").write_text("{not json")
    (folder / "local_list.json").write_text("[]")
    good = str(uuid4())
    _write_index_record(root, cli_session_id=good, title="Fine", lastActivityAt=1)
    entries, totals = read_session_index(root)
    assert list(entries) == [good]
    assert totals["unreadable"] == 2


def test_raw_session_id_resolves_both_bound_identity_forms() -> None:
    session_id = str(uuid4())
    composite = _bridge_provider_session_id("claude-local:abc", session_id)

    assert raw_session_id(session_id) == session_id
    assert raw_session_id(composite) == session_id
    assert raw_session_id("claude-sdk:only-two-parts") is None
    assert raw_session_id("not-a-uuid") is None


@pytest.mark.anyio
async def test_sync_matches_bound_sessions_only_and_is_idempotent(env) -> None:
    db, outbox, tmp_path = env
    root = tmp_path / "claude-code-sessions"
    bound = str(uuid4())
    unbound = str(uuid4())
    missing_locally = str(uuid4())
    _write_index_record(
        root,
        cli_session_id=bound,
        title="Reconcile Claude-native titles",
        lastActivityAt=10,
        cwd="/code/matrx-local",
        branch="main",
        isArchived=False,
    )
    # A local session AI Matrx never mirrored: its label must never leave.
    _write_index_record(
        root, cli_session_id=unbound, title="Private local work", lastActivityAt=11
    )
    client = _FakeClient(
        [
            {"provider_session_id": bound, "provider_project_key": None},
            {"provider_session_id": missing_locally, "provider_project_key": None},
        ]
    )
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=client,
        index_reader=lambda: read_session_index(root),
    )

    first = await reconciler.sync()
    assert first["bound_sessions"] == 2
    assert first["matched"] == 1
    assert first["unmatched"] == 1
    assert first["unmatched_session_ids"] == [missing_locally]
    assert first["queued"] == 1

    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    assert len(rows) == 1
    envelope = BridgeRequest.model_validate_json(rows[0]["envelope_json"])
    assert envelope.action.value == "observe_hook"
    assert envelope.provider_session_id == bound
    assert envelope.hook_event is not None
    assert envelope.hook_event.name == "SessionMetadata"
    assert envelope.hook_event.payload == {
        "title": "Reconcile Claude-native titles",
        "project_name": "matrx-local",
        "git_branch": "main",
        "is_archived": False,
    }
    serialized = rows[0]["envelope_json"]
    assert unbound not in serialized
    assert "Private local work" not in serialized

    # Local enqueue is not cloud sync. Before acknowledgement, a second pass
    # reports the already-durable row instead of marking it synchronized.
    second = await reconciler.sync()
    assert second["queued"] == 0
    assert second["already_queued"] == 1
    assert second["unchanged"] == 0
    assert len(await db.fetchall("SELECT id FROM coding_session_bridge_outbox")) == 1
    assert await db.fetchall("SELECT * FROM claude_session_metadata_sent") == []

    publisher = CodingSessionBridgeOutbox(
        db=db,
        client=_BridgeAckClient(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    assert (await publisher.sync_pending())["sent"] == 1
    acknowledged_rows = await db.fetchall(
        "SELECT provider_session_id, payload_sha256 FROM claude_session_metadata_sent"
    )
    assert len(acknowledged_rows) == 1
    assert acknowledged_rows[0]["provider_session_id"] == bound
    acknowledged = await reconciler.sync()
    assert acknowledged["queued"] == 0
    assert acknowledged["already_queued"] == 0
    assert acknowledged["unchanged"] == 1


@pytest.mark.anyio
async def test_a_rename_in_claude_code_reaches_the_next_sync(env) -> None:
    db, outbox, tmp_path = env
    root = tmp_path / "claude-code-sessions"
    bound = str(uuid4())
    _write_index_record(
        root, cli_session_id=bound, title="Original name", lastActivityAt=1
    )
    client = _FakeClient([{"provider_session_id": bound}])
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=client,
        index_reader=lambda: read_session_index(root),
    )
    assert (await reconciler.sync())["queued"] == 1

    # The user renames the session in Claude Code; the app writes a newer record.
    _write_index_record(
        root,
        account="acct-2",
        cli_session_id=bound,
        title="Renamed by the user",
        titleSource="user",
        lastActivityAt=2,
    )
    third = await reconciler.sync()
    assert third["queued"] == 1
    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    latest = BridgeRequest.model_validate_json(rows[-1]["envelope_json"])
    assert latest.hook_event is not None
    assert latest.hook_event.payload["title"] == "Renamed by the user"


@pytest.mark.anyio
async def test_dry_run_reports_without_enqueueing(env) -> None:
    db, outbox, tmp_path = env
    root = tmp_path / "claude-code-sessions"
    bound = str(uuid4())
    _write_index_record(
        root, cli_session_id=bound, title="Planned label", lastActivityAt=1
    )
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient([{"provider_session_id": bound}]),
        index_reader=lambda: read_session_index(root),
    )
    result = await reconciler.sync(dry_run=True)
    assert result["queued"] == 1
    assert result["sample_titles"][0]["title"] == "Planned label"
    assert await db.fetchall("SELECT id FROM coding_session_bridge_outbox") == []


@pytest.mark.anyio
async def test_sync_blocks_loudly_instead_of_half_running(env) -> None:
    db, outbox, tmp_path = env
    root = tmp_path / "claude-code-sessions"
    for error in (AIDreamOfflineError("down"), AIDreamError(500, "boom")):
        reconciler = ClaudeSessionMetadataReconciler(
            db=db,
            outbox=outbox,
            client=_FakeClient(error),
            index_reader=lambda: read_session_index(root),
        )
        with pytest.raises(ClaudeTitleSyncBlocked):
            await reconciler.sync()

    await db.execute("DELETE FROM auth_tokens")
    await db.commit()
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient([]),
        index_reader=lambda: read_session_index(root),
    )
    with pytest.raises(ClaudeTitleSyncBlocked) as blocked:
        await reconciler.sync()
    assert blocked.value.reason == "no_active_user_jwt"


def test_acknowledgement_accepts_native_binding_and_settles_unbound() -> None:
    request = session_metadata_request(
        provider_session_id=str(uuid4()),
        provider_project_key=None,
        payload={"title": "Label"},
    )
    base = {"schema_version": 1, "action": "observe_hook", "provider": "claude_code"}

    # An IMPORTED session's binding is native fidelity — the label still applies.
    _validate_upstream_acknowledgement(
        {
            **base,
            "fidelity": "native",
            "session_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "accepted": 1,
            "duplicates": 0,
            "conflicts": 0,
        },
        request,
    )
    # An unmirrored session settles: accepted=0, no session identity, no retry.
    _validate_upstream_acknowledgement(
        {**base, "accepted": 0, "duplicates": 0, "conflicts": 0}, request
    )
    with pytest.raises(AIDreamError):
        _validate_upstream_acknowledgement(
            {**base, "accepted": 0, "duplicates": 0, "conflicts": 1}, request
        )
    with pytest.raises(AIDreamError):
        _validate_upstream_acknowledgement(
            {
                **base,
                "fidelity": "native",
                "session_id": "not-a-uuid",
                "conversation_id": str(uuid4()),
                "accepted": 1,
                "duplicates": 0,
                "conflicts": 0,
            },
            request,
        )


@pytest.mark.anyio
async def test_import_carries_the_claude_index_title(env) -> None:
    db, outbox, tmp_path = env
    config_dir = tmp_path / ".claude"
    sessions_dir = tmp_path / "claude-code-sessions"
    session_id = str(uuid4())
    project = config_dir / "projects" / "-code-matrx-local"
    project.mkdir(parents=True)
    (project / f"{session_id}.jsonl").write_bytes(
        json.dumps(
            {
                "type": "user",
                "uuid": str(uuid4()),
                "cwd": "/code/matrx-local",
                "message": {"role": "user", "content": "first prompt text"},
            },
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    _write_index_record(
        sessions_dir,
        cli_session_id=session_id,
        title="Exact sidebar label",
        titleSource="user",
        lastActivityAt=5,
        cwd="/code/matrx-local",
        branch="main",
    )
    importer = ClaudeHistoryImporter(
        db=db,
        outbox=outbox,
        config_dir=config_dir,
        sessions_dir=sessions_dir,
        account_reader=_account_a,
    )
    preview = await importer.preview()
    item = preview["sessions"][0]
    assert item["title"] == "Exact sidebar label"
    assert item["title_from_claude_index"] is True
    assert item["claude_title_source"] == "user"
    assert item["git_branch"] == "main"

    receipt = await importer.import_selected(
        ClaudeHistoryImportRequest.model_validate(
            {
                "provider_account_key": preview["provider_account_key"],
                "sessions": [
                    {
                        "session_id": session_id,
                        "provider_project_key": item["project_key"],
                        "source_revision": item["source_revision"],
                    }
                ],
            }
        )
    )
    assert receipt["labeled_sessions"] == 1
    assert receipt["queued_label_updates"] == 1

    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    envelopes = [BridgeRequest.model_validate_json(r["envelope_json"]) for r in rows]
    # The label observation is queued BEHIND the batches that mint the binding.
    assert envelopes[-1].action.value == "observe_hook"
    assert envelopes[0].action.value == "append_native"
    label = envelopes[-1]
    assert label.hook_event is not None
    assert label.hook_event.payload["title"] == "Exact sidebar label"
    assert label.provider_session_id == _bridge_provider_session_id(
        item["project_key"], session_id
    )

    # The import shares the same durable outbox transaction. Until the cloud
    # acknowledges it, the label is pending rather than falsely "synced".
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient(
            [
                {
                    "provider_session_id": label.provider_session_id,
                    "provider_project_key": item["project_key"],
                }
            ]
        ),
        index_reader=lambda: read_session_index(sessions_dir),
    )
    result = await reconciler.sync()
    assert result["matched"] == 1
    assert result["unchanged"] == 0
    assert result["queued"] == 0
    assert result["already_queued"] == 1
