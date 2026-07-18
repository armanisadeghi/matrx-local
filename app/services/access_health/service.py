"""AccessHealthService — the single backend authority for filesystem access health.

Doctrine (see FEATURE.md):
- Evidence is resource-scoped and capability-scoped. An observation mutates
  only its own resource; a success on one capability never clears a failure
  on another.
- Evidence (what failed, where, errno) is separate from diagnosis (why).
  The FDA diagnosis lives in probes.diagnose_fda() and is only ever asserted
  when positively established.
- In-memory only. Startup probes rebuild the full truth in milliseconds, and
  any persisted health snapshot is a lie the moment the user toggles a
  Privacy setting.
- This module is leaf-level: it must not import documents/paths/launcher at
  module import time (owners hand in resolver callables; launcher mirroring
  is lazy) — file_manager initialises at import and would cycle.
"""

from __future__ import annotations

import asyncio
import errno as _errno
import inspect
import logging
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from app.services.access_health.models import (
    AccessDeniedError,
    Capability,
    Observation,
    Provenance,
    ResourceHealth,
    is_permission_error,
    now,
)
from app.services.access_health.probes import capability_probe, diagnose_fda

logger = logging.getLogger(__name__)

# While a resource is degraded, let one operation through this often so a
# single probe op can detect restored access (same cadence the old guard used).
_RETRY_WINDOW_S = 60.0

TransitionCallback = Callable[[str, str, str], Any]  # (resource_id, old, new)


class AccessHealthService:
    """Registry of watched resources + their per-capability access evidence.

    Thread-safe: observations arrive from the async lifespan, request
    handlers, the watchfiles thread, and to_thread probe workers. All state
    mutation goes through one lock; callbacks and logging run outside it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resources: dict[str, ResourceHealth] = {}
        self._resolvers: dict[str, Callable[[], Path]] = {}
        self._provenance_fns: dict[str, Callable[[], Provenance]] = {}
        self._registry_services: dict[str, str] = {}  # resource_id → launcher service
        self._callbacks: dict[str, list[TransitionCallback]] = {}
        self._generation = 1
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_transitions: list[tuple[str, str, str]] = []

    # ── Registration ─────────────────────────────────────────────────────────

    def register(
        self,
        resource_id: str,
        *,
        resolver: Callable[[], Path],
        label: str,
        provenance: Provenance | Callable[[], Provenance] = Provenance.DEFAULT,
        registry_service: str | None = None,
    ) -> None:
        """Register (or re-register) a watched resource.

        ``resolver`` is called lazily on every root resolution so path
        overrides take effect without re-registration. ``registry_service``
        mirrors this resource's ok/degraded transitions into the launcher
        ServiceRegistry (e.g. notes-canonical → "notes_sync").
        """
        prov_fn = provenance if callable(provenance) else (lambda: provenance)
        with self._lock:
            self._resolvers[resource_id] = resolver
            self._provenance_fns[resource_id] = prov_fn
            if registry_service:
                self._registry_services[resource_id] = registry_service
            if resource_id not in self._resources:
                self._resources[resource_id] = ResourceHealth(
                    resource_id=resource_id,
                    label=label,
                    root=self._safe_resolve(resource_id),
                    provenance=prov_fn(),
                    generation=self._generation,
                )
            else:
                self._resources[resource_id].label = label
        logger.debug("[access] registered resource %s (%s)", resource_id, label)

    def unregister(self, resource_id: str) -> None:
        with self._lock:
            self._resources.pop(resource_id, None)
            self._resolvers.pop(resource_id, None)
            self._provenance_fns.pop(resource_id, None)
            self._registry_services.pop(resource_id, None)
            self._callbacks.pop(resource_id, None)
        logger.debug("[access] unregistered resource %s", resource_id)

    def registered_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._resources)

    def is_registered(self, resource_id: str) -> bool:
        with self._lock:
            return resource_id in self._resources

    def _safe_resolve(self, resource_id: str) -> str:
        resolver = self._resolvers.get(resource_id)
        if resolver is None:
            return ""
        try:
            return str(resolver())
        except Exception:
            logger.warning(
                "[access] resolver for %s failed", resource_id, exc_info=True
            )
            return ""

    # ── Event-loop plumbing (async transition callbacks from any thread) ─────

    def capture_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Capture the engine's event loop and flush queued transitions.

        Called once from the lifespan once the loop is running. Observations
        recorded before this (import-time _ensure_dirs, early probes) queue
        their transitions; they fire here.
        """
        self._loop = loop or asyncio.get_event_loop()
        with self._lock:
            pending, self._pending_transitions = self._pending_transitions, []
        for transition in pending:
            self._fire_callbacks(*transition)

    def on_transition(self, resource_id: str, callback: TransitionCallback) -> None:
        """Register a callback fired on every status change of *resource_id*.

        Callback signature: (resource_id, old_status, new_status). Sync
        callbacks run inline on the observing thread; coroutine callbacks are
        scheduled on the captured engine loop.
        """
        with self._lock:
            self._callbacks.setdefault(resource_id, []).append(callback)

    def _fire_callbacks(self, resource_id: str, old: str, new: str) -> None:
        with self._lock:
            callbacks = list(self._callbacks.get(resource_id, ()))
        for cb in callbacks:
            try:
                result = cb(resource_id, old, new)
                if inspect.iscoroutine(result):
                    if self._loop is not None and not self._loop.is_closed():
                        asyncio.run_coroutine_threadsafe(result, self._loop)
                    else:
                        result.close()
                        logger.warning(
                            "[access] dropped async transition callback for %s — "
                            "no captured event loop",
                            resource_id,
                        )
            except Exception:
                logger.warning(
                    "[access] transition callback for %s crashed", resource_id,
                    exc_info=True,
                )

    def _mirror_registry(self, resource_id: str, new_status: str) -> None:
        """Mirror ok/degraded into the launcher ServiceRegistry (lazy import)."""
        service = self._registry_services.get(resource_id)
        if not service:
            return
        try:
            from app.launcher import get_registry

            if new_status == "ok":
                get_registry().ready(service)
            elif new_status == "degraded":
                get_registry().degraded(
                    service, reason=self.message(resource_id)
                )
        except Exception:  # registry is best-effort — never mask the real error
            logger.debug(
                "[access] could not mirror %s → %s", resource_id, new_status,
                exc_info=True,
            )

    # ── Recording evidence ───────────────────────────────────────────────────

    def record(
        self,
        resource_id: str,
        capability: Capability,
        *,
        ok: bool,
        path: str = "",
        errno: int | None = None,
        error: str | None = None,
        op: str = "",
        source: str = "unknown",
        generation: int | None = None,
    ) -> None:
        """Record one observation. The ONLY mutation path for evidence."""
        with self._lock:
            resource = self._resources.get(resource_id)
            if resource is None:
                return
            gen = generation if generation is not None else self._generation
            obs = Observation(
                resource_id=resource_id,
                path=path or resource.root,
                capability=capability,
                ok=ok,
                errno=errno,
                error=(error or None) and str(error)[:300],
                op=op,
                source=source,
                at=now(),
                generation=gen,
            )
            resource.recent.append(obs)
            old_status = resource.status
            if gen < resource.generation:
                # Generation fencing: an op that started before a reset/recheck
                # must not flip state the user has since re-verified. It stays
                # visible in `recent` as evidence only.
                return
            resource.per_capability[capability] = obs
            if ok:
                resource.last_success_at = obs.at
            else:
                resource.last_failure = obs
                if obs.errno in (_errno.EPERM, _errno.EACCES):
                    resource.last_denied_monotonic = time.monotonic()
            new_status = resource.status
        if new_status != old_status:
            self._log_transition(resource_id, old_status, new_status, obs)
            self._mirror_registry(resource_id, new_status)
            if self._loop is None:
                with self._lock:
                    self._pending_transitions.append(
                        (resource_id, old_status, new_status)
                    )
            else:
                self._fire_callbacks(resource_id, old_status, new_status)

    def _log_transition(
        self, resource_id: str, old: str, new: str, obs: Observation
    ) -> None:
        if new == "degraded":
            logger.warning(
                "[access] %s → degraded: %s failed while %s (errno=%s: %s)",
                resource_id, obs.capability.value, obs.op, obs.errno, obs.error,
            )
        else:
            logger.info("[access] %s → %s (after %s)", resource_id, new, obs.op)

    @contextmanager
    def observing(
        self,
        resource_id: str,
        capability: Capability,
        *,
        op: str,
        source: str,
        path: str = "",
    ) -> Iterator[None]:
        """Wrap a real filesystem operation: clean exit records success;
        a permission error records failure and raises AccessDeniedError;
        other OSErrors pass through untouched (they are not access evidence).
        """
        with self._lock:
            gen = self._generation
        try:
            yield
        except OSError as e:
            if not is_permission_error(e):
                raise
            self.record(
                resource_id,
                capability,
                ok=False,
                path=path,
                errno=e.errno,
                error=str(e),
                op=op,
                source=source,
                generation=gen,
            )
            raise AccessDeniedError(
                self.message(resource_id),
                resource_id=resource_id,
                path=path or self._safe_resolve(resource_id),
                capability=capability,
                errno=e.errno,
                op=op,
            ) from e
        else:
            self.record(
                resource_id,
                capability,
                ok=True,
                path=path,
                op=op,
                source=source,
                generation=gen,
            )

    def note_external_io(
        self,
        abs_path: str | Path,
        capability: Capability,
        *,
        exc: BaseException | None = None,
        op: str = "",
        source: str = "tool:file_ops",
    ) -> None:
        """Feed evidence from generic tool I/O when the path falls under a
        registered resource root. No-op otherwise; never raises.

        This is how tool activity reconciles with the health view: an agent
        successfully writing into the notes tree IS proof of access.
        """
        try:
            p = str(Path(abs_path))
        except Exception:
            return
        if exc is not None and not is_permission_error(exc):
            return
        with self._lock:
            match: str | None = None
            match_root = ""
            for rid, resource in self._resources.items():
                root = resource.root
                if not root:
                    continue
                if p == root or p.startswith(root.rstrip("/") + "/"):
                    if len(root) > len(match_root):
                        match, match_root = rid, root
        if match is None:
            return
        errno_val = getattr(exc, "errno", None) if exc is not None else None
        self.record(
            match,
            capability,
            ok=exc is None,
            path=p,
            errno=errno_val,
            error=str(exc) if exc is not None else None,
            op=op or f"{capability.value} {p}",
            source=source,
        )

    # ── Gating ───────────────────────────────────────────────────────────────

    def should_attempt(self, resource_id: str) -> bool:
        """True when healthy/unknown; while degraded, True once per retry
        window so a single probe op can detect restored access."""
        with self._lock:
            resource = self._resources.get(resource_id)
            if resource is None or resource.status != "degraded":
                return True
            if time.monotonic() - resource.last_denied_monotonic >= _RETRY_WINDOW_S:
                resource.last_denied_monotonic = time.monotonic()
                return True
            return False

    def is_degraded(self, resource_id: str) -> bool:
        with self._lock:
            resource = self._resources.get(resource_id)
            return resource is not None and resource.status == "degraded"

    # ── Views ────────────────────────────────────────────────────────────────

    def health(self, resource_id: str) -> dict[str, Any] | None:
        with self._lock:
            resource = self._resources.get(resource_id)
            if resource is None:
                return None
            resource.root = self._safe_resolve(resource_id) or resource.root
            resource.provenance = self._provenance_fns[resource_id]()
            payload = resource.to_dict()
        payload["message"] = self._build_message(payload)
        return payload

    def snapshot(self) -> dict[str, Any]:
        """The full /access/health payload."""
        with self._lock:
            ids = sorted(self._resources)
            generation = self._generation
        resources = [h for rid in ids if (h := self.health(rid)) is not None]
        return {
            "generation": generation,
            "platform": sys.platform,
            "degraded": any(r["status"] == "degraded" for r in resources),
            "resources": resources,
            "fda": diagnose_fda() if sys.platform == "darwin" else None,
        }

    def message(self, resource_id: str) -> str:
        health = self.health(resource_id)
        if health is None:
            return "Unknown resource"
        return health["message"]

    def _build_message(self, payload: dict[str, Any]) -> str:
        """Evidence-based, diagnosis-aware wording. Never asserts FDA unless
        diagnose_fda() positively established the denial."""
        if payload["status"] == "ok":
            return f"{payload['label']} is accessible."
        if payload["status"] == "unknown":
            return f"{payload['label']} has not been checked yet."

        failure = payload.get("last_failure") or {}
        failing_caps = sorted(
            cap for cap, obs in payload.get("capabilities", {}).items()
            if not obs.get("ok")
        )
        where = failure.get("path") or payload["root"]
        if payload["kind"] == "missing_dir":
            return f"{payload['label']} is missing at {where}."

        detail = (
            f"The OS denied {', '.join(failing_caps) or 'access'} on {where}"
            f" (errno {failure.get('errno')})."
        )
        if sys.platform != "darwin":
            return (
                f"{detail} Check the folder's permissions and ownership."
            )
        fda = diagnose_fda()
        if fda["status"] == "denied":
            return (
                f"{detail} Full Disk Access is not granted — enable it for "
                "Matrx in System Settings > Privacy & Security."
            )
        if fda["status"] == "granted":
            return (
                f"{detail} Full Disk Access IS granted, so this is the "
                "folder's own permissions, ownership, or volume state — not "
                "a macOS privacy setting."
            )
        return (
            f"{detail} Possible causes: macOS Privacy controls (Full Disk "
            "Access), folder permissions, or ownership."
        )

    # ── Active probing / reset ───────────────────────────────────────────────

    def recheck(
        self,
        resource_ids: list[str] | None = None,
        *,
        create_missing: bool = False,
    ) -> dict[str, Any]:
        """Actively probe the given (or all) resources and record complete
        per-capability evidence. Blocking — run via asyncio.to_thread from
        request handlers."""
        ids = resource_ids if resource_ids is not None else self.registered_ids()
        for rid in ids:
            with self._lock:
                resource = self._resources.get(rid)
                if resource is None:
                    continue
                self._generation += 1
                resource.generation = self._generation
                gen = self._generation
            root = self._safe_resolve(rid)
            if not root:
                continue
            results = capability_probe(Path(root), create_missing=create_missing)
            for cap, res in results.items():
                self.record(
                    rid,
                    cap,
                    ok=res["ok"],
                    path=res["path"],
                    errno=res["errno"],
                    error=res["error"],
                    op=res["op"],
                    source="probe",
                    generation=gen,
                )
        if sys.platform == "darwin":
            diagnose_fda(force=True)
        return self.snapshot()

    def reset(self) -> dict[str, Any]:
        """Clear all evidence, bump the generation, and re-probe everything.

        This is the backend behind the UI's access-health reset: after it, the
        health view reflects ONLY fresh probes — no stale denials, no stale
        successes.
        """
        with self._lock:
            self._generation += 1
            for resource in self._resources.values():
                resource.per_capability.clear()
                resource.recent.clear()
                resource.last_failure = None
                resource.last_success_at = None
                resource.last_denied_monotonic = 0.0
                resource.generation = self._generation
        logger.info("[access] state reset — re-probing all resources")
        return self.recheck()


# ── Singleton ────────────────────────────────────────────────────────────────

_service: AccessHealthService | None = None
_service_lock = threading.Lock()


def get_access_health() -> AccessHealthService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = AccessHealthService()
    return _service
