"""The /remote-scraper/scrape proxy speaks the same contract as the local lane.

The scraper server streams NDJSON envelopes; the browser expects SSE frames of
the client contract. These tests pin the translation — the batch route and the
stream route must hand back exactly what the local `Scrape` tool does.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.api import remote_scraper_routes as routes
from app.services.scraper import remote_client as remote_client_module
from app.services.scraper.remote_client import (
    RemoteScraperClient,
    quick_scrape_payload,
    search_and_scrape_payload,
)
from app.services.scraper.result_contract import CLIENT_FIELDS
from matrx_connect.context.data_types import (
    FetchResultsData,
    SearchErrorData,
    SearchResultsData,
)
from matrx_connect.context.events import StreamEvent
from matrx_scraper.api.scrape_router import (
    QuickScrapeRequest as UpstreamQuickScrapeRequest,
)
from matrx_scraper.api.scrape_router import (
    SearchAndScrapeRequest as UpstreamSearchAndScrapeRequest,
)

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
        event = next(
            line[len("event: ") :]
            for line in text.splitlines()
            if line.startswith("event: ")
        )
        data = next(
            line[len("data: ") :]
            for line in text.splitlines()
            if line.startswith("data: ")
        )
        events.append((event, json.loads(data)))
    return events


async def _collect(agen) -> list[bytes]:
    return [chunk async for chunk in agen]


@pytest.mark.anyio
async def test_batch_response_is_the_client_contract(monkeypatch):
    _install(
        monkeypatch,
        _FakeClient(
            batch={
                "status": "success",
                "execution_time_ms": 812.4,
                "results": [SERVER_PAGE],
            }
        ),
    )

    class _Req:
        state = type("S", (), {"user_token": None})()

    resp = await routes.remote_scrape(
        routes.ScrapeRequest(urls=[SERVER_PAGE["url"]]), _Req()
    )

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
    envelope = json.dumps(
        {
            "event": "data",
            "data": {
                "type": "fetch_results",
                "metadata": {"execution_time_ms": 640.2},
                "results": [SERVER_PAGE],
            },
        }
    )
    # Package lifecycle/progress envelopes remain visible after conversion.
    progress = [
        json.dumps({"event": "phase", "data": {"phase": "connected"}}),
        json.dumps({"event": "info", "data": {"code": "scrape_start"}}),
        "",
    ]
    _install(
        monkeypatch,
        _FakeClient(
            lines=[*progress, envelope, json.dumps({"event": "end", "data": {}})]
        ),
    )

    events = _parse_sse(
        await _collect(routes._scrape_sse([SERVER_PAGE["url"]], None, None))
    )

    assert [e for e, _ in events] == ["phase", "info", "page_result", "done"]
    page = events[2][1]
    assert tuple(page) == CLIENT_FIELDS
    assert page["success"] is True
    assert page["elapsed_ms"] == 640


def test_flattened_payloads_validate_against_the_upstream_request_models():
    quick = quick_scrape_payload(
        [SERVER_PAGE["url"]], {"get_links": True, "use_cache": False, "ignored": "no"}
    )
    quick_model = UpstreamQuickScrapeRequest.model_validate(quick)
    assert quick_model.urls == [SERVER_PAGE["url"]]
    assert quick_model.get_links is True
    assert quick_model.use_cache is False
    assert quick_model.stream is True
    assert "ignored" not in quick

    search = search_and_scrape_payload(
        ["example"],
        12,
        {"get_overview": True, "keywords": ["wrong"], "ignored": "no"},
        country_code="CA",
    )
    search_model = UpstreamSearchAndScrapeRequest.model_validate(search)
    assert search_model.keywords == ["example"]
    assert search_model.total_results_per_keyword == 12
    assert search_model.country_code == "CA"
    assert search_model.get_overview is True
    assert "ignored" not in search


@pytest.mark.anyio
async def test_stream_failure_emits_an_error_event_then_done(monkeypatch):
    class _Boom(_FakeClient):
        async def stream_sse(self, path, payload, auth_token=None, timeout=300.0):
            raise RuntimeError("server exploded")
            yield b""  # pragma: no cover - makes this an async generator

    _install(monkeypatch, _Boom())

    events = _parse_sse(
        await _collect(routes._scrape_sse(["https://example.com"], None, None))
    )

    assert [e for e, _ in events] == ["error", "done"]
    assert "server exploded" in events[0][1]["failure_reason"]


@pytest.mark.anyio
async def test_unparseable_line_emits_an_honest_error_then_done(monkeypatch):
    _install(monkeypatch, _FakeClient(lines=["<html>gateway error</html>"]))

    events = _parse_sse(
        await _collect(routes._scrape_sse(["https://example.com"], None, None))
    )

    assert [e for e, _ in events] == ["error", "done"]
    assert events[0][1] == {
        "failure_reason": "Remote scraper sent malformed stream data."
    }


@pytest.mark.anyio
async def test_missing_end_emits_an_honest_error_then_done(monkeypatch):
    _install(
        monkeypatch, _FakeClient(lines=[json.dumps({"event": "info", "data": {}})])
    )

    events = _parse_sse(
        await _collect(routes._scrape_sse(["https://example.com"], None, None))
    )

    assert [event for event, _ in events] == ["info", "error", "done"]
    assert events[1][1] == {
        "failure_reason": "Remote scraper stream ended before completion."
    }


@pytest.mark.anyio
async def test_keyword_search_error_does_not_discard_later_page_results(monkeypatch):
    page_envelope = json.dumps(
        {
            "event": "data",
            "data": {
                "type": "fetch_results",
                "metadata": {"execution_time_ms": 640.2},
                "results": [SERVER_PAGE],
            },
        }
    )
    _install(
        monkeypatch,
        _FakeClient(
            lines=[
                json.dumps(
                    {
                        "event": "data",
                        "data": {
                            "type": "search_error",
                            "metadata": {"keyword": "failed"},
                            "error": "Brave unavailable",
                        },
                    }
                ),
                page_envelope,
                json.dumps({"event": "end", "data": {}}),
            ]
        ),
    )

    events = _parse_sse(
        await _collect(routes._scrape_sse([SERVER_PAGE["url"]], None, None))
    )

    assert [event for event, _ in events] == ["error", "page_result", "done"]
    assert events[0][1]["failure_reason"] == "Brave unavailable"
    assert events[1][1]["url"] == SERVER_PAGE["url"]


def _data_line(payload) -> str:
    return StreamEvent(event="data", data=payload.model_dump()).to_jsonl()


@pytest.mark.anyio
async def test_collector_reads_actual_ndjson_and_retains_partial_results(monkeypatch):
    search_payload = SearchResultsData(
        metadata={"keyword": "example"},
        results=[{"url": SERVER_PAGE["url"], "title": "Example"}],
    )
    lines = "\n".join(
        [
            "",
            _data_line(search_payload),
            _data_line(
                FetchResultsData(
                    metadata={"execution_time_ms": 1}, results=[SERVER_PAGE]
                )
            ),
            _data_line(
                SearchErrorData(
                    metadata={"keyword": "other"}, error="Brave unavailable"
                )
            ),
            StreamEvent(event="end", data={}).to_jsonl(),
        ]
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=lines)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        remote_client_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_async_client(*args, transport=transport, **kwargs),
    )
    client = RemoteScraperClient(server_url="https://scraper.test")

    collected = await client.collect_stream(
        "/api/scraper/search-and-scrape",
        search_and_scrape_payload(["example"], 10, {"get_links": True}),
    )

    assert collected == {
        "results": [SERVER_PAGE],
        "search_results": [
            {
                "metadata": {"keyword": "example"},
                "results": search_payload.model_dump()["results"],
            }
        ],
        "errors": [
            {
                "metadata": {"keyword": "other"},
                "failure_reason": "Brave unavailable",
            }
        ],
    }
    assert seen[0].url.path == "/api/scraper/search-and-scrape"
    UpstreamSearchAndScrapeRequest.model_validate(json.loads(seen[0].content))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "line",
    [
        StreamEvent(
            event="error",
            data={"message": "upstream stopped", "user_message": "Try again."},
        ).to_jsonl(),
        "<html>gateway error</html>\n",
        _data_line(SearchResultsData(metadata={"keyword": "example"}, results=[])),
    ],
)
async def test_collector_rejects_fatal_malformed_or_truncated_ndjson(monkeypatch, line):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=line))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        remote_client_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_async_client(*args, transport=transport, **kwargs),
    )
    client = RemoteScraperClient(server_url="https://scraper.test")

    with pytest.raises(ValueError):
        await client.collect_stream("/api/scraper/search", {"keywords": ["example"]})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "stream_request", "expected_path", "expected_payload"),
    [
        (
            routes.remote_search_and_scrape_stream,
            routes.SearchAndScrapeRequest(
                keywords=["example"],
                total_results_per_keyword=12,
                options={"get_links": True, "ignored": "no"},
            ),
            "/api/scraper/search-and-scrape",
            {
                "keywords": ["example"],
                "country_code": "all",
                "total_results_per_keyword": 12,
                "get_links": True,
            },
        ),
        (
            routes.remote_research_stream,
            routes.ResearchRequest(query="example", effort="high", country="CA"),
            "/api/scraper/search-and-scrape",
            {
                "keywords": ["example"],
                "country_code": "CA",
                "total_results_per_keyword": 10,
            },
        ),
    ],
)
async def test_search_and_research_ndjson_become_sse_and_keep_progress(
    monkeypatch,
    endpoint,
    stream_request,
    expected_path,
    expected_payload,
):
    """The package emits these envelopes from search_and_scrape()."""

    class _RecordingClient(_FakeClient):
        def __init__(self):
            super().__init__(
                lines=[
                    json.dumps({"event": "info", "data": {"code": "search_progress"}}),
                    json.dumps(
                        {
                            "event": "data",
                            "data": {
                                "response_type": "search_results",
                                "metadata": {"keyword": "example"},
                                "results": [{"url": SERVER_PAGE["url"]}],
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "event": "data",
                            "data": {
                                "response_type": "fetch_results",
                                "metadata": {"execution_time_ms": 321.8},
                                "results": [SERVER_PAGE],
                            },
                        }
                    ),
                    json.dumps({"event": "end", "data": {}}),
                ]
            )
            self.calls = []

        async def stream_sse(self, path, payload, auth_token=None, timeout=300.0):
            self.calls.append((path, payload, auth_token, timeout))
            async for line in super().stream_sse(path, payload, auth_token, timeout):
                yield line

    client = _RecordingClient()
    _install(monkeypatch, client)
    request_context = type(
        "Request", (), {"state": type("State", (), {"user_token": "token"})()}
    )()

    response = await endpoint(stream_request, request_context)
    events = _parse_sse(await _collect(response.body_iterator))

    expected_events = ["info", "search_results", "page_result", "done"]
    if endpoint is routes.remote_research_stream:
        expected_events.insert(0, "info")
        assert events[0][1]["code"] == "research_capability_notice"
    assert [event for event, _ in events] == expected_events
    offset = 1 if endpoint is routes.remote_research_stream else 0
    assert events[1 + offset][1]["results"] == [{"url": SERVER_PAGE["url"]}]
    assert tuple(events[2 + offset][1]) == CLIENT_FIELDS
    assert events[2 + offset][1]["elapsed_ms"] == 321
    UpstreamSearchAndScrapeRequest.model_validate(client.calls[0][1])
    assert client.calls == [(expected_path, expected_payload, "token", 300.0)]
