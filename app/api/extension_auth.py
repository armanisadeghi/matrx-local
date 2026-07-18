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
   needed on the engine. Tokens signed with HS256 are still common in
   Supabase projects — they are NOT verifiable here and will fall through
   to mode 2.
2. **Loopback presence-only (the desktop default).** When the token is
   HS256 (cannot be verified by JWKS) OR ``SUPABASE_URL`` is unset, we
   accept any non-empty Bearer. This is correct for a process that only
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

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, Request, WebSocket

from app.common.system_logger import get_logger
from app.config import SUPABASE_URL

logger = get_logger()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _supabase_jwks_url() -> Optional[str]:
    """Derive the well-known JWKS URL from ``SUPABASE_URL``.

    Returns ``None`` when ``SUPABASE_URL`` is empty, which lets the caller
    cleanly skip the JWKS path without raising.
    """
    base = (SUPABASE_URL or "").rstrip("/")
    if not base:
        return None
    return f"{base}/auth/v1/.well-known/jwks.json"


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
      * ``"hs256_token_passthrough"`` — JWKS is configured but the
        incoming token is HS256-signed (cannot be verified by JWKS).
        Engine accepts on presence. Migrate the Supabase project to
        RS256/ES256 if you want crypto verification of these tokens.
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
    elif reason == "hs256_token_passthrough":
        logger.info(
            "[extension_auth] /extension/* auth: JWKS is configured but "
            "incoming tokens are HS256-signed (Supabase project still on "
            "symmetric signing). HS256 tokens cannot be verified by JWKS, "
            "so they are accepted on presence over loopback. Migrate the "
            "Supabase project to RS256/ES256 if you want crypto "
            "verification of these tokens."
        )


# Per-kid suppression for the per-request DEBUG noise. Without this, a
# steady stream of /extension/sessions polls (every 2s) emits one DEBUG
# line per call for the same offending key id, drowning the log.
_DEBUG_FAILED_KIDS: set[str] = set()


# ---------------------------------------------------------------------------
# Rate-limited rejection logging
#
# The extension and other clients commonly poll /extension/rpc on a 2s
# interval. When the user is signed out (or the popup hasn't yet fetched a
# token), every poll fails with "missing Bearer token" — without rate
# limiting that's 30 WARNING lines per minute per polling client, which
# completely drowns the engine log and makes real warnings impossible to
# spot.
#
# Strategy: log the FIRST rejection of a given (path, reason) tuple as
# WARNING so the operator sees the issue immediately. Subsequent identical
# rejections within ``_REJECT_LOG_WINDOW_SECONDS`` are demoted to DEBUG and
# coalesced into a single "still rejecting" summary every
# ``_REJECT_SUMMARY_INTERVAL_SECONDS``. The summary tells you the
# rejection rate without restoring the per-request flood.
# ---------------------------------------------------------------------------

# Quiet gap after which the next rejection re-surfaces as a WARNING (a
# stream that stopped and later resumed is news, not suppressed noise).
_REJECT_LOG_WINDOW_SECONDS = 60.0
_REJECT_SUMMARY_INTERVAL_SECONDS = 60.0
# Hard bound on tracked (kind, path, reason) keys so arbitrary rejected
# paths (port scanners, typo'd clients) cannot grow the dict forever.
_REJECT_STATE_MAX_KEYS = 256

# Per-rejection-key state: { key: {first_seen, last_seen, last_summary, count} }
_RejectStateKey = tuple[str, str, str]  # (kind, path, reason)
_reject_log_state: dict[_RejectStateKey, dict[str, float]] = {}


def _log_rejection(kind: str, path: str, reason: str, *, method: str = "") -> None:
    """Log an auth-rejected request with rate-limit suppression.

    Args:
        kind: ``"http"`` or ``"ws"`` — purely cosmetic, distinguishes
            the two surfaces in the log line.
        path: the rejected request path. Used as part of the suppression
            key so the same path's repeated rejects coalesce, but a
            different path still surfaces immediately.
        reason: short stable identifier for *why* it was rejected
            (e.g. ``"missing_bearer"``, ``"invalid_signature"``).
        method: HTTP method (HTTP rejections only); blank for WS.

    Behaviour:
        * First reject for a (kind, path, reason) tuple → WARNING.
        * Subsequent rejects within the suppression window → DEBUG
          (silent in normal operation).
        * Every ``_REJECT_SUMMARY_INTERVAL_SECONDS`` of continuous
          rejections → INFO summary with the cumulative count.
    """
    now = time.monotonic()
    key: _RejectStateKey = (kind, path, reason)
    state = _reject_log_state.get(key)

    # A rejection stream that went quiet for the full window and then
    # resumed is news — drop the stale entry so it re-WARNs below.
    if state is not None and now - state["last_seen"] >= _REJECT_LOG_WINDOW_SECONDS:
        del _reject_log_state[key]
        state = None

    method_str = f"{method} " if method else ""
    label = "rejected" if kind == "http" else "WS rejected"

    if state is None:
        # Bound the state dict: evict the longest-idle key when full.
        if len(_reject_log_state) >= _REJECT_STATE_MAX_KEYS:
            oldest = min(
                _reject_log_state,
                key=lambda k: _reject_log_state[k]["last_seen"],
            )
            del _reject_log_state[oldest]
        _reject_log_state[key] = {
            "first_seen": now,
            "last_seen": now,
            "last_summary": now,
            "count": 1.0,
        }
        logger.warning(
            "[extension_auth] %s %s%s — %s",
            label,
            method_str,
            path,
            reason.replace("_", " "),
        )
        return

    state["count"] += 1
    state["last_seen"] = now

    # Continued rejection beyond the suppression window — emit a periodic
    # summary so the operator knows the situation hasn't resolved.
    if now - state["last_summary"] >= _REJECT_SUMMARY_INTERVAL_SECONDS:
        elapsed = now - state["first_seen"]
        rate = state["count"] / elapsed if elapsed > 0 else 0.0
        logger.info(
            "[extension_auth] still %s %s%s (%s) — %.0f rejections in last %.0fs (%.1f/s)",
            label,
            method_str,
            path,
            reason.replace("_", " "),
            state["count"],
            elapsed,
            rate,
        )
        state["last_summary"] = now
        # Reset count so the next summary covers the next window without
        # double-counting historic data.
        state["first_seen"] = now
        state["count"] = 0.0
        return

    # Within suppression window after the initial WARNING — DEBUG only.
    logger.debug(
        "[extension_auth] %s %s%s — %s (suppressed; count=%.0f)",
        label,
        method_str,
        path,
        reason.replace("_", " "),
        state["count"],
    )


def _debug_log_jwks_failure(token: str, exc: Exception) -> None:
    """DEBUG-log a JWKS validation failure once per (kid, error-type).

    Per-request DEBUG output is too noisy when the same token (or family
    of tokens with the same ``kid``) keeps arriving. We hash the
    combination of ``kid`` and exception class so a genuinely new failure
    still surfaces while the steady-state poll-loop noise is muted.
    """
    try:
        import jwt as _jwt

        kid = _jwt.get_unverified_header(token).get("kid") or "<no-kid>"
    except Exception:
        kid = "<unparseable>"
    cache_key = f"{kid}:{type(exc).__name__}"
    if cache_key in _DEBUG_FAILED_KIDS:
        return
    _DEBUG_FAILED_KIDS.add(cache_key)
    logger.debug(
        "[extension_auth] JWKS validation failed for kid=%s: %s "
        "(further failures with this kid are suppressed)",
        kid,
        exc,
    )


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
# ---------------------------------------------------------------------------

# Cached PyJWKClient — the client itself caches signing keys with a TTL,
# but constructing it does an HTTP fetch the first time, so we hold one
# per process keyed by JWKS URL.
_jwks_client_cache: dict[str, Any] = {}


def _get_jwks_client(jwks_url: str) -> Any:
    """Return a cached ``jwt.PyJWKClient`` for the given URL.

    The PyJWKClient caches signing keys for one hour; we re-use the same
    client object so that cache survives across requests.
    """
    cached = _jwks_client_cache.get(jwks_url)
    if cached is not None:
        return cached
    # Lazy import — keep ``jwt`` out of the module-import cycle so the
    # catalog regen / tooling that touches this file works without a
    # mandatory PyJWT runtime.
    import jwt as _jwt

    client = _jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
    _jwks_client_cache[jwks_url] = client
    return client


def _decode_with_jwks_sync(token: str, jwks_url: str) -> dict[str, Any]:
    """Verify ``token`` against Supabase JWKS. Synchronous (run in a thread).

    PyJWT's ``decode`` is CPU-bound (signature verification) and the JWKS
    client's first call is blocking I/O — both warrant ``asyncio.to_thread``.

    ``verify_aud=False``: Supabase tokens carry ``aud="authenticated"``
    which we don't pin since we accept any authenticated user. ``verify_exp``
    stays default-on, so expired tokens are rejected.
    """
    import jwt as _jwt

    client = _get_jwks_client(jwks_url)
    signing_key = client.get_signing_key_from_jwt(token)
    return _jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256", "RS256"],
        options={"verify_aud": False},
    )


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
      * RS256/ES256 + JWKS configured → verify the signature locally.
      * Otherwise (HS256 — the Supabase project's mode — or no JWKS) → the
        engine cannot check the signature itself (it holds no symmetric
        secret by design). Ask the issuer instead: introspect the token via
        the Supabase auth server. A confirmed token yields a fully-verified
        principal even for HS256.

    Trust fallback when introspection cannot confirm the token:
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

    # Peek at the token header to choose the validation path.
    try:
        import jwt as _jwt

        alg = _jwt.get_unverified_header(token).get("alg")
    except Exception as exc:
        # Malformed token. Fail closed.
        raise exc

    jwks_url = _supabase_jwks_url()

    # JWKS path: only meaningful for asymmetric tokens with a configured URL.
    if jwks_url and alg != "HS256":
        try:
            payload = await asyncio.to_thread(
                _decode_with_jwks_sync, token, jwks_url
            )
            return _principal_from_payload(payload, token)
        except Exception as exc:
            # JWKS path was applicable but rejected the token (bad sig,
            # expired, unreachable issuer, etc.). Fail closed — do NOT
            # silently downgrade to presence-only for an asymmetric token
            # that failed crypto verification.
            _debug_log_jwks_failure(token, exc)
            raise exc

    # HS256 (or no JWKS): verify by introspection against the auth server.
    from app.api.remote_auth import verify_supabase_token

    verified = await verify_supabase_token(token)
    if verified is not None:
        return ExtensionPrincipal(
            user_id=verified.user_id,
            email=verified.email,
            is_anon=verified.is_anon,
            raw_token=token,
            verified=True,
        )

    # Introspection could not confirm the token.
    if via_tunnel:
        raise ValueError("unverified token over tunnel")

    # Direct loopback: presence-only is acceptable (the socket is the boundary).
    _log_startup_notice_once(
        "hs256_token_passthrough" if jwks_url else "presence_only"
    )
    return _degraded_principal(token)


def _principal_from_payload(
    payload: dict[str, Any], raw_token: str
) -> ExtensionPrincipal:
    """Construct a principal from a verified JWT payload."""
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        # A signature-valid token without a ``sub`` claim is not actionable;
        # treat it as a hard failure rather than silently empty.
        raise ValueError("JWT missing 'sub' claim")

    email_raw = payload.get("email")
    email = email_raw if isinstance(email_raw, str) and email_raw else None

    role = payload.get("role")
    is_anon = role == "anon" or bool(payload.get("is_anonymous"))

    return ExtensionPrincipal(
        user_id=sub,
        email=email,
        is_anon=is_anon,
        raw_token=raw_token,
        verified=True,
    )


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
