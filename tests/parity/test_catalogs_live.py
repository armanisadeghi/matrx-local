"""Release validation: the LIVE catalog_entries rows are fetchable and sane.

Per the cross-repo spec (common-docs/systems/clients/remote-catalogs/FEATURE.md), the
matrx-local release pipeline must verify:

  (a) the live `matrx-local` catalog is fetchable through the PostgREST
      primary path and parses against the CatalogEntry schema;
  (b) every kind the compiled fallback ships is NON-EMPTY in the live
      catalog — a kind that exists in code but not in the DB means the
      remote tier silently loses data the fallback still has;
  (c) the compiled fallback itself adapts cleanly and covers every kind.

The aidream fallback endpoint ships separately — a 404 there skips that
assertion with a loud message instead of blocking this repo's release. Any
other aidream failure (connection refused, 5xx, invalid payload) FAILS: a
dead fallback is a real release blocker.

NETWORK TEST — wired into scripts/check.sh Step 1 alongside the other parity
steps (which sets MATRX_LIVE_CHECKS=1 for this invocation); requires outbound
HTTPS. A plain offline ``pytest tests/`` skips this module entirely.
"""

from __future__ import annotations

import os
from collections import Counter

import httpx
import pytest

from app.services.catalogs.client import APP_KEY, _fetch_aidream, _fetch_postgrest
from app.services.catalogs.compiled import compiled_catalog_entries
from app.services.catalogs.models import KNOWN_KINDS, CatalogEntry

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


def _assert_entries_sane(entries: list[CatalogEntry], source: str) -> None:
    assert entries, f"{source}: zero catalog entries"
    compiled_kinds = {e.kind for e in compiled_catalog_entries()}
    live_counts = Counter(e.kind for e in entries if e.is_active)
    for kind in sorted(compiled_kinds):
        assert live_counts.get(kind, 0) > 0, (
            f"{source}: kind {kind!r} exists in the compiled fallback but has "
            "ZERO active rows in the live catalog — the remote tier would "
            "silently lose that catalog"
        )
    unknown = set(live_counts) - KNOWN_KINDS
    assert not unknown, (
        f"{source}: live catalog carries kinds this build does not know: "
        f"{sorted(unknown)} — add them to KNOWN_KINDS (and a consumer) or "
        "fix the rows"
    )


def test_live_postgrest_catalog_fetch_and_kind_coverage() -> None:
    import asyncio

    entries = asyncio.run(_fetch_postgrest())
    _assert_entries_sane(entries, "PostgREST (primary)")


def test_live_aidream_fallback_catalog_fetch() -> None:
    import asyncio

    try:
        entries = asyncio.run(_fetch_aidream())
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            pytest.skip(
                "aidream GET /api/catalogs/matrx-local not deployed yet (404) — "
                "ships separately; re-run once the aidream side lands"
            )
        raise
    _assert_entries_sane(entries, f"aidream fallback ({APP_KEY})")


def test_compiled_fallback_adapts_cleanly_and_covers_every_kind() -> None:
    entries = compiled_catalog_entries()
    kinds = {e.kind for e in entries}
    assert kinds == KNOWN_KINDS, (
        f"compiled fallback kind coverage drifted: missing "
        f"{sorted(KNOWN_KINDS - kinds)}, extra {sorted(kinds - KNOWN_KINDS)}"
    )
    keys = Counter((e.kind, e.key) for e in entries)
    dupes = [k for k, n in keys.items() if n > 1]
    assert not dupes, f"duplicate compiled (kind, key) pairs: {dupes}"
