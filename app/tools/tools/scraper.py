"""Local scraper tools.

These tools scrape URLs using the local ScraperEngine (user's residential IP)
and immediately push every successful result to the scraper server via
POST /api/scraper/content/save so the web app and all devices see the result
instantly.

Failed scrapes are reported back to the caller — they are NOT automatically
added to the server retry queue (the server does that for its own failed
scrapes; local failures are surfaced to the user directly).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _save_to_server(
    url: str,
    content: dict[str, Any],
    char_count: int,
    auth_token: str | None,
) -> None:
    """Push scraped content to the server database. Errors are logged, not raised."""
    try:
        from app.services.scraper.remote_client import get_remote_scraper
        client = get_remote_scraper()
        await client.save_content(
            url=url,
            content=content,
            content_type="html",
            char_count=char_count,
            auth_token=auth_token,
        )
        logger.info("[scraper.py] Saved to server: %s (%d chars)", url, char_count)
    except Exception as exc:
        logger.warning("[scraper.py] Failed to save to server for %s: %s", url, exc)


async def local_scrape(
    urls: list[str],
    use_cache: bool = True,
    auth_token: str | None = None,
) -> dict[str, Any]:
    """Scrape URLs locally and immediately push results to the server database.

    Args:
        urls: List of URLs to scrape.
        use_cache: Whether to use the local in-memory session cache.
        auth_token: User's Supabase JWT — forwarded to the server for save.

    Returns:
        Dict with keys:
          - results: list of per-URL result dicts
          - saved: number of pages successfully saved to the server
          - failed: list of URLs that could not be scraped
    """
    from matrx_scraper.scrape_options import ScrapeOptions

    from app.services.scraper.engine import LocalScrapeOptions, get_scraper_engine
    from app.services.scraper.scrape_store import content_from_result

    engine = get_scraper_engine()
    if not engine.is_ready:
        return {
            "results": [],
            "saved": 0,
            "failed": urls,
            "error": "Local scraper engine is not ready",
        }

    try:
        pages = await engine.scrape(
            urls,
            LocalScrapeOptions(
                fields=ScrapeOptions(
                    get_text_data=True, get_links=True, get_overview=True
                ),
                use_cache=use_cache,
            ),
        )
    except Exception as exc:
        logger.error("[scraper.py] Local scrape failed: %s", exc, exc_info=True)
        return {
            "results": [],
            "saved": 0,
            "failed": urls,
            "error": str(exc),
        }

    results = []
    saved = 0
    failed = []

    for page in pages:
        url = page.url
        if page.success:
            content = content_from_result(page)
            char_count = len(
                (content.get("text_data") or "") + (content.get("ai_research_content") or "")
            )
            await _save_to_server(url, content, char_count, auth_token)
            saved += 1
            results.append({
                "url": url,
                "status": "success",
                "char_count": char_count,
                "saved_to_server": True,
                "content": content,
            })
        else:
            failed.append(url)
            results.append({
                "url": url,
                "status": "failed",
                "error": page.failure_reason or "Scrape returned no content",
                "saved_to_server": False,
            })

    logger.info(
        "[scraper.py] local_scrape: %d succeeded (%d saved), %d failed",
        saved, saved, len(failed),
    )
    return {
        "results": results,
        "saved": saved,
        "failed": failed,
    }


# ── Tool registry wrappers ────────────────────────────────────────────────────
# These match the pattern used by other tools in this directory.

TOOL_DEFINITIONS = [
    {
        "name": "LocalScrape",
        "description": (
            "Scrape one or more URLs using the user's local computer and residential IP address. "
            "Useful for sites that block cloud servers (Cloudflare, paywalls, geo-restricted). "
            "Every successful result is immediately saved to the shared server database so the "
            "web app and all other devices see it instantly. Returns scraped content and a count "
            "of how many pages were saved."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of URLs to scrape. Max 20 per call.",
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Use in-memory session cache to skip URLs scraped recently. Default true.",
                    "default": True,
                },
            },
            "required": ["urls"],
        },
    },
]


async def execute_tool(name: str, params: dict[str, Any], auth_token: str | None = None) -> dict[str, Any]:
    """Entry point called by the tool registry."""
    if name == "LocalScrape":
        urls = params.get("urls", [])
        if not urls:
            return {"error": "urls parameter is required"}
        if len(urls) > 20:
            return {"error": "Maximum 20 URLs per LocalScrape call"}
        use_cache = params.get("use_cache", True)
        return await local_scrape(urls=urls, use_cache=use_cache, auth_token=auth_token)

    return {"error": f"Unknown tool: {name}"}
