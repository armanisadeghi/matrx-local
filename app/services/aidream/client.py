"""AIDream server API client.

Used by the SyncEngine to pull shared data (models, prompts, tools) from the
AIDream server into local SQLite.  Never used for direct reads — all reads go
through SQLite repositories.

URL is always read from the remote app-config accessor
(app.services.app_config.get_aidream_server_url — env dev-override > remote
config > disk cache > compiled default).  Never hardcoded.

Offline behaviour: raises AIDreamOfflineError when the server is unreachable.
The SyncEngine catches this and skips the sync cycle gracefully, logging a warning.
"""

from __future__ import annotations

import logging
import ssl
from typing import Any, Optional

import httpx

from app.services.app_config import get_aidream_server_url

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 15.0  # seconds


class AIDreamOfflineError(Exception):
    """Raised when the AIDream server is unreachable or returns a network error."""


class AIDreamTimeoutError(AIDreamOfflineError):
    """Raised when a request times out and the remote outcome may be unknown."""


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

    def __init__(
        self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not base_url:
            raise ValueError(
                "[aidream_client] No AIDream server URL was resolved from app "
                "config. Cannot create AIDreamClient without a base URL."
            )
        self._base_url = base_url.rstrip("/")
        self._transport = transport

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
            async with httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT, transport=self._transport
            ) as http:
                resp = await http.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise AIDreamTimeoutError(
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
        except (ssl.SSLError, OSError) as exc:
            # httpx does not wrap every transport failure. A TLS alert raised
            # mid-stream (SSLV3_ALERT_BAD_RECORD_MAC, seen live 2026-08-19 on
            # the coding-session publisher) reaches callers RAW, escapes their
            # `except AIDreamOfflineError` and kills the caller's loop. At this
            # boundary an OSError means exactly one thing — the server was not
            # reached — which is this client's definition of offline.
            raise AIDreamOfflineError(
                f"[aidream_client] Transport failure reaching {url}: {exc}"
            ) from exc

        if not resp.is_success:
            raise AIDreamError(
                resp.status_code,
                f"[aidream_client] {path} → HTTP {resp.status_code}",
            )

        return resp.json()

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        jwt: Optional[str] = None,
        timeout: float = 130.0,
    ) -> Any:
        """Perform an authenticated JSON POST to ``/api{path}``.

        Tool execution can legitimately run for up to 120 seconds, so callers
        may use a longer timeout than the catalog-oriented GET default.
        """
        url = f"{self._base_url}/api{path}"
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"

        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self._transport
            ) as http:
                resp = await http.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise AIDreamTimeoutError(
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
        except (ssl.SSLError, OSError) as exc:
            # httpx does not wrap every transport failure. A TLS alert raised
            # mid-stream (SSLV3_ALERT_BAD_RECORD_MAC, seen live 2026-08-19 on
            # the coding-session publisher) reaches callers RAW, escapes their
            # `except AIDreamOfflineError` and kills the caller's loop. At this
            # boundary an OSError means exactly one thing — the server was not
            # reached — which is this client's definition of offline.
            raise AIDreamOfflineError(
                f"[aidream_client] Transport failure reaching {url}: {exc}"
            ) from exc

        if not resp.is_success:
            detail = resp.text[:1000]
            raise AIDreamError(
                resp.status_code,
                f"[aidream_client] {path} → HTTP {resp.status_code}: {detail}",
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

    async def fetch_agent_execution_definition(
        self,
        definition_id: str,
        *,
        is_version: bool,
        jwt: str,
    ) -> dict[str, Any]:
        """Fetch one complete, ownership-scoped executable-agent definition."""

        if is_version:
            path = f"/agents/versions/{definition_id}/execution-definition"
        else:
            path = f"/agents/{definition_id}/execution-definition"
        data = await self.get(path, jwt=jwt)
        if not isinstance(data, dict):
            raise AIDreamError(
                502,
                f"[aidream_client] {path} returned a non-object definition",
            )
        return data

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

    The base URL comes from the remote app-config accessor (env override >
    remote > cache > compiled default). If the effective URL changes after a
    config refresh, the singleton is rebuilt on the next call so redirects
    apply without an app restart. Returns None if no URL is resolvable, so
    callers can gracefully skip sync rather than crashing.
    """
    global _instance

    base_url = get_aidream_server_url()
    if not base_url:
        logger.warning(
            "[aidream_client] No AIDream server URL resolved from app config. "
            "Remote sync (models, prompts, tools) is DISABLED. "
            "Data will be served from local SQLite only."
        )
        return None

    if _instance is not None and _instance._base_url == base_url.rstrip("/"):
        return _instance

    if _instance is not None:
        logger.warning(
            "[aidream_client] aidream server URL changed (%s → %s) via app "
            "config refresh — rebuilding client",
            _instance._base_url,
            base_url,
        )

    _instance = AIDreamClient(base_url)
    logger.info("[aidream_client] AIDreamClient created. base_url=%s", base_url)
    return _instance
