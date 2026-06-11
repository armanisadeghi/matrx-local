"""app/preflight.py — Service registry, orphan cleanup, port assignment.

The single source of truth for "what subprocesses the ENGINE manages and how
to ensure clean state before engine startup." Replaces the engine-owned kill
logic that was previously scattered across:

    run.py                         (_kill_stale_instances, _kill_stale_owner)
    scripts/launch.sh              (check_and_handle_engine, check_and_handle_desktop)
    scripts/stop.sh                (engine + cloudflared portions of the forensic killer)

Adding a managed service now means editing one list (SERVICES) in this file.

⚠ OWNERSHIP BOUNDARY ⚠

This module manages ONLY services owned by the Python engine (the engine
itself + its child cloudflared tunnel). It does NOT manage llama-server
or any other Rust-owned subprocess.

llama-server is owned by the Rust desktop shell:
    • spawned by desktop/src-tauri/src/lib.rs setup() (Auto-start LLM block)
    • torn down by graceful_shutdown_sync() and kill_orphaned_llama_server()

If preflight tried to "clean up" llama-server, it would race against Rust's
own auto-start path that runs ~1s before this module executes — the engine
would kill the llama-server Rust just spawned. That is the EXACT failure
mode this ownership boundary exists to prevent.

Three call paths:

    1. Python (run.py) — `from app.preflight import clean_orphans, assign_engine_port`
       Runs at engine startup, before binding the uvicorn port.

    2. Rust (lib.rs, future) — `python -m app.preflight clean`
       Runs before spawning the bundled sidecar binary.

    3. Bash (launch.sh / stop.sh, future) — `python3 -m app.preflight {clean|shutdown}`
       Lets dev tools delegate instead of maintaining their own kill lists.

Discovery file `~/.matrx/local.json` keeps every existing top-level field for
backward compatibility (matrx-extend probes against it; the docs document
`port`/`host`/`url`/`ws`/`pid`/`version`/`tunnel_url`/`tunnel_ws`). New fields
are added under a nested `services` map and a `schema` version bump.

Self-protection invariants:

    • The current process is NEVER killed.
    • Direct ancestors of the current process are NEVER killed (so when run.py
      runs as a Tauri sidecar, calling clean_orphans() does not kill the Tauri
      shell that spawned us).
    • Only processes owned by the current user are killed (no sudo escalation).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

import psutil

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants — defaults can be overridden by env vars (matches run.py behavior).
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_ENGINE_PORT = 22140
ENGINE_PORT_SCAN = 20

DISCOVERY_FILE = Path(
    os.environ.get("MATRX_HOME_DIR", str(Path.home() / ".matrx"))
) / "local.json"

# Discovery-file schema version. Bump this when the shape of `services` changes
# in a way that consumers must be aware of. Top-level fields stay frozen.
DISCOVERY_SCHEMA = 2

# Graceful-shutdown timeout per process before escalating to SIGKILL.
GRACE_SECONDS = 5.0

# How long to wait between two passes of psutil.process_iter() so that
# processes we just SIGTERMed have a chance to actually exit.
DRAIN_SECONDS = 0.5


# ──────────────────────────────────────────────────────────────────────────────
# Service registry — the one place new managed services are declared.
# ──────────────────────────────────────────────────────────────────────────────

SpawnedBy = Literal["rust", "python", "either"]


@dataclass(frozen=True)
class ManagedService:
    """A subprocess we manage. Identified by cmdline regex (works on every
    platform without parsing per-OS process listings) plus an optional Windows
    image name fallback (some Windows binaries hide their path from cmdline).

    Fields:
        name             Human-readable label used in logs / discovery file.
        cmdline_patterns List of regexes; a process matches if ANY of these
                         appears in its full command line (case-insensitive).
        windows_images   Image names usable with `taskkill /IM`. Belt-and-
                         suspenders for Windows where cmdline may be elided.
        port             Default port if this service binds one. None means
                         it does not own a TCP listener (e.g. cloudflared).
        port_scan_count  How many sequential ports past `port` we'll try if
                         the default is taken. 0 means no fallback.
        discovery_key    Key under `services` in local.json. None means
                         consumers don't need to know about this service.
        spawned_by       Who normally launches this. Used only for docs.
        binary_pinned    True if this service must run on its declared port
                         (e.g. external tools assume 22140). False means
                         port_scan is allowed.
    """

    name: str
    cmdline_patterns: tuple[str, ...]
    windows_images: tuple[str, ...] = ()
    port: int | None = None
    port_scan_count: int = 0
    discovery_key: str | None = None
    spawned_by: SpawnedBy = "either"
    binary_pinned: bool = False
    # Safety keyword: when a cmdline pattern is ambiguous (e.g. "run.py" matches
    # any project that ships a run.py), we additionally require this keyword
    # to appear in the process's cmdline, executable path, or current working
    # directory. Set to None to skip the check (e.g. binary names like
    # "matrx-engine" are already unambiguous on their own). Case-insensitive.
    safety_keyword: str | None = None


SERVICES: tuple[ManagedService, ...] = (
    ManagedService(
        name="engine",
        cmdline_patterns=(
            r"\brun\.py\b",
            r"matrx-engine",
            r"aimatrx-engine",
            r"Matrx Engine",
        ),
        windows_images=(
            "matrx-engine.exe",
            "matrx-engine-x86_64-pc-windows-msvc.exe",
            "aimatrx-engine.exe",
            "aimatrx-engine-x86_64-pc-windows-msvc.exe",
        ),
        port=DEFAULT_ENGINE_PORT,
        port_scan_count=ENGINE_PORT_SCAN,
        discovery_key="engine",
        spawned_by="rust",
        # `run.py` exists in many repos (e.g. aidream/run.py). The bundled
        # binary names (matrx-engine, Matrx Engine) self-identify, but any
        # match against the bare `run.py` pattern needs the additional
        # "matrx" keyword in cmdline / exe path / cwd to confirm it is ours.
        safety_keyword="matrx",
    ),
    # NOTE: llama-server is INTENTIONALLY OMITTED from this registry.
    #
    # Per the lifecycle ownership contract (see ARCHITECTURE.md → "Lifecycle &
    # Ownership"), each component manages only its own children. llama-server
    # is owned by the Rust desktop shell — it is auto-started by Tauri's
    # setup() hook (desktop/src-tauri/src/lib.rs, "Auto-start LLM server")
    # and torn down by Rust's graceful_shutdown_sync() / kill_orphaned_llama_server().
    #
    # The Python engine MUST NOT include llama-server in this registry. If it
    # did, preflight.clean_orphans() (which runs ~1s after the engine sidecar
    # spawns, so AFTER Rust has already auto-started llama-server) would
    # immediately kill the llama-server Rust just spawned. Two orchestrators
    # stepping on each other → the exact bug this whole architecture exists
    # to prevent.
    #
    # If you find yourself adding llama_server back here because something
    # else feels broken: stop. The bug is somewhere else. Fix the broken
    # thing in its own ownership scope.
    ManagedService(
        name="cloudflared",
        cmdline_patterns=(r"cloudflared\s+tunnel",),
        windows_images=("cloudflared.exe",),
        port=None,
        port_scan_count=0,
        discovery_key="tunnel",
        spawned_by="python",
    ),
)


# ──────────────────────────────────────────────────────────────────────────────
# Output helpers — use plain stdout so the lines stream directly into the
# Tauri sidecar pipe and the engine.log tail. Avoid logging here so the user
# can SEE every step of the orchestration regardless of log level.
# ──────────────────────────────────────────────────────────────────────────────


def _say(line: str) -> None:
    """Emit a structured progress line. Always flushes — the caller may be
    watching a log tail and we want each step visible immediately."""
    print(f"[preflight] {line}", flush=True)


def _ok(svc: str, msg: str) -> None:
    _say(f"  {svc:<14}: ✓ {msg}")


def _warn(svc: str, msg: str) -> None:
    _say(f"  {svc:<14}: ⚠ {msg}")


def _err(svc: str, msg: str) -> None:
    _say(f"  {svc:<14}: ✗ {msg}")


# ──────────────────────────────────────────────────────────────────────────────
# Self-protection — compute the set of PIDs we must never touch.
# ──────────────────────────────────────────────────────────────────────────────


def _self_pid_chain() -> set[int]:
    """Return current PID plus every ancestor PID up to PID 1.

    Used as the "do not kill" set. When run.py is launched as a Tauri sidecar,
    its parent is the Tauri shell — we MUST NOT kill the Tauri shell when
    cleaning orphans, or we'd kill the very thing that's trying to start us.
    """
    chain: set[int] = set()
    try:
        proc: psutil.Process | None = psutil.Process(os.getpid())
        while proc is not None and proc.pid > 1:
            chain.add(proc.pid)
            try:
                proc = proc.parent()
            except psutil.Error:
                break
    except psutil.Error:
        chain.add(os.getpid())
    return chain


def _current_user() -> str | None:
    """Owner username for the current process. None on platforms where it can't
    be determined — in that case we fall back to "kill anything that matches"
    which is acceptable on single-user desktops (the design target)."""
    try:
        return psutil.Process(os.getpid()).username()
    except psutil.Error:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Process discovery — one pass through psutil, classify by service.
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class FoundProcess:
    pid: int
    name: str
    cmdline: str
    service: ManagedService
    # TCP ports this process is currently listening on. Populated lazily by
    # `_resolve_listening_ports` after the cmdline-pattern scan, because
    # psutil.Process.connections() is significantly slower than the cmdline
    # iter and we only want to pay that cost for matched processes.
    listening_ports: list[int] = field(default_factory=list)


def _compile_patterns(svc: ManagedService) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in svc.cmdline_patterns]


def _safe_proc_attr(proc: psutil.Process, attr: str) -> str:
    """Return a string-valued psutil attribute or empty string on access errors.

    psutil raises AccessDenied / NoSuchProcess for many short-lived or
    privileged processes. We never want process discovery to fail because
    of one inaccessible PID — those processes are simply skipped from the
    safety_keyword evidence, which means they fail the keyword check
    (correct conservative default: don't kill things we can't identify).
    """
    try:
        value = getattr(proc, attr)()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return ""
    except Exception:
        return ""
    return value or ""


def _scan_processes(
    services: Iterable[ManagedService],
    *,
    protected_pids: set[int],
    user: str | None,
) -> list[FoundProcess]:
    """Return all processes matching any service's cmdline pattern, excluding
    protected PIDs and processes not owned by the current user.

    For services declaring a `safety_keyword`, the process's cmdline / exe
    path / cwd must additionally contain that keyword (case-insensitive).
    This prevents broad patterns like `run.py` from matching unrelated
    sibling repos (e.g. `aidream/run.py`) that legitimately ship the same
    filename.
    """
    compiled = [(svc, _compile_patterns(svc)) for svc in services]
    found: list[FoundProcess] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline", "username"]):
        info = proc.info  # type: ignore[union-attr]
        pid = info.get("pid")
        if pid is None or pid in protected_pids:
            continue

        if user is not None:
            owner = info.get("username")
            if owner is not None and owner != user:
                continue

        name = info.get("name") or ""
        cmdline_list = info.get("cmdline") or []
        cmdline = " ".join(cmdline_list) if cmdline_list else name
        if not cmdline:
            continue

        # Match against every service; first match wins. Order matters in
        # SERVICES — engine is most specific (run.py + engine binaries), so
        # we declare it first and an ambiguous `python run.py` lands there
        # before bleeding into a more permissive future service.
        for svc, patterns in compiled:
            if not any(p.search(cmdline) for p in patterns):
                continue

            # Safety keyword check (only when defined on the service).
            # The keyword must appear in cmdline / exe / cwd (case-insensitive).
            # If we can't read exe / cwd (zombies, permission denied), the
            # keyword check falls back to cmdline-only — which is accurate
            # because the binary names baked into our SERVICES list (e.g.
            # "matrx-engine") already contain the safety keyword.
            if svc.safety_keyword:
                kw = svc.safety_keyword.lower()
                evidence = cmdline.lower()
                if kw not in evidence:
                    exe = _safe_proc_attr(proc, "exe").lower()
                    cwd = _safe_proc_attr(proc, "cwd").lower()
                    if kw not in exe and kw not in cwd:
                        # Looks like our pattern but lacks the keyword — not ours.
                        break

            found.append(
                FoundProcess(pid=pid, name=name, cmdline=cmdline, service=svc)
            )
            break

    return found


def _resolve_listening_ports(found: list[FoundProcess]) -> None:
    """Populate `listening_ports` on each FoundProcess by querying psutil.

    Best-effort: any process where we can't read connections (zombie, gone,
    permission denied) keeps an empty list, which surfaces in the log as
    `port=-` rather than failing the whole sweep. We only look up TCP listeners
    because that's the contract our managed services hold (HTTP and WebSocket
    on the engine; cloudflared has no local listener; llama-server has its own
    HTTP port). UDP and outbound TCP are intentionally ignored.

    Called once per `clean_orphans()` run, AFTER the cmdline-pattern scan, so
    we only pay the connections() cost for processes we actually plan to kill.
    """
    for fp in found:
        try:
            proc = psutil.Process(fp.pid)
        except psutil.NoSuchProcess:
            continue
        try:
            conns = proc.connections(kind="tcp")
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except Exception:
            # psutil's connections() can raise OSError on some macOS versions
            # when the process exited mid-query. Treat any unexpected error
            # the same as AccessDenied — the kill path doesn't need port info.
            continue

        ports: set[int] = set()
        for c in conns:
            try:
                if c.status == psutil.CONN_LISTEN and c.laddr:
                    ports.add(c.laddr.port)
            except (AttributeError, TypeError):
                pass
        fp.listening_ports = sorted(ports)


# ──────────────────────────────────────────────────────────────────────────────
# Killing — graceful TERM with timeout, then KILL. On Windows we ALSO kill the
# entire process tree (Python's signal model on Windows can't reach child
# processes; only the OS's taskkill /T does).
# ──────────────────────────────────────────────────────────────────────────────


def _terminate_pid(pid: int, *, label: str) -> bool:
    """Try graceful termination. Returns True if the process is gone after.

    On Unix: SIGTERM, wait up to GRACE_SECONDS, SIGKILL if needed.
    On Windows: SIGTERM equivalent then SIGKILL via psutil; we ALSO invoke
    `taskkill /F /T /PID` so the entire child tree dies (uvicorn workers,
    Playwright, etc.).
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True
    except psutil.Error as exc:
        _warn(label, f"could not access pid {pid}: {exc}")
        return False

    try:
        proc.terminate()  # SIGTERM on Unix, TerminateProcess on Windows
    except psutil.NoSuchProcess:
        return True
    except psutil.Error as exc:
        _warn(label, f"terminate(pid={pid}) failed: {exc}; trying kill()")

    try:
        proc.wait(timeout=GRACE_SECONDS)
        return True
    except psutil.TimeoutExpired:
        _warn(
            label,
            f"pid {pid} did not exit within {GRACE_SECONDS:.0f}s of SIGTERM "
            f"(this is a shutdown bug — escalating to SIGKILL)",
        )
    except psutil.NoSuchProcess:
        return True
    except psutil.Error as exc:
        _warn(label, f"wait(pid={pid}) error: {exc}; escalating")

    # Windows: kill the whole tree so children don't keep ports/files locked.
    if sys.platform == "win32":
        try:
            import subprocess as _sp  # local import keeps top-level clean
            _sp.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass

    try:
        proc.kill()
    except psutil.NoSuchProcess:
        return True
    except psutil.Error as exc:
        _err(label, f"kill(pid={pid}) failed: {exc}")
        return False

    try:
        proc.wait(timeout=2.0)
    except (psutil.TimeoutExpired, psutil.Error):
        pass

    return not psutil.pid_exists(pid)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry points — these are what run.py / Rust / bash actually call.
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class CleanReport:
    """Result of a clean_orphans() run. Returned to callers and stringifiable
    for logs. `orphans_killed` is the canonical count consumers care about —
    if it's > 0 on every startup, that's a shutdown bug worth investigating."""

    inspected: int = 0
    orphans_found: int = 0
    orphans_killed: int = 0
    orphans_survived: int = 0
    by_service: dict[str, int] = field(default_factory=dict)
    discovery_file_removed: bool = False

    def __str__(self) -> str:
        return (
            f"inspected={self.inspected} "
            f"orphans_found={self.orphans_found} "
            f"killed={self.orphans_killed} "
            f"survived={self.orphans_survived} "
            f"by_service={self.by_service}"
        )


def clean_orphans(*, services: Iterable[ManagedService] | None = None) -> CleanReport:
    """Kill every running process that matches our service patterns, except
    the current process and its ancestors.

    Call this BEFORE spawning anything else. After this returns, ports owned
    by managed services should be free and no stale instances are left to
    interfere with our launch.

    The output is structured as three phases that always emit, in order:

        1. Scan announcement     — one line: "Scanning ... services: A, B, C"
        2. Per-service result    — one line per service whether it's clean or
                                   has lingerers; lingerers list pid + held
                                   ports + truncated cmdline.
        3. Action log            — only emitted when there's something to kill;
                                   "Terminating N processes..." then per-PID
                                   SIGTERM result (with SIGKILL escalation log
                                   inside `_terminate_pid` if SIGTERM is
                                   ignored).

    Returns a CleanReport. Errors are reported via _warn/_err and recorded in
    the report; the function never raises so it can't take down a startup.
    """
    services = tuple(services) if services is not None else SERVICES
    protected = _self_pid_chain()
    user = _current_user()

    # ── Phase 1: scan announcement ──────────────────────────────────────────
    _say(
        "Scanning for lingering processes from previous session "
        f"(services: {', '.join(s.name for s in services)})"
    )
    if user:
        _say(f"  user={user!r}  protected_pids={sorted(protected)}")

    report = CleanReport()
    found = _scan_processes(services, protected_pids=protected, user=user)
    report.inspected = sum(
        1 for _ in psutil.process_iter()  # cheap re-iter for the count
    )
    report.orphans_found = len(found)

    # Resolve TCP listening ports for matched processes BEFORE we report.
    # See `_resolve_listening_ports` — best-effort, never fails the sweep.
    _resolve_listening_ports(found)

    # Group by service so every managed service gets exactly one summary line,
    # even when it's clean. Without this the user can't tell whether a service
    # was checked and clean, or never checked at all.
    by_service: dict[str, list[FoundProcess]] = {s.name: [] for s in services}
    for fp in found:
        by_service.setdefault(fp.service.name, []).append(fp)

    # ── Phase 2: per-service result (always emitted, every service) ─────────
    held_ports: set[int] = set()
    for svc in services:
        procs = by_service.get(svc.name, [])
        report.by_service[svc.name] = len(procs)
        if not procs:
            _ok(svc.name, "clean (no lingering processes)")
            continue
        plural = "es" if len(procs) != 1 else ""
        _warn(svc.name, f"FOUND {len(procs)} lingering process{plural} from previous session")
        for fp in procs:
            short_cmd = fp.cmdline if len(fp.cmdline) <= 80 else fp.cmdline[:77] + "..."
            ports_str = (
                "ports=" + ",".join(str(p) for p in fp.listening_ports)
                if fp.listening_ports
                else "ports=-"
            )
            _say(f"      pid={fp.pid:<6} {ports_str:<18} {short_cmd}")
            held_ports.update(fp.listening_ports)

    if held_ports:
        # Single line so the user can grep for "Held ports" on bad startups.
        _warn(
            "ports",
            f"Held by lingering processes: {', '.join(str(p) for p in sorted(held_ports))} "
            f"— will be released by SIGTERM/SIGKILL below",
        )

    # ── Phase 3: action log (only when there's actually something to kill) ──
    if found:
        plural = "es" if len(found) != 1 else ""
        _say(f"Terminating {len(found)} lingering process{plural}...")
        for fp in found:
            label = fp.service.name
            ports_str = (
                "ports=" + ",".join(str(p) for p in fp.listening_ports)
                if fp.listening_ports
                else "ports=-"
            )
            _say(f"  {label:<14}: → SIGTERM pid={fp.pid} {ports_str}")

            # _terminate_pid waits up to GRACE_SECONDS, then escalates to
            # SIGKILL (with its own _warn line so the escalation is visible).
            killed = _terminate_pid(fp.pid, label=label)
            if killed:
                report.orphans_killed += 1
                _ok(label, f"pid {fp.pid} terminated")
            else:
                report.orphans_survived += 1
                _err(label, f"pid {fp.pid} could NOT be terminated")

        # Brief drain so subsequent port binds see the released port. Without
        # this, _is_port_free() in assign_engine_port() can race ahead of the
        # kernel's TCP socket close and report the port as still busy.
        time.sleep(DRAIN_SECONDS)

    # Discovery file cleanup — only remove it if its recorded PID is dead.
    # This avoids a race where Rust's preflight runs AFTER a freshly-started
    # engine wrote the file.
    report.discovery_file_removed = _maybe_remove_stale_discovery_file()

    _say(f"Done: {report}")
    return report


def _maybe_remove_stale_discovery_file() -> bool:
    if not DISCOVERY_FILE.exists():
        return False
    try:
        data = json.loads(DISCOVERY_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        # Corrupt file. Remove it — we'll write a fresh one.
        try:
            DISCOVERY_FILE.unlink()
            _ok("discovery", f"removed corrupt {DISCOVERY_FILE}")
            return True
        except OSError:
            return False

    pid = data.get("pid")
    if isinstance(pid, int) and psutil.pid_exists(pid):
        # The recorded PID is still alive — could be us (just-started engine)
        # or an unrelated reuse. Either way, leave the file alone.
        return False

    try:
        DISCOVERY_FILE.unlink()
        _ok("discovery", f"removed stale {DISCOVERY_FILE} (pid {pid} gone)")
        return True
    except OSError as exc:
        _warn("discovery", f"could not remove stale file: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Port assignment — single shared implementation. Used by run.py.
# ──────────────────────────────────────────────────────────────────────────────


def _is_port_free(port: int) -> bool:
    """Bind-test localhost:port. SO_REUSEADDR avoids false-busy from TIME_WAIT
    sockets left by a recently stopped server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform == "win32":
            # On Windows SO_REUSEADDR lets the bind SUCCEED over an active
            # foreign listener (unlike Linux where it only relaxes TIME_WAIT),
            # so the probe reported busy ports as free and the engine could
            # double-bind. SO_EXCLUSIVEADDRUSE gives the correct semantics.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(0.1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def assign_engine_port(*, env_override: str | None = None) -> int:
    """Find a port for the engine, honoring MATRX_PORT if set.

    Behavior matches the existing `run.py::find_available_port` so swapping in
    this implementation is observationally identical.
    """
    if env_override is None:
        env_override = os.environ.get("MATRX_PORT")

    if env_override:
        try:
            port = int(env_override)
        except ValueError:
            raise SystemExit(f"MATRX_PORT={env_override!r} is not a valid integer")
        if _is_port_free(port):
            _ok("engine", f"port {port} (MATRX_PORT override)")
            return port
        raise SystemExit(f"Port {port} (from MATRX_PORT) is already in use")

    if _is_port_free(DEFAULT_ENGINE_PORT):
        _ok("engine", f"port {DEFAULT_ENGINE_PORT} (default)")
        return DEFAULT_ENGINE_PORT

    for offset in range(1, ENGINE_PORT_SCAN):
        candidate = DEFAULT_ENGINE_PORT + offset
        if _is_port_free(candidate):
            _warn(
                "engine",
                f"default port {DEFAULT_ENGINE_PORT} held by foreign process — "
                f"falling back to {candidate}",
            )
            return candidate

    raise SystemExit(
        f"No free port in range {DEFAULT_ENGINE_PORT}-"
        f"{DEFAULT_ENGINE_PORT + ENGINE_PORT_SCAN - 1}. "
        f"Set MATRX_PORT to a specific open port."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Discovery file — extends the existing schema without breaking consumers.
#
# Old shape (still emitted, frozen):
#   { port, host, url, ws, pid, version, tunnel_url?, tunnel_ws? }
#
# New nested addition:
#   { ..., schema: 2, services: { engine: {...}, llama: {...}, tunnel: {...} } }
#
# Consumers that only read top-level fields keep working unchanged.
# ──────────────────────────────────────────────────────────────────────────────


def write_discovery_file(
    *,
    engine_port: int,
    pid: int,
    version: str,
    tunnel_url: str | None = None,
) -> None:
    """Atomically write ~/.matrx/local.json with both legacy fields (for
    matrx-extend / docs / integration guide) AND the new `services` map."""
    DISCOVERY_FILE.parent.mkdir(parents=True, exist_ok=True)

    payload: dict = {
        # Legacy top-level — frozen contract.
        "port": engine_port,
        "host": "127.0.0.1",
        "url": f"http://127.0.0.1:{engine_port}",
        "ws": f"ws://127.0.0.1:{engine_port}/ws",
        "pid": pid,
        "version": version,
        # New schema marker.
        "schema": DISCOVERY_SCHEMA,
        # Nested per-service map. New consumers prefer this; it's authoritative.
        "services": {
            "engine": {
                "port": engine_port,
                "url": f"http://127.0.0.1:{engine_port}",
                "ws": f"ws://127.0.0.1:{engine_port}/ws",
                "pid": pid,
            },
        },
    }

    if tunnel_url:
        payload["tunnel_url"] = tunnel_url
        payload["tunnel_ws"] = tunnel_url.replace("https://", "wss://") + "/ws"
        payload["services"]["tunnel"] = {
            "url": tunnel_url,
            "ws": tunnel_url.replace("https://", "wss://") + "/ws",
        }

    # Atomic write: stage to a temp file in the same directory then rename.
    # Avoids consumers reading a half-written file.
    tmp = DISCOVERY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(DISCOVERY_FILE)


def update_discovery_service(
    service_key: str,
    info: dict | None,
) -> None:
    """Update one entry in the `services` map without rewriting the rest.

    Use for late-binding services (e.g. tunnel comes up after the engine).
    Pass info=None to remove the entry.
    """
    if not DISCOVERY_FILE.exists():
        return
    try:
        data = json.loads(DISCOVERY_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return

    services_map = data.setdefault("services", {})
    if info is None:
        services_map.pop(service_key, None)
        if service_key == "tunnel":
            data.pop("tunnel_url", None)
            data.pop("tunnel_ws", None)
    else:
        services_map[service_key] = info
        if service_key == "tunnel":
            url = info.get("url")
            if isinstance(url, str):
                data["tunnel_url"] = url
                data["tunnel_ws"] = url.replace("https://", "wss://") + "/ws"

    tmp = DISCOVERY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(DISCOVERY_FILE)


def read_discovery_file() -> dict | None:
    """Read the current discovery file or None if missing/invalid."""
    if not DISCOVERY_FILE.exists():
        return None
    try:
        return json.loads(DISCOVERY_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def remove_discovery_file() -> None:
    try:
        DISCOVERY_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Status — read-only forensic view. Used by `--status` and by humans.
# ──────────────────────────────────────────────────────────────────────────────


def status() -> int:
    """Print what's running for each managed service. Exit code is the number
    of running matched processes (0 = clean state)."""
    user = _current_user()
    found = _scan_processes(SERVICES, protected_pids=set(), user=user)
    by_svc: dict[str, list[FoundProcess]] = {s.name: [] for s in SERVICES}
    for fp in found:
        by_svc[fp.service.name].append(fp)

    _say("Status:")
    for svc in SERVICES:
        procs = by_svc[svc.name]
        if not procs:
            _ok(svc.name, "not running")
            continue
        for fp in procs:
            short = fp.cmdline if len(fp.cmdline) <= 80 else fp.cmdline[:77] + "..."
            _say(f"  {svc.name:<14}: pid={fp.pid:<6} {short}")

    data = read_discovery_file()
    if data:
        _say(f"Discovery: {DISCOVERY_FILE} (schema={data.get('schema', 1)})")
        _say(f"  port={data.get('port')} pid={data.get('pid')} version={data.get('version')}")
        services = data.get("services") or {}
        for k, v in services.items():
            _say(f"  services.{k}: {v}")
    else:
        _say(f"Discovery: {DISCOVERY_FILE} — absent")

    return len(found)


# ──────────────────────────────────────────────────────────────────────────────
# Shutdown — like clean_orphans but with stronger framing: caller is asking
# "stop everything we manage." Used by Tauri CloseRequested or `stop` CLI.
# Same implementation as clean_orphans but documented separately so the
# semantics stay clear.
# ──────────────────────────────────────────────────────────────────────────────


def shutdown_all() -> CleanReport:
    """Gracefully terminate every managed service. Same implementation as
    clean_orphans (the operation is identical from the OS's perspective) but
    expresses intent: caller is shutting down, not just clearing orphans."""
    return clean_orphans()


# ──────────────────────────────────────────────────────────────────────────────
# CLI — `python -m app.preflight {clean|status|shutdown}`
# ──────────────────────────────────────────────────────────────────────────────


def _signal_passthrough() -> None:
    """Make the CLI script Ctrl-C cleanly. We are normally short-lived (~5s)
    but a stuck terminate() should still let the user abort."""
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, lambda *_: sys.exit(130))


def main(argv: list[str] | None = None) -> int:
    _signal_passthrough()
    parser = argparse.ArgumentParser(
        prog="python -m app.preflight",
        description="Matrx Local — managed-service preflight & cleanup.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("clean", help="Kill all stale managed processes.")
    sub.add_parser("status", help="Report running managed processes (read-only).")
    sub.add_parser("shutdown", help="Graceful TERM of every managed process.")

    ports = sub.add_parser("ports", help="Print port assignments without killing.")
    ports.add_argument(
        "--engine",
        action="store_true",
        help="Print only the engine port (one integer on stdout).",
    )

    args = parser.parse_args(argv)

    if args.cmd == "clean":
        report = clean_orphans()
        return 0 if report.orphans_survived == 0 else 1
    if args.cmd == "shutdown":
        report = shutdown_all()
        return 0 if report.orphans_survived == 0 else 1
    if args.cmd == "status":
        running = status()
        return 0 if running == 0 else running
    if args.cmd == "ports":
        port = assign_engine_port()
        if args.engine:
            print(port)
        else:
            _say(f"engine port = {port}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
