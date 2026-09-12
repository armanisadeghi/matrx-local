"""Auth middleware for the local engine.

Since the engine runs on localhost, we don't validate JWTs here — that happens
on the remote scraper server. We just ensure callers provide a Bearer token
(the Supabase JWT or the local API key) to prevent unauthorized access from
other processes on the machine.

Public routes (health and discovery) are excluded from the auth check.
"""

from __future__ import annotations

from fastapi import Request

import app.common.access_log as access_log
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.common.system_logger import get_logger
from app.api.remote_auth import (
    headers_indicate_tunnel,
    is_instance_owner,
    verify_supabase_token,
)
from app.services.pairing import matches_pair_token
from app.services.catalogs.models import KNOWN_KINDS as _CATALOG_KINDS

logger = get_logger()

# ── Trust tiers ────────────────────────────────────────────────────────────
#
# The engine binds 127.0.0.1 only. Two kinds of caller reach that socket:
#   * Direct loopback — the Tauri webview, a local browser, the Rust shell
#     calling /admin/*. These are inside the user's machine; the loopback
#     socket itself is the trust boundary, so presence of a Bearer is enough.
#   * Via the Cloudflare tunnel — mobile / remote browser. This is the
#     designed remote-access feature, but the traffic is untrusted: it must
#     carry a *verified* Supabase identity (or the local API key).
#
# headers_indicate_tunnel() distinguishes the two (Cf-Ray / Cf-Connecting-Ip).
# See app/api/remote_auth.py for why that signal is safe.

# Always public — safe even when served over the tunnel (health, discovery,
# read-only metadata). No user data, no mutation of identity.
_PUBLIC_PATHS = frozenset(
    {
        "/",
        "/tools/list",
        "/remote-scraper/status",
        "/proxy/status",
        "/chat/tools",
        "/chat/tools/by-category",
        "/chat/tools/anthropic",
        "/chat/models",  # read-only model list, no user data
        "/chat/sync/status",  # read-only sync status — useful for diagnostics before auth
        "/chat/ai-status",  # read-only provider availability — needed before auth to show warnings
        "/remote-scraper/queue/poller-stats",
        "/docs",
        "/openapi.json",
        "/redoc",
        # Discovery & health — the web app needs these before it can send auth.
        "/health",
        "/version",
        "/ports",
        "/cloud/heartbeat",
        # Tunnel status — needed by mobile/remote clients before they have a
        # session to check if the tunnel is active without authenticating first.
        "/tunnel/status",
    }
)

# Resolved remote-catalog reads (GET /catalogs/{kind}) — read-only public
# metadata (the same rows anyone can read anonymously from Supabase), needed
# by the desktop webview before auth, same posture as /chat/models. Built
# from KNOWN_KINDS so an unknown kind is never public.
_PUBLIC_PATHS = _PUBLIC_PATHS | frozenset(f"/catalogs/{k}" for k in _CATALOG_KINDS)

# Local-bootstrap — allowed WITHOUT a token only on direct loopback, because
# the legitimate callers (Rust shell, the desktop UI handing the engine its
# JWT during sign-in) have no verifiable credential to send yet, and the
# loopback socket is their trust boundary. Over the tunnel these become
# dangerous (remote DoS via /admin/shutdown, identity hijack via
# /cloud/configure, info disclosure via /setup/debug), so when the request
# arrives via the tunnel they fall through to the full verified-auth check.
_LOCAL_BOOTSTRAP_PATHS = frozenset(
    {
        "/cloud/configure",
        "/cloud/reconfigure",
        "/settings",  # PUT /settings runs in parallel with /cloud/configure
        "/chat/sync/trigger",  # force sync — used from setup/diagnostics
        # The agent catalog. These were PUBLIC while the list was builtins +
        # the caller's own agents ("no user data"). Since 2026-09-08 the rows
        # are the platform catalog (agx_get_list_full), which carries SHARED
        # and ORG-SHARED agents including the sharer's email — user data by
        # any reading. They stay reachable on direct loopback (the desktop
        # webview reads the list before its JWT hydrates) but a tunnel caller
        # now falls through to verified auth.
        "/agents/catalog",
        # Local llama-server bridge. The desktop UI/Rust shell may need to
        # register a just-started local model before Supabase auth has fully
        # hydrated in the webview; over the tunnel these still require auth.
        "/chat/local-llm/connect",
        "/chat/local-llm/disconnect",
        "/chat/local-llm/status",
        "/chat/delegation/status",  # read-only local diagnostics; tunnel requires auth
        "/auth/token",  # JWT is the credential being *given* to the engine
        "/admin/status",
        "/admin/shutdown",
        "/admin/diagnose",
        "/admin/refresh-config",
        "/admin/refresh-catalogs",
        "/admin/recovery",
    }
)

# Path PREFIXES that are local-bootstrap (same rule as above): permissive on
# direct loopback, verified-auth required over the tunnel.
_LOCAL_BOOTSTRAP_PREFIXES = (
    "/admin/recovery/",
    "/devices/",  # device status polling — local UI
    "/fetch-proxy",  # in-app browser iframe navigation (also an SSRF vector)
    "/setup/",  # setup wizard — system probing, installs
    # Browser-runtime probe + the one-click Chromium install it offers. Same
    # class as /setup/ (it IS the same installer): the Scraping page must be
    # able to say "the browser isn't installed" before Supabase auth has
    # hydrated in the webview. Tunnel callers still fall through to verified
    # auth, so no remote caller can trigger a download.
    "/browser-runtime/",
    # The agent catalog's sub-paths (/rpc, /status, /{id}/execution). Same
    # posture and same reason as "/agents/catalog" in the exact set above: the
    # desktop webview reads the catalog before its JWT hydrates; a tunnel
    # caller falls through to verified auth because these rows are user data.
    "/agents/catalog/",
    # Process-local media bytes for the desktop webview. Tunnel callers still
    # fall through to verified auth; direct <img> requests cannot attach the
    # API's bearer header.
    "/artifacts/",
)


def _is_local_bootstrap(path: str) -> bool:
    return path in _LOCAL_BOOTSTRAP_PATHS or path.startswith(_LOCAL_BOOTSTRAP_PREFIXES)


def _auth_error_response(
    path: str,
    *,
    status_code: int,
    message: str,
    code: str,
    request: Request | None = None,
) -> JSONResponse:
    """Return the auth error shape expected by the target surface.

    Also records the rejection in the structured access log. The request-logging
    middleware is the INNERMOST one, so a request refused here never reaches it:
    before this, every auth failure was absent from access.log entirely, and the
    log showed only 200/202 across ~59k requests — reading as "nothing ever
    failed" when in fact failures were structurally unloggable.
    """
    if request is not None:
        try:
            access_log.record(
                method=request.method,
                path=path,
                query="",
                origin=request.headers.get("origin", ""),
                user_agent=request.headers.get("user-agent", ""),
                status=status_code,
                duration_ms=0.0,
            )
        except Exception:  # logging must never break the auth decision
            logger.debug("[auth] could not record rejection", exc_info=True)
    if path == "/v1" or path.startswith("/v1/"):
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "message": message,
                    "type": "authentication_error",
                    "param": None,
                    "code": code,
                }
            },
        )
    return JSONResponse(status_code=status_code, content={"detail": message})


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"

        # CORS preflights carry no credentials and must pass through.
        if request.method == "OPTIONS":
            return await call_next(request)

        via_tunnel = headers_indicate_tunnel(request.headers)
        request.state.via_tunnel = via_tunnel

        # Always-public routes (health/discovery/read-only) — allowed on any path.
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Pairing bootstrap owns its tunnel rejection. Let the route return
        # the documented hard 403 instead of allowing this outer middleware
        # to turn the missing credential into a misleading 401 first.
        if path == "/extension/pair":
            return await call_next(request)

        # Provider command hooks cannot carry the user's Supabase JWT. This
        # mutation surface owns a stricter direct-loopback-only check and must
        # receive tunnel-marked requests itself so they get the contractual
        # hard 403 rather than a generic missing-credential 401.
        if path == "/coding-session/hooks":
            return await call_next(request)

        # Local-bootstrap routes — permissive on direct loopback (the Rust
        # shell / desktop UI have no verifiable token yet), but over the
        # tunnel they require a verified identity like any other route.
        if _is_local_bootstrap(path) and not via_tunnel:
            return await call_next(request)

        # Extract Bearer token — prefer Authorization header, fall back to
        # ?token query param (required for EventSource / SSE connections that
        # cannot set custom request headers).
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        else:
            token = (request.query_params.get("token") or "").strip() or None

        if not token:
            logger.warning(
                "[auth] rejected %s %s — missing bearer token or ?token= query (tunnel=%s)",
                request.method,
                path,
                via_tunnel,
            )
            return _auth_error_response(
                path,
                request=request,
                status_code=401,
                message="Authorization required",
                code="authorization_required",
            )

        # Tunnel traffic is untrusted: require a cryptographically-verified
        # Supabase identity (validated by the auth server, since the project
        # signs HS256 and the engine holds no secret) AND that the identity is
        # this instance's owner. A static local API key is deliberately NOT
        # accepted over the tunnel — it's a loopback-only credential.
        # Direct-loopback traffic keeps the presence-only boundary — the
        # loopback socket itself is the trust boundary there.
        if via_tunnel and not (
            path.startswith("/extension/") and matches_pair_token(token)
        ):
            user = await verify_supabase_token(token)
            if user is None:
                logger.warning(
                    "[auth] rejected %s %s — unverified token over tunnel",
                    request.method,
                    path,
                )
                return _auth_error_response(
                    path,
                    request=request,
                    status_code=401,
                    message="Invalid or expired credentials",
                    code="invalid_credentials",
                )
            # Owner-only: a valid token from a *different* AI Matrx user must
            # not control this machine remotely.
            if not await is_instance_owner(user.user_id):
                logger.warning(
                    "[auth] rejected %s %s — token user is not this instance's owner",
                    request.method,
                    path,
                )
                return _auth_error_response(
                    path,
                    request=request,
                    status_code=403,
                    message="Not authorized for this instance",
                    code="not_authorized_for_instance",
                )
            request.state.principal = user

        # Store token on request state for downstream forwarding.
        request.state.user_token = token

        return await call_next(request)
