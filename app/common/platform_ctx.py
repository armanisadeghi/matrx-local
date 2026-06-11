"""Single platform context module — one source of truth for OS/arch detection.

Computed once at import time. All other modules import from here instead of
calling platform.system() / platform.machine() themselves.

Usage:
    from app.common.platform_ctx import PLATFORM, CAPABILITIES, refresh_capabilities

The `PLATFORM` dict is frozen after initial population (module-level constant).
`CAPABILITIES` starts with package-availability flags set at import time.
Hardware-dependent flags (mic, screen capture, GPU) that require subprocess
probing are populated by calling `await refresh_capabilities()` at app startup.
The frontend receives the full merged object via GET /platform/context.
"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# OS / architecture — computed once, truly immutable
# ---------------------------------------------------------------------------

_sys_platform: str = sys.platform          # 'darwin', 'win32', 'linux'
_machine: str = platform.machine()         # 'arm64', 'x86_64', 'AMD64'
_system: str = platform.system()           # 'Darwin', 'Windows', 'Linux'
_release: str = platform.release()
_python_version: str = platform.python_version()
_hostname: str = platform.node()
_os_version: str = platform.platform()
_processor: str = platform.processor()
_mac_version: str = platform.mac_ver()[0] if _sys_platform == "darwin" else ""
_version: str = platform.version()


@functools.lru_cache(maxsize=1)
def _detect_wsl() -> bool:
    """Detect if running inside Windows Subsystem for Linux."""
    if _sys_platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


def _has_display() -> bool:
    """Check whether a desktop/display environment is available."""
    if _sys_platform != "linux":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


PLATFORM: dict[str, Any] = {
    # Raw values
    "os": _sys_platform,
    "system": _system,
    "machine": _machine,
    "release": _release,
    "python_version": _python_version,
    "hostname": _hostname,
    "os_version": _os_version,
    "processor": _processor,
    "mac_version": _mac_version,
    "version": _version,
    "path_separator": os.sep,
    "home_dir": str(Path.home()),
    # Boolean shortcuts
    "is_mac": _sys_platform == "darwin",
    "is_mac_silicon": _sys_platform == "darwin" and _machine == "arm64",
    "is_mac_intel": _sys_platform == "darwin" and _machine == "x86_64",
    "is_windows": _sys_platform == "win32",
    "is_linux": _sys_platform.startswith("linux"),
    "is_wsl": _detect_wsl(),
    # Convenience aliases used throughout the codebase
    "IS_MACOS": _system == "Darwin",
    "IS_WINDOWS": _system == "Windows",
    "IS_LINUX": _system == "Linux",
}

# ---------------------------------------------------------------------------
# Package / binary availability — checked at import (fast, no subprocess)
# ---------------------------------------------------------------------------

def _pkg_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None

def _binary_available(name: str) -> bool:
    return shutil.which(name) is not None


_is_wsl = _detect_wsl()
_display = _has_display()

_zsh_path: str | None = shutil.which("zsh")
_bash_path: str | None = shutil.which("bash")
_ps_path: str | None = shutil.which("pwsh") or shutil.which("powershell.exe")
_cf_path: str | None = shutil.which("cloudflared")
_tess_path: str | None = shutil.which("tesseract")


def _default_shell() -> str | None:
    """Return the best available shell path for the current platform."""
    if _sys_platform == "win32":
        return _ps_path
    return _zsh_path or _bash_path


CAPABILITIES: dict[str, Any] = {
    # ---- Package presence ----
    "has_playwright": _pkg_available("playwright"),
    "has_psutil": _pkg_available("psutil"),
    "has_sounddevice": _pkg_available("sounddevice"),
    "has_cv2": _pkg_available("cv2"),
    "has_numpy": _pkg_available("numpy"),
    "has_pytesseract": _pkg_available("pytesseract"),
    "has_mss": _pkg_available("mss"),
    "has_pil": _pkg_available("PIL"),
    "has_fitz": _pkg_available("fitz"),           # PyMuPDF
    "has_zeroconf": _pkg_available("zeroconf"),
    "has_screeninfo": _pkg_available("screeninfo"),
    "has_tkinter": _pkg_available("tkinter"),
    "has_pyperclip": _pkg_available("pyperclip"),
    "has_watchfiles": _pkg_available("watchfiles"),
    "has_quartz": _pkg_available("Quartz"),
    "has_speech_framework": _pkg_available("Speech"),
    "has_wmi": _pkg_available("wmi"),
    "has_plistlib": _pkg_available("plistlib"),
    # ---- Binary presence ----
    "has_ffmpeg": _binary_available("ffmpeg"),
    "has_cloudflared": _cf_path is not None,
    "cloudflared_path": _cf_path,
    "has_powershell": _ps_path is not None,
    "powershell_path": _ps_path,
    "has_fd": _binary_available("fd"),
    "has_rg": _binary_available("rg"),
    "has_xdg_open": _binary_available("xdg-open"),
    "has_nautilus": _binary_available("nautilus"),
    "has_xdotool": _binary_available("xdotool"),
    "has_wmctrl": _binary_available("wmctrl"),
    "has_bluetoothctl": _binary_available("bluetoothctl"),
    "has_nmcli": _binary_available("nmcli"),
    "has_xrandr": _binary_available("xrandr"),
    "has_imagesnap": _binary_available("imagesnap"),
    "has_whereami": _binary_available("whereami"),
    "has_geoclue": _binary_available("geoclue-where-am-i"),
    "has_systemd_inhibit": _binary_available("systemd-inhibit"),
    "has_chrome": (
        _binary_available("google-chrome")
        or _binary_available("google-chrome-stable")
        or _binary_available("chromium")
    ),
    "chrome_path": (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
    ),
    "has_zsh": _zsh_path is not None,
    "zsh_path": _zsh_path,
    "has_bash": _bash_path is not None,
    "bash_path": _bash_path,
    "shell_path": _default_shell(),
    "has_dns_sd": _binary_available("dns-sd"),
    "has_avahi_browse": _binary_available("avahi-browse"),
    "has_airport": (
        _binary_available("airport")
        or os.path.isfile(
            "/System/Library/PrivateFrameworks/Apple80211.framework"
            "/Versions/Current/Resources/airport"
        )
    ),
    "has_cliclick": _binary_available("cliclick"),
    "has_xclip": _binary_available("xclip"),
    "has_xsel": _binary_available("xsel"),
    "has_lsusb": _binary_available("lsusb"),
    "has_tesseract": _tess_path is not None,
    "tesseract_path": _tess_path,
    "has_cmd": _binary_available("cmd.exe") or _binary_available("cmd"),
    # ---- Environment / display ----
    "has_display": _display,
    "is_wsl": _is_wsl,
    # ---- Derived ----
    "has_system_tray": (
        _sys_platform == "win32"
        or _sys_platform == "darwin"
        or (_sys_platform.startswith("linux") and not _is_wsl and _display)
    ),
    "permission_model": (
        "tcc" if _sys_platform == "darwin"
        else "uac" if _sys_platform == "win32"
        else "polkit" if _sys_platform.startswith("linux")
        else None
    ),
    # ---- Hardware / permission flags (populated by refresh_capabilities) ----
    "mic_available": None,       # None = not yet probed
    "speakers_available": None,
    "camera_available": None,
    "screen_capture_available": None,
    "gpu_available": None,
    "gpu_name": None,
    "gpu_type": None,            # 'apple_silicon' | 'nvidia' | 'amd' | 'integrated' | None
}

# ---------------------------------------------------------------------------
# Async capability refresh — run once at app startup via lifespan
# ---------------------------------------------------------------------------

async def refresh_capabilities() -> None:
    """Probe hardware/permission capabilities that need subprocess calls.

    Safe to call multiple times; later calls update CAPABILITIES in-place.
    Designed to run in the FastAPI lifespan so it doesn't block startup.
    """
    loop = asyncio.get_event_loop()

    gpu_available, gpu_name, gpu_type = await loop.run_in_executor(None, _probe_gpu)
    CAPABILITIES["gpu_available"] = gpu_available
    CAPABILITIES["gpu_name"] = gpu_name
    CAPABILITIES["gpu_type"] = gpu_type

    mic, spk, cam = await loop.run_in_executor(None, _probe_audio_devices)
    CAPABILITIES["mic_available"] = mic
    CAPABILITIES["speakers_available"] = spk
    CAPABILITIES["camera_available"] = cam

    CAPABILITIES["screen_capture_available"] = await loop.run_in_executor(
        None, _probe_screen_capture
    )


# ---------------------------------------------------------------------------
# Internal probers — run in thread-pool, no async I/O
# ---------------------------------------------------------------------------

def _probe_gpu() -> tuple[bool, str | None, str | None]:
    """Return (available, name, type).

    Delegates to the hardware detector which handles NVIDIA (via nvidia-smi with
    WSL/Windows path fallbacks), Apple Silicon, Vulkan, and ROCm.
    """
    if PLATFORM["is_mac_silicon"]:
        return True, "Apple Silicon (Metal)", "apple_silicon"

    if PLATFORM["is_mac_intel"]:
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=8,
            )
            if result.returncode == 0 and result.stdout.strip():
                return True, "Intel/AMD (Metal)", "integrated"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return False, None, None

    # Windows / Linux / WSL — use the hardware detector which handles all cases
    try:
        from app.services.hardware.detector import _detect_gpus
        gpus = _detect_gpus()
        for gpu in gpus:
            name = gpu.get("name", "")
            backend = gpu.get("backend", "cpu")
            if not name or name == "No GPU detected" or backend == "cpu":
                continue
            if "cuda" in backend:
                return True, name, "nvidia"
            if backend == "rocm":
                return True, name, "amd"
            if backend in ("metal",):
                return True, name, "apple_silicon"
            if backend in ("vulkan", "cuda+vulkan"):
                # Distinguish NVIDIA Vulkan from AMD Vulkan by name
                name_lc = name.lower()
                if any(x in name_lc for x in ("nvidia", "geforce", "quadro", "rtx", "gtx")):
                    return True, name, "nvidia"
                if any(x in name_lc for x in ("amd", "radeon", "rx ")):
                    return True, name, "amd"
                if any(x in name_lc for x in ("intel", "arc", "iris")):
                    return True, name, "integrated"
                return True, name, "vulkan"
            # Unknown backend with a real GPU name
            return True, name, "unknown"
    except Exception:
        pass

    return False, None, None


def _probe_audio_devices() -> tuple[bool, bool, bool]:
    """Return (mic_available, speakers_available, camera_available).

    Uses sounddevice for audio; camera presence is a best-effort check via
    cv2 if available, otherwise falls back to OS-level probes.
    """
    mic_available = False
    speakers_available = False
    camera_available = False

    try:
        import sounddevice as sd  # type: ignore[import]
        # DeviceList is a tuple subclass, NOT a list — the old isinstance(list)
        # check wrapped the whole list and .get() raised, so mic/speakers were
        # reported unavailable on every platform.
        devices = sd.query_devices()
        for d in (devices if not isinstance(devices, dict) else [devices]):
            if d.get("max_input_channels", 0) > 0:
                mic_available = True
            if d.get("max_output_channels", 0) > 0:
                speakers_available = True
            if mic_available and speakers_available:
                break
    except Exception:
        pass

    # macOS: system_profiler gives camera presence without triggering TCC dialog
    if PLATFORM["is_mac"]:
        try:
            result = subprocess.run(
                ["system_profiler", "SPCameraDataType"],
                capture_output=True, text=True, timeout=5,
            )
            camera_available = "Camera" in result.stdout or "FaceTime" in result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    elif PLATFORM["is_linux"]:
        import os
        camera_available = any(
            f.startswith("video") for f in os.listdir("/dev") if os.path.exists("/dev")
        )
    elif PLATFORM["is_windows"]:
        try:
            import cv2  # type: ignore[import]
            cap = cv2.VideoCapture(0)
            camera_available = cap.isOpened()
            cap.release()
        except Exception:
            pass

    return mic_available, speakers_available, camera_available


def _probe_screen_capture() -> bool:
    """Return True if screen capture is expected to work on this platform."""
    if PLATFORM["is_mac"]:
        return _pkg_available("mss")
    if PLATFORM["is_linux"]:
        return CAPABILITIES["has_display"]
    if PLATFORM["is_windows"]:
        return _pkg_available("mss")
    return False


# ---------------------------------------------------------------------------
# Cross-platform convenience helpers
# ---------------------------------------------------------------------------

def open_path_cross_platform(path_str: str) -> tuple[bool, str]:
    """Open a file or folder in the OS file manager.

    Returns (success, message).  Uses PLATFORM / CAPABILITIES for all
    OS branching so no caller needs its own platform detection.
    """
    clean = path_str.rstrip("/\\") or path_str

    try:
        if PLATFORM["is_mac"]:
            subprocess.Popen(["open", clean])
        elif PLATFORM["is_windows"]:
            if os.path.isfile(clean):
                subprocess.Popen(["explorer.exe", f"/select,{clean}"])
            else:
                subprocess.Popen(["explorer.exe", clean])
        elif PLATFORM["is_wsl"]:
            wsl_path = subprocess.check_output(
                ["wslpath", "-w", clean], text=True
            ).strip()
            wsl_clean = wsl_path.rstrip("\\")
            subprocess.Popen(["explorer.exe", wsl_clean])
        else:
            if CAPABILITIES["has_xdg_open"]:
                subprocess.Popen(["xdg-open", clean])
            elif CAPABILITIES["has_nautilus"]:
                subprocess.Popen(["nautilus", clean])
            else:
                return True, f"No file manager available. Path: {path_str}"
        return True, f"Opened {path_str}"
    except Exception as e:
        return False, f"Failed to open: {e}"


# ---------------------------------------------------------------------------
# Serialisation helper — called by the /platform/context route
# ---------------------------------------------------------------------------

def get_platform_context() -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of PLATFORM + CAPABILITIES."""
    return {
        "platform": PLATFORM,
        "capabilities": CAPABILITIES,
    }
