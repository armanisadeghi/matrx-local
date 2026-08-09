"""The local scrape lane honours the USER's concurrency preference.

`MAX_SCRAPE_CONCURRENCY = 5` used to be a module constant — a guess about
someone else's laptop and home connection. It is now a setting, and these tests
pin the two properties that make that real:

1. the semaphore actually admits exactly the configured number of calls at once
   (counted at a NON-default value, so a hardcoded 5 fails the test), and
2. the value is read at call time, so changing it applies to the next scrape
   with no engine restart.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.services.scraper import engine as engine_mod
from app.services.scraper.engine import (
    DEFAULT_RESEARCH_CONCURRENCY,
    DEFAULT_SCRAPE_CONCURRENCY,
    MAX_CONCURRENCY,
    MIN_CONCURRENCY,
    ScraperEngine,
    clamp_concurrency,
)


class _FakeSync:
    """Stand-in for SettingsSync — a plain dict with a .get()."""

    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSync:
    """Point the engine's settings lookup at an in-test settings blob."""
    fake = _FakeSync({})
    import app.services.cloud_sync.settings_sync as sync_mod

    monkeypatch.setattr(sync_mod, "get_settings_sync", lambda: fake)
    return fake


class _ConcurrencyProbe:
    """Replacement for scrape_one that records peak simultaneous in-flight calls."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def __call__(self, url: str, options: object = None) -> str:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        # Yield enough times that every admitted coroutine overlaps.
        for _ in range(5):
            await asyncio.sleep(0)
        self.in_flight -= 1
        return url


# ── clamp_concurrency ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, 1),
        (12, 12),
        (20, 20),
        (0, MIN_CONCURRENCY),
        (-3, MIN_CONCURRENCY),
        (500, MAX_CONCURRENCY),
        ("7", 7),
        (None, DEFAULT_SCRAPE_CONCURRENCY),
        ("nonsense", DEFAULT_SCRAPE_CONCURRENCY),
    ],
)
def test_clamp_concurrency(raw: object, expected: int) -> None:
    assert clamp_concurrency(raw, DEFAULT_SCRAPE_CONCURRENCY) == expected


# ── The engine reads the setting, fresh, every call ──────────────────────────


def test_defaults_when_unset(settings: _FakeSync) -> None:
    eng = ScraperEngine()
    assert eng.scrape_concurrency == DEFAULT_SCRAPE_CONCURRENCY == 5
    assert eng.research_concurrency == DEFAULT_RESEARCH_CONCURRENCY == 5


def test_value_is_not_cached_between_reads(settings: _FakeSync) -> None:
    eng = ScraperEngine()
    settings.values["scrape_concurrency"] = 2
    assert eng.scrape_concurrency == 2
    # No restart, no re-start() — the very next read sees the new value.
    settings.values["scrape_concurrency"] = 15
    assert eng.scrape_concurrency == 15


def test_out_of_range_stored_value_is_clamped_not_obeyed(settings: _FakeSync) -> None:
    eng = ScraperEngine()
    settings.values["scrape_concurrency"] = 500
    settings.values["research_concurrency"] = 0
    assert eng.scrape_concurrency == MAX_CONCURRENCY
    assert eng.research_concurrency == MIN_CONCURRENCY


# ── The semaphore honours it ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_scrape_honours_configured_concurrency(settings: _FakeSync) -> None:
    settings.values["scrape_concurrency"] = 3  # deliberately not the default
    eng = ScraperEngine()
    probe = _ConcurrencyProbe()
    eng.scrape_one = probe  # type: ignore[method-assign]

    results = await eng.scrape([f"https://example.com/{i}" for i in range(12)])

    assert len(results) == 12
    assert probe.peak == 3


@pytest.mark.anyio
async def test_scrape_stream_honours_configured_concurrency(
    settings: _FakeSync,
) -> None:
    settings.values["scrape_concurrency"] = 8
    eng = ScraperEngine()
    probe = _ConcurrencyProbe()
    eng.scrape_one = probe  # type: ignore[method-assign]

    seen = [r async for r in eng.scrape_stream([f"https://e.com/{i}" for i in range(20)])]

    assert len(seen) == 20
    assert probe.peak == 8


@pytest.mark.anyio
async def test_research_honours_configured_concurrency(
    settings: _FakeSync, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.values["research_concurrency"] = 2
    urls = [f"https://research.example/{i}" for i in range(10)]

    eng = ScraperEngine()
    monkeypatch.setattr(eng, "ensure_search_client", lambda: True)
    probe = _ConcurrencyProbe()

    class _Result:
        def __init__(self, url: str) -> None:
            self.url = url
            self.title = ""
            self.ai_research_content = "text"
            self.text_data = None
            self.raw_text = None
            self.failure_reason = None

    async def _scrape_one(url: str, options: object = None) -> _Result:
        await probe(url)
        return _Result(url)

    eng.scrape_one = _scrape_one  # type: ignore[method-assign]

    import matrx_scraper.search as search_mod

    async def _fake_search(**_kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(search_mod, "async_brave_search", _fake_search)
    monkeypatch.setattr(
        search_mod,
        "extract_urls_from_search_results",
        lambda _pairs: [{"url": u, "title": ""} for u in urls],
    )

    pages = [
        ev
        async for ev in eng.research("anything", effort="low")
        if isinstance(ev, engine_mod.ResearchPageEvent)
    ]

    assert len(pages) == 10
    assert probe.peak == 2


# ── The API rejects an out-of-range value with a message ─────────────────────


def test_settings_route_rejects_out_of_range() -> None:
    from app.api.settings_routes import EngineSettings

    assert EngineSettings().scrape_concurrency == DEFAULT_SCRAPE_CONCURRENCY

    with pytest.raises(ValidationError) as excinfo:
        EngineSettings(scrape_concurrency=500)
    assert "between 1 and 20" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        EngineSettings(research_concurrency=0)
    assert "between 1 and 20" in str(excinfo.value)
