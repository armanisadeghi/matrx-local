"""Active probes: capability-accurate directory probing and macOS FDA diagnosis.

Two jobs, kept separate on purpose:

1. capability_probe() gathers EVIDENCE — it exercises the exact operations the
   documents/sync pipeline needs (enumerate, create, write+fsync, atomic
   replace, delete) using a disposable probe file. A directory that is
   readable but not writable, or writable but not replaceable, is caught here;
   the old scandir-only probe could not see those.

2. diagnose_fda() forms a DIAGNOSIS — it positively establishes whether macOS
   Full Disk Access is granted to THIS process by probing FDA-protected
   locations. Only a positive "denied" verdict justifies telling the user to
   grant Full Disk Access. A generic EACCES on some folder is NOT proof of
   missing FDA (read-only volumes, ACLs, ownership, unmounted drives all
   produce the same errno) — the predecessor's core mistake.
"""

from __future__ import annotations

import errno as _errno
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from app.services.access_health.models import (
    Capability,
    is_permission_error,
)

logger = logging.getLogger(__name__)

_PROBE_PREFIX = ".matrx-probe-"


def capability_probe(
    root: Path, *, create_missing: bool = False
) -> dict[Capability, dict[str, Any]]:
    """Exercise every tracked capability against *root* with a disposable file.

    Never touches user content. Probe artifacts are dot-prefixed and
    pid-suffixed, and removed in ``finally`` — at worst a crash leaves one
    hidden temp file behind (cloud-sync folders may briefly observe it).

    Returns {capability: {"ok": bool, "errno": int|None, "error": str|None,
    "op": str, "path": str}} for every capability, so the caller can record a
    complete, per-capability evidence set in one shot.
    """
    results: dict[Capability, dict[str, Any]] = {}

    def _fail(cap: Capability, op: str, exc: OSError, path: Path) -> None:
        results[cap] = {
            "ok": False,
            "errno": exc.errno,
            "error": str(exc)[:300],
            "op": op,
            "path": str(path),
        }

    def _ok(cap: Capability, op: str, path: Path) -> None:
        results[cap] = {
            "ok": True,
            "errno": None,
            "error": None,
            "op": op,
            "path": str(path),
        }

    def _fail_all(op: str, exc: OSError, path: Path) -> dict[Capability, dict[str, Any]]:
        for cap in Capability:
            _fail(cap, op, exc, path)
        return results

    # Root existence / creation
    try:
        exists = root.exists()
    except OSError as e:
        return _fail_all(f"checking {root} exists", e, root)
    if not exists:
        if create_missing:
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return _fail_all(f"creating {root}", e, root)
        else:
            missing = OSError(_errno.ENOENT, "directory does not exist")
            return _fail_all(f"checking {root} exists", missing, root)

    # ENUMERATE
    try:
        with os.scandir(root) as it:
            next(it, None)
        _ok(Capability.ENUMERATE, f"listing {root}", root)
    except OSError as e:
        _fail(Capability.ENUMERATE, f"listing {root}", e, root)

    # CREATE + WRITE + REPLACE + DELETE via a disposable probe file
    tmp_path: str | None = None
    replaced_path: Path | None = None
    try:
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=root, prefix=_PROBE_PREFIX, suffix=f"-{os.getpid()}.tmp"
            )
            _ok(Capability.CREATE, f"creating probe file in {root}", root)
        except OSError as e:
            _fail(Capability.CREATE, f"creating probe file in {root}", e, root)
            # Without a file, the remaining write-path capabilities share the
            # same evidence — record them against the same failed op.
            for cap in (Capability.WRITE, Capability.REPLACE, Capability.DELETE):
                _fail(cap, f"creating probe file in {root}", e, root)
            # READ mirrors ENUMERATE when we can't make our own file to read.
            results[Capability.READ] = dict(results[Capability.ENUMERATE])
            return results

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("matrx access probe")
                f.flush()
                os.fsync(f.fileno())
            _ok(Capability.WRITE, f"writing probe file in {root}", root)
        except OSError as e:
            _fail(Capability.WRITE, f"writing probe file in {root}", e, root)

        # READ back our own probe file.
        try:
            Path(tmp_path).read_text(encoding="utf-8")
            _ok(Capability.READ, f"reading probe file in {root}", root)
        except OSError as e:
            _fail(Capability.READ, f"reading probe file in {root}", e, root)

        # REPLACE — the historical killer: temp file writes fine, the
        # os.replace() rename onto the target is what macOS denies.
        replaced_path = root / f"{_PROBE_PREFIX}{os.getpid()}.replaced"
        try:
            os.replace(tmp_path, replaced_path)
            tmp_path = None  # consumed by the rename
            _ok(Capability.REPLACE, f"atomically replacing probe file in {root}", root)
        except OSError as e:
            _fail(Capability.REPLACE, f"atomically replacing probe file in {root}", e, root)
            replaced_path = None

        # DELETE
        target = replaced_path if replaced_path is not None else (
            Path(tmp_path) if tmp_path else None
        )
        if target is not None:
            try:
                os.unlink(target)
                if replaced_path is not None:
                    replaced_path = None
                else:
                    tmp_path = None
                _ok(Capability.DELETE, f"deleting probe file in {root}", root)
            except OSError as e:
                _fail(Capability.DELETE, f"deleting probe file in {root}", e, root)
    finally:
        # Belt and braces: never leave probe litter behind.
        for leftover in (tmp_path, replaced_path):
            if leftover:
                try:
                    os.unlink(leftover)
                except OSError:
                    pass

    return results


# ── macOS Full Disk Access diagnosis ─────────────────────────────────────────

_FDA_CACHE_TTL_S = 30.0
_fda_lock = threading.Lock()
_fda_cache: dict[str, Any] | None = None
_fda_cache_at = 0.0


def diagnose_fda(*, force: bool = False) -> dict[str, Any]:
    """Positively establish macOS Full Disk Access state for THIS process.

    Probes FDA-protected locations (the engine helper inherits the parent
    bundle's TCC grant, so probing from the engine process is valid evidence
    for the engine's own I/O):

      - ~/Library/Messages/chat.db  (sqlite read-only open)
      - ~/Library/Mail              (scandir)
      - ~/Library/Safari            (scandir)

    Verdicts:
      granted        — at least one protected probe SUCCEEDED. Any permission
                       failure elsewhere is therefore NOT an FDA problem.
      denied         — at least one protected location exists and raised
                       EPERM/EACCES, and none succeeded. The FDA remediation
                       message is justified.
      indeterminate  — nothing conclusive (fresh account with no Messages/
                       Mail/Safari data). FDA may only be listed as a
                       *possible* cause, never asserted.
      not_applicable — non-macOS platforms.

    Cached for 30s (TCC toggles take effect immediately; the cache only
    bounds probe frequency under polling).
    """
    global _fda_cache, _fda_cache_at
    if sys.platform != "darwin":
        return {
            "status": "not_applicable",
            "evidence": [],
            "source": "engine-process probe",
            "checked_at": time.time(),
        }

    with _fda_lock:
        if (
            not force
            and _fda_cache is not None
            and time.monotonic() - _fda_cache_at < _FDA_CACHE_TTL_S
        ):
            return _fda_cache

    home = Path.home()
    evidence: list[dict[str, Any]] = []
    granted = False
    denied_seen = False

    chat_db = home / "Library" / "Messages" / "chat.db"
    try:
        if chat_db.exists():
            # sqlite read-only open — same technique the permissions checker
            # uses; succeeds only with FDA.
            conn = sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True, timeout=1.0)
            try:
                conn.execute("SELECT 1").fetchone()
            finally:
                conn.close()
            evidence.append({"probe": str(chat_db), "result": "ok"})
            granted = True
        else:
            evidence.append({"probe": str(chat_db), "result": "absent"})
    except (OSError, sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        if isinstance(e, OSError) and not is_permission_error(e):
            evidence.append({"probe": str(chat_db), "result": f"error: {e}"})
        else:
            evidence.append({"probe": str(chat_db), "result": "denied"})
            denied_seen = True

    for protected in (home / "Library" / "Mail", home / "Library" / "Safari"):
        if granted:
            break
        try:
            if not protected.exists():
                evidence.append({"probe": str(protected), "result": "absent"})
                continue
            with os.scandir(protected) as it:
                next(it, None)
            evidence.append({"probe": str(protected), "result": "ok"})
            granted = True
        except OSError as e:
            if is_permission_error(e):
                evidence.append({"probe": str(protected), "result": "denied"})
                denied_seen = True
            else:
                evidence.append({"probe": str(protected), "result": f"error: {e}"})

    if granted:
        status = "granted"
    elif denied_seen:
        status = "denied"
    else:
        status = "indeterminate"

    result = {
        "status": status,
        "evidence": evidence,
        "source": "engine-process probe",
        "checked_at": time.time(),
    }
    with _fda_lock:
        _fda_cache = result
        _fda_cache_at = time.monotonic()
    return result


def fda_hint() -> str:
    """Shared, diagnosis-aware hint for tools that hit TCC-protected data.

    Replaces per-tool hardcoded "grant Full Disk Access" strings: when the
    engine can positively see that FDA is already granted, telling the user to
    grant it again is wrong — the failure has another cause.
    """
    if sys.platform != "darwin":
        return "The operating system denied access — check file permissions and ownership."
    diag = diagnose_fda()
    if diag["status"] == "granted":
        return (
            "Full Disk Access is already granted, so this failure has another "
            "cause — check the specific file/folder's permissions, ownership, "
            "or whether its volume is mounted."
        )
    if diag["status"] == "denied":
        return (
            "Grant Full Disk Access to Matrx in System Settings > Privacy & "
            "Security > Full Disk Access, then try again."
        )
    return (
        "macOS may be blocking access. If prompted, grant Matrx access in "
        "System Settings > Privacy & Security (Full Disk Access covers "
        "protected folders like Messages, Mail, and Documents)."
    )
