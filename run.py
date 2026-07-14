"""Matrx Local — entry point.

Starts the FastAPI server.  When running standalone (outside the Tauri desktop
shell) also creates a pystray system-tray icon for port display / quit.

When spawned by Tauri as a sidecar (TAURI_SIDECAR=1), the tray icon is
**skipped** — Tauri already manages one system-tray icon that represents the
entire application.  Letting Python also create a tray icon would produce two
(or three) icons in the user's system tray.

Port selection:
  1. MATRX_PORT env var (explicit override — fails hard if taken)
  2. Default port MATRX_PORT_BASE (22140 packaged / 22240 dev — see the
     dev/live isolation guard below)
  3. If default port is held by a dead/stale previous instance of ourselves,
     we kill the stale process and reclaim the port rather than drifting.
  4. Auto-scan: tries up to 20 consecutive ports until one is free (last resort)

The chosen port is written to <MATRX_HOME_DIR>/local.json so the web/mobile
frontend can discover it without configuration.
"""

from __future__ import annotations

# ── PyInstaller multiprocessing guard — MUST run before anything else ─────────
# In the frozen binary, any multiprocessing spawn re-executes THIS binary.
# Without freeze_support(), the child skips the multiprocessing bootstrap and
# boots a complete second engine: it steals the next port, overwrites
# ~/.matrx/local.json, and the desktop UI's engine connection goes dead —
# observed live on 2026-07-09 when the first HF model download (hf_xet)
# spawned a child and a rogue duplicate engine appeared on 22141.
import multiprocessing as _mp
_mp.freeze_support()

# ── Windows UTF-8 fix — before every other import ────────────────────────────
# Windows defaults to CP1252 for stdout/stderr. Our log messages contain
# Unicode symbols (✓ → ← ─ ⚠) that CP1252 cannot encode, causing
# UnicodeEncodeError inside Starlette's logging machinery which floods stderr
# with hundreds of "--- Logging error ---" tracebacks per second.
# This must happen BEFORE importing app.common.platform_ctx (which triggers
# logging setup) so the streams are correct from the very first log line.
import sys as _sys
import os as _os
if _sys.platform == "win32":
    _os.environ.setdefault("PYTHONUTF8", "1")
    _os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
    try:
        if hasattr(_sys.stdout, "reconfigure"):
            _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        else:
            import io as _io
            _sys.stdout = _io.TextIOWrapper(
                _sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
        if hasattr(_sys.stderr, "reconfigure"):
            _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        else:
            import io as _io
            _sys.stderr = _io.TextIOWrapper(
                _sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
    except Exception:
        pass

# ── Dev/live isolation guard — MUST run before any app.* import ──────────────
# app/config.py and app/preflight.py read MATRX_HOME_DIR at import time, so
# the env must be final before they load.
#
# The packaged sidecar (PyInstaller sets sys.frozen) is the ONLY engine that
# may occupy the live position by default: ~/.matrx, port range 22140-22159.
# Every source-run engine (`uv run python run.py` — Arman or any agent) is a
# DEV engine and is fully isolated unless explicitly told otherwise:
#
#   home       ~/.matrx-dev        (own discovery file, matrx.db, settings)
#   ports      22240-22259         (OUTSIDE the live scan range — the packaged
#                                   app's Rust probes 22140-22159 and would
#                                   otherwise adopt a dev engine it finds)
#   orphans    scan skipped        (a dev engine must never kill the live
#                                   app's process tree — or another agent's)
#   instance   salted "dev" id     (cloud registration must not collide with
#                                   the live app's hardware-derived instance)
#
# Escape hatches (each var honored individually if already set):
#   MATRX_LIVE_ENGINE=1   run a source engine in the live position on purpose
#   MATRX_HOME_DIR=...    custom home (tests, per-agent isolation)
#   MATRX_PORT=... / MATRX_PORT_BASE=...   explicit port placement
#
# This is the MXL-D-043 fix: before it, any source-run engine clobbered
# ~/.matrx/local.json (even when bound to a fallback port) and every client —
# the installed app, matrx-extend, cloud round trips — silently routed to
# uncommitted code.
IS_DEV_ENGINE = (
    not getattr(_sys, "frozen", False)
    and _os.environ.get("MATRX_LIVE_ENGINE") != "1"
)
if IS_DEV_ENGINE:
    from pathlib import Path as _Path

    if "MATRX_HOME_DIR" not in _os.environ:
        _dev_home = _Path.home() / ".matrx-dev"
        _os.environ["MATRX_HOME_DIR"] = str(_dev_home)
        # Share the live home's huge, immutable-ish asset caches (~166GB of
        # models on Arman's machine) via symlink so a dev engine never
        # re-downloads them. Mutable state (matrx.db, settings.json,
        # local.json, instance.json, data/, mirror/, media/) is NEVER shared —
        # that separation is the whole point. Symlink failure (e.g. Windows
        # without developer mode) is fine: the engine just downloads into the
        # dev home. Custom MATRX_HOME_DIR values (tests, --fresh) skip this
        # so they stay fully hermetic.
        _live_home = _Path.home() / ".matrx"
        try:
            _dev_home.mkdir(parents=True, exist_ok=True)
            for _cache in (
                "image-models",
                "video-models",
                "playwright-browsers",
                "image-gen-packages",
                "tts",
                "models",
                "oww_models",
            ):
                _src = _live_home / _cache
                _dst = _dev_home / _cache
                if _src.is_dir() and not _dst.exists():
                    _dst.symlink_to(_src, target_is_directory=True)
        except OSError:
            pass
    if "MATRX_PORT" not in _os.environ:
        _os.environ.setdefault("MATRX_PORT_BASE", "22240")
    _os.environ.setdefault("MATRX_SKIP_ORPHAN_SCAN", "1")
    _os.environ.setdefault("MATRX_INSTANCE_SALT", "dev")
    print(
        "[phase:isolation] DEV ENGINE (source run, not the packaged sidecar) — "
        f"isolated from the installed app: home={_os.environ['MATRX_HOME_DIR']}, "
        f"ports={_os.environ.get('MATRX_PORT') or _os.environ.get('MATRX_PORT_BASE', '22140') + '+'}, "
        "orphan scan off, instance id salted. "
        "Set MATRX_LIVE_ENGINE=1 to run in the live position on purpose.",
        flush=True,
    )

import json
import logging
import os
import re
import signal
import sys
import threading
from pathlib import Path

# Preflight is imported lazily inside main() so that any import-time error
# from psutil / process iteration cannot prevent us from at least logging
# what went wrong before crashing.

# ── Windows asyncio pipe transport noise suppressor ──────────────────────────
#
# On Windows, when the Tauri sidecar pipe's read-end is closed (e.g. on app
# exit or restart) while Python is still writing log output, asyncio raises
# ConnectionResetError (WinError 10054) inside _ProactorBasePipeTransport.
# These errors surface in two separate paths:
#
#   1. asyncio's exception handler — receives unhandled task exceptions.
#   2. asyncio's internal logger (logging.getLogger("asyncio")) — emits ERROR
#      log records for "Exception in callback _ProactorBasePipeTransport.*".
#
# Both are harmless: the process is shutting down or the sidecar pipe is gone.
# We suppress both paths below.
if _sys.platform == "win32":
    import asyncio as _asyncio
    import logging as _logging

    def _win_asyncio_exception_handler(loop: object, context: dict) -> None:
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) and exc.winerror == 10054:  # type: ignore[attr-defined]
            return  # Pipe read-end closed — expected on shutdown, suppress.
        msg = context.get("message", "")
        if "ConnectionResetError" in msg and "10054" in msg:
            return  # Same error surfaced as a string rather than exception object.
        # Delegate to asyncio's default handler for everything else.
        loop.default_exception_handler(context)  # type: ignore[union-attr]

    # We can't call get_event_loop() at module level (no running loop yet),
    # so we install the handler lazily via a ProactorEventLoop subclass.
    # However, the simplest safe approach is to patch the policy's new_event_loop.
    _orig_new_event_loop = _asyncio.DefaultEventLoopPolicy.new_event_loop

    def _patched_new_event_loop(self: object) -> _asyncio.AbstractEventLoop:
        loop = _orig_new_event_loop(self)  # type: ignore[arg-type]
        loop.set_exception_handler(_win_asyncio_exception_handler)
        return loop

    _asyncio.DefaultEventLoopPolicy.new_event_loop = _patched_new_event_loop  # type: ignore[method-assign]

    # Path 2: asyncio logs "Exception in callback _ProactorBasePipeTransport.*"
    # at ERROR level via logging.getLogger("asyncio"). Install a log filter to
    # drop these specific records — they indicate the sidecar pipe closed, which
    # is expected during normal app exit and restart cycles.
    class _ProactorPipeFilter(_logging.Filter):
        def filter(self, record: _logging.LogRecord) -> bool:
            msg = record.getMessage()
            if "_ProactorBasePipeTransport" in msg and "connection_lost" in msg:
                return False
            if "_ProactorBasePipeTransport" in msg and "ConnectionResetError" in msg:
                return False
            return True

    _logging.getLogger("asyncio").addFilter(_ProactorPipeFilter())

from app.common.platform_ctx import CAPABILITIES, PLATFORM

def _read_version() -> str:
    """Read version — tries importlib.metadata first (works in packaged binary),
    then falls back to parsing pyproject.toml (works in dev mode and PyInstaller)."""
    try:
        from importlib.metadata import version as _meta_version, PackageNotFoundError
        try:
            return _meta_version("matrx-local")
        except PackageNotFoundError:
            pass
    except ImportError:
        pass

    # Fallback: read pyproject.toml from several candidate locations.
    # sys._MEIPASS is the PyInstaller extraction dir where bundled datas land.
    try:
        candidates: list[Path] = []
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "pyproject.toml")
        candidates += [
            Path(__file__).parent / "pyproject.toml",               # dev: run.py at project root
            Path(__file__).parent.parent / "pyproject.toml",        # edge case
        ]
        for candidate in candidates:
            if candidate.exists():
                text = candidate.read_text()
                m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
                if m:
                    return m.group(1)
    except Exception:
        pass

    return "0.0.0"

_APP_VERSION = _read_version()

import uvicorn

# NOTE: PIL.Image and pystray are imported LAZILY inside the tray functions,
# not here. pystray's __init__ connects to the X display at import time on
# Linux, which crashes in headless environments (CI, Tauri-sidecar, servers)
# before the tray-skip guard in setup_tray() ever runs. The engine is a
# server sidecar — it must boot without a display. Type hints referencing
# Icon/MenuItem stay valid because this module uses `from __future__ import
# annotations` (annotations are never evaluated at runtime).

from app.main import app
from app.config import MATRX_HOME_DIR

# run.py logs through system_logger (file + console), NOT a bare module logger.
# A bare logging.getLogger(__name__) here propagates to the root logger, whose
# only handler is the WARNING+ bridge — every INFO line run.py emitted was a
# black hole, and shutdown-path warnings were the only survivors. That silence
# is exactly how the engine died invisibly ~12 times on 2026-07-11/12 (the
# "forever loader" freezes): SIGTERM arrived, nothing was logged, the process
# vanished. Lifecycle events MUST land in system.log.
from app.common.system_logger import get_logger

logger = get_logger()

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys.executable).parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent

STATIC_DIR = BUNDLE_DIR / "static"

# Default port + scan range — these constants stay here so external imports
# (notably app/api/tunnel_routes.py imports DISCOVERY_FILE from this module)
# keep working. The actual port-finding and stale-process-killing logic now
# lives in app/preflight.py — single source of truth for managed services.
DEFAULT_PORT = int(os.environ.get("MATRX_PORT_BASE", "22140"))
MAX_PORT_SCAN = 20
DISCOVERY_DIR = MATRX_HOME_DIR
DISCOVERY_FILE = DISCOVERY_DIR / "local.json"


def write_discovery_file(port: int, tunnel_url: str | None = None) -> None:
    """Write the active port (and optional tunnel URL) to ~/.matrx/local.json.

    Delegates the file write to app.preflight.write_discovery_file so the
    schema (top-level legacy fields + nested `services` map + atomic rename)
    is owned in one place. We still own the runtime-singleton sync into
    app.api.tunnel_state so /extension/tunnel/status is correct immediately
    after startup without waiting for the next tunnel update.
    """
    try:
        from app.preflight import write_discovery_file as _pf_write
        _pf_write(
            engine_port=port,
            pid=os.getpid(),
            version=_APP_VERSION,
            tunnel_url=tunnel_url,
        )
        logger.info("Discovery file written: %s", DISCOVERY_FILE)

        try:
            from app.api.tunnel_state import set_local_url, set_tunnel_url
            set_local_url(port)
            if tunnel_url:
                set_tunnel_url(tunnel_url)
        except Exception:
            logger.debug("Failed to seed tunnel state singleton", exc_info=True)
    except Exception:
        logger.warning("Failed to write discovery file", exc_info=True)


def update_discovery_tunnel(tunnel_url: str | None) -> None:
    """Update only the tunnel fields in the discovery file (called after tunnel starts).

    Also keeps the in-memory tunnel-state singleton in sync so
    ``GET /extension/tunnel/status`` reflects the change without a
    filesystem read on every poll. Both the disk file (for clients that
    discover the engine before authenticating) and the singleton (for
    runtime introspection by authenticated clients) must agree.
    """
    try:
        from app.preflight import update_discovery_service
        if tunnel_url:
            update_discovery_service(
                "tunnel",
                {
                    "url": tunnel_url,
                    "ws": tunnel_url.replace("https://", "wss://") + "/ws",
                },
            )
        else:
            update_discovery_service("tunnel", None)
    except Exception:
        logger.debug("Failed to update tunnel in discovery file", exc_info=True)

    # Mirror into the runtime singleton. Best-effort — telemetry must
    # never block discovery-file maintenance.
    try:
        from app.api.tunnel_state import mark_tunnel_inactive, set_tunnel_url
        if tunnel_url:
            set_tunnel_url(tunnel_url)
        else:
            mark_tunnel_inactive()
    except Exception:
        logger.debug("Failed to update tunnel state singleton", exc_info=True)


def remove_discovery_file() -> None:
    """Remove the discovery file — but ONLY if this process owns it.

    Multi-instance coexistence is now allowed, so ~/.matrx/local.json may
    belong to a DIFFERENT live engine (observed live: a test engine's clean
    shutdown deleted the production app's local.json). Only unlink when the
    recorded pid is ours; otherwise leave the primary engine's file in place.
    """
    try:
        if not DISCOVERY_FILE.exists():
            return
        owner_pid = None
        try:
            owner_pid = json.loads(DISCOVERY_FILE.read_text()).get("pid")
        except (OSError, json.JSONDecodeError, ValueError):
            # Unreadable/corrupt file we can't attribute — treat as ours to
            # clean up (a corrupt local.json is not another engine's live state).
            owner_pid = os.getpid()
        if owner_pid != os.getpid():
            logger.info(
                "Discovery file owned by pid %s — leaving in place", owner_pid
            )
            return
        DISCOVERY_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def create_tray_image() -> "Image.Image":
    from PIL import Image  # lazy — only needed when a tray is actually shown

    icon_path = STATIC_DIR / "apple-touch-icon.png"
    if icon_path.exists():
        return Image.open(str(icon_path))
    return Image.new("RGB", (64, 64), color=(59, 130, 246))


def _build_log_config() -> dict:
    """Build uvicorn log_config.

    - uvicorn / uvicorn.error → NullHandler here; the real routing is the
      _UvicornLogForwarder attached in start_server(), which sends every
      uvicorn record through system_logger (file + console + sensitive-data
      masking). uvicorn's lifecycle messages — "Started server", "Shutting
      down", "Waiting for connections to close" — previously went to a
      stdout-only handler, which in the packaged app meant they were INVISIBLE
      in system.log. During the 2026-07-11/12 silent-death investigation,
      "Shutting down" / "Waiting for connections to close" were exactly the
      lines that would have identified the wedge in minutes.
    - uvicorn.access → suppressed entirely (our middleware already logs every request)
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "null": {"class": "logging.NullHandler"},
        },
        "loggers": {
            "uvicorn":        {"handlers": ["null"], "level": "INFO", "propagate": False},
            "uvicorn.error":  {"handlers": ["null"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["null"], "level": "INFO", "propagate": False},
        },
    }


class _UvicornLogForwarder(logging.Handler):
    """Forward uvicorn's log records into system_logger.

    One pipeline: system_logger already owns the console handler, the rotating
    system.log file handler, sensitive-data masking, and console dedup — so
    uvicorn records get all of that for free and appear in the same places as
    every other engine log line, prefixed with [uvicorn].
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Forward exc_info: uvicorn's "Exception in ASGI application"
            # carries its traceback ONLY there — dropping it would log a bare
            # one-liner and destroy the traceback everywhere.
            logger._log(
                record.levelno,
                "[uvicorn] " + record.getMessage(),
                exc_info=record.exc_info or None,
            )
        except Exception:
            pass  # logging must never take down the server


_uvicorn_server: uvicorn.Server | None = None
_server_thread: threading.Thread | None = None
_shutdown_event = threading.Event()


def start_server(port: int) -> None:
    global _uvicorn_server
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        log_config=_build_log_config(),
        # Cap the graceful-shutdown connection drain. The default (None) waits
        # FOREVER for open connections — and this engine always has at least
        # one infinite SSE stream open (/downloads/stream) plus WebSockets, so
        # "wait for connections to drain" meant "never run lifespan teardown".
        # Result on 2026-07-11/12: shutdown started (1012 WS close), teardown
        # never ran, and the force-exit killed the process with zero teardown
        # logs. 5s gives real in-flight requests a chance, then teardown runs.
        timeout_graceful_shutdown=5,
    )
    # uvicorn.Config.__init__ has already applied the dictConfig above, so the
    # forwarder we attach now survives. Do NOT attach before Config creation —
    # dictConfig replaces the uvicorn loggers' handlers.
    forwarder = _UvicornLogForwarder(level=logging.INFO)
    for _name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(_name).addHandler(forwarder)
    server = uvicorn.Server(config)
    _uvicorn_server = server
    try:
        server.run()
    finally:
        logger.info("[shutdown] uvicorn server thread exited")
        _shutdown_event.set()


def on_quit(icon: Icon, item: MenuItem) -> None:
    logger.warning("[shutdown] Tray 'Quit' selected — beginning graceful shutdown")
    remove_discovery_file()
    icon.stop()
    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True
        _shutdown_event.set()
    else:
        os._exit(0)


def _is_tauri_sidecar() -> bool:
    """Return True when this process was launched by the Tauri desktop shell.

    Tauri sets TAURI_SIDECAR=1 before spawning the engine.  When this flag is
    present we must NOT create a pystray tray icon — Tauri already owns the
    single tray icon that represents the entire application to the user.
    """
    return os.environ.get("TAURI_SIDECAR", "") == "1"


def _start_parent_watchdog() -> None:
    """Monitor the parent process and self-terminate if it dies.

    When running as a Tauri sidecar, if the Tauri app crashes or is force-killed
    (SIGKILL, Activity Monitor, Task Manager), the sidecar is orphaned — adopted
    by PID 1 (launchd/init/System) and keeps running forever with ports bound.
    This is the primary cause of orphaned `matrx-engine` processes
    (also seen as legacy `aimatrx-engine` on installs from before the rename).

    On Windows, Tauri spawns the sidecar via an intermediate pipe/shim helper
    whose lifetime is shorter than the Tauri app itself.  os.getppid() therefore
    returns the PID of that short-lived launcher, which exits almost immediately
    after the child starts — making the watchdog falsely believe the parent died
    and triggering an immediate self-termination.

    To fix this, Tauri passes TAURI_APP_PID (the Tauri process's own PID) as an
    environment variable.  On Windows we watch that PID instead of os.getppid().
    On macOS/Linux the OS guarantees that os.getppid() stays correct (it becomes
    1 only when the true parent dies), so we use it there unchanged.
    """
    import time

    if sys.platform == "win32":
        # Prefer the explicit Tauri app PID passed via env; fall back to PPID
        # only if the env var is absent (e.g. standalone / dev mode).
        tauri_pid_str = os.environ.get("TAURI_APP_PID", "")
        if tauri_pid_str.isdigit():
            parent_pid = int(tauri_pid_str)
        else:
            parent_pid = os.getppid()
    else:
        parent_pid = os.getppid()

    if parent_pid <= 1:
        return

    def _watch() -> None:
        while not _shutdown_event.is_set():
            time.sleep(0.5)
            parent_gone = False

            if sys.platform == "win32":
                # os.kill(pid, 0) is NOT a liveness probe on Windows:
                # signal 0 == CTRL_C_EVENT, so it calls
                # GenerateConsoleCtrlEvent — either failing for GUI parents
                # (false "parent gone" → we'd kill a healthy engine) or
                # actually delivering Ctrl+C to the process group.
                try:
                    import psutil  # core dep
                    parent_gone = not psutil.pid_exists(parent_pid)
                except Exception:
                    parent_gone = False  # fail open: never self-kill on doubt
            else:
                current_ppid = os.getppid()
                parent_gone = (current_ppid == 1 or current_ppid != parent_pid)

            if parent_gone:
                logger.warning(
                    "Parent process (PID %d) is gone — self-terminating to avoid orphan",
                    parent_pid,
                )
                remove_discovery_file()
                if _uvicorn_server is not None:
                    _uvicorn_server.should_exit = True
                _shutdown_event.set()
                # 20s = the 15s sidecar teardown join in _wait_forever + 5s
                # margin, so the join and child-kill actually get to run. The
                # old 10s force-exit preempted the join mid-teardown — the
                # very bug the 2026-07-13 shutdown overhaul removed elsewhere.
                _schedule_force_exit(20)
                return

    watchdog = threading.Thread(target=_watch, daemon=True, name="parent-watchdog")
    watchdog.start()


def _has_system_tray() -> bool:
    """Check if a system tray is available (not available in WSL/headless)."""
    if PLATFORM["is_windows"] or PLATFORM["is_mac"]:
        return True
    if not CAPABILITIES["has_display"]:
        return False
    if PLATFORM["is_wsl"]:
        return False
    return True


def _teardown_join_seconds() -> int:
    """How long the main thread waits for the lifespan teardown before it
    force-kills our own children and exits.

    SIDECAR MODE MUST STAY UNDER RUST'S 20s SIGKILL. Rust's sigterm_then_kill
    escalates to SIGKILL 20s after SIGTERM, and its detached safety net only
    pkills the engine/cloudflared/llama-server — NOT the Playwright driver +
    chrome-headless-shell tree. If our join outlasted 20s, SIGKILL would land
    before _kill_child_subprocesses() ever ran and the Playwright tree would
    orphan — the exact leak that cleanup exists to prevent. 15s leaves a 5s
    margin for the kill + exit path.

    Standalone mode has no outer killer, so teardown gets the full documented
    budget (5s uvicorn drain + ~18s child teardowns + margin).
    """
    return 15 if _is_tauri_sidecar() else 25


def _wait_forever() -> None:
    """Block the main thread until SIGTERM, SIGINT, or the server exits.

    Uses _shutdown_event (set by the signal handler or when the server thread
    finishes) instead of time.sleep(), so the thread wakes up immediately on
    SIGTERM rather than sleeping for up to 1 second before checking.

    After receiving the shutdown signal, waits up to _teardown_join_seconds()
    for the uvicorn server thread to complete its lifespan teardown (proxy
    stop, Playwright close, SQLite close, etc.) before forcing exit. The
    previous fixed 10s join routinely fired MID-teardown and os._exit(0)'d
    with zero log output — that silent exit was the "engine vanished, UI spun
    forever" bug of 2026-07-11/12. Every exit from this function now logs.
    """
    try:
        _shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    join_s = _teardown_join_seconds()
    logger.info(
        "[shutdown] Main thread woke — waiting up to %ds for uvicorn lifespan teardown",
        join_s,
    )
    remove_discovery_file()
    teardown_clean = True
    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True
        server_thread_ref = _server_thread
        if server_thread_ref is not None:
            server_thread_ref.join(timeout=join_s)
            teardown_clean = not server_thread_ref.is_alive()
    if not teardown_clean:
        # Lifespan teardown did not finish — clean up our own children.
        logger.error(
            "[shutdown] Lifespan teardown did NOT complete within %ds — "
            "force-killing engine-owned children and exiting. This is a bug: "
            "something blocked uvicorn's drain or a service teardown. "
            "Check the last '[launcher]' lines above for the stuck service.",
            join_s,
        )
        _kill_child_subprocesses()
    else:
        logger.info("[shutdown] Teardown complete — engine exiting cleanly (pid=%d)", os.getpid())
    os._exit(0)


def setup_tray(port: int) -> None:
    """Show a pystray system-tray icon (standalone / non-Tauri mode only).

    When running as a Tauri sidecar the tray icon is intentionally skipped;
    instead we just block the main thread so the server keeps running.
    The Tauri shell already has its own tray icon for the whole app.
    """
    if _is_tauri_sidecar():
        logger.info(
            "Running as Tauri sidecar — skipping pystray tray icon "
            "(Tauri manages the system tray for this app)"
        )
        _wait_forever()
        return

    if IS_DEV_ENGINE:
        # Dev engines run trayless: (a) 15 agent engines = 15 tray icons of
        # noise, and (b) with the pystray AppKit run loop owning the main
        # thread, SIGTERM is silently ignored (MXL-D-042) — dev engines must
        # die cleanly when their terminal/agent kills them. _wait_forever()
        # keeps the main thread in a signal-handling wait instead.
        logger.info(
            "Dev engine — skipping pystray tray icon (signal-friendly "
            "headless mode; set MATRX_LIVE_ENGINE=1 for the tray)"
        )
        _wait_forever()
        return

    if not _has_system_tray():
        logger.info(
            "No system tray available (WSL/headless) — running without tray icon"
        )
        _wait_forever()
        return

    # Lazy import — pystray touches the X display on import (Linux), so we
    # only pull it in here, after the sidecar/headless guards above have
    # already returned for environments without a usable tray.
    from pystray import Icon, Menu, MenuItem

    menu = Menu(
        MenuItem(f"Matrx Local (:{port})", lambda *_: None, enabled=False),
        Menu.SEPARATOR,
        MenuItem("Quit", on_quit),
    )
    icon = Icon("matrx_local", create_tray_image(), "Matrx Local", menu)
    icon.run()
    # icon.run() returns after Quit — wait for the uvicorn thread to finish
    # its lifespan teardown instead of letting interpreter exit kill the
    # daemon thread mid-teardown (which orphaned cloudflared/Playwright in
    # standalone mode).
    _wait_forever()


_exit_signal_logged = threading.Event()


def _handle_exit(signum: int, frame: object) -> None:  # noqa: ARG001
    """Graceful shutdown: stop uvicorn (triggers lifespan teardown) then exit.

    LOUD BY CONTRACT: the very first thing this handler does is log which
    signal arrived. The engine died silently ~12 times on 2026-07-11/12
    (SIGTERM → no log line anywhere → process gone → UI spun forever) and the
    only forensic breadcrumb was uvicorn's incidental 1012 WebSocket close.
    A shutdown that doesn't announce itself is indistinguishable from a crash.

    Telling uvicorn to shut down via server.should_exit lets the FastAPI
    lifespan teardown run (stops proxy on 22180, tunnel, scraper, SQLite, etc.)
    before the process terminates.  Setting _shutdown_event wakes the main
    thread from _wait_forever() so it can join the server thread and exit.

    As a safety net, a background timer forces os._exit() after 30 seconds
    even if the lifespan teardown hangs — this guarantees the process WILL
    die and prevents orphaned sidecar processes on user machines.
    The 30s budget covers: uvicorn connection drain 5s (timeout_graceful_shutdown)
    + wake-word 3s + scheduler 3s + doc-watcher 3s + proxy 4s + tunnel 5s +
    scraper 5s + browsers 3s, with margin. Note that in sidecar mode Rust's
    sigterm_then_kill escalates to SIGKILL after 20s regardless — this timer
    is the standalone-mode (and belt-and-suspenders) guarantee.
    """
    # ORDER MATTERS: arm the escape hatches FIRST, talk second. Logging and
    # remove_discovery_file() both do blocking I/O (stdout pipe to the Tauri
    # parent, flock'd file writes, local.json reads) — if any of that wedged
    # BEFORE should_exit/_shutdown_event/force-exit were armed, the shutdown
    # would never start and (in standalone mode, with no outer killer) the
    # engine could hang forever. Guaranteed-death first, loudness second.
    already = _exit_signal_logged.is_set()
    _exit_signal_logged.set()
    if not already:
        if _uvicorn_server is not None:
            _uvicorn_server.should_exit = True
        _shutdown_event.set()
        _schedule_force_exit(30)

    try:
        sig_name = signal.Signals(signum).name
    except ValueError:
        sig_name = str(signum)
    if already:
        logger.warning("[shutdown] Received %s again — shutdown already in progress", sig_name)
        return
    logger.warning(
        "[shutdown] Received %s (pid=%d) — beginning graceful shutdown: "
        "uvicorn drain (≤5s) → lifespan teardown → exit. If no "
        "'[shutdown] … complete' line follows, the teardown hung and the "
        "force-exit watchdog fired.",
        sig_name, os.getpid(),
    )
    remove_discovery_file()

    if _uvicorn_server is None:
        logger.warning("[shutdown] uvicorn server not yet created — exiting immediately")
        os._exit(0)


def _kill_child_subprocesses() -> None:
    """Kill child subprocesses WE spawned — by remembered PID.

    Called only when the lifespan teardown timed out. Killing by PID instead
    of pkill-by-name matters: `pkill -f "cloudflared tunnel"` matched ANY
    cloudflared on the machine (e.g. the user's personal named tunnels),
    violating the ownership contract.

    Covers two engine-owned trees that otherwise leak on a hung/crashed
    shutdown:
      1. the cloudflared tunnel process, and
      2. the Playwright driver + its chrome-headless-shell browser tree
         (tracked by app/services/scraper/engine.py; see terminate_playwright_tree).
    """
    # 1) cloudflared tunnel — by remembered PID.
    try:
        from app.services.tunnel.manager import get_tunnel_manager

        tm = get_tunnel_manager()
        proc = getattr(tm, "_process", None)
        pid = getattr(proc, "pid", None)
        if pid is not None:
            import signal as _signal

            try:
                os.kill(pid, _signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            else:
                import time as _time

                _time.sleep(0.3)
                try:
                    os.kill(pid, _signal.SIGKILL if sys.platform != "win32" else _signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
    except Exception:
        logger.debug("cloudflared cleanup failed", exc_info=True)

    # 2) Playwright driver + browser tree — by remembered driver PID.
    try:
        from app.services.scraper.engine import get_scraper_engine, terminate_playwright_tree

        terminate_playwright_tree(get_scraper_engine().driver_pid)
    except Exception:
        logger.debug("Playwright tree cleanup failed", exc_info=True)


def _schedule_force_exit(timeout_seconds: int) -> None:
    """Spawn a daemon thread that force-kills the process after a timeout.

    This is the last-resort guarantee that the engine process WILL exit even
    if the lifespan teardown hangs (stuck Playwright browser, blocked I/O,
    etc.).  Without this, a hung teardown leaves the process alive with
    ports bound and resources locked — the exact orphan sidecar problem.
    """
    def _force_exit() -> None:
        import time
        time.sleep(timeout_seconds)
        logger.warning(
            "Shutdown watchdog: lifespan teardown did not complete within %ds — forcing exit",
            timeout_seconds,
        )
        _kill_child_subprocesses()
        os._exit(1)

    watchdog = threading.Thread(target=_force_exit, daemon=True)
    watchdog.start()


def main() -> None:
    print("[phase:starting] Engine initializing...", flush=True)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_exit)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_exit)
    # Windows: CTRL_BREAK_EVENT maps to SIGBREAK (NOT SIGINT). Without this
    # handler, the default disposition terminates the process immediately —
    # /admin/shutdown then skipped the entire lifespan teardown and orphaned
    # cloudflared/Playwright children.
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_exit)

    # Parent-death watchdog: self-terminate when the launching process dies, so
    # the engine never survives as a port-squatting zombie.
    #   • Sidecar: dies with the Tauri app (its original purpose).
    #   • Dev engine: dies with the terminal / coding-agent that ran
    #     `uv run python run.py`. Without this, ending that session reparents
    #     the engine to launchd (PPID 1) and it runs forever holding its port —
    #     the zombie engines that pile up on dev machines with many agents
    #     (MXL-D-049). Dev engines are now on 22240+, so the zombie squats the
    #     DEV range, but a zombie is still a zombie — kill it with its session.
    #   • Explicit-live standalone (MATRX_LIVE_ENGINE=1) is intentionally exempt:
    #     a deliberate live run may be meant to outlive its shell.
    if _is_tauri_sidecar() or IS_DEV_ENGINE:
        _start_parent_watchdog()

    # ── Preflight ────────────────────────────────────────────────────────────
    # Sweep every managed service (engine sidecars from prior installs, stray
    # cloudflared tunnel processes, orphaned llama-server) BEFORE we touch a
    # port. The full registry of "what we manage" lives in app/preflight.py;
    # adding a new service is a one-line edit there. Ancestor PIDs (the Tauri
    # shell when we run as a sidecar) are protected automatically.
    #
    # The structured `[preflight]` lines emitted from clean_orphans() are the
    # source of truth for what was found and what we did about it. Do not
    # add a parallel "summary" log here — duplicating those lines would drift
    # out of sync with the actual cleanup behavior.
    print(
        "[phase:preflight] Checking for lingering processes from previous session...",
        flush=True,
    )
    try:
        from app.preflight import clean_orphans, assign_engine_port
        if os.environ.get("MATRX_SKIP_ORPHAN_SCAN") == "1":
            # Test/isolated mode: this instance must NEVER touch other
            # processes on the machine. The pytest engine fixture sets this
            # (tests/conftest.py) so a test-spawned engine cannot kill a live
            # dev engine — clean_orphans() pattern-matches ALL matrx engines
            # system-wide and the live-owner health probe is not a guarantee.
            # Loud by design: if you see this line outside a test run,
            # something is misconfigured.
            print(
                "[phase:preflight] MATRX_SKIP_ORPHAN_SCAN=1 — SKIPPING orphan "
                "sweep (isolated/test instance; no system processes touched)",
                flush=True,
            )
        else:
            clean_orphans()
    except Exception:
        # Preflight must never block startup. Fall back to direct port bind
        # and rely on the port scan below if the orphan sweep failed.
        logger.exception("Preflight clean_orphans failed — continuing anyway")

        def assign_engine_port() -> int:  # type: ignore[no-redef]
            import socket as _s
            env = os.environ.get("MATRX_PORT")
            if env:
                return int(env)
            for offset in range(MAX_PORT_SCAN):
                p = DEFAULT_PORT + offset
                with _s.socket(_s.AF_INET, _s.SOCK_STREAM) as sk:
                    # Windows: SO_REUSEADDR binds over live listeners — see
                    # app/preflight.py _is_port_free for the full rationale.
                    if sys.platform == "win32":
                        if hasattr(_s, "SO_EXCLUSIVEADDRUSE"):
                            sk.setsockopt(_s.SOL_SOCKET, _s.SO_EXCLUSIVEADDRUSE, 1)
                    else:
                        sk.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
                    try:
                        sk.bind(("127.0.0.1", p))
                        return p
                    except OSError:
                        continue
            raise SystemExit("No free port in scan range")

    print("[phase:port] Finding available port...", flush=True)
    port = assign_engine_port()
    logger.info("Starting Matrx Local on port %d", port)
    print(f"[phase:port] Engine will bind to port {port}", flush=True)

    write_discovery_file(port)

    print("[phase:server] Starting server...", flush=True)
    global _server_thread
    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    _server_thread = server_thread
    server_thread.start()

    try:
        setup_tray(port)
    except (KeyboardInterrupt, SystemExit):
        remove_discovery_file()
        os._exit(0)


if __name__ == "__main__":
    main()
