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
    blocked_sentence,
    cloud_disagrees,
    payload_digest,
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
    # A machine's session index is only readable in the scope the app says it
    # is signed into, and the app says so in its own plain files beside the
    # index tree — never by a per-record timestamp, which IS copied between
    # scopes (see app/services/coding_sessions/claude_scope.py). A fixture
    # that writes records but no such statement is a machine whose pins are
    # UNKNOWN, which is why this helper writes both.
    app_support = root.parent
    app_support.mkdir(parents=True, exist_ok=True)
    (app_support / "config.json").write_text(
        json.dumps({"lastKnownAccountUuid": account})
    )
    (app_support / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": account})
    )
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
        True, "a" * 64, "a" * 12, "2.1.228", None, account_label="arman@titaniumsuccess.com"
    )


class _FakeClient:
    def __init__(self, sessions: list[dict[str, Any]] | Exception) -> None:
        self._sessions = sessions
        self.calls: list[str] = []

    async def get(self, path: str, jwt: str | None = None) -> Any:
        self.calls.append(path)
        if isinstance(self._sessions, Exception):
            raise self._sessions
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
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    try:
        from app.services import sync_client

        class _Daemon:
            async def access_grant(self) -> tuple[str, str]:
                return (
                    "eyJhbGciOiJub25lIn0.eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMDAwMDAwMDEiLCJleHAiOjQxMDI0NDQ4MDB9.signature",
                    "00000000-0000-4000-8000-000000000001",
                )

        monkeypatch.setattr(sync_client, "get_sync_client", _Daemon)
        outbox = CodingSessionBridgeOutbox(db=db, cloud_enabled=False)
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
    # Exact cwd remains local-only for native resume; it never becomes payload.
    assert entry.local_cwd == Path("/Users/someone/code/matrx-frontend")
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
    assert result["queued"] == 0
    assert result["detected"] == 1
    assert result["comparisons"][0]["state"] == "detected"
    assert result["operation"]["mode"] == "preview"
    assert {item["field"] for item in result["comparisons"][0]["comparisons"]} == {
        "title",
        "project_name",
        "git_branch",
        "worktree_name",
        "is_archived",
        "is_pinned",
        "pinned_rank",
        "category",
    }
    project_comparison = next(
        item
        for item in result["comparisons"][0]["comparisons"]
        if item["field"] == "project_name"
    )
    assert project_comparison["local_observed"] is False
    assert project_comparison["equal"] is False
    operation_rows = await db.fetchall(
        """SELECT state, action FROM coding_session_metadata_sync_rows
           WHERE operation_id=?""",
        (result["operation_id"],),
    )
    assert [(row["state"], row["action"]) for row in operation_rows] == [
        ("detected", "observe_ai_matrx")
    ]
    assert result["sample_titles"][0]["title"] == "Planned label"
    assert await db.fetchall("SELECT id FROM coding_session_bridge_outbox") == []


@pytest.mark.anyio
async def test_sync_blocks_loudly_instead_of_half_running(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    from app.services import sync_client

    class _SignedOutDaemon:
        async def access_grant(self) -> None:
            return None

    monkeypatch.setattr(sync_client, "get_sync_client", _SignedOutDaemon)
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


@pytest.mark.anyio
async def test_unpin_reaches_a_claude_sdk_composite_binding(env) -> None:
    """A composite binding's stale pin must be clearable — MXL-D-093.

    The live server holds Claude Code bindings under two ``provider_session_id``
    shapes: the bare ``cliSessionId`` the event mirror writes, and the
    ``claude-sdk:<project digest>:<b64 cliSessionId>`` composite a native import
    writes. Measured 2026-09-17 against the live DB, 43 of the 76 pinned
    composite rows were for conversations Arman had already unpinned in Claude
    Code, and 6 truly-pinned ones carried no pin.

    The resolution that makes those reconcilable is ``raw_session_id`` decoding
    the composite's last segment back to the exact ``cliSessionId`` the desktop
    index is keyed by. NOTHING covered it, so the reconciler was believed unable
    to match a composite at all. This is that cover: a composite identity whose
    conversation the app has UNPINNED must queue an observation carrying
    ``is_pinned: false`` — addressed to the composite id, because that is the
    row the server has to clear.
    """
    db, outbox, tmp_path = env
    root = tmp_path / "claude-code-sessions"
    unpinned = str(uuid4())
    still_pinned = str(uuid4())
    # One signed-in scope, the app's own pin field on each record.
    _write_index_record(
        root,
        cli_session_id=unpinned,
        title="Stale composite pin",
        lastActivityAt=20,
        lastFocusedAt=1789684595177,
        isStarred=False,
    )
    _write_index_record(
        root,
        cli_session_id=still_pinned,
        title="Genuinely pinned",
        lastActivityAt=21,
        lastFocusedAt=1789684595100,
        isStarred=True,
    )
    project_key = "claude-local:matrx-local"
    composites = {
        session: _bridge_provider_session_id(project_key, session)
        for session in (unpinned, still_pinned)
    }
    for session, composite in composites.items():
        # The decode is the whole resolution: it must be exact, not approximate.
        assert raw_session_id(composite) == session
        assert composite.startswith("claude-sdk:")

    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient(
            [
                {"provider_session_id": composite,
                 "provider_project_key": project_key}
                for composite in composites.values()
            ]
        ),
        index_reader=lambda: read_session_index(
            root, ledger_path=tmp_path / "no-ledger.json"
        ),
    )
    result = await reconciler.sync()
    assert result["matched"] == 2, result
    assert result["unmatched"] == 0, result
    assert result["queued"] == 2, result

    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    queued = {}
    for row in rows:
        envelope = BridgeRequest.model_validate_json(row["envelope_json"])
        assert envelope.hook_event is not None
        queued[envelope.provider_session_id] = envelope.hook_event.payload
    assert set(queued) == set(composites.values())
    assert queued[composites[unpinned]]["is_pinned"] is False
    assert queued[composites[still_pinned]]["is_pinned"] is True


# ---------------------------------------------------------------------------
# 2026-09-18 (CS-33 / F2): the pass could not heal a divergence it had caused
#
# A zero-authorship verifier measured the live database: Claude Code's real
# pinned set was 218, the server held 160 distinct favourites — 116 pinned on
# this Mac and not favourite there, 58 favourite there and not pinned here —
# and no `is_favorite` anywhere had changed since 2026-09-15 18:49 UTC while
# the same bindings were updated every few minutes. Every pass walked the
# whole ledger and sent nothing, because the decision to speak compared the
# local payload to the digest of the payload LAST SENT: once a payload had
# left this Mac, the pass was permanently satisfied with whatever the server
# had ended up holding.
#
# So the gate now also compares against what AI Matrx demonstrably holds, and
# one pass sends every difference — both kinds.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_one_pass_sends_every_divergence_in_both_directions(env: Any) -> None:
    """Both divergence kinds, already acknowledged locally, in one pass.

    The local sent-digest row is written for every session BEFORE the pass, so
    the old gate has nothing to say about any of them. Only a comparison
    against AI Matrx's own values can find these.
    """
    db, outbox, tmp_path = env
    root = tmp_path / "claude-code-sessions"

    pinned_here_not_there = str(uuid4())  # the 116 class
    favourite_there_not_here = str(uuid4())  # the 58 class
    agreeing = str(uuid4())  # must stay silent

    _write_index_record(
        root,
        cli_session_id=pinned_here_not_there,
        title="Pinned here",
        lastActivityAt=30,
        lastFocusedAt=1789684595177,
        isStarred=True,
    )
    _write_index_record(
        root,
        cli_session_id=favourite_there_not_here,
        title="Unpinned here",
        lastActivityAt=31,
        lastFocusedAt=1789684595176,
        isStarred=False,
    )
    _write_index_record(
        root,
        cli_session_id=agreeing,
        title="Agreed",
        lastActivityAt=32,
        lastFocusedAt=1789684595175,
        isStarred=True,
    )

    index_reader = lambda: read_session_index(  # noqa: E731
        root, ledger_path=tmp_path / "no-ledger.json"
    )
    entries, _totals = index_reader()

    # What AI Matrx is holding for each: the two divergences and one agreement.
    cloud = {
        pinned_here_not_there: {"ai_matrx_is_favorite": False},
        favourite_there_not_here: {"ai_matrx_is_favorite": True},
        agreeing: {"ai_matrx_is_favorite": True},
    }
    sessions = [
        {
            "provider_session_id": session,
            "provider_project_key": "claude-local:matrx-local",
            "claude_title": entries[session].title,
            **fields,
        }
        for session, fields in cloud.items()
    ]

    # Every one of them was already acknowledged locally — this is the state
    # the engine was really in, and why nothing ever moved again.
    for session in cloud:
        payload = entries[session].metadata_payload()
        await db.execute(
            """INSERT INTO claude_session_metadata_sent
                   (provider_session_id, payload_sha256, updated_at)
               VALUES (?, ?, datetime('now'))""",
            (session, payload_digest(payload)),
        )
    await db.commit()

    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient(sessions),
        index_reader=index_reader,
    )
    result = await reconciler.sync()

    assert result["matched"] == 3, result
    # One pass, both kinds, and nothing extra.
    assert result["pins"] == {
        "local": 2,
        "ai_matrx": 2,
        "to_pin": 1,
        "to_unpin": 1,
        "to_reconcile": 2,
    }, result["pins"]
    assert result["ai_matrx_out_of_step"] == 2, result
    assert result["ai_matrx_out_of_step_fields"].get("is_pinned") == 2

    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    queued: dict[str, Any] = {}
    for row in rows:
        envelope = BridgeRequest.model_validate_json(row["envelope_json"])
        assert envelope.hook_event is not None
        queued[envelope.provider_session_id] = envelope.hook_event.payload
    assert set(queued) == {pinned_here_not_there, favourite_there_not_here}, (
        "one pass must send exactly the divergences — no more, no fewer"
    )
    assert queued[pinned_here_not_there]["is_pinned"] is True
    assert queued[favourite_there_not_here]["is_pinned"] is False
    assert agreeing not in queued, "an agreeing session must stay silent"


@pytest.mark.anyio
async def test_a_field_ai_matrx_has_never_observed_is_not_a_divergence(
    env: Any,
) -> None:
    """Unknown is never false, on the cloud side of the comparison too.

    A server row that carries no pin opinion has not disagreed with this Mac;
    treating its absence as ``false`` would make every pass re-send every pin
    forever and drown the outbox.
    """
    db, outbox, tmp_path = env
    root = tmp_path / "claude-code-sessions"
    session = str(uuid4())
    _write_index_record(
        root,
        cli_session_id=session,
        title="Pinned here, unknown there",
        lastActivityAt=40,
        lastFocusedAt=1789684595177,
        isStarred=True,
    )
    index_reader = lambda: read_session_index(  # noqa: E731
        root, ledger_path=tmp_path / "no-ledger.json"
    )
    entries, _totals = index_reader()
    await db.execute(
        """INSERT INTO claude_session_metadata_sent
               (provider_session_id, payload_sha256, updated_at)
           VALUES (?, ?, datetime('now'))""",
        (session, payload_digest(entries[session].metadata_payload())),
    )
    await db.commit()

    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient(
            [
                {
                    "provider_session_id": session,
                    "provider_project_key": "claude-local:matrx-local",
                    "claude_title": entries[session].title,
                }
            ]
        ),
        index_reader=index_reader,
    )
    result = await reconciler.sync()
    assert result["ai_matrx_out_of_step"] == 0, result
    assert result["unchanged"] == 1, result
    rows = await db.fetchall("SELECT count(*) AS n FROM coding_session_bridge_outbox")
    assert int(rows[0]["n"]) == 0


@pytest.mark.anyio
async def test_the_status_line_states_the_pin_divergence_and_the_last_pass(
    env: Any,
) -> None:
    """The numbers the Sessions screen says out loud, from the pass's own rows.

    Before this, nothing on any screen mentioned that the server was 116/58
    out of step, so it sat there for three days. ``checked: false`` before any
    pass has run is part of the contract: an unread divergence must not render
    as a converged zero.
    """
    db, outbox, tmp_path = env
    root = tmp_path / "claude-code-sessions"
    pinned_here = str(uuid4())
    favourite_there = str(uuid4())
    _write_index_record(
        root,
        cli_session_id=pinned_here,
        title="Pinned here",
        lastActivityAt=50,
        lastFocusedAt=1789684595177,
        isStarred=True,
    )
    _write_index_record(
        root,
        cli_session_id=favourite_there,
        title="Unpinned here",
        lastActivityAt=51,
        lastFocusedAt=1789684595176,
        isStarred=False,
    )
    index_reader = lambda: read_session_index(  # noqa: E731
        root, ledger_path=tmp_path / "no-ledger.json"
    )
    entries, _totals = index_reader()
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient(
            [
                {
                    "provider_session_id": pinned_here,
                    "provider_project_key": "claude-local:matrx-local",
                    "claude_title": entries[pinned_here].title,
                    "ai_matrx_is_favorite": False,
                },
                {
                    "provider_session_id": favourite_there,
                    "provider_project_key": "claude-local:matrx-local",
                    "claude_title": entries[favourite_there].title,
                    "ai_matrx_is_favorite": True,
                },
            ]
        ),
        index_reader=index_reader,
    )

    before = await reconciler.pin_divergence()
    assert before["checked"] is False
    assert before["to_reconcile"] is None
    assert "no reconcile pass has finished" in before["reason"]

    await reconciler.sync()

    after = await reconciler.pin_divergence()
    assert after["checked"] is True
    assert after["local"] == 1
    assert after["ai_matrx"] == 1
    assert after["to_pin"] == 1
    assert after["to_unpin"] == 1
    assert after["to_reconcile"] == 2
    assert after["sessions_walked"] == 2
    assert after["last_pass_at"], "the screen must be able to say when"


# ---------------------------------------------------------------------------
# 2026-09-18 (CS-33, second pass): the row that reported a lie, and the
# endpoint that was too slow to report anything
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_blocked_pass_is_not_a_converged_zero(env: Any) -> None:
    """THE BUG, measured on a temp-home engine on 2026-09-18.

    A fresh engine home is not signed in, so the apply pass is blocked with
    ``no_active_user_jwt`` BEFORE it compares anything, and that is journalled
    as status ``failed`` with ``compared_sessions`` 0. ``pin_divergence`` keyed
    only on ``completed_at IS NOT NULL``, so it answered:

        {"checked": true, "local": 0, "ai_matrx": 0, "to_reconcile": 0}

    and the Sessions row said the pins AGREE when nothing had ever been
    compared. A pass that compared nothing is not evidence of anything.
    """
    db, outbox, tmp_path = env
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient([]),
        index_reader=lambda: ({}, {"files": 0, "records": 0, "unreadable": 0}),
    )
    await db.execute(
        """INSERT INTO coding_session_metadata_sync_operations (
               operation_id, mode, status, started_at, completed_at,
               compared_sessions, error_message
           ) VALUES ('op-blocked', 'apply', 'failed', '2026-09-18T15:06:00Z',
                     '2026-09-18T15:06:00Z', 0, 'no_active_user_jwt')"""
    )
    await db.commit()

    divergence = await reconciler.pin_divergence()
    assert divergence["checked"] is False, divergence
    assert divergence["to_reconcile"] is None
    assert divergence["local"] is None and divergence["ai_matrx"] is None
    # ...and it says WHY, in words a person can act on.
    assert divergence["reason"] == (
        "this Mac is not signed in to AI Matrx, so the last check could not "
        "ask what AI Matrx holds"
    )
    assert divergence["last_attempt_status"] == "failed"
    assert divergence["last_attempt_at"] == "2026-09-18T15:06:00Z"


@pytest.mark.anyio
async def test_a_completed_pass_that_compared_nothing_is_also_not_believed(
    env: Any,
) -> None:
    """Zero compared sessions is never a divergence of zero, however it ended."""
    db, outbox, tmp_path = env
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient([]),
        index_reader=lambda: ({}, {"files": 0, "records": 0, "unreadable": 0}),
    )
    await db.execute(
        """INSERT INTO coding_session_metadata_sync_operations (
               operation_id, mode, status, started_at, completed_at,
               compared_sessions
           ) VALUES ('op-empty', 'apply', 'completed', '2026-09-18T15:00:00Z',
                     '2026-09-18T15:00:00Z', 0)"""
    )
    await db.commit()
    divergence = await reconciler.pin_divergence()
    assert divergence["checked"] is False, divergence
    assert divergence["to_reconcile"] is None


@pytest.mark.anyio
async def test_every_blocked_reason_reaches_the_screen_in_english(env: Any) -> None:
    """No blocked reason may arrive as a bare machine token or an empty shrug."""
    for reason, fragment in (
        ("no_active_user_jwt", "not signed in to AI Matrx"),
        ("aidream_unreachable", "could not be reached"),
        ("aidream_server_unconfigured", "no AI Matrx server configured"),
        ("claude_index_incomplete", "could not be read completely"),
        ("identity_list_completeness_unavailable", "every conversation"),
        ("aidream_error:503 upstream", "503 upstream"),
    ):
        assert fragment in blocked_sentence(reason), reason
    # An unknown reason is SHOWN, never swallowed.
    assert "something_new" in blocked_sentence("something_new")
    assert "recorded no reason" in blocked_sentence(None)


@pytest.mark.anyio
async def test_status_never_refreshes_the_index(env: Any, monkeypatch: Any) -> None:
    """``status`` is a READ — the endpoint the Sessions screen polls.

    It used to call the reconciler's index reader, which refreshes the whole
    index first. Measured on this Mac's 79,206 record files (2026-09-18): 69.5 s
    cold and 7.8 s warm for the refresh, plus 7.2 s to probe every record path
    for writability — ~15 s warm and ~77 s cold, past the desktop client's 60 s
    timeout, so the pins row showed its "could not read" variant instead of
    numbers. The persisted snapshot answers the same question in 0.2 s.
    """
    db, outbox, tmp_path = env
    calls: list[str] = []

    def _reader() -> Any:
        calls.append("index_reader")
        return {}, {"files": 0, "records": 0, "unreadable": 0}

    reconciler = ClaudeSessionMetadataReconciler(
        db=db, outbox=outbox, client=_FakeClient([]), index_reader=_reader
    )
    import app.services.coding_sessions.title_sync as title_sync

    def _no_probe(_entries: Any) -> bool:
        raise AssertionError("status probed every record path for writability")

    monkeypatch.setattr(title_sync, "_record_paths_writable", _no_probe)
    await reconciler.status()
    assert calls == [], "status refreshed the index instead of reading it"


@pytest.mark.anyio
async def test_status_reports_writability_with_its_age_never_a_stale_false(
    env: Any,
) -> None:
    """Never measured is None, not False — a probe nobody ran is not a verdict."""
    db, outbox, tmp_path = env
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient([]),
        index_reader=lambda: ({}, {"files": 0, "records": 0, "unreadable": 0}),
    )
    fresh = await reconciler.status()
    assert fresh["index_writable"] is None
    assert fresh["index_writable_measured_at"] is None

    await db.execute(
        """INSERT INTO coding_session_metadata_sync_operations (
               operation_id, mode, status, started_at, completed_at,
               compared_sessions, index_writable, index_writable_probed
           ) VALUES ('op-w', 'apply', 'completed', '2026-09-18T15:00:00Z',
                     '2026-09-18T15:00:00Z', 3, 1, 1)"""
    )
    await db.commit()
    measured = await reconciler.status()
    assert measured["index_writable"] is True
    assert measured["index_writable_measured_at"] == "2026-09-18T15:00:00Z"


# ---------------------------------------------------------------------------
# 2026-09-18, V-CS-33: the comparison was against the bridge's own ECHO
#
# A zero-authorship verifier refuted the claim that this pass compares Claude's
# star against what AI Matrx holds. It compared `is_pinned` against
# `claude_is_pinned`, which aidream fills from `metadata["provider_pinned"]` —
# the value THIS MAC last reported. Comparing a value to its own echo can only
# report agreement. Live proof: binding `claude-sdk:31cc02c5…` carried
# `provider_pinned = true` while the owner's real
# `platform.user_entity_state.is_favorite` was `false`, a pass running the new
# code touched that row at 00:04:20, and the divergence was still open.
#
# The pin now compares against `ai_matrx_is_favorite` — the real favourite.
# These guards hold it there.
# ---------------------------------------------------------------------------


def test_the_pin_compares_against_the_favourite_not_the_provider_echo() -> None:
    """The mapping itself is the defect, so the mapping is asserted."""
    from app.services.coding_sessions.title_sync import (
        _CLOUD_DETAIL_KEYS,
    )

    assert _CLOUD_DETAIL_KEYS["is_pinned"] == "ai_matrx_is_favorite"
    assert PROVIDER_PIN_ECHO_KEY == "claude_is_pinned"
    assert "claude_is_pinned" not in _CLOUD_DETAIL_KEYS.values(), (
        "the pin is being compared against the provider's own echo again"
    )


@pytest.mark.anyio
async def test_the_echo_alone_can_never_produce_a_divergence(env: Any) -> None:
    """THE LIVE ROW, as a fixture: echo says pinned, real favourite is false.

    A server that sends only the echo (an older aidream) must leave the pin
    UNKNOWN — not agreeing, and not disagreeing either. Before this fix the
    engine read the echo, saw its own `true` reflected back at it, and stayed
    silent while the favourite was `false`.
    """
    db, outbox, tmp_path = env
    session = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    _write_index_record(
        root,
        cli_session_id=session,
        title="Pinned here, echo agrees, favourite does not",
        lastActivityAt=60,
        lastFocusedAt=1789684595177,
        isStarred=True,
    )
    index_reader = lambda: read_session_index(  # noqa: E731
        root, ledger_path=tmp_path / "no-ledger.json"
    )
    entries, _totals = index_reader()
    local = entries[session].metadata_payload()

    # Echo only — exactly what the server sent before the fix.
    echo_only = {"claude_is_pinned": True}
    assert cloud_disagrees(local, _cloud_detail_values(echo_only)) == [], (
        "the echo must not be read as AI Matrx's opinion at all"
    )

    # The same row with the REAL favourite present: a disagreement, at last.
    truthful = {"claude_is_pinned": True, "ai_matrx_is_favorite": False}
    assert cloud_disagrees(local, _cloud_detail_values(truthful)) == ["is_pinned"]


@pytest.mark.anyio
async def test_the_live_refuted_row_now_produces_an_unpin_free_pin(env: Any) -> None:
    """One pass must now SEND the pin for the row that sat open all day.

    Shape taken from the live binding: pinned in Claude, echo already `true`
    (so the old local-digest gate had nothing to say either), real favourite
    `false`. Both gates were silent; one of them must not be.
    """
    db, outbox, tmp_path = env
    session = str(uuid4())
    root = tmp_path / "claude-code-sessions"
    _write_index_record(
        root,
        cli_session_id=session,
        title="The live row",
        lastActivityAt=61,
        lastFocusedAt=1789684595177,
        isStarred=True,
    )
    index_reader = lambda: read_session_index(  # noqa: E731
        root, ledger_path=tmp_path / "no-ledger.json"
    )
    entries, _totals = index_reader()
    composite = _bridge_provider_session_id("claude-local:matrx-local", session)
    # Already acknowledged locally — the echo matched, so nothing "changed".
    await db.execute(
        """INSERT INTO claude_session_metadata_sent
               (provider_session_id, payload_sha256, updated_at)
           VALUES (?, ?, datetime('now'))""",
        (composite, payload_digest(entries[session].metadata_payload())),
    )
    await db.commit()

    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient(
            [
                {
                    "provider_session_id": composite,
                    "provider_project_key": "claude-local:matrx-local",
                    "claude_title": entries[session].title,
                    "claude_is_pinned": True,  # the echo
                    "ai_matrx_is_favorite": False,  # the truth
                }
            ]
        ),
        index_reader=index_reader,
    )
    result = await reconciler.sync()
    assert result["ai_matrx_out_of_step"] == 1, result
    assert result["pins"] == {
        "local": 1,
        "ai_matrx": 0,
        "to_pin": 1,
        "to_unpin": 0,
        "to_reconcile": 1,
    }, result["pins"]
    rows = await db.fetchall(
        "SELECT envelope_json FROM coding_session_bridge_outbox ORDER BY id"
    )
    assert len(rows) == 1, "the row that sat open all day was not sent"
    envelope = BridgeRequest.model_validate_json(rows[0]["envelope_json"])
    assert envelope.hook_event is not None
    assert envelope.hook_event.payload["is_pinned"] is True


@pytest.mark.anyio
async def test_a_blocked_pass_never_reports_the_records_as_unwritable(
    env: Any,
) -> None:
    """Defect C: the same lie, in the field beside the one I fixed.

    The probe runs only AFTER the identity inventory is in hand, so a pass
    blocked on `no_active_user_jwt` never measures it — yet it journalled
    `index_writable = 0` and status reported `false`. The verifier's engine did
    exactly that while all 198 record files it probed by hand were writable.
    """
    db, outbox, tmp_path = env
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient([]),
        index_reader=lambda: ({}, {"files": 0, "records": 0, "unreadable": 0}),
    )
    await db.execute(
        """INSERT INTO coding_session_metadata_sync_operations (
               operation_id, mode, status, started_at, completed_at,
               compared_sessions, index_writable, index_writable_probed,
               error_message
           ) VALUES ('op-blocked-w', 'apply', 'failed', '2026-09-18T16:00:00Z',
                     '2026-09-18T16:00:00Z', 0, 0, 0, 'no_active_user_jwt')"""
    )
    await db.commit()
    status = await reconciler.status()
    assert status["index_writable"] is None, (
        "a probe that never ran was reported as a verdict"
    )
    assert status["index_writable_measured_at"] is None
    assert "not a verdict" in status["index_writable_reason"]


@pytest.mark.anyio
async def test_a_blocked_pass_journals_that_it_never_probed(env: Any) -> None:
    """At the source, not only at the reader: no probe = probed flag 0."""
    db, outbox, tmp_path = env
    reconciler = ClaudeSessionMetadataReconciler(
        db=db,
        outbox=outbox,
        client=_FakeClient(AIDreamOfflineError("down")),
        index_reader=lambda: ({}, {"files": 0, "records": 0, "unreadable": 0}),
    )
    with pytest.raises(ClaudeTitleSyncBlocked):
        await reconciler.sync()
    row = await db.fetchone(
        """SELECT index_writable, index_writable_probed, status
             FROM coding_session_metadata_sync_operations
         ORDER BY started_at DESC LIMIT 1"""
    )
    assert int(row["index_writable_probed"]) == 0, (
        "a pass that never probed claimed it had"
    )
    status = await reconciler.status()
    assert status["index_writable"] is None
