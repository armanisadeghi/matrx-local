from __future__ import annotations

import asyncio
import logging
from typing import Optional

from playwright.async_api import Browser, async_playwright, Playwright

logger = logging.getLogger(__name__)


class PlaywrightBrowserPool:
    def __init__(self, pool_size: int = 3) -> None:
        self._pool_size = pool_size
        self._queue: asyncio.Queue[Browser] = asyncio.Queue()
        self._playwright: Optional[Playwright] = None
        self._browsers: list[Browser] = []

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        for i in range(self._pool_size):
            browser = await self._playwright.chromium.launch(headless=True)
            self._browsers.append(browser)
            self._queue.put_nowait(browser)
        logger.info("PlaywrightBrowserPool started with %d browsers", self._pool_size)

    async def stop(self) -> None:
        for browser in self._browsers:
            try:
                await browser.close()
            except Exception as exc:
                # Playwright may close the shared driver before individual
                # Browser proxies during app teardown. The browsers are
                # already gone in that case; reporting three red tracebacks
                # turns a clean shutdown into a false incident.
                if "Connection closed while reading from the driver" in str(exc):
                    logger.debug("Browser driver already closed during shutdown")
                else:
                    logger.exception("Error closing browser")
        self._browsers.clear()

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("PlaywrightBrowserPool stopped")

    async def acquire(self, timeout: float = 30.0) -> Browser:
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    def release(self, browser: Browser) -> None:
        self._queue.put_nowait(browser)

    async def fetch(
        self,
        url: str,
        proxy: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> tuple[str, str, int, dict[str, str], str]:
        """Returns (content, response_url, status_code, headers, title)."""
        browser = await self.acquire()
        try:
            context_kwargs: dict = {}
            if proxy:
                context_kwargs["proxy"] = {"server": proxy}

            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()

            try:
                resp = await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                content = await page.content()
                title = await page.title()
                response_url = page.url
                status_code = resp.status if resp else 500
                headers = await resp.all_headers() if resp else {}
            finally:
                await page.close()
                await context.close()

            return content, response_url, status_code, headers, title
        finally:
            self.release(browser)

    @property
    def size(self) -> int:
        return self._pool_size

    @property
    def available(self) -> int:
        return self._queue.qsize()
