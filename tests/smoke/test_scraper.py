"""
Scraper smoke tests — both lanes.

REMOTE lane (the proxy to scraper.app.matrxserver.com):
  GET /remote-scraper/status — public; verifies the scraper connection state
                               (works even if remote scraper server is unreachable)

LOCAL lane (the reason this app exists): the `Scrape` tool through
POST /tools/invoke, which runs the `matrx_scraper` package on THIS machine and
the user's own IP via app/services/scraper/engine.py. Covered here because the
local lane was rewritten on 2026-08-09 (the forked `scraper-service/` engine was
deleted) with no automated re-check behind it.

Network policy for the local-lane tests: exactly ONE real fetch happens, in the
session-scoped `example_com_scrape` fixture, of a deliberately boring stable URL.
If this machine is offline — or the fetch fails for a transport reason — those
tests SKIP with the reason attached; they never turn a dropped connection into a
red regression. The error-path, SSRF and auth tests need no network at all.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

# The one stable target. example.com is IANA-operated, has been a documentation
# constant for 30 years, and its markup is three paragraphs — nothing about it
# drifts under us.
STABLE_URL = "https://example.com"

# RFC 2606 reserves `.invalid` — it can never resolve, so this exercises the
# failure path without depending on some real domain staying dead.
UNRESOLVABLE_URL = "https://matrx-smoke-nonexistent-8f3a.invalid/"

# Substrings that mean "the network, not the code, is what failed". Used ONLY to
# convert a transient outage into a skip; anything else still fails the test.
TRANSPORT_FAILURES = (
    "request_error",
    "timeout",
    "timed out",
    "connection",
    "connect",
    "resolve",
    "dns",
    "network",
    "ssl",
)

# Every bucket matrx_scraper's link organizer emits. A missing key means the
# organizer's contract changed and downstream consumers are reading `None`.
LINK_BUCKETS = (
    "internal",
    "external",
    "images",
    "documents",
    "audio",
    "videos",
    "archives",
    "others",
)


def invoke_scrape(client: httpx.Client, **params: Any) -> dict[str, Any]:
    """POST /tools/invoke for the Scrape tool.

    The endpoint must answer 200 even when the scrape itself fails — a failed
    fetch is a RESULT (`type: "error"`), never an HTTP 500. Callers assert on
    the body.
    """
    r = client.post("/tools/invoke", json={"tool": "Scrape", "input": params})
    assert r.status_code == 200, (
        f"POST /tools/invoke(Scrape) returned {r.status_code}: {r.text[:500]}"
    )
    return r.json()


def _skip_if_transport_failure(data: dict[str, Any], context: str) -> None:
    """Skip (don't fail) when the only thing wrong is the network."""
    if data.get("type") != "error":
        return
    blob = f"{data.get('output', '')} {data.get('metadata', {}).get('error', '')}".lower()
    if any(marker in blob for marker in TRANSPORT_FAILURES):
        pytest.skip(
            f"{context}: outbound network unavailable or the fetch failed in "
            f"transport — {data.get('output', '')[:200]}"
        )


@pytest.fixture(scope="session")
def example_com_scrape(http: httpx.Client) -> dict[str, Any]:
    """The suite's ONE real local scrape, shared by every success-path test.

    Session-scoped so the network is touched once. `use_cache=False` so the
    fetch is genuinely exercised rather than served from the session cache of a
    previous test.

    Note this deliberately does NOT construct a `ScraperEngine` — it drives the
    engine the session fixture already spawned, over HTTP. Building one in a
    fixture has previously reaped the developer's running dev engine.
    """
    data = invoke_scrape(
        http,
        urls=[STABLE_URL],
        use_cache=False,
        get_links=True,
        get_overview=True,
    )
    _skip_if_transport_failure(data, f"scraping {STABLE_URL}")
    return data


def test_remote_scraper_status_public(http_public: httpx.Client) -> None:
    """GET /remote-scraper/status is public and returns 200."""
    r = http_public.get("/remote-scraper/status")
    assert r.status_code == 200, r.text


def test_remote_scraper_status_structure(http_public: httpx.Client) -> None:
    """GET /remote-scraper/status returns a dict with connectivity info."""
    r = http_public.get("/remote-scraper/status")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict), f"Expected dict, got {type(data).__name__}"
    # Should have some connectivity/status field
    has_status = any(
        k in data
        for k in ("available", "status", "connected", "reachable", "ok", "error")
    )
    assert has_status, (
        f"Remote scraper status missing connectivity key. Got: {list(data.keys())}"
    )


def test_remote_scraper_config_domains(http_public: httpx.Client) -> None:
    """GET /remote-scraper/config/domains returns 200."""
    r = http_public.get("/remote-scraper/config/domains")
    assert r.status_code in (200, 401, 503), (
        f"Unexpected status from /remote-scraper/config/domains: {r.status_code} {r.text}"
    )


def test_tunnel_status(http_public: httpx.Client) -> None:
    """GET /tunnel/status (public) returns a structured response."""
    r = http_public.get("/tunnel/status")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# LOCAL scrape lane — app/services/scraper/engine.py over the matrx_scraper
# package, reached through the `Scrape` tool.
# ---------------------------------------------------------------------------


def test_local_scrape_succeeds(example_com_scrape: dict[str, Any]) -> None:
    """A local scrape of a stable URL succeeds with a 200 and real content."""
    data = example_com_scrape
    assert data["type"] == "success", f"Scrape reported failure: {data}"

    meta = data.get("metadata", {})
    assert meta.get("status") == "success", f"metadata.status not success: {meta}"
    assert meta.get("status_code") == 200, f"Expected HTTP 200 from the target: {meta}"
    assert meta.get("content_type") == "html", f"Unexpected content_type: {meta}"
    assert meta.get("title"), f"No page title extracted: {meta}"

    output = data.get("output") or ""
    assert output.strip(), "Scrape returned an empty output body"
    assert "(no text content extracted)" not in output, (
        "The parser produced no text for a plain HTML page — extraction is broken, "
        f"not the fetch. Output head: {output[:300]}"
    )
    assert meta["title"] in output, (
        f"Rendered output does not carry the page title: {output[:300]}"
    )


def test_local_scrape_returns_link_buckets(example_com_scrape: dict[str, Any]) -> None:
    """A scrape asking for links returns every bucket the organizer defines.

    Pins the ORGANIZER's contract, not the `get_links` flag: the flag is
    currently inert in the local lane (MXL-D-076) — `engine.scrape_one` calls the
    package's `scrape()`, which takes no field options and always computes
    everything, and `tool_scrape` never runs the `apply_field_flags` filter. What
    matters to every consumer today is that these buckets arrive and are
    classified, which is what this asserts.
    """
    links = example_com_scrape.get("metadata", {}).get("links")
    assert isinstance(links, dict), (
        f"No links dict on the result: {example_com_scrape.get('metadata')}"
    )

    missing = [bucket for bucket in LINK_BUCKETS if bucket not in links]
    assert not missing, f"Link buckets missing from the result: {missing} (got {list(links)})"

    assert any(links[bucket] for bucket in LINK_BUCKETS), (
        f"Every link bucket is empty — the page has links, so nothing was classified: {links}"
    )


def test_local_scrape_returns_overview(example_com_scrape: dict[str, Any]) -> None:
    """A scrape asking for an overview returns the structured page overview.

    Same caveat as the link buckets: `get_overview` is inert today
    (MXL-D-076); what is pinned is that the overview is produced and carries the
    keys consumers read.
    """
    overview = example_com_scrape.get("metadata", {}).get("overview")
    assert isinstance(overview, dict), (
        f"No overview dict on the result: {example_com_scrape.get('metadata')}"
    )

    for key in ("page_title", "char_count", "outline", "metadata"):
        assert key in overview, f"overview missing '{key}': {list(overview)}"

    assert overview["page_title"], f"overview.page_title is empty: {overview}"


def test_local_scrape_unresolvable_domain_is_a_result_not_a_500(
    http: httpx.Client,
) -> None:
    """A dead domain returns type error with a reason — the endpoint stays 200.

    `invoke_scrape` already asserts the HTTP 200: a fetch failure must surface as
    a tool RESULT the caller can read, never as a server error.
    """
    data = invoke_scrape(
        http, urls=[UNRESOLVABLE_URL], use_cache=False, get_links=False,
        get_overview=False,
    )

    if data.get("type") == "success":
        pytest.skip(
            "An RFC 2606 .invalid host resolved — this resolver hijacks NXDOMAIN, "
            "so the failure path cannot be exercised here."
        )

    assert data["type"] == "error", f"Expected an error result, got: {data}"
    meta = data.get("metadata", {})
    assert meta.get("status") == "error", f"metadata.status not error: {meta}"
    assert meta.get("error"), f"No failure reason on a failed scrape: {meta}"
    assert "SCRAPE ERROR" in (data.get("output") or ""), (
        f"Failure not rendered into the output body: {data.get('output')!r}"
    )


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "[::1]"],
    ids=["localhost", "loopback-ipv4", "loopback-ipv6"],
)
def test_local_scrape_refuses_loopback(http: httpx.Client, engine_url: str, host: str) -> None:
    """SSRF gate: the scraper refuses loopback targets instead of fetching them.

    This is a security boundary, not a nicety. The engine holds a token that any
    cloud agent can present; if `validate_and_correct_url` ever stops rejecting
    loopback and private hosts, the desktop app silently becomes an
    internal-network proxy for whoever holds that token.

    The target is the test engine's OWN /health — a URL that is guaranteed
    reachable and whose body is unmistakable. So this asserts two things: the
    scrape was refused, AND the response does not contain what /health would
    have returned had it actually been fetched.
    """
    port = httpx.URL(engine_url).port
    target = f"http://{host}:{port}/health"

    data = invoke_scrape(
        http, urls=[target], use_cache=False, get_links=False, get_overview=False
    )

    assert data["type"] == "error", (
        f"Loopback URL {target} was NOT refused — the SSRF gate is open: {data}"
    )

    reason = (data.get("metadata", {}).get("error") or "") + " " + (data.get("output") or "")
    assert "localhost" in reason.lower() or "non-public ip" in reason.lower(), (
        f"Loopback refusal did not name a loopback/private-address reason "
        f"(so it may have failed for an unrelated reason): {reason[:300]!r}"
    )

    body = data.get("output") or ""
    assert "Status: 200" not in body, f"Loopback target was actually fetched: {body[:300]}"
    for leaked in ("uptime", "engine", "healthy", "\"status\":"):
        assert leaked not in body.lower(), (
            f"Response body contains /health payload — the loopback URL was fetched "
            f"despite the error type: {body[:300]}"
        )


def test_tools_invoke_requires_auth(http_public: httpx.Client) -> None:
    """POST /tools/invoke without an Authorization header is rejected."""
    r = http_public.post(
        "/tools/invoke",
        json={"tool": "Scrape", "input": {"urls": [STABLE_URL]}},
    )
    assert r.status_code == 401, (
        f"Unauthenticated /tools/invoke returned {r.status_code}, expected 401: "
        f"{r.text[:300]}"
    )
