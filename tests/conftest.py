"""
pytest configuration for the Matrx Local test suite.

Engine fixture strategy:
  - Uses MATRX_PORT=22399 and MATRX_PORT_BASE=22399 — outside both the live
    (22140-22159) and dev (22240-22259) worlds. The base override also moves
    the test proxy to 22439 instead of the live proxy's 22180.
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

# The pytest engine has its own fixed TEST position. Keep it outside both
# renderer scan ranges and below the run-specific packaged-smoke range.
ENGINE_SCAN_RANGES = (range(22140, 22160), range(22240, 22260))
TEST_PORT = 22399
assert all(TEST_PORT not in ports for ports in ENGINE_SCAN_RANGES)
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

    Explicitly PID-scoped: no pattern matching and no port sweeps in either
    the live 22140-22159 or dev 22240-22259 range. Other engines on this
    machine are invisible to teardown by construction.
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
    # the real ~/.matrx that the installed app owns (previously the test
    # engine OVERWROTE ~/.matrx/local.json and unlinked it on teardown,
    # breaking discovery for the live engine).
    test_root = tmp_path_factory.mktemp("matrx-test-world")
    matrx_home = test_root / "matrx-home"
    os_home = test_root / "os-home"
    user_dir = test_root / "user-files"
    for path in (matrx_home, os_home, user_dir):
        path.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "MATRX_PORT": str(TEST_PORT),
        "MATRX_PORT_BASE": str(TEST_PORT),
        "MATRX_ISOLATED_TEST": "1",
        "TEST_MODE": "1",
        "DEBUG": "1",
        # NEVER let the test engine scan/kill other processes on the machine
        # (see run.py preflight gate — this is what protects a live dev engine).
        "MATRX_SKIP_ORPHAN_SCAN": "1",
        # Isolate all ~/.matrx state (discovery file, matrx.db, diagnostics).
        "MATRX_HOME_DIR": str(matrx_home),
        "MATRX_TEMP_DIR": str(test_root / "temp"),
        "MATRX_DATA_DIR": str(test_root / "platform-data"),
        "MATRX_CONFIG_DIR": str(test_root / "config"),
        "MATRX_LOG_DIR": str(test_root / "logs"),
        "LOG_DIR": str(test_root / "logs"),
        "MATRX_USER_DIR": str(user_dir),
        "MATRX_NOTES_DIR": str(user_dir / "Notes"),
        "MATRX_FILES_DIR": str(user_dir / "Files"),
        "MATRX_CODE_DIR": str(user_dir / "Code"),
        "MATRX_WORKSPACES_DIR": str(test_root / "workspaces"),
        "MATRX_AGENT_DATA_DIR": str(test_root / "agent-data"),
        "HOME": str(os_home),
        "USERPROFILE": str(os_home),
        "APPDATA": str(os_home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(os_home / "AppData" / "Local"),
        "XDG_DATA_HOME": str(os_home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(os_home / ".config"),
        "XDG_CACHE_HOME": str(os_home / ".cache"),
        "XDG_STATE_HOME": str(os_home / ".local" / "state"),
        "XDG_DOCUMENTS_DIR": str(user_dir),
        "MATRX_INSTANCE_SALT": "pytest",
        "MATRX_CLOUD_PARTICIPATION": "0",
        # Disable features that require external services or long startup
        "TUNNEL_ENABLED": "0",
        # Suppress pystray tray icon in test environment
        "TAURI_SIDECAR": "1",
    }

    # --no-sync: run against the venv AS-IS. During the matrx-ai 0.3.0
    # pre-PyPI interim the venv deliberately diverges from uv.lock (wheels
    # built from aidream main are installed by hand — see the pyproject
    # matrx-* pin comment); a plain `uv run --frozen` would silently re-sync
    # the venv back to the stale lock and boot the OLD matrx-ai.
    # A full smoke run emits far more than an OS pipe buffer. Leaving stdout
    # and stderr as undrained PIPEs eventually blocks the engine inside a log
    # write, after which every remaining HTTP test times out. A seekable temp
    # file keeps output bounded by disk rather than pipe capacity and still
    # lets startup failures include their tail.
    engine_log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    proc = subprocess.Popen(
        ["uv", "run", "--frozen", "--no-sync", "python", "run.py"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=engine_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    ready = _wait_for_engine(TEST_BASE_URL, timeout=ENGINE_BOOT_TIMEOUT)

    if not ready:
        _reap_own_tree(proc)
        try:
            engine_log.flush()
            engine_log.seek(0)
            output = engine_log.read()
        except (OSError, ValueError):
            output = ""
        engine_log.close()
        pytest.fail(
            f"Engine did not start within {ENGINE_BOOT_TIMEOUT:.0f} seconds "
            f"on port {TEST_PORT}.\n"
            f"ENGINE LOG:\n{output[-4000:]}"
        )

    try:
        yield proc
    finally:
        # Teardown — graceful first, then force-kill; scoped to OUR tree only.
        _reap_own_tree(proc)
        engine_log.close()


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


# ── the organization admission gate (2026-08-30) ─────────────────────────────
#
# aidream's AuthMiddleware refuses any authenticated request that names no
# organization, so both aidream transports now resolve and attach
# `X-Organization-Id` where they build `Authorization`. Resolving it is a live
# Supabase round trip (`mbr_for_user`), which no unit test may make: without
# this stub every transport test 401s against the real database and fails for
# a reason that has nothing to do with what it is testing.
#
# This stubs ONLY the network identity lookup. Header assembly — attach,
# caller-supplied-wins, public-calls-untouched, and the refusal when no
# organization resolves — is exercised for real in
# tests/unit/test_aidream_transport_organization_header.py, which overrides
# this fixture with its own resolvers.
TEST_ORGANIZATION_ID = "11111111-2222-4333-8444-555555555555"


@pytest.fixture(autouse=True)
def _organization_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.aidream import organization as organization_module

    async def _resolve(_jwt: str) -> str:
        return TEST_ORGANIZATION_ID

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", _resolve
    )
