"""AIDream server API client.

Used by the SyncEngine to pull shared data (models, prompts, tools) from the
AIDream server into local SQLite.  Never used for direct reads — all reads go
through SQLite repositories.

URL is always read from config.AIDREAM_SERVER_URL (which reads AIDREAM_SERVER_URL_LIVE
from .env).  Never hardcoded.

Offline behaviour: raises AIDreamOfflineError when the server is unreachable.
The SyncEngine catches this and skips the sync cycle gracefully, logging a warning.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import AIDREAM_SERVER_URL

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 15.0  # seconds


class AIDreamOfflineError(Exception):
    """Raised when the AIDream server is unreachable or returns a network error."""


class AIDreamError(Exception):
    """Raised when the AIDream server returns a non-2xx HTTP response."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class AIDreamClient:
    """Thin async HTTP client for the AIDream REST API.

    Usage::

        client = get_aidream_client()

        # public endpoint — no JWT needed
        models = await client.get("/ai-models")

        # authenticated endpoint — pass user JWT
        agents = await client.get("/agents", jwt=user_jwt)
    """

    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise ValueError(
                "[aidream_client] AIDREAM_SERVER_URL_LIVE is not set in .env. "
                "Cannot create AIDreamClient without a base URL."
            )
        self._base_url = base_url.rstrip("/")

    async def get(self, path: str, jwt: Optional[str] = None) -> Any:
        """Perform a GET request to /api{path}.

        Returns parsed JSON.
        Raises AIDreamOfflineError on network failure.
        Raises AIDreamError on non-2xx response.
        """
        url = f"{self._base_url}/api{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as http:
                resp = await http.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise AIDreamOfflineError(
                f"[aidream_client] Timeout reaching {url}"
            ) from exc
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            raise AIDreamOfflineError(
                f"[aidream_client] Cannot reach {url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIDreamOfflineError(
                f"[aidream_client] HTTP error reaching {url}: {exc}"
            ) from exc

        if not resp.is_success:
            raise AIDreamError(
                resp.status_code,
                f"[aidream_client] {path} → HTTP {resp.status_code}",
            )

        return resp.json()

    # ------------------------------------------------------------------
    # Named helpers for each endpoint group
    # ------------------------------------------------------------------

    async def fetch_models(self) -> list[dict[str, Any]]:
        """GET /api/ai-models — public, no auth needed."""
        data = await self.get("/ai-models")
        return data.get("models", [])

    async def fetch_agents(self, jwt: str) -> list[dict[str, Any]]:
        """GET /api/agents — requires user JWT.

        Returns the unified agent catalog: platform agents (formerly prompt
        builtins, now rows in the ``agent.definition`` table) plus the
        authenticated user's own agents. There is no public/anonymous variant;
        the old /api/prompts/builtins, /api/prompts and /api/prompts/all
        endpoints are unmounted. Each item is shaped as
        ``{id, name, description, category, tags, type, variables}``.
        """
        data = await self.get("/agents", jwt=jwt)
        return data.get("agents", [])

    async def fetch_tools(self) -> list[dict[str, Any]]:
        """GET /api/ai-tools — public, no auth needed."""
        data = await self.get("/ai-tools")
        return data.get("tools", [])

    async def fetch_tools_for_app(self, source_app: str) -> list[dict[str, Any]]:
        """GET /api/ai-tools/app/{source_app}/all — public, no auth needed."""
        data = await self.get(f"/ai-tools/app/{source_app}/all")
        return data.get("tools", [])


# ---------------------------------------------------------------------------
# Singleton — cached per process
# ---------------------------------------------------------------------------

_instance: Optional[AIDreamClient] = None


def get_aidream_client() -> Optional[AIDreamClient]:
    """Return the module-level AIDreamClient singleton.

    Returns None if AIDREAM_SERVER_URL_LIVE is not configured, so callers can
    gracefully skip sync rather than crashing.
    """
    global _instance
    if _instance is not None:
        return _instance

    if not AIDREAM_SERVER_URL:
        logger.warning(
            "[aidream_client] AIDREAM_SERVER_URL_LIVE is not set in .env. "
            "Remote sync (models, prompts, tools) is DISABLED. "
            "Data will be served from local SQLite only."
        )
        return None

    _instance = AIDreamClient(AIDREAM_SERVER_URL)
    logger.info(
        "[aidream_client] AIDreamClient created. base_url=%s", AIDREAM_SERVER_URL
    )
    return _instance
