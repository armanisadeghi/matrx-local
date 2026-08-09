from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── Playwright browser automation ─────────────────────────────────────────────

class BrowserNavigateArgs(BaseModel):
    url: str = Field(description="URL to navigate to (must include scheme: http/https).")
    wait_for: str | None = Field(
        default=None,
        description=(
            "CSS selector to wait for after navigation before returning. "
            "Useful for SPAs or pages with lazy-loaded content."
        ),
    )
    timeout: int = Field(
        default=30,
        ge=1,
        le=120,
        description="Seconds to wait for the page to load.",
    )


class BrowserClickArgs(BaseModel):
    selector: str = Field(description="CSS selector of the element to click.")
    timeout: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Seconds to wait for the element to appear before clicking.",
    )


class BrowserTypeArgs(BaseModel):
    selector: str = Field(description="CSS selector of the input element to type into.")
    text: str = Field(description="Text to type.")
    clear_first: bool = Field(
        default=True,
        description="Clear the field before typing.",
    )
    press_enter: bool = Field(
        default=False,
        description="Press Enter after typing.",
    )
    timeout: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Seconds to wait for the element.",
    )


class BrowserExtractArgs(BaseModel):
    selector: str | None = Field(
        default=None,
        description=(
            "CSS selector to scope extraction. If omitted, extracts from full page."
        ),
    )
    extract_type: Literal["text", "html", "attribute", "value"] = Field(
        default="text",
        description=(
            "What to extract: 'text' (visible text), 'html' (innerHTML), "
            "'attribute' (requires attribute param), 'value' (form field value)."
        ),
    )
    attribute: str | None = Field(
        default=None,
        description="HTML attribute name to extract when extract_type='attribute'.",
    )
    all_matches: bool = Field(
        default=False,
        description="If true, return a list of all matching elements; otherwise return the first.",
    )


class BrowserScreenshotArgs(BaseModel):
    full_page: bool = Field(
        default=False,
        description="If true, capture the full scrollable page instead of just the viewport.",
    )
    selector: str | None = Field(
        default=None,
        description="CSS selector of a specific element to screenshot.",
    )


class BrowserEvalArgs(BaseModel):
    javascript: str = Field(
        description="JavaScript expression to evaluate in the page context. The return value is serialized."
    )


class BrowserTabsArgs(BaseModel):
    action: Literal["list", "new", "close", "switch"] = Field(
        default="list",
        description=(
            "Tab action: 'list' (list all tabs), 'new' (open a new tab), "
            "'close' (close tab at tab_index), 'switch' (focus tab at tab_index)."
        ),
    )
    tab_index: int | None = Field(
        default=None,
        description="Zero-based tab index, required for 'close' and 'switch' actions.",
    )
    url: str | None = Field(
        default=None,
        description="URL to load when action='new'.",
    )


# ── HTTP / Scraping ───────────────────────────────────────────────────────────

class FetchUrlArgs(BaseModel):
    url: str = Field(description="URL to fetch.")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = Field(
        default="GET",
        description="HTTP method.",
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Additional request headers.",
    )
    body: str | None = Field(
        default=None,
        description="Request body (for POST/PUT/PATCH). Typically JSON.",
    )
    follow_redirects: bool = Field(
        default=True,
        description="Follow HTTP redirects.",
    )
    timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Seconds before the request times out.",
    )


class FetchWithBrowserArgs(BaseModel):
    url: str = Field(description="URL to fetch using a headless browser.")
    wait_for: str | None = Field(
        default=None,
        description="CSS selector to wait for after page load.",
    )
    wait_timeout: int = Field(
        default=30000,
        ge=1000,
        le=120000,
        description="Milliseconds to wait for wait_for selector.",
    )
    extract_text: bool = Field(
        default=False,
        description="If true, return plain text instead of full HTML.",
    )


class ScrapeArgs(BaseModel):
    urls: list[str] = Field(
        description="List of URLs to scrape (max 10 per call).",
        min_length=1,
        max_length=10,
    )
    use_cache: bool = Field(
        default=True,
        description="Return cached results if available.",
    )
    output_mode: Literal["rich", "text", "html", "markdown"] = Field(
        default="rich",
        description=(
            "'rich' returns structured data with metadata, "
            "'text' plain text, 'html' raw HTML, 'markdown' Markdown."
        ),
    )
    get_links: bool = Field(
        default=False,
        description="Also return a list of all links found on the page.",
    )
    get_overview: bool = Field(
        default=False,
        description="Include a brief AI-generated overview of the page content.",
    )
    get_extraction: bool = Field(
        default=False,
        description=(
            "Also return the page's STRUCTURED extraction in the result "
            "metadata: heading outline, tables as rows, images/videos/audios, "
            "code blocks, renderable markdown, head metadata, redirect chain. "
            "The parse produces all of this either way; this only controls "
            "whether it is returned, and it can be large — ask for it when "
            "something will actually read it (a UI, a table extraction), not "
            "for prose."
        ),
    )


class SearchArgs(BaseModel):
    keywords: list[str] = Field(
        description="Search terms to look up.",
        min_length=1,
    )
    country: str = Field(
        default="us",
        description="Two-letter country code for localized results.",
    )
    count: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of results to return.",
    )
    freshness: str | None = Field(
        default=None,
        description=(
            "Filter by result age: 'pd' (past day), 'pw' (past week), "
            "'pm' (past month), 'py' (past year)."
        ),
    )


class ResearchArgs(BaseModel):
    query: str = Field(description="Research question or topic.")
    country: str = Field(
        default="us",
        description="Two-letter country code for localized search results.",
    )
    effort: Literal["low", "medium", "high"] = Field(
        default="medium",
        description=(
            "'low' (search only), 'medium' (search + top results scraped), "
            "'high' (search + all results scraped + synthesis)."
        ),
    )
    freshness: str | None = Field(
        default=None,
        description="Filter by age: 'pd', 'pw', 'pm', 'py'.",
    )
