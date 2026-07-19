"""Core primitives for on-demand pip installs into user-writable targets.

Copied from the image-gen installer path that already ships to end users:
find a real Python (never the frozen sidecar), install into a target with that
Python's pip or uv when pip is intentionally absent, and stream progress over
SSE.
"""

from __future__ import annotations

import asyncio
import os
import shutil
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
_ISOLATED_FALLBACK_DIR = ".isolated-packages"
_reported_path_escapes: set[tuple[Path, Path]] = set()
_reported_path_escapes_lock = threading.Lock()


class InstallCancelledError(RuntimeError):
    """A managed package subprocess was stopped during engine shutdown."""


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def packages_dir(name: str) -> Path:
    """Platform-appropriate directory for a named optional package set.

    macOS / Linux  →  ~/.matrx/<name>/
    Windows        →  %LOCALAPPDATA%\\AI Matrx\\<name>\\
    Source-run isolation sets ``MATRX_HOME_DIR``. It must win on every
    platform so a dev engine can never migrate the installed app's runtime.
    """
    if Path(name).name != name or name in ("", ".", ".."):
        raise ValueError(f"Optional package directory must be a basename: {name!r}")

    isolated_home = os.getenv("MATRX_HOME_DIR")
    if isolated_home:
        active_home = Path(isolated_home).expanduser().resolve(strict=False)
        requested = active_home / name
        resolved = requested.resolve(strict=False)
        if _is_within(resolved, active_home):
            return requested

        # Old dev setups shared the live image runtime through a symlink. That
        # cache became mutable once automatic compatibility migrations shipped,
        # allowing an ordinary dev startup to rewrite the installed app's
        # runtime. Preserve the user's link untouched but never follow it.
        fallback = active_home / _ISOLATED_FALLBACK_DIR / name
        fallback_resolved = fallback.resolve(strict=False)
        if not _is_within(fallback_resolved, active_home):
            raise RuntimeError(
                "Both the requested and fallback optional-package paths escape "
                f"MATRX_HOME_DIR ({active_home})"
            )
        key = (requested, resolved)
        with _reported_path_escapes_lock:
            first_report = key not in _reported_path_escapes
            _reported_path_escapes.add(key)
        if first_report:
            logger.error(
                "[optional_packages] Refusing package path %s because it resolves "
                "outside active MATRX_HOME_DIR %s (resolved=%s); using isolated "
                "fallback %s without modifying the existing path",
                requested,
                active_home,
                resolved,
                fallback,
            )
        return fallback
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
    cancel_event: threading.Event | None = None,
) -> None:
    """Run ``pip install --target``, forwarding each output line to progress.

    Raises RuntimeError on non-zero exit or inactivity timeout.
    """
    python = find_python()
    cmd = _install_command(python, target)
    if extra_index:
        cmd += ["--extra-index-url", extra_index]
    cmd += packages

    logger.info("[optional_packages] Installing packages for Python: %s", python)
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
    cancelled = threading.Event()

    def _watchdog() -> None:
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled.set()
                _terminate_process_tree(proc)
                return
            if time.monotonic() - last_output > INACTIVITY_TIMEOUT_S:
                watchdog_fired.set()
                _terminate_process_tree(proc)
                return
            # A shutdown request must not wait behind the ordinary inactivity
            # watchdog cadence. Poll it promptly; keep the old low-wake cadence
            # for installs that have no lifecycle cancellation event.
            time.sleep(0.25 if cancel_event is not None else 5.0)

    watchdog = threading.Thread(target=_watchdog, daemon=True, name="pip-watchdog")
    watchdog.start()

    for raw_line in proc.stdout:
        last_output = time.monotonic()
        line = raw_line.rstrip("\n").rstrip("\r")
        if line:
            progress.log(line)

    proc.wait()
    if cancelled.is_set():
        raise InstallCancelledError("package installation cancelled during shutdown")
    if watchdog_fired.is_set():
        raise RuntimeError(
            f"pip produced no output for {int(INACTIVITY_TIMEOUT_S)}s and was "
            f"killed while installing {packages}"
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"package installer exited with code {proc.returncode} while "
            f"installing {packages}"
        )


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate an installer and every child it spawned, then escalate."""
    try:
        import psutil  # noqa: PLC0415 — core dependency; only used on cancellation

        parent = psutil.Process(proc.pid)
        processes = [*parent.children(recursive=True), parent]
        for process in processes:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(processes, timeout=3.0)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(alive, timeout=2.0)
    except Exception:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except (OSError, subprocess.TimeoutExpired):
                if proc.poll() is None:
                    proc.kill()


def run_subprocess_cancellable(
    command: list[str],
    *,
    cancel_event: threading.Event | None,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a captured helper process with the same shutdown ownership as pip."""
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=0.25)
            return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process_tree(proc)
                proc.communicate()
                raise InstallCancelledError(
                    "package verification cancelled during shutdown"
                )
            if time.monotonic() >= deadline:
                _terminate_process_tree(proc)
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(command, timeout, stdout, stderr)


def _python_has_pip(python: str) -> bool:
    """Probe without importing pip into the engine process."""
    try:
        result = subprocess.run(
            [python, "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _install_command(python: str, target: Path) -> list[str]:
    """Choose a target installer that works in packaged and uv dev runtimes.

    `uv sync` intentionally creates environments without pip. Source startup
    migrations still need to repair an existing managed runtime, so use the
    user's uv executable when available. Packaged installations continue to
    prefer the selected interpreter's pip and fall back to ensurepip when the
    interpreter ships it but has not bootstrapped it yet.
    """
    if not _python_has_pip(python):
        uv = shutil.which("uv")
        if uv:
            logger.info(
                "[optional_packages] %s has no pip; using uv for the target install",
                python,
            )
            return [
                uv,
                "pip",
                "install",
                "--python",
                python,
                "--target",
                str(target),
                "--upgrade",
                "--no-cache",
                "--no-progress",
            ]

        for extra in ([], ["--user"]):
            try:
                subprocess.run(
                    [python, "-m", "ensurepip", "--upgrade", *extra],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if _python_has_pip(python):
                break
        else:
            raise RuntimeError(
                f"Python at {python} has no pip, uv is not installed, and "
                "ensurepip could not bootstrap pip. Install Python 3 with pip "
                "and try again."
            )

    return [
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
