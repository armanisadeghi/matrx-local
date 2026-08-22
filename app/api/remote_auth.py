"""Shared request authentication for both local and tunnel-reachable surfaces.

Why this module exists
----------------------
The engine binds ``127.0.0.1`` only, and historically the auth posture was
"if you can reach the loopback socket you're already inside the user's
machine, so a *present* Bearer is enough." That is sound — until the
Cloudflare tunnel is enabled. The tunnel deliberately bridges the public
internet to the loopback socket so the user can drive their machine from a
phone or remote browser (this is a designed feature, not an accident). The
moment that bridge exists, "reached loopback" no longer means "is local",
and presence-only auth becomes remote code execution.

This module draws the missing distinction and verifies identity:

1. :func:`request_is_via_tunnel` — tunnel traffic transits Cloudflare's
   edge, which stamps ``Cf-Ray`` / ``Cf-Connecting-Ip`` headers on every
   request. A direct loopback request (Tauri webview, local browser, the
   Rust shell calling ``/admin/*``) has none. A local process can only
   *add* these headers — which makes its request look *more* remote and
   therefore subject to *stricter* checks — it cannot strip them off a
   genuine tunnel request. So presence of the marker is a safe one-way
   signal: "treat as untrusted remote."

2. :func:`verify_supabase_token` — the Supabase project signs JWTs with
   HS256. The engine has no secure place for the symmetric signing secret
   (CLAUDE.md hard rule) and cannot verify HS256 locally. The correct
   verification for a token you can't check yourself is to ask the issuer:
   we call GoTrue's ``GET /auth/v1/user`` with the token. A 200 means the
   auth server validated the signature + expiry and hands back the user.
   Results are cached briefly so the hot path doesn't hammer the network.

3. A local API key (``MATRX_LOCAL_API_KEY`` / ``TEST_MODE``) for headless
   and test callers that have no Supabase session.

Trust contract enforced by callers (see ``auth.py`` / ``extension_auth.py``):
  * Direct loopback  → presence is acceptable (the loopback socket is the
    boundary); identity is still populated opportunistically.
  * Via tunnel       → a *verified* Supabase token (or the local API key)
    is REQUIRED for every non-trivial route; no public bypass beyond
    health/version/discovery.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Literal, Optional

import httpx

from app.common.system_logger import get_logger
from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL

logger = get_logger()


# ---------------------------------------------------------------------------
# Tunnel detection
# ---------------------------------------------------------------------------

# Headers stamped by Cloudflare's edge on tunnel traffic. cloudflared forwards
# these to the local origin; a direct loopback client never sets them.
_TUNNEL_MARKER_HEADERS = ("cf-ray", "cf-connecting-ip", "cf-worker")


def headers_indicate_tunnel(headers) -> bool:
    """Return True when the request arrived via the Cloudflare tunnel.

    ``headers`` is any case-insensitive mapping (Starlette ``Headers`` for
    HTTP, ``websocket.headers`` for WS). The check is presence-only and
    one-directional safe (see module docstring).
    """
    for name in _TUNNEL_MARKER_HEADERS:
        if headers.get(name):
            return True
    return False


# ---------------------------------------------------------------------------
# Supabase token verification (introspection — works for HS256)
#
# Note: there is intentionally no "local API key" accepted here. Over the
# tunnel only a verified Supabase identity (owner) is accepted; on direct
# loopback the presence-only boundary in AuthMiddleware already accepts any
# token (the loopback socket is the trust boundary), so a separate local-key
# concept would be redundant and a static secret accepted remotely would only
# widen the attack surface.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedUser:
    user_id: str
    email: Optional[str]
    is_anon: bool


TokenVerificationStatus = Literal[
    "verified",
    "invalid",
    "unavailable",
    "unconfigured",
]


@dataclass(frozen=True)
class TokenVerificationResult:
    """Issuer-verification result with invalid and unavailable kept distinct."""

    status: TokenVerificationStatus
    user: Optional[VerifiedUser] = None


# Positive results cached briefly to keep the auth hot-path off the network.
# Keyed by a hash of the token (never store the raw token in a process-global
# dict). Negative results get a shorter TTL so a freshly-issued token isn't
# locked out by a stale "invalid" entry.
_POSITIVE_TTL_SECONDS = 60.0
_NEGATIVE_TTL_SECONDS = 5.0
_MAX_CACHE_ENTRIES = 512

# key -> (expires_at, VerifiedUser | None)
_verify_cache: dict[str, tuple[float, Optional[VerifiedUser]]] = {}


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> tuple[bool, Optional[VerifiedUser]]:
    """Return (hit, value). ``value`` is None for a cached negative."""
    entry = _verify_cache.get(key)
    if entry is None:
        return False, None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        _verify_cache.pop(key, None)
        return False, None
    return True, value


def _cache_put(key: str, value: Optional[VerifiedUser]) -> None:
    if len(_verify_cache) >= _MAX_CACHE_ENTRIES:
        # Cheap eviction: drop everything already expired, then, if still
        # full, clear the map. Auth caches are small and short-lived so a
        # periodic full flush is acceptable and avoids unbounded growth.
        now = time.monotonic()
        for k in [k for k, (exp, _) in _verify_cache.items() if exp <= now]:
            _verify_cache.pop(k, None)
        if len(_verify_cache) >= _MAX_CACHE_ENTRIES:
            _verify_cache.clear()
    ttl = _POSITIVE_TTL_SECONDS if value is not None else _NEGATIVE_TTL_SECONDS
    _verify_cache[key] = (time.monotonic() + ttl, value)


async def verify_supabase_token_result(token: str) -> TokenVerificationResult:
    """Validate a token against the configured Supabase Auth issuer.

    ``invalid`` is an issuer verdict and may be cached briefly. ``unavailable``
    means the issuer could not be reached or returned a transient server error;
    callers that persist credentials must not erase a previously-good session
    merely because validation infrastructure is temporarily unavailable.
    """
    if not token:
        return TokenVerificationResult("invalid")
    if not (SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY):
        return TokenVerificationResult("unconfigured")

    key = _token_key(token)
    hit, cached = _cache_get(key)
    if hit:
        return TokenVerificationResult(
            "verified" if cached is not None else "invalid",
            cached,
        )

    url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/user"
    headers = {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:
        logger.debug("[remote_auth] token introspection network error: %s", exc)
        return TokenVerificationResult("unavailable")

    if resp.status_code in {401, 403}:
        _cache_put(key, None)
        return TokenVerificationResult("invalid")
    if resp.status_code != 200:
        logger.debug(
            "[remote_auth] token introspection temporarily unavailable (HTTP %s)",
            resp.status_code,
        )
        return TokenVerificationResult("unavailable")

    try:
        data = resp.json()
    except Exception:
        return TokenVerificationResult("unavailable")

    uid = data.get("id")
    if not isinstance(uid, str) or not uid:
        return TokenVerificationResult("unavailable")

    email = data.get("email")
    role = data.get("role") or data.get("aud")
    is_anon = role == "anon" or bool(data.get("is_anonymous"))
    user = VerifiedUser(
        user_id=uid,
        email=email if isinstance(email, str) and email else None,
        is_anon=is_anon,
    )
    _cache_put(key, user)
    return TokenVerificationResult("verified", user)


async def verify_supabase_token(token: str) -> Optional[VerifiedUser]:
    """Validate a Supabase access token via the auth server; cache the result.

    Returns a :class:`VerifiedUser` when the token is genuine and unexpired,
    or ``None`` when it is invalid/expired/unverifiable. Never raises — a
    network failure is treated as "not verified" (fail closed) so an
    unreachable auth server cannot turn into an auth bypass.
    """
    return (await verify_supabase_token_result(token)).user


def invalidate_token(token: str) -> None:
    """Drop a token from the verification cache (e.g. on logout)."""
    if token:
        _verify_cache.pop(_token_key(token), None)


async def is_instance_owner(user_id: str) -> bool:
    """Return True when ``user_id`` is the owner who signed into THIS instance.

    Remote (tunnel) callers must be the machine's own owner — a valid token
    from a different AI Matrx account must not be able to drive someone else's
    machine. The owner is the user_id persisted in auth_tokens when the desktop
    UI signed in. If no owner is recorded yet (nobody has signed in locally),
    no remote caller can match — remote control requires a prior local sign-in.
    """
    if not user_id:
        return False
    try:
        from app.services.local_db.repositories import TokenRepo

        owner = await TokenRepo().get_owner_user_id()
    except Exception:
        # Fail closed: if we can't determine the owner, don't authorize remote.
        logger.debug("[remote_auth] owner lookup failed", exc_info=True)
        return False
    return bool(owner) and owner == user_id
