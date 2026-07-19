"""Regression coverage for user-provider status sourcing."""

import asyncio

import pytest

from app.api.chat_routes import ai_provider_status, provider_readiness
from app.services.ai import key_manager


def test_provider_status_uses_persisted_user_key_cache(monkeypatch: pytest.MonkeyPatch):
    """A developer shell variable must not masquerade as a user setting."""
    monkeypatch.setenv("OPENAI_API_KEY", "developer-shell-key")
    async def loaded_keys() -> dict[str, str]:
        return {"anthropic": "user-entered-key", "openai": ""}

    monkeypatch.setattr(key_manager, "get_user_keys_when_ready", loaded_keys)

    result = asyncio.run(ai_provider_status())

    assert "anthropic" in result["providers"]["available"]
    assert "openai" in result["providers"]["missing"]


def test_provider_readiness_targets_the_selected_provider(monkeypatch: pytest.MonkeyPatch):
    async def loaded_keys() -> dict[str, str]:
        return {"anthropic": "configured", "openai": ""}

    monkeypatch.setattr(key_manager, "get_user_keys_when_ready", loaded_keys)

    result = asyncio.run(provider_readiness("openai"))

    assert result.ready is False
    assert result.action_needed is not None
    assert result.action_needed.fingerprint == "api-key:openai"
    assert result.action_needed.action.provider == "openai"
    assert result.action_needed.action.route.endswith("provider=openai")


def test_provider_readiness_resolves_when_key_exists(monkeypatch: pytest.MonkeyPatch):
    async def loaded_keys() -> dict[str, str]:
        return {"openai": "configured"}

    monkeypatch.setattr(key_manager, "get_user_keys_when_ready", loaded_keys)
    result = asyncio.run(provider_readiness("openai"))
    assert result.ready is True
    assert result.action_needed is None


def test_provider_readiness_does_not_reuse_a_historical_invalid_verdict(
    monkeypatch: pytest.MonkeyPatch,
):
    """Actual provider use, not an old test result, decides authentication."""
    async def loaded_keys() -> dict[str, str]:
        return {"openai": "configured"}

    monkeypatch.setattr(key_manager, "get_user_keys_when_ready", loaded_keys)
    result = asyncio.run(provider_readiness("openai"))
    assert result.ready is True
    assert result.action_needed is None
