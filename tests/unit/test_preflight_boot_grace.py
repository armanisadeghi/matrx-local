"""Mid-boot engines must never be preflight-swept as orphans.

Engine startup takes ~40s before /health answers. Before the boot-grace
clause in ``_protected_engine_pids``, two engines booting concurrently would
each sweep the other as a "lingering process from a previous session"
(SIGTERM → SIGKILL) because neither answered /health yet — observed live
2026-08-19 as the "engine exited code=1 ~35s after spawn" boot crashes.
"""

from __future__ import annotations

import pytest

from app import preflight


def _engine_service() -> preflight.ManagedService:
    for svc in preflight.SERVICES:
        if svc.protect_live_owner:
            return svc
    raise AssertionError("no protect_live_owner service registered")


def _found(pid: int) -> preflight.FoundProcess:
    return preflight.FoundProcess(
        pid=pid,
        name="Matrx Engine",
        cmdline="/Applications/AI Matrx.app/.../Matrx Engine",
        service=_engine_service(),
    )


@pytest.fixture()
def unhealthy_world(monkeypatch: pytest.MonkeyPatch):
    """No discovery file, nothing answers /health, no ancestors resolvable."""
    monkeypatch.setattr(preflight, "read_discovery_file", lambda: None)
    monkeypatch.setattr(preflight, "_probe_matrx_health", lambda *a, **k: False)
    monkeypatch.setattr(preflight, "_ancestor_pids", lambda pid: set())


def test_young_unhealthy_engine_is_protected(unhealthy_world, monkeypatch):
    monkeypatch.setattr(preflight, "_process_age_seconds", lambda pid: 30.0)
    assert preflight._protected_engine_pids([_found(4242)]) == {4242}


def test_old_unhealthy_engine_is_not_protected(unhealthy_world, monkeypatch):
    monkeypatch.setattr(
        preflight,
        "_process_age_seconds",
        lambda pid: preflight.ENGINE_BOOT_GRACE_SECONDS + 60.0,
    )
    assert preflight._protected_engine_pids([_found(4242)]) == set()


def test_unreadable_age_is_not_protected(unhealthy_world, monkeypatch):
    # If the process vanished or its create_time is unreadable, fall back to
    # the pre-existing behavior (unprotected) rather than sparing a zombie.
    monkeypatch.setattr(preflight, "_process_age_seconds", lambda pid: None)
    assert preflight._protected_engine_pids([_found(4242)]) == set()
