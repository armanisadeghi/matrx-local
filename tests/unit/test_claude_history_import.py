from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.services.coding_sessions.claude_history import (
    ACCOUNT_KEY_VERSION,
    ClaudeHistoryConflict,
    ClaudeHistoryImporter,
    ClaudeHistoryImportRequest,
    _AccountSnapshot,
    account_label,
    derive_account_key,
    mask_email,
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
    project_dir_name: str = "-private-project",
) -> Path:
    project = config_dir / "projects" / project_dir_name
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
    return _AccountSnapshot(
        True, "a" * 64, "a" * 12, "2.1.228", None, account_label="a***n@t***.com"
    )


async def _account_b() -> _AccountSnapshot:
    return _AccountSnapshot(
        True, "b" * 64, "b" * 12, "2.1.228", None, account_label="o***r@e***.com"
    )


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
async def test_preview_discloses_scope_without_paths_or_account_pii(
    importer_env,
) -> None:
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
    assert preview["provider_account_key_version"] == ACCOUNT_KEY_VERSION
    assert preview["provider_account_label"] == "a***n@t***.com"
    serialized = json.dumps(preview)
    assert "/private/company" not in serialized
    assert "private prompt" not in serialized
    # Only the masked display label may carry an "@"; never a raw address.
    assert "@" not in serialized.replace("a***n@t***.com", "")


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
                    "provider_project_key": preview["sessions"][0]["project_key"],
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
    assert first["queued_label_updates"] == 1
    assert replay["queued_label_updates"] == 0
    rows = await db.fetchall(
        """SELECT outbox.envelope_json, metadata.enqueue_origin
           FROM coding_session_bridge_outbox AS outbox
           JOIN coding_session_bridge_queue_metadata AS metadata
             ON metadata.receipt_id = outbox.id
           ORDER BY outbox.id"""
    )
    assert {row["enqueue_origin"] for row in rows} == {"explicit_history"}
    all_envelopes = [
        BridgeRequest.model_validate_json(row["envelope_json"]) for row in rows
    ]
    envelopes = [item for item in all_envelopes if item.action.value == "append_native"]
    # The import also queues Claude's own labels as one metadata-plane
    # observation per session, behind the transcript batches that mint the
    # binding. It is never an append_native copy and carries no entries.
    labels = [item for item in all_envelopes if item.action.value == "observe_hook"]
    assert len(labels) == 1
    assert labels[0].hook_event is not None
    assert labels[0].hook_event.name == "SessionMetadata"
    assert labels[0].entries == []
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
    metadata = envelopes[0].source_metadata
    assert metadata is not None
    assert metadata.provider_account_key == "a" * 64
    assert metadata.provider_account_key_version == ACCOUNT_KEY_VERSION
    assert metadata.provider_account_fingerprint == "a" * 12
    assert metadata.provider_account_label == "a***n@t***.com"
    # Only the masked display label may carry an "@"; never a raw address.
    assert "@" not in serialized.replace("a***n@t***.com", "")


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
                "provider_project_key": preview["sessions"][0]["project_key"],
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
        await switched.import_selected(
            ClaudeHistoryImportRequest.model_validate(payload)
        )
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
        await importer.import_selected(
            ClaudeHistoryImportRequest.model_validate(payload)
        )
    assert await outbox.pending_count() == 0


@pytest.mark.anyio
async def test_account_switch_during_bounded_read_does_not_enqueue(
    importer_env,
) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    _write_session(
        config_dir,
        session_id=session_id,
        records=[{"type": "user", "message": {"content": "hello"}}],
    )
    preview = await ClaudeHistoryImporter(
        db=db, outbox=outbox, config_dir=config_dir, account_reader=_account_a
    ).preview()
    calls = 0

    async def switching_account() -> _AccountSnapshot:
        nonlocal calls
        calls += 1
        return await (_account_a() if calls == 1 else _account_b())

    importer = ClaudeHistoryImporter(
        db=db,
        outbox=outbox,
        config_dir=config_dir,
        account_reader=switching_account,
    )
    item = preview["sessions"][0]
    with pytest.raises(ClaudeHistoryConflict, match="changed while history"):
        await importer.import_selected(
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
    assert await outbox.pending_count() == 0


@pytest.mark.anyio
async def test_reused_provider_uuid_is_rejected_without_partial_outbox(
    importer_env,
) -> None:
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
                    "provider_project_key": preview["sessions"][0]["project_key"],
                    "source_revision": preview["sessions"][0]["source_revision"],
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="reuses entry UUID"):
        await importer.import_selected(request)
    assert await outbox.pending_count() == 0


@pytest.mark.anyio
async def test_transcript_and_label_enqueue_roll_back_as_one_transaction(
    importer_env,
) -> None:
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
                "message": {"role": "user", "content": "Atomic label import"},
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
    item = preview["sessions"][0]
    await db.execute(
        """CREATE TRIGGER reject_session_metadata
           BEFORE INSERT ON coding_session_bridge_outbox
           WHEN json_extract(NEW.envelope_json, '$.hook_event.name') = 'SessionMetadata'
           BEGIN
             SELECT RAISE(ABORT, 'metadata enqueue rejected');
           END"""
    )
    await db.commit()

    with pytest.raises(sqlite3.IntegrityError, match="metadata enqueue rejected"):
        await importer.import_selected(
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

    assert await outbox.pending_count() == 0
    assert (
        await db.fetchall("SELECT * FROM coding_session_bridge_delivery_activity") == []
    )


@pytest.mark.anyio
async def test_same_size_same_mtime_replacement_fails_revision_gate(
    importer_env,
) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    path = _write_session(
        config_dir,
        session_id=session_id,
        records=[{"type": "user", "message": {"content": "alpha"}}],
    )
    importer = ClaudeHistoryImporter(
        db=db, outbox=outbox, config_dir=config_dir, account_reader=_account_a
    )
    preview = await importer.preview()
    item = preview["sessions"][0]
    original = path.read_bytes()
    prior = path.stat()
    replacement = original.replace(b"alpha", b"bravo")
    assert len(replacement) == len(original)
    path.write_bytes(replacement)
    os.utime(path, ns=(prior.st_atime_ns, prior.st_mtime_ns))

    with pytest.raises(ClaudeHistoryConflict, match="changed after preview"):
        await importer.import_selected(
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
    assert await outbox.pending_count() == 0


@pytest.mark.anyio
async def test_giant_unterminated_line_is_bounded_and_not_enqueued(
    importer_env,
) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    _write_session(
        config_dir,
        session_id=session_id,
        records=[b"{" + b"x" * (2_097_152 + 200_000)],
    )
    importer = ClaudeHistoryImporter(
        db=db, outbox=outbox, config_dir=config_dir, account_reader=_account_a
    )
    preview = await importer.preview()
    item = preview["sessions"][0]
    with pytest.raises(ValueError, match="has no valid entries"):
        await importer.import_selected(
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
    assert await outbox.pending_count() == 0


@pytest.mark.anyio
async def test_preview_summary_tail_with_unterminated_giant_line_is_bounded(
    importer_env,
) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    path = _write_session(
        config_dir,
        session_id=session_id,
        records=[
            {"type": "user", "message": {"content": "hello"}},
            b"{" + b"x" * 500_000,
        ],
    )
    preview = await ClaudeHistoryImporter(
        db=db, outbox=outbox, config_dir=config_dir, account_reader=_account_a
    ).preview()
    assert preview["sessions"][0]["session_id"] == session_id
    assert preview["sessions"][0]["bytes"] == path.stat().st_size
    assert preview["sessions"][0]["title"].startswith("Claude session")


@pytest.mark.anyio
async def test_symlinked_sources_are_never_discovered(
    importer_env, tmp_path: Path
) -> None:
    config_dir, db, outbox = importer_env
    project = config_dir / "projects" / "-private-project"
    project.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(_line({"type": "user", "message": {"content": "secret"}}))
    (project / f"{uuid4()}.jsonl").symlink_to(outside)

    preview = await ClaudeHistoryImporter(
        db=db, outbox=outbox, config_dir=config_dir, account_reader=_account_a
    ).preview()
    assert preview["totals"]["session_count"] == 0
    assert "secret" not in json.dumps(preview)


@pytest.mark.anyio
async def test_same_uuid_across_projects_keeps_project_identity(importer_env) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    for project, content in (("-project-a", "alpha"), ("-project-b", "bravo")):
        _write_session(
            config_dir,
            session_id=session_id,
            project_dir_name=project,
            records=[{"type": "user", "message": {"content": content}}],
        )
    importer = ClaudeHistoryImporter(
        db=db, outbox=outbox, config_dir=config_dir, account_reader=_account_a
    )
    preview = await importer.preview()
    matches = [item for item in preview["sessions"] if item["session_id"] == session_id]
    assert len(matches) == 2
    assert len({item["project_key"] for item in matches}) == 2

    for item in matches:
        await importer.import_selected(
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
    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    all_envelopes = [
        BridgeRequest.model_validate_json(row["envelope_json"]) for row in rows
    ]
    envelopes = [item for item in all_envelopes if item.action.value == "append_native"]
    assert {item.provider_project_key for item in all_envelopes} == {
        match["project_key"] for match in matches
    }
    # Both the transcript copies and the label observations keep the two
    # same-UUID projects apart under distinct bridge session identities.
    assert len({item.provider_session_id for item in envelopes}) == 2
    labels = [item for item in all_envelopes if item.action.value == "observe_hook"]
    assert len({item.provider_session_id for item in labels}) == 2
    assert all(
        item.source_metadata
        and str(item.source_metadata.provider_native_session_id) == session_id
        for item in envelopes
    )


def test_account_key_is_deterministic_across_machines() -> None:
    fields = {
        "api_provider": "firstParty",
        "auth_method": "claude.ai",
        "org_id": "e883f812-239f-4dd8-b03e-bee73ca21fc3",
        "email": "user@example.com",
    }
    key = derive_account_key(**fields)
    # No installation secret participates: any machine derives the same key.
    assert key == derive_account_key(**fields)
    assert len(key) == 64 and int(key, 16) >= 0
    assert key != derive_account_key(**{**fields, "email": "other@example.com"})
    assert key != derive_account_key(**{**fields, "org_id": None})
    # Field boundaries are delimited: shifting characters between fields differs.
    assert derive_account_key(
        api_provider="ab", auth_method="c", org_id=None, email=None
    ) != derive_account_key(api_provider="a", auth_method="bc", org_id=None, email=None)


def test_account_label_is_masked_and_never_a_raw_email() -> None:
    assert mask_email("arman@titaniumsuccess.com") == "a***n@t***.com"
    assert mask_email("x@y.io") == "x***@y***.io"
    assert mask_email("not-an-email") is None
    assert mask_email("user@nodot") is None
    assert account_label(email="arman@titaniumsuccess.com", org_id="e883f812-239f") == (
        "a***n@t***.com"
    )
    assert account_label(email=None, org_id="e883f812-239f-4dd8") == "org:e883f812"
    assert account_label(email=None, org_id=None) is None


@pytest.mark.anyio
async def test_discard_pending_history_preserves_hook_events(importer_env) -> None:
    config_dir, db, outbox = importer_env
    session_id = str(uuid4())
    _write_session(
        config_dir,
        session_id=session_id,
        records=[{"type": "user", "message": {"content": "hello"}}],
    )
    importer = ClaudeHistoryImporter(
        db=db, outbox=outbox, config_dir=config_dir, account_reader=_account_a
    )
    preview = await importer.preview()
    item = preview["sessions"][0]
    imported = await importer.import_selected(
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
    await outbox.enqueue(
        BridgeRequest.model_validate(
            {
                "action": "observe_hook",
                "provider": "claude_code",
                "provider_session_id": str(uuid4()),
                "origin": "independent_hook",
                "hook_event": {
                    "name": "Stop",
                    "stable_event_id": "keep-this-hook",
                    "payload": {"reason": "complete"},
                },
            }
        )
    )
    status = await importer.status()
    assert status["pending_history_imports"] == imported["queued_batches"]

    # Discarding queued transcript COPIES drops only the append_native rows.
    # The independent hook event AND the session's label observation survive —
    # a label update is metadata-plane, cheap and idempotent, and must never be
    # collateral damage of abandoning a byte copy.
    discarded = await importer.discard_pending()
    assert discarded == {
        "schema_version": 1,
        "source": "claude_local_jsonl",
        "discarded": imported["queued_batches"],
        "pending": 2,
    }
    remaining = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    assert len(remaining) == 2
    assert [json.loads(row["envelope_json"])["action"] for row in remaining] == [
        "observe_hook",
        "observe_hook",
    ]
    assert {
        json.loads(row["envelope_json"])["hook_event"]["name"] for row in remaining
    } == {"SessionMetadata", "Stop"}
