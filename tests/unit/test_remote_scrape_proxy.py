"""The /remote-scraper/scrape proxy speaks the same contract as the local lane.

The scraper server streams NDJSON envelopes; the browser expects SSE frames of
the client contract. These tests pin the translation — the batch route and the
stream route must hand back exactly what the local `Scrape` tool does.
"""

from __future__ import annotations

import json

import pytest

from app.api import remote_scraper_routes as routes
from app.services.scraper.result_contract import CLIENT_FIELDS

SERVER_PAGE = {
    "url": "https://example.com",
    "response_url": "https://example.com/",
    "success": True,
    "status_code": 200,
    "content_type": "html",
    "title": "Example Domain",
    "text_data": "# Example Domain",
    # Envelope artefacts the server's FetchResultItem always adds.
    "content": "",
    "status": "",
}


class _FakeClient:
    """Stands in for RemoteScraperClient with a canned server response."""

    def __init__(self, lines: list[str] | None = None, batch: dict | None = None):
        self._lines = lines or []
        self._batch = batch or {}

    async def scrape(self, urls, options, auth_token=None):
        return self._batch

    async def stream_sse(self, path, payload, auth_token=None, timeout=300.0):
        for line in self._lines:
            yield (line + "\n").encode()


def _install(monkeypatch, client):
    monkeypatch.setattr(routes, "_get_client_or_raise", lambda: client)


def _parse_sse(chunks: list[bytes]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in chunks:
        text = chunk.decode()
        event = next(l[len("event: "):] for l in text.splitlines() if l.startswith("event: "))
        data = next(l[len("data: "):] for l in text.splitlines() if l.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


async def _collect(agen) -> list[bytes]:
    return [chunk async for chunk in agen]


@pytest.mark.anyio
async def test_batch_response_is_the_client_contract(monkeypatch):
    _install(monkeypatch, _FakeClient(batch={
        "status": "success",
        "execution_time_ms": 812.4,
        "results": [SERVER_PAGE],
    }))

    class _Req:
        state = type("S", (), {"user_token": None})()

    resp = await routes.remote_scrape(routes.ScrapeRequest(urls=[SERVER_PAGE["url"]]), _Req())

    assert resp["total"] == 1
    assert resp["success_count"] == 1
    assert resp["elapsed_ms"] == 812
    page = resp["results"][0]
    assert tuple(page) == CLIENT_FIELDS
    assert page["success"] is True
    assert page["text_data"] == "# Example Domain"
    assert "status" not in page and "error" not in page


@pytest.mark.anyio
async def test_ndjson_pages_become_canonical_sse_events(monkeypatch):
    envelope = json.dumps({
        "event": "data",
        "data": {
            "type": "fetch_results",
            "metadata": {"execution_time_ms": 640.2},
            "results": [SERVER_PAGE],
        },
    })
    # Envelopes the stream also carries and this route must ignore.
    noise = [
        json.dumps({"event": "phase", "data": {"phase": "connected"}}),
        json.dumps({"event": "info", "data": {"code": "scrape_start"}}),
        "",
    ]
    _install(monkeypatch, _FakeClient(lines=[*noise, envelope]))

    events = _parse_sse(await _collect(routes._scrape_sse([SERVER_PAGE["url"]], None, None)))

    assert [e for e, _ in events] == ["page_result", "done"]
    page = events[0][1]
    assert tuple(page) == CLIENT_FIELDS
    assert page["success"] is True
    assert page["elapsed_ms"] == 640


@pytest.mark.anyio
async def test_stream_failure_emits_an_error_event_then_done(monkeypatch):
    class _Boom(_FakeClient):
        async def stream_sse(self, path, payload, auth_token=None, timeout=300.0):
            raise RuntimeError("server exploded")
            yield b""  # pragma: no cover - makes this an async generator

    _install(monkeypatch, _Boom())

    events = _parse_sse(await _collect(routes._scrape_sse(["https://example.com"], None, None)))

    assert [e for e, _ in events] == ["error", "done"]
    assert "server exploded" in events[0][1]["failure_reason"]


@pytest.mark.anyio
async def test_unparseable_line_is_skipped_not_fatal(monkeypatch):
    _install(monkeypatch, _FakeClient(lines=["<html>gateway error</html>"]))

    events = _parse_sse(await _collect(routes._scrape_sse(["https://example.com"], None, None)))

    assert [e for e, _ in events] == ["done"]
