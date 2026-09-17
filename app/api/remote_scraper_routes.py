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
from pydantic import BaseModel, Field
from app.services.scraper.remote_client import (
    RESEARCH_CAPABILITY_NOTICE,
    get_remote_scraper,
    quick_scrape_payload,
    search_and_scrape_payload,
)
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
    count: int = Field(default=20, ge=1, le=100)
    country: str = "US"


class SearchAndScrapeRequest(BaseModel):
    keywords: list[str]
    total_results_per_keyword: int = Field(default=10, ge=10, le=30)
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
        resp = await client.scrape(
            req.urls, req.options, auth_token=_get_user_token(request)
        )
    except Exception as e:
        logger.error("Remote scrape failed: %s", e)
        raise HTTPException(502, f"Remote scraper error: {e}")

    elapsed_ms = int(resp.get("execution_time_ms") or 0)
    pages = [
        from_page_dict(p, elapsed_ms=elapsed_ms) for p in resp.get("results") or []
    ]
    return {
        "results": pages,
        "total": len(pages),
        "success_count": sum(1 for p in pages if p["success"]),
        "elapsed_ms": elapsed_ms,
    }


def _sse_frame(event: str, payload: object) -> bytes:
    """Encode one browser-readable SSE frame."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def _failure_reason(data: object) -> str:
    """Select the user-facing reason from a remote error envelope."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return str(
            data.get("user_message") or data.get("message") or data.get("error") or data
        )
    return str(data)


async def _remote_scraper_sse(
    path: str,
    payload: dict,
    auth_token: str | None,
    timeout: float = 300.0,
):
    """Translate the scraper server's NDJSON stream into browser SSE frames.

    The server streams NDJSON envelopes (`{"event":"data","data":{"type":
    "fetch_results","results":[page,…]}}`) — not SSE. Forwarding those raw
    under a `text/event-stream` content type gave the browser lines its SSE
    parser silently dropped, so remote streaming produced nothing. Each page
    becomes a canonical `page_result`; search, progress, and lifecycle events
    retain their package event names and payloads.
    """
    client = _get_client_or_raise()
    saw_end = False
    terminal_error = False

    try:
        async for raw in client.stream_sse(
            path,
            payload,
            auth_token=auth_token,
            timeout=timeout,
        ):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Remote scrape stream: unparseable line %r", line[:200])
                yield _sse_frame(
                    "error",
                    {"failure_reason": "Remote scraper sent malformed stream data."},
                )
                terminal_error = True
                break
            if not isinstance(envelope, dict):
                logger.warning(
                    "Remote scrape stream: malformed envelope %r", line[:200]
                )
                yield _sse_frame(
                    "error",
                    {"failure_reason": "Remote scraper sent malformed stream data."},
                )
                terminal_error = True
                break

            kind = envelope.get("event")
            data = envelope.get("data")
            response_type = (
                data.get("type") or data.get("response_type")
                if isinstance(data, dict)
                else None
            )
            if kind == "data" and response_type == "fetch_results":
                elapsed_ms = int(
                    (data.get("metadata") or {}).get("execution_time_ms") or 0
                )
                for page in data.get("results") or []:
                    yield _sse_frame(
                        "page_result", from_page_dict(page, elapsed_ms=elapsed_ms)
                    )
            elif kind == "error":
                yield _sse_frame("error", {"failure_reason": _failure_reason(data)})
                terminal_error = True
                break
            elif response_type == "search_error":
                # One keyword can fail while the package continues to scrape
                # results from the other keywords in this same stream.
                yield _sse_frame("error", {"failure_reason": _failure_reason(data)})
            elif kind == "end":
                # This proxy emits one terminal event below for every outcome.
                saw_end = True
                break
            elif kind == "data" and response_type:
                yield _sse_frame(response_type, data)
            elif isinstance(kind, str):
                yield _sse_frame(kind, data)
    except Exception as e:
        logger.error("Remote scrape stream failed: %s", e)
        yield _sse_frame("error", {"failure_reason": f"Remote scraper error: {e}"})
        terminal_error = True

    if not saw_end and not terminal_error:
        yield _sse_frame(
            "error",
            {"failure_reason": "Remote scraper stream ended before completion."},
        )

    yield _sse_frame("done", {})


async def _scrape_sse(urls: list[str], options: dict | None, auth_token: str | None):
    """Stream a remote quick scrape through the shared NDJSON-to-SSE adapter."""
    async for frame in _remote_scraper_sse(
        "/api/scraper/quick-scrape",
        quick_scrape_payload(urls, options),
        auth_token,
    ):
        yield frame


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
            req.keywords,
            req.count,
            req.country,
            auth_token=_get_user_token(request),
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
            req.keywords,
            req.total_results_per_keyword,
            req.options,
            auth_token=_get_user_token(request),
        )
    except Exception as e:
        logger.error("Remote search-and-scrape failed: %s", e)
        raise HTTPException(502, f"Remote scraper error: {e}")


@router.post("/search-and-scrape/stream")
async def remote_search_and_scrape_stream(
    req: SearchAndScrapeRequest, request: Request
):
    """Search + scrape via browser-readable SSE events."""
    return StreamingResponse(
        _remote_scraper_sse(
            "/api/scraper/search-and-scrape",
            search_and_scrape_payload(
                req.keywords, req.total_results_per_keyword, req.options
            ),
            auth_token=_get_user_token(request),
            timeout=300.0,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Research ──────────────────────────────────────────────────────────────────


@router.post("/research")
async def remote_research(req: ResearchRequest, request: Request):
    """Search-and-scrape fallback for the legacy research route."""
    client = _get_client_or_raise()
    try:
        return await client.research(
            req.query,
            req.effort,
            req.country,
            auth_token=_get_user_token(request),
        )
    except Exception as e:
        logger.error("Remote research failed: %s", e)
        raise HTTPException(502, f"Remote scraper error: {e}")


@router.post("/research/stream")
async def remote_research_stream(req: ResearchRequest, request: Request):
    """Stream the available search-and-scrape research fallback."""
    return StreamingResponse(
        _research_sse(req, _get_user_token(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _research_sse(req: ResearchRequest, auth_token: str | None):
    """Stream the available search-and-scrape fallback with an honest notice."""
    yield _sse_frame(
        "info",
        {
            "code": "research_capability_notice",
            "system_message": RESEARCH_CAPABILITY_NOTICE,
            "user_message": RESEARCH_CAPABILITY_NOTICE,
            "metadata": {"capability": "search_and_scrape_fallback"},
        },
    )
    async for frame in _remote_scraper_sse(
        "/api/scraper/search-and-scrape",
        search_and_scrape_payload([req.query], 10, country_code=req.country),
        auth_token=auth_token,
        timeout=300.0,
    ):
        yield frame


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
        return await client.get_pending(
            tier=tier, limit=limit, auth_token=_get_user_token(request)
        )
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
