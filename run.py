"""Matrx Local — entry point.

Starts the FastAPI server.  When running standalone (outside the Tauri desktop
shell) also creates a pystray system-tray icon for port display / quit.

When spawned by Tauri as a sidecar (TAURI_SIDECAR=1), the tray icon is
**skipped** — Tauri already manages one system-tray icon that represents the
entire application.  Letting Python also create a tray icon would produce two
(or three) icons in the user's system tray.

Port selection:
  1. MATRX_PORT env var (explicit override — fails hard if taken)
  2. Default port 22140 (chosen to avoid conflicts with common dev ports)
  3. If default port is held by a dead/stale previous instance of ourselves,
     we kill the stale process and reclaim the port rather than drifting.
  4. Auto-scan: tries up to 20 consecutive ports until one is free (last resort)

The chosen port is written to ~/.matrx/local.json so the web/mobile frontend
can discover it without configuration.
"""

from __future__ import annotations

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

logger = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys.executable).parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent

STATIC_DIR = BUNDLE_DIR / "static"

# Default port + scan range — these constants stay here so external imports
# (notably app/api/tunnel_routes.py imports DISCOVERY_FILE from this module)
# keep working. The actual port-finding and stale-process-killing logic now
# lives in app/preflight.py — single source of truth for managed services.
DEFAULT_PORT = 22140
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
    try:
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

    - uvicorn.error  → our console format (startup/shutdown messages)
    - uvicorn.access → suppressed entirely (our middleware already logs every request)
    """
    from app.config import LOCAL_DEV

    fmt = "%(levelname)s - %(message)s" if LOCAL_DEV else "%(asctime)s - %(levelname)s - %(message)s"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": fmt},
        },
        "handlers": {
            "default": {"class": "logging.StreamHandler", "stream": "ext://sys.stdout", "formatter": "default"},
            "null":    {"class": "logging.NullHandler"},
        },
        "loggers": {
            "uvicorn":        {"handlers": ["default"], "level": "INFO",  "propagate": False},
            "uvicorn.error":  {"handlers": ["default"], "level": "INFO",  "propagate": False},
            "uvicorn.access": {"handlers": ["null"],    "level": "INFO",  "propagate": False},
        },
    }


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
    )
    server = uvicorn.Server(config)
    _uvicorn_server = server
    try:
        server.run()
    finally:
        _shutdown_event.set()


def on_quit(icon: Icon, item: MenuItem) -> None:
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
                try:
                    os.kill(parent_pid, 0)
                except (ProcessLookupError, PermissionError, OSError):
                    parent_gone = True
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
                _schedule_force_exit(10)
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


def _wait_forever() -> None:
    """Block the main thread until SIGTERM, SIGINT, or the server exits.

    Uses _shutdown_event (set by the signal handler or when the server thread
    finishes) instead of time.sleep(), so the thread wakes up immediately on
    SIGTERM rather than sleeping for up to 1 second before checking.

    After receiving the shutdown signal, waits up to 10 seconds for the uvicorn
    server thread to complete its lifespan teardown (proxy stop, Playwright
    close, SQLite close, etc.) before forcing exit.
    """
    try:
        _shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    remove_discovery_file()
    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True
        server_thread_ref = _server_thread
        if server_thread_ref is not None:
            server_thread_ref.join(timeout=10)
    _kill_child_subprocesses()
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


def _handle_exit(signum: int, frame: object) -> None:  # noqa: ARG001
    """Graceful shutdown: stop uvicorn (triggers lifespan teardown) then exit.

    Telling uvicorn to shut down via server.should_exit lets the FastAPI
    lifespan teardown run (stops proxy on 22180, tunnel, scraper, SQLite, etc.)
    before the process terminates.  Setting _shutdown_event wakes the main
    thread from _wait_forever() so it can join the server thread and exit.

    As a safety net, a background timer forces os._exit() after 25 seconds
    even if the lifespan teardown hangs — this guarantees the process WILL
    die and prevents orphaned sidecar processes on user machines.
    The 25s budget covers: wake-word 3s + scheduler 3s + doc-watcher 3s +
    proxy 4s + tunnel 5s + scraper 5s + browsers 3s, with margin.
    """
    remove_discovery_file()

    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True
        _shutdown_event.set()
        _schedule_force_exit(25)
    else:
        os._exit(0)


def _kill_child_subprocesses() -> None:
    """Kill known child subprocesses that we spawned (cloudflared, etc.).

    Called just before os._exit() in the force-exit watchdog to prevent
    orphaned subprocesses when the Python lifespan teardown didn't complete.
    """
    import subprocess as _sp
    if sys.platform == "win32":
        _sp.run(["taskkill", "/F", "/T", "/IM", "cloudflared.exe"],
                capture_output=True, timeout=5)
    else:
        _sp.run(["pkill", "-TERM", "-f", "cloudflared tunnel"],
                capture_output=True, timeout=5)
        try:
            import time
            time.sleep(0.3)
            _sp.run(["pkill", "-KILL", "-f", "cloudflared tunnel"],
                    capture_output=True, timeout=5)
        except Exception:
            pass


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

    if _is_tauri_sidecar():
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
