"""Release validation: the LIVE app_config row is fetchable and schema-valid.

Per the cross-repo spec (common-docs/systems/platform/app-config/FEATURE.md §Release
validation), the matrx-local release pipeline must verify:

  (a) the live `matrx-local` row is fetchable through BOTH fetch paths and
      parses against the Pydantic schema — a schema drift between code and
      DB can never ship;
  (b) the compiled defaults in app/config.py parse against the same schema.

The PostgREST (primary) path MUST pass. The aidream fallback endpoint ships
separately — a 404 there skips that assertion with a loud message instead of
blocking this repo's release. Any other aidream failure (connection refused,
5xx, invalid payload) FAILS: a dead fallback is a real release blocker.

NETWORK TEST — wired into scripts/check.sh Step 1 alongside the other parity
steps (which sets MATRX_LIVE_CHECKS=1 for this invocation); requires outbound
HTTPS. A plain offline ``pytest tests/`` skips this module entirely.
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.services.app_config.client import APP_KEY
from app.services.app_config.models import AppConfigRow, parse_row
from app.services.app_config.service import _COMPILED_DEFAULTS_ROW

from app.config import (
    AIDREAM_SERVER_URL_DEFAULT,
    SUPABASE_PUBLISHABLE_KEY,
    SUPABASE_URL,
)

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("MATRX_LIVE_CHECKS") != "1",
        reason=(
            "live-network release validation — set MATRX_LIVE_CHECKS=1 to run "
            "(scripts/check.sh does; plain offline pytest must not hard-fail)"
        ),
    ),
]

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _assert_row_shape(row: AppConfigRow, source: str) -> None:
    assert row.app == APP_KEY, f"{source}: row is for app={row.app!r}, not {APP_KEY!r}"
    assert row.schema_version >= 1, f"{source}: schema_version must be >= 1"
    assert row.min_supported_app_version, f"{source}: min_supported_app_version empty"
    assert row.config.aidream_server_url.startswith("https://"), source
    assert row.config.matrx_files_url.startswith("https://"), source
    assert row.config.scraper_server_url.startswith("https://"), source


def test_live_row_via_postgrest_primary() -> None:
    """Primary fetch path (anon PostgREST read) must return a valid row."""
    resp = httpx.get(
        f"{SUPABASE_URL.rstrip('/')}/rest/v1/app_config",
        params={"app": f"eq.{APP_KEY}"},
        headers={"apikey": SUPABASE_PUBLISHABLE_KEY},
        timeout=_TIMEOUT,
    )
    assert resp.status_code == 200, (
        f"PostgREST app_config read failed: HTTP {resp.status_code} — "
        f"{resp.text[:300]}"
    )
    body = resp.json()
    assert isinstance(body, list) and body, (
        f"No live app_config row for app={APP_KEY!r} — seed the row before "
        f"releasing (see common-docs/systems/platform/app-config/FEATURE.md)."
    )
    row = parse_row(body[0])
    _assert_row_shape(row, "postgrest")


def test_live_row_via_aidream_fallback() -> None:
    """Fallback fetch path (aidream public endpoint) must return the same row.

    Skips ONLY on 404 — that endpoint deploys from the aidream repo and may
    lag this repo's release. Everything else is a hard failure.
    """
    url = f"{AIDREAM_SERVER_URL_DEFAULT.rstrip('/')}/api/app-config/{APP_KEY}"
    resp = httpx.get(url, timeout=_TIMEOUT)
    if resp.status_code == 404:
        pytest.skip(
            f"aidream fallback endpoint not deployed yet (404 from {url}) — "
            "it ships from the aidream repo; the PostgREST primary path above "
            "is the gating check until then."
        )
    assert resp.status_code == 200, (
        f"aidream app-config fallback failed: HTTP {resp.status_code} — "
        f"{resp.text[:300]}"
    )
    row = parse_row(resp.json())
    _assert_row_shape(row, "aidream")


def test_compiled_defaults_parse_against_schema() -> None:
    """The compiled-in last-resort payload must satisfy AppConfigV1 forever."""
    row = parse_row(_COMPILED_DEFAULTS_ROW)
    _assert_row_shape(row, "compiled defaults")
