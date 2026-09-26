"""LocalScrape surfaces the Source a saved page landed as (SOURCE-CONVERGENCE §4.4).

No network: the engine and the server client are faked. Proves the tool result
carries `processed_document_id` from `/content/save`, sends the server-required
`page_name`, and never claims a save the server refused.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.tools.tools import scraper as tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeEngine:
    is_ready = True

    async def scrape(self, urls: list[str], _opts: Any) -> list[Any]:
        return [SimpleNamespace(url=u, success=True, failure_reason=None) for u in urls]


class _FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def save_content(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("server said no")
        return {
            "status": "saved",
            "processed_document_id": "6b8c38dd-6d68-4824-b664-a380b7611627",
            "source_id": "spp-1",
            "notices": [{"code": "intelligence_deferred", "message": "Saved, not processed yet.", "remedy": ""}],
        }


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    import app.services.scraper.engine as engine_mod
    import app.services.scraper.remote_client as remote_mod
    import app.services.scraper.scrape_store as store_mod

    client = _FakeClient()
    monkeypatch.setattr(engine_mod, "get_scraper_engine", lambda: _FakeEngine())
    monkeypatch.setattr(remote_mod, "get_remote_scraper", lambda: client)
    monkeypatch.setattr(store_mod, "content_from_result", lambda _p: {"text_data": "hello world"})
    return client


async def test_result_carries_the_source_id(patched: _FakeClient) -> None:
    out = await tool.local_scrape(["https://example.com/a"], auth_token="jwt")
    [row] = out["results"]
    assert row["processed_document_id"] == "6b8c38dd-6d68-4824-b664-a380b7611627"
    assert row["saved_to_server"] is True
    assert row["source_notices"][0]["code"] == "intelligence_deferred"
    assert out["saved"] == 1
    assert patched.calls[0]["page_name"]  # the server's ContentSaveRequest requires it


async def test_a_refused_save_is_never_reported_as_saved(patched: _FakeClient) -> None:
    patched.fail = True
    out = await tool.local_scrape(["https://example.com/b"], auth_token="jwt")
    [row] = out["results"]
    assert row["saved_to_server"] is False
    assert row["processed_document_id"] is None
    assert "server said no" in row["save_error"]
    assert out["saved"] == 0
