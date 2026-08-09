"""Remote scraper client — delegates scrape jobs to the dedicated scraper server.

This is used when the desktop app wants to use the remote scraper server
(scraper.app.matrxserver.com) instead of or in addition to the local
scraper engine. Useful for offloading heavy scraping, leveraging the
server's proxy pool, or accessing server-side cached content.

All communication uses the scraper server's REST API with Bearer token auth.
No direct database access — the server handles all persistence.

API contract (matrx-scraper standalone microservice):
    https://scraper.app.matrxserver.com/api/scraper/...

Migrated 2026-04-29 from /api/v1/* to /api/scraper/*. Health is now
/health/ready (the server's readiness probe). Response shapes also
changed — see packages/matrx-scraper/MIGRATION_GUIDE.md in the
aidream-current repo for the full diff.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import SCRAPER_API_KEY
from app.services.app_config import get_scraper_server_url

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0

# All scrape/queue endpoints live under this prefix on the standalone
# matrx-scraper microservice. Health endpoints live at the app root.
API_PREFIX = "/api/scraper"
HEALTH_PATH = "/health/ready"


class RemoteScraperClient:
    """HTTP client for the remote scraper server API."""

    def __init__(
        self,
        server_url: str | None = None,
        api_key: str = SCRAPER_API_KEY,
    ) -> None:
        # Explicit server_url (tests) wins; otherwise read per-use from the
        # remote app-config accessor so a config refresh applies on the next
        # request without recreating the singleton.
        self._server_url_override = server_url.rstrip("/") if server_url else None
        self._api_key = api_key

    @property
    def _server_url(self) -> str:
        if self._server_url_override:
            return self._server_url_override
        return get_scraper_server_url().rstrip("/")

    @property
    def is_configured(self) -> bool:
        # Server URL is always set (hardcoded default). API key is optional since
        # authenticated routes accept Supabase JWTs. The retry poller uses the
        # API key as fallback — return True so it runs for all users.
        return bool(self._server_url)

    def _headers(self, auth_token: str | None = None) -> dict[str, str]:
        token = auth_token or self._api_key
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._server_url}{HEALTH_PATH}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def scrape(
        self,
        urls: list[str],
        options: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Non-streaming batch scrape. Maps to the new `/api/scraper/batch`.

        The response shape changed in the new contract:
            old: {"results": [{"status": "success"|"error", "error": "..."}]}
            new: {"status": "success", "execution_time_ms": ...,
                  "results": [{"success": true|false, "failure_reason": "..."}]}

        Callers parsing results should check `page["success"]` (bool) instead
        of `page["status"] == "success"`, and read `page["failure_reason"]`
        instead of `page["error"]`.
        """
        body: dict[str, Any] = {"urls": urls}
        if options:
            body.update(options)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self._server_url}{API_PREFIX}/batch",
                headers=self._headers(auth_token),
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    async def search(
        self,
        keywords: list[str],
        count: int = 10,
        country: str = "US",
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self._server_url}{API_PREFIX}/search",
                headers=self._headers(auth_token),
                json={"keywords": keywords, "count": count, "country": country},
            )
            resp.raise_for_status()
            return resp.json()

    async def search_and_scrape(
        self,
        keywords: list[str],
        total_results_per_keyword: int = 5,
        options: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._server_url}{API_PREFIX}/search-and-scrape",
                headers=self._headers(auth_token),
                json={
                    "keywords": keywords,
                    "total_results_per_keyword": total_results_per_keyword,
                    "options": options or {},
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def research(
        self,
        query: str,
        effort: str = "thorough",
        country: str = "US",
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        # The standalone scraper does not currently expose a dedicated
        # /research endpoint — the closest semantic equivalent is
        # search-and-scrape with `fast=true` (research-mode flag that skips
        # link extraction and hashing). Caller should treat the response as
        # search-and-scrape output, not the legacy research shape.
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{self._server_url}{API_PREFIX}/search-and-scrape",
                headers=self._headers(auth_token),
                json={
                    "keywords": [query],
                    "country": country,
                    "options": {"fast": True, "effort": effort},
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def stream_sse(
        self,
        path: str,
        payload: dict[str, Any],
        auth_token: str | None = None,
        timeout: float = 300.0,
    ) -> AsyncIterator[bytes]:
        """Open an NDJSON stream from the scraper server and yield raw lines.

        `path` should be a fully-formed path under the new API prefix, e.g.
        `/api/scraper/quick-scrape` (the new streaming default for scrape) or
        `/api/scraper/search-and-scrape`.
        """
        headers = self._headers(auth_token)
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            async with client.stream(
                "POST",
                f"{self._server_url}{path}",
                headers=headers,
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    yield (line + "\n").encode("utf-8")

    async def get_domain_configs(self, auth_token: str | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{self._server_url}{API_PREFIX}/config/domains",
                headers=self._headers(auth_token),
            )
            resp.raise_for_status()
            return resp.json()

    # ── Content save-back ────────────────────────────────────────────────────

    async def save_content(
        self,
        url: str,
        page_name: str,
        content: dict[str, Any],
        content_type: str = "html",
        char_count: int | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Push locally-scraped content to the server's central database.

        The server stores it in ``scrape_parsed_page`` — the same table it
        uses for its own scrapes — so all clients can access the result.

        ``content`` should include at least ``text_data`` or
        ``ai_research_content``.  Optional keys: ``overview``, ``links``,
        ``hashes``, ``main_image``.

        ``page_name`` is REQUIRED by the server's ``ContentSaveRequest`` and is
        the package's ``unique_page_name`` for the URL. Omitting it answers 422
        on every single call — which is exactly what this client did from the
        2026-04-29 ``/api/v1`` → ``/api/scraper`` migration until 2026-08-09,
        silently killing the dual write. There is no ``ttl_days`` on the server
        model either; sending one only advertised a retention guarantee the
        server never made.
        """
        body: dict[str, Any] = {
            "url": url,
            "page_name": page_name,
            "content": content,
            "content_type": content_type,
        }
        if char_count is not None:
            body["char_count"] = char_count
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self._server_url}{API_PREFIX}/content/save",
                headers=self._headers(auth_token),
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    # ── Retry queue ──────────────────────────────────────────────────────────

    async def get_pending(
        self,
        tier: str = "desktop",
        limit: int = 10,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Fetch URLs that failed on the server and need local retry."""
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{self._server_url}{API_PREFIX}/queue/pending",
                headers=self._headers(auth_token),
                params={"tier": tier, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json()

    async def claim_items(
        self,
        item_ids: list[str],
        client_id: str,
        client_type: str = "desktop",
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Claim queue items (10-min TTL) so no other client picks them up."""
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self._server_url}{API_PREFIX}/queue/claim",
                headers=self._headers(auth_token),
                json={
                    "item_ids": item_ids,
                    "client_id": client_id,
                    "client_type": client_type,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def submit_result(
        self,
        queue_item_id: str,
        url: str,
        content: dict[str, Any],
        content_type: str = "html",
        char_count: int | None = None,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Submit a successfully scraped result for a claimed queue item."""
        body: dict[str, Any] = {
            "queue_item_id": queue_item_id,
            "url": url,
            "content": content,
            "content_type": content_type,
        }
        if char_count is not None:
            body["char_count"] = char_count
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self._server_url}{API_PREFIX}/queue/submit",
                headers=self._headers(auth_token),
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    async def report_failure(
        self,
        queue_item_id: str,
        error: str,
        promote_to_extension: bool = True,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Report that a local scrape attempt failed for a queue item."""
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{self._server_url}{API_PREFIX}/queue/fail",
                headers=self._headers(auth_token),
                json={
                    "queue_item_id": queue_item_id,
                    "error": error,
                    "promote_to_extension": promote_to_extension,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def queue_stats(self, auth_token: str | None = None) -> dict[str, Any]:
        """Get retry queue statistics."""
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{self._server_url}{API_PREFIX}/queue/stats",
                headers=self._headers(auth_token),
            )
            resp.raise_for_status()
            return resp.json()


_client: RemoteScraperClient | None = None


def get_remote_scraper() -> RemoteScraperClient:
    global _client
    if _client is None:
        _client = RemoteScraperClient()
    return _client
