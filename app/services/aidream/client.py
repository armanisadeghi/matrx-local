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

import asyncio
import logging
import random
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
    """Raised when the AIDream server returns a non-2xx HTTP response.

    ``organization_refusal`` is set ONLY on the synthetic 400 this transport
    raises before a byte reaches the server, when this Mac has no organization
    set. It carries the canonical refusal (code / message / remedy / action /
    held) so a publisher can tell "waiting on one click" from "the server
    refused you" WITHOUT matching on error text. Text matching is how the
    distinction was lost the first time.
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        organization_refusal: dict[str, Any] | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.organization_refusal = organization_refusal
        #: The server's PARSED JSON envelope when the response carried one
        #: (``{"error", "message", "details": {...}}``), else ``None``. A
        #: structured refusal — the coding-session hold's membership list, for
        #: one — must be read from here, never scraped back out of ``message``.
        self.body = body


def _json_body_or_none(resp: httpx.Response) -> Any:
    """The response's JSON object, or ``None`` when it is not one."""
    try:
        parsed = resp.json()
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


# Owner-scoped coding-session routes. The server exempts them from the
# organization admission gate (aidream/api/middleware/auth.py
# ORGANIZATION_EXEMPT_PATHS) and resolves the organization INSIDE the handler
# from the connection's OWN person-set organization — never a user-level
# default or personal organization, both abolished by Arman on 2026-09-19
# (coding_session_bridge/ownership.py: `_configured_coding_session_organization`
# reads `connection_organization_id`, membership-verified). When nothing was
# configured yet, the handler HOLDS: it raises `MissingCodingSessionOrganization`
# and the route answers 409 `{"error": "organization_required", ...}` — see
# `_organization_refusal_of` in app/services/coding_sessions/service.py, which
# turns that response into the same HELD refusal as a local one. Demanding a
# header here anyway is what paused every delivery on a Mac with several
# memberships and no default chosen: 116,803 envelopes for nine days
# (2026-08-30 → 2026-09-08) behind a header the server would not have read. A
# caller-supplied header still wins; this only stops the transport from
# REFUSING to send when it has nothing to say.
# ── One immediate retry for a transport that failed on the wire (SR-09) ──────
#
# Measured over the 72h to 2026-09-14 on this transport: 394 deliveries of
# /api/coding-sessions/bridge failed with
#
#     [SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac
#
# plus 232 plain "Cannot reach". What the numbers say about the cause matters,
# because three plausible explanations are wrong:
#
#   * NOT a shared client under concurrency, and NOT keep-alive reuse across a
#     suspend — this client opens a FRESH httpx.AsyncClient per request (one
#     construction site, in _send, inside `async with`), so no connection is
#     ever reused between requests and there is nothing to share. The outbox
#     DOES deliver in parallel waves (its window grows toward
#     delivery_concurrency), so the handshakes are concurrent even though the
#     connections are not shared — which is the whole reason the retry below
#     waits a jittered moment instead of re-handshaking in the same
#     millisecond as its siblings.
#   * NOT proxy or tunnel interference configured by this app — it sets no
#     HTTP_PROXY/HTTPS_PROXY anywhere.
#   * NOT a storm: 370 DISTINCT outbox rows, never more than 5 in any minute,
#     spread evenly across three days and every hour of them.
#
# It is a genuine, roughly-one-in-two-hundred failure of an individual brand
# new TLS connection, on the network path between this Mac and
# server.app.matrxserver.com. The client cannot prevent it. What it CAN stop
# doing is treating it as "the server is unreachable": 326 of those 370 rows
# failed exactly once and were delivered on their very next attempt, having
# been pushed through an outbox backoff (2s → 64s) and a WARNING line each.
#
# So a wire-level failure gets ONE immediate retry on a new connection before
# it is called offline. Every GET is safe to repeat. A POST is only repeated on
# a path whose handler is replay-safe, because a record-mac failure cannot
# prove the request never landed — the bridge is exactly that (the server
# dedupes a replayed envelope by receipt id and answers 409), and the outbox
# would have re-sent the row regardless; this only makes it sooner and quieter.
_REPLAY_SAFE_POST_PATHS: tuple[str, ...] = ("/coding-sessions/bridge",)

# Wire failures are counted and summarised rather than logged per occurrence:
# retrying silently would hide a network problem, and 394 WARNINGs hid it just
# as well by drowning the log.
_RETRY_SUMMARY_EVERY = 25
_wire_retry_counts: dict[str, int] = {"retried": 0, "recovered": 0}


def wire_retry_stats() -> dict[str, int]:
    """Transport-level retries and how many of them recovered the request."""
    return dict(_wire_retry_counts)


_ORGANIZATION_SELF_RESOLVED_PATHS: tuple[str, ...] = (
    "/coding-sessions/bridge",
    "/coding-sessions/sessions",
    # The door OUT of the bridge's hold: which organization this account's
    # coding sessions are filed in (GET reports it + the choices, PUT sets
    # it). Owner-scoped on the JWT — the route exists because no organization
    # is known yet, so demanding one here would be the hold with no exit.
    "/coding-sessions/connection/organization",
)


def _is_organization_self_resolved(path: str) -> bool:
    bare = path.split("?", 1)[0].rstrip("/")
    for prefix in _ORGANIZATION_SELF_RESOLVED_PATHS:
        if bare == prefix or bare.startswith(prefix + "/"):
            return True
    return False


class AIDreamClient:
    """Thin async HTTP client for the AIDream REST API.

    Usage::

        client = get_aidream_client()

        # public endpoint — no JWT needed
        models = await client.get("/ai-models")

        # authenticated endpoint — pass user JWT
        models = await client.fetch_models()
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

    async def _build_headers(
        self,
        base: dict[str, str],
        jwt: Optional[str],
        headers: Optional[dict[str, str]],
        *,
        path: str = "",
    ) -> dict[str, str]:
        """The ONE place every request's identity headers are assembled.

        aidream's AuthMiddleware refuses an authenticated request that names
        no organization (400 ``organization_required``) BEFORE it routes, and
        it will never pick one for the caller. That made the organization a
        per-request transport fact exactly like the bearer token — so it is
        attached here, on the same line as ``Authorization``, instead of at
        each call site. Attaching it per-call-site is what left the coding
        session bridge, ``/ai/user/pending_calls`` and
        ``/coding-sessions/sessions`` 400ing in production on 2026-08-30 while
        four other call sites were fine: the four that remembered.

        Rules:
          - No JWT -> public call, no organization to state. Untouched.
          - A caller-supplied ``X-Organization-Id`` always WINS (the runtime
            spine and the tool bridge state the org of the specific lease they
            act under; the transport must never overwrite that).
          - Otherwise resolve the caller's own active organization through the
            canonical resolver, which never guesses.

        An organization that cannot be resolved raises rather than sending a
        header-less request that is a guaranteed 400 — the same rule the
        runtime spine already follows. The refusal names the remedy.
        """
        merged = dict(base)
        if jwt:
            merged["Authorization"] = f"Bearer {jwt}"
        if headers:
            merged.update(headers)

        if not jwt:
            return merged
        if any(name.lower() == "x-organization-id" for name in merged):
            return merged
        if _is_organization_self_resolved(path):
            return merged

        from app.services.aidream.organization import (
            OrganizationNotResolvedError,
            resolve_active_organization_id,
        )

        try:
            merged["X-Organization-Id"] = await resolve_active_organization_id(jwt)
        except OrganizationNotResolvedError as exc:
            from app.services.aidream.organization import organization_refusal

            refusal = organization_refusal(exc)
            raise AIDreamError(
                400,
                f"[aidream_client] {refusal['message']} {refusal['remedy']}",
                organization_refusal=refusal,
            ) from exc
        return merged

    # ── The one place a request meets the wire ───────────────────────────
    _WIRE_FAILURES = (
        httpx.ConnectError,
        httpx.NetworkError,
        httpx.RemoteProtocolError,
        ssl.SSLError,
        OSError,
    )

    @staticmethod
    def _is_replay_safe(method: str, path: str) -> bool:
        if method in ("GET", "PUT"):
            return True
        return any(path.startswith(safe) for safe in _REPLAY_SAFE_POST_PATHS)

    async def _send(
        self,
        method: str,
        url: str,
        *,
        path: str,
        headers: dict[str, str],
        timeout: float,
        json: Any = None,
    ) -> httpx.Response:
        """Send once; on a wire-level failure, retry once on a NEW connection.

        See _REPLAY_SAFE_POST_PATHS for why this exists and what it will and
        will not repeat. Anything else — a timeout (remote outcome unknown), an
        HTTP status, a non-replay-safe POST — is raised on the first failure,
        exactly as before.
        """
        attempts = 2 if self._is_replay_safe(method, path) else 1
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=timeout, transport=self._transport
                ) as http:
                    response = await http.request(
                        method, url, headers=headers, json=json
                    )
            except httpx.TimeoutException as exc:
                raise AIDreamTimeoutError(
                    f"[aidream_client] Timeout reaching {url}"
                ) from exc
            except self._WIRE_FAILURES as exc:
                if attempt >= attempts:
                    # Same words as before for each class, so callers and the
                    # log lines that match on them are unchanged.
                    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
                        raise AIDreamOfflineError(
                            f"[aidream_client] Cannot reach {url}: {exc}"
                        ) from exc
                    raise AIDreamOfflineError(
                        f"[aidream_client] Transport failure reaching {url}: {exc}"
                    ) from exc
                _wire_retry_counts["retried"] += 1
                logger.debug(
                    "[aidream_client] wire failure on %s %s (%s) — retrying once "
                    "on a new connection",
                    method, path, exc,
                )
                # A short jittered pause: simultaneous deliveries must not all
                # re-handshake in the same millisecond.
                await asyncio.sleep(0.2 + random.random() * 0.3)
                continue
            except httpx.HTTPError as exc:
                raise AIDreamOfflineError(
                    f"[aidream_client] HTTP error reaching {url}: {exc}"
                ) from exc
            if attempt > 1:
                _wire_retry_counts["recovered"] += 1
                if _wire_retry_counts["recovered"] % _RETRY_SUMMARY_EVERY == 0:
                    logger.info(
                        "[aidream_client] %s wire-level retries so far, %s of them "
                        "recovered the request on a new connection (the network "
                        "path to this server drops individual TLS connections; "
                        "nothing is lost, deliveries are not delayed by it)",
                        _wire_retry_counts["retried"],
                        _wire_retry_counts["recovered"],
                    )
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    async def get(
        self,
        path: str,
        jwt: Optional[str] = None,
        *,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """Perform a GET request to /api{path}.

        ``headers`` merges on top of the defaults (used e.g. for the
        ``X-Organization-Id`` context header some routers require).
        Returns parsed JSON.
        Raises AIDreamOfflineError on network failure.
        Raises AIDreamError on non-2xx response.
        """
        url = f"{self._base_url}/api{path}"
        headers = await self._build_headers(
            {"Accept": "application/json"}, jwt, headers, path=path
        )

        # httpx does not wrap every transport failure: a TLS alert raised
        # mid-stream (SSLV3_ALERT_BAD_RECORD_MAC, live since 2026-08-19 on the
        # coding-session publisher) reaches callers RAW, escapes their
        # `except AIDreamOfflineError` and kills the caller's loop. _send maps
        # every wire failure onto this client's offline contract, and retries
        # the replay-safe ones once first.
        resp = await self._send(
            "GET", url, path=path, headers=headers, timeout=_REQUEST_TIMEOUT
        )

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
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """Perform an authenticated JSON POST to ``/api{path}``.

        Tool execution can legitimately run for up to 120 seconds, so callers
        may use a longer timeout than the catalog-oriented GET default.
        ``headers`` merges on top of the defaults (e.g. ``X-Organization-Id``).
        """
        url = f"{self._base_url}/api{path}"
        headers = await self._build_headers(
            {"Accept": "application/json", "Content-Type": "application/json"},
            jwt,
            headers,
            path=path,
        )

        resp = await self._send(
            "POST", url, path=path, headers=headers, timeout=timeout, json=payload
        )

        if not resp.is_success:
            detail = resp.text[:1000]
            raise AIDreamError(
                resp.status_code,
                f"[aidream_client] {path} → HTTP {resp.status_code}: {detail}",
                body=_json_body_or_none(resp),
            )

        return resp.json()

    async def put(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        jwt: Optional[str] = None,
        timeout: float = _REQUEST_TIMEOUT,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """Perform an authenticated JSON PUT to ``/api{path}``.

        Same transport, same header assembly, same error contract as ``post``.
        A PUT is idempotent by definition, so unlike a POST it is always
        replay-safe on a wire-level failure.
        """
        url = f"{self._base_url}/api{path}"
        headers = await self._build_headers(
            {"Accept": "application/json", "Content-Type": "application/json"},
            jwt,
            headers,
            path=path,
        )

        resp = await self._send(
            "PUT", url, path=path, headers=headers, timeout=timeout, json=payload
        )

        if not resp.is_success:
            detail = resp.text[:1000]
            raise AIDreamError(
                resp.status_code,
                f"[aidream_client] {path} → HTTP {resp.status_code}: {detail}",
                body=_json_body_or_none(resp),
            )

        return resp.json()

    # ------------------------------------------------------------------
    # Named helpers for each endpoint group
    # ------------------------------------------------------------------

    async def fetch_models(self) -> list[dict[str, Any]]:
        """GET /api/ai-models — public, no auth needed."""
        data = await self.get("/ai-models")
        return data.get("models", [])

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
