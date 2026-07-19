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
    • A source-run engine is DEV unless its process environment positively
      opts into the live world with ``MATRX_LIVE_ENGINE=1``. LIVE preflight
      never signals that DEV engine or any matched child in its process tree,
      even before it has bound a port or written discovery state.
    • A LIVE, HEALTHY sibling engine is NEVER killed (services with
      protect_live_owner=True). A matched engine process is protected — along
      with its whole ancestor chain — when it is the discovery-file owner (pid
      match) answering /health as a Matrx engine, OR when it is listening on a
      port in the 22140–22159 scan range and answers /health as a Matrx engine.
      This is what lets two intentional instances coexist: the packaged app and
      a dev `uv run run.py`, in either order, no longer kill each other at
      startup. True orphan reclamation (dead parent / stale discovery file) is
      unaffected — only the live, health-confirmed owner is spared. See
      assign_engine_port() for the matching port-fallback behavior (a live
      incumbent on 22140 is named loudly and we bind the next free port).
    • Leaked Playwright trees (the scraper's chrome-headless-shell browsers and
      the driver node — children of the engine) are reclaimed ONLY when truly
      orphaned (orphan_only) and ONLY when scoped to OUR
      PLAYWRIGHT_BROWSERS_PATH, so the user's real Chrome or an unrelated
      Playwright is never touched. The in-session force-exit path lives in
      run.py (_kill_child_subprocesses → scraper engine terminate_playwright_tree).
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
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

import psutil

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants — defaults can be overridden by env vars (matches run.py behavior).
# ──────────────────────────────────────────────────────────────────────────────

# MATRX_PORT_BASE moves the whole default+scan window. run.py's dev/live
# isolation guard sets 22240 for source-run (dev) engines so they can never
# land inside the packaged app's 22140-22159 discovery-scan range.
DEFAULT_ENGINE_PORT = int(os.environ.get("MATRX_PORT_BASE", "22140"))
ENGINE_PORT_SCAN = 20

_MATRX_HOME = Path(os.environ.get("MATRX_HOME_DIR", str(Path.home() / ".matrx")))

DISCOVERY_FILE = _MATRX_HOME / "local.json"


def _playwright_browsers_path() -> str:
    """Absolute path where the engine keeps its Playwright browsers.

    This is the tight scope that lets us reclaim leaked ``chrome-headless-shell``
    and the Playwright driver WITHOUT ever touching the user's real Chrome or an
    unrelated Playwright install: our browsers/driver carry this exact path (in
    the browser executable's cmdline and in the driver's PLAYWRIGHT_BROWSERS_PATH
    env), and nobody else's do.

    Honors an explicit PLAYWRIGHT_BROWSERS_PATH override if already set;
    otherwise mirrors the default computed in app/main.py
    (``MATRX_HOME_DIR/playwright-browsers``). Note this runs at import time,
    before app/main.py sets the env var, so the default branch is the norm.
    """
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return str(Path(override))
    return str(_MATRX_HOME / "playwright-browsers")


PLAYWRIGHT_BROWSERS_PATH = _playwright_browsers_path()

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
    # Additional environment-variable markers: a tuple of (ENV_KEY, substring).
    # A candidate matches ONLY if ALL markers are present in its environment
    # (case-insensitive substring). Used to tightly scope processes whose
    # cmdline is generic (e.g. the Playwright driver `cli.js run-driver`, which
    # carries no path in argv but inherits our PLAYWRIGHT_BROWSERS_PATH). If the
    # environment can't be read, the candidate FAILS the check — we never kill
    # something we can't positively identify as ours.
    env_markers: tuple[tuple[str, str], ...] = ()
    # When True, only reap this process if it is a TRUE ORPHAN — its owning
    # parent is dead (reparented to PID 1, or the parent PID no longer exists).
    # This is what makes it safe to sweep leaked Playwright browsers: a browser
    # whose engine is still alive (e.g. a second intentional instance — see the
    # engine service's protect_live_owner) is left untouched; only the trees
    # orphaned by a crashed/force-killed engine are reclaimed.
    orphan_only: bool = False
    # When True, terminate the entire descendant process tree, not just the
    # matched PID. The Playwright driver owns the chrome-headless-shell tree,
    # so killing the driver alone would leak its browsers.
    kill_tree: bool = False
    # When True, never kill this process if it is a live, healthy Matrx engine
    # (the discovery-file owner, or any instance answering /health on a port in
    # the scan range) — its whole ancestor chain is protected too. Lets two
    # intentional instances (e.g. the packaged app + a dev engine) coexist
    # instead of one killing the other at startup.
    protect_live_owner: bool = False


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
        # Never kill a live, healthy sibling engine (packaged app vs dev engine).
        protect_live_owner=True,
    ),
    # ── Playwright leak reclamation (L1) ──────────────────────────────────────
    # The scraper's browser pool (chrome-headless-shell × pool_size + helper
    # processes) and the Playwright driver node are CHILDREN OF THE ENGINE. In a
    # clean shutdown the lifespan teardown closes them. But on a crash or a
    # force-exit they get reparented to PID 1 and linger forever, holding
    # hundreds of MB of RAM. These two entries reclaim exactly those orphans and
    # NOTHING ELSE:
    #   • orphan_only    → only trees whose owning engine already died.
    #   • env_markers /  → scoped to OUR PLAYWRIGHT_BROWSERS_PATH so the user's
    #     safety_keyword    real Chrome or an unrelated Playwright is never hit.
    #   • kill_tree      → the driver owns the browser processes; reap the tree.
    ManagedService(
        name="playwright_driver",
        # `node .../playwright/driver/package/cli.js run-driver`. argv carries
        # no browsers path, so scope purely by the inherited env marker.
        cmdline_patterns=(r"\brun-driver\b",),
        port=None,
        port_scan_count=0,
        discovery_key=None,
        spawned_by="python",
        orphan_only=True,
        kill_tree=True,
        env_markers=(("PLAYWRIGHT_BROWSERS_PATH", PLAYWRIGHT_BROWSERS_PATH),),
    ),
    ManagedService(
        name="playwright_browser",
        # The chrome-headless-shell executable path literally contains our
        # browsers path (…/.matrx/playwright-browsers/chromium_headless_shell-*/…),
        # so the browsers-path safety_keyword is an airtight scope on its own.
        cmdline_patterns=(r"chrome[-_]headless[-_]shell", r"\bheadless_shell\b"),
        port=None,
        port_scan_count=0,
        discovery_key=None,
        spawned_by="python",
        orphan_only=True,
        kill_tree=True,
        safety_keyword=PLAYWRIGHT_BROWSERS_PATH,
    ),
    # NOTE: llama-server is INTENTIONALLY OMITTED from this registry.
    #
    # Per the lifecycle ownership contract (see docs/official/lifecycle-ownership.md),
    # each component manages only its own children. llama-server
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
    # Source-run ``run.py`` processes are DEV by contract unless they carry the
    # explicit live opt-in. Frozen engine binaries are always eligible for the
    # current live preflight sweep.
    source_run_dev: bool = False


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


def _env_markers_ok(proc: psutil.Process, markers: tuple[tuple[str, str], ...]) -> bool:
    """True IFF every (key, substring) marker is present in the process's env.

    Reading another process's environment can fail (permission, gone, platform).
    Any failure returns False — we must NOT kill a process we cannot positively
    fingerprint as ours.
    """
    try:
        env = proc.environ()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except Exception:
        return False
    for key, needle in markers:
        value = env.get(key) or ""
        if needle.lower() not in value.lower():
            return False
    return True


def _is_dev_source_engine(proc: psutil.Process, cmdline: str) -> bool:
    """Return whether an engine candidate belongs to the source-run DEV world.

    ``run.py`` defines the authority: every non-frozen source run is DEV unless
    it was launched with ``MATRX_LIVE_ENGINE=1``. Environment inspection is
    deliberately fail-closed; an unreadable source process is never safe for a
    LIVE orphan sweep to signal.
    """
    if re.search(r"\brun\.py\b", cmdline, re.IGNORECASE) is None:
        return False
    try:
        env = proc.environ()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return True
    except Exception:
        return True
    return env.get("MATRX_LIVE_ENGINE") != "1"


def _is_orphaned(proc: psutil.Process) -> bool:
    """True IFF the process's owning parent is gone (reparented to init / dead).

    Unix reparents orphans to PID 1; Windows leaves a dangling ppid. Either way,
    a live parent means the tree is still owned by a running engine and must be
    left alone. If we can't read the ppid, return False (conservative).
    """
    try:
        ppid = proc.ppid()
    except psutil.Error:
        return False
    if ppid <= 1:
        return True
    try:
        return not psutil.pid_exists(ppid)
    except Exception:
        return False


def _ancestor_pids(pid: int) -> set[int]:
    """All ancestor PIDs of ``pid`` up to (but excluding) PID 1."""
    out: set[int] = set()
    try:
        proc: psutil.Process | None = psutil.Process(pid)
        proc = proc.parent()
        while proc is not None and proc.pid > 1:
            out.add(proc.pid)
            proc = proc.parent()
    except psutil.Error:
        pass
    return out


def _descendant_pids(pid: int) -> list[int]:
    """Descendant PIDs of ``pid`` (captured before we start killing)."""
    try:
        return [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except psutil.Error:
        return []


def _probe_matrx_health(base_url: str, *, timeout: float = 1.0) -> bool:
    """True IFF ``{base_url}/health`` answers as a live Matrx engine.

    Uses only the stdlib so preflight stays import-light. Any error (connection
    refused, timeout, wrong body) returns False.
    """
    import json as _json
    import urllib.request

    url = base_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (loopback only)
            body = resp.read(2048)
    except Exception:
        return False
    try:
        data = _json.loads(body)
    except Exception:
        return False
    return str(data.get("service", "")).lower().startswith("matrx")


def _is_scan_range_port(port: int) -> bool:
    return DEFAULT_ENGINE_PORT <= port < DEFAULT_ENGINE_PORT + ENGINE_PORT_SCAN


def _pid_listening_on(port: int) -> int | None:
    """PID of the process listening on ``port`` (loopback), or None.

    Prefers the discovery-file PID when its recorded port matches (cheap and
    reliable), then falls back to scanning psutil's listening sockets.
    """
    data = read_discovery_file() or {}
    if data.get("port") == port and isinstance(data.get("pid"), int):
        return data["pid"]
    try:
        for c in psutil.net_connections(kind="inet"):
            if (
                c.status == psutil.CONN_LISTEN
                and c.laddr
                and c.laddr.port == port
                and c.pid
            ):
                return c.pid
    except (psutil.AccessDenied, Exception):
        return None
    return None


def _protected_engine_pids(found: list[FoundProcess]) -> set[int]:
    """PIDs of live, healthy Matrx engines (and their ancestor chains) that
    ``protect_live_owner`` services must never kill.

    A matched engine process is protected when either:
      (a) it is the discovery-file owner (pid match) AND that URL answers
          /health as a Matrx engine, or
      (b) it is listening on a port in the engine scan range AND that port
          answers /health as a Matrx engine.

    The healthy engine's ancestor chain is protected too, so a parent/launcher
    process that also matches the engine pattern (e.g. the packaged app's outer
    "Matrx Engine" wrapper) isn't reaped out from under the live child.
    """
    protected: set[int] = set()
    discovery = read_discovery_file() or {}
    disc_pid = discovery.get("pid")
    disc_url = discovery.get("url")

    for fp in found:
        if not fp.service.protect_live_owner:
            continue
        healthy = False
        if (
            isinstance(disc_pid, int)
            and disc_pid == fp.pid
            and isinstance(disc_url, str)
            and _probe_matrx_health(disc_url)
        ):
            healthy = True
        if not healthy:
            for port in fp.listening_ports:
                if _is_scan_range_port(port) and _probe_matrx_health(
                    f"http://127.0.0.1:{port}"
                ):
                    healthy = True
                    break
        if healthy:
            protected.add(fp.pid)
            protected |= _ancestor_pids(fp.pid)
    return protected


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

            # Environment-marker check (only when defined). The candidate must
            # carry ALL declared env markers; if we can't read its environment
            # we treat it as NOT ours (conservative — never kill the unknown).
            if svc.env_markers and not _env_markers_ok(proc, svc.env_markers):
                break

            # Orphan gate (only when defined). Reap solely trees whose owning
            # parent is dead — a live engine's browsers/driver are left alone.
            if svc.orphan_only and not _is_orphaned(proc):
                break

            found.append(
                FoundProcess(
                    pid=pid,
                    name=name,
                    cmdline=cmdline,
                    service=svc,
                    source_run_dev=(
                        svc.name == "engine" and _is_dev_source_engine(proc, cmdline)
                    ),
                )
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


def _terminate_pid(pid: int, *, label: str, kill_tree: bool = False) -> bool:
    """Try graceful termination. Returns True if the process is gone after.

    On Unix: SIGTERM, wait up to GRACE_SECONDS, SIGKILL if needed.
    On Windows: SIGTERM equivalent then SIGKILL via psutil; we ALSO invoke
    `taskkill /F /T /PID` so the entire child tree dies (uvicorn workers,
    Playwright, etc.).

    ``kill_tree`` (Unix): capture the descendant PIDs first, then terminate them
    after the parent — needed for the Playwright driver, which owns the
    chrome-headless-shell processes (killing the driver alone leaks them). On
    Windows the taskkill /T below already covers the tree.
    """
    # Capture the descendant PIDs BEFORE we start killing — once the parent
    # dies the children reparent, but their PIDs remain valid kill targets.
    tree_pids: list[int] = []
    if kill_tree and sys.platform != "win32":
        tree_pids = _descendant_pids(pid)

    result = _terminate_single_pid(pid, label=label)

    # Reap the (Unix) descendant tree after the parent, whatever its outcome.
    if tree_pids:
        for child_pid in tree_pids:
            _terminate_single_pid(child_pid, label=f"{label}-child")

    return result


def _terminate_tree_pids(pids: list[int], *, label: str) -> None:
    for child_pid in pids:
        _terminate_single_pid(child_pid, label=f"{label}-child")


def _terminate_single_pid(pid: int, *, label: str) -> bool:
    """Graceful TERM→KILL of a SINGLE pid (no tree handling)."""
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
    # Live, healthy sibling engines we deliberately DID NOT kill (L2). A non-
    # zero value is normal now that two intentional instances can coexist.
    protected: int = 0
    by_service: dict[str, int] = field(default_factory=dict)
    discovery_file_removed: bool = False

    def __str__(self) -> str:
        return (
            f"inspected={self.inspected} "
            f"orphans_found={self.orphans_found} "
            f"killed={self.orphans_killed} "
            f"survived={self.orphans_survived} "
            f"protected={self.protected} "
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
        1
        for _ in psutil.process_iter()  # cheap re-iter for the count
    )

    # DEV/LIVE isolation is stronger than health/discovery protection. A DEV
    # engine may still be starting, unhealthy, or using a throwaway home; none
    # of those states grants LIVE preflight ownership of it. Protect its whole
    # tree so an engine-owned child such as cloudflared cannot be reaped either.
    dev_tree_pids: set[int] = set()
    for fp in found:
        if not fp.source_run_dev:
            continue
        dev_tree_pids.add(fp.pid)
        dev_tree_pids |= _ancestor_pids(fp.pid)
        dev_tree_pids.update(_descendant_pids(fp.pid))
    if dev_tree_pids:
        survivors: list[FoundProcess] = []
        for fp in found:
            if fp.pid in dev_tree_pids:
                report.protected += 1
                _ok(
                    fp.service.name,
                    f"pid {fp.pid} belongs to the source-run DEV world — leaving it running",
                )
            else:
                survivors.append(fp)
        found = survivors

    # Resolve TCP listening ports for matched processes BEFORE we report.
    # See `_resolve_listening_ports` — best-effort, never fails the sweep.
    _resolve_listening_ports(found)

    # L2: protect live, healthy same-world sibling engines, including their
    # ancestor chains. Cross-world DEV protection above does not depend on
    # health or discovery because the LIVE sweep never owns DEV processes.
    protected_engine_pids = _protected_engine_pids(found)
    if protected_engine_pids:
        survivors: list[FoundProcess] = []
        for fp in found:
            if fp.pid in protected_engine_pids:
                report.protected += 1
                _ok(
                    fp.service.name,
                    f"pid {fp.pid} is a LIVE healthy engine (discovery/health "
                    f"confirmed) — leaving it running",
                )
            else:
                survivors.append(fp)
        found = survivors

    report.orphans_found = len(found)

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
        _warn(
            svc.name,
            f"FOUND {len(procs)} lingering process{plural} from previous session",
        )
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
            # kill_tree reaps the Playwright driver's browser children too.
            killed = _terminate_pid(fp.pid, label=label, kill_tree=fp.service.kill_tree)
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

    # The recorded PID is DEAD but a discovery file remained — the previous
    # engine never ran its clean-shutdown path (which unlinks the file). That
    # is a crash / OOM / kill signature. Scream about it and drop a breadcrumb
    # so the post-mortem is not silent, then still remove the stale file.
    _warn(
        "discovery",
        f"previous engine (pid {pid}) exited WITHOUT clean shutdown — "
        f"likely crash/OOM/kill (stale {DISCOVERY_FILE} left behind)",
    )
    _write_crash_breadcrumb(dead_pid=pid, version=data.get("version"))

    try:
        DISCOVERY_FILE.unlink()
        _ok("discovery", f"removed stale {DISCOVERY_FILE} (pid {pid} gone)")
        return True
    except OSError as exc:
        _warn("discovery", f"could not remove stale file: {exc}")
        return False


def _write_crash_breadcrumb(dead_pid: object, version: object) -> None:
    """Drop a small JSON breadcrumb when a dead-pid discovery file is found.

    The previous engine crashed without cleaning up. Record when we noticed,
    which pid was dead, and the version it reported, so ~/.matrx/diagnostics/
    carries a durable trail even though the crashing process wrote nothing.
    """
    try:
        diag_dir = _MATRX_HOME / "diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc)
        crumb = {
            "event": "unclean_shutdown_detected",
            "detected_at": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "dead_pid": dead_pid,
            "version": version,
            "note": (
                "discovery file with a dead pid was found on boot — previous "
                "engine exited without clean shutdown (crash/OOM/kill)"
            ),
        }
        fname = f"crash-{ts.strftime('%Y%m%dT%H%M%S')}-pid{dead_pid}.json"
        (diag_dir / fname).write_text(json.dumps(crumb, indent=2))
    except Exception as exc:
        _warn("discovery", f"could not write crash breadcrumb: {exc}")


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

    # L3: the default port is taken. Probe the incumbent so the log says WHY we
    # fell back. A live Matrx engine there is now expected (two intentional
    # instances are allowed — see clean_orphans protect_live_owner); name its
    # PID loudly. A non-Matrx listener keeps the original "foreign process"
    # wording.
    incumbent_is_matrx = _probe_matrx_health(f"http://127.0.0.1:{DEFAULT_ENGINE_PORT}")
    incumbent_pid = _pid_listening_on(DEFAULT_ENGINE_PORT)
    pid_str = f"pid {incumbent_pid}" if incumbent_pid is not None else "pid unknown"

    for offset in range(1, ENGINE_PORT_SCAN):
        candidate = DEFAULT_ENGINE_PORT + offset
        if _is_port_free(candidate):
            if incumbent_is_matrx:
                _warn(
                    "engine",
                    f"default port {DEFAULT_ENGINE_PORT} already served by a LIVE "
                    f"Matrx engine ({pid_str}) — running a SECOND instance on "
                    f"{candidate}. This is allowed; both instances coexist.",
                )
            else:
                _warn(
                    "engine",
                    f"default port {DEFAULT_ENGINE_PORT} held by foreign process "
                    f"({pid_str}) — falling back to {candidate}",
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
    matrx-extend / docs / integration guide) AND the new `services` map.

    Clobber guard (MXL-D-043): if the file is currently owned by a DIFFERENT
    engine that is alive and answering /health at its recorded URL, we refuse
    to overwrite it. Every discovery consumer (the desktop app's Rust,
    matrx-extend, cloud round trips) routes via this file — a second instance
    silently claiming it redirects all of them to itself. The second instance
    still runs fine on its own port; it just doesn't get discovered. Loud by
    design so the operator knows which engine owns discovery.
    """
    existing = read_discovery_file() or {}
    owner_pid = existing.get("pid")
    owner_url = existing.get("url")
    if (
        isinstance(owner_pid, int)
        and owner_pid != pid
        and isinstance(owner_url, str)
        and psutil.pid_exists(owner_pid)
        and _probe_matrx_health(owner_url)
    ):
        _warn(
            "engine",
            f"discovery file {DISCOVERY_FILE} is owned by a LIVE engine "
            f"(pid {owner_pid}, {owner_url}) — NOT overwriting. This instance "
            f"(pid {pid}, port {engine_port}) keeps running but is not the "
            "discovery owner.",
        )
        return

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

    Ownership guard (MXL-D-043): only the engine whose pid is recorded in the
    file may mutate it — a non-owner instance updating its tunnel here would
    corrupt the discovery owner's entries.
    """
    if not DISCOVERY_FILE.exists():
        return
    try:
        data = json.loads(DISCOVERY_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return

    owner_pid = data.get("pid")
    if isinstance(owner_pid, int) and owner_pid != os.getpid():
        _warn(
            "engine",
            f"discovery file owned by pid {owner_pid} — skipping "
            f"'{service_key}' service update from non-owner pid {os.getpid()}.",
        )
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
        _say(
            f"  port={data.get('port')} pid={data.get('pid')} version={data.get('version')}"
        )
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
