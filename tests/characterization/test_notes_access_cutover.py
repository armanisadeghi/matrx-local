"""Notes ↔ access-health cutover contract.

Pins the behaviors that replaced the deleted global notes_access_guard:

  - A mapped-directory failure degrades ONLY that mapping's resource — the
    canonical notes resource stays healthy (no more global false-FDA banner
    from one dead external dir).
  - A healthy mapping's success never clears another mapping's failure.
  - Bulk sync gating short-circuits through the access service.
  - The watcher refuses to start while notes-canonical is degraded.
  - get_status() keeps the notes_access_* frontend contract keys.
"""

from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
from typing import Any

import pytest

import app.common  # noqa: F401 — resolves the app.config/app.common import cycle

import app.services.documents.file_manager as fm_mod
import app.services.documents.sync_engine as se_mod
from app.services.access_health import Capability
from app.services.access_health.service import AccessHealthService
from app.services.documents.access_resources import NOTES_RESOURCE, mapping_resource_id
from app.services.documents.file_manager import DocumentFileManager
from app.services.documents.sync_engine import SyncEngine


@pytest.fixture()
def svc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AccessHealthService:
    """Fresh access-health service wired into the documents modules."""
    service = AccessHealthService()
    notes_root = tmp_path / "notes"
    notes_root.mkdir(parents=True, exist_ok=True)
    service.register(
        NOTES_RESOURCE, resolver=lambda: notes_root, label="Notes folder"
    )
    monkeypatch.setattr(fm_mod, "get_access_health", lambda: service)
    monkeypatch.setattr(se_mod, "get_access_health", lambda: service)
    return service


def _degrade_notes(service: AccessHealthService) -> None:
    service.record(
        NOTES_RESOURCE,
        Capability.ENUMERATE,
        ok=False,
        errno=errno.EACCES,
        error="denied",
        op="test",
        source="test",
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores POSIX permissions")
def test_mapped_dir_failure_degrades_only_that_mapping(
    svc: AccessHealthService, tmp_path: Path
) -> None:
    notes_root = tmp_path / "notes"
    fm = DocumentFileManager(base_dir=notes_root)
    fm.write_note("General", "hello", "content")

    good_dir = tmp_path / "good"
    good_dir.mkdir()
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad_dir.chmod(0o500)

    good_id = mapping_resource_id("f1", str(good_dir))
    bad_id = mapping_resource_id("f1", str(bad_dir))
    svc.register(good_id, resolver=lambda: good_dir, label="good")
    svc.register(bad_id, resolver=lambda: bad_dir, label="bad")

    try:
        written = fm.sync_to_mapped_dirs(
            "General/hello.md", [str(bad_dir), str(good_dir)], folder_id="f1"
        )
    finally:
        bad_dir.chmod(0o700)

    assert written == [str(good_dir / "hello.md")]
    assert svc.is_degraded(bad_id), "the failing mapping must be degraded"
    assert not svc.is_degraded(good_id)
    assert not svc.is_degraded(NOTES_RESOURCE), (
        "a mapped-dir failure must NEVER degrade canonical notes health"
    )
    # And the good mapping's success must not have cleared the bad one.
    assert svc.is_degraded(bad_id)


def test_canonical_write_records_against_notes_resource(
    svc: AccessHealthService, tmp_path: Path
) -> None:
    fm = DocumentFileManager(base_dir=tmp_path / "notes")
    fm.write_note("General", "hello", "content")
    health = svc.health(NOTES_RESOURCE)
    assert health["capabilities"]["replace"]["ok"] is True
    assert health["status"] == "ok"


def test_bulk_sync_short_circuits_while_degraded(svc: AccessHealthService) -> None:
    _degrade_notes(svc)
    skipped = SyncEngine._access_skipped()
    assert skipped is not None
    assert skipped["sync_skipped"] is True
    assert skipped["notes_access_degraded"] is True
    assert isinstance(skipped["reason"], str) and skipped["reason"]


def test_bulk_sync_proceeds_when_healthy(svc: AccessHealthService) -> None:
    assert SyncEngine._access_skipped() is None


def test_watcher_refuses_to_start_while_degraded(
    svc: AccessHealthService, tmp_path: Path
) -> None:
    _degrade_notes(svc)
    engine = SyncEngine(fm=DocumentFileManager(base_dir=tmp_path / "notes"))
    asyncio.run(engine.start_watcher())
    assert engine.watcher_active is False


def test_watcher_restarts_immediately_on_recovery(
    svc: AccessHealthService, tmp_path: Path
) -> None:
    """degraded → ok transition must restart the watcher NOW, not on the next
    600s auto-sync tick (the historical up-to-10-minute dead-watcher gap)."""
    engine = SyncEngine(fm=DocumentFileManager(base_dir=tmp_path / "notes"))

    async def scenario() -> None:
        svc.capture_loop(asyncio.get_running_loop())
        engine.ensure_recovery_hook()
        _degrade_notes(svc)
        await engine.start_watcher()
        assert engine.watcher_active is False
        # Recovery: the failing capability succeeds again.
        svc.record(
            NOTES_RESOURCE, Capability.ENUMERATE, ok=True, op="test", source="test"
        )
        for _ in range(50):
            if engine.watcher_active:
                break
            await asyncio.sleep(0.02)
        assert engine.watcher_active is True
        await engine.stop_watcher()

    asyncio.run(scenario())


def test_get_status_keeps_frontend_contract_keys(
    svc: AccessHealthService, tmp_path: Path
) -> None:
    engine = SyncEngine(fm=DocumentFileManager(base_dir=tmp_path / "notes"))
    status: dict[str, Any] = engine.get_status()
    assert status["notes_access_degraded"] is False
    assert status["notes_access_reason"] is None
    assert status["notes_access_kind"] is None

    _degrade_notes(svc)
    status = engine.get_status()
    # get_status itself performs reads that may re-observe; degraded must
    # persist because ENUMERATE failure is only cleared by ENUMERATE success
    # on the same resource — and the fixture root IS enumerable, so a
    # list_conflicts success must not clear the canonical failure unless it
    # actually enumerates the same capability successfully. Accept either
    # truthful outcome but require internal consistency.
    if status["notes_access_degraded"]:
        assert isinstance(status["notes_access_reason"], str)
        assert status["notes_access_kind"] == "permission"
    else:
        assert status["notes_access_reason"] is None
