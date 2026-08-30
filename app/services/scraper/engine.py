"""The LOCAL scrape lane — the user's own machine, the user's own IP.

There is ONE scraper in the platform: `matrx_scraper`. This module is not a
second engine, it is the local *execution lane* for that one engine: it holds
the desktop-shaped pieces (an in-process cache, a browser pool the Rust host
must be able to reap, the user's Brave key out of the in-app key store) and
then calls the package.

**No proxies, ever.** `use_proxy=False` on every call is the entire reason this
lane exists — the value of scraping from a downloaded desktop app is that the
request leaves the user's residential IP. Routing it through the datacenter
pool would make this identical to the server and worth nothing.

Until 2026-08-09 this file bootstrapped a forked COPY of the engine out of
`scraper-service/` via a sys.modules aliasing trick. The fork is gone; anything
it did that the package did not now lives in the package (see its FEATURE.md
change log).

Lifecycle:
    engine = get_scraper_engine()
    await engine.start()     # once, during app lifespan
    ...
    await engine.stop()      # on shutdown
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

from matrx_scraper.orchestrator import ScrapeResult, scrape, scrape_many_stream
from matrx_scraper.scrape_options import ScrapeOptions, apply_field_flags
from matrx_scraper.scraper import RequestType
from matrx_scraper.utils.url import validate_and_correct_url

from app.services.scraper import browser_runtime

logger = logging.getLogger(__name__)

# How many URLs the local lane fetches at once. The DEFAULT is deliberately far
# below the server's 20: this runs on the user's own laptop and their own home
# connection, both of which they are also USING. A desktop app that saturates
# someone's uplink to finish a bulk scrape 3 seconds sooner is a bad neighbour.
#
# But 5 is only a guess about someone else's hardware and line, so both values
# are user preferences (settings keys `scrape_concurrency` /
# `research_concurrency`, in the same synced blob as `scrape_delay`) and are
# read at CALL time — a change applies to the next scrape with no engine
# restart, exactly like `ensure_search_client()` picks up a newly saved Brave
# key. The bounds exist so nobody can type 500 and melt their machine or get
# themselves blocked by every site they touch.
DEFAULT_SCRAPE_CONCURRENCY = 5
DEFAULT_RESEARCH_CONCURRENCY = 5
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 20

SCRAPE_CONCURRENCY_SETTING = "scrape_concurrency"
RESEARCH_CONCURRENCY_SETTING = "research_concurrency"


def clamp_concurrency(raw: Any, default: int) -> int:
    """Coerce a stored/incoming concurrency value into the allowed range.

    A garbage value (missing, non-numeric, out of range) is never fatal here —
    the request-time path falls back to the default rather than refusing to
    scrape. Rejection with a message is the job of the settings API
    (`PUT /settings`), which is where the user actually types a number.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[scraper/engine.py] Ignoring non-numeric concurrency setting %r — using %d",
            raw,
            default,
        )
        return default
    if value < MIN_CONCURRENCY or value > MAX_CONCURRENCY:
        clamped = max(MIN_CONCURRENCY, min(MAX_CONCURRENCY, value))
        logger.warning(
            "[scraper/engine.py] Concurrency setting %d out of range %d-%d — using %d",
            value,
            MIN_CONCURRENCY,
            MAX_CONCURRENCY,
            clamped,
        )
        return clamped
    return value


def _concurrency_setting(key: str, default: int) -> int:
    """Read the user's current value for ``key`` from the synced settings blob."""
    try:
        from app.services.cloud_sync.settings_sync import get_settings_sync

        return clamp_concurrency(get_settings_sync().get(key, default), default)
    except Exception:
        logger.exception(
            "[scraper/engine.py] Could not read %s — using default %d", key, default
        )
        return default

# Session-scoped dedupe only. Durable storage is scrape_store.py (SQLite) plus
# the server; this exists so re-scraping the same URL inside one sitting is free.
CACHE_MAX_SIZE = 1000
CACHE_TTL_SECONDS = 1800

# Pages per research effort level.
RESEARCH_EFFORT_LIMITS = {"low": 10, "medium": 25, "high": 50, "extreme": 100}

# Hard ceiling on how long the Playwright driver + Chromium may take to come
# up. A healthy launch is 1-3 seconds; a hung one (stale driver, Gatekeeper
# stall, corrupt browser build) previously awaited FOREVER inside the FastAPI
# lifespan — the engine never finished startup, never bound its port, and the
# desktop reported "did not become reachable within 300 seconds" (2026-08-30).
# On timeout the pool is reaped and recorded as a launch failure: a STATE with
# a one-click repair, exactly like any other unlaunchable browser.
BROWSER_POOL_START_TIMEOUT_SECONDS = 30.0


class BrowserUnavailable(RuntimeError):
    """This machine has no usable Playwright browser right now.

    A STATE, not a crash: the engine is READY and every HTTP scrape still
    works — only browser-rendered fetches are unavailable, and the remedy is a
    one-click capability install. Callers turn this into an `action_needed`,
    never into a traceback.
    """


@dataclass
class ResearchPageEvent:
    """One page finished during a research run."""

    url: str
    title: str = ""
    scraped_content: Optional[str] = None
    scrape_failure_reason: Optional[str] = None


@dataclass
class ResearchDoneEvent:
    """A research run finished; carries the compiled text."""

    total_urls: int
    scraped: int
    text_content: str
    execution_time_ms: float


@dataclass
class LocalScrapeOptions:
    """What the desktop asks for from one scrape call.

    Field selection itself is the package's `ScrapeOptions` — the shape of a
    result is defined once, there. This adds only the two knobs that belong to
    the LOCAL lane: whether to use the session cache, and whether to render in
    a real browser.
    """

    fields: ScrapeOptions = field(default_factory=ScrapeOptions)
    use_cache: bool = True
    use_browser: bool = False


def _extract_driver_pid(browser_pool: Any) -> int | None:
    """Best-effort: return the PID of the Playwright driver node process.

    Tries the Playwright object's internal transport (fast, exact); falls back
    to scanning our own child processes for the `run-driver` node. Any failure
    returns None — the preflight orphan sweep is the backstop, so a missing PID
    here is not fatal.

    The scan fallback is only safe when THIS pool's driver is the one being
    looked for on a healthy pool (a just-started pool's driver is a
    `run-driver` child of this process). It must never be used to pick a
    reap target after a FAILED launch: this process can own other drivers —
    the headed `local_browser` session runs its own — and the scan would
    return whichever it finds first. Failure cleanup uses
    ``_transport_driver_pid`` instead.
    """
    pid = _transport_driver_pid(browser_pool)
    if pid is not None:
        return pid

    # Fallback: find a `run-driver` node among our own descendants.
    try:
        import os as _os

        import psutil

        me = psutil.Process(_os.getpid())
        for child in me.children(recursive=True):
            try:
                cmd = " ".join(child.cmdline())
            except psutil.Error:
                continue
            if "run-driver" in cmd:
                return child.pid
    except Exception:
        pass
    return None


def _transport_driver_pid(browser_pool: Any) -> int | None:
    """The driver PID recorded on THIS pool's Playwright transport, or None.

    Exact by construction — it can only ever name the driver this pool
    spawned, so it is the only extraction failure cleanup may reap by.
    """
    try:
        pw = getattr(browser_pool, "_playwright", None)
        proc = getattr(
            getattr(
                getattr(getattr(pw, "_impl_obj", None), "_connection", None),
                "_transport",
                None,
            ),
            "_proc",
            None,
        )
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int) and pid > 0:
            return pid
    except Exception:
        pass
    return None


def terminate_playwright_tree(driver_pid: int | None) -> None:
    """Force-terminate the Playwright driver + its browser tree by PID.

    Called from run.py's force-exit path (``_kill_child_subprocesses``) when the
    graceful lifespan teardown did not run. Killing by remembered PID (never
    ``pkill -f``) honors the ownership contract — we only ever touch the driver
    tree WE spawned, never an unrelated Playwright on the machine.
    """
    if not driver_pid:
        return
    try:
        import psutil
    except Exception:
        return
    try:
        driver = psutil.Process(driver_pid)
    except psutil.Error:
        return

    # Collect the tree (children reparent after the driver dies, but their PIDs
    # stay valid) then TERM the driver and every descendant.
    try:
        targets = driver.children(recursive=True)
    except psutil.Error:
        targets = []
    targets.append(driver)

    for proc in targets:
        try:
            proc.terminate()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(targets, timeout=2.0)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    logger.info(
        "[scraper/engine.py] terminate_playwright_tree: reaped driver_pid=%s (+%d children)",
        driver_pid,
        len(targets) - 1,
    )


class ScraperEngine:
    """Owns the local lane's long-lived pieces and runs scrapes through them.

    No database. Durable persistence is scrape_store.py (local SQLite) and the
    server (remote_client.py); the cache here is session-scoped dedupe only.
    """

    def __init__(self) -> None:
        self._browser_pool: Any = None
        self._cache: Any = None
        self._domain_config: Any = None
        self._search_key: str | None = None
        self._started = False
        # PID of the Playwright driver node process (parent of the
        # chrome-headless-shell tree). Captured at start() so the force-exit
        # path in run.py can reap the whole browser tree we own if the graceful
        # lifespan teardown never runs (crash / hung shutdown).
        self._driver_pid: int | None = None

    @property
    def is_ready(self) -> bool:
        return self._started

    @property
    def driver_pid(self) -> int | None:
        """PID of the Playwright driver node we spawned, or None."""
        return self._driver_pid

    @property
    def browser_pool(self) -> Any:
        return self._browser_pool

    @property
    def has_browser(self) -> bool:
        """Is browser rendering live in THIS engine right now?"""
        return self._browser_pool is not None

    @property
    def has_search(self) -> bool:
        return bool(self._search_key)

    # ------------------------------------------------------------------
    # Concurrency — the USER's preference, read fresh on every call
    # ------------------------------------------------------------------

    @property
    def scrape_concurrency(self) -> int:
        """How many URLs a bulk scrape fetches at once, right now.

        Never cached on the instance: a value saved in Settings must take
        effect on the very next scrape without restarting the engine.
        """
        return _concurrency_setting(SCRAPE_CONCURRENCY_SETTING, DEFAULT_SCRAPE_CONCURRENCY)

    @property
    def research_concurrency(self) -> int:
        """How many pages a research run fetches at once, right now."""
        return _concurrency_setting(
            RESEARCH_CONCURRENCY_SETTING, DEFAULT_RESEARCH_CONCURRENCY
        )

    # ------------------------------------------------------------------
    # Brave key — the USER's key, from the in-app store, never an env var
    # ------------------------------------------------------------------

    def ensure_search_client(self) -> bool:
        """Sync the package's Brave client with the user's current key.

        Called before every search/research so a key the user just saved works
        immediately — no engine restart. Returns whether search is available.
        """
        try:
            from matrx_scraper.search import configure_client

            from app.services.ai.key_manager import get_cached_user_keys

            key = (
                get_cached_user_keys().get("brave", "").strip()
                # Developer convenience only; a shipped build never has this.
                or os.environ.get("BRAVE_API_KEY", "").strip()
            )
            if key == (self._search_key or ""):
                return bool(key)

            configure_client(key or None)
            self._search_key = key or None
            if key:
                logger.info("[scraper/engine.py] Brave Search enabled from user key store")
            else:
                logger.info("[scraper/engine.py] Brave Search key cleared")
            return bool(key)
        except Exception:
            logger.exception("[scraper/engine.py] Could not configure Brave Search")
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return

        logger.info("[scraper/engine.py] ScraperEngine: starting")

        from matrx_scraper.cache import MemoryCache
        from matrx_scraper.domain_config import StaticDomainConfigStore

        self._cache = MemoryCache(max_size=CACHE_MAX_SIZE, ttl_seconds=CACHE_TTL_SECONDS)
        self._domain_config = StaticDomainConfigStore()

        await self.ensure_browser_pool()

        self.ensure_search_client()

        self._started = True
        logger.info(
            "[scraper/engine.py] ScraperEngine: ready ✓ (browser=%s, search=%s)",
            self._browser_pool is not None,
            self.has_search,
        )

    async def ensure_browser_pool(self) -> bool:
        """Start the browser pool if it isn't running. Never raises.

        Split out of ``start()`` so the one-click browser install can bring
        rendering up in the RUNNING engine — the browser download finishes
        minutes after Phase 3 has already given up on it, and telling the user
        to restart the app would not be a fix.

        A missing browser is a STATE, not a failure: every HTTP scrape still
        works, only browser-rendered fetches are unavailable. The reason is
        recorded on ``browser_runtime`` so status surfaces can say it out loud
        instead of leaving it in a log line.
        """
        if self._browser_pool is not None:
            return True

        pool = None
        try:
            from matrx_scraper.browser_pool import PlaywrightBrowserPool

            # One browser. The server runs five because it crawls; a desktop
            # app renders the occasional page and every extra Chromium is
            # ~200 MB of the user's RAM.
            pool = PlaywrightBrowserPool(pool_size=1)
            # Bounded on purpose: this runs inside the app lifespan, and an
            # unbounded hung launch blocks the ENTIRE engine from ever
            # accepting a request (see BROWSER_POOL_START_TIMEOUT_SECONDS).
            await asyncio.wait_for(
                pool.start(), timeout=BROWSER_POOL_START_TIMEOUT_SECONDS
            )
            self._browser_pool = pool
            self._driver_pid = _extract_driver_pid(pool)
            browser_runtime.record_pool_started()
            logger.info(
                "[scraper/engine.py] ScraperEngine: browser pool started ✓ (driver_pid=%s)",
                self._driver_pid,
            )
            return True
        except Exception as pw_exc:
            if isinstance(pw_exc, asyncio.TimeoutError):
                pw_exc = TimeoutError(
                    "Chromium did not finish launching within "
                    f"{BROWSER_POOL_START_TIMEOUT_SECONDS:.0f}s"
                )
            # A timed-out (or half-failed) launch can leave a live driver node
            # + Chromium tree behind. Reap ONLY the PID recorded on THIS
            # pool's transport — never the run-driver child scan, which could
            # name a different driver this process owns (the headed
            # local_browser session) and kill the wrong tree. No transport pid
            # → leave it to the preflight orphan sweep, the documented
            # backstop.
            if pool is not None:
                terminate_playwright_tree(_transport_driver_pid(pool))
            browser_runtime.record_launch_failure(pw_exc)
            logger.warning(
                "[scraper/engine.py] ScraperEngine: Playwright browser pool unavailable — "
                "browser-rendered scrapes disabled (%s). The desktop surfaces this as a "
                "one-click install; see app/services/scraper/browser_runtime.py. Error: %s",
                browser_runtime.status().code,
                pw_exc,
            )
            self._browser_pool = None
            return False

    async def stop(self) -> None:
        if not self._started:
            return

        logger.info("[scraper/engine.py] ScraperEngine: stopping")

        if self._browser_pool:
            try:
                await asyncio.wait_for(self._browser_pool.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.debug(
                    "[scraper/engine.py] ScraperEngine: browser pool stop timed out "
                    "(expected on SIGINT)"
                )
            except Exception:
                logger.debug(
                    "[scraper/engine.py] ScraperEngine: browser pool stop failed "
                    "(expected on SIGINT)"
                )
            self._browser_pool = None
            browser_runtime.record_pool_stopped()

        self._started = False
        self._driver_pid = None
        logger.info("[scraper/engine.py] ScraperEngine: stopped")

    # ------------------------------------------------------------------
    # Lending the ONE browser — every browser fetch in this process
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def borrow_browser(
        self, *, headless: bool = True, timeout: float = 60.0
    ) -> AsyncGenerator[Any, None]:
        """Yield a Playwright ``Browser`` from the ONE pool this engine owns.

        This exists so no other module ever calls `async_playwright()` for a
        page fetch. A second `async_playwright()` means a second driver node
        and a second ~200 MB Chromium tree on the USER'S laptop, and — worse —
        that tree is invisible to `driver_pid` / `terminate_playwright_tree`,
        which is exactly the untracked-orphan class that produced "ended
        unexpectedly" crash reports (CLAUDE.md Hard Rule 0).

        The borrower gets a raw Browser and owns whatever context it opens on
        it: this pool's browsers are LONG-LIVED and shared, so every context
        you create you must close, and you must never call `browser.close()`.

        `headless=False` honours the user's "Headless Mode" setting (Settings →
        Scraping): a visible window cannot come out of the headless pool, so it
        gets a transient browser launched from the SAME driver — still one
        driver tree, still reaped by the same PID, closed when you're done.

        Raises `BrowserUnavailable` when there is no browser to lend.
        """
        if not self._started:
            raise BrowserUnavailable(
                "The scraper engine has not started, so no browser is available yet."
            )
        pool = self._browser_pool
        if pool is None:
            raise BrowserUnavailable(
                "No Playwright browser is installed on this machine, so pages "
                "cannot be rendered."
            )

        if not headless:
            # The pool launched headless; a visible window needs its own
            # browser. Take it off the pool's driver so the process tree — and
            # therefore `driver_pid` — still covers it.
            playwright = getattr(pool, "_playwright", None)
            if playwright is None:
                logger.warning(
                    "[scraper/engine.py] borrow_browser: headless=False requested but "
                    "the pool's Playwright driver is not reachable — serving a HEADLESS "
                    "browser instead. The page will render with no visible window."
                )
            else:
                browser = await playwright.chromium.launch(headless=False)
                try:
                    yield browser
                finally:
                    try:
                        await browser.close()
                    except Exception:
                        logger.debug(
                            "[scraper/engine.py] borrow_browser: headful browser close "
                            "failed (already gone?)",
                            exc_info=True,
                        )
                return

        browser = await pool.acquire(timeout=timeout)
        try:
            yield browser
        finally:
            pool.release(browser)

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------

    async def scrape_one(
        self, url: str, options: LocalScrapeOptions | None = None
    ) -> ScrapeResult:
        """Scrape a single URL from this machine. Never raises for a bad URL."""
        opts = options or LocalScrapeOptions()
        try:
            corrected = validate_and_correct_url(url)
        except ValueError as exc:
            return ScrapeResult(
                url=url,
                response_url=url,
                success=False,
                content_type="unknown",
                failure_reason=str(exc),
            )

        return await scrape(
            corrected,
            use_proxy=False,  # the whole point: the user's own IP
            request_type=RequestType.BROWSER if opts.use_browser else RequestType.NORMAL,
            cache=self._cache if opts.use_cache else None,
            domain_config=self._domain_config,
            browser_pool=self._browser_pool,
        )

    async def scrape(
        self, urls: list[str], options: LocalScrapeOptions | None = None
    ) -> list[ScrapeResult]:
        """Scrape many URLs concurrently, bounded for a home connection."""
        opts = options or LocalScrapeOptions()
        semaphore = asyncio.Semaphore(self.scrape_concurrency)

        async def _bounded(url: str) -> ScrapeResult:
            async with semaphore:
                return await self.scrape_one(url, opts)

        return list(await asyncio.gather(*[_bounded(u) for u in urls]))

    async def scrape_stream(
        self, urls: list[str], options: LocalScrapeOptions | None = None
    ) -> AsyncGenerator[ScrapeResult, None]:
        """Yield each result the moment it finishes, not in input order."""
        opts = options or LocalScrapeOptions()
        semaphore = asyncio.Semaphore(self.scrape_concurrency)

        async def _bounded(url: str) -> ScrapeResult:
            async with semaphore:
                return await self.scrape_one(url, opts)

        for future in asyncio.as_completed([_bounded(u) for u in urls]):
            yield await future

    @staticmethod
    def to_payload(result: ScrapeResult, options: ScrapeOptions) -> dict[str, Any]:
        """The result as the wire/storage dict, honouring the caller's flags.

        Both the shape and the filter come from the package, so what the
        desktop stores and what the server stores are the same thing.
        """
        return apply_field_flags(result.to_dict(), options)

    # ------------------------------------------------------------------
    # Research — search, then scrape every hit locally
    # ------------------------------------------------------------------

    async def research(
        self,
        query: str,
        country: str = "us",
        effort: str = "extreme",
        freshness: Optional[str] = None,
        safe_search: str = "off",
    ) -> AsyncGenerator[ResearchPageEvent | ResearchDoneEvent, None]:
        if not self.ensure_search_client():
            raise RuntimeError("Search client not configured")

        from matrx_scraper.search import (
            async_brave_search,
            extract_urls_from_search_results,
        )

        start_time = time.monotonic()
        max_urls = RESEARCH_EFFORT_LIMITS.get(effort, 100)

        search_results = await async_brave_search(
            query=query,
            count=20,
            country=country,
            extra_snippets=True,
            safe_search=safe_search,
            freshness=freshness,
        )

        entries = extract_urls_from_search_results([(query, search_results)])
        by_url = {e["url"]: e for e in entries}
        urls_to_scrape = [e["url"] for e in entries[:max_urls]]

        # Research wants readable prose per page, nothing else — no links, no
        # overview, no organized_data. Those are the expensive fields.
        options = LocalScrapeOptions(
            fields=ScrapeOptions(get_text_data=True),
            use_cache=True,
        )

        scraped_count = 0
        all_content: list[str] = []
        semaphore = asyncio.Semaphore(self.research_concurrency)

        async def _bounded(url: str) -> ScrapeResult:
            async with semaphore:
                return await self.scrape_one(url, options)

        for future in asyncio.as_completed([_bounded(u) for u in urls_to_scrape]):
            result = await future
            content = result.ai_research_content or result.text_data or result.raw_text
            yield ResearchPageEvent(
                url=result.url,
                title=result.title or by_url.get(result.url, {}).get("title", ""),
                scraped_content=content or None,
                scrape_failure_reason=result.failure_reason,
            )
            if content:
                scraped_count += 1
                all_content.append(f"--- {result.url} ---\n{content}")

        yield ResearchDoneEvent(
            total_urls=len(urls_to_scrape),
            scraped=scraped_count,
            text_content="\n\n".join(all_content),
            execution_time_ms=(time.monotonic() - start_time) * 1000,
        )


_engine: Optional[ScraperEngine] = None


def get_scraper_engine() -> ScraperEngine:
    """Get the singleton ScraperEngine instance."""
    global _engine
    if _engine is None:
        _engine = ScraperEngine()
    return _engine
