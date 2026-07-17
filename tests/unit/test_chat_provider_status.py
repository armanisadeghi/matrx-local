"""Regression coverage for user-provider status sourcing."""

import asyncio

import pytest

import app.common  # noqa: F401 — initialize before app.config import paths.
from app.api.chat_routes import ai_provider_status
from app.services.ai import key_manager


def test_provider_status_uses_persisted_user_key_cache(monkeypatch: pytest.MonkeyPatch):
    """A developer shell variable must not masquerade as a user setting."""
    monkeypatch.setenv("OPENAI_API_KEY", "developer-shell-key")
    monkeypatch.setattr(
        key_manager,
        "get_cached_user_keys",
        lambda: {"anthropic": "user-entered-key", "openai": ""},
    )

    result = asyncio.run(ai_provider_status())

    assert "anthropic" in result["providers"]["available"]
    assert "openai" in result["providers"]["missing"]
