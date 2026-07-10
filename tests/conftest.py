"""
pytest configuration for the Matrx Local test suite.

Engine fixture strategy:
  - Uses MATRX_PORT=22199 — OUTSIDE the engine auto-scan range 22140-22159 —
    so the test engine never conflicts with (or is mistaken for) a dev
    instance running on the default port 22140.
  - Sets MATRX_SKIP_ORPHAN_SCAN=1 so the spawned engine NEVER runs
    preflight.clean_orphans() — a test engine must never scan or kill other
    processes on the machine (clean_orphans pattern-matches ALL matrx engines
    system-wide; its live-owner health probe is not a guarantee, and it
    SIGKILLed live dev engines twice on 2026-07-09).
  - Sets MATRX_HOME_DIR to a session-scoped temp dir so the test engine's
    discovery file (local.json), local DB, diagnostics, etc. never clobber
    the real ~/.matrx of a running dev engine.
  - Spawns the real engine via `uv run python run.py` with TEST_MODE=1.
  - Waits up to ENGINE_BOOT_TIMEOUT (60s) for /health. pytest-timeout's 30s
    per-test limit does NOT clip this: tests/pytest.ini sets
    `timeout_func_only = true` so fixture setup is excluded from the timer.
  - Teardown kills ONLY the process this fixture spawned (SIGTERM → SIGKILL
    on the tracked Popen) plus any of ITS surviving descendants — never a
    pattern/port sweep, never anything on 22140-22159.
  - Session-scoped: the engine starts once and is reused across all smoke tests.

Auth strategy:
  - /health, /version, /tools/list, /settings, /platform/context, /hardware,
    /devices/*, /proxy/status, /cloud/debug, /remote-scraper/status are all
    public per the AuthMiddleware _PUBLIC_PATHS definition, or are device/*
    paths which are unconditionally public. Tests use these public endpoints
    directly without a Bearer token.
  - Tests that need a token (tool invocations, notes, etc.) pass a dummy local
    API_KEY. In TEST_MODE the engine accepts it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Generator

import httpx
import psutil
import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
REPO_ROOT = PROJECT_ROOT  # same thing

# ---------------------------------------------------------------------------
# Platform markers — registered here so pytest knows them
# ---------------------------------------------------------------------------

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: marks tests as slow (use --full to include)")
    config.addinivalue_line("markers", "macos_only: test only runs on macOS")
    config.addinivalue_line("markers", "windows_only: test only runs on Windows")
    config.addinivalue_line("markers", "linux_only: test only runs on Linux")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Auto-skip platform-specific tests on non-matching OSes."""
    is_mac = sys.platform == "darwin"
    is_win = sys.platform == "win32"
    is_linux = sys.platform.startswith("linux")

    skip_macos = pytest.mark.skip(reason="macOS only")
    skip_windows = pytest.mark.skip(reason="Windows only")
    skip_linux = pytest.mark.skip(reason="Linux only")

    for item in items:
        if "macos_only" in item.keywords and not is_mac:
            item.add_marker(skip_macos)
        if "windows_only" in item.keywords and not is_win:
            item.add_marker(skip_windows)
        if "linux_only" in item.keywords and not is_linux:
            item.add_marker(skip_linux)


# ---------------------------------------------------------------------------
# Engine fixture
# ---------------------------------------------------------------------------

# MUST stay outside the engine auto-scan range 22140-22159 (see
# app/preflight.py DEFAULT_ENGINE_PORT / ENGINE_PORT_SCAN). A test engine on
# a scan-range port could collide with, or be mistaken for, a real dev engine.
ENGINE_SCAN_RANGE = range(22140, 22160)
TEST_PORT = 22199
assert TEST_PORT not in ENGINE_SCAN_RANGE, (
    f"TEST_PORT {TEST_PORT} is inside the engine auto-scan range "
    f"{ENGINE_SCAN_RANGE.start}-{ENGINE_SCAN_RANGE.stop - 1} — pick a port "
    "outside it so tests never touch a real dev engine."
)
TEST_BASE_URL = f"http://127.0.0.1:{TEST_PORT}"

# How long we allow the real engine to boot before failing loudly. This is
# intentionally LONGER than the 30s per-test pytest-timeout; fixture setup is
# excluded from that timer via `timeout_func_only = true` in tests/pytest.ini.
ENGINE_BOOT_TIMEOUT = 60.0

# A placeholder API key used for authenticated endpoints in test mode.
# The engine accepts any non-empty token string when TEST_MODE=1.
TEST_TOKEN = "test-token-matrx-local"


def _wait_for_engine(base_url: str, timeout: float = ENGINE_BOOT_TIMEOUT) -> bool:
    """Poll /health until the engine responds or timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _spawned_descendants(pid: int) -> list[psutil.Process]:
    """Live descendants of OUR spawned engine process — the only processes
    (besides the Popen itself) this fixture is ever allowed to touch."""
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return []


def _reap_own_tree(proc: subprocess.Popen) -> None:
    """Terminate ONLY the process this fixture spawned plus its descendants.

    Explicitly PID-scoped: no pattern matching, no port sweeps, nothing in
    the 22140-22159 range. A live dev engine on this machine is invisible to
    this teardown by construction.
    """
    # Snapshot descendants BEFORE killing the parent — children reparent to
    # init once the parent dies, but their PIDs stay valid kill targets.
    descendants = _spawned_descendants(proc.pid)

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # Reap any surviving children of OUR engine (uv wrapper, cloudflared, ...).
    survivors = [p for p in descendants if p.is_running()]
    for child in survivors:
        try:
            child.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(survivors, timeout=5)
    for child in alive:
        try:
            child.kill()
        except psutil.Error:
            pass


@pytest.fixture(scope="session")
def engine_process(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[subprocess.Popen, None, None]:
    """Spawn the real Matrx engine on TEST_PORT and yield the Popen object."""
    # Isolated MATRX home: the test engine's discovery file (local.json),
    # local DB, settings, and diagnostics land in a throwaway dir — never in
    # the real ~/.matrx that a live dev engine owns (previously the test
    # engine OVERWROTE ~/.matrx/local.json and unlinked it on teardown,
    # breaking discovery for the live engine).
    matrx_home = tmp_path_factory.mktemp("matrx-home")

    # Share the heavy, read-only model/asset dirs from the real ~/.matrx via
    # symlink so tests that need downloaded models (TTS, wake word, LLM/image
    # catalogs) behave exactly as before isolation. Mutable state files
    # (local.json, matrx.db, settings.json, downloads.db, media outputs) are
    # deliberately NOT shared.
    real_home = Path(os.environ.get("MATRX_HOME_DIR", str(Path.home() / ".matrx")))
    for shared in (
        "tts",
        "models",
        "oww_models",
        "image-models",
        "video-models",
        "image-gen-packages",
        "playwright-browsers",
    ):
        src = real_home / shared
        if src.exists():
            (matrx_home / shared).symlink_to(src)

    env = {
        **os.environ,
        "MATRX_PORT": str(TEST_PORT),
        "TEST_MODE": "1",
        "DEBUG": "1",
        # NEVER let the test engine scan/kill other processes on the machine
        # (see run.py preflight gate — this is what protects a live dev engine).
        "MATRX_SKIP_ORPHAN_SCAN": "1",
        # Isolate all ~/.matrx state (discovery file, matrx.db, diagnostics).
        "MATRX_HOME_DIR": str(matrx_home),
        # Disable features that require external services or long startup
        "TUNNEL_ENABLED": "0",
        # Suppress pystray tray icon in test environment
        "TAURI_SIDECAR": "1",
    }

    proc = subprocess.Popen(
        ["uv", "run", "--frozen", "python", "run.py"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    ready = _wait_for_engine(TEST_BASE_URL, timeout=ENGINE_BOOT_TIMEOUT)

    if not ready:
        _reap_own_tree(proc)
        stdout, stderr = "", ""
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError):
            pass
        pytest.fail(
            f"Engine did not start within {ENGINE_BOOT_TIMEOUT:.0f} seconds "
            f"on port {TEST_PORT}.\n"
            f"STDOUT:\n{stdout[-2000:]}\nSTDERR:\n{stderr[-2000:]}"
        )

    yield proc

    # Teardown — graceful first, then force-kill; scoped to OUR tree only.
    _reap_own_tree(proc)


@pytest.fixture(scope="session")
def engine_url(engine_process: subprocess.Popen) -> str:
    """Return the base URL of the running test engine."""
    return TEST_BASE_URL


@pytest.fixture(scope="session")
def http(engine_url: str) -> Generator[httpx.Client, None, None]:
    """
    Session-scoped httpx client with the test token pre-set.
    Used for endpoints that require authentication.
    """
    with httpx.Client(
        base_url=engine_url,
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        timeout=15.0,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def http_public(engine_url: str) -> Generator[httpx.Client, None, None]:
    """Session-scoped httpx client without auth (for public endpoints)."""
    with httpx.Client(base_url=engine_url, timeout=15.0) as client:
        yield client
