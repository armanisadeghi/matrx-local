"""Parity: docs/SYNC_CONTRACT.md invariants stay wired to the code.

These are cheap structural guards, not behavior suites (behavior lives in
tests/characterization/). Each test maps to a contract clause:

  - the sync_queue outbox table exists in the real SQLite schema
  - conflict resolution modes enumerate EXACTLY the documented set
  - deletion paths are tombstones, not hard-deletes (where implemented)
  - the remote wire boundary normalizes deleted_at -> is_deleted
  - the sync modules' docstrings reference SYNC_CONTRACT.md
  - the settings ping-pong guard (origin-echo suppression, 9ca565245) holds

If one of these goes red you are amending the contract: update
docs/SYNC_CONTRACT.md in the same change and say so in the commit message.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest

# ORDER MATTERS: app.common first — importing app.config as the process's
# first app module trips the app.config <-> app.common circular import
# (tracked in .matrx/AGENT_TASKS.md).
import app.common  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "docs" / "SYNC_CONTRACT.md"


def test_contract_document_exists() -> None:
    assert CONTRACT.is_file(), "docs/SYNC_CONTRACT.md is the named contract — do not delete/move it"
    text = CONTRACT.read_text(encoding="utf-8")
    for anchor in ("FIRST-ACCESS REPLICA", "Do-not list", "Current gaps"):
        assert anchor in text, f"contract lost its '{anchor}' section"


# ---------------------------------------------------------------------------
# sync_queue outbox exists in the REAL schema
# ---------------------------------------------------------------------------


def test_sync_queue_table_exists_in_schema(tmp_path: Path) -> None:
    """The dormant offline-write outbox must not be 'cleaned up'.

    Contract do-not list: 'do not delete the sync_queue'. It is the designated
    outbox for future offline-write push pipelines (conversations first)."""
    from app.services.local_db.database import LocalDatabase

    async def main() -> None:
        db = LocalDatabase(path=tmp_path / "contract.db")
        await db.connect()  # runs the real migrations
        try:
            row = await db.fetchone(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_queue'"
            )
            assert row is not None, "sync_queue table missing from migrated schema"
            cols = await db.fetchall("PRAGMA table_info(sync_queue)")
            names = {c["name"] for c in cols}
            assert {"entity_type", "entity_id", "action", "payload", "attempts"} <= names
        finally:
            await db.close()

    asyncio.run(main())


def test_sync_meta_repo_keeps_queue_methods() -> None:
    from app.services.local_db.repositories import SyncMetaRepo

    for method in ("enqueue", "dequeue", "remove_from_queue", "increment_attempts", "pending_count"):
        assert callable(getattr(SyncMetaRepo, method, None)), (
            f"SyncMetaRepo.{method} removed — the sync_queue outbox API is contract-protected"
        )


# ---------------------------------------------------------------------------
# Conflict modes — exactly the documented set
# ---------------------------------------------------------------------------

DOCUMENTED_CONFLICT_MODES = {
    "keep_local",
    "keep_remote",
    "merge",
    "append",
    "split",
    "exclude",
}


def test_conflict_modes_enumerate_documented_set() -> None:
    from app.services.documents.sync_engine import SyncEngine

    src = inspect.getsource(SyncEngine._resolve_conflict)
    modes = set(re.findall(r'resolution == "([a-z_]+)"', src))
    assert modes == DOCUMENTED_CONFLICT_MODES, (
        f"conflict modes drifted from the contract: code={sorted(modes)} "
        f"documented={sorted(DOCUMENTED_CONFLICT_MODES)} — update "
        "docs/SYNC_CONTRACT.md and the characterization tests together"
    )
    # And the contract document lists every mode.
    text = CONTRACT.read_text(encoding="utf-8")
    for mode in DOCUMENTED_CONFLICT_MODES:
        assert f"`{mode}`" in text, f"contract doc no longer documents mode '{mode}'"


# ---------------------------------------------------------------------------
# Deletions are tombstones, not hard-deletes (where implemented)
# ---------------------------------------------------------------------------


def test_notes_repo_soft_delete_is_a_tombstone() -> None:
    from app.services.local_db.repositories import NotesRepo

    src = inspect.getsource(NotesRepo.soft_delete)
    assert "is_deleted = 1" in src and "UPDATE" in src
    assert "DELETE FROM" not in src, "NotesRepo.soft_delete became a hard delete"


def test_remote_soft_delete_is_a_tombstone() -> None:
    from app.services.documents.supabase_client import SupabaseDocClient

    src = inspect.getsource(SupabaseDocClient.soft_delete_note)
    assert '"PATCH"' in src and "deleted_at" in src
    assert '"DELETE"' not in src, "soft_delete_note became a hard delete on the wire"


def test_delete_note_route_uses_tombstones() -> None:
    """The API delete path tombstones SQLite AND propagates a cloud soft-delete
    (offline case retried via full_sync's local_note.is_deleted branch)."""
    route_src = (REPO_ROOT / "app" / "api" / "document_routes.py").read_text(encoding="utf-8")
    m = re.search(r'@router\.delete\("/notes/\{note_id\}"\).*?(?=\n@router|\Z)', route_src, re.S)
    assert m, "delete_note route not found"
    body = m.group(0)
    assert "repo.soft_delete(" in body, "delete_note no longer tombstones SQLite"
    assert "soft_delete_note(" in body, "delete_note no longer propagates a cloud soft-delete"


def test_external_delete_handler_tombstones_and_full_sync_propagates() -> None:
    from app.services.documents.sync_engine import SyncEngine

    handler_src = inspect.getsource(SyncEngine._handle_external_delete)
    assert "soft_delete(" in handler_src

    full_sync_src = inspect.getsource(SyncEngine.full_sync)
    assert 'get("is_deleted")' in full_sync_src and "soft_delete_note(" in full_sync_src, (
        "full_sync lost the offline-delete propagation branch "
        "(local tombstone -> cloud soft-delete on reconnect)"
    )


def test_remote_reads_normalize_deleted_at_to_is_deleted() -> None:
    """Wire boundary guard: workbench.notes speaks deleted_at; the engine
    speaks is_deleted. Every read path returning note rows must normalize —
    skipping it resurrects deleted notes (regression shipped 2026-06)."""
    from app.services.documents import supabase_client as sc

    assert callable(getattr(sc, "_normalize_note_row", None))
    for method_name in ("get_note", "get_notes_since", "list_notes"):
        src = inspect.getsource(getattr(sc.SupabaseDocClient, method_name))
        assert "_normalize_note_row" in src, (
            f"SupabaseDocClient.{method_name} no longer normalizes deleted_at -> is_deleted"
        )


# ---------------------------------------------------------------------------
# Docstrings stay wired to the contract
# ---------------------------------------------------------------------------


def test_sync_module_docstrings_reference_contract() -> None:
    import app.services.cloud_sync.settings_sync as settings_sync
    import app.services.documents.sync_engine as documents_sync
    import app.services.local_db.sync_engine as local_db_sync

    for mod in (local_db_sync, documents_sync, settings_sync):
        assert mod.__doc__ and "SYNC_CONTRACT.md" in mod.__doc__, (
            f"{mod.__name__} docstring no longer references docs/SYNC_CONTRACT.md"
        )


def test_ai_engine_docstring_references_contract() -> None:
    import app.services.ai.engine as ai_engine

    assert ai_engine.__doc__ and "SYNC_CONTRACT.md" in ai_engine.__doc__


def test_no_sqlite_source_of_truth_claims_in_sync_modules() -> None:
    """The replica doctrine forbids re-declaring SQLite authoritative inside
    the three sync subsystems. (Known stale claims OUTSIDE these directories
    are tracked in .matrx/AGENT_TASKS.md.)"""
    sync_dirs = [
        REPO_ROOT / "app" / "services" / "local_db",
        REPO_ROOT / "app" / "services" / "documents",
        REPO_ROOT / "app" / "services" / "cloud_sync",
    ]
    offenders: list[str] = []
    pattern = re.compile(r"SQLite is the (single )?source of truth", re.I)
    for d in sync_dirs:
        for py in d.glob("*.py"):
            if pattern.search(py.read_text(encoding="utf-8")):
                offenders.append(str(py.relative_to(REPO_ROOT)))
    assert offenders == [], f"replica doctrine violated in: {offenders}"


# ---------------------------------------------------------------------------
# Settings ping-pong guard (origin-echo suppression, fixed in 9ca565245)
# ---------------------------------------------------------------------------


class _PingPongHarness:
    """SettingsSync with the cloud stubbed; records pushes."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.services.cloud_sync.settings_sync as ss

        monkeypatch.setattr(ss, "LOCAL_SETTINGS_FILE", tmp_path / "settings.json")
        self.sync = ss.SettingsSync()
        self.sync.configure(
            supabase_url="https://stub.supabase.co",
            supabase_key="stub-key",
            jwt="stub-jwt",
            user_id="user-1",
            instance_id="inst-1",
        )
        self.cloud_row: dict[str, Any] | None = None
        self.pushes: list[dict[str, Any]] = []

        async def fetch() -> dict[str, Any] | None:
            return self.cloud_row

        async def push() -> None:
            self.pushes.append(self.sync.get_all())
            self.cloud_row = {
                "settings_json": self.sync.get_all(),
                "updated_at": "2026-07-10T12:00:00+00:00",
            }

        async def status(*_a: Any, **_k: Any) -> None:
            return None

        monkeypatch.setattr(self.sync, "_fetch_cloud_settings", fetch)
        monkeypatch.setattr(self.sync, "_push_to_cloud", push)
        monkeypatch.setattr(self.sync, "_update_sync_status", status)


def test_settings_pull_stamps_cloud_timestamp_not_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _PingPongHarness(tmp_path, monkeypatch)
    cloud_ts = "2026-07-10T08:00:00+00:00"
    h.cloud_row = {"settings_json": {"theme": "light"}, "updated_at": cloud_ts}
    # Local file is older than the cloud row.
    h.sync._local_updated_at = "2026-07-01T00:00:00+00:00"

    result = asyncio.run(h.sync.sync())
    assert result["status"] == "pulled"
    # THE guard: local updated_at records the CLOUD timestamp. Stamping now()
    # here is what created the pull->push->pull ping-pong (9ca565245).
    assert h.sync._local_updated_at == cloud_ts
    on_disk = json.loads((tmp_path / "settings.json").read_text())
    assert on_disk["updated_at"] == cloud_ts


def test_settings_second_sync_after_pull_is_in_sync_not_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _PingPongHarness(tmp_path, monkeypatch)
    cloud_ts = "2026-07-10T08:00:00+00:00"
    h.cloud_row = {"settings_json": {"theme": "light"}, "updated_at": cloud_ts}
    h.sync._local_updated_at = "2026-07-01T00:00:00+00:00"

    first = asyncio.run(h.sync.sync())
    assert first["status"] == "pulled"
    # Unchanged cloud: the very next sync must be a no-op. A 'pushed' here is
    # the ping-pong regression (the in_sync state was unreachable).
    second = asyncio.run(h.sync.sync())
    assert second == {"status": "in_sync", "reason": "timestamps_match"}
    assert h.pushes == []


def test_settings_local_edit_still_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echo suppression must not suppress REAL local edits."""
    h = _PingPongHarness(tmp_path, monkeypatch)
    cloud_ts = "2026-07-10T08:00:00+00:00"
    h.cloud_row = {"settings_json": {"theme": "light"}, "updated_at": cloud_ts}
    h.sync._local_updated_at = "2026-07-01T00:00:00+00:00"
    asyncio.run(h.sync.sync())  # pulled, stamped with cloud_ts

    h.sync.set("theme", "dark")  # real local edit -> stamps now() > cloud_ts
    result = asyncio.run(h.sync.sync())
    assert result["status"] == "pushed"
    assert len(h.pushes) == 1 and h.pushes[0]["theme"] == "dark"
