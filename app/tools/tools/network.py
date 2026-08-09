"""Network tools — HTTP fetch, headless browser fetch, and scraper-engine tools.

`FetchUrl` is a plain httpx request from the user's residential IP.

`FetchWithBrowser` renders one page in a real browser and hands back raw HTML
or body text — no parsing, caller-controlled waits. It is the deliberately
LIGHT browser path; the parsing one is `Scrape`.

Advanced tools (Scrape, Search, Research) run the canonical `matrx_scraper`
engine through the local lane (`app/services/scraper/engine.py`): browser
impersonation with a Playwright fallback, Cloudflare/firewall detection,
HTML/PDF/image/JSON extraction, and a session cache — all from this machine's
own IP, never a proxy.

**There is ONE browser.** Both browser paths borrow the single Playwright pool
owned by `ScraperEngine` (`borrow_browser`), so this process holds one driver
tree, tracked by one `driver_pid` and reaped by one owner. Nothing in this
module may call `async_playwright()`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType
from app.services.action_needed import ActionNeeded, ActionNeededAction, ActionNeededKind
from app.services.scraper.result_contract import from_page_dict, from_scrape_result

logger = logging.getLogger(__name__)


def _brave_key_needed(feature: str, *, invalid: bool = False) -> ActionNeeded:
    return ActionNeeded(
        fingerprint="api-key:brave",
        code="api_key_invalid" if invalid else "api_key_missing",
        kind=ActionNeededKind.API_KEY,
        feature=feature,
        title="Update your Brave Search API key" if invalid else "Add your Brave Search API key",
        message=(
            "Brave Search rejected the saved key. Replace it in Settings."
            if invalid
            else "Web search needs a Brave Search key saved in Settings."
        ),
        action=ActionNeededAction(
            kind="settings_api_keys",
            label="Update API key" if invalid else "Add API key",
            provider="brave",
            route="/settings?tab=api-keys&provider=brave",
        ),
        source="tools.network",
    )


def _check_forbidden(url: str) -> ToolResult | None:
    """Return an error ToolResult if ``url`` is on the forbidden list, else None."""
    try:
        from app.api.settings_routes import is_url_forbidden
        if is_url_forbidden(url):
            return ToolResult(
                type=ToolResultType.ERROR,
                output=f"URL is blocked by the forbidden URL list: {url}",
            )
    except Exception:
        pass
    return None

MAX_RESPONSE_SIZE = 500_000
DEFAULT_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Simple tools (original — direct HTTP/browser)
# ---------------------------------------------------------------------------

async def tool_fetch_url(
    session: ToolSession,
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    follow_redirects: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
) -> ToolResult:
    if blocked := _check_forbidden(url):
        return blocked
    try:
        import httpx
    except ImportError:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="HTTP fetch requires httpx. Install it with: uv add httpx",
        )

    req_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        req_headers.update(headers)

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            follow_redirects=follow_redirects,
            timeout=httpx.Timeout(timeout),
        ) as client:
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=req_headers,
                content=body.encode("utf-8") if body else None,
            )
    except httpx.TimeoutException:
        return ToolResult(type=ToolResultType.ERROR, output=f"Request timed out after {timeout}s")
    except httpx.RequestError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Request failed: {type(e).__name__}: {e}")

    elapsed_ms = int((time.monotonic() - start) * 1000)

    body_text = response.text
    if len(body_text) > MAX_RESPONSE_SIZE:
        body_text = body_text[:MAX_RESPONSE_SIZE] + "\n\n... [truncated at 500KB]"

    resp_headers = dict(response.headers)

    output_parts = [
        f"HTTP {response.status_code} {response.reason_phrase}",
        f"URL: {response.url}",
        f"Time: {elapsed_ms}ms",
        f"Content-Type: {response.headers.get('content-type', 'unknown')}",
        f"Content-Length: {len(response.content)} bytes",
        "",
        body_text,
    ]

    return ToolResult(
        output="\n".join(output_parts),
        metadata={
            "status_code": response.status_code,
            "headers": resp_headers,
            "url": str(response.url),
            "elapsed_ms": elapsed_ms,
            "content_length": len(response.content),
        },
    )


async def tool_fetch_with_browser(
    session: ToolSession,
    url: str,
    wait_for: str | None = None,
    wait_timeout: int = 30000,
    extract_text: bool = False,
) -> ToolResult:
    """Render one page in a real browser and return its HTML or text.

    The navigation semantics here are this tool's own and deliberately differ
    from the scraper lane's: a caller-supplied `wait_for` selector, a
    `networkidle` settle when there is none, a fixed desktop UA + 1920x1080
    viewport, and raw HTML (or `body` inner text) with NO parsing. The scraper
    lane instead runs the full matrx_scraper pipeline and returns a parsed
    `ScrapeResult`.

    What it does NOT own is the browser. Until 2026-08-09 this called
    `async_playwright()` itself, so the engine could hold TWO driver trees at
    once and only one of them was tracked for reaping. The browser now comes
    from the single pool owned by `ScraperEngine` (`borrow_browser`), which
    keeps one `driver_pid` and one lifecycle owner.
    """
    if blocked := _check_forbidden(url):
        return blocked
    # A missing browser is a STATE the user can fix in one click, not a
    # developer error telling them to run a shell command they will never run.
    from app.services.scraper import browser_runtime

    if (needed := browser_runtime.browser_action_needed("browser page fetching")) is not None:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"{needed.title}. {needed.message}",
            action_needed=needed,
        )

    # Read headless setting from engine settings
    try:
        from app.services.cloud_sync.settings_sync import get_settings_sync
        _headless = get_settings_sync().get("headless_scraping", True)
    except Exception:
        _headless = True

    from app.services.scraper.engine import BrowserUnavailable

    start = time.monotonic()
    try:
        # The pool holds ONE browser, so a concurrent scrape can be using it.
        # Wait at least as long as this fetch is itself allowed to take rather
        # than failing a healthy request on a fixed queue timeout.
        borrow_timeout = max(30.0, wait_timeout / 1000 + 15.0)
        async with _get_engine().borrow_browser(
            headless=_headless, timeout=borrow_timeout
        ) as browser:
            # The borrowed browser is shared and long-lived: close the context
            # we opened, never the browser.
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
            try:
                page = await context.new_page()

                response = await page.goto(url, wait_until="domcontentloaded", timeout=wait_timeout)

                if wait_for:
                    await page.wait_for_selector(wait_for, timeout=wait_timeout)
                else:
                    await page.wait_for_load_state("networkidle", timeout=wait_timeout)

                if extract_text:
                    content = await page.inner_text("body")
                else:
                    content = await page.content()

                status = response.status if response else 0
                final_url = page.url
            finally:
                await context.close()
    except BrowserUnavailable as e:
        # A STATE with a one-click remedy, not a failure to shout about: the
        # dispatcher turns `fix_capability_id` into the canonical ActionNeeded.
        logger.info("[tool_fetch_with_browser] no browser available: %s", e)
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                f"{e} Install the Browser Automation capability "
                "(Settings → Capabilities) and try again."
            ),
            metadata={"fix_capability_id": "browser_automation"},
        )
    except asyncio.TimeoutError:
        # Only the pool's `acquire` raises this — Playwright's own page
        # timeouts are a different class. Say WHICH wait expired; "Browser
        # fetch failed: TimeoutError:" told the user nothing.
        logger.warning(
            "[tool_fetch_with_browser] waited %.0fs for the shared browser and gave up (url=%s)",
            borrow_timeout, url,
        )
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                f"The browser was busy with another page for {borrow_timeout:.0f}s. "
                "Try again in a moment."
            ),
        )
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Browser fetch failed: {type(e).__name__}: {e}")

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if len(content) > MAX_RESPONSE_SIZE:
        content = content[:MAX_RESPONSE_SIZE] + "\n\n... [truncated at 500KB]"

    output_parts = [
        f"HTTP {status}",
        f"URL: {final_url}",
        f"Time: {elapsed_ms}ms",
        f"Mode: {'text extraction' if extract_text else 'full HTML'}",
        "",
        content,
    ]

    # Same client contract the Scrape tool emits — a browser fetch is just
    # another lane, and a client must not need a second reader for it.
    page = from_page_dict(
        {
            "url": url,
            "response_url": final_url,
            "success": True,
            "status_code": status,
            "content_type": "text/html",
            "text_data": content,
        },
        elapsed_ms=elapsed_ms,
    )

    return ToolResult(
        output="\n".join(output_parts),
        metadata={
            **page,
            "results": [page],
            "total": 1,
            "success_count": 1,
            "content_length": len(content),
        },
    )


# ---------------------------------------------------------------------------
# Advanced tools (scraper-engine powered)
# ---------------------------------------------------------------------------

def _get_engine() -> Any:
    from app.services.scraper.engine import get_scraper_engine
    return get_scraper_engine()


async def _persist_research_pages(events: list[Any], user_id: str = "") -> None:
    """Persist pages collected during a research run."""
    try:
        from app.services.scraper.scrape_store import save_scrape
        for event in events:
            url: str = getattr(event, "url", "") or ""
            if not url:
                continue
            scraped = getattr(event, "scraped_content", None)
            if not scraped:
                continue
            # `scraped_content` is the page's prose, a plain string. It used to
            # be read with getattr(scraped, "text_data") — attribute access on
            # a str, which always fell through to "" and persisted every
            # research page EMPTY.
            content: dict[str, Any] = {
                "text_data": scraped,
                "ai_research_content": scraped,
                "title": getattr(event, "title", "") or "",
            }
            try:
                await save_scrape(url=url, content=content, content_type="html", user_id=user_id)
            except Exception as inner_exc:
                logger.error("[network] Failed to persist research page %s: %s", url, inner_exc)
    except Exception as exc:
        logger.error("[network] _persist_research_pages raised: %s", exc, exc_info=True)


async def _persist_scrape_results(results: list[Any], user_id: str = "") -> None:
    """Dual-write successful scrape results to local SQLite + cloud.

    Called after every scrape regardless of batch size.  Failures here are
    logged but never raise — the caller already has the results it needs.
    """
    try:
        from app.services.scraper.scrape_store import content_from_result, save_scrape

        for r in results:
            if not getattr(r, "success", False):
                continue
            url: str = getattr(r, "url", "") or ""
            if not url:
                continue
            content_type: str = getattr(r, "content_type", "html") or "html"
            try:
                await save_scrape(
                    url=url,
                    content=content_from_result(r),
                    content_type=content_type,
                    user_id=user_id,
                )
            except Exception as inner_exc:
                logger.error("[network] Failed to persist scrape for %s: %s", url, inner_exc)
    except Exception as exc:
        logger.error("[network] _persist_scrape_results raised: %s", exc, exc_info=True)


def _scrape_result_to_output(result: Any) -> str:
    """Format a matrx_scraper ScrapeResult into a readable string."""
    parts: list[str] = []

    if not result.success:
        parts.append(f"SCRAPE ERROR: {result.failure_reason or 'unknown'}")
        parts.append(f"URL: {result.url}")
        if result.status_code:
            parts.append(f"Status: {result.status_code}")
        if result.firewall:
            parts.append(f"Firewall: {result.firewall}")
        return "\n".join(parts)

    parts.append(f"URL: {result.url}")
    if result.status_code:
        parts.append(f"Status: {result.status_code}")
    if result.content_type:
        parts.append(f"Content-Type: {result.content_type}")
    if result.scraped_at:
        parts.append(f"Scraped: {result.scraped_at}")
    if result.title:
        parts.append(f"Title: {result.title}")
    if result.cms:
        parts.append(f"CMS: {result.cms}")
    if result.firewall and result.firewall != "none":
        parts.append(f"Firewall: {result.firewall}")

    parts.append("")

    # `raw_text` carries PDF/image/JSON extractions — a non-HTML scrape has no
    # text_data at all, and printing "(no text content extracted)" for a PDF we
    # successfully OCR'd is a lie the fork used to tell.
    text = (
        result.ai_research_content
        or result.text_data
        or result.raw_text
        or "(no text content extracted)"
    )

    if len(text) > MAX_RESPONSE_SIZE:
        text = text[:MAX_RESPONSE_SIZE] + "\n\n... [truncated at 500KB]"
    parts.append(text)

    return "\n".join(parts)


def _scrape_result_to_metadata(result: Any, elapsed_ms: int | None = None) -> dict[str, Any]:
    """Client-shaped metadata for one matrx_scraper ScrapeResult.

    Thin delegate: the shape lives in `app/services/scraper/result_contract`,
    which the remote proxy uses too, so local and remote results are the same
    payload. The old `status`/`error` shim that translated the package's
    `success`/`failure_reason` back down for a legacy UI is gone — the client
    now speaks the package contract.
    """
    return from_scrape_result(result, elapsed_ms=elapsed_ms)


async def tool_scrape(
    session: ToolSession,
    urls: list[str],
    use_cache: bool = True,
    output_mode: str = "rich",
    get_links: bool = False,
    get_overview: bool = False,
) -> ToolResult:
    """Scrape one or more URLs from THIS machine, on the user's own IP.

    Runs the canonical `matrx_scraper` engine in the local lane: curl_cffi
    browser impersonation with a Playwright fallback, Cloudflare/firewall
    detection, HTML/PDF/image/JSON extraction, and a session cache. Never uses
    a proxy — the point of scraping here rather than on the server is the
    user's residential IP.

    `output_mode="research"` trims the payload to prose only (no links,
    overview or organized data), which is what a research pass consumes.
    """
    call_start = time.monotonic()
    logger.info(
        "[tool_scrape] START — urls=%s use_cache=%s output_mode=%s",
        urls, use_cache, output_mode,
    )

    blocked_urls = [u for u in urls if _check_forbidden(u)]
    if blocked_urls:
        logger.warning("[tool_scrape] BLOCKED — forbidden URLs: %s", blocked_urls)
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"The following URLs are blocked by the forbidden URL list: {', '.join(blocked_urls)}",
        )

    engine = _get_engine()
    if not engine.is_ready:
        logger.error(
            "[tool_scrape] FAILED — scraper engine not ready. "
            "Check engine startup logs for initialization errors. is_started=%s",
            getattr(engine, "_started", "?"),
        )
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Scraper engine not initialized. Check logs for startup errors.",
        )

    from matrx_scraper.scrape_options import ScrapeOptions

    from app.services.scraper.engine import LocalScrapeOptions

    research_mode = output_mode == "research"
    options = LocalScrapeOptions(
        fields=ScrapeOptions(
            get_text_data=True,
            get_links=get_links and not research_mode,
            get_overview=get_overview and not research_mode,
        ),
        use_cache=use_cache,
    )
    logger.info("[tool_scrape] Options: %s", options)

    start = time.monotonic()
    logger.info("[tool_scrape] Scraping %d URL(s) locally...", len(urls))
    try:
        results = await engine.scrape(urls, options)
    except Exception as e:
        logger.error(
            "[tool_scrape] engine.scrape() RAISED after %.1fs — %s: %s",
            time.monotonic() - start, type(e).__name__, e,
            exc_info=True,
            extra={
                "urls": urls,
                "use_cache": use_cache,
                "output_mode": output_mode,
                "elapsed_s": round(time.monotonic() - start, 2),
            },
        )
        return ToolResult(type=ToolResultType.ERROR, output=f"Scrape failed: {type(e).__name__}: {e}")

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "[tool_scrape] engine.scrape() returned %d result(s) in %dms — ok=%d",
        len(results), elapsed_ms, sum(1 for r in results if r.success),
    )
    for i, r in enumerate(results):
        if not r.success:
            logger.warning(
                "[tool_scrape] result[%d] ERROR: url=%s status_code=%s firewall=%s error=%s",
                i, getattr(r, "url", "?"), getattr(r, "status_code", "?"),
                getattr(r, "firewall", "?"), getattr(r, "failure_reason", "?"),
            )
        else:
            logger.info(
                "[tool_scrape] result[%d] OK: url=%s status_code=%s content_type=%s chars=%d",
                i, getattr(r, "url", "?"), getattr(r, "status_code", "?"),
                getattr(r, "content_type", "?"),
                len(getattr(r, "text_data", "") or "") + len(getattr(r, "ai_research_content", "") or ""),
            )

    # Dual-write every successful result: local SQLite + cloud (fire-and-forget).
    # This must happen regardless of how many URLs were scraped.
    user_id = getattr(session, "user_id", "") or ""
    logger.info("[tool_scrape] Persisting results (fire-and-forget cloud push)...")
    await _persist_scrape_results(results, user_id)
    logger.info("[tool_scrape] DONE — total elapsed %dms", int((time.monotonic() - call_start) * 1000))

    all_meta = [_scrape_result_to_metadata(r, elapsed_ms=elapsed_ms) for r in results]
    success_count = sum(1 for r in results if r.success)

    # `results` is present for EVERY call, single or bulk — a client that had
    # to branch on url count to find its result is a client that gets it wrong
    # for one of the two shapes.
    if len(results) == 1:
        r = results[0]
        return ToolResult(
            output=_scrape_result_to_output(r),
            type=ToolResultType.SUCCESS if r.success else ToolResultType.ERROR,
            metadata={
                **all_meta[0],
                "results": all_meta,
                "total": 1,
                "success_count": success_count,
            },
        )

    output_parts = [f"Scraped {len(results)} URLs in {elapsed_ms}ms\n"]
    output_parts.append(f"Success: {success_count}/{len(results)}\n")

    for i, r in enumerate(results, 1):
        output_parts.append(f"--- Result {i}/{len(results)} ---")
        output_parts.append(_scrape_result_to_output(r))
        output_parts.append("")

    return ToolResult(
        output="\n".join(output_parts),
        metadata={
            "results": all_meta,
            "total": len(results),
            "success_count": success_count,
            "elapsed_ms": elapsed_ms,
        },
    )


async def tool_search(
    session: ToolSession,
    keywords: list[str],
    country: str = "us",
    count: int = 10,
    freshness: str | None = None,
) -> ToolResult:
    """Search the web using Brave Search API.

    Returns structured search results with titles, URLs, descriptions, and
    snippets. Requires the user's own Brave key in the in-app key store
    (Settings → API Keys) — a missing key is a STATE with a prompt, not an
    error, so this returns an `action_needed` the UI can act on.
    """
    engine = _get_engine()
    if not engine.is_ready:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Scraper engine not initialized.",
        )

    if not engine.ensure_search_client():
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Search needs a Brave Search API key.",
            action_needed=_brave_key_needed("search"),
        )

    start = time.monotonic()
    all_results: list[dict[str, Any]] = []

    from matrx_scraper.search import async_brave_search

    try:
        for keyword in keywords:
            # async_brave_search owns the 429 back-off ladder and the per-key
            # rate limiter; going straight to the client would skip both.
            search_results = await async_brave_search(
                query=keyword,
                count=min(count, 20),
                country=country,
                extra_snippets=True,
                freshness=freshness,
            )

            if search_results and "web" in search_results and "results" in search_results["web"]:
                for item in search_results["web"]["results"]:
                    all_results.append({
                        "keyword": keyword,
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "description": item.get("description", ""),
                        "age": item.get("age"),
                    })
    except Exception as e:
        logger.exception("Search failed")
        text = str(e).lower()
        invalid = any(token in text for token in ("401", "403", "unauthorized", "forbidden"))
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"Search failed: {type(e).__name__}: {e}",
            action_needed=_brave_key_needed("search", invalid=True) if invalid else None,
        )

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if not all_results:
        return ToolResult(
            output="No search results found.",
            metadata={"elapsed_ms": elapsed_ms, "total": 0},
        )

    output_parts = [f"Found {len(all_results)} results in {elapsed_ms}ms\n"]
    for i, r in enumerate(all_results, 1):
        output_parts.append(f"{i}. {r['title']}")
        output_parts.append(f"   {r['url']}")
        if r["description"]:
            desc = r["description"][:200]
            output_parts.append(f"   {desc}")
        output_parts.append("")

    return ToolResult(
        output="\n".join(output_parts),
        metadata={
            "results": all_results,
            "total": len(all_results),
            "elapsed_ms": elapsed_ms,
        },
    )


async def tool_research(
    session: ToolSession,
    query: str,
    country: str = "us",
    effort: str = "medium",
    freshness: str | None = None,
) -> ToolResult:
    """Deep research: search + scrape all results + compile findings.

    Combines Brave Search with the scraper engine to search for a query,
    scrape all result pages, and return compiled content. Effort levels
    control how many pages to scrape: low=10, medium=25, high=50, extreme=100.
    """
    engine = _get_engine()
    if not engine.is_ready:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Scraper engine not initialized.",
        )

    if not engine.ensure_search_client():
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Research needs a Brave Search API key.",
            action_needed=_brave_key_needed("research"),
        )

    start = time.monotonic()
    pages_scraped = 0
    pages_failed = 0
    all_content: list[str] = []
    scraped_pages: list[Any] = []

    from app.services.scraper.engine import ResearchDoneEvent, ResearchPageEvent

    try:
        async for event in engine.research(
            query=query,
            country=country,
            effort=effort,
            freshness=freshness,
        ):
            if isinstance(event, ResearchPageEvent):
                if event.scraped_content:
                    pages_scraped += 1
                    scraped_pages.append(event)
                else:
                    pages_failed += 1
            elif isinstance(event, ResearchDoneEvent):
                all_content.append(event.text_content)

    except Exception as e:
        logger.exception("Research failed")
        text = str(e).lower()
        invalid = any(token in text for token in ("401", "403", "unauthorized", "forbidden"))
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"Research failed: {type(e).__name__}: {e}",
            action_needed=_brave_key_needed("research", invalid=True) if invalid else None,
        )

    # Persist every successfully scraped page from the research run
    user_id = getattr(session, "user_id", "") or ""
    if scraped_pages:
        await _persist_research_pages(scraped_pages, user_id)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    compiled = "\n\n".join(all_content)
    if len(compiled) > MAX_RESPONSE_SIZE:
        compiled = compiled[:MAX_RESPONSE_SIZE] + "\n\n... [truncated at 500KB]"

    output_parts = [
        f"Research complete: {query}",
        f"Pages scraped: {pages_scraped} | Failed: {pages_failed}",
        f"Time: {elapsed_ms}ms",
        "",
        compiled,
    ]

    return ToolResult(
        output="\n".join(output_parts),
        metadata={
            "query": query,
            "pages_scraped": pages_scraped,
            "pages_failed": pages_failed,
            "elapsed_ms": elapsed_ms,
            "content_length": len(compiled),
        },
    )
