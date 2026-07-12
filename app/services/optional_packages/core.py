"""Core primitives for on-demand pip installs into user-writable targets.

Copied from the image-gen installer path that already ships to end users:
find a real Python (never the frozen sidecar), ``pip install --target``,
stream progress over SSE.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import AsyncIterator

from app.common.system_logger import get_logger

logger = get_logger()

# CPU-only torch index — avoids the multi-GB CUDA wheel on Linux/Windows x86.
# Apple Silicon uses the standard PyPI ARM wheel.
TORCH_CPU_INDEX_URL = "https://download.pytorch.org/whl/cpu"


def packages_dir(name: str) -> Path:
    """Platform-appropriate directory for a named optional package set.

    macOS / Linux  →  ~/.matrx/<name>/
    Windows        →  %LOCALAPPDATA%\\AI Matrx\\<name>\\
    """
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "AI Matrx" / name
    return Path.home() / ".matrx" / name


class InstallProgress:
    """Thread-safe progress bag shared between the installer thread and SSE stream."""

    def __init__(self, log_prefix: str = "optional_packages") -> None:
        self.status: str = "idle"  # idle | running | complete | error
        self.stage: str = ""
        self.percent: float = 0.0
        self.message: str = ""
        self.log_lines: list[str] = []
        self.error: str | None = None
        self.log_prefix = log_prefix
        self._lock = threading.Lock()
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _emit(self, event: dict) -> None:
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._queue.put(event), loop)

    def update(self, stage: str, percent: float, message: str) -> None:
        with self._lock:
            self.stage = stage
            self.percent = percent
            self.message = message
        logger.info("[%s] [%.0f%%] %s — %s", self.log_prefix, percent, stage, message)
        self._emit(
            {
                "status": self.status,
                "stage": stage,
                "percent": percent,
                "message": message,
            }
        )

    def log(self, line: str) -> None:
        with self._lock:
            self.log_lines.append(line)
            if len(self.log_lines) > 2000:
                self.log_lines = self.log_lines[-2000:]
        logger.debug("[pip] %s", line)
        self._emit(
            {
                "status": self.status,
                "stage": self.stage,
                "percent": self.percent,
                "message": line,
                "log": True,
            }
        )

    def finish(self, message: str = "Installation complete") -> None:
        with self._lock:
            self.status = "complete"
            self.percent = 100.0
            self.message = message
        logger.info("[%s] Installation complete ✓", self.log_prefix)
        self._emit(
            {
                "status": "complete",
                "stage": "done",
                "percent": 100.0,
                "message": message,
            }
        )

    def fail(self, error: str) -> None:
        with self._lock:
            self.status = "error"
            self.error = error
            self.message = error
        logger.error("[%s] FAILED: %s", self.log_prefix, error)
        self._emit(
            {
                "status": "error",
                "stage": "error",
                "percent": self.percent,
                "message": error,
            }
        )

    async def events(self) -> AsyncIterator[dict]:
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=300)
            except asyncio.TimeoutError:
                yield {
                    "status": "error",
                    "message": "Installer timed out (5 min with no output)",
                }
                return
            yield event
            if event.get("status") in ("complete", "error"):
                return


def find_python() -> str:
    """Return a usable Python 3 interpreter for running pip.

    Inside a PyInstaller --onefile binary ``sys.executable`` points at the
    engine itself. Running ``engine -m pip`` launches a second engine instance
    which steals the port and kills the running server.

    Resolution order:
      1. Dev mode: ``sys.executable`` (real interpreter).
      2. uv-managed Python under ~/.local/share/uv/python (or ~/.uv/python).
      3. System Python 3 on PATH.
      4. Platform fixed paths (macOS CLT, Windows ``py`` launcher).
    """
    is_frozen = getattr(sys, "frozen", False)

    if not is_frozen:
        return sys.executable

    uv_python_roots = [
        Path.home() / ".local" / "share" / "uv" / "python",
        Path.home() / ".uv" / "python",
    ]
    preferred = ["3.13", "3.12", "3.11", "3.10"]
    for root in uv_python_roots:
        if not root.exists():
            continue

        def _rank(p: Path) -> int:
            name = p.name
            for i, ver in enumerate(preferred):
                if ver in name:
                    return i
            return len(preferred)

        candidates = sorted(root.iterdir(), key=_rank)
        for entry in candidates:
            for rel in ("bin/python3", "bin/python", "python.exe"):
                exe = entry / rel
                if exe.exists():
                    logger.debug("[%s] Using uv Python: %s", "optional_packages", exe)
                    return str(exe)

    import shutil

    for name in ("python3", "python3.13", "python3.12", "python3.11", "python"):
        found = shutil.which(name)
        if found and found != sys.executable:
            try:
                out = subprocess.check_output(
                    [found, "-c", "import sys; print(sys.version_info.major)"],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()
                if out == "3":
                    logger.debug("[optional_packages] Using system Python: %s", found)
                    return found
            except Exception:
                continue

    if sys.platform == "darwin":
        for p in [
            "/usr/bin/python3",
            "/Library/Developer/CommandLineTools/usr/bin/python3",
        ]:
            if Path(p).exists() and p != sys.executable:
                return p

    if sys.platform == "win32":
        py = shutil.which("py")
        if py:
            return py

    raise RuntimeError(
        "Could not find a Python interpreter to run pip. "
        "Please install Python 3 from https://python.org and try again."
    )


def run_pip_streaming(
    packages: list[str],
    target: Path,
    progress: InstallProgress,
    extra_index: str | None = None,
) -> None:
    """Run ``pip install --target``, forwarding each output line to progress.

    Raises RuntimeError on non-zero exit or inactivity timeout.
    """
    python = find_python()
    cmd = [
        python,
        "-m",
        "pip",
        "install",
        "--target",
        str(target),
        "--upgrade",
        "--no-cache-dir",
        "--progress-bar",
        "off",
        "--disable-pip-version-check",
    ]
    if extra_index:
        cmd += ["--extra-index-url", extra_index]
    cmd += packages

    logger.info("[optional_packages] Running pip via: %s", python)
    logger.info("[optional_packages] Command: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert proc.stdout is not None

    INACTIVITY_TIMEOUT_S = 600.0
    last_output = time.monotonic()
    watchdog_fired = threading.Event()

    def _watchdog() -> None:
        while proc.poll() is None:
            if time.monotonic() - last_output > INACTIVITY_TIMEOUT_S:
                watchdog_fired.set()
                proc.kill()
                return
            time.sleep(5.0)

    watchdog = threading.Thread(target=_watchdog, daemon=True, name="pip-watchdog")
    watchdog.start()

    for raw_line in proc.stdout:
        last_output = time.monotonic()
        line = raw_line.rstrip("\n").rstrip("\r")
        if line:
            progress.log(line)

    proc.wait()
    if watchdog_fired.is_set():
        raise RuntimeError(
            f"pip produced no output for {int(INACTIVITY_TIMEOUT_S)}s and was "
            f"killed while installing {packages}"
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"pip exited with code {proc.returncode} while installing {packages}"
        )
