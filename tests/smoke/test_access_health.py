"""Access-health service semantics — the contract that kills the false-FDA bug.

These tests pin the reconciliation rules of app/services/access_health:

  - Evidence is resource-scoped: a failure on one resource NEVER degrades
    another, and a success on one NEVER clears another's failure. (The old
    notes_access_guard violated both — one dead mapped dir produced a global
    "grant Full Disk Access" banner, and one healthy dir cleared a real
    denial.)
  - Evidence is capability-scoped: a WRITE success never clears an ENUMERATE
    failure.
  - Generation fencing: an operation that started before a reset/recheck
    cannot flip state the user has since re-verified.
  - The FDA message is a DIAGNOSIS asserted only on positive evidence, never
    a blanket translation of EACCES.

Pure-Python (no engine fixture) so they run on CI rung 1.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

from app.services.access_health.models import (
    AccessDeniedError,
    Capability,
    Provenance,
)
from app.services.access_health.probes import capability_probe, diagnose_fda
from app.services.access_health.service import AccessHealthService


def _svc_with(tmp_path: Path, *ids: str) -> AccessHealthService:
    svc = AccessHealthService()
    for rid in ids:
        root = tmp_path / rid
        root.mkdir(parents=True, exist_ok=True)
        svc.register(rid, resolver=lambda r=root: r, label=rid)
    return svc


def _deny(svc: AccessHealthService, rid: str, cap: Capability = Capability.WRITE) -> None:
    svc.record(
        rid, cap, ok=False, errno=errno.EACCES, error="denied", op="test", source="test"
    )


def _allow(svc: AccessHealthService, rid: str, cap: Capability = Capability.WRITE) -> None:
    svc.record(rid, cap, ok=True, op="test", source="test")


# ── Resource isolation ───────────────────────────────────────────────────────


def test_failure_on_one_resource_never_degrades_another(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "notes-canonical", "notes-mapping:f1:abcd1234")
    _deny(svc, "notes-mapping:f1:abcd1234")
    assert svc.is_degraded("notes-mapping:f1:abcd1234")
    assert not svc.is_degraded("notes-canonical")
    assert svc.health("notes-canonical")["status"] == "unknown"


def test_success_on_one_resource_never_clears_another(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a", "b")
    _deny(svc, "a")
    _allow(svc, "b")
    assert svc.is_degraded("a"), "b's success must not clear a's real denial"
    assert svc.health("b")["status"] == "ok"


# ── Capability isolation ─────────────────────────────────────────────────────


def test_write_success_does_not_clear_enumerate_failure(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    _deny(svc, "a", Capability.ENUMERATE)
    _allow(svc, "a", Capability.WRITE)
    assert svc.is_degraded("a")
    health = svc.health("a")
    assert health["capabilities"]["enumerate"]["ok"] is False
    assert health["capabilities"]["write"]["ok"] is True


def test_same_capability_success_clears_its_own_failure(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    _deny(svc, "a", Capability.WRITE)
    _allow(svc, "a", Capability.WRITE)
    assert not svc.is_degraded("a")
    assert svc.health("a")["status"] == "ok"


# ── Generation fencing ───────────────────────────────────────────────────────


def test_stale_generation_observation_cannot_flip_status(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    stale_gen = svc._generation  # generation before the recheck bump
    svc.recheck(["a"])  # bumps resource generation; tmp dir probes clean
    assert svc.health("a")["status"] == "ok"
    svc.record(
        "a",
        Capability.WRITE,
        ok=False,
        errno=errno.EACCES,
        error="stale in-flight denial",
        op="test",
        source="test",
        generation=stale_gen,
    )
    assert svc.health("a")["status"] == "ok", (
        "an op that started before the recheck must not re-poison verified state"
    )
    # ...but it stays visible as evidence.
    assert any(
        not o["ok"] for o in svc.health("a")["recent"]
    ), "fenced observation should still appear in recent evidence"


# ── Kind derivation ──────────────────────────────────────────────────────────


def test_kind_permission_vs_missing_dir(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a", "b")
    _deny(svc, "a")
    svc.record(
        "b", Capability.ENUMERATE, ok=False, errno=errno.ENOENT,
        error="gone", op="test", source="test",
    )
    assert svc.health("a")["kind"] == "permission"
    assert svc.health("b")["kind"] == "missing_dir"


# ── should_attempt backoff ───────────────────────────────────────────────────


def test_should_attempt_healthy_and_degraded_window(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    assert svc.should_attempt("a")  # unknown → attempt
    _deny(svc, "a")
    assert not svc.should_attempt("a")  # inside backoff window
    # Age the denial past the window.
    with svc._lock:
        svc._resources["a"].last_denied_monotonic = 0.0
    assert svc.should_attempt("a")  # one probe op allowed
    assert not svc.should_attempt("a")  # window re-armed


# ── observing() context manager ──────────────────────────────────────────────


def test_observing_permission_error_records_and_raises(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    with pytest.raises(AccessDeniedError) as exc_info:
        with svc.observing("a", Capability.REPLACE, op="renaming x", source="test"):
            raise PermissionError(errno.EACCES, "denied")
    assert svc.is_degraded("a")
    assert exc_info.value.resource_id == "a"
    assert exc_info.value.capability is Capability.REPLACE
    assert exc_info.value.errno == errno.EACCES
    assert exc_info.value.http_status == 503


def test_observing_success_records_ok(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    with svc.observing("a", Capability.WRITE, op="writing x", source="test"):
        pass
    assert svc.health("a")["capabilities"]["write"]["ok"] is True


def test_observing_non_permission_errors_pass_through(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    with pytest.raises(FileNotFoundError):
        with svc.observing("a", Capability.WRITE, op="w", source="test"):
            raise FileNotFoundError(errno.ENOENT, "nope")
    with pytest.raises(ValueError):
        with svc.observing("a", Capability.WRITE, op="w", source="test"):
            raise ValueError("not filesystem evidence")
    # Neither counted as access evidence for the write capability.
    assert svc.health("a")["capabilities"] == {} or (
        "write" not in svc.health("a")["capabilities"]
    )


# ── External tool I/O reconciliation ─────────────────────────────────────────


def test_note_external_io_matches_longest_root(tmp_path: Path) -> None:
    svc = AccessHealthService()
    outer = tmp_path / "notes"
    inner = outer / "mapped"
    inner.mkdir(parents=True)
    svc.register("outer", resolver=lambda: outer, label="outer")
    svc.register("inner", resolver=lambda: inner, label="inner")
    svc.note_external_io(inner / "x.md", Capability.WRITE)
    assert svc.health("inner")["capabilities"]["write"]["ok"] is True
    assert "write" not in svc.health("outer")["capabilities"]


def test_note_external_io_outside_roots_is_noop(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    svc.note_external_io("/somewhere/else/entirely.md", Capability.WRITE)
    assert svc.health("a")["capabilities"] == {}


def test_note_external_io_non_permission_exc_ignored(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    root = Path(svc.health("a")["root"])
    svc.note_external_io(
        root / "x.md", Capability.READ, exc=FileNotFoundError(errno.ENOENT, "gone")
    )
    assert svc.health("a")["capabilities"] == {}, (
        "a missing file is not access evidence"
    )


# ── Capability probe ─────────────────────────────────────────────────────────


def test_capability_probe_full_pass_and_no_litter(tmp_path: Path) -> None:
    results = capability_probe(tmp_path)
    assert set(results) == set(Capability)
    assert all(r["ok"] for r in results.values()), results
    assert list(tmp_path.iterdir()) == [], "probe must leave no artifacts behind"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores POSIX permissions")
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX chmod semantics")
def test_capability_probe_detects_readonly_dir(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)  # r-x: enumerable but not writable
    try:
        results = capability_probe(locked)
        assert results[Capability.ENUMERATE]["ok"] is True
        assert results[Capability.CREATE]["ok"] is False
        assert results[Capability.CREATE]["errno"] in (errno.EACCES, errno.EPERM)
    finally:
        locked.chmod(0o700)


def test_capability_probe_missing_dir(tmp_path: Path) -> None:
    results = capability_probe(tmp_path / "does-not-exist")
    assert all(not r["ok"] for r in results.values())
    assert results[Capability.ENUMERATE]["errno"] == errno.ENOENT


def test_capability_probe_create_missing(tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    results = capability_probe(target, create_missing=True)
    assert target.is_dir()
    assert all(r["ok"] for r in results.values()), results


# ── recheck / reset ──────────────────────────────────────────────────────────


def test_recheck_clears_stale_denial_on_healthy_dir(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    _deny(svc, "a")
    assert svc.is_degraded("a")
    svc.recheck(["a"])
    assert not svc.is_degraded("a")


def test_reset_clears_everything_and_reprobes(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a", "b")
    _deny(svc, "a")
    gen_before = svc._generation
    snapshot = svc.reset()
    assert svc._generation > gen_before
    assert not svc.is_degraded("a")
    assert snapshot["degraded"] is False
    for res in snapshot["resources"]:
        assert res["status"] == "ok"


# ── FDA diagnosis / message wording ──────────────────────────────────────────


def test_diagnose_fda_indeterminate_on_empty_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform != "darwin":
        assert diagnose_fda()["status"] == "not_applicable"
        return
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    diag = diagnose_fda(force=True)
    assert diag["status"] == "indeterminate"
    assert diag["evidence"], "evidence list must explain the verdict"


def test_message_never_asserts_fda_without_positive_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core wording contract: EACCES alone must NOT produce a definitive
    'Full Disk Access' claim. Only a positively-established denial may."""
    import app.services.access_health.service as service_mod

    svc = _svc_with(tmp_path, "a")
    _deny(svc, "a")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(service_mod.sys, "platform", "darwin", raising=False)

    monkeypatch.setattr(
        service_mod, "diagnose_fda", lambda **kw: {"status": "indeterminate"}
    )
    msg = svc.message("a")
    assert "Possible causes" in msg
    assert "is not granted" not in msg

    monkeypatch.setattr(
        service_mod, "diagnose_fda", lambda **kw: {"status": "granted"}
    )
    msg = svc.message("a")
    assert "IS granted" in msg, (
        "when FDA is provably granted the message must exonerate it"
    )

    monkeypatch.setattr(
        service_mod, "diagnose_fda", lambda **kw: {"status": "denied"}
    )
    msg = svc.message("a")
    assert "Full Disk Access is not granted" in msg


def test_message_non_darwin_never_mentions_fda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.access_health.service as service_mod

    svc = _svc_with(tmp_path, "a")
    _deny(svc, "a")
    monkeypatch.setattr(service_mod.sys, "platform", "linux", raising=False)
    assert "Full Disk Access" not in svc.message("a")


def test_missing_dir_message_names_path(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    svc.record(
        "a", Capability.ENUMERATE, ok=False, errno=errno.ENOENT,
        error="gone", op="checking", source="test", path=str(tmp_path / "a"),
    )
    msg = svc.message("a")
    assert "missing" in msg
    assert str(tmp_path / "a") in msg


# ── Transition callbacks ─────────────────────────────────────────────────────


def test_transition_callback_fires_on_recovery(tmp_path: Path) -> None:
    svc = _svc_with(tmp_path, "a")
    events: list[tuple[str, str, str]] = []
    svc.on_transition("a", lambda rid, old, new: events.append((rid, old, new)))
    # No loop captured: transitions queue. Sync callbacks fire once flushed —
    # but record() only queues when loop is None, so flush via capture_loop.
    _deny(svc, "a")
    _allow(svc, "a")
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        svc.capture_loop(loop)
    finally:
        loop.close()
    assert ("a", "unknown", "degraded") in events
    assert ("a", "degraded", "ok") in events
