"""Remote fetch paths for the App Config row.

Two independent paths, tried in order (see the cross-repo spec):

  1. Primary — Supabase PostgREST
     ``GET {SUPABASE_URL}/rest/v1/app_config?app=eq.matrx-local`` with the
     publishable key only (anon read via RLS; MUST work pre-login, so no JWT).
  2. Fallback — aidream
     ``GET {AIDREAM_SERVER_URL}/api/app-config/matrx-local`` (public,
     unauthenticated, server-cached). Protects against Supabase-side outages.

Both return the identical row (PostgREST wraps it in a one-element array).
A payload that fails schema validation counts as a failure of THAT path —
the other path is still tried. If every path fails, ``AppConfigFetchError``
carries all per-path reasons.

Bootstrap invariant: the URLs used HERE (Supabase URL + publishable key,
aidream fallback URL) are compiled-in bootstrap constants and are NEVER
remotely configurable — a hostile config row must not be able to repoint
where the app fetches its config from.
"""

from __future__ import annotations

import httpx

# app.common before app.config — see the import-cycle note in service.py.
from app.common.system_logger import get_logger

from app.config import (
    AIDREAM_SERVER_URL,
    SUPABASE_PUBLISHABLE_KEY,
    SUPABASE_URL,
)
from app.services.app_config.models import (
    AppConfigFetchError,
    AppConfigRow,
    AppConfigValidationError,
    parse_row,
)

logger = get_logger()

APP_KEY = "matrx-local"

# Per the spec: 5s total, 5s connect. Config fetch is a background concern —
# it must never hold anything up for long.
_TIMEOUT = httpx.Timeout(5.0, connect=5.0)


async def _fetch_postgrest() -> AppConfigRow:
    """Primary path: anon PostgREST read with the publishable key."""
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_config"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            url,
            params={"app": f"eq.{APP_KEY}"},
            headers={"apikey": SUPABASE_PUBLISHABLE_KEY},
        )
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, list) or not body:
        raise AppConfigValidationError(
            f"PostgREST returned no app_config row for app={APP_KEY!r} "
            f"(got {body!r:.200})"
        )
    return parse_row(body[0])


async def _fetch_aidream() -> AppConfigRow:
    """Fallback path: aidream's public app-config endpoint."""
    url = f"{AIDREAM_SERVER_URL.rstrip('/')}/api/app-config/{APP_KEY}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return parse_row(resp.json())


async def fetch_remote() -> AppConfigRow:
    """Fetch the live row: PostgREST primary, aidream fallback.

    Raises ``AppConfigFetchError`` (with every per-path reason) when both
    paths fail. Logs which path succeeded so provenance is always visible
    in the engine log.
    """
    failures: list[str] = []

    try:
        row = await _fetch_postgrest()
        logger.info("[app_config] fetched remote config via PostgREST (primary)")
        return row
    except (httpx.HTTPError, AppConfigValidationError, ValueError) as exc:
        failures.append(f"postgrest: {exc}")
        logger.warning(
            "[app_config] primary fetch path (PostgREST) failed — trying aidream "
            "fallback: %s",
            exc,
        )

    try:
        row = await _fetch_aidream()
        logger.info("[app_config] fetched remote config via aidream (fallback)")
        return row
    except (httpx.HTTPError, AppConfigValidationError, ValueError) as exc:
        failures.append(f"aidream: {exc}")

    raise AppConfigFetchError(
        "all remote app_config fetch paths failed: " + " | ".join(failures)
    )
