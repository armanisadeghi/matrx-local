"""Provider API-key validation — the single canonical "is this key real?" path.

Every provider we accept a key for exposes a **free, auth-only** endpoint: a
whoami or a model-list that authenticates the caller but runs no inference and
bills nothing. This module registers one spec per provider and turns the call
into a tri-state verdict.

Why tri-state and not a bool
----------------------------
A bool forces every non-2xx into "invalid", and that lies to the user in the
two cases that matter most: a provider outage and a dead network both mean we
simply *do not know*. Telling someone their key is bad when it isn't sends them
to rotate a perfectly good credential — the exact dead end we are removing.

  valid       the provider authenticated us (2xx, or 429: auth passed, then the
              rate limiter kicked in — you cannot rate-limit an unauthenticated
              caller by key)
  invalid     the provider explicitly rejected the credential
  unknown     5xx, timeout, DNS/connection failure — our problem, not the key's
  unsupported no free auth-only endpoint exists (fastino)

Status codes are NOT uniform across providers, so "401 means bad" is wrong:
Google and xAI answer an invalid key with **400**. Each spec therefore names the
statuses that mean "rejected" for that provider, and anything unrecognised falls
through to `unknown` rather than being guessed at.

The Civitai trap
----------------
`GET https://civitai.com/api/v1/models` returns **200 with a garbage token** — it
is a public endpoint that ignores auth entirely. Validating against it would
mark every key, including an empty one, as valid. `/api/v1/me` is the only
Civitai endpoint that actually rejects a bad token; that is why the spec below
uses it. Do not "simplify" this to the models endpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.common.system_logger import get_logger

logger = get_logger()

Verdict = Literal["valid", "invalid", "unknown", "unsupported"]

# One network call, generously bounded: these are cold-path health checks a human
# is watching a spinner for, not a hot request path.
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

# Auth passed, then the rate limiter fired. A provider cannot rate-limit an
# unauthenticated caller *by key*, so 429 is positive evidence the key is real.
_RATE_LIMITED = 429


@dataclass(frozen=True)
class ProviderSpec:
    """How to ask one provider "is this key real?" for free."""

    url: str
    # Key → request headers. Most providers are Bearer; Anthropic and ElevenLabs
    # use their own header names.
    headers: Callable[[str], dict[str, str]] = field(
        default=lambda key: {"Authorization": f"Bearer {key}"}
    )
    # Key → query params (Google authenticates via ?key=, not a header).
    params: Callable[[str], dict[str, str]] = field(default=lambda key: {})
    # Statuses that mean "the provider rejected this credential". Explicit per
    # provider because they disagree: Google and xAI say 400, everyone else 401.
    rejects: tuple[int, ...] = (401, 403)
    # Response body → the account this key belongs to, when the provider tells
    # us. Surfacing it catches "valid key, wrong account", which is invisible
    # from a bare thumbs-up.
    account: Callable[[Any], str | None] = field(default=lambda body: None)


def _account_name(body: Any) -> str | None:
    """Pull a human-recognisable account name out of a whoami payload.

    Providers disagree on the field name, so try the usual suspects in order of
    how recognisable they are to the person reading the result.
    """
    if not isinstance(body, dict):
        return None
    for key in ("name", "username", "first_name", "email"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# The registry. Adding a provider = add a key here + to VALID_PROVIDERS.
# Every endpoint below is free: auth-only, no inference, no credits consumed.
PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(url="https://api.openai.com/v1/models"),
    "anthropic": ProviderSpec(
        url="https://api.anthropic.com/v1/models",
        headers=lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
        params=lambda key: {"limit": "1"},
    ),
    "google": ProviderSpec(
        url="https://generativelanguage.googleapis.com/v1beta/models",
        headers=lambda key: {},
        params=lambda key: {"key": key},
        # Google reports an invalid key as 400 API_KEY_INVALID, not 401.
        rejects=(400, 401, 403),
    ),
    "groq": ProviderSpec(url="https://api.groq.com/openai/v1/models"),
    "together": ProviderSpec(url="https://api.together.xyz/v1/models"),
    "cerebras": ProviderSpec(url="https://api.cerebras.ai/v1/models"),
    "xai": ProviderSpec(
        url="https://api.x.ai/v1/api-key",
        # xAI answers a malformed/invalid key with 400 invalid-argument.
        rejects=(400, 401, 403),
    ),
    "huggingface": ProviderSpec(
        url="https://huggingface.co/api/whoami-v2",
        account=_account_name,
    ),
    "civitai": ProviderSpec(
        # NOT /api/v1/models — that endpoint is public and returns 200 for a
        # garbage token. See module docstring.
        url="https://civitai.com/api/v1/me",
        account=_account_name,
    ),
    "elevenlabs": ProviderSpec(
        url="https://api.elevenlabs.io/v1/user",
        headers=lambda key: {"xi-api-key": key},
        account=_account_name,
    ),
    "brave": ProviderSpec(
        url="https://api.search.brave.com/res/v1/web/search",
        headers=lambda key: {"X-Subscription-Token": key},
        params=lambda key: {"q": "Matrx credential check", "count": "1"},
    ),
    # fastino: no public auth-only endpoint. Deliberately absent → "unsupported",
    # which the UI renders as "can't be tested" rather than a scary red X.
}


@dataclass(frozen=True)
class ValidationResult:
    provider: str
    verdict: Verdict
    # One sentence, written for the user, not for a log.
    message: str
    # The account the key belongs to, when the provider discloses it.
    account: str | None = None
    status_code: int | None = None


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is not an error here
        return None


async def validate_key(provider: str, key: str) -> ValidationResult:
    """Ask a provider whether this key is real. Free — never bills the user.

    Never raises: every failure mode is folded into a verdict, because the
    caller is a UI button and a thrown exception there is just a worse "unknown".
    """
    provider = provider.strip().lower()
    key = (key or "").strip()

    if not key:
        return ValidationResult(
            provider=provider, verdict="invalid", message="No key provided."
        )

    spec = PROVIDER_SPECS.get(provider)
    if spec is None:
        return ValidationResult(
            provider=provider,
            verdict="unsupported",
            message="This provider has no free endpoint to test a key against.",
        )

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                spec.url, headers=spec.headers(key), params=spec.params(key)
            )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.warning(
            "[key_validation] %s unreachable — verdict 'unknown', NOT 'invalid' (%s)",
            provider,
            exc,
        )
        return ValidationResult(
            provider=provider,
            verdict="unknown",
            message="Couldn't reach the provider. Check your connection and try again — this doesn't mean the key is bad.",
        )

    status = resp.status_code

    if resp.is_success:
        account = spec.account(_body(resp))
        return ValidationResult(
            provider=provider,
            verdict="valid",
            message=f"Key is valid — signed in as {account}." if account else "Key is valid.",
            account=account,
            status_code=status,
        )

    if status == _RATE_LIMITED:
        return ValidationResult(
            provider=provider,
            verdict="valid",
            message="Key is valid (the provider is rate-limiting right now, but it accepted the key).",
            status_code=status,
        )

    if status in spec.rejects:
        return ValidationResult(
            provider=provider,
            verdict="invalid",
            message="The provider rejected this key. Check it was copied in full and hasn't been revoked.",
            status_code=status,
        )

    logger.warning(
        "[key_validation] %s returned an unexpected %s — verdict 'unknown'",
        provider,
        status,
    )
    return ValidationResult(
        provider=provider,
        verdict="unknown",
        message=f"The provider returned an unexpected response ({status}). Couldn't confirm the key either way.",
        status_code=status,
    )


async def validate_stored_key(provider: str) -> ValidationResult:
    """Validate the key currently saved for a provider."""
    from app.services.ai.key_manager import get_cached_user_keys

    key = get_cached_user_keys().get(provider)
    if not key:
        # Fall back to SQLite: the cache is warmed at startup, but a key set by
        # another process (or before this one booted) still lives on disk.
        from app.services.local_db.repositories import ApiKeysRepo

        key = await ApiKeysRepo().get(provider) or ""

    if not key.strip():
        return ValidationResult(
            provider=provider,
            verdict="invalid",
            message="No key is saved for this provider.",
        )
    return await validate_key(provider, key)


async def validate_many(keys: dict[str, str]) -> list[ValidationResult]:
    """Validate several providers concurrently. Used by 'Test all'."""
    providers = list(keys)
    results = await asyncio.gather(
        *(validate_key(p, keys[p]) for p in providers), return_exceptions=True
    )
    out: list[ValidationResult] = []
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("[key_validation] %s raised: %s", provider, result)
            out.append(
                ValidationResult(
                    provider=provider,
                    verdict="unknown",
                    message="Couldn't complete the check.",
                )
            )
        else:
            out.append(result)
    return out
