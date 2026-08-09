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

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def browser_binary_present() -> bool:
    """Is at least one usable Chromium build present in this world's path?"""
    path = browsers_path()
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    return any(entry.startswith(marker) for marker in BROWSER_MARKERS for entry in entries)


@dataclass(frozen=True)
class BrowserRuntimeStatus:
    """What the desktop needs to decide whether to offer browser rendering."""

    available: bool
    code: str
    reason: str | None
    browsers_path: str
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
            "browsers_path": self.browsers_path,
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

    def start(self) -> None:
        self.running = True
        self.percent = 0
        self.message = "Preparing browser download…"

    def update(self, percent: int | None, message: str | None) -> None:
        if percent is not None:
            self.percent = percent
        if message:
            self.message = message

    def finish(self) -> None:
        self.running = False
        self.percent = None
        self.message = None


_install = _InstallState()

# Why the browser pool could not launch, as reported by ScraperEngine.start().
# None means "no launch has failed" — which is not the same as "available".
_launch_error: str | None = None
# The pool is live in the running engine.
_pool_live = False


def record_pool_started() -> None:
    global _launch_error, _pool_live
    _launch_error = None
    _pool_live = True


def record_pool_stopped() -> None:
    global _pool_live
    _pool_live = False


def record_launch_failure(exc: BaseException | str) -> None:
    """Remember WHY the pool could not start, so status can say it out loud."""
    global _launch_error, _pool_live
    _launch_error = str(exc).strip() or exc.__class__.__name__
    _pool_live = False


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


def status() -> BrowserRuntimeStatus:
    """The current, freshly-probed browser-runtime state."""
    path = str(browsers_path())

    if not playwright_package_present():
        return BrowserRuntimeStatus(
            available=False,
            code="playwright_package_missing",
            reason="The Playwright Python package is not installed in this engine.",
            browsers_path=path,
            installing=_install.running,
            install_percent=_install.percent,
            install_message=_install.message,
        )

    if not browser_binary_present():
        reason = (
            "No Chromium build was found in this app's browser folder "
            f"({path})."
        )
        if _launch_error:
            reason = f"{reason} Last launch error: {_launch_error}"
        return BrowserRuntimeStatus(
            available=False,
            code="browser_not_installed",
            reason=reason,
            browsers_path=path,
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
            browsers_path=path,
            installing=False,
        )

    if _launch_error:
        return BrowserRuntimeStatus(
            available=False,
            code="browser_launch_failed",
            reason=f"Chromium is installed but would not start: {_launch_error}",
            browsers_path=path,
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
        browsers_path=path,
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

    if current.code == "browser_launch_failed":
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
            "browsers_path": current.browsers_path,
            "reason": current.reason,
            "installing": current.installing,
            "download_size_hint": DOWNLOAD_SIZE_HINT,
        },
    )


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
        browsers_path=current.browsers_path,
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
