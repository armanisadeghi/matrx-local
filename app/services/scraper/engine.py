"""ScraperEngine — bridge between matrx_local and the scraper-service package.

The scraper-service uses `app.*` imports internally, which collide with
matrx_local's own `app/` package.  We solve this by temporarily swapping
sys.modules and sys.path during import so the scraper-service's `app`
package loads under `scraper_app.*` in the module registry.

Lifecycle:
    engine = ScraperEngine()
    await engine.start()     # called once during app lifespan
    ...
    await engine.stop()      # called on shutdown
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCRAPER_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "scraper-service"

_scraper_modules: dict[str, Any] = {}
_bootstrap_done = False


def _bootstrap_scraper_package() -> None:
    """One-time setup: register scraper-service/app as 'scraper_app' in sys.modules.

    This lets all internal `from app.xxx import yyy` inside the scraper-service
    resolve correctly by aliasing every `app.*` sub-module to `scraper_app.*`.
    """
    global _bootstrap_done
    if _bootstrap_done:
        return

    scraper_root = str(SCRAPER_SERVICE_ROOT)

    matrx_app_children = {
        k: v for k, v in sys.modules.items()
        if k == "app" or k.startswith("app.")
    }

    for k in matrx_app_children:
        del sys.modules[k]

    sys.path.insert(0, scraper_root)
    try:
        scraper_app = importlib.import_module("app")

        snapshot: dict[str, ModuleType] = {}
        for k, v in list(sys.modules.items()):
            if k == "app" or k.startswith("app."):
                snapshot[k] = v

        for k, v in snapshot.items():
            alias = "scraper_app" + k[3:]  # "app.foo" → "scraper_app.foo"
            sys.modules[alias] = v

        sys.modules["scraper_app"] = scraper_app
    finally:
        sys.path.remove(scraper_root)

        for k in list(sys.modules.keys()):
            if k == "app" or k.startswith("app."):
                if k not in matrx_app_children:
                    del sys.modules[k]

        sys.modules.update(matrx_app_children)

    _bootstrap_done = True


def _import_scraper(module_path: str) -> Any:
    """Import a module from the scraper-service package.

    Translates `app.foo.bar` → `scraper_app.foo.bar` and loads it
    with proper sys.path/sys.modules isolation.
    """
    aliased = "scraper_app" + module_path[3:] if module_path.startswith("app.") else module_path

    if aliased in _scraper_modules:
        return _scraper_modules[aliased]

    _bootstrap_scraper_package()

    if aliased in sys.modules:
        _scraper_modules[aliased] = sys.modules[aliased]
        return sys.modules[aliased]

    scraper_root = str(SCRAPER_SERVICE_ROOT)

    matrx_app_children = {
        k: v for k, v in sys.modules.items()
        if k == "app" or k.startswith("app.")
    }

    scraper_app_children = {
        k: v for k, v in sys.modules.items()
        if k == "scraper_app" or k.startswith("scraper_app.")
    }
    for k, v in scraper_app_children.items():
        original = "app" + k[11:]  # "scraper_app.foo" → "app.foo"
        sys.modules[original] = v
    for k in matrx_app_children:
        if k not in {("app" + sk[11:]) for sk in scraper_app_children}:
            if k in sys.modules:
                del sys.modules[k]

    sys.path.insert(0, scraper_root)
    try:
        original_path = "app" + module_path[3:] if module_path.startswith("app.") else module_path
        mod = importlib.import_module(original_path)

        for k, v in list(sys.modules.items()):
            if k == "app" or k.startswith("app."):
                alias = "scraper_app" + k[3:]
                sys.modules[alias] = v
    finally:
        sys.path.remove(scraper_root)

        for k in list(sys.modules.keys()):
            if k == "app" or k.startswith("app."):
                if k not in matrx_app_children:
                    del sys.modules[k]
        sys.modules.update(matrx_app_children)

    _scraper_modules[aliased] = mod
    return mod


def _extract_driver_pid(browser_pool: Any) -> int | None:
    """Best-effort: return the PID of the Playwright driver node process.

    Tries the Playwright object's internal transport (fast, exact); falls back
    to scanning our own child processes for the `run-driver` node. Any failure
    returns None — the preflight orphan sweep is the backstop, so a missing PID
    here is not fatal.
    """
    # 1) Internal transport (async_playwright().start() → driver subprocess).
    try:
        pw = getattr(browser_pool, "_playwright", None)
        proc = (
            getattr(
                getattr(
                    getattr(getattr(pw, "_impl_obj", None), "_connection", None),
                    "_transport",
                    None,
                ),
                "_proc",
                None,
            )
        )
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int) and pid > 0:
            return pid
    except Exception:
        pass

    # 2) Fallback: find a `run-driver` node among our own descendants.
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
        driver_pid, len(targets) - 1,
    )


class ScraperEngine:
    """Manages the scraper-service orchestrator and its dependencies.

    No direct database connection. All scrape results are pushed to the
    server via POST /api/v1/content/save after every successful scrape.
    In-memory TTLCache is used for deduplication within a session only.
    """

    def __init__(self) -> None:
        self._orchestrator: Any = None
        self._fetcher: Any = None
        self._browser_pool: Any = None
        self._page_cache: Any = None
        self._domain_config_store: Any = None
        self._search_client: Any = None
        self._search_key: str | None = None
        self._settings: Any = None
        self._started = False
        # PID of the Playwright driver node process (parent of the
        # chrome-headless-shell tree). Captured at start() so the force-exit
        # path in run.py can reap the whole browser tree we own if the graceful
        # lifespan teardown never runs (crash / hung shutdown). See
        # `driver_pid` and app/preflight.py's playwright orphan reclamation.
        self._driver_pid: int | None = None

    @property
    def is_ready(self) -> bool:
        return self._started and self._orchestrator is not None

    @property
    def driver_pid(self) -> int | None:
        """PID of the Playwright driver node we spawned, or None.

        The driver owns the chrome-headless-shell processes, so terminating its
        tree cleans up every browser this engine started.
        """
        return self._driver_pid

    @property
    def orchestrator(self) -> Any:
        return self._orchestrator

    @property
    def search_client(self) -> Any:
        return self._search_client

    def ensure_search_client(self) -> Any:
        """Apply a newly saved Brave key without restarting the engine."""
        if self._settings is None:
            return self._search_client
        try:
            from app.services.ai.key_manager import get_cached_user_keys

            key = (
                get_cached_user_keys().get("brave", "").strip()
                or os.environ.get("BRAVE_API_KEY", "").strip()
            )
            if key == (self._search_key or ""):
                return self._search_client
            if not key:
                self._search_client = None
                self._search_key = None
                if self._orchestrator is not None:
                    self._orchestrator.search_client = None
                return None
            self._settings.BRAVE_API_KEY = key
            search_mod = _import_scraper("app.core.search")
            self._search_client = search_mod.BraveSearchClient(self._settings)
            self._search_key = key
            if self._orchestrator is not None:
                self._orchestrator.search_client = self._search_client
            logger.info("[scraper/engine.py] Brave Search enabled from user key store")
        except Exception:
            logger.exception("[scraper/engine.py] Could not enable Brave Search")
        return self._search_client

    async def start(self) -> None:
        """Initialize scraper-service components.

        Order: settings → browser pool → fetcher → domain config (stub) →
               page cache (in-memory) → search client → orchestrator.

        No direct database connection. All persistence goes through the
        scraper server API (scraper.app.matrxserver.com). Results are pushed
        via POST /api/v1/content/save after every successful scrape.
        """
        if self._started:
            return

        logger.info("[scraper/engine.py] ScraperEngine: starting")

        config_mod = _import_scraper("app.config")
        settings_cls = config_mod.Settings

        # The scraper-service Settings requires API_KEY and DATABASE_URL.
        # We supply stubs — the desktop engine never uses either directly.
        os.environ.setdefault("API_KEY", "local-scraper")
        os.environ.setdefault("DATABASE_URL", "postgresql://stub:stub@localhost/stub")

        try:
            self._settings = settings_cls()  # type: ignore[call-arg]
        except Exception as exc:
            logger.exception(
                "[scraper/engine.py] ScraperEngine: failed to load settings"
            )
            # Re-raise: a silent return here let app/main.py mark the registry
            # READY for a scraper whose orchestrator was never created —
            # every subsequent scrape call would hit None.scrape.
            raise RuntimeError("Scraper settings failed to load") from exc

        try:
            browser_pool_mod = _import_scraper("app.core.fetcher.browser_pool")
            browser_pool = browser_pool_mod.PlaywrightBrowserPool(
                pool_size=self._settings.PLAYWRIGHT_POOL_SIZE,
            )
            await browser_pool.start()
            self._browser_pool = browser_pool
            self._driver_pid = _extract_driver_pid(browser_pool)
            logger.info(
                "[scraper/engine.py] ScraperEngine: browser pool started ✓ (size=%d, driver_pid=%s)",
                self._settings.PLAYWRIGHT_POOL_SIZE,
                self._driver_pid,
            )
        except Exception as pw_exc:
            logger.warning(
                "[scraper/engine.py] ScraperEngine: Playwright browser pool failed — "
                "browser automation will be disabled. Error: %s",
                pw_exc,
            )
            self._browser_pool = None

            # fetcher.py imports browser_pool at module level. If Playwright is not
            # installed, browser_pool.py never loaded into sys.modules, so fetcher.py
            # would fail on its top-level import. Inject a stub module so fetcher.py
            # can import successfully — the stub class is never instantiated.
            import types as _types
            _stub_mod = _types.ModuleType("app.core.fetcher.browser_pool")

            class _StubBrowserPool:
                def __init__(self, **kwargs: object) -> None: pass
                async def start(self) -> None: pass
                async def stop(self) -> None: pass

            _stub_mod.PlaywrightBrowserPool = _StubBrowserPool  # type: ignore[attr-defined]
            # Register under both the original and aliased names so _import_scraper finds it.
            sys.modules["app.core.fetcher.browser_pool"] = _stub_mod
            sys.modules["scraper_app.core.fetcher.browser_pool"] = _stub_mod

        fetcher_mod = _import_scraper("app.core.fetcher.fetcher")
        self._fetcher = fetcher_mod.UnifiedFetcher(
            settings=self._settings,
            browser_pool=self._browser_pool,
        )

        # Domain config: use no-DB stub — config is loaded from server API separately.
        domain_config_mod = _import_scraper("app.domain_config.config_store")
        self._domain_config_store = domain_config_mod.DomainConfigStore.__new__(
            domain_config_mod.DomainConfigStore
        )
        self._domain_config_store._pool = None
        self._domain_config_store._domains = {}
        self._domain_config_store._base_config = []
        self._domain_config_store._refresh_task = None

        # Page cache: in-memory only. Persistence is the server's responsibility.
        self._page_cache = _MemoryOnlyPageCache(
            max_size=self._settings.PAGE_CACHE_MAX_SIZE,
            ttl_seconds=self._settings.PAGE_CACHE_TTL_SECONDS,
        )

        search_mod = _import_scraper("app.core.search")
        if self._settings.BRAVE_API_KEY:
            self._search_client = search_mod.BraveSearchClient(self._settings)
            self._search_key = str(self._settings.BRAVE_API_KEY)
            logger.info("[scraper/engine.py] ScraperEngine: Brave Search configured ✓")
        else:
            logger.info("[scraper/engine.py] ScraperEngine: no BRAVE_API_KEY — search disabled")

        orchestrator_mod = _import_scraper("app.core.orchestrator")

        # Patch out the DB-write helpers so they silently no-op when a fetch
        # fails instead of raising RuntimeError from _NullPool.  The desktop
        # engine never has a real DB connection; failure logging is handled
        # by the remote server after the retry-queue poller reports the result.
        async def _noop_log_failure(*_args: Any, **_kwargs: Any) -> None:
            logger.debug(
                "[scraper/engine.py] log_failure suppressed (no-DB mode): url=%s reason=%s",
                _kwargs.get("target_url", _args[1] if len(_args) > 1 else "?"),
                _kwargs.get("failure_reason", _args[2] if len(_args) > 2 else "?"),
            )

        async def _noop_enqueue_retry(*_args: Any, **_kwargs: Any) -> None:
            logger.debug(
                "[scraper/engine.py] enqueue_retry suppressed (no-DB mode): url=%s",
                _kwargs.get("target_url", _args[1] if len(_args) > 1 else "?"),
            )

        orchestrator_mod.log_failure = _noop_log_failure
        orchestrator_mod.enqueue_retry = _noop_enqueue_retry

        self._orchestrator = orchestrator_mod.ScrapeOrchestrator(
            fetcher=self._fetcher,
            settings=self._settings,
            db_pool=_NullPool(),
            page_cache=self._page_cache,
            domain_config_store=self._domain_config_store,
            search_client=self._search_client,
        )

        self._started = True
        logger.info(
            "[scraper/engine.py] ScraperEngine: ready ✓ (browser=%s, search=%s)",
            self._browser_pool is not None,
            self._search_client is not None,
        )

    async def stop(self) -> None:
        if not self._started:
            return

        logger.info("[scraper/engine.py] ScraperEngine: stopping")

        if self._browser_pool:
            # When SIGINT fires, Playwright's driver subprocess is killed before we
            # reach shutdown. Subsequent browser.close() calls all raise
            # "Connection closed while reading from the driver" — this is expected
            # and harmless. Suppress the browser_pool logger temporarily so these
            # known-benign errors don't flood the console as full tracebacks.
            _bp_logger = logging.getLogger("scraper_app.core.fetcher.browser_pool")
            _orig_level = _bp_logger.level
            _bp_logger.setLevel(logging.CRITICAL)
            try:
                import asyncio as _asyncio
                await _asyncio.wait_for(self._browser_pool.stop(), timeout=5.0)
            except _asyncio.TimeoutError:
                logger.debug("[scraper/engine.py] ScraperEngine: browser pool stop timed out (expected on SIGINT)")
            except Exception:
                logger.debug("[scraper/engine.py] ScraperEngine: browser pool stop failed (expected on SIGINT)")
            finally:
                _bp_logger.setLevel(_orig_level)

        self._started = False
        self._driver_pid = None
        logger.info("[scraper/engine.py] ScraperEngine: stopped")


class _MemoryOnlyPageCache:
    """Drop-in replacement for PageCache when no database is available.

    Uses only the in-memory TTLCache — no persistence.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 1800) -> None:
        from cachetools import TTLCache

        self._memory: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=max_size, ttl=ttl_seconds)

    async def get(self, page_name: str) -> Optional[dict[str, Any]]:
        return self._memory.get(page_name)

    async def set(
        self,
        page_name: str,
        url: str,
        domain: str,
        content: dict[str, Any],
        content_type: str,
        char_count: int,
        ttl_days: int = 30,
    ) -> None:
        from datetime import datetime, timezone

        self._memory[page_name] = {
            "content": content,
            "url": url,
            "domain": domain,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "content_type": content_type,
            "char_count": char_count,
        }

    async def invalidate(self, page_name: str) -> None:
        self._memory.pop(page_name, None)


class _NullConnection:
    """Stub connection that raises on any DB operation."""

    async def fetchrow(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("No database connection — scraper running in memory-only mode")

    async def fetch(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("No database connection — scraper running in memory-only mode")

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("No database connection — scraper running in memory-only mode")

    async def executemany(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("No database connection — scraper running in memory-only mode")


class _NullAcquireContext:
    """Async context manager returned by _NullPool.acquire().

    asyncpg Pool.acquire() is used as ``async with pool.acquire() as conn:``,
    so it must return an async context manager, not a bare coroutine.
    """

    async def __aenter__(self) -> "_NullConnection":
        return _NullConnection()

    async def __aexit__(self, *args: Any) -> None:
        pass


class _NullPool:
    """Stub that satisfies the asyncpg.Pool type hint when no DB is available.

    Any actual DB operation will fail with a clear message.
    acquire() returns an async context manager so callers can use
    ``async with pool.acquire() as conn:`` without a TypeError.
    """

    def acquire(self) -> _NullAcquireContext:
        return _NullAcquireContext()

    async def close(self) -> None:
        pass


_engine: Optional[ScraperEngine] = None


def get_scraper_engine() -> ScraperEngine:
    """Get the singleton ScraperEngine instance."""
    global _engine
    if _engine is None:
        _engine = ScraperEngine()
    return _engine
