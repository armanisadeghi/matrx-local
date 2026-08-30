"""The LOCAL scrape lane's contract with the one scraper package.

`app/services/scraper/engine.py` is not an engine — it is the desktop execution
lane over `matrx_scraper`. These tests pin the properties that make that lane
worth having, stubbing the package's `scrape()` so nothing here touches the
network, a browser, or an engine process.

The load-bearing one is `use_proxy=False`. Scraping from a downloaded desktop
app is valuable for exactly one reason: the request leaves the USER's own
residential IP. A refactor that flipped the lane onto the datacenter proxy pool
would make matrx-local pointless while every other test still passed — so every
path that reaches the package is asserted here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import matrx_scraper.search as scraper_search
from matrx_scraper.cache import MemoryCache
from matrx_scraper.orchestrator import ScrapeResult
from matrx_scraper.scrape_options import ScrapeOptions
from matrx_scraper.scraper import RequestType

from app.services.ai import key_manager
from app.services.scraper import engine as engine_mod
from app.services.scraper import retry_queue, scrape_store
from app.services.scraper.engine import (
    DEFAULT_SCRAPE_CONCURRENCY,
    RESEARCH_EFFORT_LIMITS,
    LocalScrapeOptions,
    ResearchDoneEvent,
    ResearchPageEvent,
    ScraperEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(url: str, **kwargs: Any) -> ScrapeResult:
    return ScrapeResult(
        url=url, response_url=url, success=True, content_type="html", **kwargs
    )


class RecordingScrape:
    """Stand-in for `matrx_scraper.orchestrator.scrape` that records its calls."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.delay = delay
        self.concurrent = 0
        self.peak_concurrent = 0

    async def __call__(self, url: str, **kwargs: Any) -> ScrapeResult:
        self.calls.append({"url": url, **kwargs})
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return _ok(url, text_data=f"body of {url}")
        finally:
            self.concurrent -= 1


def _stub_scrape(
    monkeypatch: pytest.MonkeyPatch, stub: RecordingScrape | Any
) -> Any:
    """Patch the package seam the lane actually calls."""
    monkeypatch.setattr(engine_mod, "scrape", stub)
    return stub


def _offline_engine(monkeypatch: pytest.MonkeyPatch) -> ScraperEngine:
    """A started engine with NO browser and NO search key.

    Playwright is forced to fail so `start()` never spawns a driver process —
    a unit test must not leave a browser tree behind (and must never touch a
    dev/live engine's world).
    """

    class _NoBrowser:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            raise RuntimeError("playwright unavailable (test)")

    import matrx_scraper.browser_pool as browser_pool_mod

    monkeypatch.setattr(browser_pool_mod, "PlaywrightBrowserPool", _NoBrowser)
    monkeypatch.setattr(key_manager, "get_cached_user_keys", dict)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(scraper_search, "configure_client", lambda _key: None)

    eng = ScraperEngine()
    asyncio.run(eng.start())
    return eng


# ---------------------------------------------------------------------------
# 1. NO PROXY, EVER
# ---------------------------------------------------------------------------


def test_normal_lane_never_uses_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)

    asyncio.run(eng.scrape_one("https://example.com"))

    assert len(stub.calls) == 1
    assert stub.calls[0]["use_proxy"] is False


def test_browser_lane_never_uses_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)

    asyncio.run(
        eng.scrape_one("https://example.com", LocalScrapeOptions(use_browser=True))
    )

    assert stub.calls[0]["use_proxy"] is False
    assert stub.calls[0]["request_type"] is RequestType.BROWSER


def test_bulk_and_stream_lanes_never_use_a_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)

    urls = [f"https://example.com/{i}" for i in range(4)]
    asyncio.run(eng.scrape(urls))

    async def _drain() -> None:
        async for _ in eng.scrape_stream(urls):
            pass

    asyncio.run(_drain())

    assert len(stub.calls) == 8
    assert all(call["use_proxy"] is False for call in stub.calls)


def test_retry_queue_path_never_uses_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server hands a URL back precisely so OUR residential IP fetches it."""
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)
    monkeypatch.setattr(engine_mod, "get_scraper_engine", lambda: eng)

    content = asyncio.run(retry_queue._scrape_locally("https://blocked.example.com"))

    assert content is not None
    assert len(stub.calls) == 1
    assert stub.calls[0]["use_proxy"] is False
    # And the hand-off must not be answered out of our own session cache.
    assert stub.calls[0]["cache"] is None


# ---------------------------------------------------------------------------
# 2. Bad URLs never raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "not a url at all",
        "http://localhost:1234/x",
        "http://192.168.1.20/admin",
        "ftp://example.com/file.txt",
        "https://",
    ],
)
def test_bad_urls_return_a_failed_result_not_an_exception(
    monkeypatch: pytest.MonkeyPatch, bad_url: str
) -> None:
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)

    result = asyncio.run(eng.scrape_one(bad_url))

    assert isinstance(result, ScrapeResult)
    assert result.success is False
    assert result.failure_reason
    assert result.url == bad_url
    # A rejected URL must never reach the package.
    assert stub.calls == []


def test_a_bad_url_in_a_batch_does_not_sink_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)

    results = asyncio.run(
        eng.scrape(["https://good.example.com", "http://localhost:9/x"])
    )

    assert [r.success for r in results] == [True, False]


# ---------------------------------------------------------------------------
# 3 + 4. Option plumbing
# ---------------------------------------------------------------------------


def test_use_browser_selects_the_browser_request_type_and_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)
    sentinel_pool = object()
    eng._browser_pool = sentinel_pool  # type: ignore[assignment]

    asyncio.run(eng.scrape_one("https://example.com"))
    asyncio.run(
        eng.scrape_one("https://example.com", LocalScrapeOptions(use_browser=True))
    )

    default_call, browser_call = stub.calls
    assert default_call["request_type"] is RequestType.NORMAL
    assert browser_call["request_type"] is RequestType.BROWSER
    # The pool is always handed over — the package decides whether to use it.
    assert default_call["browser_pool"] is sentinel_pool
    assert browser_call["browser_pool"] is sentinel_pool


def test_use_cache_toggles_the_session_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)

    asyncio.run(eng.scrape_one("https://example.com"))
    asyncio.run(
        eng.scrape_one("https://example.com", LocalScrapeOptions(use_cache=False))
    )

    cached_call, uncached_call = stub.calls
    assert isinstance(cached_call["cache"], MemoryCache)
    assert cached_call["cache"] is eng._cache
    assert uncached_call["cache"] is None


# ---------------------------------------------------------------------------
# 5 + 6. Concurrency and streaming
# ---------------------------------------------------------------------------


def test_local_lane_default_concurrency_stays_far_below_the_server() -> None:
    """The default is pinned literally on purpose.

    Asserting only against the configured concurrency would let someone raise the
    constant to the server's 20 and keep a green suite; the doctrine is the
    conservative DEFAULT — a desktop app shares the user's uplink with the user.
    """
    assert DEFAULT_SCRAPE_CONCURRENCY == 5


def test_bulk_scrape_is_bounded_for_a_home_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A desktop app must not saturate the user's own uplink."""
    stub = _stub_scrape(monkeypatch, RecordingScrape(delay=0.02))
    eng = _offline_engine(monkeypatch)

    urls = [f"https://example.com/{i}" for i in range(20)]
    results = asyncio.run(eng.scrape(urls))

    assert len(results) == 20
    assert stub.peak_concurrent <= 5
    # …and it really does run in parallel, not serially.
    assert stub.peak_concurrent > 1


def test_scrape_stream_is_bounded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub_scrape(monkeypatch, RecordingScrape(delay=0.02))
    eng = _offline_engine(monkeypatch)

    async def _drain() -> list[ScrapeResult]:
        return [r async for r in eng.scrape_stream(
            [f"https://example.com/{i}" for i in range(20)]
        )]

    results = asyncio.run(_drain())

    assert len(results) == 20
    assert stub.peak_concurrent <= 5
    assert stub.peak_concurrent > 1


def test_scrape_stream_yields_by_completion_not_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the stream is that a fast page shows up first."""
    # Only the configured concurrency starts at once, so keep the batch inside
    # window and make the LAST url the fastest.
    urls = [
        f"https://example.com/{i}" for i in range(DEFAULT_SCRAPE_CONCURRENCY)
    ]
    delays = {url: 0.10 - (i * 0.02) for i, url in enumerate(urls)}

    async def _delayed(url: str, **_kwargs: Any) -> ScrapeResult:
        await asyncio.sleep(delays[url])
        return _ok(url)

    _stub_scrape(monkeypatch, _delayed)
    eng = _offline_engine(monkeypatch)

    async def _drain() -> list[str]:
        return [r.url async for r in eng.scrape_stream(urls)]

    order = asyncio.run(_drain())

    assert sorted(order) == sorted(urls)  # exactly one result per URL
    assert order != urls
    assert order == list(reversed(urls))


# ---------------------------------------------------------------------------
# 7 + 8. scrape_store result mapping and its upstream-rename tripwire
# ---------------------------------------------------------------------------


def test_content_from_result_carries_every_stored_field() -> None:
    result = _ok(
        "https://example.com",
        title="Title",
        text_data="prose",
        ai_research_content="research",
        overview={"a": 1},
        links={"internal": ["https://example.com/x"]},
        main_image="https://example.com/i.png",
        hashes={"text": "abc"},
        cms="wordpress",
        firewall=None,
        status_code=200,
    )

    content = scrape_store.content_from_result(result)

    assert set(scrape_store.STORED_FIELDS) <= set(content)
    assert content["title"] == "Title"
    assert content["text_data"] == "prose"
    assert content["ai_research_content"] == "research"
    assert content["status_code"] == 200
    assert content["scraped_at"]


def test_non_html_results_fall_back_to_raw_text() -> None:
    """PDF / image / JSON scrapes carry their extraction in `raw_text`."""
    for content_type in ("pdf", "image", "json"):
        result = ScrapeResult(
            url="https://example.com/doc",
            response_url="https://example.com/doc",
            success=True,
            content_type=content_type,
            raw_text="EXTRACTED",
        )

        content = scrape_store.content_from_result(result)

        assert content["text_data"] == "EXTRACTED", content_type
        # ai_research_content falls back through text_data, so it lands too.
        assert content["ai_research_content"] == "EXTRACTED", content_type


def test_empty_result_still_yields_string_fields() -> None:
    content = scrape_store.content_from_result(
        ScrapeResult(
            url="https://example.com",
            response_url="https://example.com",
            success=False,
            content_type="unknown",
            failure_reason="boom",
        )
    )
    assert content["text_data"] == ""
    assert content["ai_research_content"] == ""


def test_stored_fields_tripwire_fires_on_an_upstream_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renamed ScrapeResult field must crash, not store null forever."""
    monkeypatch.setattr(
        scrape_store,
        "STORED_FIELDS",
        scrape_store.STORED_FIELDS + ("text_data_renamed_upstream",),
    )

    with pytest.raises(RuntimeError, match="text_data_renamed_upstream"):
        scrape_store.content_from_result(_ok("https://example.com"))


def test_stored_fields_are_all_real_today() -> None:
    scrape_store._assert_known_fields()


# ---------------------------------------------------------------------------
# 9. research()
# ---------------------------------------------------------------------------


def _stub_search(monkeypatch: pytest.MonkeyPatch, count: int) -> list[dict[str, str]]:
    entries = [
        {"url": f"https://example.com/{i}", "title": f"Result {i}"}
        for i in range(count)
    ]

    async def _search(**_kwargs: Any) -> dict[str, Any]:
        return {"web": {"results": []}}

    monkeypatch.setattr(scraper_search, "async_brave_search", _search)
    monkeypatch.setattr(
        scraper_search, "extract_urls_from_search_results", lambda _q: entries
    )
    return entries


def _research_engine(monkeypatch: pytest.MonkeyPatch) -> ScraperEngine:
    eng = _offline_engine(monkeypatch)
    monkeypatch.setattr(eng, "ensure_search_client", lambda: True)
    return eng


def test_research_emits_one_page_event_per_url_then_one_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_search(monkeypatch, count=3)
    _stub_scrape(monkeypatch, RecordingScrape())
    eng = _research_engine(monkeypatch)

    async def _drain() -> list[Any]:
        return [e async for e in eng.research("q", effort="low")]

    events = asyncio.run(_drain())

    pages = [e for e in events if isinstance(e, ResearchPageEvent)]
    done = [e for e in events if isinstance(e, ResearchDoneEvent)]
    assert len(pages) == 3
    assert len(done) == 1
    assert isinstance(events[-1], ResearchDoneEvent)
    assert {p.url for p in pages} == {f"https://example.com/{i}" for i in range(3)}
    assert done[0].total_urls == 3
    assert done[0].scraped == 3
    assert all(f"--- https://example.com/{i} ---" in done[0].text_content
               for i in range(3))


@pytest.mark.parametrize("effort", sorted(RESEARCH_EFFORT_LIMITS))
def test_research_effort_caps_the_page_count(
    monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    limit = RESEARCH_EFFORT_LIMITS[effort]
    _stub_search(monkeypatch, count=limit + 7)
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _research_engine(monkeypatch)

    async def _drain() -> list[Any]:
        return [e async for e in eng.research("q", effort=effort)]

    events = asyncio.run(_drain())

    pages = [e for e in events if isinstance(e, ResearchPageEvent)]
    assert len(pages) == limit
    assert len(stub.calls) == limit
    assert events[-1].total_urls == limit  # type: ignore[union-attr]


def test_research_reports_failed_pages_instead_of_dropping_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_search(monkeypatch, count=3)

    async def _mixed(url: str, **_kwargs: Any) -> ScrapeResult:
        if url.endswith("/1"):
            return ScrapeResult(
                url=url,
                response_url=url,
                success=False,
                content_type="unknown",
                failure_reason="403 Forbidden",
            )
        return _ok(url, text_data="prose")

    _stub_scrape(monkeypatch, _mixed)
    eng = _research_engine(monkeypatch)

    async def _drain() -> list[Any]:
        return [e async for e in eng.research("q", effort="low")]

    events = asyncio.run(_drain())
    pages = {e.url: e for e in events if isinstance(e, ResearchPageEvent)}
    done = events[-1]

    assert len(pages) == 3
    failed = pages["https://example.com/1"]
    assert failed.scrape_failure_reason == "403 Forbidden"
    assert failed.scraped_content is None
    assert done.scraped == 2  # type: ignore[union-attr]
    assert done.total_urls == 3  # type: ignore[union-attr]


def test_research_without_a_search_key_raises_rather_than_scraping_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)
    monkeypatch.setattr(eng, "ensure_search_client", lambda: False)

    async def _drain() -> None:
        async for _ in eng.research("q"):
            pass

    with pytest.raises(RuntimeError, match="Search client not configured"):
        asyncio.run(_drain())


def test_research_asks_only_for_prose(monkeypatch: pytest.MonkeyPatch) -> None:
    """Links / overview / organized_data are the expensive fields; research
    wants readable text per page and nothing else."""
    captured: list[LocalScrapeOptions] = []
    _stub_search(monkeypatch, count=2)
    _stub_scrape(monkeypatch, RecordingScrape())
    eng = _research_engine(monkeypatch)

    original = eng.scrape_one

    async def _spy(url: str, options: LocalScrapeOptions | None = None) -> ScrapeResult:
        assert options is not None
        captured.append(options)
        return await original(url, options)

    monkeypatch.setattr(eng, "scrape_one", _spy)

    async def _drain() -> None:
        async for _ in eng.research("q", effort="low"):
            pass

    asyncio.run(_drain())

    assert captured
    fields: ScrapeOptions = captured[0].fields
    assert fields.get_text_data is True
    assert fields.get_links is False
    assert fields.get_overview is False


# ---------------------------------------------------------------------------
# 10. Readiness and the degraded-browser STATE
# ---------------------------------------------------------------------------


def test_engine_is_not_ready_before_start() -> None:
    eng = ScraperEngine()
    assert eng.is_ready is False
    assert eng.browser_pool is None
    assert eng.driver_pid is None


def test_a_playwright_failure_leaves_the_engine_ready_without_a_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing browser is a degraded STATE, not a startup failure — every
    HTTP scrape still works, only browser-rendered fetches are unavailable."""
    stub = _stub_scrape(monkeypatch, RecordingScrape())
    eng = _offline_engine(monkeypatch)

    assert eng.is_ready is True
    assert eng.browser_pool is None
    assert eng.driver_pid is None

    result = asyncio.run(eng.scrape_one("https://example.com"))
    assert result.success is True
    assert stub.calls[0]["browser_pool"] is None
    assert stub.calls[0]["use_proxy"] is False


def test_stop_clears_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = _offline_engine(monkeypatch)
    asyncio.run(eng.stop())
    assert eng.is_ready is False
    assert eng.browser_pool is None


def test_a_hung_browser_launch_times_out_instead_of_blocking_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Chromium launch that never returns must NOT wedge engine startup.

    2026-08-30: pool.start() hung on a real machine, the FastAPI lifespan
    never yielded, and the desktop reported "did not become reachable within
    300 seconds". The launch is now bounded; on timeout the engine still
    starts, degraded, exactly like any other launch failure.
    """

    class _HangingPool:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def start(self) -> None:
            await asyncio.sleep(3600)

    import matrx_scraper.browser_pool as browser_pool_mod

    monkeypatch.setattr(browser_pool_mod, "PlaywrightBrowserPool", _HangingPool)
    monkeypatch.setattr(key_manager, "get_cached_user_keys", dict)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(scraper_search, "configure_client", lambda _key: None)
    # Keep the test fast: the ceiling's VALUE is a constant, its EXISTENCE is
    # what this test pins.
    monkeypatch.setattr(engine_mod, "BROWSER_POOL_START_TIMEOUT_SECONDS", 0.05)

    from app.services.scraper import browser_runtime

    monkeypatch.setattr(browser_runtime, "record_launch_failure", _RecordCall())
    reaps = _RecordCall()
    monkeypatch.setattr(engine_mod, "terminate_playwright_tree", reaps)

    eng = ScraperEngine()
    asyncio.run(asyncio.wait_for(eng.start(), timeout=5.0))

    assert eng.is_ready is True
    assert eng.browser_pool is None
    # Failure cleanup reaps ONLY the pool's own transport PID. A hung pool
    # has none, so the reap target must be None — never a PID found by
    # scanning this process's children, which could name a DIFFERENT driver
    # we own (the headed local_browser session).
    assert reaps.calls == [((None,), {})]


class _RecordCall:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))
