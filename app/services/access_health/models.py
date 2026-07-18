"""Data model for the canonical filesystem access-health service.

Every access observation is scoped to ONE resource and ONE capability.
There is deliberately no global boolean anywhere in this package — the
predecessor (`notes_access_guard` in documents/file_manager.py) was a single
global flag that unrelated paths poisoned and unrelated successes cleared,
and that turned every macOS permission error into a "Full Disk Access"
diagnosis. See FEATURE.md for the doctrine.
"""

from __future__ import annotations

import errno as _errno
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Capability(str, Enum):
    """The distinct filesystem operations whose access we track separately.

    A directory can be readable but not writable, enumerable but not
    replaceable — one capability's success must never clear another's failure.
    """

    ENUMERATE = "enumerate"
    READ = "read"
    CREATE = "create"
    WRITE = "write"
    REPLACE = "replace"
    DELETE = "delete"


class Provenance(str, Enum):
    """Where the resolved root path came from."""

    DEFAULT = "default"    # compiled default location
    OVERRIDE = "override"  # user-configured path, in effect
    FALLBACK = "fallback"  # user configured a path but it was unusable — default in use
    MAPPED = "mapped"      # user-mapped external directory (notes mappings)


def is_permission_error(exc: BaseException) -> bool:
    """True for POSIX/macOS permission denials (PermissionError or EPERM/EACCES)."""
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, OSError) and exc.errno in (_errno.EPERM, _errno.EACCES)


@dataclass(frozen=True)
class Observation:
    """One piece of evidence: an actual filesystem operation and its outcome."""

    resource_id: str
    path: str                 # absolute path the operation touched
    capability: Capability
    ok: bool
    errno: int | None         # errno for failures, None for successes
    error: str | None         # str(exc), truncated — evidence, not diagnosis
    op: str                   # human phrase: "renaming temp file onto state.json"
    source: str               # "file_manager" | "sync_engine" | "probe" | "startup" | "tool:file_ops" | "paths"
    at: float                 # time.time()
    generation: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "capability": self.capability.value,
            "ok": self.ok,
            "errno": self.errno,
            "error": self.error,
            "op": self.op,
            "source": self.source,
            "at": self.at,
            "generation": self.generation,
        }


# How many recent observations each resource retains for the API evidence view.
RECENT_OBSERVATIONS = 20


@dataclass
class ResourceHealth:
    """Live health of one registered resource (a directory root we care about)."""

    resource_id: str
    label: str
    root: str
    provenance: Provenance
    per_capability: dict[Capability, Observation] = field(default_factory=dict)
    last_success_at: float | None = None
    last_failure: Observation | None = None
    recent: deque[Observation] = field(
        default_factory=lambda: deque(maxlen=RECENT_OBSERVATIONS)
    )
    generation: int = 0
    # Backoff bookkeeping for should_attempt() — monotonic clock.
    last_denied_monotonic: float = 0.0

    @property
    def failing(self) -> list[Observation]:
        return [o for o in self.per_capability.values() if not o.ok]

    @property
    def status(self) -> str:
        """"ok" | "degraded" | "unknown" — derived, never stored."""
        if not self.per_capability:
            return "unknown"
        return "degraded" if self.failing else "ok"

    @property
    def kind(self) -> str | None:
        """Why degraded: "permission" | "missing_dir" | None.

        Permission wins over missing_dir when both are present — a permission
        denial can make the root *look* missing, and "grant access" is the
        actionable fix in that case.
        """
        failing = self.failing
        if not failing:
            return None
        if any(o.errno in (_errno.EPERM, _errno.EACCES) for o in failing):
            return "permission"
        if any(o.errno == _errno.ENOENT for o in failing):
            return "missing_dir"
        return "permission"

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "label": self.label,
            "root": self.root,
            "provenance": self.provenance.value,
            "status": self.status,
            "kind": self.kind,
            "capabilities": {
                cap.value: obs.to_dict() for cap, obs in self.per_capability.items()
            },
            "last_success_at": self.last_success_at,
            "last_failure": self.last_failure.to_dict() if self.last_failure else None,
            "recent": [o.to_dict() for o in self.recent],
            "generation": self.generation,
        }


class AccessDeniedError(RuntimeError):
    """The OS denied a filesystem operation on a registered resource.

    Replaces the old NotesAccessError. Carries the evidence (resource, path,
    capability, errno) and an evidence-based message — never a bare "grant
    Full Disk Access" claim unless the FDA diagnosis positively established it.
    """

    http_status = 503

    def __init__(
        self,
        message: str,
        *,
        resource_id: str,
        path: str = "",
        capability: Capability | None = None,
        errno: int | None = None,
        op: str = "",
    ) -> None:
        super().__init__(message)
        self.reason = message
        self.resource_id = resource_id
        self.path = path
        self.capability = capability
        self.errno = errno
        self.op = op


def now() -> float:
    return time.time()
