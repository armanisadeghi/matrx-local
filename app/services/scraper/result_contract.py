"""THE client-facing scrape result shape — one contract, both lanes.

Local and remote scrapes run the SAME engine (`matrx_scraper`), so a client
must never be able to tell them apart by looking at the payload. This module
is the ONE place a scrape result becomes the dict a client reads; every field
name here is a real `matrx_scraper.ScrapeResult` field.

Two producers, one shape:

* `from_scrape_result()` — a local `ScrapeResult` object (the `Scrape` tool).
* `from_page_dict()`     — a page dict from the scraper server, which is
  `ScrapeResult.to_dict()` reshaped by the streaming envelope. The envelope
  (`FetchResultItem`, extra="allow") force-adds empty `title`/`content`/
  `status` keys to every page; those are envelope artefacts, not engine
  fields, and are dropped here.

The contract is the package's: `success: bool` + `failure_reason: str | None`.
There is no `status` string and no `error` field anywhere downstream — if you
find yourself writing one, you are re-forking the contract.

Consumers: `app/tools/tools/network.py` (Scrape / FetchWithBrowser tool
metadata), `app/api/remote_scraper_routes.py` (the /remote-scraper/scrape
proxy and its stream), and `desktop/src/lib/scrape-result.ts` (the single
client-side reader).
"""

from __future__ import annotations

from typing import Any

# Same ceiling the tool applies to its prose output, so a client never gets a
# structured payload larger than the human one.
MAX_TEXT_BYTES = 500_000
_TRUNCATION_NOTE = "\n\n... [truncated at 500KB]"

#: Every key a client result carries. Ordered for readability; a consumer that
#: needs a new field adds it HERE and in `scrape-result.ts`, never at a call site.
CLIENT_FIELDS: tuple[str, ...] = (
    "url",
    "response_url",
    "success",
    "failure_reason",
    "status_code",
    "content_type",
    "title",
    "text_data",
    "scraped_at",
    "cms",
    "firewall",
    "links",
    "overview",
    "elapsed_ms",
)

# Keys the streaming envelope invents. `content`/`status` are always empty and
# would shadow the real fields if a client ever reached for them.
_ENVELOPE_ARTEFACTS = frozenset({"content", "status", "error"})


def _truncate(text: str) -> str:
    if len(text) <= MAX_TEXT_BYTES:
        return text
    return text[:MAX_TEXT_BYTES] + _TRUNCATION_NOTE


def _text_of(get: Any) -> str:
    """Page text, whichever extractor produced it.

    `text_data` is the HTML path; PDFs / images / JSON land in `raw_text`, and
    a research-mode scrape only fills `ai_research_content`. Falling back keeps
    a successful non-HTML scrape from looking empty to the UI.
    """
    for key in ("text_data", "ai_research_content", "raw_text"):
        value = get(key)
        if value:
            return _truncate(str(value))
    return ""


def _build(get: Any, elapsed_ms: int | None) -> dict[str, Any]:
    return {
        "url": get("url") or "",
        "response_url": get("response_url") or get("url") or "",
        "success": bool(get("success")),
        "failure_reason": get("failure_reason") or None,
        "status_code": get("status_code"),
        "content_type": get("content_type") or None,
        "title": get("title") or "",
        "text_data": _text_of(get),
        "scraped_at": _iso(get("scraped_at")),
        "cms": get("cms") or None,
        "firewall": get("firewall") or None,
        "links": get("links") or None,
        "overview": get("overview") or None,
        "elapsed_ms": int(elapsed_ms) if elapsed_ms is not None else 0,
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def from_scrape_result(result: Any, elapsed_ms: int | None = None) -> dict[str, Any]:
    """Convert a local `matrx_scraper.ScrapeResult` into the client shape."""
    return _build(lambda key: getattr(result, key, None), elapsed_ms)


def from_page_dict(page: dict[str, Any], elapsed_ms: int | None = None) -> dict[str, Any]:
    """Convert a page *dict* into the client shape.

    Used for the scraper server's pages — it hands back
    `ScrapeResult.to_dict()` (None values omitted) plus the envelope
    artefacts, so this is a field selection, never a rename — and for the
    browser lane, which synthesizes the same field names from a Playwright
    fetch.
    """
    clean = {k: v for k, v in page.items() if k not in _ENVELOPE_ARTEFACTS}
    return _build(clean.get, elapsed_ms)
