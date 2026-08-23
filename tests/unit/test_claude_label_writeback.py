"""The RETURN direction: an AI Matrx rename must reach Claude Code's own label.

Arman's ruling (2026-08-16): *"The Claude Code title is what we should use for
our label. And when our conversations go to Claude Code, or if I update this,
then the Claude Code value should be updated to match."*

These tests pin the half that writes into ANOTHER application's data, so most
of them are about what the writer REFUSES to do.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.services.coding_sessions import claude_label_writer
from app.services.coding_sessions.claude_label_writer import ClaudeSessionIndexWriter
from app.services.coding_sessions.claude_session_index import read_session_index
from app.services.coding_sessions.service import CodingSessionBridgeOutbox
from app.services.coding_sessions.title_sync import ClaudeSessionMetadataReconciler
from app.services.local_db.database import LocalDatabase

# Every serialization Claude Code's own writers have produced.
SERIALIZERS = (
    {"separators": (", ", ": "), "ensure_ascii": True},
    {"separators": (", ", ": "), "ensure_ascii": False},
    {"separators": (",", ":"), "ensure_ascii": False},
)


def _record(cli_session_id: str, **fields: Any) -> dict[str, Any]:
    """A record shaped like the real ones, in Claude's own key order."""
    record: dict[str, Any] = {
        "sessionId": f"local_{uuid4()}",
        "cliSessionId": cli_session_id,
        "cwd": "/Users/someone/code/matrx-local",
        "originCwd": "/Users/someone/code/matrx-local",
        "createdAt": 1786921049020,
        "lastActivityAt": 1786921051893,
        "model": "claude-opus-5",
        "isArchived": False,
        "title": "Claude's own label — with an em dash",
        "permissionMode": "auto",
        "bridgeSessionIds": ["session_01TgqEhPbRErQqiwsRGuQjuo"],
        "spawnSeed": {},
    }
    record.update(fields)
    return record


def _write_record(
    root: Path,
    record: dict[str, Any],
    *,
    account: str = "acct-1",
    org: str = "org-1",
    serializer: dict[str, Any] | None = None,
) -> Path:
    folder = root / account / org
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{record['sessionId']}.json"
    path.write_bytes(json.dumps(record, **(serializer or SERIALIZERS[0])).encode("utf-8"))
    return path


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


class _FakeClient:
    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        self._sessions = sessions

    async def get(self, path: str, jwt: str | None = None) -> Any:
        return {
            "schema_version": 2,
            "provider": "claude_code",
            "sessions": self._sessions,
            "total_count": len(self._sessions),
            "page_count": len(self._sessions),
            "has_more": False,
            "complete": True,
            "next_cursor": None,
        }


async def _pending_payloads(db: LocalDatabase) -> list[dict[str, Any]]:
    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    return [json.loads(row["envelope_json"]) for row in rows]


# --------------------------------------------------------------------------
# The writer: two fields, byte-for-byte, atomic, fenced, backed up.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("serializer", SERIALIZERS)
def test_write_changes_only_title_and_title_source_byte_for_byte(
    tmp_path: Path, serializer: dict[str, Any]
) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    record = _record(session_id)
    path = _write_record(root, record, serializer=serializer)
    before = path.read_bytes()

    writer = ClaudeSessionIndexWriter(backup_root=tmp_path / "backups")
    result = writer.write_title(
        cli_session_id=session_id, title="Renamed in AI Matrx", record_paths=(path,)
    )

    assert result.applied and result.written == 1
    after = json.loads(path.read_bytes())
    assert after["title"] == "Renamed in AI Matrx"
    assert after["titleSource"] == "user"
    # Everything else survives, values AND key order, with `titleSource`
    # inserted exactly where Claude Code's own rename puts it.
    original = json.loads(before)
    assert {k: v for k, v in after.items() if k not in {"title", "titleSource"}} == {
        k: v for k, v in original.items() if k != "title"
    }
    assert list(after) == [
        *list(original)[: list(original).index("title") + 1],
        "titleSource",
        *list(original)[list(original).index("title") + 1 :],
    ]
    # The file is re-serialized the same way Claude wrote it: only the two
    # changed values differ from the original bytes.
    rewritten = json.dumps(after, **serializer).encode("utf-8")
    assert path.read_bytes() == rewritten


def test_write_refuses_a_serialization_it_has_never_seen(tmp_path: Path) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    folder = root / "acct" / "org"
    folder.mkdir(parents=True)
    path = folder / "local_pretty.json"
    # Pretty-printed: reproducible by none of the known serializers.
    path.write_bytes(json.dumps(_record(session_id), indent=2).encode("utf-8"))
    before = path.read_bytes()

    result = ClaudeSessionIndexWriter(backup_root=tmp_path / "b").write_title(
        cli_session_id=session_id, title="New title", record_paths=(path,)
    )

    assert not result.applied
    assert result.summary()["refusal_reasons"] == ["unknown_format"]
    assert path.read_bytes() == before


def test_write_refuses_when_claude_code_moved_the_file_underneath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(session_id))
    real_fence = claude_label_writer._fence
    seen: list[int] = []

    def racing_fence(target: Path) -> tuple[int, int] | None:
        seen.append(1)
        if len(seen) > 1:
            # Claude Code rewrote the record between our read and our rename.
            target.write_bytes(
                json.dumps(_record(session_id, title="Claude won the race")).encode()
            )
        return real_fence(target)

    monkeypatch.setattr(claude_label_writer, "_fence", racing_fence)
    result = ClaudeSessionIndexWriter(backup_root=tmp_path / "b").write_title(
        cli_session_id=session_id, title="Renamed in AI Matrx", record_paths=(path,)
    )

    assert not result.applied
    assert result.summary()["refusal_reasons"] == ["concurrent_modification"]
    # Claude's value stands; the next inbound pass will pull it up.
    assert json.loads(path.read_bytes())["title"] == "Claude won the race"
    assert not list((tmp_path / "claude-code-sessions").rglob(".matrx-*"))


def test_write_refuses_when_the_record_now_names_a_different_session(
    tmp_path: Path,
) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(session_id))
    path.write_bytes(json.dumps(_record(str(uuid4()))).encode("utf-8"))
    before = path.read_bytes()

    result = ClaudeSessionIndexWriter(backup_root=tmp_path / "b").write_title(
        cli_session_id=session_id, title="New title", record_paths=(path,)
    )

    assert result.summary()["refusal_reasons"] == ["identity_changed"]
    assert path.read_bytes() == before


def test_backup_holds_the_bytes_from_before_ai_matrx_ever_wrote(tmp_path: Path) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(session_id))
    pristine = path.read_bytes()
    backups = tmp_path / "backups"
    writer = ClaudeSessionIndexWriter(backup_root=backups)

    writer.write_title(
        cli_session_id=session_id, title="First rename", record_paths=(path,)
    )
    writer.write_title(
        cli_session_id=session_id, title="Second rename", record_paths=(path,)
    )

    saved = list(backups.iterdir())
    assert len(saved) == 1
    # A later write never overwrites the snapshot — it is the file as it was
    # before AI Matrx touched it, not the previous version.
    assert saved[0].read_bytes() == pristine
    assert json.loads(path.read_bytes())["title"] == "Second rename"


def test_write_updates_every_account_copy_of_the_same_session(tmp_path: Path) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    shared = _record(session_id)
    paths = tuple(
        _write_record(root, shared, account=f"acct-{n}", serializer=SERIALIZERS[n % 3])
        for n in range(5)
    )

    result = ClaudeSessionIndexWriter(backup_root=tmp_path / "b").write_title(
        cli_session_id=session_id, title="One label everywhere", record_paths=paths
    )

    assert result.written == 5 and result.applied
    assert {json.loads(p.read_bytes())["title"] for p in paths} == {
        "One label everywhere"
    }


def test_write_is_a_no_op_when_the_label_already_matches(tmp_path: Path) -> None:
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(session_id, title="Same", titleSource="user"))
    before = path.read_bytes()

    result = ClaudeSessionIndexWriter(backup_root=tmp_path / "b").write_title(
        cli_session_id=session_id, title="Same", record_paths=(path,)
    )

    assert result.applied and result.written == 0 and result.unchanged == 1
    assert path.read_bytes() == before
    assert not (tmp_path / "b").exists()


def test_write_refuses_an_empty_title(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ClaudeSessionIndexWriter(backup_root=tmp_path / "b").write_title(
            cli_session_id=str(uuid4()), title="   ", record_paths=()
        )


# --------------------------------------------------------------------------
# The index reader's tie-break — observed against the real app 2026-08-16.
# --------------------------------------------------------------------------


def test_freshest_record_wins_when_last_activity_ties(tmp_path: Path) -> None:
    """Claude renames only the active account's copy, without bumping activity.

    Every sibling copy then reports the SAME ``lastActivityAt``, so only the
    file's own mtime separates the renamed record from four stale ones.
    """
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    stale = [
        _write_record(
            root, _record(session_id, title="Stale label"), account=f"acct-{n}"
        )
        for n in range(4)
    ]
    # Sorts LAST, so a first-read tie-break would pick a stale sibling.
    fresh = _write_record(
        root, _record(session_id, title="Renamed here", titleSource="user"), account="zz"
    )
    for path in stale:
        os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    os.utime(fresh, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))

    entries, _ = read_session_index(root)

    assert entries[session_id].title == "Renamed here"
    # And the writer is handed every copy, so no stale sibling can win later.
    assert set(entries[session_id].record_paths) == {*stale, fresh}


# --------------------------------------------------------------------------
# The reconciler: which renames travel down, and which stand down.
# --------------------------------------------------------------------------


def _identity(session_id: str, **fields: Any) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "provider_session_id": session_id,
        "provider_project_key": "claude-local:abc",
        "conversation_id": str(uuid4()),
        "fidelity": "event_mirror",
        "last_seen_at": "2026-08-16T00:00:00Z",
        "conversation_title": "Renamed in AI Matrx",
        "title_source": "user",
        "claude_title": "Claude's own label — with an em dash",
    }
    identity.update(fields)
    return identity


def _reconciler(db, outbox, tmp_path, identities):
    root = tmp_path / "claude-code-sessions"
    return ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient(identities),
        index_reader=lambda: read_session_index(root),
        writer=ClaudeSessionIndexWriter(backup_root=tmp_path / "backups"),
    )


@pytest.mark.anyio
async def test_ai_matrx_rename_reaches_claudes_own_label_and_reports_it_back(
    env,
) -> None:
    db, outbox, tmp_path = env
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(session_id))

    result = await _reconciler(db, outbox, tmp_path, [_identity(session_id)]).sync()

    assert result["push_down"]["written"] == 1
    assert result["push_down"]["refused"] == 0
    assert json.loads(path.read_bytes())["title"] == "Renamed in AI Matrx"
    assert json.loads(path.read_bytes())["titleSource"] == "user"
    # The server is told the agreed label, marked as the user's, so its
    # `applied_title` converges and a later Claude rename can still win.
    payloads = [
        envelope["hook_event"]["payload"]
        for envelope in await _pending_payloads(db)
        if envelope["hook_event"]["payload"].get("title_origin")
    ]
    assert payloads == [
        {"title": "Renamed in AI Matrx", "title_origin": "ai_matrx_user"}
    ]

    # Second pass: both sides agree, so nothing is written and nothing is sent.
    again = await _reconciler(db, outbox, tmp_path, [_identity(session_id)]).sync()
    assert again["push_down"]["written"] == 0
    assert again["push_down"]["already_identical"] == 1


@pytest.mark.anyio
async def test_only_a_user_titled_session_travels_down(env) -> None:
    db, outbox, tmp_path = env
    provider_titled = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(provider_titled))
    before = path.read_bytes()

    result = await _reconciler(
        db,
        outbox,
        tmp_path,
        [_identity(provider_titled, title_source="provider")],
    ).sync()

    assert result["push_down"]["written"] == 0
    assert path.read_bytes() == before


@pytest.mark.anyio
async def test_a_session_ai_matrx_does_not_own_is_never_written(env) -> None:
    """The server list is the allowlist for the write, not just the read."""
    db, outbox, tmp_path = env
    unowned = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(unowned, title="Private local session"))
    before = path.read_bytes()

    result = await _reconciler(db, outbox, tmp_path, []).sync()

    assert result["push_down"]["written"] == 0
    assert result["push_down"]["user_titled_sessions"] == 0
    assert path.read_bytes() == before


@pytest.mark.anyio
async def test_claude_code_wins_when_both_sides_were_renamed(env) -> None:
    db, outbox, tmp_path = env
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(session_id, title="Renamed in Claude Code"))

    result = await _reconciler(db, outbox, tmp_path, [_identity(session_id)]).sync()

    # Claude's new label is on its way up; the return direction stands down so
    # the two do not fight inside one pass.
    assert result["queued"] == 1
    assert result["push_down"]["written"] == 0
    assert result["push_down"]["deferred_to_claude"] == 1
    assert json.loads(path.read_bytes())["title"] == "Renamed in Claude Code"


@pytest.mark.anyio
async def test_claude_overwriting_our_write_is_not_fought_over(env) -> None:
    db, outbox, tmp_path = env
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(session_id))
    reconciler = _reconciler(db, outbox, tmp_path, [_identity(session_id)])
    await reconciler.sync()
    assert json.loads(path.read_bytes())["title"] == "Renamed in AI Matrx"

    # Claude Code reloads and flushes its own cached label back over ours.
    path.write_bytes(json.dumps(_record(session_id, title="Claude's cached label")).encode())
    result = await _reconciler(db, outbox, tmp_path, [_identity(session_id)]).sync()

    # We already wrote exactly this title once; Claude's value is newer, so it
    # stands and the inbound leg carries it up instead.
    assert result["push_down"]["written"] == 0
    assert json.loads(path.read_bytes())["title"] == "Claude's cached label"


@pytest.mark.anyio
async def test_dry_run_never_opens_another_applications_files(env) -> None:
    db, outbox, tmp_path = env
    session_id = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    path = _write_record(root, _record(session_id))
    before = path.read_bytes()

    result = await _reconciler(db, outbox, tmp_path, [_identity(session_id)]).sync(
        dry_run=True
    )

    assert result["push_down"]["written"] == 0
    assert result["push_down"]["sample_titles"][0]["title"] == "Renamed in AI Matrx"
    assert path.read_bytes() == before
    assert await _pending_payloads(db) == []
