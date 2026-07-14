"""app/launcher.py — Engine-side service registry, lifecycle, and diagnostics.

Owns: every subprocess and long-running task the engine spawns
      (cloudflared tunnel, scraper engine, HTTP proxy, scheduler,
      file watchers, download manager, sync engine, etc.).

Exposes:
    - ServiceRegistry: in-memory state of every managed service
    - dump_diagnostics(): full snapshot of engine state for failure debugging
    - get_registry(): singleton accessor

Surfaced over HTTP by app/api/admin_routes.py:
    GET  /admin/status     → JSON snapshot of every service
    POST /admin/shutdown   → graceful shutdown (cascade through children, reply when done)
    POST /admin/diagnose   → write a diagnostic snapshot and return its path

────────────────────────────────────────────────────────────────────────────
Ownership principle (NON-NEGOTIABLE — see docs/official/lifecycle-ownership.md):

    Each level only touches its own children, AND when the parent triggers a
    start or stop, that level must cascade the same to its children before
    reporting done.

This file is the Python-side embodiment of that principle. Rust spawns the
engine and signals the engine to start/stop. The engine then cascades to
every child it owns (tunnel, proxy, scraper, etc.). Rust never reaches
across to kill the engine's children directly — that races against the
engine's own teardown and produces "ended unexpectedly" errors.

The structured `[launcher] <service> → <state>` log lines emitted from this
module are the source of truth for what's running and what state it's in.
Do not duplicate them in feature modules; call into the registry instead.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import psutil

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────

_HOME = Path(
    os.environ.get("USERPROFILE") or os.environ.get("HOME") or "."
).expanduser()
# Honors MATRX_HOME_DIR so a dev engine's diagnostics land in its own home
# (~/.matrx-dev), never the installed app's (MXL-D-043 isolation).
_MATRX_HOME = Path(os.environ.get("MATRX_HOME_DIR", str(_HOME / ".matrx")))
DIAGNOSTICS_DIR = _MATRX_HOME / "diagnostics"

# How many diagnostic snapshots to keep on disk before pruning oldest.
_MAX_DIAGNOSTICS = 50


# ──────────────────────────────────────────────────────────────────────────────
# Service state
# ──────────────────────────────────────────────────────────────────────────────


class ServiceState(str, Enum):
    """Lifecycle state of one managed service."""

    PENDING = "pending"  # registered but not yet started
    STARTING = "starting"  # start() in progress
    READY = "ready"  # start() succeeded, healthy
    DEGRADED = "degraded"  # running but reduced functionality (e.g. tunnel started but no URL captured)
    STOPPING = "stopping"  # stop() in progress
    STOPPED = "stopped"  # stop() succeeded, no longer running
    FAILED = "failed"  # start() or stop() failed


@dataclass
class ServiceRecord:
    """Live state of one managed service.

    Records are mutated in-place by registry methods; consumers should
    treat them as snapshot data and not hold references across state
    transitions (use registry.snapshot() for stable views).
    """

    name: str
    state: ServiceState = ServiceState.PENDING
    started_at: Optional[float] = None
    ready_at: Optional[float] = None
    stopped_at: Optional[float] = None
    pid: Optional[int] = None
    port: Optional[int] = None
    url: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        running_states = {
            ServiceState.STARTING,
            ServiceState.READY,
            ServiceState.DEGRADED,
            ServiceState.STOPPING,
        }
        uptime_s: Optional[float] = None
        if self.started_at and self.state in running_states:
            uptime_s = time.time() - self.started_at
        elif self.started_at and self.stopped_at:
            uptime_s = self.stopped_at - self.started_at

        return {
            "name": self.name,
            "state": self.state.value,
            "started_at": self.started_at,
            "ready_at": self.ready_at,
            "stopped_at": self.stopped_at,
            "uptime_s": uptime_s,
            "pid": self.pid,
            "port": self.port,
            "url": self.url,
            "error": self.error,
            "metadata": self.metadata,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────


class ServiceRegistry:
    """In-memory registry of every service the engine owns.

    Designed to be called from any context — async lifespan code, sync signal
    handlers, the admin endpoint thread. All mutation goes through a single
    threading.Lock since we cannot hold an asyncio.Lock from a sync signal
    handler. The lock is held for trivial state mutations only; logging and
    diagnostic dumps run outside the lock to avoid blocking the event loop.

    Service names are expected to be short, stable identifiers — they appear
    in logs and in the /admin/status JSON. Conventions:
        "preflight"        orphan cleanup at startup
        "database"         local SQLite
        "downloads"        universal download manager
        "ai_engine"        matrx-ai initialization
        "tools"            tool registry
        "sync_engine"      cloud-to-local sync
        "scraper"          Playwright scraper
        "scheduler"        scheduled tasks restorer
        "proxy"            HTTP proxy server
        "tunnel"           cloudflared tunnel
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._services: dict[str, ServiceRecord] = {}
        self._engine_started_at: float = time.time()

    # ── State transitions ────────────────────────────────────────────────────

    def register(self, name: str) -> ServiceRecord:
        """Pre-register a service so it appears in /admin/status as PENDING.

        Optional — services that go straight to starting() will be auto-created.
        Useful when the registry should report "we plan to start X" before X
        actually begins booting.
        """
        with self._lock:
            r = self._services.get(name)
            if r is None:
                r = ServiceRecord(name=name)
                self._services[name] = r
            return r

    def starting(self, name: str, **fields: Any) -> None:
        """Mark the service as starting. Records started_at and any fields.

        Recognized field shortcuts: port, pid, url. Anything else is folded
        into metadata.
        """
        with self._lock:
            r = self._services.setdefault(name, ServiceRecord(name=name))
            r.state = ServiceState.STARTING
            r.started_at = time.time()
            self._apply_fields(r, fields)
        logger.info("[launcher] %s → starting%s", name, _meta_tail(fields))

    def ready(self, name: str, **fields: Any) -> None:
        """Mark the service as ready (healthy). Records ready_at and any fields."""
        with self._lock:
            r = self._services.setdefault(name, ServiceRecord(name=name))
            r.state = ServiceState.READY
            r.ready_at = time.time()
            self._apply_fields(r, fields)
            took = (r.ready_at - r.started_at) if r.started_at else None
        extras = []
        if took is not None:
            extras.append(f"took {took:.1f}s")
        for k in ("port", "url", "pid"):
            v = fields.get(k)
            if v:
                extras.append(f"{k}={v}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        logger.info("[launcher] %s → ✓ ready%s", name, suffix)

    def degraded(self, name: str, reason: str, **fields: Any) -> None:
        """Mark the service as running but with reduced functionality."""
        with self._lock:
            r = self._services.setdefault(name, ServiceRecord(name=name))
            r.state = ServiceState.DEGRADED
            r.error = reason
            self._apply_fields(r, fields)
        logger.warning("[launcher] %s → ⚠ degraded — %s", name, reason)

    def failed(self, name: str, error: str | Exception, **fields: Any) -> None:
        """Mark the service as failed and emit a diagnostic snapshot.

        On every FAILED transition we automatically write a diagnostic
        snapshot to ~/.matrx/diagnostics/ — the user's request for
        "log the living shit out of every value we have when something
        goes wrong" lives here. The path is logged at ERROR level so the
        operator can find it immediately.
        """
        err_str = str(error)
        if isinstance(error, BaseException):
            tb = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
        else:
            tb = None
        with self._lock:
            r = self._services.setdefault(name, ServiceRecord(name=name))
            r.state = ServiceState.FAILED
            r.error = err_str
            r.stopped_at = time.time()
            self._apply_fields(r, fields)
            if tb:
                r.metadata["traceback"] = tb
        logger.error("[launcher] %s → ✗ FAILED — %s", name, err_str)

        # Diagnostic dump runs OUTSIDE the lock — gathering psutil data and
        # writing a multi-KB JSON file is too slow to hold a lock for.
        try:
            path = dump_diagnostics(self, focus=name, error=err_str)
            logger.error("[launcher] %s → diagnostic snapshot: %s", name, path)
        except Exception:
            logger.exception("[launcher] %s → diagnostic dump itself failed", name)

    def current_state(self, name: str) -> str | None:
        """Return the service's current state value, or None if unknown.

        Lets shutdown code skip the STOPPING→STOPPED transition for services
        that never started (disabled in settings) or already FAILED — driving
        them to STOPPED erased that history from the final snapshot.
        """
        with self._lock:
            r = self._services.get(name)
            return r.state.value if r else None

    def stopping(self, name: str) -> None:
        """Mark the service as stopping. Idempotent."""
        with self._lock:
            r = self._services.setdefault(name, ServiceRecord(name=name))
            if r.state == ServiceState.STOPPED:
                return
            r.state = ServiceState.STOPPING
        logger.info("[launcher] %s → stopping", name)

    def stopped(self, name: str, **fields: Any) -> None:
        """Mark the service as cleanly stopped, optionally attaching metadata.

        Useful for child processes that exit during stop() — pass
        e.g. exit_code and recent_output so the diagnostic dump captures
        them instead of having to dig into per-service singletons.
        """
        with self._lock:
            r = self._services.setdefault(name, ServiceRecord(name=name))
            r.state = ServiceState.STOPPED
            r.stopped_at = time.time()
            took = (r.stopped_at - r.started_at) if r.started_at else None
            self._apply_fields(r, fields)
        suffix = f" (uptime {took:.1f}s)" if took is not None else ""
        logger.info("[launcher] %s → ✓ stopped%s", name, suffix)

    def annotate(self, name: str, **fields: Any) -> None:
        """Attach extra metadata to a service record without changing state.

        Useful for late-arriving diagnostic info (e.g. cloudflared's exit
        code surfaced after stop() completes) that should appear in
        /admin/status and the diagnostic snapshot.
        """
        with self._lock:
            r = self._services.setdefault(name, ServiceRecord(name=name))
            self._apply_fields(r, fields)

    # ── Snapshot ─────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable view of every service's current state.

        Safe to call from any thread. Used by GET /admin/status and by
        dump_diagnostics().
        """
        with self._lock:
            services = {name: r.to_dict() for name, r in self._services.items()}

        return {
            "engine_pid": os.getpid(),
            "engine_started_at": self._engine_started_at,
            "engine_uptime_s": time.time() - self._engine_started_at,
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "python": platform.python_version(),
                "machine": platform.machine(),
            },
            "services": services,
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_fields(r: ServiceRecord, fields: dict[str, Any]) -> None:
        """Apply known fields onto a record; everything else goes to metadata."""
        for k in ("port", "pid", "url"):
            if k in fields:
                setattr(r, k, fields[k])
        for k, v in fields.items():
            if k not in ("port", "pid", "url"):
                r.metadata[k] = v


def _meta_tail(fields: dict[str, Any]) -> str:
    """Format a {k=v, k=v} suffix for inline log lines, only including non-empty values."""
    parts = [f"{k}={v}" for k, v in fields.items() if v not in (None, "")]
    return f" ({', '.join(parts)})" if parts else ""


# ──────────────────────────────────────────────────────────────────────────────
# Diagnostic snapshot
# ──────────────────────────────────────────────────────────────────────────────


def dump_diagnostics(
    registry: ServiceRegistry,
    *,
    focus: str | None = None,
    error: str | None = None,
) -> Path:
    """Write a comprehensive engine state snapshot to ~/.matrx/diagnostics/.

    Captures every piece of state we have at the moment of failure so the
    operator never has to wonder "what was happening when this broke."
    Includes:

      • Service registry state (every managed service: name, state, pid, port, url, error)
      • Engine process info (pid, ppid, RSS, CPU, threads, open files, create time)
      • All matrx-related processes visible to psutil (catches orphans)
      • Listening TCP ports with PIDs (so we can see who has what bound)
      • Environment subset (PATH, MATRX_*, TAURI_*, PLAYWRIGHT_*, SUPABASE_* — secrets redacted)
      • Stack frames for every live Python thread
      • Free disk on ~/.matrx and tmp
      • The error string and focus service that triggered the dump

    Returns the path of the written snapshot. On write failure, falls back to
    a repr() dump so SOMETHING ends up on disk.
    """
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)

    ts_iso = time.strftime("%Y-%m-%dT%H-%M-%S", time.localtime())
    suffix = f"_{focus}" if focus else ""
    path = DIAGNOSTICS_DIR / f"{ts_iso}{suffix}.json"

    snapshot: dict[str, Any] = {
        "schema": 1,
        "timestamp_unix": time.time(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "focus": focus,
        "error": error,
        "registry": registry.snapshot(),
    }

    # ── Engine process ────────────────────────────────────────────────────────
    snapshot["engine_process"] = _capture_engine_process()

    # ── Related processes (matrx, llama, cloudflared, playwright) ────────────
    snapshot["related_processes"] = _capture_related_processes()

    # ── Listening ports ───────────────────────────────────────────────────────
    snapshot["listening_ports"] = _capture_listening_ports()

    # ── Environment subset ────────────────────────────────────────────────────
    snapshot["env"] = _capture_env_subset()

    # ── Live thread stacks ────────────────────────────────────────────────────
    snapshot["threads"] = _capture_threads()

    # ── Disk usage ────────────────────────────────────────────────────────────
    snapshot["disk"] = _capture_disk_usage()

    # ── Write ──────────────────────────────────────────────────────────────────
    try:
        path.write_text(json.dumps(snapshot, indent=2, default=str))
    except Exception:
        # Last-resort: write whatever we can serialize. A diagnostic that
        # disappears because of its own JSON failure is the worst outcome.
        try:
            path.write_text(repr(snapshot))
        except Exception:
            logger.exception("[launcher] diagnostic write failed completely")

    _prune_old_diagnostics()
    return path


def _capture_engine_process() -> dict[str, Any]:
    try:
        p = psutil.Process(os.getpid())
        with p.oneshot():
            try:
                num_open_files = len(p.open_files())
            except (psutil.AccessDenied, OSError):
                num_open_files = None
            return {
                "pid": p.pid,
                "ppid": p.ppid(),
                "rss_mb": round(p.memory_info().rss / (1024 * 1024), 1),
                "cpu_percent": p.cpu_percent(interval=0),
                "num_threads": p.num_threads(),
                "num_open_files": num_open_files,
                "create_time": p.create_time(),
                "create_time_iso": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.create_time())
                ),
            }
    except Exception as exc:
        return {"error": str(exc)}


def _capture_related_processes() -> list[dict[str, Any]]:
    """Find every process that looks matrx-related so we can spot orphans."""
    patterns = ("matrx", "llama-server", "cloudflared", "aimatrx", "playwright")
    out: list[dict[str, Any]] = []
    try:
        for proc in psutil.process_iter(
            ["pid", "name", "cmdline", "create_time", "username"]
        ):
            try:
                info = proc.info
                cmd = " ".join(info.get("cmdline") or [])
                name = (info.get("name") or "").lower()
                hay = (cmd + " " + name).lower()
                if any(pat in hay for pat in patterns):
                    out.append(
                        {
                            "pid": info["pid"],
                            "name": info.get("name"),
                            "user": info.get("username"),
                            "cmdline": cmd[:300],
                            "create_time": info.get("create_time"),
                        }
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as exc:
        return [{"error": str(exc)}]
    return out


def _capture_listening_ports() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr:
                out.append(
                    {
                        "port": c.laddr.port,
                        "addr": c.laddr.ip,
                        "pid": c.pid,
                    }
                )
    except (psutil.AccessDenied, Exception) as exc:
        return [{"error": str(exc)}]
    return out


def _capture_env_subset() -> dict[str, str]:
    """Capture matrx/tauri/playwright env vars; redact obvious secrets."""
    prefixes = ("MATRX_", "TAURI_", "PLAYWRIGHT_", "SUPABASE_", "CLOUDFLARE_")
    keys = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "TEMP",
        "TMPDIR",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "LANG",
        "LC_ALL",
    }
    secret_markers = ("SECRET", "TOKEN", "KEY", "PASSWORD", "PASSWD")
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        if k in keys or any(k.startswith(pre) for pre in prefixes):
            if any(marker in k.upper() for marker in secret_markers):
                out[k] = f"<redacted len={len(v)}>"
            else:
                out[k] = v
    return out


def _capture_threads() -> list[dict[str, Any]]:
    """Capture stack frames for every live Python thread."""
    out: list[dict[str, Any]] = []
    try:
        threads_by_id = {t.ident: t for t in threading.enumerate()}
        for tid, frame in sys._current_frames().items():
            t = threads_by_id.get(tid)
            try:
                stack = traceback.format_stack(frame)
                # Trim to most recent 20 frames to keep diagnostics manageable.
                stack = stack[-20:]
            except Exception as exc:
                stack = [f"<stack capture failed: {exc}>"]
            out.append(
                {
                    "tid": tid,
                    "name": t.name if t else "?",
                    "daemon": t.daemon if t else None,
                    "alive": t.is_alive() if t else None,
                    "stack": stack,
                }
            )
    except Exception as exc:
        return [{"error": str(exc)}]
    return out


def _capture_disk_usage() -> dict[str, Any]:
    """Capture free-disk numbers for ~/.matrx and the system temp dir."""
    out: dict[str, Any] = {}
    candidates: list[Path] = [_MATRX_HOME]
    if sys.platform == "win32":
        for env in ("TEMP", "TMP"):
            v = os.environ.get(env)
            if v:
                candidates.append(Path(v))
                break
    else:
        candidates.append(Path("/tmp"))

    for p in candidates:
        try:
            if p.exists():
                u = shutil.disk_usage(p)
                out[str(p)] = {
                    "free_gb": round(u.free / (1024**3), 2),
                    "total_gb": round(u.total / (1024**3), 2),
                    "used_pct": round(100 * u.used / u.total, 1),
                }
        except Exception as exc:
            out[str(p)] = {"error": str(exc)}
    return out


def _prune_old_diagnostics() -> None:
    """Keep at most _MAX_DIAGNOSTICS files in the diagnostics dir; delete oldest."""
    try:
        files = sorted(
            DIAGNOSTICS_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[_MAX_DIAGNOSTICS:]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────────────────────────────────────


_registry: ServiceRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> ServiceRegistry:
    """Return the global ServiceRegistry, creating it on first call.

    Safe to call from any thread. The registry is process-singleton.
    """
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ServiceRegistry()
    return _registry
