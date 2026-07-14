import asyncio
import io
import os
import re
import uuid
import zipfile
from pathlib import Path
from typing import Optional

def _read_version() -> str:
    """Read version — tries importlib.metadata first (works in packaged binary),
    then falls back to parsing pyproject.toml (works in dev mode and PyInstaller)."""
    # importlib.metadata works when the package is installed (dev: uv install, prod: PyInstaller)
    try:
        from importlib.metadata import version as _meta_version, PackageNotFoundError
        try:
            return _meta_version("matrx-local")
        except PackageNotFoundError:
            pass
    except ImportError:
        pass

    # Fallback: read pyproject.toml. Check several candidate locations:
    # 1. sys._MEIPASS — PyInstaller extraction dir (pyproject.toml bundled via datas)
    # 2. project root via __file__ traversal — works in dev mode
    try:
        import sys as _sys
        candidates: list[Path] = []
        if hasattr(_sys, "_MEIPASS"):
            candidates.append(Path(_sys._MEIPASS) / "pyproject.toml")
        candidates += [
            Path(__file__).parent.parent.parent / "pyproject.toml",  # dev layout
            Path(__file__).parent.parent / "pyproject.toml",         # shallow edge case
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

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.api.browser_events import handle_browser_event
from app.api.system_control import get_system_info
from app.common.system_logger import get_logger
import app.common.access_log as access_log
from app.config import (
    BASE_DIR, LOG_DIR, TEMP_DIR, DATA_DIR, CONFIG_DIR,
    MATRX_HOME_DIR, MATRX_USER_DIR,
    MATRX_NOTES_DIR, MATRX_FILES_DIR, MATRX_CODE_DIR,
    MATRX_WORKSPACES_DIR, MATRX_DATA_DIR,
)
from app.services.screenshots.capture import take_screenshot
from app.utils.directory_utils.generate_directory_structure import (
    get_structure_with_common_configs,
)

from pathlib import Path

# Initialize the APIRouter
router = APIRouter()
logger = get_logger()


# Root endpoint
@router.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"status": "ok", "service": "matrx-local", "version": _APP_VERSION}


# ── Public discovery / health endpoints ─────────────────────────────────


@router.get("/health")
async def health():
    """Health check that tells the truth about managed services.

    CONTRACT: ``status`` is ALWAYS the literal "ok" while the engine process
    is alive — matrx-extend's discovery pins it with `z.literal('ok')`
    (matrx-extend src/lib/desktop/types.ts) and discards the port on any
    other value, so overloading ``status`` with degraded states makes a
    LIVE engine invisible to the extension. Liveness and health are
    different questions: ``status`` answers "is the engine here", while
    ``health`` / ``failed`` / ``degraded`` answer "is everything working" —
    v1.3.105 shipped an engine that answered a bare {"status": "ok"} while
    ai_engine was failed and 0 tools were registered (MXL-D-029/MXL-D-030),
    which is why the detail fields exist. Reading the in-memory snapshot
    costs microseconds, so this stays probe-cheap (MXL-D-018).
    """
    from app.launcher import get_registry

    services = get_registry().snapshot().get("services", {})
    failed = sorted(n for n, s in services.items() if s.get("state") == "failed")
    degraded = sorted(n for n, s in services.items() if s.get("state") == "degraded")

    health_state = "ok"
    if degraded:
        health_state = "degraded"
    if failed:
        health_state = "failed_services"

    payload: dict = {
        "status": "ok",  # liveness literal — see contract note above
        "health": health_state,
        "service": "matrx-local",
        "version": _APP_VERSION,
    }
    if failed:
        payload["failed"] = failed
    if degraded:
        payload["degraded"] = degraded

    # Remote app-config provenance: {tier, fetched_at, update_required, notice}
    # so the desktop UI / diagnostics can show "config: remote (2m ago)" vs
    # "compiled defaults", gate on the version floor, and render the operator
    # notice. Never allowed to break the liveness probe.
    try:
        from app.services.app_config import get_app_config_service

        payload["app_config"] = get_app_config_service().status_payload()
    except Exception:
        logger.warning("[routes] /health app_config status read failed", exc_info=True)

    return payload


@router.get("/version")
async def version():
    """Return the engine version."""
    return {"version": _APP_VERSION, "service": "matrx-local"}


@router.get("/ports")
async def ports(request: Request):
    """Return the ports the engine and proxy are listening on."""
    from app.services.proxy.server import get_proxy_server

    proxy = get_proxy_server()
    return {
        "engine": request.url.port,
        "proxy": proxy.port if proxy.running else None,
    }


# Trigger event
@router.post("/trigger")
async def trigger_event(data: dict):
    logger.info(f"Trigger event called with data: {data}")
    try:
        response = await handle_browser_event(data)
        return response
    except Exception as e:
        logger.error(f"Error in trigger_event: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error triggering event: {str(e)}")


# System info endpoint
@router.get("/system/info")
async def system_info():
    logger.info("System info endpoint accessed")
    try:
        return get_system_info()
    except Exception as e:
        logger.error(f"Error fetching system info: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Error fetching system info: {str(e)}"
        )


@router.get("/system/paths")
async def system_paths():
    """Return all named path aliases as resolved absolute paths.

    React and microservices use this to learn where things actually live on
    the current user's machine.  They should never construct paths themselves.

    Response shape:
    {
      "aliases": {
        "@matrx":  "/home/arman/.matrx",
        "@docs":   "/home/arman/.matrx/documents",
        "@temp":   "/home/arman/.matrx/temp",       // or OS cache dir
        "@data":   "...",
        "@logs":   "...",
        "@home":   "/home/arman"
      },
      "resolved": {
        "discovery":  "/home/arman/.matrx/local.json",
        "settings":   "/home/arman/.matrx/settings.json",
        "documents":  "/home/arman/.matrx/documents",
        "temp":       "...",
        "data":       "...",
        "logs":       "...",
        "config":     "...",
        "screenshots":"..."
      }
    }
    """
    from app.tools.session import _build_alias_map
    from app.services.paths.manager import safe_dir

    aliases = {alias: str(path) for alias, path in _build_alias_map().items()}

    notes   = safe_dir("notes")
    files   = safe_dir("files")
    code    = safe_dir("code")
    ws      = safe_dir("workspaces")
    adata   = safe_dir("agent_data")
    tmp     = safe_dir("temp")
    logs_d  = safe_dir("logs")

    return {
        "aliases": aliases,
        "resolved": {
            # Engine internals
            "discovery":   str(MATRX_HOME_DIR / "local.json"),
            "settings":    str(MATRX_HOME_DIR / "settings.json"),
            "instance":    str(MATRX_HOME_DIR / "instance.json"),
            "agent_data":  str(adata),
            "workspaces":  str(ws),
            # User-visible (may be user-overridden)
            "user_root":   str(MATRX_USER_DIR),
            "notes":       str(notes),
            "files":       str(files),
            "code":        str(code),
            # Platform cache
            "temp":        str(tmp),
            "screenshots": str(tmp / "screenshots"),
            "data":        str(DATA_DIR),
            "logs":        str(logs_d),
            "config":      str(CONFIG_DIR),
        },
    }


@router.post("/system/open-folder")
async def open_system_folder(data: dict):
    """Open the logs or data folder in the OS file manager."""
    from app.tools.tools import open_path_cross_platform

    folder = data.get("folder", "logs")
    if folder == "logs":
        target = Path(LOG_DIR)
    elif folder == "data":
        target = Path(DATA_DIR)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown folder: {folder}")

    target.mkdir(parents=True, exist_ok=True)
    target_str = str(target.resolve())

    success, message = open_path_cross_platform(target_str)
    if not success:
        logger.error("Failed to open folder '%s': %s", target_str, message)
        raise HTTPException(status_code=500, detail=message)
    return {"opened": target_str}


# List files
@router.get("/files")
async def list_files(directory: str = "."):
    logger.info(f"Listing files in directory: {directory}")
    try:
        return {"files": os.listdir(directory)}
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error listing files: {str(e)}")


# Capture screenshot
@router.post("/screenshot")
async def capture_screenshot():
    logger.info("Capture screenshot endpoint accessed")
    try:
        screenshot_dir = os.path.join(TEMP_DIR, "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)

        unique_id = uuid.uuid4().hex
        screenshot_filename = f"screenshot_{unique_id}.png"
        screenshot_path = os.path.join(screenshot_dir, screenshot_filename)

        saved_path = take_screenshot(screenshot_path)
        logger.info(f"Screenshot saved at: {saved_path}")

        return {"screenshot_path": saved_path}
    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Error capturing screenshot: {str(e)}"
        )


# ── Logging endpoints ────────────────────────────────────────────────────────


@router.get("/logs")
async def get_logs(n: int = Query(default=100, ge=1, le=2000)):
    """Return the last *n* lines of system.log as plain strings."""
    log_file = Path(LOG_DIR) / "system.log"
    logger.info("Logs endpoint accessed")
    try:
        # system.log persists across runs and can be tens of MB — read it off
        # the event loop.
        def _tail() -> list[str]:
            with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
                return fh.readlines()[-n:]

        lines = await asyncio.to_thread(_tail)
        return {"logs": [l.rstrip("\n") for l in lines]}
    except FileNotFoundError:
        logger.warning("Log file not found: %s", log_file)
        raise HTTPException(status_code=404, detail="Log file not found")
    except Exception as e:
        logger.error(f"Error reading log file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/access")
async def get_access_logs(n: int = Query(default=100, ge=1, le=500)):
    """Return the last *n* structured access-log entries as JSON."""
    return {"entries": access_log.recent(n)}


@router.get("/logs/stream")
async def stream_system_log():
    """SSE stream that tails system.log in real time (text/event-stream)."""
    log_file = Path(LOG_DIR) / "system.log"

    async def _tail():
        try:
            with open(log_file, "r", encoding="utf-8") as fh:
                fh.seek(0, 2)  # jump to end
                while True:
                    line = fh.readline()
                    if line:
                        yield f"data: {line.rstrip()}\n\n"
                    else:
                        await asyncio.sleep(0.25)
        except FileNotFoundError:
            yield "data: [log file not found]\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        _tail(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/logs/access/stream")
async def stream_access_log():
    """SSE stream that pushes new structured access-log entries as JSON."""
    q = access_log.subscribe()

    async def _push():
        import json

        try:
            while True:
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # prevent proxy timeouts
        except asyncio.CancelledError:
            pass
        finally:
            access_log.unsubscribe(q)

    return StreamingResponse(
        _push(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# Generate directory structure and serve files
@router.post("/generate-directory-structure/text")
async def generate_directory_structure(
    root_directory: str,
    project_root: str,
    common_configs: Optional[dict] = None,
):
    logger.info(
        f"Generate directory structure called with root: {root_directory}, project: {project_root}"
    )
    try:
        directory_structure, output_file, text_output_file = (
            get_structure_with_common_configs(
                root_directory, project_root, common_configs
            )
        )
        logger.info(f"Files generated: {output_file}, {text_output_file}")
        return FileResponse(
            path=text_output_file,
            media_type="text/plain",
            filename="directory_structure.txt",
        )
    except Exception as e:
        logger.error(f"Error generating directory structure: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Error generating directory structure: {str(e)}"
        )


@router.post("/generate-directory-structure/json")
async def generate_directory_structure_json(
    root_directory: str,
    project_root: str,
    common_configs: Optional[dict] = None,
):
    logger.info(
        f"Generate directory structure called with root: {root_directory}, project: {project_root}"
    )
    try:
        directory_structure, output_file, text_output_file = (
            get_structure_with_common_configs(
                root_directory, project_root, common_configs
            )
        )
        logger.info(f"Files generated: {output_file}, {text_output_file}")

        return JSONResponse(
            content=directory_structure,
            media_type="application/json",
        )
    except Exception as e:
        logger.error(f"Error generating directory structure: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Error generating directory structure: {str(e)}"
        )


@router.post("/generate-directory-structure/zip")
async def generate_directory_structure(
    root_directory: str,
    project_root: str,
    common_configs: Optional[dict] = None,
):
    try:
        directory_structure, output_file, text_output_file = (
            get_structure_with_common_configs(
                root_directory, project_root, common_configs
            )
        )

        # Create a zip file in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Add both files to the zip
            zip_file.write(output_file, "directory_structure.json")
            zip_file.write(text_output_file, "directory_structure.txt")

        # Seek to start of the buffer
        zip_buffer.seek(0)

        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=directory_structure.zip"
            },
        )
    except Exception as e:
        logger.error(f"Error generating directory structure: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Error generating directory structure: {str(e)}"
        )


# ── Push Notifications ──────────────────────────────────────────────────────


class NotifyRequest(BaseModel):
    title: str = Field(description="Notification title.")
    message: str = Field(description="Notification body text.")
    timeout: int = Field(default=10, ge=1, le=60, description="OS popup display seconds.")
    level: str = Field(default="info", description="Severity: info | success | warning | error")


@router.post("/notify")
async def push_notification(body: NotifyRequest):
    """
    Push a notification to the user from the cloud AI or any external caller.
    Fires the native OS popup AND broadcasts a 'notification' WebSocket event
    to every connected UI client.
    """
    from app.tools.tools.notify import send_notification
    result = await send_notification(
        title=body.title,
        message=body.message,
        timeout=body.timeout,
        level=body.level,
        broadcast=True,
    )
    return {
        "status": "ok",
        "output": result.output,
        "os_fired": result.metadata.get("os_fired", False),
    }
