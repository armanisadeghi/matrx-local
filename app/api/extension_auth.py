"""Supabase JWT validation for the /extension/* surface.

Background
----------
The engine's existing ``AuthMiddleware`` (``app/api/auth.py``) only checks
that *some* Bearer token is present on protected requests — it does not
verify the signature. That posture is acceptable for the loopback-only
boundary the engine binds to today (``127.0.0.1`` + Tauri/extension as the
only callers): the trust boundary is "if you can reach this socket on
loopback, you're already inside the user's machine."

This module adds an OPTIONAL second layer specifically for the
``/extension/*`` routes — the surface the Chrome extension calls into.
Other engine routes (``/ws``, ``/tools/*``, etc.) keep the permissive
Bearer check; the user trusts their own desktop UI and CLI clients.

Verification posture
--------------------
The engine runs on the user's own machine. It cannot have a server-side
JWT signing secret (HS256/``SUPABASE_JWT_SECRET``) — there is no secure
place to put one and no point in trying. We support exactly two modes:

1. **JWKS / asymmetric (only crypto path).** When ``SUPABASE_URL`` is set
   AND the project issues asymmetric tokens (RS256/ES256), we fetch the
   public signing keys from
   ``<SUPABASE_URL>/auth/v1/.well-known/jwks.json`` via ``jwt.PyJWKClient``
   (with a 1-hour key cache) and verify the signature locally. No secret
   needed on the engine. The key fetch, cache and algorithm allow-list are
   owned by ``app/api/remote_auth.py`` and consumed here. The Matrx project
   signs ES256 (verified 2026-09-20); a legacy HS256 token is NOT verifiable
   here and falls through to mode 2.
2. **Loopback presence-only (the degraded path).** When the token is
   HS256 (cannot be verified without a symmetric secret this engine must
   not hold) OR ``SUPABASE_URL`` is unset, we accept any non-empty Bearer.
   This is correct for a process that only
   listens on ``127.0.0.1``: the security boundary is the loopback
   socket, not the JWT signature.

Token presence is always required; missing-token requests are rejected
with ``401`` / WebSocket close ``1008`` regardless of configuration.

Public API
----------
``ExtensionPrincipal`` — dataclass returned by both validators.
``validate_extension_principal(request)`` — FastAPI HTTP dependency.
``validate_extension_principal_ws(websocket)`` — async helper for the
    ``/extension/ws`` and ``/extension/bridge-events`` upgrades, where
    FastAPI's ``Depends`` machinery does not apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, WebSocket

from app.api.auth_rejection_log import log_rejection
from app.api.remote_auth import supabase_jwks_url
from app.common.system_logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# One-time startup notice. The engine is a desktop sidecar — its security
# boundary is the loopback socket, not the JWT signature. We log the posture
# once at first use so operators understand what mode they're in, but at
# INFO (not WARNING) — this is the EXPECTED mode for desktop installs.
_STARTUP_NOTICE_LOGGED = False


def _log_startup_notice_once(reason: str) -> None:
    """Emit a single INFO line describing the auth posture.

    Reasons:
      * ``"presence_only"`` — no JWKS configured (no SUPABASE_URL).
        Engine accepts any non-empty Bearer on loopback. This is the
        normal mode for desktop installs.
      * ``"token_unverifiable"`` — JWKS is configured, but this token could
        not be CHECKED: it is a legacy HS256 token (verifiable only with a
        symmetric secret this engine must not hold), or the project's key set
        was out of reach. Accepted on presence over loopback only; over the
        tunnel it is refused.
    """
    global _STARTUP_NOTICE_LOGGED
    if _STARTUP_NOTICE_LOGGED:
        return
    _STARTUP_NOTICE_LOGGED = True

    if reason == "presence_only":
        logger.info(
            "[extension_auth] /extension/* auth: presence-only mode "
            "(no JWKS configured). Accepts any non-empty Bearer over "
            "loopback. This is the expected mode for desktop installs."
        )
    elif reason == "token_unverifiable":
        logger.info(
            "[extension_auth] /extension/* auth: JWKS is configured, but a "
            "token arrived that this engine could not CHECK — a legacy HS256 "
            "token (no symmetric secret here, by design) or the project's key "
            "set out of reach. It is accepted on presence over loopback only; "
            "over the tunnel it is refused. A token the engine actively "
            "REFUSES is rejected on every surface. The project signs ES256 as "
            "of 2026-09-20, so a user holding an HS256 token gets a verifiable "
            "one by signing in again."
        )

# Rate-limited rejection logging lives in ONE place for every surface:
# app/api/auth_rejection_log.py. It used to live here and only here, which is
# why app/api/auth.py logged ~37,000 unthrottled rejections in 72h (SR-05).


def _log_rejection(kind: str, path: str, reason: str, *, method: str = "") -> None:
    """Log an auth-rejected /extension/* request with rate-limit suppression."""
    log_rejection("extension_auth", kind, path, reason, method=method)



# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtensionPrincipal:
    """Validated identity for an inbound /extension/* request.

    Attributes:
        user_id: JWT ``sub`` claim. For Supabase, this is the auth user UUID.
            Empty string in degraded-fallback mode (when validation is
            disabled and we accept the token unverified).
        email: JWT ``email`` claim if present, otherwise ``None``.
        is_anon: ``True`` when the JWT carries ``role: 'anon'`` or
            ``is_anonymous: true`` (Supabase guest sessions). Always
            ``False`` in degraded-fallback mode.
        raw_token: Original token string, for downstream forwarding to the
            scraper / aidream backends that may need to re-authenticate
            the same user.
        verified: ``True`` when the signature was cryptographically
            verified, ``False`` when this principal came from the
            degraded-fallback path. Lets handlers branch on trust level
            without re-checking config state.
    """

    user_id: str
    email: Optional[str]
    is_anon: bool
    raw_token: str
    verified: bool
    # True when the bearer was the engine-issued pairing token rather than a
    # Supabase JWT. A paired caller is by definition one of the owner's own
    # devices (the token is only obtainable over loopback or by copying it
    # from the desktop UI), so pairing satisfies the tunnel owner check.
    via_pairing: bool = False


# ---------------------------------------------------------------------------
# JWKS path (preferred)
#
# The JWKS URL, the key cache, the algorithm allow-list and the failure
# cooldown live in exactly ONE place for the whole engine:
# ``app/api/remote_auth.py``. This surface consumes them — a second
# PyJWKClient here is how one surface silently keeps accepting what the other
# rejects.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _extract_bearer(request: Request) -> Optional[str]:
    """Extract a Bearer token from ``Authorization`` header or ``?token=``."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        candidate = auth[7:].strip()
        if candidate:
            return candidate
    qp_token = request.query_params.get("token")
    if isinstance(qp_token, str) and qp_token.strip():
        return qp_token.strip()
    return None


def _extract_bearer_ws(websocket: WebSocket) -> Optional[str]:
    """Extract a Bearer token from a WebSocket upgrade request.

    Same precedence as the HTTP path. Browsers cannot set custom headers on
    a WS upgrade so the ``?token=`` query param is the canonical channel
    for the extension; the header path stays available for non-browser
    clients (curl, the desktop test panel).
    """
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        candidate = auth[7:].strip()
        if candidate:
            return candidate
    qp_token = websocket.query_params.get("token")
    if isinstance(qp_token, str) and qp_token.strip():
        return qp_token.strip()
    return None


# ---------------------------------------------------------------------------
# Core verification
# ---------------------------------------------------------------------------


async def _verify_token(token: str, *, via_tunnel: bool = False) -> ExtensionPrincipal:
    """Verify ``token`` and return a populated principal.

    Verification strategy:
      * ES256/RS256 + JWKS configured → verify the signature locally against
        the project's published key (``remote_auth``, the one primitive).
        This is the project's mode: it signs ES256.
      * Otherwise (a legacy HS256 token, or no JWKS) → the engine cannot check
        the signature itself and holds no symmetric secret by design, so
        ``remote_auth`` reports the token UNVERIFIABLE rather than invalid.

    Trust fallback when the token could not be verified:
      * via tunnel → REJECT. Remote callers must present a verified identity;
        there is no presence-only bypass over the public bridge.
      * direct loopback → degraded (presence-only) principal. The loopback
        socket is the trust boundary for local callers.

    Raises on malformed tokens, failed crypto verification, or an
    unverifiable token arriving over the tunnel. Callers map to 401 / 1008.
    """
    # Engine-issued pairing token — the extension's canonical credential
    # (it never sends the user's Supabase JWT to a probed localhost port,
    # per its audit P1-5). Constant-time compare inside. A paired caller is
    # one of the owner's own devices, so this is accepted over the tunnel
    # too; the token itself is only obtainable over loopback.
    from app.services.pairing import matches_pair_token

    if matches_pair_token(token):
        return ExtensionPrincipal(
            user_id="",
            email=None,
            is_anon=False,
            raw_token=token,
            verified=True,
            via_pairing=True,
        )

    # ONE verifier for the whole engine. It owns the JWKS key cache, the
    # algorithm allow-list, the bounded resolve and the refresh budget — and,
    # critically, the difference between "this token is refused" and "this
    # machine could not check it", which is the only thing this surface is
    # allowed to downgrade on loopback.
    from app.api.remote_auth import verify_supabase_token_result

    verification = await verify_supabase_token_result(token)
    verified = verification.user
    if verified is not None:
        return ExtensionPrincipal(
            user_id=verified.user_id,
            email=verified.email,
            is_anon=verified.is_anon,
            raw_token=token,
            verified=True,
        )

    if verification.status == "invalid":
        # A verdict: malformed, expired, wrong signature, or an algorithm we
        # refuse. Fail closed on EVERY surface — loopback included. There is no
        # presence-only downgrade for a token we actively rejected.
        raise ValueError("token failed verification")

    # ``unavailable`` / ``unconfigured``: we could not check it at all.
    if via_tunnel:
        raise ValueError("unverified token over tunnel")

    # Direct loopback: presence-only is acceptable (the socket is the boundary).
    _log_startup_notice_once(
        "token_unverifiable" if supabase_jwks_url() else "presence_only"
    )
    return _degraded_principal(token)


def _degraded_principal(token: str) -> ExtensionPrincipal:
    """Construct a fallback principal when no verification path is configured.

    Used only when the engine is running without JWT-secret + JWKS — the
    loopback-only happy path. The principal is marked ``verified=False``
    so any downstream code that wants to gate on real identity can do so.
    """
    return ExtensionPrincipal(
        user_id="",
        email=None,
        is_anon=False,
        raw_token=token,
        verified=False,
    )


# ---------------------------------------------------------------------------
# Public — HTTP dependency
# ---------------------------------------------------------------------------


async def validate_extension_principal(request: Request) -> ExtensionPrincipal:
    """FastAPI dependency: verify the inbound Bearer token, return principal.

    Raises:
        HTTPException(401): missing token, invalid signature, or expired
        token. The detail message intentionally stays generic ("Invalid
        or missing credentials") to avoid leaking which leg of the
        verification failed.

    Returns:
        ExtensionPrincipal — ``verified=True`` on a real signature check,
        ``verified=False`` in graceful-degradation mode.
    """
    token = _extract_bearer(request)
    if not token:
        _log_rejection(
            "http",
            request.url.path,
            "missing_bearer_token",
            method=request.method,
        )
        raise HTTPException(
            status_code=401,
            detail="Authorization Bearer token required",
        )

    from app.api.remote_auth import headers_indicate_tunnel, is_instance_owner

    _via_tunnel = headers_indicate_tunnel(request.headers)
    try:
        principal = await _verify_token(token, via_tunnel=_via_tunnel)
    except Exception as exc:
        _log_rejection(
            "http",
            request.url.path,
            f"jwt_validation_failed_{type(exc).__name__}",
            method=request.method,
        )
        # Detailed exception goes at DEBUG to keep the WARNING line stable
        # while still preserving the full error in the dev log.
        logger.debug(
            "[extension_auth] %s %s validation exc detail: %s",
            request.method,
            request.url.path,
            exc,
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired credentials",
        ) from exc

    # Owner-only over the tunnel: a valid token from another AI Matrx user must
    # not reach this instance's extension/sandbox surface. Paired principals
    # skip this — the pairing token is itself proof of owner-device access.
    if _via_tunnel and not principal.via_pairing and not await is_instance_owner(principal.user_id):
        _log_rejection("http", request.url.path, "not_instance_owner", method=request.method)
        raise HTTPException(status_code=403, detail="Not authorized for this instance")

    request.state.principal = principal
    return principal


# ---------------------------------------------------------------------------
# Public — WebSocket helper
# ---------------------------------------------------------------------------


# WS close codes — RFC 6455 / RFC 7235 align with these conventions:
WS_CLOSE_POLICY_VIOLATION = 1008  # Auth failures (missing/invalid token)


async def validate_extension_principal_ws(
    websocket: WebSocket,
) -> Optional[ExtensionPrincipal]:
    """Verify the inbound WS upgrade. Closes the socket on failure.

    FastAPI's ``Depends`` machinery only runs *after* ``websocket.accept()``
    has resolved, which means a dependency-raised ``HTTPException`` would
    bubble up *post-handshake* and result in a confused client. The
    correct pattern for WS auth is to validate inline before ``accept()``
    and close with code ``1008`` on rejection — that's what this helper
    does.

    Returns:
        ExtensionPrincipal on success, or ``None`` if the socket was
        already closed (caller should ``return`` immediately on ``None``).
    """
    token = _extract_bearer_ws(websocket)
    if not token:
        _log_rejection("ws", websocket.url.path, "missing_bearer_token")
        await websocket.close(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason="Missing auth token",
        )
        return None

    from app.api.remote_auth import headers_indicate_tunnel, is_instance_owner

    _via_tunnel = headers_indicate_tunnel(websocket.headers)
    try:
        principal = await _verify_token(token, via_tunnel=_via_tunnel)
    except Exception as exc:
        _log_rejection(
            "ws",
            websocket.url.path,
            f"jwt_validation_failed_{type(exc).__name__}",
        )
        logger.debug(
            "[extension_auth] WS %s validation exc detail: %s",
            websocket.url.path,
            exc,
        )
        await websocket.close(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason="Invalid or expired credentials",
        )
        return None

    if _via_tunnel and not principal.via_pairing and not await is_instance_owner(principal.user_id):
        _log_rejection("ws", websocket.url.path, "not_instance_owner")
        await websocket.close(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason="Not authorized for this instance",
        )
        return None

    return principal
