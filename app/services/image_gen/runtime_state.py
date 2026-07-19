"""Durable state and immutable-slot primitives for the managed media runtime.

The legacy ``.install-complete`` file is deliberately only *evidence* inside a
slot.  Readiness is authoritative only when ``state.json`` points at that slot
and the slot carries an exact, platform-matching manifest.
"""

from __future__ import annotations

import json
import hashlib
import os
import platform
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.services.optional_packages.core import packages_dir


RUNTIME_MANIFEST_REVISION = 1
SLOT_MANIFEST = ".runtime-manifest.json"
INSTALL_EVIDENCE = ".install-complete"


class RuntimePhase(StrEnum):
    ABSENT = "absent"
    INSTALLING = "installing"
    UPDATING = "updating"
    REPAIRING = "repairing"
    VALIDATING = "validating"
    ACTIVATING = "activating"
    RESTART_REQUIRED = "restart_required"
    READY = "ready"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass(slots=True)
class RuntimeSnapshot:
    state: RuntimePhase = RuntimePhase.ABSENT
    runtime_revision: str | None = None
    manifest_revision: int = RUNTIME_MANIFEST_REVISION
    operation: str | None = None
    attempt_id: str | None = None
    stage: str = ""
    percent: float = 0.0
    message: str = "Managed media runtime is not installed."
    failure_code: str | None = None
    failure_detail: str | None = None
    active_slot: str | None = None
    last_known_good_slot: str | None = None
    candidate_slot: str | None = None
    packages: dict[str, str] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    @property
    def ready(self) -> bool:
        return self.state is RuntimePhase.READY and self.active_slot is not None

    @property
    def requires_repair(self) -> bool:
        return self.state in {RuntimePhase.FAILED, RuntimePhase.ROLLED_BACK}

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["ready"] = self.ready
        payload["requires_repair"] = self.requires_repair
        payload["repairable"] = self.requires_repair
        return payload


def runtime_control_dir() -> Path:
    return packages_dir("image-gen-runtime")


def runtime_slots_dir() -> Path:
    return runtime_control_dir() / "slots"


def runtime_state_path() -> Path:
    return runtime_control_dir() / "state.json"


def runtime_lock_path() -> Path:
    return runtime_control_dir() / "runtime.lock"


def current_runtime_contract() -> dict[str, str | int]:
    return {
        "manifest_revision": RUNTIME_MANIFEST_REVISION,
        "python_abi": sys.implementation.cache_tag or "unknown",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform,
        "machine": platform.machine().lower(),
    }


def _slot_path(slot: str) -> Path:
    if Path(slot).name != slot or slot in {"", ".", ".."}:
        raise ValueError(f"Invalid runtime slot name: {slot!r}")
    root = runtime_slots_dir().resolve(strict=False)
    path = (root / slot).resolve(strict=False)
    if path.parent != root:
        raise ValueError(f"Runtime slot escapes slot root: {slot!r}")
    return path


def slot_path(slot: str) -> Path:
    return _slot_path(slot)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        temp.unlink(missing_ok=True)


def read_snapshot() -> RuntimeSnapshot:
    path = runtime_state_path()
    if not path.exists():
        return RuntimeSnapshot()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        snapshot = RuntimeSnapshot(
            state=RuntimePhase(raw["state"]),
            runtime_revision=(
                str(raw["runtime_revision"])
                if raw.get("runtime_revision") is not None
                else None
            ),
            manifest_revision=int(raw["manifest_revision"]),
            operation=raw.get("operation"),
            attempt_id=raw.get("attempt_id"),
            stage=str(raw.get("stage", "")),
            percent=float(raw.get("percent", 0.0)),
            message=str(raw.get("message", "")),
            failure_code=raw.get("failure_code"),
            failure_detail=raw.get("failure_detail"),
            active_slot=raw.get("active_slot"),
            last_known_good_slot=raw.get("last_known_good_slot"),
            candidate_slot=raw.get("candidate_slot"),
            packages={str(k): str(v) for k, v in raw.get("packages", {}).items()},
            updated_at=float(raw.get("updated_at", 0.0)),
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return RuntimeSnapshot(
            state=RuntimePhase.FAILED,
            message="Managed media runtime state is corrupt.",
            failure_code="state_corrupt",
            failure_detail=str(exc),
        )
    if (
        snapshot.manifest_revision != RUNTIME_MANIFEST_REVISION
    ):
        snapshot.state = RuntimePhase.FAILED
        snapshot.message = "Managed media runtime manifest revision is unsupported."
        snapshot.failure_code = "revision_mismatch"
        snapshot.failure_detail = (
            f"expected manifest revision {RUNTIME_MANIFEST_REVISION}, got "
            f"{snapshot.manifest_revision}"
        )
    return snapshot


def write_snapshot(snapshot: RuntimeSnapshot) -> None:
    snapshot.updated_at = time.time()
    _atomic_json_write(runtime_state_path(), snapshot.to_dict())


def write_slot_manifest(
    slot_dir: Path,
    *,
    runtime_revision: str,
    packages: dict[str, str],
    target: str,
    record_hashes: dict[str, str] | None = None,
) -> None:
    payload: dict[str, Any] = {
        **current_runtime_contract(),
        "runtime_revision": runtime_revision,
        "target": target,
        "packages": packages,
        "record_hashes": record_hashes or {},
        "verified_at": time.time(),
    }
    _atomic_json_write(slot_dir / SLOT_MANIFEST, payload)


def validate_slot(
    slot: str,
    *,
    expected_revision: str | None = None,
    expected_packages: dict[str, str] | None = None,
    expected_target: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    try:
        path = _slot_path(slot)
    except ValueError as exc:
        return False, str(exc), {}
    manifest_path = path / SLOT_MANIFEST
    evidence_path = path / INSTALL_EVIDENCE
    if not evidence_path.is_file():
        return False, f"slot {slot} has no install evidence", {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"slot {slot} manifest cannot be read: {exc}", {}
    expected = current_runtime_contract()
    mismatches = [
        f"{key}={manifest.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if manifest.get(key) != value
    ]
    if mismatches:
        return False, "; ".join(mismatches), manifest
    if expected_revision is not None and manifest.get("runtime_revision") != expected_revision:
        return False, (
            f"runtime_revision={manifest.get('runtime_revision')!r} "
            f"(expected {expected_revision!r})"
        ), manifest
    if expected_target is not None and manifest.get("target") != expected_target:
        return False, (
            f"target={manifest.get('target')!r} (expected {expected_target!r})"
        ), manifest
    packages = manifest.get("packages")
    if not isinstance(packages, dict) or not packages:
        return False, f"slot {slot} manifest has no resolved packages", manifest
    if expected_packages is not None and packages != expected_packages:
        return False, "slot package versions do not match the release contract", manifest
    record_hashes = manifest.get("record_hashes", {})
    if not isinstance(record_hashes, dict):
        return False, "slot record_hashes is not an object", manifest
    for relative, expected_hash in record_hashes.items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            return False, "slot record hash entry is malformed", manifest
        candidate = (path / relative).resolve(strict=False)
        if not candidate.is_relative_to(path) or not candidate.is_file():
            return False, f"slot record is missing or escapes slot: {relative}", manifest
        actual_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        normalized = expected_hash.removeprefix("sha256:")
        if actual_hash != normalized:
            return False, f"slot record hash mismatch: {relative}", manifest
    return True, "", manifest


def authoritative_snapshot() -> RuntimeSnapshot:
    snapshot = read_snapshot()
    selected = (
        snapshot.active_slot
        if snapshot.state is RuntimePhase.READY
        else snapshot.candidate_slot
        if snapshot.state is RuntimePhase.RESTART_REQUIRED
        else None
    )
    if selected is None:
        return snapshot
    valid, reason, manifest = validate_slot(
        selected, expected_revision=snapshot.runtime_revision
    )
    if not valid:
        snapshot.state = RuntimePhase.FAILED
        snapshot.message = "Managed media runtime evidence failed validation."
        snapshot.failure_code = "manifest_invalid"
        snapshot.failure_detail = reason
        return snapshot
    if snapshot.state is RuntimePhase.READY:
        snapshot.packages = {
            str(k): str(v) for k, v in manifest.get("packages", {}).items()
        }
    return snapshot


def active_slot_path() -> Path | None:
    snapshot = authoritative_snapshot()
    if not snapshot.ready or snapshot.active_slot is None:
        return None
    return _slot_path(snapshot.active_slot)


def create_staging_slot() -> tuple[str, Path]:
    root = runtime_slots_dir()
    root.mkdir(parents=True, exist_ok=True)
    name = f".staging-{uuid.uuid4().hex}"
    path = root / name
    path.mkdir()
    return name, path


def finalize_staging_slot(staging: Path, runtime_revision: str) -> tuple[str, Path]:
    root = runtime_slots_dir().resolve(strict=False)
    resolved = staging.resolve(strict=False)
    if resolved.parent != root or not resolved.name.startswith(".staging-"):
        raise ValueError(f"Not a managed staging slot: {staging}")
    safe_revision = "".join(
        char if char.isalnum() or char in {"-", "_"} else "-"
        for char in runtime_revision[:48]
    )
    name = f"{safe_revision}-{uuid.uuid4().hex[:12]}"
    final = root / name
    os.replace(staging, final)
    return name, final


def remove_slot(path: Path) -> None:
    root = runtime_slots_dir().resolve(strict=False)
    resolved = path.resolve(strict=False)
    if resolved.parent != root:
        raise ValueError(f"Refusing to remove non-slot path: {path}")
    shutil.rmtree(path, ignore_errors=True)


class RuntimeFileLock(AbstractContextManager["RuntimeFileLock"]):
    """Cross-process exclusive lock for install, validation, and activation."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._handle: Any = None

    def __enter__(self) -> "RuntimeFileLock":
        path = runtime_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a+b")
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write(b"0")
            self._handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise TimeoutError("Timed out waiting for managed media runtime lock")
                time.sleep(0.1)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
