"""The Scrape tool's result-metadata contract.

The desktop Scraping page renders this payload directly
(`desktop/src/lib/scrape-extraction.ts`), so three things must stay true:

  * the long-standing keys keep their names and meanings,
  * every extraction block is opt-in — the same metadata is handed to the model
    by `local_tool_bridge`, so an unconditional table set would land in an
    agent's context on every scrape,
  * a cap NEVER lies: what is dropped is reported in `truncated` /
    `link_counts` / `rows_total`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools.tools.network import (
    MAX_IMAGES,
    MAX_LINKS_PER_BUCKET,
    MAX_TABLE_ROWS,
    MAX_TABLES,
    _scrape_result_to_metadata,
)


def make_result(**overrides):
    """A ScrapeResult-shaped stand-in (the package dataclass, minus the parse)."""
    base = dict(
        url="https://example.com/a",
        response_url="https://example.com/a",
        success=True,
        content_type="html",
        status_code=200,
        scraped_at="2026-08-09T00:00:00+00:00",
        title="A page",
        published_at=None,
        modified_at=None,
        cms="wordpress",
        firewall="none",
        failure_reason=None,
        overview={"website": "example.com"},
        document_outline=[{"type": "header", "level": 1, "content": "Top"}],
        tables=[{"type": "table", "rows": [{"col1": "a"}]}],
        images=[{"type": "image", "src": "https://example.com/i.png"}],
        videos=[],
        audios=[],
        code_blocks=[{"type": "code", "content": "print(1)"}],
        links={"internal": ["https://example.com/b"], "external": []},
        markdown_renderable="# Top\n\nbody",
        metadata={"canonical_url": "https://example.com/a"},
        redirect_chain=[{"status": 200, "url": "https://example.com/a"}],
        hashes={"simhash": "abc"},
        main_image="https://example.com/main.png",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_defaults_carry_only_the_transport_facts():
    meta = _scrape_result_to_metadata(make_result())

    assert meta["status"] == "success"
    assert meta["url"] == "https://example.com/a"
    assert meta["status_code"] == 200
    assert meta["content_type"] == "html"
    assert meta["title"] == "A page"
    assert meta["cms"] == "wordpress"
    assert meta["firewall"] == "none"

    # Every extraction block is opt-in — this payload also reaches the model.
    for key in (
        "overview",
        "links",
        "document_outline",
        "tables",
        "images",
        "code_blocks",
        "markdown_renderable",
        "page_metadata",
        "redirect_chain",
        "hashes",
    ):
        assert key not in meta, f"{key} must be opt-in"


def test_flags_open_exactly_their_own_block():
    links_only = _scrape_result_to_metadata(make_result(), include_links=True)
    assert links_only["links"] == {"internal": ["https://example.com/b"], "external": []}
    assert links_only["link_counts"] == {"internal": 1, "external": 0}
    assert "tables" not in links_only

    overview_only = _scrape_result_to_metadata(make_result(), include_overview=True)
    assert overview_only["overview"] == {"website": "example.com"}
    assert "links" not in overview_only

    extraction = _scrape_result_to_metadata(make_result(), include_extraction=True)
    assert extraction["document_outline"][0]["content"] == "Top"
    assert extraction["tables"][0]["rows"] == [{"col1": "a"}]
    assert extraction["images"][0]["src"] == "https://example.com/i.png"
    assert extraction["code_blocks"][0]["content"] == "print(1)"
    assert extraction["markdown_renderable"] == "# Top\n\nbody"
    assert extraction["page_metadata"] == {"canonical_url": "https://example.com/a"}
    assert extraction["redirect_chain"] == [{"status": 200, "url": "https://example.com/a"}]
    assert extraction["hashes"] == {"simhash": "abc"}
    assert extraction["main_image"] == "https://example.com/main.png"
    assert extraction["scraped_at"] == "2026-08-09T00:00:00+00:00"
    # Empty media lists are omitted, not shipped as empty scaffolding.
    assert "videos" not in extraction
    assert "audios" not in extraction
    assert "truncated" not in extraction


def test_a_non_html_result_reports_nothing_rather_than_empty_panels():
    """A PDF/image/JSON scrape carries `raw_text` and no HTML extraction."""
    pdf = make_result(
        content_type="pdf",
        document_outline=None,
        tables=None,
        images=None,
        videos=None,
        audios=None,
        code_blocks=None,
        links=None,
        markdown_renderable=None,
        metadata=None,
        hashes={},
        main_image=None,
        overview=None,
    )
    meta = _scrape_result_to_metadata(
        pdf, include_extraction=True, include_links=True, include_overview=True
    )

    assert meta["status"] == "success"
    assert meta["content_type"] == "pdf"
    for key in (
        "document_outline",
        "tables",
        "images",
        "videos",
        "audios",
        "code_blocks",
        "links",
        "markdown_renderable",
        "page_metadata",
        "hashes",
        "overview",
    ):
        assert key not in meta, f"{key} must be absent, not empty"


def test_failure_reports_the_reason_and_the_firewall():
    meta = _scrape_result_to_metadata(
        make_result(success=False, failure_reason="cloudflare_challenge", firewall="cloudflare")
    )
    assert meta["status"] == "error"
    assert meta["error"] == "cloudflare_challenge"
    assert meta["firewall"] == "cloudflare"


def test_caps_never_lie_about_what_was_dropped():
    result = make_result(
        tables=[
            {"type": "table", "rows": [{"c": str(i)} for i in range(MAX_TABLE_ROWS + 40)]}
        ]
        + [{"type": "table", "rows": [{"c": "x"}]} for _ in range(MAX_TABLES + 5)],
        images=[{"type": "image", "src": f"https://e.com/{i}.png"} for i in range(MAX_IMAGES + 10)],
        links={"internal": [f"https://e.com/{i}" for i in range(MAX_LINKS_PER_BUCKET + 25)]},
    )
    meta = _scrape_result_to_metadata(result, include_extraction=True, include_links=True)

    assert len(meta["tables"]) == MAX_TABLES
    assert len(meta["tables"][0]["rows"]) == MAX_TABLE_ROWS
    assert meta["tables"][0]["rows_total"] == MAX_TABLE_ROWS + 40
    assert len(meta["images"]) == MAX_IMAGES
    assert len(meta["links"]["internal"]) == MAX_LINKS_PER_BUCKET
    # The TRUE total survives the cap, so the UI can say "500 of 525".
    assert meta["link_counts"]["internal"] == MAX_LINKS_PER_BUCKET + 25
    assert meta["truncated"] == {"links": True, "tables": True, "images": True}


@pytest.mark.parametrize("flag", ["include_extraction", "include_links", "include_overview"])
def test_missing_attributes_are_tolerated(flag):
    """Cached/partial results may not carry every field; none of that raises."""
    bare = SimpleNamespace(
        url="https://e.com",
        success=True,
        status_code=200,
        content_type="html",
        title=None,
        cms=None,
        firewall=None,
        overview=None,
        failure_reason=None,
    )
    meta = _scrape_result_to_metadata(bare, **{flag: True})
    assert meta["status"] == "success"
