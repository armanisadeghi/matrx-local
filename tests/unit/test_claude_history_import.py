from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.services.coding_sessions.claude_history import (
    ClaudeHistoryConflict,
    ClaudeHistoryImporter,
    ClaudeHistoryImportRequest,
    _AccountSnapshot,
)
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import CodingSessionBridgeOutbox
from app.services.local_db.database import LocalDatabase


def _line(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def _write_session(
    config_dir: Path,
    *,
    session_id: str,
    records: list[dict[str, Any] | bytes],
    subagents: dict[str, list[dict[str, Any]]] | None = None,
) -> Path:
    project = config_dir / "projects" / "-private-project"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    with path.open("wb") as handle:
        for record in records:
            handle.write(record if isinstance(record, bytes) else _line(record))
    for agent_id, entries in (subagents or {}).items():
        subagent_dir = project / session_id / "subagents"
        subagent_dir.mkdir(parents=True, exist_ok=True)
        with (subagent_dir / f"{agent_id}.jsonl").open("wb") as handle:
            for entry in entries:
                handle.write(_line(entry))
    return path


async def _account_a() -> _AccountSnapshot:
    return _AccountSnapshot(True, "a" * 64, "a" * 12, "2.1.228", None)


async def _account_b() -> _AccountSnapshot:
    return _AccountSnapshot(True, "b" * 64, "b" * 12, "2.1.228", None)


@pytest.fixture
async def importer_env(tmp_path: Path):
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    await db.execute(
        """INSERT INTO auth_tokens
           (key, access_token, user_id, updated_at)
           VALUES ('current_user', 'test-token', ?, datetime('now'))""",
        ("00000000-0000-4000-8000-000000000001",),
    )
    await db.commit()
    outbox = CodingSessionBridgeOutbox(db=db, cloud_enabled=False)
    try:
        yield tmp_path / ".claude", db, outbox
    finally:
        await db.close()


@pytest.mark.anyio
async def test_preview_discloses_scope_without_paths_or_account_pii(importer_env) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    _write_session(
        config_dir,
        session_id=session_id,
        records=[
            {
                "type": "user",
                "uuid": str(uuid4()),
                "sessionId": session_id,
                "cwd": "/private/company/secret-project",
                "gitBranch": "feature/history",
                "message": {"role": "user", "content": "private prompt"},
            },
            {
                "type": "custom-title",
                "sessionId": session_id,
                "customTitle": "History work",
            },
        ],
        subagents={
            "agent-one": [
                {
                    "type": "assistant",
                    "uuid": str(uuid4()),
                    "sessionId": session_id,
                    "message": {"role": "assistant", "content": "subagent"},
                }
            ]
        },
    )
    preview = await ClaudeHistoryImporter(
        db=db,
        outbox=outbox,
        config_dir=config_dir,
        account_reader=_account_a,
    ).preview()

    assert preview["import_ready"] is True
    assert preview["totals"] == {
        "session_count": 1,
        "file_count": 2,
        "bytes": preview["totals"]["bytes"],
        "project_count": 1,
    }
    assert preview["sessions"][0]["title"] == "History work"
    assert preview["sessions"][0]["project_name"] == "secret-project"
    assert preview["sessions"][0]["subagent_count"] == 1
    serialized = json.dumps(preview)
    assert "/private/company" not in serialized
    assert "private prompt" not in serialized
    assert "@" not in serialized


@pytest.mark.anyio
async def test_selected_import_is_atomic_bounded_and_replay_safe(importer_env) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    user_id = str(uuid4())
    assistant_id = str(uuid4())
    _write_session(
        config_dir,
        session_id=session_id,
        records=[
            {
                "type": "user",
                "uuid": user_id,
                "sessionId": session_id,
                "cwd": "/private/company/secret-project",
                "message": {"role": "user", "content": "hello"},
            },
            b"{partial-json\n",
            {
                "type": "assistant",
                "uuid": assistant_id,
                "sessionId": session_id,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                },
            },
            {
                "type": "system",
                "uuid": str(uuid4()),
                "sessionId": session_id,
                "subtype": "compact_boundary",
            },
        ],
        subagents={
            "agent-one": [
                {
                    "type": "assistant",
                    "uuid": str(uuid4()),
                    "sessionId": session_id,
                    "message": {"role": "assistant", "content": "subagent"},
                }
            ]
        },
    )
    importer = ClaudeHistoryImporter(
        db=db,
        outbox=outbox,
        config_dir=config_dir,
        account_reader=_account_a,
    )
    preview = await importer.preview()
    selected = ClaudeHistoryImportRequest.model_validate(
        {
            "provider_account_key": preview["provider_account_key"],
            "sessions": [
                {
                    "session_id": session_id,
                    "source_revision": preview["sessions"][0]["source_revision"],
                }
            ],
        }
    )
    first = await importer.import_selected(selected)
    replay = await importer.import_selected(selected)

    assert first["accepted"] is True
    assert first["corrupt_lines"] == 1
    assert first["source_complete"] is False
    assert first["native_restore_available"] is False
    assert replay["queued_batches"] == 0
    assert replay["duplicate_pending_batches"] == first["queued_batches"]
    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    envelopes = [BridgeRequest.model_validate_json(row["envelope_json"]) for row in rows]
    assert {envelope.stream_key for envelope in envelopes} == {
        "main",
        "subagent:agent-one",
    }
    main_entries = [
        entry
        for envelope in envelopes
        if envelope.stream_key == "main"
        for entry in envelope.entries
    ]
    assert [entry.source_sequence for entry in main_entries] == [0, 2, 3]
    assert any(entry.kind == "system" for entry in main_entries)
    assert all(envelope.source_metadata for envelope in envelopes)
    assert all(
        envelope.source_metadata and not envelope.source_metadata.source_complete
        for envelope in envelopes
    )
    serialized = "".join(row["envelope_json"] for row in rows)
    assert "/private/company" in serialized
    assert "provider_account_key" in serialized
    assert "@" not in serialized


@pytest.mark.anyio
async def test_account_switch_and_changed_revision_do_not_enqueue(importer_env) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    path = _write_session(
        config_dir,
        session_id=session_id,
        records=[
            {
                "type": "user",
                "uuid": str(uuid4()),
                "sessionId": session_id,
                "message": {"role": "user", "content": "hello"},
            }
        ],
    )
    importer = ClaudeHistoryImporter(
        db=db,
        outbox=outbox,
        config_dir=config_dir,
        account_reader=_account_a,
    )
    preview = await importer.preview()
    payload = {
        "provider_account_key": preview["provider_account_key"],
        "sessions": [
            {
                "session_id": session_id,
                "source_revision": preview["sessions"][0]["source_revision"],
            }
        ],
    }
    switched = ClaudeHistoryImporter(
        db=db,
        outbox=outbox,
        config_dir=config_dir,
        account_reader=_account_b,
    )
    with pytest.raises(ClaudeHistoryConflict, match="changed after preview"):
        await switched.import_selected(ClaudeHistoryImportRequest.model_validate(payload))
    assert await outbox.pending_count() == 0

    with path.open("ab") as handle:
        handle.write(
            _line(
                {
                    "type": "assistant",
                    "uuid": str(uuid4()),
                    "sessionId": session_id,
                    "message": {"role": "assistant", "content": "update"},
                }
            )
        )
    with pytest.raises(ClaudeHistoryConflict, match="changed after preview"):
        await importer.import_selected(ClaudeHistoryImportRequest.model_validate(payload))
    assert await outbox.pending_count() == 0


@pytest.mark.anyio
async def test_reused_provider_uuid_is_rejected_without_partial_outbox(importer_env) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    reused = str(uuid4())
    _write_session(
        config_dir,
        session_id=session_id,
        records=[
            {
                "type": "user",
                "uuid": reused,
                "sessionId": session_id,
                "message": {"role": "user", "content": "one"},
            },
            {
                "type": "assistant",
                "uuid": reused,
                "sessionId": session_id,
                "message": {"role": "assistant", "content": "two"},
            },
        ],
    )
    importer = ClaudeHistoryImporter(
        db=db,
        outbox=outbox,
        config_dir=config_dir,
        account_reader=_account_a,
    )
    preview = await importer.preview()
    request = ClaudeHistoryImportRequest.model_validate(
        {
            "provider_account_key": preview["provider_account_key"],
            "sessions": [
                {
                    "session_id": session_id,
                    "source_revision": preview["sessions"][0]["source_revision"],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="reuses entry UUID"):
        await importer.import_selected(request)
    assert await outbox.pending_count() == 0
