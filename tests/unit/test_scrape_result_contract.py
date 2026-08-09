"""The client scrape contract — one shape for the local and remote lanes.

Both lanes run the same `matrx_scraper` engine, so a client must not be able
to tell them apart. These tests pin that: the two producers agree key-for-key,
the package's `success`/`failure_reason` contract survives end to end, and the
TypeScript reader in the desktop app declares the same fields.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.scraper.result_contract import (
    CLIENT_FIELDS,
    MAX_TEXT_BYTES,
    from_page_dict,
    from_scrape_result,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TS_CONTRACT = REPO_ROOT / "desktop" / "src" / "lib" / "scrape-result.ts"


def _local_result(**overrides):
    """A ScrapeResult-shaped object with every field the contract reads."""
    fields = {
        "url": "https://example.com",
        "response_url": "https://example.com/",
        "success": True,
        "failure_reason": None,
        "status_code": 200,
        "content_type": "html",
        "title": "Example Domain",
        "text_data": "# Example Domain",
        "ai_research_content": "",
        "raw_text": None,
        "scraped_at": datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        "cms": "unknown",
        "firewall": "cloudflare",
        "links": {"external": ["https://iana.org/domains/example"]},
        "overview": {"page_title": "Example Domain"},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _remote_page(**overrides):
    """What the scraper server streams: ScrapeResult.to_dict() + envelope junk."""
    page = {
        "url": "https://example.com",
        "response_url": "https://example.com/",
        "success": True,
        "status_code": 200,
        "content_type": "html",
        "title": "Example Domain",
        "text_data": "# Example Domain",
        "scraped_at": "2026-08-09T12:00:00+00:00",
        "cms": "unknown",
        "firewall": "cloudflare",
        "links": {"external": ["https://iana.org/domains/example"]},
        "overview": {"page_title": "Example Domain"},
        # FetchResultItem force-adds these — always empty, never engine fields.
        "content": "",
        "status": "",
    }
    page.update(overrides)
    return page


def test_local_and_remote_produce_the_same_payload():
    local = from_scrape_result(_local_result(), elapsed_ms=1234)
    remote = from_page_dict(_remote_page(), elapsed_ms=1234)
    assert local == remote


def test_both_producers_emit_exactly_the_client_fields():
    for payload in (
        from_scrape_result(_local_result()),
        from_page_dict(_remote_page()),
    ):
        assert tuple(payload) == CLIENT_FIELDS


def test_no_legacy_status_or_error_keys():
    """The `status` string / `error` shim is retired — the contract is the package's."""
    payload = from_page_dict(_remote_page(status="success", error="boom"))
    assert "status" not in payload
    assert "error" not in payload
    assert payload["success"] is True


def test_failure_carries_failure_reason_not_error():
    local = from_scrape_result(
        _local_result(success=False, failure_reason="403 Forbidden", status_code=403)
    )
    remote = from_page_dict(
        _remote_page(success=False, failure_reason="403 Forbidden", status_code=403)
    )
    assert local == remote
    assert local["success"] is False
    assert local["failure_reason"] == "403 Forbidden"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"text_data": "html text"}, "html text"),
        # A PDF/image scrape has no text_data at all — raw_text is the extraction.
        ({"text_data": "", "raw_text": "pdf text"}, "pdf text"),
        # Research mode only fills ai_research_content.
        ({"text_data": "", "ai_research_content": "research text"}, "research text"),
        ({"text_data": "", "raw_text": None}, ""),
    ],
)
def test_text_falls_back_to_whichever_extractor_ran(overrides, expected):
    assert from_scrape_result(_local_result(**overrides))["text_data"] == expected


def test_text_is_truncated():
    payload = from_scrape_result(_local_result(text_data="x" * (MAX_TEXT_BYTES + 10)))
    assert payload["text_data"].startswith("x" * 100)
    assert payload["text_data"].endswith("[truncated at 500KB]")


def test_missing_fields_degrade_to_the_declared_types():
    payload = from_page_dict({"url": "https://example.com"})
    assert payload["success"] is False
    assert payload["failure_reason"] is None
    assert payload["status_code"] is None
    assert payload["title"] == ""
    assert payload["text_data"] == ""
    assert payload["response_url"] == "https://example.com"
    assert payload["elapsed_ms"] == 0


def test_typescript_reader_declares_the_same_fields():
    """Cross-language tripwire.

    The shape travels as free-form tool metadata, so no generated type covers
    it — this test is what keeps `scrape-result.ts` and `CLIENT_FIELDS` from
    drifting apart.
    """
    source = TS_CONTRACT.read_text()
    body = source.split("export interface ScrapeResultData {", 1)[1].split("}", 1)[0]
    ts_fields = tuple(re.findall(r"^\s*(\w+):", body, flags=re.MULTILINE))
    assert set(ts_fields) == set(CLIENT_FIELDS)
