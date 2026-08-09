"""Auth middleware and OAuth callback route for the local engine.

Since the engine runs on localhost, we don't validate JWTs here — that happens
on the remote scraper server. We just ensure callers provide a Bearer token
(the Supabase JWT or the local API key) to prevent unauthorized access from
other processes on the machine.

Public routes (health, discovery, and the OAuth callback) are excluded from
the auth check. The OAuth callback MUST be public because the external browser
delivers it with no auth token — it's the result of an OAuth flow, not an
authenticated API call.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
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
# read-only metadata, OAuth callback). No user data, no mutation of identity.
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
        "/chat/agents",  # read-only agent/prompt list, no user data — frontend calls this before auth
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
        # OAuth callback — the external browser delivers this with no token.
        # Auth is completed inside the Tauri webview after the code is forwarded.
        "/auth/callback",
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
) -> JSONResponse:
    """Return the auth error shape expected by the target surface."""
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


# ---------------------------------------------------------------------------
# OAuth callback router
# ---------------------------------------------------------------------------

auth_router = APIRouter(tags=["auth"])

_SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Matrx — Signed In</title>
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
      min-height:100vh;display:flex;align-items:center;justify-content:center;
      background:#09090b;color:#fafafa;
    }
    .card{
      text-align:center;max-width:340px;padding:2.5rem 2rem;
      background:#18181b;border:1px solid #27272a;border-radius:1.25rem;
      box-shadow:0 25px 50px -12px rgba(0,0,0,.6);
    }
    .icon{
      width:56px;height:56px;margin:0 auto 1.25rem;
      border-radius:.875rem;background:rgba(132,204,22,.12);
      display:flex;align-items:center;justify-content:center;
    }
    h1{font-size:1.25rem;font-weight:600;margin-bottom:.5rem;}
    p{font-size:.875rem;color:#a1a1aa;line-height:1.5;margin-bottom:1.5rem;}
    .close-hint{
      font-size:.75rem;color:#52525b;
      border-top:1px solid #27272a;padding-top:1rem;margin-top:1rem;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#84cc16"
           stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="20 6 9 17 4 12"/>
      </svg>
    </div>
    <h1>Signed in successfully!</h1>
    <p>You're being returned to AI&nbsp;Matrx now.<br>This tab will close automatically.</p>
    <div class="close-hint">You can also close this tab manually.</div>
  </div>
  <script>
    // Attempt to close the tab automatically after a short delay.
    // This works in most browsers when the tab was opened programmatically.
    setTimeout(() => { try { window.close(); } catch(_) {} }, 2000);
  </script>
</body>
</html>
"""


@auth_router.get("/auth/callback", response_class=HTMLResponse)
async def oauth_callback(request: Request):
    """
    OAuth redirect target for the Tauri desktop app.

    Supabase completes authentication in the user's external browser and then
    redirects to this local URL.  We capture whichever parameters Supabase
    included (PKCE ``code``, or implicit ``access_token`` / ``refresh_token``
    in the URL fragment — though fragments are never sent to the server, so
    for implicit flow the frontend must parse them itself from the redirected
    page URL).

    Once the parameters are captured we broadcast them to every connected
    WebSocket client so the Tauri webview can complete the session exchange
    without any page navigation.
    """
    # Import here to avoid a circular import with main.py
    from app.main import websocket_manager  # type: ignore[attr-defined]

    params = dict(request.query_params)
    logger.info("[oauth_callback] received params: %s", list(params.keys()))

    # Broadcast whichever parameters arrived so the webview can handle them.
    # The frontend checks for `code` (PKCE) first, then falls back to
    # `access_token` + `refresh_token` (implicit).
    payload: dict = {"type": "oauth-callback"}

    if "code" in params:
        payload["code"] = params["code"]
        logger.info("[oauth_callback] PKCE code received, broadcasting to webview")
    elif "access_token" in params:
        payload["access_token"] = params["access_token"]
        payload["refresh_token"] = params.get("refresh_token", "")
        logger.info(
            "[oauth_callback] implicit tokens received, broadcasting to webview"
        )
    else:
        logger.warning(
            "[oauth_callback] unexpected params — forwarding raw: %s",
            list(params.keys()),
        )
        payload["raw"] = params

    # Broadcast the OAuth credentials ONLY to direct-loopback WebSocket
    # clients (the Tauri webview signing in is one of these). Tunnel-borne
    # connections are remote and untrusted — sending a freshly-minted OAuth
    # code / access token to them would let any remote WS client harvest
    # another user's login. The desktop sign-in webview always connects over
    # direct loopback, so it still receives the payload.
    # Snapshot the dict — a client (dis)connecting during an awaited send
    # would otherwise raise mid-iteration and 500 the OAuth callback, losing
    # the sign-in broadcast.
    for conn in list(websocket_manager.connections.values()):
        if getattr(conn, "via_tunnel", False):
            continue
        try:
            await websocket_manager._send(conn, payload)
        except Exception:
            logger.warning(
                "OAuth credential broadcast to one client failed", exc_info=True
            )

    return HTMLResponse(_SUCCESS_HTML)


# ---------------------------------------------------------------------------
# Auth middleware (unchanged)
# ---------------------------------------------------------------------------


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
                    status_code=403,
                    message="Not authorized for this instance",
                    code="not_authorized_for_instance",
                )
            request.state.principal = user

        # Store token on request state for downstream forwarding.
        request.state.user_token = token

        return await call_next(request)
