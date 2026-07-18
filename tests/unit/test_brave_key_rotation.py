"""Saved Brave grants take effect immediately and failures stay actionable."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.ai import key_manager
from app.services.scraper import engine as scraper_module
from app.services.scraper.engine import ScraperEngine
from app.tools.session import ToolSession
from app.tools.tools import network


def test_search_client_rotates_and_disables_without_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys = {"brave": "first"}
    created: list[str] = []

    class FakeClient:
        def __init__(self, settings: object) -> None:
            created.append(str(getattr(settings, "BRAVE_API_KEY")))

    monkeypatch.setattr(key_manager, "get_cached_user_keys", lambda: keys)
    monkeypatch.setattr(
        scraper_module,
        "_import_scraper",
        lambda _name: SimpleNamespace(BraveSearchClient=FakeClient),
    )

    engine = ScraperEngine()
    engine._settings = SimpleNamespace(BRAVE_API_KEY="")
    engine._orchestrator = SimpleNamespace(search_client=None)
    first = engine.ensure_search_client()
    assert first is engine._orchestrator.search_client

    keys["brave"] = "second"
    second = engine.ensure_search_client()
    assert second is not first
    assert created == ["first", "second"]

    keys["brave"] = ""
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert engine.ensure_search_client() is None
    assert engine._orchestrator.search_client is None


def test_brave_auth_rejection_returns_update_key_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectedClient:
        async def search_with_retry(self, **_kwargs: object) -> object:
            raise RuntimeError("HTTP 401 Unauthorized")

    fake_engine = SimpleNamespace(
        is_ready=True,
        ensure_search_client=lambda: True,
        search_client=RejectedClient(),
    )
    monkeypatch.setattr(network, "_get_engine", lambda: fake_engine)

    result = asyncio.run(network.tool_search(ToolSession(), ["test"]))
    assert result.action_needed is not None
    assert result.action_needed.code == "api_key_invalid"
    assert result.action_needed.action.provider == "brave"

