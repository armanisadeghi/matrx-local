"""Is a Playwright browser actually installed on THIS machine, in THIS world?

A missing Chromium is a STATE, not an error (CLAUDE.md § states-not-errors):
HTTP scrapes keep working, the engine stays up, and only browser-rendered
fetches are gone. But the user has to be TOLD, and the fix has to be one
click — before this module existed the whole thing was a WARNING line in a log
nobody reads, and the Scraping page happily offered a "Browser" method that
could only fail.

Browsers are deliberately NOT bundled in the PyInstaller sidecar (Chrome's
nested framework structure breaks macOS codesign — see build-lessons.md), so a
packaged install downloads them at first boot in the background
(`app/main.py::_ensure_playwright_browsers`). That means a REAL user hits the
missing-browser state routinely: during that first-boot download, and forever
after if it failed (offline, captive portal, corporate proxy, disk full). The
scraper starts at Phase 3, seconds after the background download begins, so a
first boot ALWAYS starts with no browser pool.

Everything here is parameterised by ``PLAYWRIGHT_BROWSERS_PATH`` /
``MATRX_HOME_DIR`` — Hard Rule 9: a dev engine installs into ``~/.matrx-dev``
(or its ``--fresh`` temp home) and must never write into the installed app's
``~/.matrx``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.services.action_needed.models import (
    ActionNeeded,
    ActionNeededAction,
    ActionNeededKind,
)

logger = logging.getLogger(__name__)

# Versioned directory prefixes Playwright creates under the browsers path.
# `chromium_headless_shell-*` is what the scrape lane actually launches;
# `chromium-*` is the full headed build (also usable).
BROWSER_MARKERS = ("chromium-", "chromium_headless_shell-")

FEATURE = "browser-rendered scraping"
SOURCE = "services.scraper.browser_runtime"
# Stable key for the registry binding; one requirement, one card.
OPERATION_KEY = "scraper:browser_runtime"
FINGERPRINT = "capability:playwright_browser:scraping"

# The Playwright build the scrape lane actually launches. The first-run wizard
# fetches the full "chromium" (520 MB on disk with ffmpeg); the repair here only
# needs the shell, which is what the launch error names.
INSTALL_BROWSER = "chromium-headless-shell"
# Download size, shown to the user before they commit to it.
DOWNLOAD_SIZE_HINT = "~90 MB"


def browsers_path() -> Path:
    """Where THIS world keeps its Playwright browsers.

    Honors an explicit ``PLAYWRIGHT_BROWSERS_PATH`` (set by the frozen runtime
    hook and by ``app/main.py`` at boot); otherwise derives it from the matrx
    home so dev and live never share a directory.
    """
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override)
    from app.config import MATRX_HOME_DIR

    return Path(MATRX_HOME_DIR) / "playwright-browsers"


def playwright_package_present() -> bool:
    """Is the Playwright Python package importable?"""
    try:
        __import__("playwright")
        return True
    except ImportError:
        return False


# Playwright writes this marker into a versioned browser directory only after
# the download was extracted completely. The directory itself appears at the
# START of the download, so "a chromium-* directory exists" is not evidence
# of a browser — it is exactly what a half-finished first-boot download looks
# like (observed 2026-09-13: status said "starting" for a folder that held no
# executable yet).
INSTALL_COMPLETE_MARKER = "INSTALLATION_COMPLETE"


@dataclass(frozen=True)
class BrowserBuild:
    """One versioned Playwright browser directory on this machine."""

    name: str
    """Directory prefix without the revision — ``chromium_headless_shell``."""
    revision: int
    """Playwright's build id. ``-1`` when the directory name carries none."""
    directory: Path
    complete: bool
    """Has Playwright written INSTALLATION_COMPLETE into it?"""

    @property
    def label(self) -> str:
        return f"{self.name}-{self.revision}" if self.revision >= 0 else self.name


def installed_browser_builds(include_incomplete: bool = False) -> list[BrowserBuild]:
    """Every Chromium build in this world's browsers path, NEWEST FIRST.

    Scanned, never assumed: what is on disk is decided by whichever Playwright
    last ran an install here, and that is not necessarily the one this engine
    imports (see :func:`browser_install_report`).
    """
    path = browsers_path()
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return []
    builds: list[BrowserBuild] = []
    for entry in entries:
        for marker in BROWSER_MARKERS:
            if not entry.startswith(marker):
                continue
            name = marker.rstrip("-")
            tail = entry[len(marker) :]
            revision = int(tail) if tail.isdigit() else -1
            directory = path / entry
            build = BrowserBuild(
                name=name,
                revision=revision,
                directory=directory,
                complete=(directory / INSTALL_COMPLETE_MARKER).exists(),
            )
            if build.complete or include_incomplete:
                builds.append(build)
            break
    builds.sort(key=lambda build: build.revision, reverse=True)
    return builds


# The launchable binaries Playwright ships, by basename. Playwright's layout is
# arch-suffixed and has changed shape before
# (``chrome-headless-shell-mac-arm64/chrome-headless-shell``,
# ``chrome-mac-arm64/Google Chrome for Testing.app/...``), so the build
# directory is SCANNED for these rather than assembled from a guessed path — a
# guess that misses is a path that does not exist, which is the whole bug class.
_EXECUTABLE_NAMES = (
    "chrome-headless-shell",
    "chrome-headless-shell.exe",
    "headless_shell",
    "headless_shell.exe",
    "chrome",
    "chrome.exe",
)


def _executable_inside(directory: Path) -> Path | None:
    try:
        platform_dirs = sorted(entry for entry in directory.iterdir() if entry.is_dir())
    except OSError:
        return None
    for platform_dir in platform_dirs:
        for name in _EXECUTABLE_NAMES:
            candidate = platform_dir / name
            if candidate.is_file():
                return candidate
        # macOS full Chromium ships an .app bundle instead of a bare binary.
        for bundled in sorted(platform_dir.glob("*.app/Contents/MacOS/*")):
            if bundled.is_file():
                return bundled
    return None


def resolve_browser_executable() -> Path | None:
    """The launchable binary of the NEWEST complete build actually on disk.

    Diagnostic and forward-looking: it names the browser this machine really
    has, whatever revision the engine's own Playwright happens to pin. (Handing
    it to the pool needs ``executable_path`` support in
    ``matrx_scraper.browser_pool.PlaywrightBrowserPool``, which does not exist
    yet — so today the repair is to install the revision this engine asks for.)
    """
    for build in installed_browser_builds():
        executable = _executable_inside(build.directory)
        if executable is not None:
            return executable
    return None


def expected_browser_revision(browser: str = INSTALL_BROWSER) -> int | None:
    """The build id THIS engine's Playwright will look for, or None if unknown.

    Playwright resolves a browser by exact directory name
    (``chromium_headless_shell-<revision>``), and the revision is pinned inside
    the Playwright package this process imported. When an install into this
    world was performed by a DIFFERENT Playwright version, the directory on
    disk carries a different revision and every launch fails with "Executable
    doesn't exist" until somebody restarts something — the condition that left
    browser-rendered scraping dead for 18.6+ hours (audit row SR-03).
    """
    try:
        from playwright._impl._driver import compute_driver_directory  # type: ignore[import]

        manifest = Path(compute_driver_directory()) / "package" / "browsers.json"
    except Exception:
        try:
            import playwright  # type: ignore[import]

            manifest = (
                Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
            )
        except Exception:
            return None
    try:
        import json

        entries = json.loads(manifest.read_text()).get("browsers", [])
    except Exception:
        return None
    for entry in entries:
        if entry.get("name") == browser:
            revision = str(entry.get("revision", ""))
            return int(revision) if revision.isdigit() else None
    return None


@dataclass(frozen=True)
class BrowserInstallReport:
    """What this engine needs versus what this machine has."""

    expected_revision: int | None
    installed: tuple[BrowserBuild, ...]

    @property
    def newest_installed(self) -> BrowserBuild | None:
        return self.installed[0] if self.installed else None

    @property
    def expected_present(self) -> bool:
        """Can this engine's Playwright resolve a browser at all?

        Unknown pin (a build whose manifest we cannot read) falls back to the
        old rule — any complete build counts — rather than declaring a working
        install broken.
        """
        if self.expected_revision is None:
            return bool(self.installed)
        return any(build.revision == self.expected_revision for build in self.installed)

    @property
    def mismatch(self) -> bool:
        """A browser IS installed, but not the build this engine asks for."""
        return bool(self.installed) and not self.expected_present

    def describe(self) -> str:
        newest = self.newest_installed
        return (
            f"This engine needs browser build {self.expected_revision}, but the only "
            f"build installed in {browsers_path()} is {newest.label if newest else 'none'}. "
            "Installing the build it needs fixes it — no app restart."
        )


def browser_install_report() -> BrowserInstallReport:
    return BrowserInstallReport(
        expected_revision=expected_browser_revision(),
        installed=tuple(installed_browser_builds()),
    )


def browser_binary_present() -> bool:
    """Is a browser THIS engine can actually launch installed in this world?

    Not "is there a chromium directory": a complete install of the wrong
    revision is unlaunchable, and treating it as present is what made the
    one-click repair skip the download and the whole class self-heal never.
    """
    return browser_install_report().expected_present


@dataclass(frozen=True)
class BrowserRuntimeStatus:
    """What the desktop needs to decide whether to offer browser rendering."""

    available: bool
    code: str
    reason: str | None
    installing: bool
    install_percent: int | None = None
    install_message: str | None = None
    # True while the engine is running with no pool but a browser IS on disk —
    # the install landed after the scraper started and a pool restart is due.
    pool_restart_pending: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "code": self.code,
            "reason": self.reason,
            "installing": self.installing,
            "install_percent": self.install_percent,
            "install_message": self.install_message,
            "pool_restart_pending": self.pool_restart_pending,
            "download_size_hint": DOWNLOAD_SIZE_HINT,
        }


class _InstallState:
    """Progress of the one in-flight install, if any."""

    def __init__(self) -> None:
        self.running = False
        self.percent: int | None = None
        self.message: str | None = None
        # Canonical status may be rendered and captured. Keep only a stable
        # category here, never installer output, paths, or exception text.
        self.failure_code: str | None = None

    def start(self) -> None:
        self.running = True
        self.percent = 0
        self.message = "Preparing browser download…"
        self.failure_code = None

    def update(self, percent: int | None, message: str | None) -> None:
        if percent is not None:
            self.percent = percent
        if message:
            self.message = message

    def finish(self) -> None:
        self.running = False
        self.percent = None
        self.message = None

    def fail(self, code: str) -> None:
        self.running = False
        self.percent = None
        self.message = None
        self.failure_code = code

    def recover(self) -> None:
        self.failure_code = None


_install = _InstallState()

# Why the browser pool could not launch, as reported by ScraperEngine.start().
# None means "no launch has failed" — which is not the same as "available".
_launch_error: str | None = None
# The pool is live in the running engine.
_pool_live = False
# A retry of the pool launch is scheduled and has not run yet. The FIRST boot
# attempt is not evidence of a broken browser: the engine's startup phases can
# starve the event loop long enough for a perfectly healthy Chromium to miss its
# launch bound (2026-09-13 — the pool's 30s bound expired inside the startup
# session-index scan and the Dashboard told the user the browser "would not
# start" while it launched in 3.7s from a terminal). While a retry is pending
# this is a TRANSIENT state, not an ask: the user is told it is still starting
# and is asked for nothing.
_retry_pending = False


def record_pool_started() -> None:
    global _launch_error, _pool_live, _retry_pending
    _launch_error = None
    _pool_live = True
    _retry_pending = False
    # A failed multi-browser download may still have installed the exact
    # Chromium build this engine needs. A live pool is stronger evidence than
    # the stale installer exit and must restore canonical readiness.
    _install.recover()


def record_pool_stopped() -> None:
    global _pool_live
    _pool_live = False


def record_launch_failure(exc: BaseException | str) -> None:
    """Remember WHY the pool could not start, so status can say it out loud.

    Always clears the pending-retry flag: the retry either has not been
    scheduled yet (a caller schedules it right after this) or has just run and
    failed, and in that second case the failure is final and must stand.
    """
    global _launch_error, _pool_live, _retry_pending
    _launch_error = str(exc).strip() or exc.__class__.__name__
    _pool_live = False
    _retry_pending = False


def record_retry_pending() -> None:
    """A pool-launch retry is scheduled; hold back the failure state until it runs."""
    global _retry_pending
    _retry_pending = True


def retry_pending() -> bool:
    return _retry_pending


def pool_is_live() -> bool:
    """Is the browser pool up in THIS engine right now?"""
    return _pool_live


def install_in_progress() -> bool:
    return _install.running


def install_started() -> None:
    """Mark an install as running so every surface can show it, not just the
    tab that clicked the button."""
    _install.start()


def install_progress(percent: int | None, message: str | None) -> None:
    _install.update(percent, message)


def install_finished() -> None:
    _install.finish()


def record_install_failure(code: str = "browser_install_failed") -> None:
    """Persist a privacy-safe terminal browser-installer category."""
    _install.fail(code)


async def _signal_installer_tree(
    process: asyncio.subprocess.Process,
    *,
    force: bool,
) -> None:
    """Signal the installer and every descendant it owns."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
        return

    # The Python fallback launches Playwright's Node driver as a descendant.
    # Windows process groups do not make terminate() recursive; taskkill /T
    # is the OS primitive that closes that owned tree.
    args = ["taskkill", "/T", "/PID", str(process.pid)]
    if force:
        args.insert(1, "/F")
    killer = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(killer.wait(), timeout=2.0)
    except TimeoutError:
        killer.kill()
        await killer.wait()


async def stop_background_install(
    task: asyncio.Task[None] | None,
    process: asyncio.subprocess.Process | None,
    *,
    timeout: float = 5.0,
    signal_tree: Callable[
        [asyncio.subprocess.Process], Awaitable[None]
    ] | None = None,
) -> bool:
    """Stop and reap the first-boot browser installer owned by this engine.

    Cancelling only ``communicate()`` leaves the Node installer alive. Signal
    the child first, give the owning task a bounded chance to finish, then
    cancel/kill as the final fallback.
    """
    if task is None and process is None:
        return True

    if process is not None and process.returncode is None:
        if signal_tree is None:
            await _signal_installer_tree(process, force=False)
        else:
            await signal_tree(process)

    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    if process is not None and process.returncode is None:
        if signal_tree is None:
            await _signal_installer_tree(process, force=True)
        else:
            await signal_tree(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            return False

    return (task is None or task.done()) and (
        process is None or process.returncode is not None
    )


class BackgroundInstallOwner:
    """One race-free owner for the first-boot Playwright installer tree."""

    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.stopping = False
        self._spawn_lock = asyncio.Lock()

    def start(self, work: Awaitable[None]) -> None:
        self.stopping = False
        self.task = asyncio.create_task(work)

    async def spawn(
        self,
        *cmd: str,
        create: Callable[..., Awaitable[asyncio.subprocess.Process]] = asyncio.create_subprocess_exec,
        **kwargs: Any,
    ) -> asyncio.subprocess.Process | None:
        async with self._spawn_lock:
            if self.stopping:
                return None
            if os.name == "posix":
                kwargs["start_new_session"] = True
            else:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            self.process = await create(*cmd, **kwargs)
            return self.process

    def clear_process(self, process: asyncio.subprocess.Process) -> None:
        if self.process is process:
            self.process = None

    async def stop(self, *, timeout: float = 5.0) -> bool:
        self.stopping = True
        # Serialize with spawn. Once this lock is acquired, either the child is
        # fully registered or the stopping flag prevented it from existing.
        async with self._spawn_lock:
            task = self.task
            process = self.process
        stopped = await stop_background_install(task, process, timeout=timeout)
        self.task = None
        self.process = None
        return stopped


def status() -> BrowserRuntimeStatus:
    """The current, freshly-probed browser-runtime state."""
    if _install.failure_code and not _install.running:
        return BrowserRuntimeStatus(
            available=False,
            code=_install.failure_code,
            reason="The built-in browser download did not finish. Try again.",
            installing=False,
        )

    if not playwright_package_present():
        return BrowserRuntimeStatus(
            available=False,
            code="playwright_package_missing",
            reason="The Playwright Python package is not installed in this engine.",
            installing=_install.running,
            install_percent=_install.percent,
            install_message=_install.message,
        )

    # browser_binary_present() stays the ONE presence seam (it is what the
    # install route and the retry scheduler ask); the report only decides WHICH
    # absence this is.
    if not browser_binary_present():
        report = browser_install_report()
        if report.mismatch:
            # A browser IS on disk — just not the build this engine resolves.
            # Saying "not installed" here would send the user looking for a
            # download they already have. The exact installed build and local
            # directory are diagnostics, not client-facing runtime status.
            newest = report.newest_installed
            installed_revision = newest.revision if newest else "unknown"
            reason = (
                f"The built-in browser is build {installed_revision}, but this app "
                f"needs build {report.expected_revision}. Updating it fixes the "
                "mismatch without restarting the app."
            )
            return BrowserRuntimeStatus(
                available=False,
                code="browser_build_mismatch",
                reason=reason,
                installing=_install.running,
                install_percent=_install.percent,
                install_message=_install.message,
            )
        reason = "No built-in browser is installed yet."
        return BrowserRuntimeStatus(
            available=False,
            code="browser_not_installed",
            reason=reason,
            installing=_install.running,
            install_percent=_install.percent,
            install_message=_install.message,
        )

    # Binary is on disk. If the pool is live we are fully available; if it is
    # not, either the install landed after startup (restart the pool) or the
    # launch failed for a reason that is NOT a missing download.
    if _pool_live:
        return BrowserRuntimeStatus(
            available=True,
            code="ready",
            reason=None,
            installing=False,
        )

    if _retry_pending:
        # A launch failed, but another attempt is coming. Saying "would not
        # start" here would be a lie with a button on it.
        return BrowserRuntimeStatus(
            available=False,
            code="browser_starting",
            reason="The built-in browser is still starting",
            installing=_install.running,
            install_percent=_install.percent,
            install_message=_install.message,
            pool_restart_pending=True,
        )

    if _launch_error:
        return BrowserRuntimeStatus(
            available=False,
            code="browser_launch_failed",
            reason="The built-in browser did not start. Repair it and restart the app if needed.",
            installing=_install.running,
            install_percent=_install.percent,
            install_message=_install.message,
            pool_restart_pending=True,
        )

    # Installed, no pool, no error: nothing has tried yet (engine still booting,
    # or a tool launches Playwright directly). Treat as available — the binary
    # is what browser-rendered fetching actually needs.
    return BrowserRuntimeStatus(
        available=True,
        code="ready",
        reason=None,
        installing=_install.running,
    )


def browser_action_needed(feature: str = FEATURE) -> ActionNeeded | None:
    """The user-facing ask, or None when the browser is available.

    Plain language on purpose: the person reading this is a subject-matter
    expert, not someone who knows what Chromium is.
    """
    current = status()
    if current.available:
        return None

    if current.code == "browser_starting":
        # Transient by definition: a retry is already scheduled and the person
        # has nothing to do. An ask here would be noise that clears itself.
        return None

    if current.code == "browser_build_mismatch":
        title = "The built-in browser needs an update"
        message = (
            "The built-in browser on this computer is a different version from "
            "the one this app needs, so pages that need a real browser can't be "
            f"loaded. Getting the right version is a {DOWNLOAD_SIZE_HINT} download "
            "and nothing else stops working meanwhile."
        )
        label = "Update browser"
    elif current.code == "browser_launch_failed":
        title = "The built-in browser needs a restart"
        message = (
            "Web pages that need a real browser can't be loaded right now. "
            "Reinstalling the browser usually fixes it."
        )
        label = "Repair browser"
    elif current.installing:
        title = "The built-in browser is still downloading"
        message = (
            "Pages that need a real browser aren't available until the "
            f"download finishes ({DOWNLOAD_SIZE_HINT}). Everything else keeps working."
        )
        label = "View progress"
    elif current.code == "browser_install_failed":
        title = "The built-in browser download did not finish"
        message = (
            "Pages that need a real browser are unavailable for now. Try the "
            "download again; everything else keeps working."
        )
        label = "Try again"
    else:
        title = "The built-in browser isn't installed yet"
        message = (
            "Matrx needs a small built-in browser to read pages that only show "
            f"their content after running JavaScript. It's a {DOWNLOAD_SIZE_HINT} "
            "one-time download. Regular page fetching works without it."
        )
        label = "Install browser"

    return ActionNeeded(
        fingerprint=FINGERPRINT,
        code=current.code,
        kind=ActionNeededKind.CAPABILITY_INSTALL,
        feature=feature,
        title=title,
        message=message,
        action=ActionNeededAction(
            kind="install_browser_engine",
            label=label,
            route="/scraping",
        ),
        source=SOURCE,
        details={
            "reason": current.reason,
            "installing": current.installing,
            "download_size_hint": DOWNLOAD_SIZE_HINT,
        },
    )


# One automatic build repair per engine process. A ~90 MB download is not
# free, and a repair that cannot fix the condition must never loop.
_self_repair_attempted = False


def self_repair_attempted() -> bool:
    return _self_repair_attempted


async def self_heal_browser_build() -> bool:
    """Install the browser build THIS engine needs, then bring the pool up.

    The repair for a build mismatch, performed IN the running engine — no app
    restart, no user click. Before this, the condition (row SR-03) sat degraded
    for 18.6+ hours: nothing re-resolved the path while the engine lived, and
    the only fix was a restart nobody was ever told to perform.

    Bounded to one attempt per process and loud at both ends: the install
    announces itself through the shared install state (so every surface shows
    the download, not just a tab that clicked something), and a failure leaves
    the honest degraded state plus its one-click action in place.

    Returns whether browser rendering is available afterwards.
    """
    global _self_repair_attempted

    report = browser_install_report()
    if not report.mismatch:
        return report.expected_present
    if _self_repair_attempted:
        return False
    if _install.running:
        return False
    _self_repair_attempted = True

    path = str(browsers_path())
    logger.warning(
        "[scraper/browser_runtime] Browser build mismatch — repairing automatically: %s",
        report.describe(),
    )
    install_started()
    install_progress(5, "Updating the built-in browser…")
    failed = False
    try:
        from app.api.browser_runtime_routes import _parse_progress
        from app.api.setup_routes import _install_playwright_browsers

        async for event in _install_playwright_browsers(path, browser=INSTALL_BROWSER):
            # Mirror the ONE installer's own progress into the shared state, so
            # every surface shows the download rather than a silent stall.
            percent, message, event_status = _parse_progress(event)
            install_progress(percent, message)
            if event_status == "error":
                failed = True
    except Exception:
        failed = True
        logger.warning(
            "[scraper/browser_runtime] Automatic browser repair could not run",
            exc_info=True,
        )
    finally:
        if failed:
            record_install_failure()
        else:
            install_finished()

    if failed or not browser_binary_present():
        logger.warning(
            "[scraper/browser_runtime] Automatic browser repair did not produce build %s "
            "— leaving the visible degraded state and its one-click repair in place",
            report.expected_revision,
        )
        sync_service_registry()
        await publish_action_needed()
        return False

    # The right build is on disk: bring the pool up in THIS engine.
    from app.services.scraper.engine import get_scraper_engine

    engine = get_scraper_engine()
    started = await engine.ensure_browser_pool()
    logger.info(
        "[scraper/browser_runtime] Automatic browser repair installed build %s and %s",
        report.expected_revision,
        "browser rendering is available again" if started else "the pool still would not start",
    )
    sync_service_registry()
    await publish_action_needed()
    return started


def sync_service_registry() -> BrowserRuntimeStatus:
    """Mirror the current state onto the ``scraper`` service record.

    READY means healthy; a scraper that cannot render pages is DEGRADED, which
    is exactly what ``ServiceState.DEGRADED`` exists for. It is never FAILED —
    HTTP scrapes keep working and startup must not abort.
    """
    from app.launcher import get_registry

    current = status()
    registry = get_registry()
    registry.annotate(
        "scraper",
        browser_available=current.available,
        browser_code=current.code,
        browser_reason=current.reason,
    )
    if current.available:
        registry.ready("scraper")
    else:
        registry.degraded(
            "scraper",
            reason=f"browser rendering unavailable ({current.code}): {current.reason}",
        )
    return current


async def publish_action_needed() -> None:
    """Push the current state into the action-needed registry (or clear it)."""
    from app.services.action_needed.registry import get_action_needed_registry

    await get_action_needed_registry().reconcile_operation(
        OPERATION_KEY, browser_action_needed()
    )
