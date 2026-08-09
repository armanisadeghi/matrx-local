"""Routes that proxy requests to the remote scraper server.

These let the React frontend call the remote scraper server through the
local engine, so all external credentials stay server-side.

Auth: Authenticated users' Supabase JWTs are forwarded directly and accepted
by the scraper server. SCRAPER_API_KEY is only used as a fallback for
unauthenticated requests (server-to-server or dev). All routes work for
authenticated users regardless of whether SCRAPER_API_KEY is set.
"""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.services.scraper.remote_client import get_remote_scraper
from app.services.scraper.result_contract import from_page_dict
from app.common.system_logger import get_logger

router = APIRouter(prefix="/remote-scraper", tags=["remote-scraper"])
logger = get_logger()


def _get_user_token(request: Request) -> str | None:
    return getattr(request.state, "user_token", None)


def _get_client_or_raise():
    """Return the remote scraper client. Always available — server URL is hardcoded."""
    return get_remote_scraper()


class ScrapeRequest(BaseModel):
    urls: list[str]
    options: dict | None = None


class SearchRequest(BaseModel):
    keywords: list[str]
    count: int = 20
    country: str = "US"


class SearchAndScrapeRequest(BaseModel):
    keywords: list[str]
    total_results_per_keyword: int = 10
    options: dict | None = None


class ResearchRequest(BaseModel):
    query: str
    effort: str = "extreme"
    country: str = "US"


class ContentSaveRequest(BaseModel):
    url: str
    content: dict
    content_type: str = "html"
    char_count: int | None = None
    ttl_days: int = 30


@router.get("/status")
async def remote_scraper_status():
    """Check if the remote scraper server is reachable."""
    client = get_remote_scraper()
    try:
        health = await client.health()
        return {"available": True, **health}
    except Exception as e:
        return {"available": False, "reason": str(e)}


# ── Scrape ────────────────────────────────────────────────────────────────────

@router.post("/scrape")
async def remote_scrape(req: ScrapeRequest, request: Request):
    """Scrape URLs via the remote server. Results are stored server-side.

    Results are normalised through `result_contract` — the SAME conversion the
    local `Scrape` tool runs — so a client cannot tell a remote result from a
    local one. The server's own response is untouched; only this proxy's
    payload is shaped.
    """
    client = _get_client_or_raise()
    try:
        resp = await client.scrape(req.urls, req.options, auth_token=_get_user_token(request))
    except Exception as e:
        logger.error("Remote scrape failed: %s", e)
        raise HTTPException(502, f"Remote scraper error: {e}")

    elapsed_ms = int(resp.get("execution_time_ms") or 0)
    pages = [from_page_dict(p, elapsed_ms=elapsed_ms) for p in resp.get("results") or []]
    return {
        "results": pages,
        "total": len(pages),
        "success_count": sum(1 for p in pages if p["success"]),
        "elapsed_ms": elapsed_ms,
    }


async def _scrape_sse(urls: list[str], options: dict | None, auth_token: str | None):
    """Translate the scraper server's NDJSON stream into canonical SSE frames.

    The server streams NDJSON envelopes (`{"event":"data","data":{"type":
    "fetch_results","results":[page,…]}}`) — not SSE. Forwarding those raw
    under a `text/event-stream` content type gave the browser lines its SSE
    parser silently dropped, so remote streaming produced nothing. Here each
    page becomes one `page_result` event carrying exactly the client contract,
    identical to what the local lane returns.
    """
    client = _get_client_or_raise()

    def frame(event: str, payload: dict) -> bytes:
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()

    try:
        async for raw in client.stream_sse(
            "/api/scraper/quick-scrape",
            {"urls": urls, "options": options or {}},
            auth_token=auth_token,
        ):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Remote scrape stream: unparseable line %r", line[:200])
                continue

            kind = envelope.get("event")
            data = envelope.get("data")
            if kind == "data" and isinstance(data, dict) and data.get("type") == "fetch_results":
                elapsed_ms = int((data.get("metadata") or {}).get("execution_time_ms") or 0)
                for page in data.get("results") or []:
                    yield frame("page_result", from_page_dict(page, elapsed_ms=elapsed_ms))
            elif kind == "error":
                message = data if isinstance(data, str) else json.dumps(data)
                yield frame("error", {"failure_reason": message})
    except Exception as e:
        logger.error("Remote scrape stream failed: %s", e)
        yield frame("error", {"failure_reason": f"Remote scraper error: {e}"})

    yield frame("done", {})


@router.post("/scrape/stream")
async def remote_scrape_stream(req: ScrapeRequest, request: Request):
    """Scrape URLs via SSE — results stream back as each URL completes."""
    return StreamingResponse(
        _scrape_sse(req.urls, req.options, _get_user_token(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Search ────────────────────────────────────────────────────────────────────

@router.post("/search")
async def remote_search(req: SearchRequest, request: Request):
    """Search via Brave Search API on the remote server."""
    client = _get_client_or_raise()
    try:
        return await client.search(
            req.keywords, req.count, req.country, auth_token=_get_user_token(request),
        )
    except Exception as e:
        logger.error("Remote search failed: %s", e)
        raise HTTPException(502, f"Remote scraper error: {e}")


# ── Search + Scrape ───────────────────────────────────────────────────────────

@router.post("/search-and-scrape")
async def remote_search_and_scrape(req: SearchAndScrapeRequest, request: Request):
    """Search then scrape top results. Results stored server-side."""
    client = _get_client_or_raise()
    try:
        return await client.search_and_scrape(
            req.keywords, req.total_results_per_keyword, req.options,
            auth_token=_get_user_token(request),
        )
    except Exception as e:
        logger.error("Remote search-and-scrape failed: %s", e)
        raise HTTPException(502, f"Remote scraper error: {e}")


@router.post("/search-and-scrape/stream")
async def remote_search_and_scrape_stream(req: SearchAndScrapeRequest, request: Request):
    """Search + scrape via SSE stream."""
    client = _get_client_or_raise()
    return StreamingResponse(
        client.stream_sse(
            "/api/scraper/search-and-scrape",
            {
                "keywords": req.keywords,
                "total_results_per_keyword": req.total_results_per_keyword,
                "options": req.options or {},
            },
            auth_token=_get_user_token(request),
            timeout=300.0,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Research ──────────────────────────────────────────────────────────────────

@router.post("/research")
async def remote_research(req: ResearchRequest, request: Request):
    """Deep research — iterative search + scrape + compile."""
    client = _get_client_or_raise()
    try:
        return await client.research(
            req.query, req.effort, req.country, auth_token=_get_user_token(request),
        )
    except Exception as e:
        logger.error("Remote research failed: %s", e)
        raise HTTPException(502, f"Remote scraper error: {e}")


@router.post("/research/stream")
async def remote_research_stream(req: ResearchRequest, request: Request):
    """Deep research via SSE stream."""
    client = _get_client_or_raise()
    # Research endpoint not present on the new standalone scraper; use
    # search-and-scrape with research-mode options as the closest equivalent.
    return StreamingResponse(
        client.stream_sse(
            "/api/scraper/search-and-scrape",
            {
                "keywords": [req.query],
                "country": req.country,
                "options": {"fast": True, "effort": req.effort},
            },
            auth_token=_get_user_token(request),
            timeout=300.0,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Content save-back ─────────────────────────────────────────────────────────

@router.post("/content/save")
async def save_content(req: ContentSaveRequest, request: Request):
    """Save locally-scraped content to the server database immediately.

    Called after every successful local scrape so the web app and other
    devices see the result instantly. The server stores it in
    scrape_parsed_page — the same table used for server-side scrapes.
    """
    client = _get_client_or_raise()
    try:
        return await client.save_content(
            url=req.url,
            content=req.content,
            content_type=req.content_type,
            char_count=req.char_count,
            ttl_days=req.ttl_days,
            auth_token=_get_user_token(request),
        )
    except Exception as e:
        logger.error("Content save failed for %s: %s", req.url, e)
        raise HTTPException(502, f"Content save error: {e}")


# ── Retry queue ───────────────────────────────────────────────────────────────

@router.get("/queue/pending")
async def queue_pending(request: Request, tier: str = "desktop", limit: int = 10):
    """Get URLs the server failed to scrape that need local retry."""
    client = _get_client_or_raise()
    try:
        return await client.get_pending(tier=tier, limit=limit, auth_token=_get_user_token(request))
    except Exception as e:
        raise HTTPException(502, f"Remote scraper error: {e}")


@router.get("/queue/stats")
async def queue_stats(request: Request):
    """Retry queue statistics from the remote server."""
    client = _get_client_or_raise()
    try:
        return await client.queue_stats(auth_token=_get_user_token(request))
    except Exception as e:
        raise HTTPException(502, f"Remote scraper error: {e}")


@router.get("/queue/poller-stats")
async def queue_poller_stats():
    """Local retry queue poller statistics (this engine's activity)."""
    from app.services.scraper.retry_queue import get_stats
    return get_stats()


# ── Domain config ─────────────────────────────────────────────────────────────

@router.get("/config/domains")
async def get_domain_configs(request: Request):
    """Domain-specific scraping configs from the remote server."""
    client = _get_client_or_raise()
    try:
        return await client.get_domain_configs(auth_token=_get_user_token(request))
    except Exception as e:
        raise HTTPException(502, f"Remote scraper error: {e}")
