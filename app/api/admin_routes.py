"""app/api/admin_routes.py — Engine lifecycle introspection and control.

Exposes three endpoints the parent process (Tauri Rust) uses to coordinate
with the engine without reaching across to kill its children directly:

    GET  /admin/status      Snapshot of every managed service the engine owns.
                            Returns the same data dump_diagnostics() captures,
                            without the heavy psutil walk — cheap to poll.

    POST /admin/shutdown    Signal the engine to gracefully tear down its
                            children and exit. Replies 200 immediately and
                            schedules the actual shutdown so Rust gets a
                            confirmation it can act on. The engine then
                            cascades: tunnel.stop() → proxy.stop() →
                            scraper.stop() → … → process exit.

    POST /admin/diagnose    Force-write a diagnostic snapshot to
                            ~/.matrx/diagnostics/ and return its path.
                            Useful for "the engine seems wedged but isn't
                            crashed" support tickets.

────────────────────────────────────────────────────────────────────────────
Why these endpoints exist
────────────────────────────────────────────────────────────────────────────
The engine has always responded to SIGTERM/SIGINT correctly — uvicorn's
should_exit triggers the FastAPI lifespan teardown which stops every child.
But when Rust sends SIGTERM at the same time as it pkills cloudflared,
the engine's tunnel.stop() races against Rust's pkill and ends up logging
"tunnel failed to stop cleanly" on every exit.

POST /admin/shutdown gives Rust an explicit, in-band way to say "shut
down" and a 200 response confirms the engine accepted the signal. Rust can
then wait for the process to exit naturally (no need to pkill the engine's
children behind its back) or fall back to SIGTERM if the engine is wedged.

────────────────────────────────────────────────────────────────────────────
Trust model
────────────────────────────────────────────────────────────────────────────
The engine binds to 127.0.0.1 only — only processes on the same machine
can reach these endpoints. They are listed in _PUBLIC_PATHS so they can be
called without a JWT (Rust has no JWT to send). Anyone with local code
execution can already SIGTERM the engine, so a localhost-only shutdown
endpoint adds no new attack surface.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from app.launcher import dump_diagnostics, get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# Tracks whether a shutdown has already been requested so repeated calls
# from Rust don't trigger multiple teardowns. Plain bool + threading.Lock
# because this can be hit from any thread.
_shutdown_requested = False
_shutdown_lock = threading.Lock()


@router.get("/status")
async def admin_status() -> dict[str, Any]:
    """Return a snapshot of every managed service plus engine process info.

    Cheap — does not walk all OS processes or read psutil network connections.
    Safe to poll every few seconds. For the heavy snapshot use POST /admin/diagnose.
    """
    return get_registry().snapshot()


@router.post("/shutdown")
async def admin_shutdown(background: BackgroundTasks) -> dict[str, Any]:
    """Initiate graceful engine shutdown and return immediately.

    The actual teardown runs as a background task so the HTTP response can
    flush before uvicorn starts shutting down. The teardown signals
    SIGTERM to ourselves which run.py's _handle_exit handler converts into:

      uvicorn.should_exit = True   ← triggers FastAPI lifespan teardown
        → app/main.py shutdown phases (S1..S7)
          → every child stops in reverse order with per-child timeouts
        → uvicorn exits the run loop
      _shutdown_event.set()        ← wakes the main thread
        → main thread exits, process terminates

    This call is idempotent. Repeated POSTs after the first return
    "already_shutting_down" without re-signaling.
    """
    global _shutdown_requested
    with _shutdown_lock:
        already = _shutdown_requested
        _shutdown_requested = True

    if already:
        logger.info("[launcher] /admin/shutdown — already shutting down (ignoring)")
        return {
            "status": "already_shutting_down",
            "engine_pid": os.getpid(),
            "received_at": time.time(),
        }

    logger.info("[launcher] /admin/shutdown — graceful shutdown requested by parent")
    background.add_task(_trigger_self_signal)
    return {
        "status": "accepted",
        "engine_pid": os.getpid(),
        "received_at": time.time(),
        "method": "self-signal SIGTERM after response flushes",
    }


@router.post("/diagnose")
async def admin_diagnose(reason: str | None = None) -> dict[str, Any]:
    """Force-write a diagnostic snapshot and return its path.

    Useful when the engine is alive and serving requests but something
    feels off (a child stuck in a weird state, an unexpected port held).
    The snapshot includes the registry state, all matrx-related processes,
    listening ports, environment, and live thread stacks.
    """
    registry = get_registry()
    path = await asyncio.to_thread(
        dump_diagnostics, registry, focus="manual", error=reason
    )
    return {
        "path": str(path),
        "engine_pid": os.getpid(),
        "reason": reason,
    }


# ── Internal ──────────────────────────────────────────────────────────────────


def _trigger_self_signal() -> None:
    """Send SIGTERM to ourselves so run.py's existing _handle_exit fires.

    Why a self-signal instead of directly setting _uvicorn_server.should_exit?
    run.py's _handle_exit also schedules the 25-second force-exit watchdog
    that guarantees the process WILL die even if a child hangs during
    teardown. Reusing the signal handler keeps that safety net active and
    avoids splitting "graceful shutdown" logic across two code paths.

    On Windows there's no SIGTERM; we send CTRL_BREAK_EVENT to ourselves
    which run.py's signal.signal(SIGINT, _handle_exit) catches. If even
    that fails (e.g. no console attached), we fall back to setting
    uvicorn.should_exit directly via the run module.
    """
    # Tiny pause so the HTTP response flushes before we tear down the server.
    time.sleep(0.1)

    def _direct_stop() -> bool:
        """Stop uvicorn via the run module directly. Returns True on success."""
        try:
            import sys as _sys

            # In a frozen sidecar the entry module is __main__, not "run".
            run_mod = _sys.modules.get("run") or _sys.modules.get("__main__")
            if run_mod is None or not hasattr(run_mod, "_uvicorn_server"):
                import run as run_mod  # noqa: PLC0415

            if run_mod._uvicorn_server is not None:
                run_mod._uvicorn_server.should_exit = True
                run_mod._shutdown_event.set()
                run_mod._schedule_force_exit(25)
                return True
        except Exception:
            logger.exception("[launcher] /admin/shutdown — direct uvicorn stop failed")
        return False

    try:
        if os.name == "nt":
            # CTRL_BREAK_EVENT delivery depends on console/process-group
            # topology and lands on SIGBREAK — historically unhandled, which
            # made shutdown a hard kill. The direct should_exit path is
            # deterministic, so prefer it on Windows.
            if not _direct_stop():
                os.kill(os.getpid(), signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            os.kill(os.getpid(), signal.SIGTERM)
        logger.info("[launcher] /admin/shutdown — self-signal delivered")
    except Exception:
        logger.warning(
            "[launcher] /admin/shutdown — self-signal failed, falling back to direct uvicorn stop",
            exc_info=True,
        )
        if not _direct_stop():
            # Both mechanisms failed — reset the latch so a retry can re-arm
            # instead of returning "already_shutting_down" forever.
            global _shutdown_requested
            _shutdown_requested = False
