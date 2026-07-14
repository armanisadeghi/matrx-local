"""Notes access-degraded is a STATE, not an error stream.

Pins the contract behind the Documents-page Full-Disk-Access prompt
(MXL notes UX bug, 2026-07-13):

  - the guard logs ONCE per degradation (no per-operation ERROR spam)
  - degraded carries a kind ("permission" | "missing_dir") and a
    platform-appropriate, user-actionable reason
  - auto-sync skips quietly while degraded and lets a probe through after
    the recheck window so access recovers WITHOUT an engine restart
  - probe_notes_access ("Check again" / "Create folder") actively re-probes
    and clears the state the moment access is back

If one of these fails, the UI prompt regresses to the old behavior the owner
described as "it just shits all over itself" — empty lists and silent errors
with no prompt. Do not weaken these without fixing the UI contract too.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

import app.common  # noqa: F401 — import-order guard (see characterization tests)

from app.services.documents import file_manager as fm_mod
from app.services.documents.file_manager import (
    _NotesAccessGuard,
    probe_notes_access,
)


@pytest.fixture()
def guard(monkeypatch: pytest.MonkeyPatch) -> _NotesAccessGuard:
    """Fresh guard swapped in for the module singleton (probe uses the global)."""
    g = _NotesAccessGuard()
    monkeypatch.setattr(fm_mod, "notes_access_guard", g)
    return g


def _denied() -> PermissionError:
    e = PermissionError(1, "Operation not permitted")
    return e


# ---------------------------------------------------------------------------
# Guard state machine
# ---------------------------------------------------------------------------


def test_denial_logs_once_then_quiet(guard: _NotesAccessGuard, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=fm_mod.logger.name):
        guard.note_denied(_denied(), "listing folders")
        guard.note_denied(_denied(), "reading sync state")
        guard.note_denied(_denied(), "writing state.json")
    warns = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name == fm_mod.logger.name
    ]
    assert len(warns) == 1, "guard must log exactly once per degradation"
    assert guard.is_degraded
    assert guard.kind == "permission"


def test_reason_is_platform_appropriate(guard: _NotesAccessGuard) -> None:
    guard.note_denied(_denied(), "probe")
    reason = guard.reason or ""
    if sys.platform == "darwin":
        assert "Full Disk Access" in reason
    else:
        assert "Full Disk Access" not in reason
        assert "permission" in reason.lower()


def test_missing_dir_is_distinct_kind(guard: _NotesAccessGuard, tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    guard.note_missing(missing)
    assert guard.is_degraded
    assert guard.kind == "missing_dir"
    assert str(missing) in (guard.reason or "")


def test_ok_clears_and_relogs_on_next_denial(
    guard: _NotesAccessGuard, caplog
) -> None:
    guard.note_denied(_denied(), "op1")
    guard.note_ok()
    assert not guard.is_degraded
    assert guard.kind is None
    assert guard.reason is None
    with caplog.at_level(logging.WARNING, logger=fm_mod.logger.name):
        guard.note_denied(_denied(), "op2")
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_snapshot_shape(guard: _NotesAccessGuard) -> None:
    assert guard.snapshot() == {"degraded": False, "reason": None, "kind": None}
    guard.note_denied(_denied(), "op")
    snap = guard.snapshot()
    assert snap["degraded"] is True and snap["kind"] == "permission"
    assert isinstance(snap["reason"], str)


# ---------------------------------------------------------------------------
# Auto-sync skip + automatic recovery window
# ---------------------------------------------------------------------------


def test_should_skip_sync_backoff_and_probe_window(
    guard: _NotesAccessGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(fm_mod.time, "monotonic", lambda: now[0])

    assert guard.should_skip_sync() is False  # healthy → never skip

    guard.note_denied(_denied(), "op")
    assert guard.should_skip_sync() is True  # inside backoff → quiet skip

    now[0] += fm_mod._RECHECK_INTERVAL_S + 1
    assert guard.should_skip_sync() is False  # one probe op allowed through
    assert guard.should_skip_sync() is True  # window reset — quiet again


def test_sync_engine_access_skipped_uses_guard(
    guard: _NotesAccessGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.documents import sync_engine as se_mod

    monkeypatch.setattr(se_mod, "notes_access_guard", guard)
    guard.note_denied(_denied(), "op")
    skipped = se_mod.SyncEngine._access_skipped()
    assert skipped is not None
    assert skipped["notes_access_degraded"] is True
    assert skipped["sync_skipped"] is True
    assert skipped["reason"] == guard.reason


# ---------------------------------------------------------------------------
# Active probe — "Check again" / "Create folder"
# ---------------------------------------------------------------------------


def test_probe_missing_dir(guard: _NotesAccessGuard, tmp_path: Path) -> None:
    missing = tmp_path / "notes"
    snap = probe_notes_access(missing)
    assert snap == {
        "degraded": True,
        "reason": f"Notes folder does not exist: {missing}",
        "kind": "missing_dir",
    }


def test_probe_create_missing_dir_recovers(
    guard: _NotesAccessGuard, tmp_path: Path
) -> None:
    missing = tmp_path / "notes"
    probe_notes_access(missing)  # degrade first
    snap = probe_notes_access(missing, create_missing=True)
    assert missing.is_dir()
    assert snap["degraded"] is False and snap["kind"] is None


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="chmod-based denial needs non-root POSIX"
)
def test_probe_permission_denied_then_recovers(
    guard: _NotesAccessGuard, tmp_path: Path
) -> None:
    locked = tmp_path / "notes"
    locked.mkdir()
    locked.chmod(0)
    try:
        snap = probe_notes_access(locked)
        assert snap["degraded"] is True and snap["kind"] == "permission"
    finally:
        locked.chmod(0o755)
    snap = probe_notes_access(locked)
    assert snap["degraded"] is False, "probe must clear state the moment access is back"


def test_probe_healthy_dir_is_clean(guard: _NotesAccessGuard, tmp_path: Path) -> None:
    snap = probe_notes_access(tmp_path)
    assert snap == {"degraded": False, "reason": None, "kind": None}
