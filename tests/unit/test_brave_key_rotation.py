"""Saved Brave grants take effect immediately and failures stay actionable.

The Brave key is the USER's, held in the in-app key store — never an env var
(§ Security & configuration posture). So saving one in Settings must work on
the very next search with no engine restart, and clearing one must actually
disable search rather than keep answering from a stale client.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.ai import key_manager
from app.services.scraper.engine import ScraperEngine
from app.tools.session import ToolSession
from app.tools.tools import network


def test_search_client_rotates_and_disables_without_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys = {"brave": "first"}
    configured: list[str | None] = []

    monkeypatch.setattr(key_manager, "get_cached_user_keys", lambda: keys)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    # The package owns the process-wide client; the desktop just tells it which
    # key to use. Patch at that seam so no network client is ever built.
    import matrx_scraper.search as scraper_search

    monkeypatch.setattr(
        scraper_search, "configure_client", lambda key: configured.append(key)
    )

    engine = ScraperEngine()

    assert engine.ensure_search_client() is True
    assert configured == ["first"]

    # Same key again must NOT rebuild the client (it would drop the rate limiter).
    assert engine.ensure_search_client() is True
    assert configured == ["first"]

    keys["brave"] = "second"
    assert engine.ensure_search_client() is True
    assert configured == ["first", "second"]

    # Key removed → search reports unavailable AND the package client is cleared.
    keys["brave"] = ""
    assert engine.ensure_search_client() is False
    assert configured == ["first", "second", None]


def test_brave_auth_rejection_returns_update_key_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 from Brave is the user's key being wrong — prompt, don't just fail."""

    async def _reject(**_kwargs: object) -> object:
        raise RuntimeError("HTTP 401 Unauthorized")

    monkeypatch.setattr(network, "_get_engine", lambda: SimpleNamespace(
        is_ready=True, ensure_search_client=lambda: True,
    ))
    monkeypatch.setattr("matrx_scraper.search.async_brave_search", _reject)

    result = asyncio.run(network.tool_search(ToolSession(), ["test"]))
    assert result.action_needed is not None
    assert result.action_needed.code == "api_key_invalid"
    assert result.action_needed.action.provider == "brave"


def test_missing_key_is_a_prompt_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key at all is a STATE with a prompt UI, never a bare failure."""
    monkeypatch.setattr(network, "_get_engine", lambda: SimpleNamespace(
        is_ready=True, ensure_search_client=lambda: False,
    ))

    result = asyncio.run(network.tool_search(ToolSession(), ["test"]))
    assert result.action_needed is not None
    assert result.action_needed.action.provider == "brave"
