"""First-run setup status & installation endpoints.

Provides:
- GET  /setup/status                 → comprehensive check of what is installed / configured
- POST /setup/install                → SSE stream that installs missing components with live progress
- POST /setup/install-transcription  → Download a GGML whisper model (optional)
- GET  /setup/logs                   → SSE stream of the live system.log (tail -f style)
- GET  /setup/debug                  → Full diagnostic snapshot of the system state
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Captured at import (= engine boot). /setup/logs uses this to bound its
# history replay to the current run — system.log persists across runs.
_PROCESS_START = time.time()

from app.common.platform_ctx import PLATFORM

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.common.system_logger import get_logger
from app.config import MATRX_HOME_DIR, LOG_DIR

logger = get_logger()
router = APIRouter(prefix="/setup", tags=["setup"])


# ---------------------------------------------------------------------------
# Status models
# ---------------------------------------------------------------------------

class ComponentStatus(BaseModel):
    id: str
    label: str
    description: str
    # "ready" | "not_ready" | "installing" | "error" | "skipped" | "warning"
    status: str
    detail: str | None = None
    optional: bool = False
    size_hint: str | None = None  # e.g. "~280 MB"
    deep_link: str | None = None  # macOS Settings URL or "x-apple.systempreferences:…"


class SetupStatus(BaseModel):
    setup_complete: bool
    components: list[ComponentStatus]
    platform: str
    architecture: str
    gpu_available: bool
    gpu_name: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Components that must be "ready" for setup_complete=True.
# Permissions are advisory-only (can't be fixed by a subprocess on macOS TCC).
# Transcription and LLM are optional enhancements.
_BLOCKING_COMPONENTS = {"core_packages", "browser_engine", "storage_dirs", "tts_model", "cloudflared"}


def _browsers_path() -> str:
    return os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(MATRX_HOME_DIR / "playwright-browsers"),
    )


def _check_playwright_package() -> bool:
    """Return True if the playwright Python package is importable."""
    try:
        __import__("playwright")
        return True
    except ImportError:
        return False


def _check_playwright_browsers() -> ComponentStatus:
    """Check that BOTH the Playwright Python package AND Chromium binary are present."""
    has_package = _check_playwright_package()

    browsers_path = _browsers_path()
    markers = ("chromium-", "chromium_headless_shell-")
    browser_found = False
    version = None

    if os.path.isdir(browsers_path):
        for entry in os.listdir(browsers_path):
            if any(entry.startswith(m) for m in markers):
                browser_found = True
                parts = entry.split("-", 1)
                if len(parts) == 2:
                    version = parts[1]
                break

    if has_package and browser_found:
        detail = f"Chromium {version}" if version else "Chromium installed"
        return ComponentStatus(
            id="browser_engine",
            label="Browser Engine",
            description="Chromium browser for web automation, scraping, and remote browser control",
            status="ready",
            detail=detail,
        )

    if not has_package and not browser_found:
        detail = "Playwright package and Chromium not found — will be installed automatically"
    elif not has_package:
        detail = "Playwright package not installed — will be installed automatically"
    else:
        detail = "Chromium binary not found — will be downloaded automatically"

    return ComponentStatus(
        id="browser_engine",
        label="Browser Engine",
        description="Chromium browser for web automation, scraping, and remote browser control",
        status="not_ready",
        detail=detail,
        size_hint="~280 MB",
    )


def _check_storage_directories() -> ComponentStatus:
    """Check if core storage directories exist."""
    from app.config import (
        MATRX_NOTES_DIR, MATRX_FILES_DIR, MATRX_CODE_DIR,
        MATRX_DATA_DIR, MATRX_WORKSPACES_DIR,
    )

    dirs = {
        "Notes": MATRX_NOTES_DIR,
        "Files": MATRX_FILES_DIR,
        "Code": MATRX_CODE_DIR,
        "Data": MATRX_DATA_DIR,
        "Workspaces": MATRX_WORKSPACES_DIR,
    }
    missing = [name for name, path in dirs.items() if not os.path.isdir(str(path))]

    if not missing:
        return ComponentStatus(
            id="storage_dirs",
            label="Storage Directories",
            description="Local folders for notes, files, code, and workspace data",
            status="ready",
            detail=f"All {len(dirs)} directories ready",
        )
    return ComponentStatus(
        id="storage_dirs",
        label="Storage Directories",
        description="Local folders for notes, files, code, and workspace data",
        status="not_ready",
        detail=f"{len(missing)} directories will be created: {', '.join(missing)}",
    )


def _check_core_packages() -> ComponentStatus:
    """Verify core Python packages required by the engine are importable."""
    required = [
        ("playwright", "Browser automation"),
        ("psutil", "System monitoring"),
        ("zeroconf", "Network discovery"),
        ("sounddevice", "Audio I/O"),
        ("numpy", "Numeric computing"),
    ]
    missing = []
    for mod, label in required:
        try:
            __import__(mod)
        except ImportError:
            missing.append(label)

    if not missing:
        return ComponentStatus(
            id="core_packages",
            label="Core Engine Packages",
            description="Essential libraries for system monitoring, audio, and network features",
            status="ready",
            detail="All core packages verified",
        )
    return ComponentStatus(
        id="core_packages",
        label="Core Engine Packages",
        description="Essential libraries for system monitoring, audio, and network features",
        status="error",
        detail=f"Missing: {', '.join(missing)}. Engine may need reinstallation.",
    )


def _check_gpu() -> tuple[bool, str | None]:
    """Detect GPU availability (for transcription model recommendations).

    Delegates to the hardware detector which handles NVIDIA (via nvidia-smi),
    Apple Silicon (Metal), Vulkan, ROCm, and WSL passthrough.
    """
    from app.services.hardware.detector import _detect_gpus

    try:
        gpus = _detect_gpus()
        for gpu in gpus:
            name = gpu.get("name", "")
            backend = gpu.get("backend", "")
            if name and name != "No GPU detected" and backend != "cpu":
                return True, name
    except Exception:
        pass

    return False, None


def _check_tts() -> ComponentStatus:
    """Check if the Kokoro TTS model files are present and plausibly complete."""
    from app.services.tts.models import (
        ONNX_MODEL_FILENAME, VOICES_BIN_FILENAME,
        ONNX_MODEL_SIZE_BYTES, VOICES_BIN_SIZE_BYTES,
    )

    tts_dir = MATRX_HOME_DIR / "tts"
    model_path = tts_dir / ONNX_MODEL_FILENAME
    voices_path = tts_dir / VOICES_BIN_FILENAME
    model_ok = model_path.is_file() and model_path.stat().st_size >= ONNX_MODEL_SIZE_BYTES * 0.99
    voices_ok = voices_path.is_file() and voices_path.stat().st_size >= VOICES_BIN_SIZE_BYTES * 0.99

    if model_ok and voices_ok:
        return ComponentStatus(
            id="tts_model",
            label="Text-to-Speech",
            description="Kokoro neural TTS for natural-sounding speech output",
            status="ready",
            detail="Kokoro v1.0 model and voices ready",
        )

    missing = []
    if not model_ok:
        missing.append("ONNX model (~310 MB)")
    if not voices_ok:
        missing.append("voice pack (~27 MB)")

    return ComponentStatus(
        id="tts_model",
        label="Text-to-Speech",
        description="Kokoro neural TTS for natural-sounding speech output",
        status="not_ready",
        detail=f"Missing: {', '.join(missing)} — will be downloaded automatically",
        size_hint="~337 MB",
    )


def _check_cloudflared() -> ComponentStatus:
    """Check if the cloudflared binary is available."""
    from app.services.tunnel.manager import _find_preinstalled_cloudflared

    found = _find_preinstalled_cloudflared()
    if found:
        return ComponentStatus(
            id="cloudflared",
            label="Remote Access",
            description="Cloudflare Tunnel for secure remote device connections",
            status="ready",
            detail=f"Binary: {found.name}",
        )

    return ComponentStatus(
        id="cloudflared",
        label="Remote Access",
        description="Cloudflare Tunnel for secure remote device connections",
        status="not_ready",
        detail="cloudflared binary not found — will be downloaded automatically (~30 MB)",
        size_hint="~30 MB",
    )


def _check_transcription() -> ComponentStatus:
    """Check if a GGML model is available in the shared models directory.

    Note: whisper-cpp-plus is a Rust/Tauri binding — there is no PATH binary
    to check.  The Tauri side (get_voice_setup_status) is the authoritative
    source for whether the transcription engine itself is ready.  Here we only
    report whether a model file has been downloaded to the shared location so
    the Python setup route can show a meaningful status.
    """
    model_dir = os.path.join(str(MATRX_HOME_DIR), "models")
    model_found = False
    model_name = None

    min_model_bytes = 10_000_000  # 10 MB — any real GGML model is larger
    if os.path.isdir(model_dir):
        for f in os.listdir(model_dir):
            if f.startswith("ggml-") and f.endswith(".bin") and "silero" not in f:
                fpath = os.path.join(model_dir, f)
                if os.path.getsize(fpath) >= min_model_bytes:
                    model_found = True
                    model_name = f
                    break

    if model_found:
        return ComponentStatus(
            id="transcription",
            label="Audio Transcription",
            description="Local speech-to-text using whisper-cpp with GPU acceleration",
            status="ready",
            detail=f"Model: {model_name}",
            optional=True,
        )

    return ComponentStatus(
        id="transcription",
        label="Audio Transcription",
        description="Local speech-to-text using whisper-cpp with GPU acceleration",
        status="not_ready",
        detail="No GGML model found — click Install to download",
        optional=True,
        size_hint="~150 MB (base.en model)",
    )


async def _check_permissions() -> ComponentStatus:
    """Check OS-level permissions — advisory only, never blocks setup_complete.

    Logs the exact check method and raw value returned for every permission on
    every platform so failures are always diagnosable.
    """
    system = PLATFORM["system"]
    machine = PLATFORM["machine"]
    PRIVACY_DEEP_LINK = (
        "x-apple.systempreferences:com.apple.preference.security?Privacy"
    )

    logger.info("[permissions] Platform: %s %s", system, machine)

    if system == "Darwin":
        return await _check_permissions_macos(PRIVACY_DEEP_LINK)
    elif system == "Windows":
        return await _check_permissions_windows()
    else:
        return await _check_permissions_linux()


async def _check_permissions_macos(deep_link: str) -> ComponentStatus:
    """macOS: probe TCC.db and CGPreflightScreenCaptureAccess — read-only, never triggers a dialog."""
    import asyncio as _asyncio
    from app.services.permissions.checker import (
        _tcc_db_status,
        _macos_screen_recording_status,
        PermissionStatus,
    )

    loop = _asyncio.get_event_loop()
    results: dict[str, PermissionStatus] = {}

    # Microphone and Camera: read from TCC database
    for label, service in (
        ("Microphone", "kTCCServiceMicrophone"),
        ("Camera", "kTCCServiceCamera"),
    ):
        try:
            status = await loop.run_in_executor(None, _tcc_db_status, service)
            results[label] = status
            logger.info(
                "[permissions] macOS TCC check — service=%s label=%s → %s",
                service, label, status.value,
            )
        except Exception as exc:
            results[label] = PermissionStatus.UNKNOWN
            logger.warning(
                "[permissions] macOS TCC check FAILED — service=%s label=%s → error: %s",
                service, label, exc,
            )

    # Screen Recording: use CGPreflightScreenCaptureAccess (more reliable than TCC.db for this service)
    try:
        sr_status = await loop.run_in_executor(None, _macos_screen_recording_status)
        results["Screen Recording"] = sr_status
        logger.info(
            "[permissions] macOS screen recording check → %s",
            sr_status.value,
        )
    except Exception as exc:
        results["Screen Recording"] = PermissionStatus.UNKNOWN
        logger.warning("[permissions] macOS screen recording check FAILED — error: %s", exc)

    # DENIED and UNKNOWN are both "not ready" — NOT_DETERMINED no longer exists for screen recording
    not_granted = [name for name, s in results.items() if s not in (PermissionStatus.GRANTED,)]
    detail_parts = [f"{name}={s.value}" for name, s in results.items()]
    logger.info("[permissions] macOS summary — %s", ", ".join(detail_parts))

    if not not_granted:
        return ComponentStatus(
            id="permissions",
            label="Device Permissions",
            description="OS-level access for microphone, camera, and screen recording",
            status="ready",
            detail="All granted — " + ", ".join(detail_parts),
        )

    return ComponentStatus(
        id="permissions",
        label="Device Permissions",
        description="OS-level access for microphone, camera, and screen recording",
        status="warning",
        detail=f"Not granted: {', '.join(not_granted)} | All values: {', '.join(detail_parts)} — click Review & Grant",
        deep_link=deep_link,
    )


async def _check_permissions_windows() -> ComponentStatus:
    """Windows: check microphone and camera via winreg (direct, no subprocess)."""
    import asyncio as _asyncio

    _WIN_CONSENT_HKCU = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
    )

    def _read_consent(capability_key: str) -> str:
        """Read the Windows ConsentStore Value for a capability.

        Returns "Allow", "Deny", or "unknown" (missing key = not yet configured,
        treated as Allow on modern Windows which defaults to allowing user-level
        apps).  Never raises.
        """
        try:
            import winreg
            full_path = f"{_WIN_CONSENT_HKCU}\\{capability_key}"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, full_path) as k:
                val, _ = winreg.QueryValueEx(k, "Value")
                return str(val)
        except FileNotFoundError:
            # Key absent = system has not written a consent decision yet → treat as Allow
            return "Allow"
        except OSError:
            return "unknown"
        except Exception:
            return "unknown"

    # Capability sub-keys for mic and camera in ConsentStore
    checks_def = {
        "Microphone": "microphone",
        "Camera": "webcam",
    }

    loop = _asyncio.get_event_loop()
    results: dict[str, str] = {}
    for label, cap_key in checks_def.items():
        val = await loop.run_in_executor(None, _read_consent, cap_key)
        results[label] = val
        logger.debug(
            "[permissions] Windows ConsentStore — %s (%s) → %r",
            label, cap_key, val,
        )

    # Screen recording: always available on Windows
    results["Screen Recording"] = "Allow"
    logger.info("[permissions] Windows: Screen Recording → always allowed")

    detail_parts = [f"{name}={val}" for name, val in results.items()]
    logger.info("[permissions] Windows summary — %s", ", ".join(detail_parts))

    not_allowed = [name for name, val in results.items() if val.lower() not in ("allow", "unknown")]
    if not_allowed:
        return ComponentStatus(
            id="permissions",
            label="Device Permissions",
            description="OS-level access for microphone and camera",
            status="warning",
            detail=f"Restricted: {', '.join(not_allowed)} | All values: {', '.join(detail_parts)} — check Settings > Privacy",
        )

    return ComponentStatus(
        id="permissions",
        label="Device Permissions",
        description="OS-level access for microphone and camera",
        status="ready",
        detail="All allowed — " + ", ".join(detail_parts),
    )


async def _check_permissions_linux() -> ComponentStatus:
    """Linux: check device node access and audio group membership."""
    import grp
    import os as _os
    import stat as _stat

    checks: dict[str, str] = {}

    # Microphone: check /dev/snd/* device nodes are accessible
    snd_devices = []
    try:
        import glob as _glob
        snd_devices = _glob.glob("/dev/snd/pcmC*D*c")  # capture devices
        readable = [d for d in snd_devices if _os.access(d, _os.R_OK)]
        val = f"{len(readable)}/{len(snd_devices)} capture nodes readable"
        checks["Microphone"] = val
        logger.info("[permissions] Linux mic check — /dev/snd/pcmC*D*c → %s", val)
    except Exception as exc:
        checks["Microphone"] = f"error: {exc}"
        logger.warning("[permissions] Linux mic check FAILED: %s", exc)

    # Audio group membership
    try:
        uid = _os.getuid()
        import pwd
        username = pwd.getpwuid(uid).pw_name
        audio_group = grp.getgrnam("audio")
        in_audio = username in audio_group.gr_mem or _os.getgid() == audio_group.gr_gid
        val = f"user={username} in_audio_group={in_audio}"
        checks["Audio Group"] = val
        logger.info("[permissions] Linux audio group check → %s", val)
    except Exception as exc:
        checks["Audio Group"] = f"error: {exc}"
        logger.warning("[permissions] Linux audio group check FAILED: %s", exc)

    # Camera: check /dev/video* nodes
    try:
        import glob as _glob
        video_devices = _glob.glob("/dev/video*")
        readable = [d for d in video_devices if _os.access(d, _os.R_OK)]
        val = f"{len(readable)}/{len(video_devices)} video nodes readable"
        checks["Camera"] = val
        logger.info("[permissions] Linux camera check — /dev/video* → %s", val)
    except Exception as exc:
        checks["Camera"] = f"error: {exc}"
        logger.warning("[permissions] Linux camera check FAILED: %s", exc)

    # Screen recording: check for X11 display or Wayland socket
    try:
        display = _os.environ.get("DISPLAY", "")
        wayland = _os.environ.get("WAYLAND_DISPLAY", "")
        val = f"DISPLAY={display or '(not set)'} WAYLAND_DISPLAY={wayland or '(not set)'}"
        checks["Screen Recording"] = val
        logger.info("[permissions] Linux display check → %s", val)
    except Exception as exc:
        checks["Screen Recording"] = f"error: {exc}"

    detail_parts = [f"{name}={val}" for name, val in checks.items()]
    logger.info("[permissions] Linux summary — %s", ", ".join(detail_parts))

    has_issue = not snd_devices  # only flag if no audio devices at all
    if has_issue:
        return ComponentStatus(
            id="permissions",
            label="Device Permissions",
            description="OS-level access for audio, camera, and display",
            status="warning",
            detail="No audio capture devices found | " + ", ".join(detail_parts),
        )

    return ComponentStatus(
        id="permissions",
        label="Device Permissions",
        description="OS-level access for audio, camera, and display",
        status="ready",
        detail=", ".join(detail_parts),
    )


# ---------------------------------------------------------------------------
# GET /setup/status
# ---------------------------------------------------------------------------

@router.get("/status", response_model=SetupStatus)
async def get_setup_status() -> SetupStatus:
    """Return comprehensive installation/setup status."""
    gpu_available, gpu_name = _check_gpu()

    components = [
        _check_core_packages(),
        _check_playwright_browsers(),
        _check_storage_directories(),
        _check_tts(),
        _check_cloudflared(),
        await _check_permissions(),
        _check_transcription(),
    ]

    # Only blocking components determine setup_complete
    setup_complete = all(
        c.status == "ready"
        for c in components
        if c.id in _BLOCKING_COMPONENTS
    )

    return SetupStatus(
        setup_complete=setup_complete,
        components=components,
        platform=PLATFORM["system"],
        architecture=PLATFORM["machine"],
        gpu_available=gpu_available,
        gpu_name=gpu_name,
    )


# ---------------------------------------------------------------------------
# POST /setup/install — SSE stream with real-time progress
# ---------------------------------------------------------------------------

async def _sse_event(event: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Event."""
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def _build_playwright_cmd() -> list[str]:
    """Return the command list to run `playwright install chromium`.

    compute_driver_executable() returns a (node_binary, cli.js) tuple.
    str()-ing it produces a broken path — we unpack it explicitly.
    Fall back to `python -m playwright install` when the driver binary is
    not available (e.g. inside a frozen PyInstaller binary).
    """
    try:
        from playwright._impl._driver import compute_driver_executable  # type: ignore[import]
        node_exe, cli_js = compute_driver_executable()
        if not os.path.isfile(node_exe):
            raise FileNotFoundError(f"Playwright node binary not found: {node_exe}")
        return [node_exe, cli_js, "install", "chromium"]
    except Exception as exc:
        logger.info(
            "[setup_routes] Playwright driver binary unavailable (%s) — "
            "using `python -m playwright install` fallback",
            exc,
        )
        return [sys.executable, "-m", "playwright", "install", "chromium"]


async def _install_playwright_browsers(browsers_path: str):
    """Install Playwright Python package (if missing) then Chromium binary, yielding SSE events."""
    os.makedirs(browsers_path, exist_ok=True)

    yield await _sse_event("progress", {
        "component": "browser_engine",
        "status": "installing",
        "message": "Preparing browser engine installation...",
        "percent": 0,
    })

    # Step 1: Install the playwright Python package if it isn't importable yet.
    if not _check_playwright_package():
        yield await _sse_event("progress", {
            "component": "browser_engine",
            "status": "installing",
            "message": "Installing Playwright Python package...",
            "percent": 5,
        })
        try:
            pkg_proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "playwright",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            pkg_out, _ = await pkg_proc.communicate()
            if pkg_proc.returncode != 0:
                out_text = pkg_out.decode("utf-8", errors="replace").strip() if pkg_out else "(no output)"
                yield await _sse_event("progress", {
                    "component": "browser_engine",
                    "status": "error",
                    "message": f"Failed to install Playwright package: {out_text[-500:]}",
                    "percent": 0,
                })
                return
        except Exception as e:
            yield await _sse_event("progress", {
                "component": "browser_engine",
                "status": "error",
                "message": f"Could not run pip install playwright: {e}",
                "percent": 0,
            })
            return

        yield await _sse_event("progress", {
            "component": "browser_engine",
            "status": "installing",
            "message": "Playwright package installed — downloading Chromium...",
            "percent": 15,
        })

    try:
        cmd = _build_playwright_cmd()
    except Exception as e:
        yield await _sse_event("progress", {
            "component": "browser_engine",
            "status": "error",
            "message": f"Could not locate Playwright installer: {e}",
            "percent": 0,
        })
        return

    yield await _sse_event("progress", {
        "component": "browser_engine",
        "status": "installing",
        "message": f"Running: {' '.join(cmd[:3])} ... install chromium",
        "percent": 20,
    })

    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": browsers_path}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        yield await _sse_event("progress", {
            "component": "browser_engine",
            "status": "error",
            "message": f"Failed to launch installer: {e}",
            "percent": 0,
        })
        return

    import re as _re

    output_lines: list[str] = []
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                output_lines.append(text)
                percent = 25
                if "downloading" in text.lower():
                    percent = 40
                if "%" in text:
                    m = _re.search(r"(\d+)%", text)
                    if m:
                        percent = min(int(m.group(1)), 95)
                if "extracting" in text.lower() or "unpack" in text.lower():
                    percent = 80
                yield await _sse_event("progress", {
                    "component": "browser_engine",
                    "status": "installing",
                    "message": text,
                    "percent": percent,
                })
    except (asyncio.CancelledError, GeneratorExit):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return

    await proc.wait()

    if proc.returncode == 0:
        yield await _sse_event("progress", {
            "component": "browser_engine",
            "status": "ready",
            "message": "Chromium installed successfully",
            "percent": 100,
        })
    else:
        last_lines = "\n".join(output_lines[-5:]) if output_lines else "(no output)"
        yield await _sse_event("progress", {
            "component": "browser_engine",
            "status": "error",
            "message": f"Installation failed (exit {proc.returncode}). Last output: {last_lines}",
            "percent": 0,
        })


async def _create_storage_directories():
    """Create missing storage directories, yielding SSE progress events."""
    from app.config import (
        MATRX_NOTES_DIR, MATRX_FILES_DIR, MATRX_CODE_DIR,
        MATRX_DATA_DIR, MATRX_WORKSPACES_DIR,
    )

    dirs = {
        "Notes": MATRX_NOTES_DIR,
        "Files": MATRX_FILES_DIR,
        "Code": MATRX_CODE_DIR,
        "Data": MATRX_DATA_DIR,
        "Workspaces": MATRX_WORKSPACES_DIR,
    }

    yield await _sse_event("progress", {
        "component": "storage_dirs",
        "status": "installing",
        "message": "Creating storage directories...",
        "percent": 0,
    })

    total = len(dirs)
    for idx, (name, dir_path) in enumerate(dirs.items()):
        path = Path(str(dir_path))
        if not path.is_dir():
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                yield await _sse_event("progress", {
                    "component": "storage_dirs",
                    "status": "error",
                    "message": f"Failed to create {name}: {e}",
                    "percent": int(((idx + 1) / total) * 100),
                })
                continue
        pct = int(((idx + 1) / total) * 100)
        yield await _sse_event("progress", {
            "component": "storage_dirs",
            "status": "installing",
            "message": f"{name} directory ready",
            "percent": min(pct, 100),
        })

    yield await _sse_event("progress", {
        "component": "storage_dirs",
        "status": "ready",
        "message": f"All {total} directories ready",
        "percent": 100,
    })


def _emit_total(total_percent: int, message: str):
    """Build a total_progress SSE event (non-blocking progress update for the grand bar)."""
    return _sse_event("total_progress", {
        "total_percent": min(max(total_percent, 0), 100),
        "message": message,
    })


async def _download_transcription_model(model: str, request: Request):
    """Download a GGML whisper model, yielding SSE events (reused by first_run stream)."""
    model_dir = os.path.join(str(MATRX_HOME_DIR), "models")
    os.makedirs(model_dir, exist_ok=True)
    model_file = f"ggml-{model}.bin"
    model_path = os.path.join(model_dir, model_file)
    _min_model_bytes = 10_000_000  # 10 MB — any real GGML model is larger

    stale_tmp = model_path + ".tmp"
    if os.path.exists(stale_tmp):
        os.remove(stale_tmp)

    if os.path.isfile(model_path) and os.path.getsize(model_path) >= _min_model_bytes:
        yield await _sse_event("progress", {
            "component": "transcription",
            "status": "ready",
            "message": f"Model {model_file} already downloaded",
            "percent": 100,
        })
        return

    yield await _sse_event("progress", {
        "component": "transcription",
        "status": "installing",
        "message": f"Downloading {model_file} (~150 MB) from HuggingFace...",
        "percent": 2,
    })

    model_url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{model_file}"

    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
            async with client.stream("GET", model_url) as resp:
                if resp.status_code != 200:
                    yield await _sse_event("progress", {
                        "component": "transcription",
                        "status": "error",
                        "message": f"Download failed: HTTP {resp.status_code}",
                        "percent": 0,
                    })
                    return

                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                tmp_path = model_path + ".tmp"

                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        if await request.is_disconnected():
                            f.close()
                            if os.path.exists(tmp_path):
                                os.remove(tmp_path)
                            yield await _sse_event("cancelled", {"message": "Download cancelled by user"})
                            return
                        f.write(chunk)
                        downloaded += len(chunk)
                        pct = int((downloaded / total * 90) + 5) if total > 0 else 50
                        if downloaded % (256 * 1024) < len(chunk):
                            mb_done = downloaded / (1024 * 1024)
                            mb_total = total / (1024 * 1024) if total > 0 else 0
                            yield await _sse_event("progress", {
                                "component": "transcription",
                                "status": "installing",
                                "message": f"Downloading {model_file}: {mb_done:.1f} / {mb_total:.0f} MB",
                                "percent": min(pct, 99),
                                "bytes_downloaded": downloaded,
                                "total_bytes": total,
                            })

                Path(tmp_path).replace(model_path)

        yield await _sse_event("progress", {
            "component": "transcription",
            "status": "ready",
            "message": f"Model {model_file} installed successfully",
            "percent": 100,
        })

    except Exception as e:
        tmp_cleanup = model_path + ".tmp"
        if os.path.exists(tmp_cleanup):
            os.remove(tmp_cleanup)
        logger.error(f"Transcription model download failed: {e}", exc_info=True)
        yield await _sse_event("progress", {
            "component": "transcription",
            "status": "error",
            "message": f"Download failed: {str(e)}",
            "percent": 0,
        })


async def _download_tts_model(request: Request):
    """Download the Kokoro TTS ONNX model and voices pack, yielding SSE events."""
    from app.services.tts.models import (
        ONNX_MODEL_FILENAME, VOICES_BIN_FILENAME,
        ONNX_MODEL_URL, VOICES_BIN_URL,
        ONNX_MODEL_SIZE_BYTES, VOICES_BIN_SIZE_BYTES,
    )

    tts_dir = MATRX_HOME_DIR / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)

    for stale in tts_dir.glob("*.tmp"):
        stale.unlink(missing_ok=True)

    files = [
        (ONNX_MODEL_URL, ONNX_MODEL_FILENAME, ONNX_MODEL_SIZE_BYTES, "Kokoro TTS model"),
        (VOICES_BIN_URL, VOICES_BIN_FILENAME, VOICES_BIN_SIZE_BYTES, "TTS voice pack"),
    ]

    total_bytes = sum(s for _, _, s, _ in files)
    overall_downloaded = 0

    for url, filename, expected_size, label in files:
        dest = tts_dir / filename
        if dest.is_file() and dest.stat().st_size >= expected_size * 0.99:
            overall_downloaded += expected_size
            pct = int((overall_downloaded / total_bytes) * 100)
            yield await _sse_event("progress", {
                "component": "tts_model",
                "status": "installing",
                "message": f"{label} already downloaded",
                "percent": pct,
            })
            continue

        yield await _sse_event("progress", {
            "component": "tts_model",
            "status": "installing",
            "message": f"Downloading {label}...",
            "percent": int((overall_downloaded / total_bytes) * 100),
        })

        try:
            import httpx
            async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        yield await _sse_event("progress", {
                            "component": "tts_model",
                            "status": "error",
                            "message": f"Download failed for {label}: HTTP {resp.status_code}",
                            "percent": 0,
                        })
                        return

                    file_total = int(resp.headers.get("content-length", expected_size))
                    file_downloaded = 0
                    tmp_path = dest.with_suffix(".tmp")

                    with open(tmp_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            if await request.is_disconnected():
                                tmp_path.unlink(missing_ok=True)
                                yield await _sse_event("cancelled", {"message": "Download cancelled"})
                                return
                            f.write(chunk)
                            file_downloaded += len(chunk)
                            combined = overall_downloaded + file_downloaded
                            pct = int((combined / total_bytes) * 100)
                            if file_downloaded % (256 * 1024) < len(chunk):
                                mb = combined / (1024 * 1024)
                                total_mb = total_bytes / (1024 * 1024)
                                yield await _sse_event("progress", {
                                    "component": "tts_model",
                                    "status": "installing",
                                    "message": f"Downloading {label}: {mb:.0f} / {total_mb:.0f} MB",
                                    "percent": min(pct, 99),
                                })

            # Outside the stream context — file handle is closed, safe to rename atomically.
            # Path.replace() overwrites an existing dest on both Windows and POSIX.
            tmp_path.replace(dest)
            overall_downloaded += file_downloaded

        except Exception as e:
            dest.with_suffix(".tmp").unlink(missing_ok=True)
            logger.error("TTS model download failed: %s", e, exc_info=True)
            yield await _sse_event("progress", {
                "component": "tts_model",
                "status": "error",
                "message": f"Download failed for {label}: {e}",
                "percent": 0,
            })
            return

    yield await _sse_event("progress", {
        "component": "tts_model",
        "status": "ready",
        "message": "Kokoro TTS model and voices installed",
        "percent": 100,
    })


async def _download_cloudflared(request: Request):
    """Download the cloudflared binary if not already present, yielding SSE events."""
    from app.services.tunnel.manager import (
        _find_preinstalled_cloudflared, _bin_path, _get_download_url, _finalize_binary,
    )

    existing = _find_preinstalled_cloudflared()
    if existing:
        yield await _sse_event("progress", {
            "component": "cloudflared",
            "status": "ready",
            "message": f"cloudflared binary found: {existing.name}",
            "percent": 100,
        })
        return

    yield await _sse_event("progress", {
        "component": "cloudflared",
        "status": "installing",
        "message": "Downloading cloudflared for remote access...",
        "percent": 5,
    })

    dest = _bin_path()
    try:
        url = _get_download_url()
    except RuntimeError as e:
        yield await _sse_event("progress", {
            "component": "cloudflared",
            "status": "error",
            "message": str(e),
            "percent": 0,
        })
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".tmp")
    tmp_path.unlink(missing_ok=True)

    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    yield await _sse_event("progress", {
                        "component": "cloudflared",
                        "status": "error",
                        "message": f"Download failed: HTTP {resp.status_code}",
                        "percent": 0,
                    })
                    return

                file_total = int(resp.headers.get("content-length", 0))
                downloaded = 0

                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        if await request.is_disconnected():
                            tmp_path.unlink(missing_ok=True)
                            yield await _sse_event("cancelled", {"message": "Download cancelled"})
                            return
                        f.write(chunk)
                        downloaded += len(chunk)
                        pct = int((downloaded / file_total * 90) + 5) if file_total > 0 else 50
                        if downloaded % (256 * 1024) < len(chunk):
                            mb = downloaded / (1024 * 1024)
                            yield await _sse_event("progress", {
                                "component": "cloudflared",
                                "status": "installing",
                                "message": f"Downloading cloudflared: {mb:.1f} MB",
                                "percent": min(pct, 99),
                            })

        # Outside both async-with contexts — file handle closed, safe to finalize.
        # _finalize_binary uses Path.replace() (atomic on Windows + POSIX) and chmod.
        _finalize_binary(tmp_path, dest)

    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        logger.error("cloudflared download failed: %s", e, exc_info=True)
        yield await _sse_event("progress", {
            "component": "cloudflared",
            "status": "error",
            "message": f"Download failed: {e}",
            "percent": 0,
        })
        return

    yield await _sse_event("progress", {
        "component": "cloudflared",
        "status": "ready",
        "message": "cloudflared installed for remote access",
        "percent": 100,
    })


async def _install_stream(request: Request, first_run: bool = False):
    """Generator that orchestrates all installation steps and yields SSE events.

    Always installs all core components: storage dirs, core packages, Playwright
    Chromium, TTS model, cloudflared, and permissions check.

    first_run=True additionally downloads the transcription model so everything
    is ready in one pass on first launch.

    Guarantees: always emits a 'complete' event at the end (even if some
    components failed), so the frontend never sees 'stream ended unexpectedly'.

    Grand progress weighting (first_run=True):
      storage_dirs      ~2%     →  0–2
      core_packages     ~1%     →  2–3
      browser_engine   ~40%     →  3–43
      tts_model        ~25%     →  43–68
      cloudflared       ~2%     →  68–70
      transcription    ~25%     →  70–95
      permissions       ~5%     →  95–100

    Standard (first_run=False):
      storage_dirs      ~3%     →  0–3
      core_packages     ~2%     →  3–5
      browser_engine   ~50%     →  5–55
      tts_model        ~35%     →  55–90
      cloudflared       ~3%     →  90–93
      permissions       ~7%     →  93–100
    """
    had_error = False
    error_summary: list[str] = []

    try:
        yield await _sse_event("started", {
            "component": "_system",
            "message": "Starting setup...",
            "timestamp": time.time(),
            "total_percent": 0,
            "first_run": first_run,
        })

        # ── Step 1: Storage directories (fast) ────────────────────────────────
        yield await _emit_total(0, "Creating storage directories...")
        async for event in _create_storage_directories():
            if await request.is_disconnected():
                yield await _sse_event("cancelled", {"message": "Setup cancelled by user"})
                return
            yield event
        step1_end = 2 if first_run else 3
        yield await _emit_total(step1_end, "Storage directories ready")

        # ── Step 2: Core package verification (fast) ──────────────────────────
        status = _check_core_packages()
        yield await _sse_event("progress", {
            "component": "core_packages",
            "status": status.status,
            "message": status.detail or "Core packages verified",
            "percent": 100 if status.status == "ready" else 0,
        })
        step2_end = 3 if first_run else 5
        yield await _emit_total(step2_end, "Core packages verified")
        if status.status != "ready":
            had_error = True
            error_summary.append(f"core_packages: {status.detail}")

        # ── Step 3: Playwright browsers ───────────────────────────────────────
        browser_base = step2_end
        browser_span = 40 if first_run else 50
        browser_status = _check_playwright_browsers()
        if browser_status.status != "ready":
            yield await _emit_total(browser_base, "Installing browser engine (~280 MB)...")
            async for event in _install_playwright_browsers(_browsers_path()):
                if await request.is_disconnected():
                    yield await _sse_event("cancelled", {"message": "Setup cancelled by user"})
                    return
                yield event
                try:
                    parsed = json.loads(event.split("data: ", 1)[1].split("\n")[0])
                    if parsed.get("status") == "error":
                        had_error = True
                        error_summary.append(f"browser_engine: {parsed.get('message', '')}")
                    sub = parsed.get("percent", 0)
                    grand = int(browser_base + sub * browser_span / 100)
                    yield await _emit_total(grand, parsed.get("message", "Installing browser engine..."))
                except Exception:
                    pass
        else:
            yield await _sse_event("progress", {
                "component": "browser_engine",
                "status": "ready",
                "message": browser_status.detail or "Already installed",
                "percent": 100,
            })
        browser_end = browser_base + browser_span
        yield await _emit_total(browser_end, "Browser engine ready")

        # ── Step 4: TTS model ─────────────────────────────────────────────────
        tts_base = browser_end
        tts_span = 25 if first_run else 35
        tts_status = _check_tts()
        if tts_status.status != "ready":
            yield await _emit_total(tts_base, "Downloading text-to-speech model (~337 MB)...")
            async for event in _download_tts_model(request):
                if await request.is_disconnected():
                    yield await _sse_event("cancelled", {"message": "Setup cancelled by user"})
                    return
                yield event
                try:
                    parsed = json.loads(event.split("data: ", 1)[1].split("\n")[0])
                    if parsed.get("status") == "error":
                        had_error = True
                        error_summary.append(f"tts_model: {parsed.get('message', '')}")
                    sub = parsed.get("percent", 0)
                    grand = int(tts_base + sub * tts_span / 100)
                    yield await _emit_total(grand, parsed.get("message", "Downloading TTS model..."))
                except Exception:
                    pass
        else:
            yield await _sse_event("progress", {
                "component": "tts_model",
                "status": "ready",
                "message": tts_status.detail or "Already installed",
                "percent": 100,
            })
        tts_end = tts_base + tts_span
        yield await _emit_total(tts_end, "Text-to-speech ready")

        # ── Step 5: cloudflared ───────────────────────────────────────────────
        cf_base = tts_end
        cf_span = 2 if first_run else 3
        cf_status = _check_cloudflared()
        if cf_status.status != "ready":
            yield await _emit_total(cf_base, "Downloading remote access binary...")
            async for event in _download_cloudflared(request):
                if await request.is_disconnected():
                    yield await _sse_event("cancelled", {"message": "Setup cancelled by user"})
                    return
                yield event
                try:
                    parsed = json.loads(event.split("data: ", 1)[1].split("\n")[0])
                    if parsed.get("status") == "error":
                        had_error = True
                        error_summary.append(f"cloudflared: {parsed.get('message', '')}")
                    sub = parsed.get("percent", 0)
                    grand = int(cf_base + sub * cf_span / 100)
                    yield await _emit_total(grand, parsed.get("message", "Downloading cloudflared..."))
                except Exception:
                    pass
        else:
            yield await _sse_event("progress", {
                "component": "cloudflared",
                "status": "ready",
                "message": cf_status.detail or "Already installed",
                "percent": 100,
            })
        cf_end = cf_base + cf_span
        yield await _emit_total(cf_end, "Remote access ready")

        # ── Step 6: Transcription model (first_run only) ──────────────────────
        if first_run:
            tx_base = cf_end
            tx_span = 95 - cf_end
            transcription_status = _check_transcription()
            if transcription_status.status != "ready":
                yield await _emit_total(tx_base, "Downloading transcription model (~150 MB)...")
                async for event in _download_transcription_model("base.en", request):
                    if await request.is_disconnected():
                        yield await _sse_event("cancelled", {"message": "Setup cancelled by user"})
                        return
                    yield event
                    try:
                        parsed = json.loads(event.split("data: ", 1)[1].split("\n")[0])
                        if parsed.get("status") == "error":
                            had_error = True
                            error_summary.append(f"transcription: {parsed.get('message', '')}")
                        sub = parsed.get("percent", 0)
                        grand = int(tx_base + sub * tx_span / 100)
                        yield await _emit_total(grand, parsed.get("message", "Downloading transcription model..."))
                    except Exception:
                        pass
            else:
                yield await _sse_event("progress", {
                    "component": "transcription",
                    "status": "ready",
                    "message": transcription_status.detail or "Already installed",
                    "percent": 100,
                })
                yield await _emit_total(95, "Transcription model ready")

        # ── Step 7: Permissions check (informational only) ────────────────────
        try:
            perm_status = await _check_permissions()
        except Exception as e:
            logger.warning(f"Permission check failed during install: {e}")
            perm_status = ComponentStatus(
                id="permissions",
                label="Device Permissions",
                description="OS-level access for microphone, screen recording, and accessibility",
                status="warning",
                detail="Could not check permissions — this is non-blocking",
                deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy",
            )
        yield await _sse_event("progress", {
            "component": "permissions",
            "status": perm_status.status,
            "message": perm_status.detail or "Permissions checked",
            "percent": 100,
            "deep_link": perm_status.deep_link,
        })
        yield await _emit_total(100, "All done!")

    except (asyncio.CancelledError, GeneratorExit):
        logger.info("Setup install stream cancelled by client")
        return
    except Exception as e:
        logger.error(f"Setup install stream failed: {e}", exc_info=True)
        had_error = True
        error_summary.append(str(e))
        try:
            yield await _sse_event("progress", {
                "component": "_system",
                "status": "error",
                "message": f"Setup error: {str(e)}",
                "percent": 0,
            })
        except Exception:
            pass

    # Always emit 'complete' so the frontend never triggers 'stream ended unexpectedly'
    try:
        if had_error:
            yield await _sse_event("complete", {
                "message": "Setup finished with some errors — see component details above",
                "had_errors": True,
                "errors": error_summary,
                "timestamp": time.time(),
                "total_percent": 100,
            })
        else:
            yield await _sse_event("complete", {
                "message": "Setup complete — Matrx Local is ready to use",
                "had_errors": False,
                "errors": [],
                "timestamp": time.time(),
                "total_percent": 100,
            })
    except Exception:
        pass


@router.post("/install")
async def run_install(request: Request, mode: str = "standard"):
    """Run the setup installation as an SSE stream with real-time progress.

    Both modes install: storage dirs, core packages, Playwright Chromium,
    TTS model, cloudflared, and permissions check.

    mode=first_run: additionally downloads the transcription model.
    mode=standard:  everything except transcription model.
    """
    return StreamingResponse(
        _install_stream(request, first_run=(mode == "first_run")),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /setup/install-transcription — optional whisper GGML model download
# ---------------------------------------------------------------------------

class TranscriptionInstallRequest(BaseModel):
    model: str = "base.en"  # default to English base model


@router.post("/install-transcription")
async def install_transcription(req: TranscriptionInstallRequest, request: Request):
    """Download a GGML whisper model to ~/.matrx/models/. Returns SSE stream."""

    async def _stream():
        had_errors = False
        errors: list[str] = []
        async for event in _download_transcription_model(req.model, request):
            yield event
            try:
                parsed = json.loads(event.split("data: ", 1)[1].split("\n")[0])
                if parsed.get("status") == "error":
                    had_errors = True
                    errors.append(parsed.get("message", ""))
            except Exception:
                pass

        if had_errors:
            yield await _sse_event("complete", {
                "message": "Transcription download failed",
                "had_errors": True,
                "errors": errors,
            })
        else:
            yield await _sse_event("complete", {
                "message": "Transcription model ready",
                "had_errors": False,
                "errors": [],
            })

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /setup/logs — live tail of system.log as SSE (no auth required)
# ---------------------------------------------------------------------------

@router.get("/logs")
async def stream_logs(request: Request, lines: int = 200):
    """Stream the live system.log file as Server-Sent Events.

    First emits the last `lines` lines already in the file (history), then
    follows the file in real-time — like `tail -f` — until the client
    disconnects.  Each SSE event has type "log" and data containing a JSON
    object with fields: { line, level, timestamp }.

    No authentication required — this endpoint is under /setup/ which is
    already on the public bypass list.
    """
    log_path = os.path.join(str(LOG_DIR), "system.log")

    def _parse_level(line: str) -> str:
        """Extract log level from a line like '2024-01-01 12:00:00,000 - INFO - ...'"""
        parts = line.split(" - ", 2)
        if len(parts) >= 2:
            lvl = parts[1].strip().upper()
            if lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                return lvl.lower()
        return "info"

    async def _generate():
        # ── Emit a "connected" handshake so the browser knows the stream is live ──
        yield f"event: connected\ndata: {json.dumps({'log_path': log_path, 'timestamp': time.time()})}\n\n"

        if not os.path.isfile(log_path):
            yield f"event: log\ndata: {json.dumps({'line': f'[setup/logs] Log file not found: {log_path}', 'level': 'warn', 'timestamp': time.time()})}\n\n"
        else:
            # ── Tail the last N lines for history — current boot only ────────────
            # system.log persists across runs, so a naive tail replays lines from
            # previous runs (sometimes weeks old) which the client then stamps
            # with the current time — stale warnings resurfacing in issue
            # reports was a real bug. Filter by each line's own timestamp;
            # unparsable lines (tracebacks etc.) inherit the previous line's
            # include/exclude decision.
            def _line_after_boot(raw: str, prev: bool, cutoff: datetime) -> bool:
                try:
                    return datetime.strptime(raw[:23], "%Y-%m-%d %H:%M:%S,%f") >= cutoff
                except ValueError:
                    return prev

            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    all_lines = fh.readlines()
                cutoff = datetime.fromtimestamp(_PROCESS_START - 5.0)
                history: list[str] = []
                include = False
                for raw in all_lines:
                    include = _line_after_boot(raw, include, cutoff)
                    if include:
                        history.append(raw)
                history = history[-lines:] if len(history) > lines else history
                for raw in history:
                    raw = raw.rstrip("\n")
                    if raw:
                        yield f"event: log\ndata: {json.dumps({'line': raw, 'level': _parse_level(raw), 'timestamp': time.time()})}\n\n"
                seek_pos = sum(len(l.encode("utf-8", errors="replace")) for l in all_lines)
            except Exception as exc:
                yield f"event: log\ndata: {json.dumps({'line': f'[setup/logs] Error reading log: {exc}', 'level': 'error', 'timestamp': time.time()})}\n\n"
                seek_pos = 0

            yield f"event: history_end\ndata: {json.dumps({'lines_sent': len(history)})}\n\n"

            # ── Follow new lines in real-time ─────────────────────────────────────
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(seek_pos)
                    while True:
                        if await request.is_disconnected():
                            return
                        chunk = fh.read(65536)
                        if chunk:
                            for raw in chunk.splitlines():
                                raw = raw.strip()
                                if raw:
                                    yield f"event: log\ndata: {json.dumps({'line': raw, 'level': _parse_level(raw), 'timestamp': time.time()})}\n\n"
                        else:
                            # No new data — send a keepalive comment and wait
                            yield ": keepalive\n\n"
                            await asyncio.sleep(0.5)
            except (asyncio.CancelledError, GeneratorExit):
                return
            except Exception as exc:
                yield f"event: log\ndata: {json.dumps({'line': f'[setup/logs] Follow error: {exc}', 'level': 'error', 'timestamp': time.time()})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /setup/debug — full diagnostic snapshot (no auth required)
# ---------------------------------------------------------------------------

@router.get("/debug")
async def debug_state() -> dict[str, Any]:
    """Full diagnostic snapshot — visible in the Dashboard without auth.

    Returns the state of every system component: matrx-ai init, client mode,
    Supabase config, SQLite counts, sync status, and a live probe of the
    PostgREST connection.
    """
    import matrx_ai as _matrx_ai
    from app.services.ai.engine import is_client_mode, is_initialized, tools_loaded, has_db
    from app.services.local_db.repositories import (
        ModelsRepo, AgentsRepo, ToolsRepo, SyncMetaRepo,
    )

    report: dict[str, Any] = {}

    # ── 1. matrx-ai state ────────────────────────────────────────────
    report["matrx_ai"] = {
        "initialized": _matrx_ai._initialized,
        "client_mode": _matrx_ai.is_client_mode() if _matrx_ai._initialized else False,
        "engine_is_initialized": is_initialized(),
        "engine_client_mode_active": is_client_mode(),
        "engine_tools_loaded": tools_loaded(),
        "engine_has_db": has_db(),
    }
    logger.info("[setup/debug] matrx-ai state: %s", report["matrx_ai"])

    # ── 2. Environment / config ───────────────────────────────────────
    supabase_url = os.environ.get("SUPABASE_URL", "")
    anon_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    report["env"] = {
        "SUPABASE_URL": supabase_url or "(NOT SET)",
        "SUPABASE_PUBLISHABLE_KEY": "SET ✓" if anon_key else "(NOT SET ✗)",
        "MATRX_AI_CLIENT_MODE": os.environ.get("MATRX_AI_CLIENT_MODE", "(not in env)"),
        "log_dir": str(LOG_DIR),
        "log_file": os.path.join(str(LOG_DIR), "system.log"),
        "log_file_exists": os.path.isfile(os.path.join(str(LOG_DIR), "system.log")),
    }
    logger.info("[setup/debug] env: %s", report["env"])

    # ── 3. Client singleton check ─────────────────────────────────────
    if _matrx_ai._initialized and _matrx_ai.is_client_mode():
        try:
            from matrx_ai.db import get_client_singleton
            config, auth = get_client_singleton()
            report["client_singleton"] = {
                "ok": True,
                "url": config.url,
                "anon_key_set": bool(config.anon_key),
                "session_active": auth.session is not None,
                "session_user_id": auth.session.user_id if auth.session else None,
            }
            logger.info("[setup/debug] client singleton: %s", report["client_singleton"])
        except Exception as exc:
            report["client_singleton"] = {"ok": False, "error": str(exc)}
            logger.error("[setup/debug] client singleton FAILED: %s", exc)
    else:
        report["client_singleton"] = {"ok": False, "reason": "not in client mode or not initialized"}

    # ── 4. Live PostgREST probe ───────────────────────────────────────
    if report.get("client_singleton", {}).get("ok"):
        probes: dict[str, Any] = {}
        for table in ("ai_model", "prompt_builtins", "prompts"):
            try:
                from matrx_orm.client import SupabaseManager
                from matrx_ai.db import get_client_singleton
                cfg, ath = get_client_singleton()
                mgr = SupabaseManager(table, config=cfg, auth=ath)
                count = await mgr.count()
                probes[table] = {"ok": True, "count": count}
                logger.info("[setup/debug] probe %r → count=%s", table, count)
            except Exception as exc:
                probes[table] = {"ok": False, "error": str(exc)}
                logger.error("[setup/debug] probe %r FAILED: %s", table, exc, exc_info=True)
        report["postgrest_probes"] = probes
    else:
        report["postgrest_probes"] = {"skipped": "client singleton not available"}

    # ── 5. SQLite counts ──────────────────────────────────────────────
    try:
        models_repo = ModelsRepo()
        agents_repo = AgentsRepo()
        tools_repo = ToolsRepo()
        sync_meta = SyncMetaRepo()
        report["sqlite"] = {
            "ai_models_cached": await models_repo.count(),
            "agents_cached_builtin": len(await agents_repo.list_all(source="builtin")),
            "agents_cached_user": len(await agents_repo.list_all(source="user")),
            "tools_cached": await tools_repo.count(),
            "sync_status": await sync_meta.get_all_sync_status(),
        }
        logger.info("[setup/debug] SQLite: %s", report["sqlite"])
    except Exception as exc:
        report["sqlite"] = {"error": str(exc)}
        logger.error("[setup/debug] SQLite probe FAILED: %s", exc)

    # ── Summary ───────────────────────────────────────────────────────
    problems = []
    if not report["matrx_ai"]["initialized"]:
        problems.append("matrx-ai not initialized")
    if not supabase_url:
        problems.append("SUPABASE_URL not set")
    if not anon_key:
        problems.append("SUPABASE_PUBLISHABLE_KEY not set")
    if not report.get("client_singleton", {}).get("ok"):
        problems.append("client singleton not available")
    for tbl, probe in report.get("postgrest_probes", {}).items():
        if isinstance(probe, dict) and not probe.get("ok"):
            problems.append(f"PostgREST probe failed for {tbl}: {probe.get('error', '?')}")

    report["summary"] = {
        "healthy": len(problems) == 0,
        "problems": problems,
    }
    if problems:
        logger.error("[setup/debug] PROBLEMS DETECTED: %s", problems)
    else:
        logger.info("[setup/debug] All systems healthy ✓")

    return report
