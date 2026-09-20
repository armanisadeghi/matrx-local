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

2. :func:`verify_supabase_token` — the Matrx Supabase project signs access
   tokens with **ES256**, an asymmetric algorithm whose *public* verifying
   keys are published at ``/auth/v1/.well-known/jwks.json``. A public key
   needs no secure storage, so the engine verifies the signature ITSELF,
   locally, with PyJWT — exactly as trusted as asking the auth server,
   because the signature is cryptographically checked either way.

   This module used to call GoTrue's ``GET /auth/v1/user`` instead, on the
   premise that the project signed HS256 and the engine had no safe place
   for a symmetric secret. **That premise is stale** (the project rotated to
   ES256; verified 2026-09-20), and the round trip it justified put a
   5s-timeout network call on the auth hot path of every tunnel-reachable
   request — so a network blip locked the user out of their own desktop app.
   Contract: ``common-docs/systems/platform/proxy-identity/FEATURE.md`` —
   local verification instead of a per-request auth-server round trip, a
   BOUNDED resolve, and an authority we could not REACH is never read as a
   signed-out person.

   Everything this engine needs is a standard JWT claim: ``sub``, ``email``,
   ``role``/``aud``, ``is_anonymous``. Claims do NOT carry ``created_at``,
   ``identities``, ``last_sign_in_at``, ``factors`` or the ``*_confirmed_at``
   fields — a future call site that needs one of those, or that deliberately
   wants a per-request revocation check, keeps the network call ON PURPOSE
   and writes the reason down.

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

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

from app.common.system_logger import get_logger
from app.config import SUPABASE_URL

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
# Supabase token verification (local signature check against the project JWKS)
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
    """Local-verification result.

    The three failure statuses answer three different questions and must never
    be collapsed:

    ``invalid``       we REFUSE this token: the signature does not match the
                      project's published key, it has expired, it is
                      malformed, or it names an algorithm outside the
                      allow-list (``none``, HS384, an invention). A verdict.
                      Signing in again fixes it, and it is the only failure a
                      caller may treat as "this session is over".
    ``unavailable``   we could not CHECK the token: the key set could not be
                      fetched, the project publishes no key for its ``kid``,
                      or it is a legacy HS256 token (verifiable only with a
                      symmetric secret this engine must not hold). Nothing is
                      known about the session — per the proxy-identity
                      contract, an authority we could not reach is never a
                      signed-out person, so callers that persist credentials
                      MUST NOT erase a previously-good session on this.
    ``unconfigured``  this engine has no Supabase URL at all, so there is no
                      issuer to verify against.
    """

    status: TokenVerificationStatus
    user: Optional[VerifiedUser] = None


# Verification is local, so this cache exists only to skip repeated signature
# math for the same token on a hot path — not to dodge the network. Keyed by a
# hash of the token (never store the raw token in a process-global dict).
# Negative results get a shorter TTL so a freshly-issued token isn't locked out
# by a stale "invalid" entry.
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


def _cache_put(key: str, value: Optional[VerifiedUser], ttl: Optional[float] = None) -> None:
    if len(_verify_cache) >= _MAX_CACHE_ENTRIES:
        # Cheap eviction: drop everything already expired, then, if still
        # full, clear the map. Auth caches are small and short-lived so a
        # periodic full flush is acceptable and avoids unbounded growth.
        now = time.monotonic()
        for k in [k for k, (exp, _) in _verify_cache.items() if exp <= now]:
            _verify_cache.pop(k, None)
        if len(_verify_cache) >= _MAX_CACHE_ENTRIES:
            _verify_cache.clear()
    if ttl is None:
        ttl = _POSITIVE_TTL_SECONDS if value is not None else _NEGATIVE_TTL_SECONDS
    if ttl <= 0:
        return
    _verify_cache[key] = (time.monotonic() + ttl, value)


# ---------------------------------------------------------------------------
# JWKS: the ONE local-verification primitive in this repo
#
# app/api/extension_auth.py consumes ``verify_supabase_token_result`` rather
# than building a second PyJWKClient — one key cache, one algorithm allow-list,
# one refresh budget, one set of verdicts. A second copy is how one surface
# silently keeps accepting what the other rejects.
#
# Shape copied from the platform's existing verifier
# (aidream/packages/matrx-connect/matrx_connect/middleware/auth.py): our own
# kid -> key map in front of PyJWT's client, one refresh at a time, and a
# refresh cooldown. That is what keeps the steady state at ZERO network calls
# and stops unknown-kid traffic from turning this engine into a JWKS hammer.
# ---------------------------------------------------------------------------

# The algorithms whose verifying key is PUBLIC. HS256 is deliberately absent:
# verifying it needs the project's symmetric secret, which a program running on
# the user's machine has no safe place to hold (CLAUDE.md configuration
# posture). An HS256 token is reported ``unavailable``, never ``invalid`` — we
# did not check it, so we know nothing about it. Any OTHER algorithm (``none``,
# HS384/512, an attacker's invention) is a token we refuse to accept at all,
# which IS a verdict: ``invalid``.
VERIFY_ALGORITHMS = ("ES256", "RS256")

# The resolve is BOUNDED (proxy-identity rule 2). PyJWT's key fetch is blocking
# urllib; its own timeout covers the socket and the outer wait_for covers
# everything else, so no auth request can hang on a stalled issuer.
_JWKS_FETCH_TIMEOUT_SECONDS = 5.0
_JWKS_RESOLVE_TIMEOUT_SECONDS = 6.0

# At most one key-set fetch per this window, whatever arrives. A key we already
# hold is used without asking the network at all, so this budget never delays a
# token whose key is known — it only caps what unknown kids and an unreachable
# issuer can cost. Without it, every token bearing a kid we do not have (a
# flood of them needs no credentials) is its own outbound fetch.
JWKS_REFRESH_COOLDOWN_SECONDS = 30.0

_jwks_client_cache: dict[str, Any] = {}
_jwks_keys: dict[str, Any] = {}
_jwks_last_refresh = 0.0
_jwks_lock = asyncio.Lock()


def supabase_jwks_url() -> Optional[str]:
    """Derive the project's well-known JWKS URL from ``SUPABASE_URL``.

    Returns ``None`` when ``SUPABASE_URL`` is empty, which lets callers skip
    the JWKS path cleanly instead of raising.
    """
    base = (SUPABASE_URL or "").rstrip("/")
    if not base:
        return None
    return f"{base}/auth/v1/.well-known/jwks.json"


def get_jwks_client(jwks_url: str) -> Any:
    """Return the process-wide ``jwt.PyJWKClient`` for ``jwks_url``."""
    cached = _jwks_client_cache.get(jwks_url)
    if cached is not None:
        return cached
    # Lazy import — keep ``jwt`` out of the module-import cycle so catalog
    # regeneration and other tooling that touches this file still works.
    import jwt as _jwt

    client = _jwt.PyJWKClient(jwks_url, cache_keys=True, timeout=_JWKS_FETCH_TIMEOUT_SECONDS)
    _jwks_client_cache[jwks_url] = client
    return client


async def _signing_key_for(kid: str, jwks_url: str) -> Any:
    """Return the published key for ``kid``, fetching the key set if needed.

    A key we already hold answers with NO network call — that is the steady
    state, and it is why a blip or a stalled issuer is not felt by anyone whose
    key is known. A kid we do not hold costs at most one fetch per
    ``JWKS_REFRESH_COOLDOWN_SECONDS``, for everyone, so unauthenticated traffic
    carrying invented kids cannot amplify into a fetch per request.

    Raises ``jwt.PyJWKClientError`` when the key cannot be produced, for any
    reason — the caller turns that into "could not check", never a verdict.
    """
    global _jwks_last_refresh

    import jwt as _jwt

    key = _jwks_keys.get(kid)
    if key is not None:
        return key

    async with _jwks_lock:
        # Another request may have refreshed while we waited for the lock.
        key = _jwks_keys.get(kid)
        if key is not None:
            return key

        now = time.monotonic()
        if _jwks_last_refresh and now - _jwks_last_refresh < JWKS_REFRESH_COOLDOWN_SECONDS:
            raise _jwt.PyJWKClientError(
                f'no published key for kid "{kid}" and the key-set refresh is '
                "within its cooldown"
            )
        _jwks_last_refresh = now

        client = get_jwks_client(jwks_url)
        signing_keys = await asyncio.wait_for(
            asyncio.to_thread(client.get_signing_keys, True),
            timeout=_JWKS_RESOLVE_TIMEOUT_SECONDS,
        )
        # Replace wholesale: a key the project has retired must stop verifying.
        _jwks_keys.clear()
        _jwks_keys.update({k.key_id: k for k in signing_keys if k.key_id})

        key = _jwks_keys.get(kid)
        if key is None:
            raise _jwt.PyJWKClientError(f'Unable to find a signing key that matches: "{kid}"')
        return key


def _decode_with_key(token: str, key: Any) -> dict[str, Any]:
    """Check ``token``'s signature and expiry against an already-resolved key.

    ``verify_aud=False``: Supabase stamps ``aud="authenticated"`` and this
    engine accepts any authenticated user of the project, so pinning the
    audience would add a failure mode without adding a check. ``verify_exp``
    stays default-on, so an expired token is rejected here rather than by a
    round trip. The algorithm allow-list is passed explicitly — PyJWT refuses
    any token whose header names something else, which is what closes
    algorithm-confusion.
    """
    import jwt as _jwt

    return _jwt.decode(
        token,
        key.key,
        algorithms=list(VERIFY_ALGORITHMS),
        options={"verify_aud": False},
    )


# The unconfigured check is purely local, so it can be reached on EVERY
# authenticated request. Announce it loudly once per process (the persistent
# action-needed card raised on session verification is the surface that keeps saying
# it), then stay at DEBUG so one broken build cannot bury the log file.
_unconfigured_logged: set[str] = set()


def missing_supabase_config() -> list[str]:
    """Name the account-service settings session verification is missing.

    Only ``SUPABASE_URL`` is required now: the verifying key is PUBLIC and is
    fetched from that URL's JWKS document. The publishable key is no longer
    part of verifying a session (it was only needed as the ``apikey`` header
    on the retired GoTrue round trip).
    """
    return [] if SUPABASE_URL else ["SUPABASE_URL"]


def _log_unconfigured_once() -> None:
    missing = missing_supabase_config()
    signature = ",".join(missing)
    message = (
        "[remote_auth] MISCONFIGURATION: this engine cannot verify any session "
        "because its account-service configuration is missing (%s). No user "
        "session is at fault and signing in again cannot help. Remedy: set %s "
        "for https://db.matrxserver.com and restart the engine."
    )
    if signature in _unconfigured_logged:
        logger.debug(message, signature, signature)
        return
    _unconfigured_logged.add(signature)
    logger.error(message, signature, signature)


# One line the first time an HS256 token shows up, not one per request. Capped:
# ``alg`` is attacker-controlled text on a PRE-AUTH path, so an uncapped set
# here is a remote memory leak and a remote log flood.
_MAX_LOGGED_ALGS = 8
_unverifiable_alg_logged: set[str] = set()


def _log_unverifiable_algorithm_once(alg: str) -> None:
    if alg in _unverifiable_alg_logged or len(_unverifiable_alg_logged) >= _MAX_LOGGED_ALGS:
        logger.debug("[remote_auth] token alg=%s is not locally verifiable", alg)
        return
    _unverifiable_alg_logged.add(alg)
    logger.warning(
        "[remote_auth] a session token signed with %s arrived; this engine can "
        "only verify %s (public keys from the project JWKS) and holds no "
        "symmetric signing secret by design. The session is treated as "
        "UNVERIFIABLE, not invalid — it is not erased. Remedy: the Supabase "
        "project must issue asymmetric tokens (it signs ES256 as of "
        "2026-09-20); a user holding an older token gets one by signing in "
        "again.",
        alg,
        "/".join(VERIFY_ALGORITHMS),
    )


def _positive_ttl_for(claims: dict[str, Any]) -> float:
    """Cache a verified token for at most as long as it is still valid.

    Expiry is checked at verification time, so a flat 60s cache would keep
    accepting a token for up to a minute after it expired.
    """
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return _POSITIVE_TTL_SECONDS
    return max(0.0, min(_POSITIVE_TTL_SECONDS, exp - time.time()))


async def verify_supabase_token_result(token: str) -> TokenVerificationResult:
    """Verify a Supabase access token locally against the project's JWKS.

    No auth-server round trip: the signature is checked against the project's
    published public key, which is exactly as trusted and costs no network on
    the steady path (the key set is cached in-process for an hour).

    ``invalid`` is a cryptographic verdict about the token and may be cached
    briefly. ``unavailable`` means we could not check it at all; callers that
    persist credentials must not erase a previously-good session merely
    because the key set was momentarily out of reach.
    """
    if not token:
        return TokenVerificationResult("invalid")

    jwks_url = supabase_jwks_url()
    if not jwks_url:
        _log_unconfigured_once()
        return TokenVerificationResult("unconfigured")

    key = _token_key(token)
    hit, cached = _cache_get(key)
    if hit:
        return TokenVerificationResult(
            "verified" if cached is not None else "invalid",
            cached,
        )

    import jwt as _jwt

    try:
        header = _jwt.get_unverified_header(token)
        alg = header.get("alg")
        kid = header.get("kid") or ""
    except Exception as exc:
        # Not a JWT at all. That is a verdict about the token itself, and it
        # needs no issuer to reach — cache it like any other rejection.
        logger.debug("[remote_auth] token header unreadable: %s", exc)
        _cache_put(key, None)
        return TokenVerificationResult("invalid")

    if alg == "HS256":
        # The one algorithm we genuinely cannot judge: verifying it needs the
        # project's symmetric secret, which this engine must not hold. We did
        # not check the token, so we say nothing about the session.
        _log_unverifiable_algorithm_once("HS256")
        return TokenVerificationResult("unavailable")

    if alg not in VERIFY_ALGORITHMS or not kid:
        # ``none``, HS384/512, an invented algorithm, or an asymmetric token
        # naming no key: we REFUSE these, which is a verdict, not an inability.
        # (Reported as unavailable, this was a real hole: on direct loopback
        # the /extension/* surface downgrades "could not check" to a
        # presence-only principal, so an RS512 token with a garbage signature
        # got in where it used to be rejected.)
        logger.debug("[remote_auth] refusing token alg=%s kid=%s", alg, kid or "<none>")
        _cache_put(key, None)
        return TokenVerificationResult("invalid")

    try:
        signing_key = await _signing_key_for(kid, jwks_url)
        claims = await asyncio.wait_for(
            asyncio.to_thread(_decode_with_key, token, signing_key),
            timeout=_JWKS_RESOLVE_TIMEOUT_SECONDS,
        )
    except (
        _jwt.PyJWKClientError,
        asyncio.TimeoutError,
        TimeoutError,
        OSError,
    ) as exc:
        # We could not produce the key — unreachable issuer, a kid the project
        # does not publish, or a refresh inside its cooldown. Nothing is known
        # about this session: never cached, never an ``invalid`` verdict
        # (proxy-identity rule 3).
        logger.warning(
            "[remote_auth] could not verify this session because the key for "
            "kid=%s was unavailable from %s (%s: %s). The session is NOT "
            "treated as signed out.",
            kid,
            jwks_url,
            type(exc).__name__,
            exc,
        )
        return TokenVerificationResult("unavailable")
    except _jwt.InvalidTokenError as exc:
        logger.warning(
            "[remote_auth] rejected this session: the token failed signature / "
            "expiry verification against the project key set (%s: %s)",
            type(exc).__name__,
            exc,
        )
        _cache_put(key, None)
        return TokenVerificationResult("invalid")
    except Exception as exc:
        # Anything unanticipated is an inability to check, not a verdict.
        logger.warning(
            "[remote_auth] unexpected failure verifying a session (%s: %s); "
            "treating it as unverifiable, not invalid",
            type(exc).__name__,
            exc,
        )
        return TokenVerificationResult("unavailable")

    uid = claims.get("sub")
    if not isinstance(uid, str) or not uid:
        # A signed token with no subject cannot identify anybody. The
        # signature was valid, so this is a real verdict about the token.
        logger.warning("[remote_auth] rejected this session: verified token carries no 'sub'")
        _cache_put(key, None)
        return TokenVerificationResult("invalid")

    email = claims.get("email")
    role = claims.get("role") or claims.get("aud")
    is_anon = role == "anon" or bool(claims.get("is_anonymous"))
    user = VerifiedUser(
        user_id=uid,
        email=email if isinstance(email, str) and email else None,
        is_anon=is_anon,
    )
    _cache_put(key, user, ttl=_positive_ttl_for(claims))
    return TokenVerificationResult("verified", user)


async def verify_supabase_token(token: str) -> Optional[VerifiedUser]:
    """Validate a Supabase access token locally; cache the result.

    Returns a :class:`VerifiedUser` when the signature and expiry check out,
    or ``None`` when the token is invalid/expired/unverifiable. Never raises —
    an unreadable key set is treated as "not verified" (fail closed) so it
    cannot turn into an auth bypass. Callers that need to tell "bad token"
    apart from "could not check" — anything that erases a stored session —
    must use :func:`verify_supabase_token_result` instead.
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
