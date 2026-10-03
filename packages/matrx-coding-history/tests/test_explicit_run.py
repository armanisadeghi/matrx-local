"""The explicit "Import missing history" run: which sessions it takes, how it batches, how it fails."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from matrx_coding_history.importer import ClaudeHistoryImporter
from matrx_coding_history.ports import CloudError, CloudOffline
from matrx_coding_history.reconciler import (
    BEHIND_GRACE_SECONDS,
    CaptureReconcileBlocked,
    ClaudeCaptureReconciler,
    MissingPlan,
)
from coding_history_support import Ledger, Logger, RecordingSink, SyncMeta, User, account_a, organization_required


def _write(config_dir: Path, session_id: str, *, mtime: datetime, text: str = "hello") -> str:
    project = config_dir / "projects" / "-tmp-explicit"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    records = [
        {"type": "user", "uuid": str(uuid4()), "sessionId": session_id, "cwd": "/tmp/explicit", "message": {"role": "user", "content": text}},
        {"type": "assistant", "uuid": str(uuid4()), "sessionId": session_id, "message": {"role": "assistant", "content": "answer"}},
    ]
    path.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in records))
    stamp = mtime.timestamp()
    os.utime(path, (stamp, stamp))
    return "claude-local:" + hashlib.sha256(str(project.resolve()).encode()).hexdigest()


def _composite(project_key: str, session_id: str) -> str:
    digest = hashlib.sha256(project_key.encode()).hexdigest()
    return f"claude-sdk:{digest}:" + base64.urlsafe_b64encode(session_id.encode()).decode().rstrip("=")


class World:
    def __init__(self, tmp_path: Path, cloud: list[dict[str, Any]] | None = None, sink: RecordingSink | None = None) -> None:
        self.config_dir = tmp_path / ".claude"
        self.config_dir.mkdir()
        self.cloud = cloud or []
        self.sink = sink or RecordingSink()
        self.ledger = Ledger()
        self.importer = ClaudeHistoryImporter(
            sink=lambda: self.sink, user=User(), sync_meta=SyncMeta(), config_dir=self.config_dir,
            sessions_dir=tmp_path / "no-index", account_reader=account_a,
        )

        async def identities() -> list[dict[str, Any]]:
            return self.cloud

        self.reconciler = ClaudeCaptureReconciler(
            importer=self.importer, identity_source=identities, ledger=self.ledger, logger=Logger()
        )

    def run(self) -> MissingPlan:
        async def go() -> MissingPlan:
            plan = await self.reconciler.plan_missing()
            while not plan.finished:
                await self.reconciler.import_next(plan)
            return plan

        return asyncio.run(go())


def _native_sessions(sink: RecordingSink) -> set[str]:
    return {str(r.source_metadata.provider_native_session_id) for r in sink.requests if r.source_metadata is not None}


def test_missing_and_behind_are_imported_and_current_ones_are_not(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    w = World(tmp_path)
    missing, current_raw, current_composite, behind, behind_both = (str(uuid4()) for _ in range(5))
    key = _write(w.config_dir, missing, mtime=now - timedelta(days=3))
    _write(w.config_dir, current_raw, mtime=now - timedelta(hours=2))
    _write(w.config_dir, current_composite, mtime=now - timedelta(hours=2))
    _write(w.config_dir, behind, mtime=now)
    _write(w.config_dir, behind_both, mtime=now - timedelta(hours=1))
    w.cloud = [
        {"provider_session_id": current_raw, "last_seen_at": (now - timedelta(hours=2)).isoformat()},
        {"provider_session_id": _composite(key, current_composite), "last_seen_at": (now - timedelta(hours=1)).isoformat()},
        # Lost turns: the hook's last delivery is hours older than the local file.
        {"provider_session_id": behind, "last_seen_at": (now - timedelta(hours=5)).isoformat()},
        # Both forms present: the NEWEST of the two decides (an import refreshed the composite row).
        {"provider_session_id": behind_both, "last_seen_at": (now - timedelta(days=1)).isoformat()},
        {"provider_session_id": _composite(key, behind_both), "last_seen_at": (now - timedelta(minutes=50)).isoformat()},
    ]
    plan = w.run()
    assert (plan.local_sessions, plan.in_cloud, plan.missing, plan.behind) == (5, 3, 1, 1)
    assert _native_sessions(w.sink) == {missing, behind}
    assert plan.imported == 2 and not plan.failed
    assert plan.summary() == "Imported 2 of 2 sessions. 3 already in AI Matrx."
    # Recorded as successes in the same ledger the automatic pass uses.
    assert all(row["last_error"] is None for row in w.ledger.rows.values()) and len(w.ledger.rows) == 2


def test_the_era_rule_does_not_apply_to_an_explicit_run(tmp_path: Path) -> None:
    """No cloud binding at all: the automatic pass refuses; the button imports everything."""
    w = World(tmp_path)
    sessions = [str(uuid4()) for _ in range(3)]
    for s in sessions:
        _write(w.config_dir, s, mtime=datetime.now(UTC) - timedelta(days=30))
    automatic = asyncio.run(w.reconciler.reconcile())
    assert automatic["status"] == "no_mirroring_era" and w.sink.requests == []
    plan = w.run()
    assert plan.missing == 3 and plan.imported == 3
    assert _native_sessions(w.sink) == set(sessions)


def test_a_change_inside_the_grace_window_is_not_behind(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    w = World(tmp_path)
    session = str(uuid4())
    _write(w.config_dir, session, mtime=now)
    w.cloud = [{"provider_session_id": session, "last_seen_at": (now - timedelta(seconds=BEHIND_GRACE_SECONDS - 60)).isoformat()}]
    plan = w.run()
    assert (plan.in_cloud, plan.behind, plan.imported) == (1, 0, 0)
    assert plan.summary() == "Nothing missing: 1 session already in AI Matrx."


def test_one_failing_session_never_fails_its_batch(tmp_path: Path) -> None:
    sessions = [str(uuid4()) for _ in range(4)]
    bad = sessions[2]
    w = World(tmp_path, sink=RecordingSink(fail_for={bad: ValueError("transcript reuses entry UUID x")}))
    for i, s in enumerate(sessions):
        _write(w.config_dir, s, mtime=datetime.now(UTC) - timedelta(days=4 - i))
    plan = w.run()
    assert plan.imported == 3
    assert plan.failed == [{"session_id": bad, "reason": "transcript reuses entry UUID x"}]
    assert _native_sessions(w.sink) == set(sessions) - {bad}
    assert plan.summary() == "Imported 3 of 4 sessions. 1 failed."
    failed_rows = [r for r in w.ledger.rows.values() if r["last_error"]]
    assert len(failed_rows) == 1 and "reuses entry UUID" in failed_rows[0]["last_error"]


def test_the_organization_question_stops_the_whole_run(tmp_path: Path) -> None:
    w = World(tmp_path)
    s = str(uuid4())
    _write(w.config_dir, s, mtime=datetime.now(UTC))
    w.sink = RecordingSink(fail_for={s: organization_required()})

    async def go() -> None:
        plan = await w.reconciler.plan_missing()
        await w.reconciler.import_next(plan)

    with pytest.raises(CaptureReconcileBlocked) as excinfo:
        asyncio.run(go())
    assert excinfo.value.reason == "organization_required"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CloudError(401, "token expired"), "no_active_user_jwt"),
        (CloudOffline("no route"), "aidream_unreachable"),
    ],
)
def test_signed_out_or_offline_mid_run_stops_it(tmp_path: Path, error: Exception, reason: str) -> None:
    w = World(tmp_path)
    s = str(uuid4())
    _write(w.config_dir, s, mtime=datetime.now(UTC))
    w.sink = RecordingSink(fail_for={s: error})

    async def go() -> None:
        plan = await w.reconciler.plan_missing()
        await w.reconciler.import_next(plan)

    with pytest.raises(CaptureReconcileBlocked) as excinfo:
        asyncio.run(go())
    assert excinfo.value.reason == reason


def test_a_server_refusal_of_one_session_is_that_sessions_failure(tmp_path: Path) -> None:
    w = World(tmp_path)
    s = str(uuid4())
    _write(w.config_dir, s, mtime=datetime.now(UTC))
    w.sink = RecordingSink(fail_for={s: CloudError(409, "writer_lease_conflict")})
    plan = w.run()
    assert plan.failed == [{"session_id": s, "reason": "writer_lease_conflict"}]


def test_batches_are_bounded_by_count_and_bytes(tmp_path: Path) -> None:
    w = World(tmp_path)
    for i in range(23):
        _write(w.config_dir, str(uuid4()), mtime=datetime.now(UTC) - timedelta(minutes=100 - i))
    batches: list[int] = []
    original = w.importer.import_discovered

    async def counting(request, sources, **kwargs):  # type: ignore[no-untyped-def]
        batches.append(len(request.sessions))
        return await original(request, sources, **kwargs)

    w.importer.import_discovered = counting  # type: ignore[method-assign]
    plan = w.run()
    assert batches == [10, 10, 3]
    assert plan.imported == 23


def test_a_signed_out_cloud_list_blocks_before_reading_anything(tmp_path: Path) -> None:
    w = World(tmp_path)

    async def blocked() -> list[dict[str, Any]]:
        raise CaptureReconcileBlocked("no_active_user_jwt")

    w.reconciler._identity_source = blocked  # type: ignore[assignment]
    with pytest.raises(CaptureReconcileBlocked):
        asyncio.run(w.reconciler.plan_missing())
