"""The capture reconciler: backfill what the hooks silently failed to deliver.

The failure being defended against is the 2026-08-16 outage — a Claude Code
hook that fails is NON-BLOCKING, so mirroring stopped for 23.5 hours with no
error anywhere. These tests pin the two things that make an automatic backfill
safe: THE ERA RULE (never sweep up history from before the user opted into
continuous mirroring) and boundedness (a broken session cannot spin forever).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.services.coding_sessions.capture_reconciler import (
    MAX_ATTEMPTS,
    CaptureReconcileBlocked,
    ClaudeCaptureReconciler,
    _sdk_identity,
)
from app.services.coding_sessions.claude_history import (
    ClaudeHistoryImporter,
    _AccountSnapshot,
)
from app.services.coding_sessions.service import CodingSessionBridgeOutbox
from app.services.local_db.database import LocalDatabase


def _line(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def _write_session(config_dir: Path, *, session_id: str, mtime: datetime) -> Path:
    project = config_dir / "projects" / "-private-project"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    with path.open("wb") as handle:
        handle.write(
            _line(
                {
                    "type": "user",
                    "uuid": str(uuid4()),
                    "sessionId": session_id,
                    "cwd": "/private/project",
                    "message": {"role": "user", "content": "hello"},
                }
            )
        )
    stamp = mtime.timestamp()
    import os

    os.utime(path, (stamp, stamp))
    return path


async def _account_a() -> _AccountSnapshot:
    return _AccountSnapshot(
        True, "a" * 64, "a" * 12, "2.1.228", None, account_label="a***n@t***.com"
    )


class _FakeClient:
    """Stands in for aidream's owner-scoped identity list."""

    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        self._sessions = sessions
        self.calls = 0

    async def get(self, path: str, jwt: str) -> dict[str, Any]:  # noqa: ARG002
        self.calls += 1
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
    config_dir = tmp_path / ".claude"
    outbox = CodingSessionBridgeOutbox(db=db, cloud_enabled=False)
    importer = ClaudeHistoryImporter(
        db=db, outbox=outbox, config_dir=config_dir, account_reader=_account_a
    )
    try:
        yield config_dir, db, importer
    finally:
        await db.close()


def _reconciler(db, importer, cloud: list[dict[str, Any]]) -> ClaudeCaptureReconciler:
    return ClaudeCaptureReconciler(db=db, importer=importer, client=_FakeClient(cloud))


@pytest.mark.anyio
async def test_no_binding_ever_means_no_automatic_backfill(env) -> None:
    """THE ERA RULE's hard edge: a user who never mirrored gets nothing swept up."""
    config_dir, db, importer = env
    _write_session(config_dir, session_id=str(uuid4()), mtime=datetime.now(UTC))

    report = await _reconciler(db, importer, []).reconcile()

    assert report["status"] == "no_mirroring_era"
    assert report["enqueued"] == 0


@pytest.mark.anyio
async def test_sessions_from_before_the_first_binding_are_never_uploaded(env) -> None:
    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    # Pre-dates the user's first binding by a week: their own history, not ours.
    _write_session(
        config_dir, session_id=str(uuid4()), mtime=era_start - timedelta(days=7)
    )
    cloud = [
        {
            "provider_session_id": "already-known",
            "last_seen_at": era_start.isoformat(),
        }
    ]

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["enqueued"] == 0
    assert report["missing"] == 0
    assert report["skipped_pre_era"] == 1


@pytest.mark.anyio
async def test_backfills_a_session_the_hook_path_missed(env) -> None:
    """The regression: capture was running, a session exists locally, none arrived."""
    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    missed = str(uuid4())
    _write_session(config_dir, session_id=missed, mtime=datetime.now(UTC))
    cloud = [
        {"provider_session_id": "already-known", "last_seen_at": era_start.isoformat()}
    ]

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["status"] == "ok"
    assert report["enqueued"] == 1
    assert report["missing"] == 1
    # It really reached the durable outbox — this is the whole point.
    queued = await db.fetchall(
        "SELECT receipt_id, enqueue_origin FROM coding_session_bridge_queue_metadata"
    )
    assert len(queued) >= 1
    assert {row["enqueue_origin"] for row in queued} == {"capture_recovery"}


@pytest.mark.anyio
async def test_backfills_a_missed_session_older_than_the_newest_200(env) -> None:
    """Recovery must inventory all sessions, not only the preview page."""
    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    missed = str(uuid4())
    _write_session(
        config_dir, session_id=missed, mtime=era_start + timedelta(minutes=1)
    )
    cloud = [
        {"provider_session_id": "era-marker", "last_seen_at": era_start.isoformat()}
    ]
    for offset in range(200):
        session_id = str(uuid4())
        _write_session(
            config_dir,
            session_id=session_id,
            mtime=era_start + timedelta(hours=1, minutes=offset),
        )
        cloud.append(
            {
                "provider_session_id": session_id,
                "last_seen_at": (era_start + timedelta(hours=1)).isoformat(),
            }
        )

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["local_sessions"] == 201
    assert report["missing"] == 1
    assert report["enqueued"] == 1


@pytest.mark.anyio
async def test_recovery_hashes_only_selected_missing_candidates(
    env, monkeypatch
) -> None:
    """The unattended inventory must not reread every transcript each pass."""
    import app.services.coding_sessions.claude_history as history

    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    known: list[str] = []
    for _ in range(5):
        session_id = str(uuid4())
        known.append(session_id)
        _write_session(config_dir, session_id=session_id, mtime=datetime.now(UTC))
    missed = known.pop()
    cloud = [
        {"provider_session_id": "era-marker", "last_seen_at": era_start.isoformat()},
        *[
            {"provider_session_id": session_id, "last_seen_at": era_start.isoformat()}
            for session_id in known
        ],
    ]
    original = history._hash_source
    hashed: list[tuple[tuple[str, Path], ...]] = []

    def counting_hash(projects_root, streams):
        hashed.append(streams)
        return original(projects_root, streams)

    monkeypatch.setattr(history, "_hash_source", counting_hash)

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["enqueued"] == 1
    assert len(hashed) == 1
    assert hashed[0][0][1].stem == missed


@pytest.mark.anyio
async def test_a_session_already_delivered_by_hooks_is_not_re_imported(env) -> None:
    """A hook binding stores the RAW uuid; matching only the composite re-uploads everything."""
    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    session_id = str(uuid4())
    _write_session(config_dir, session_id=session_id, mtime=datetime.now(UTC))
    cloud = [
        {
            "provider_session_id": session_id,  # raw form, as hooks store it
            "last_seen_at": era_start.isoformat(),
        }
    ]

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["enqueued"] == 0
    assert report["missing"] == 0


@pytest.mark.anyio
async def test_a_session_already_imported_is_not_re_imported(env) -> None:
    """The other identity form: a native import stores the claude-sdk composite."""
    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    session_id = str(uuid4())
    _write_session(config_dir, session_id=session_id, mtime=datetime.now(UTC))
    preview = await importer.preview()
    project_key = preview["sessions"][0]["project_key"]
    cloud = [
        {
            "provider_session_id": _sdk_identity(project_key, session_id),
            "last_seen_at": era_start.isoformat(),
        }
    ]

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["enqueued"] == 0


@pytest.mark.anyio
async def test_dry_run_reports_the_gap_and_enqueues_nothing(env) -> None:
    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    _write_session(config_dir, session_id=str(uuid4()), mtime=datetime.now(UTC))
    cloud = [{"provider_session_id": "known", "last_seen_at": era_start.isoformat()}]

    report = await _reconciler(db, importer, cloud).reconcile(dry_run=True)

    assert report["missing"] == 1
    assert report["enqueued"] == 0
    assert await db.fetchall("SELECT id FROM coding_session_bridge_outbox") == []


@pytest.mark.anyio
async def test_a_broken_session_stops_being_retried(env) -> None:
    """Bounded: an unimportable transcript must not re-enter the batch forever."""
    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    session_id = str(uuid4())
    _write_session(config_dir, session_id=session_id, mtime=datetime.now(UTC))
    _account, inventory = await importer.capture_inventory()
    project_key = inventory[0].project_key
    session_key = f"{project_key}:{session_id}"
    source_state = inventory[0].source_state
    await db.execute(
        """INSERT INTO claude_capture_backfill
               (session_key, source_revision, attempts, last_error, updated_at)
           VALUES (?, ?, ?, 'boom', datetime('now'))""",
        (session_key, source_state, MAX_ATTEMPTS),
    )
    await db.commit()
    cloud = [{"provider_session_id": "known", "last_seen_at": era_start.isoformat()}]

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["enqueued"] == 0
    assert report["missing"] == 0
    assert report["exhausted"] == 1


@pytest.mark.anyio
async def test_more_local_writing_reopens_the_retry_budget(env) -> None:
    """A changed revision is a new input, not the same failure repeating."""
    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    session_id = str(uuid4())
    _write_session(config_dir, session_id=session_id, mtime=datetime.now(UTC))
    preview = await importer.preview()
    project_key = preview["sessions"][0]["project_key"]
    await db.execute(
        """INSERT INTO claude_capture_backfill
               (session_key, source_revision, attempts, last_error, updated_at)
           VALUES (?, 'stale-revision', ?, 'boom', datetime('now'))""",
        (f"{project_key}:{session_id}", MAX_ATTEMPTS),
    )
    await db.commit()
    cloud = [{"provider_session_id": "known", "last_seen_at": era_start.isoformat()}]

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["enqueued"] == 1


@pytest.mark.anyio
async def test_signed_out_is_blocked_not_crashed(env) -> None:
    config_dir, db, importer = env
    await db.execute("DELETE FROM auth_tokens")
    await db.commit()

    with pytest.raises(CaptureReconcileBlocked) as excinfo:
        await _reconciler(db, importer, []).reconcile()

    assert excinfo.value.reason == "no_active_user_jwt"


@pytest.mark.anyio
async def test_an_oversized_transcript_never_blocks_the_batch(env, monkeypatch) -> None:
    """One huge session must not burn the retry budget of every session beside it."""
    import app.services.coding_sessions.capture_reconciler as mod

    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    for _ in range(3):
        _write_session(config_dir, session_id=str(uuid4()), mtime=datetime.now(UTC))
    # Budget smaller than any real transcript: every session reads as oversized.
    monkeypatch.setattr(mod, "BATCH_BYTE_BUDGET", 1)
    cloud = [{"provider_session_id": "known", "last_seen_at": era_start.isoformat()}]

    report = await _reconciler(db, importer, cloud).reconcile()

    assert report["enqueued"] == 0
    # Recorded, not silently skipped — an invisible permanent skip is the bug.
    rows = await db.fetchall(
        "SELECT last_error FROM claude_capture_backfill WHERE last_error IS NOT NULL"
    )
    assert rows and "import budget" in str(rows[0]["last_error"])


@pytest.mark.anyio
async def test_dry_run_does_not_consume_oversized_retry_budget(
    env, monkeypatch
) -> None:
    import app.services.coding_sessions.capture_reconciler as mod

    config_dir, db, importer = env
    era_start = datetime.now(UTC) - timedelta(days=2)
    _write_session(config_dir, session_id=str(uuid4()), mtime=datetime.now(UTC))
    monkeypatch.setattr(mod, "BATCH_BYTE_BUDGET", 1)

    await _reconciler(
        db,
        importer,
        [{"provider_session_id": "known", "last_seen_at": era_start.isoformat()}],
    ).reconcile(dry_run=True)

    assert await db.fetchall("SELECT session_key FROM claude_capture_backfill") == []


@pytest.mark.anyio
async def test_success_clears_failure_budget_and_is_not_exhausted(env) -> None:
    _config_dir, db, importer = env
    reconciler = _reconciler(db, importer, [])
    for _ in range(MAX_ATTEMPTS):
        await reconciler._record_attempt("project:session", "same-state", "boom")
    await reconciler._record_attempt("project:session", "same-state", None)

    row = await db.fetchone(
        "SELECT attempts, last_error FROM claude_capture_backfill WHERE session_key = ?",
        ("project:session",),
    )
    assert row is not None
    assert row["attempts"] == 0
    assert row["last_error"] is None
    status = await reconciler.status()
    assert status["exhausted"] == []


@pytest.mark.anyio
async def test_status_counts_all_exhausted_rows_not_only_recent_50(env) -> None:
    _config_dir, db, importer = env
    for index in range(60):
        await db.execute(
            """INSERT INTO claude_capture_backfill
                   (session_key, source_revision, attempts, last_error, updated_at)
               VALUES (?, 'same-state', ?, 'boom', datetime('now'))""",
            (f"project:{index}", MAX_ATTEMPTS),
        )
    await db.commit()

    status = await _reconciler(db, importer, []).status()

    assert len(status["recent"]) == 50
    assert len(status["exhausted"]) == 60
