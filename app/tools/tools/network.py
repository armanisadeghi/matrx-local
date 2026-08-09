"""Network tools — HTTP fetch, headless browser fetch, and scraper-engine tools.

Simple tools (FetchUrl, FetchWithBrowser) use httpx/Playwright directly for
quick requests from the user's residential IP.

Advanced tools (Scrape, Search, Research) run the canonical `matrx_scraper`
engine through the local lane (`app/services/scraper/engine.py`): browser
impersonation with a Playwright fallback, Cloudflare/firewall detection,
HTML/PDF/image/JSON extraction, and a session cache — all from this machine's
own IP, never a proxy.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType
from app.services.action_needed import ActionNeeded, ActionNeededAction, ActionNeededKind

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
    if blocked := _check_forbidden(url):
        return blocked
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "Browser fetch requires playwright. Install with:\n"
                "  uv add playwright\n"
                "  playwright install chromium"
            ),
        )

    # Read headless setting from engine settings
    try:
        from app.services.cloud_sync.settings_sync import get_settings_sync
        _headless = get_settings_sync().get("headless_scraping", True)
    except Exception:
        _headless = True

    start = time.monotonic()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=_headless)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
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

            await browser.close()
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

    return ToolResult(
        output="\n".join(output_parts),
        metadata={
            "status_code": status,
            "url": final_url,
            "elapsed_ms": elapsed_ms,
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


# ---------------------------------------------------------------------------
# Scrape metadata payload budget
# ---------------------------------------------------------------------------
# Every scrape ALREADY extracts an outline, tables, media, code blocks and page
# metadata — the cost is paid during the parse whether or not anyone reads it.
# What is NOT free is shipping it: this metadata crosses the tool envelope into
# the desktop UI (and into a cloud agent's context), so each list is capped and
# any truncation is REPORTED rather than silently swallowed. The UI states
# "showing N of M"; a cap that lies is worse than no cap.
#
# Deliberately NOT forwarded, and why:
#   organized_data                — the whole parse tree; megabytes, and every
#                                   consumable view of it is already forwarded.
#   ai_content / ai_research_*    — prose variants of `output`; pure duplication.
#   markdown_renderable_by_header — the same markdown a second time, re-sliced.
#                                   The outline + `markdown_renderable` give the
#                                   UI section navigation without paying twice.
#   link_records                  — up to ~2000 anchor rows; the 8 URL buckets
#                                   are what a link list renders.
#   raw_html / raw_body           — the page source, never a UI surface here.
MAX_OUTLINE_HEADERS = 400
MAX_TABLES = 25
MAX_TABLE_ROWS = 250
MAX_IMAGES = 150
MAX_AV_ITEMS = 50
MAX_CODE_BLOCKS = 50
MAX_CODE_BLOCK_CHARS = 20_000
MAX_LINKS_PER_BUCKET = 500
MAX_MARKDOWN_CHARS = 400_000


def _cap(items: Any, limit: int) -> tuple[list[Any], bool]:
    """Return (at most `limit` items, whether anything was dropped)."""
    if not isinstance(items, list):
        return [], False
    if len(items) <= limit:
        return items, False
    return items[:limit], True


def _cap_tables(tables: Any) -> tuple[list[Any], bool]:
    """Cap the table count AND each table's row count.

    A single 5000-row table is as expensive as fifty small ones, so both
    dimensions are bounded. Row truncation is recorded on the table itself
    (`rows_total`) so the UI can say which table is partial.
    """
    capped, truncated = _cap(tables, MAX_TABLES)
    out: list[Any] = []
    for table in capped:
        if not isinstance(table, dict):
            continue
        rows = table.get("rows")
        if isinstance(rows, list) and len(rows) > MAX_TABLE_ROWS:
            out.append({**table, "rows": rows[:MAX_TABLE_ROWS], "rows_total": len(rows)})
            truncated = True
        else:
            out.append(
                {**table, "rows_total": len(rows) if isinstance(rows, list) else 0}
            )
    return out, truncated


def _cap_code_blocks(blocks: Any) -> tuple[list[Any], bool]:
    capped, truncated = _cap(blocks, MAX_CODE_BLOCKS)
    out: list[Any] = []
    for block in capped:
        if not isinstance(block, dict):
            continue
        content = block.get("content")
        if isinstance(content, str) and len(content) > MAX_CODE_BLOCK_CHARS:
            out.append({**block, "content": content[:MAX_CODE_BLOCK_CHARS], "truncated": True})
            truncated = True
        else:
            out.append(block)
    return out, truncated


def _cap_links(links: Any) -> tuple[dict[str, list[str]], dict[str, int], bool]:
    """Cap each URL bucket, preserving the TRUE per-bucket totals.

    The bucket shape (`{bucket: [url, ...]}`) is frozen — hosts across the
    platform read it. Totals travel beside it in `link_counts` so a capped
    bucket never reads as "this page has 500 internal links".
    """
    if not isinstance(links, dict):
        return {}, {}, False
    buckets: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    truncated = False
    for bucket, urls in links.items():
        if not isinstance(urls, list):
            continue
        counts[str(bucket)] = len(urls)
        if len(urls) > MAX_LINKS_PER_BUCKET:
            truncated = True
        buckets[str(bucket)] = [str(u) for u in urls[:MAX_LINKS_PER_BUCKET]]
    return buckets, counts, truncated


def _scrape_result_to_metadata(
    result: Any,
    *,
    include_extraction: bool = False,
    include_links: bool = False,
    include_overview: bool = False,
) -> dict[str, Any]:
    """Extract metadata from a matrx_scraper ScrapeResult.

    Keys are additive and stable: `status`, `url`, `status_code`,
    `content_type`, `title`, `cms`, `firewall`, `overview`, `links` and `error`
    have carried the same meaning since the fork was deleted; everything under
    `include_extraction` was added 2026-08-09 so the desktop can show what the
    parse already found. A field absent from the result is absent from the
    payload — a consumer must treat "missing" as "this page had none", which is
    exactly how a PDF or JSON scrape (no outline, no tables) reports itself.

    Every block is opt-in because this metadata is not UI-only: the local tool
    bridge hands it to the model alongside the tool output, so an unconditional
    table set and link graph would land in an agent's context on every scrape.
    `include_links` / `include_overview` are the tool's long-standing
    `get_links` / `get_overview` flags — which until now were declared and then
    ignored here, so both blocks shipped on every call regardless.
    """
    meta: dict[str, Any] = {
        # The UI reads `status`; the package reports success as a bool. Map it
        # here, in ONE place, rather than teaching every consumer both shapes.
        "status": "success" if result.success else "error",
        "url": result.url,
    }
    if result.status_code is not None:
        meta["status_code"] = result.status_code
    if result.content_type:
        meta["content_type"] = result.content_type
    if result.title:
        meta["title"] = result.title
    if result.cms:
        meta["cms"] = result.cms
    if result.firewall:
        meta["firewall"] = result.firewall
    if result.failure_reason:
        meta["error"] = result.failure_reason

    if include_overview and result.overview:
        meta["overview"] = result.overview

    truncated: dict[str, bool] = {}

    if include_links:
        links, link_counts, links_truncated = _cap_links(getattr(result, "links", None))
        if links:
            meta["links"] = links
            meta["link_counts"] = link_counts
            truncated["links"] = links_truncated

    if not include_extraction:
        if any(truncated.values()):
            meta["truncated"] = {k: v for k, v in truncated.items() if v}
        return meta

    # ── Added 2026-08-09: the extraction the UI renders ─────────────────────
    if getattr(result, "response_url", None):
        meta["response_url"] = result.response_url
    for field_name in ("scraped_at", "published_at", "modified_at", "main_image"):
        value = getattr(result, field_name, None)
        if value:
            meta[field_name] = value

    outline, outline_truncated = _cap(
        getattr(result, "document_outline", None), MAX_OUTLINE_HEADERS
    )
    if outline:
        meta["document_outline"] = outline
        truncated["document_outline"] = outline_truncated

    tables, tables_truncated = _cap_tables(getattr(result, "tables", None))
    if tables:
        meta["tables"] = tables
        truncated["tables"] = tables_truncated

    images, images_truncated = _cap(getattr(result, "images", None), MAX_IMAGES)
    if images:
        meta["images"] = images
        truncated["images"] = images_truncated

    for field_name in ("videos", "audios"):
        items, items_truncated = _cap(getattr(result, field_name, None), MAX_AV_ITEMS)
        if items:
            meta[field_name] = items
            truncated[field_name] = items_truncated

    code_blocks, code_truncated = _cap_code_blocks(getattr(result, "code_blocks", None))
    if code_blocks:
        meta["code_blocks"] = code_blocks
        truncated["code_blocks"] = code_truncated

    markdown = getattr(result, "markdown_renderable", None)
    if isinstance(markdown, str) and markdown.strip():
        if len(markdown) > MAX_MARKDOWN_CHARS:
            meta["markdown_renderable"] = markdown[:MAX_MARKDOWN_CHARS]
            truncated["markdown_renderable"] = True
        else:
            meta["markdown_renderable"] = markdown
            truncated["markdown_renderable"] = False

    # `metadata` is the page's own head metadata (json-ld / opengraph /
    # meta_tags / canonical_url). It is also nested inside `overview`, but
    # `overview` is only produced when the caller asks for it, so the UI's
    # metadata panel must not depend on that flag.
    page_metadata = getattr(result, "metadata", None)
    if isinstance(page_metadata, dict) and page_metadata:
        meta["page_metadata"] = page_metadata

    redirect_chain = getattr(result, "redirect_chain", None)
    if isinstance(redirect_chain, list) and redirect_chain:
        meta["redirect_chain"] = redirect_chain

    hashes = getattr(result, "hashes", None)
    if isinstance(hashes, dict) and hashes:
        meta["hashes"] = hashes

    if any(truncated.values()):
        meta["truncated"] = {k: v for k, v in truncated.items() if v}

    return meta


async def tool_scrape(
    session: ToolSession,
    urls: list[str],
    use_cache: bool = True,
    output_mode: str = "rich",
    get_links: bool = False,
    get_overview: bool = False,
    get_extraction: bool = False,
) -> ToolResult:
    """Scrape one or more URLs from THIS machine, on the user's own IP.

    Runs the canonical `matrx_scraper` engine in the local lane: curl_cffi
    browser impersonation with a Playwright fallback, Cloudflare/firewall
    detection, HTML/PDF/image/JSON extraction, and a session cache. Never uses
    a proxy — the point of scraping here rather than on the server is the
    user's residential IP.

    `get_extraction=True` returns the structured extraction the parse already
    produced — heading outline, tables as rows, images/videos/audios, code
    blocks, renderable markdown, head metadata, redirect chain — in the result
    metadata. The Scraping page asks for it; a prose consumer should not.

    `output_mode="research"` trims the payload to prose only (no links,
    overview or organized data), which is what a research pass consumes.
    """
    call_start = time.monotonic()
    logger.info(
        "[tool_scrape] START — urls=%s use_cache=%s output_mode=%s get_extraction=%s",
        urls, use_cache, output_mode, get_extraction,
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
    want_links = get_links and not research_mode
    want_overview = get_overview and not research_mode
    want_extraction = get_extraction and not research_mode
    options = LocalScrapeOptions(
        fields=ScrapeOptions(
            get_text_data=True,
            get_links=want_links,
            get_overview=want_overview,
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

    if len(results) == 1:
        r = results[0]
        output = _scrape_result_to_output(r)
        return ToolResult(
            output=output,
            type=ToolResultType.SUCCESS if r.success else ToolResultType.ERROR,
            metadata={
                **_scrape_result_to_metadata(
                    r,
                    include_extraction=want_extraction,
                    include_links=want_links,
                    include_overview=want_overview,
                ),
                "elapsed_ms": elapsed_ms,
            },
        )

    output_parts = [f"Scraped {len(results)} URLs in {elapsed_ms}ms\n"]
    success_count = sum(1 for r in results if r.success)
    output_parts.append(f"Success: {success_count}/{len(results)}\n")

    for i, r in enumerate(results, 1):
        output_parts.append(f"--- Result {i}/{len(results)} ---")
        output_parts.append(_scrape_result_to_output(r))
        output_parts.append("")

    all_meta = [
        _scrape_result_to_metadata(
            r,
            include_extraction=want_extraction,
            include_links=want_links,
            include_overview=want_overview,
        )
        for r in results
    ]

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
