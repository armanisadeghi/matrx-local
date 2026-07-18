"""Remote fetch paths for the catalog entries.

Two independent paths, tried in order (same posture as app_config/client.py):

  1. Primary — Supabase PostgREST
     ``GET {SUPABASE_URL}/rest/v1/catalog_entries?app=eq.matrx-local&select=*``
     with the publishable key only (anon read via RLS returns active rows;
     MUST work pre-login, so no JWT).
  2. Fallback — aidream
     ``GET {AIDREAM_SERVER_URL}/api/catalogs/matrx-local`` (public,
     unauthenticated, server-cached, active rows only). Protects against
     Supabase-side outages. The endpoint ships from aidream separately —
     this client codes to the contract in
     /Users/armanisadeghi/code/common-docs/remote-catalogs/FEATURE.md.

Both return the same rows. PostgREST responds with a bare JSON array; the
aidream endpoint may wrap it (``{"entries": [...]}``) — both shapes are
accepted, anything else is a validation failure for that path. A payload
that fails schema validation counts as a failure of THAT path — the other
path is still tried. If every path fails, ``CatalogsFetchError`` carries
all per-path reasons.

Bootstrap invariant: the URLs used HERE (Supabase URL + publishable key,
aidream fallback URL) are compiled-in bootstrap constants and are NEVER
remotely configurable — a hostile catalog row must not be able to repoint
where the app fetches its catalogs from.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.common.system_logger import get_logger

from app.config import (
    AIDREAM_SERVER_URL,
    SUPABASE_PUBLISHABLE_KEY,
    SUPABASE_URL,
)
from app.services.catalogs.models import (
    CatalogEntry,
    CatalogsFetchError,
    CatalogsValidationError,
    ParseReport,
    parse_entries,
)

logger = get_logger()

APP_KEY = "matrx-local"

# Per the spec: 5s total, 5s connect. Catalog fetch is a background concern —
# it must never hold anything up for long.
_TIMEOUT = httpx.Timeout(5.0, connect=5.0)

# PostgREST paging: Supabase's max-rows setting silently truncates an
# unpaginated response (default 1000), so the primary path pages explicitly
# with Range headers until a short page. The catalog is ~164 rows today;
# anything past this ceiling is a runaway query, not a real catalog.
_PAGE_SIZE = 1000
_MAX_TOTAL_ROWS = 5000


def _unwrap_body(body: Any, source: str) -> list[Any]:
    """Accept a bare array (PostgREST) or an ``{"entries": [...]}`` wrapper
    (aidream). Anything else is unusable."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("entries"), list):
        return body["entries"]
    raise CatalogsValidationError(
        f"{source} returned neither a JSON array nor an entries wrapper "
        f"(got {type(body).__name__})"
    )


async def _fetch_postgrest(report: ParseReport | None = None) -> list[CatalogEntry]:
    """Primary path: anon PostgREST read with the publishable key.

    Explicitly ordered (``kind,key``) and paged with Range headers —
    Supabase's server-side max-rows would otherwise silently truncate a
    single unranged response at 1000 rows. Pages until a short page;
    screams (fetch failure) past ``_MAX_TOTAL_ROWS``.
    """
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/catalog_entries"
    rows: list[Any] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        while True:
            start = len(rows)
            resp = await client.get(
                url,
                params={
                    "app": f"eq.{APP_KEY}",
                    "select": "*",
                    "order": "kind,key",
                },
                headers={
                    "apikey": SUPABASE_PUBLISHABLE_KEY,
                    "Range-Unit": "items",
                    "Range": f"{start}-{start + _PAGE_SIZE - 1}",
                },
            )
            resp.raise_for_status()
            page = _unwrap_body(resp.json(), "PostgREST")
            rows.extend(page)
            if len(rows) > _MAX_TOTAL_ROWS:
                raise CatalogsValidationError(
                    f"PostgREST returned more than {_MAX_TOTAL_ROWS} "
                    f"catalog_entries rows for app={APP_KEY!r} — runaway "
                    "query/filter, refusing the payload"
                )
            if len(page) < _PAGE_SIZE:
                break
    return parse_entries(rows, report=report)


async def _fetch_aidream(report: ParseReport | None = None) -> list[CatalogEntry]:
    """Fallback path: aidream's public catalogs endpoint."""
    url = f"{AIDREAM_SERVER_URL.rstrip('/')}/api/catalogs/{APP_KEY}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return parse_entries(_unwrap_body(resp.json(), "aidream"), report=report)


async def fetch_remote(report: ParseReport | None = None) -> list[CatalogEntry]:
    """Fetch the live catalog rows: PostgREST primary, aidream fallback.

    Raises ``CatalogsFetchError`` (with every per-path reason) when both
    paths fail. Logs which path succeeded so provenance is always visible
    in the engine log.
    """
    failures: list[str] = []

    def _merge(path_report: ParseReport) -> None:
        # Only the WINNING path's skip stats reach the caller — a failed
        # path's partial parse must not pollute the surfaced count.
        if report is not None:
            report.skipped += path_report.skipped
            report.reasons.extend(path_report.reasons)

    try:
        path_report = ParseReport()
        entries = await _fetch_postgrest(path_report)
        _merge(path_report)
        logger.info(
            "[catalogs] fetched %d remote catalog entries via PostgREST (primary)",
            len(entries),
        )
        return entries
    except (httpx.HTTPError, CatalogsValidationError, ValueError) as exc:
        failures.append(f"postgrest: {exc}")
        logger.warning(
            "[catalogs] primary fetch path (PostgREST) failed — trying aidream "
            "fallback: %s",
            exc,
        )

    try:
        path_report = ParseReport()
        entries = await _fetch_aidream(path_report)
        _merge(path_report)
        logger.info(
            "[catalogs] fetched %d remote catalog entries via aidream (fallback)",
            len(entries),
        )
        return entries
    except (httpx.HTTPError, CatalogsValidationError, ValueError) as exc:
        failures.append(f"aidream: {exc}")

    raise CatalogsFetchError(
        "all remote catalog fetch paths failed: " + " | ".join(failures)
    )
