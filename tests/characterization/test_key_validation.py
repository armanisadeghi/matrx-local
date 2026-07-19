"""Pins the API-key validation contract.

Two of these tests exist because the bug they guard against already happened
once, or would be invisible until a user hit it in production:

  * the CivitAI trap — /api/v1/models returns 200 for a garbage token, so a
    validator built on it reports every key (including an empty one) as valid;
  * the provider-registry drift — a provider in PROVIDER_ENV_MAP but not in
    VALID_PROVIDERS is unsettable through the API and can only come from .env,
    which is how elevenlabs and fastino were stranded.

No test here touches the network: every provider call is stubbed. Validation is
free in production, but a test suite that depends on ten third-party APIs being
up is not a test suite.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.ai import key_validation
from app.services.ai.key_manager import PROVIDER_ENV_MAP
from app.services.ai.key_validation import PROVIDER_SPECS, validate_key
from app.services.ai.provider_grants import VALID_PROVIDERS


def _stub_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route every httpx call the validator makes through `handler`."""
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(key_validation.httpx, "AsyncClient", _factory)


def test_registry_has_no_drift() -> None:
    """Every provider we can hold a key for must be settable through the API."""
    assert set(VALID_PROVIDERS) == set(PROVIDER_ENV_MAP), (
        "VALID_PROVIDERS and PROVIDER_ENV_MAP disagree — a provider in one but "
        "not the other is either unsettable via the API or unreadable at runtime."
    )


def test_every_testable_provider_is_a_valid_provider() -> None:
    assert set(PROVIDER_SPECS) <= set(VALID_PROVIDERS)


def test_civitai_does_not_validate_against_the_public_models_endpoint() -> None:
    """The trap: /api/v1/models 200s for any token, so it must not be the spec."""
    url = PROVIDER_SPECS["civitai"].url
    assert "/api/v1/me" in url
    assert "/models" not in url


def test_success_is_valid_and_surfaces_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"name": "armani"}),
    )
    result = asyncio.run(validate_key("huggingface", "hf_real"))
    assert result.verdict == "valid"
    assert result.account == "armani"
    assert "armani" in result.message


@pytest.mark.parametrize(
    ("provider", "status"),
    [
        ("openai", 401),
        ("huggingface", 401),
        ("civitai", 401),
        # Google and xAI reject an invalid key with 400, not 401. A naive
        # "401 means bad" validator would call these keys UNKNOWN and leave the
        # user staring at a shrug.
        ("google", 400),
        ("xai", 400),
    ],
)
def test_provider_rejection_is_invalid(
    monkeypatch: pytest.MonkeyPatch, provider: str, status: int
) -> None:
    _stub_transport(monkeypatch, lambda request: httpx.Response(status, json={}))
    result = asyncio.run(validate_key(provider, "bogus"))
    assert result.verdict == "invalid"


def test_rate_limited_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 means auth passed — you cannot rate-limit an unauthenticated key."""
    _stub_transport(monkeypatch, lambda request: httpx.Response(429, json={}))
    result = asyncio.run(validate_key("openai", "real-but-busy"))
    assert result.verdict == "valid"


@pytest.mark.parametrize("status", [500, 502, 503])
def test_provider_outage_is_unknown_never_invalid(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """A provider outage must never accuse a good key of being bad."""
    _stub_transport(monkeypatch, lambda request: httpx.Response(status, text="down"))
    result = asyncio.run(validate_key("openai", "good-key"))
    assert result.verdict == "unknown"


def test_network_failure_is_unknown_never_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns is having a day", request=request)

    _stub_transport(monkeypatch, _boom)
    result = asyncio.run(validate_key("openai", "good-key"))
    assert result.verdict == "unknown"
    assert "doesn't mean the key is bad" in result.message


def test_empty_key_is_invalid_without_a_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("an empty key must not reach the provider")

    _stub_transport(monkeypatch, _never)
    assert asyncio.run(validate_key("openai", "   ")).verdict == "invalid"


def test_provider_without_a_spec_is_unsupported() -> None:
    """fastino has no free auth-only endpoint — say so, don't fail the key."""
    result = asyncio.run(validate_key("fastino", "some-key"))
    assert result.verdict == "unsupported"
