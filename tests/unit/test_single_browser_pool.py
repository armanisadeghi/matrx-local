"""One browser pool, one driver tree, one lifecycle owner.

`FetchWithBrowser` used to call `async_playwright()` itself, so the engine
could hold TWO Chromium trees at once (~200 MB each on the USER'S laptop) and
only the scraper lane's was tracked by `driver_pid` / reaped by
`terminate_playwright_tree` on the force-exit path. An untracked Playwright
tree is the orphan class behind "ended unexpectedly" crash reports
(CLAUDE.md Hard Rule 0).

These tests pin the fix: the tool borrows from `ScraperEngine`'s pool, closes
what it opens, never closes the shared browser, and turns a missing browser
into an `action_needed` STATE instead of a crash.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from app.services.scraper.engine import BrowserUnavailable, ScraperEngine
from app.tools.session import ToolSession
from app.tools.tools import network
from app.tools.types import ToolResultType


# ---------------------------------------------------------------------------
# Fakes — a Playwright-shaped pool with no actual browser behind it
# ---------------------------------------------------------------------------


class FakePage:
    def __init__(self, url: str = "https://example.test/final") -> None:
        self.url = url
        self.waited_for: str | None = None
        self.load_states: list[str] = []

    async def goto(self, url, wait_until=None, timeout=None):  # noqa: ARG002
        class _Resp:
            status = 200

        return _Resp()

    async def wait_for_selector(self, selector, timeout=None):  # noqa: ARG002
        self.waited_for = selector

    async def wait_for_load_state(self, state, timeout=None):  # noqa: ARG002
        self.load_states.append(state)

    async def inner_text(self, _selector):
        return "TEXT BODY"

    async def content(self):
        return "<html>FULL HTML</html>"


class FakeContext:
    def __init__(self, kwargs: dict) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.page = FakePage()

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []
        self.closed = False

    async def new_context(self, **kwargs):
        ctx = FakeContext(kwargs)
        self.contexts.append(ctx)
        return ctx

    async def close(self):
        self.closed = True


class FakePool:
    """Stands in for matrx_scraper's PlaywrightBrowserPool (pool_size=1)."""

    def __init__(self) -> None:
        self.browser = FakeBrowser()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._queue.put_nowait(self.browser)
        self.acquired = 0
        self.released = 0

    async def acquire(self, timeout: float = 30.0):
        self.acquired += 1
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    def release(self, browser) -> None:
        self.released += 1
        self._queue.put_nowait(browser)


def _engine_with(pool) -> ScraperEngine:
    engine = ScraperEngine()
    engine._browser_pool = pool
    engine._started = True
    return engine


def _use_engine(monkeypatch, engine: ScraperEngine) -> None:
    monkeypatch.setattr(network, "_get_engine", lambda: engine)
    # The tool reads the user's "Headless Mode" setting; keep it off the disk.
    monkeypatch.setattr(
        "app.services.cloud_sync.settings_sync.get_settings_sync",
        lambda: {"headless_scraping": True},
    )


# ---------------------------------------------------------------------------
# The tripwire: no second Playwright driver anywhere in the fetch paths
# ---------------------------------------------------------------------------


def test_network_tools_never_launch_their_own_playwright():
    """`app/tools/tools/network.py` must borrow, never launch.

    A bare `async_playwright()` here is a second driver node and a second
    browser tree that `terminate_playwright_tree` cannot reap.
    """
    # Parsed, not grepped: the module's prose deliberately NAMES the thing it
    # must not do, and a docstring is not a launch.
    tree = ast.parse(Path(network.__file__).read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("playwright"):
            raise AssertionError(
                f"network.py imports from {node.module} at line {node.lineno} — borrow "
                "the ONE pool via ScraperEngine.borrow_browser() instead"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("playwright"), (
                    f"network.py imports {alias.name} at line {node.lineno} — borrow "
                    "the ONE pool via ScraperEngine.borrow_browser() instead"
                )
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert name not in {"async_playwright", "launch", "launch_persistent_context"}, (
                f"network.py calls {name}() at line {node.lineno} — it must not start a "
                "second Playwright driver or browser tree; borrow the ONE pool via "
                "ScraperEngine.borrow_browser()"
            )


# ---------------------------------------------------------------------------
# borrow_browser
# ---------------------------------------------------------------------------


def test_borrow_browser_returns_the_pool_browser_and_releases_it():
    pool = FakePool()
    engine = _engine_with(pool)

    async def run():
        async with engine.borrow_browser() as browser:
            assert browser is pool.browser
            assert pool.acquired == 1
            assert pool.released == 0

    asyncio.run(run())
    assert pool.released == 1
    assert pool.browser.closed is False, "the shared pool browser must survive a borrow"


def test_borrow_browser_releases_even_when_the_borrower_raises():
    pool = FakePool()
    engine = _engine_with(pool)

    async def run():
        with pytest.raises(ValueError):
            async with engine.borrow_browser():
                raise ValueError("boom")

    asyncio.run(run())
    assert pool.released == 1, "a failed fetch must not strand the only browser"


def test_headless_off_gets_a_visible_browser_off_the_same_driver():
    """"Headless Mode" off must still show a window — and still be ONE driver.

    A headless pool cannot produce a visible window, so the borrow launches a
    transient browser. It comes off the pool's OWN Playwright driver, so the
    remembered `driver_pid` still covers it, and it is closed on release.
    """
    pool = FakePool()
    launched: list[bool] = []

    class FakeChromium:
        async def launch(self, headless=True):
            launched.append(headless)
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    pool._playwright = FakePlaywright()
    engine = _engine_with(pool)

    seen: list[FakeBrowser] = []

    async def run():
        async with engine.borrow_browser(headless=False) as browser:
            seen.append(browser)

    asyncio.run(run())
    assert launched == [False], "the visible-window request must reach Playwright"
    assert seen[0] is not pool.browser, "a visible window cannot come from the headless pool"
    assert seen[0].closed is True, "the transient headful browser must be closed"
    assert pool.acquired == 0, "the headless pool must not be drained for a headful borrow"


def test_headless_off_without_a_reachable_driver_falls_back_loudly(caplog):
    """No silent default: serving headless instead of headful must say so."""
    pool = FakePool()
    engine = _engine_with(pool)  # FakePool has no _playwright

    async def run():
        async with engine.borrow_browser(headless=False) as browser:
            assert browser is pool.browser

    with caplog.at_level("WARNING"):
        asyncio.run(run())

    assert any("HEADLESS" in rec.message for rec in caplog.records), (
        "falling back to a headless browser must be logged loudly"
    )
    assert pool.released == 1


def test_borrow_browser_without_a_pool_is_a_state_not_a_crash():
    engine = ScraperEngine()
    engine._started = True  # engine READY, browser missing — the degraded case

    async def run():
        with pytest.raises(BrowserUnavailable):
            async with engine.borrow_browser():
                pass

    asyncio.run(run())


def test_borrow_browser_before_start_is_browser_unavailable():
    engine = ScraperEngine()

    async def run():
        with pytest.raises(BrowserUnavailable):
            async with engine.borrow_browser():
                pass

    asyncio.run(run())


# ---------------------------------------------------------------------------
# FetchWithBrowser — same contract, borrowed browser
# ---------------------------------------------------------------------------


def test_fetch_with_browser_borrows_and_keeps_its_result_shape(monkeypatch):
    pool = FakePool()
    engine = _engine_with(pool)
    _use_engine(monkeypatch, engine)

    result = asyncio.run(
        network.tool_fetch_with_browser(
            ToolSession(), url="https://example.test", extract_text=True
        )
    )

    assert result.type == ToolResultType.SUCCESS
    assert "TEXT BODY" in result.output
    assert result.output.startswith("HTTP 200")
    assert "Mode: text extraction" in result.output
    assert result.metadata["success"] is True
    assert result.metadata["results"] == [
        {key: value for key, value in result.metadata.items()
         if key not in {"results", "total", "success_count", "content_length"}}
    ]
    assert result.metadata["status_code"] == 200
    assert result.metadata["url"] == "https://example.test"
    assert result.metadata["response_url"] == "https://example.test/final"

    # Borrowed, returned, and the context we opened was cleaned up.
    assert pool.acquired == 1 and pool.released == 1
    assert pool.browser.closed is False
    assert [ctx.closed for ctx in pool.browser.contexts] == [True]


def test_fetch_with_browser_keeps_its_own_navigation_semantics(monkeypatch):
    """The waits and the desktop viewport are this tool's contract, not the pool's."""
    pool = FakePool()
    engine = _engine_with(pool)
    _use_engine(monkeypatch, engine)

    asyncio.run(network.tool_fetch_with_browser(ToolSession(), url="https://example.test"))
    ctx = pool.browser.contexts[0]
    assert ctx.kwargs["viewport"] == {"width": 1920, "height": 1080}
    assert "Chrome/131" in ctx.kwargs["user_agent"]
    # No selector given → settle on networkidle.
    assert ctx.page.load_states == ["networkidle"]
    assert ctx.page.waited_for is None

    asyncio.run(
        network.tool_fetch_with_browser(
            ToolSession(), url="https://example.test", wait_for="#ready"
        )
    )
    ctx2 = pool.browser.contexts[1]
    assert ctx2.page.waited_for == "#ready"
    assert ctx2.page.load_states == [], "a selector wait replaces the networkidle settle"


def test_fetch_with_browser_says_the_browser_was_busy(monkeypatch):
    """A queue timeout must name the wait that expired, not print a bare class."""
    class BusyPool(FakePool):
        async def acquire(self, timeout: float = 30.0):
            # What the real pool does when the only browser never frees up,
            # without making the test actually wait for it.
            raise asyncio.TimeoutError

    pool = BusyPool()
    engine = _engine_with(pool)
    _use_engine(monkeypatch, engine)

    result = asyncio.run(
        network.tool_fetch_with_browser(
            ToolSession(), url="https://example.test", wait_timeout=1000
        )
    )

    assert result.type == ToolResultType.ERROR
    assert "busy" in result.output
    assert "TimeoutError" not in result.output


def test_fetch_with_browser_without_a_browser_returns_action_needed(monkeypatch):
    engine = ScraperEngine()
    engine._started = True
    _use_engine(monkeypatch, engine)

    result = asyncio.run(
        network.tool_fetch_with_browser(ToolSession(), url="https://example.test")
    )

    assert result.type == ToolResultType.ERROR
    if result.action_needed is not None:
        assert result.action_needed.feature == "browser page fetching"
        assert result.action_needed.action.route == "/settings/capabilities"
    else:
        assert result.metadata["fix_capability_id"] == "browser_automation"
        assert "Settings → Capabilities" in result.output
