"""Unit tests for the Remote Catalogs consumer (app/services/catalogs).

Covers the precedence chain (remote > cache > compiled), the atomic disk
cache round trip, the per-entry min_app_version gate, the kind accessors
(sorting, is_active filtering, unknown-kind loudness), and payload → legacy
dataclass adaptation.

No network, no engine — everything is injected via the CatalogsService
constructor seams (cache_path / app_version / fetcher / compiled).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.catalogs.adapt import entries_to_dataclasses
from app.services.catalogs.models import (
    CatalogEntry,
    CatalogsValidationError,
    ParseReport,
    is_valid_kind,
    parse_entries,
)
from app.services.catalogs.service import (
    CatalogsService,
    _resolve_refresh_interval,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _row(
    kind: str = "lora",
    key: str = "some/repo",
    payload: dict | None = None,
    **overrides,
) -> dict:
    base = {
        "app": "matrx-local",
        "kind": kind,
        "key": key,
        "schema_version": 1,
        "payload": payload or {"repo_id": key, "name": "X"},
        "artifact_url": None,
        "artifact_sha256": None,
        "artifact_size_bytes": None,
        "min_app_version": None,
        "is_active": True,
        "sort_order": 0,
        "notes": None,
        "updated_at": "2026-07-14T00:00:00+00:00",
    }
    base.update(overrides)
    return base


REMOTE_ROWS = [
    _row(key="remote/one", sort_order=10),
    _row(key="remote/two", sort_order=0),
]
CACHED_ROWS = [_row(key="cached/one")]
COMPILED_ENTRIES = [
    CatalogEntry(kind="lora", key="compiled/one", payload={"repo_id": "compiled/one"})
]


def _make_service(
    tmp_path: Path,
    *,
    fetch_rows: list[dict] | None = None,
    fetch_error: Exception | None = None,
    seed_cache_rows: list[dict] | None = None,
    app_version: str = "1.3.112",
) -> CatalogsService:
    cache_path = tmp_path / "catalogs.json"
    if seed_cache_rows is not None:
        cache_path.write_text(
            json.dumps(
                {"entries": seed_cache_rows, "fetched_at": "2026-07-13T00:00:00+00:00"}
            ),
            encoding="utf-8",
        )

    async def fetcher(report: ParseReport | None = None) -> list[CatalogEntry]:
        if fetch_error is not None:
            raise fetch_error
        assert fetch_rows is not None
        return parse_entries(fetch_rows, report=report)

    return CatalogsService(
        cache_path=cache_path,
        app_version=app_version,
        fetcher=fetcher,
        compiled=lambda: list(COMPILED_ENTRIES),
        use_registry=False,
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Precedence chain
# ---------------------------------------------------------------------------


def test_compiled_when_no_cache_and_no_fetch(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_error=ConnectionError("offline"))
    assert svc.resolved.tier == "defaults"
    assert [e.key for e in svc.get_catalog("lora")] == ["compiled/one"]
    # A failed refresh keeps the compiled tier applied — and never raises.
    resolved = _run(svc.refresh_now())
    assert resolved.tier == "defaults"
    assert svc.last_error is not None and "offline" in svc.last_error


def test_cache_beats_compiled(tmp_path: Path) -> None:
    svc = _make_service(
        tmp_path,
        fetch_error=ConnectionError("offline"),
        seed_cache_rows=CACHED_ROWS,
    )
    assert svc.resolved.tier == "cache"
    assert [e.key for e in svc.get_catalog("lora")] == ["cached/one"]


def test_remote_beats_cache(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_rows=REMOTE_ROWS, seed_cache_rows=CACHED_ROWS)
    assert svc.resolved.tier == "cache"  # boot applies cache first
    resolved = _run(svc.refresh_now())
    assert resolved.tier == "remote"
    # sorted by sort_order, not fetch order
    assert [e.key for e in svc.get_catalog("lora")] == ["remote/two", "remote/one"]
    assert svc.last_error is None


def test_invalid_remote_payload_falls_to_cache(tmp_path: Path) -> None:
    svc = _make_service(
        tmp_path,
        fetch_error=CatalogsValidationError("bad payload"),
        seed_cache_rows=CACHED_ROWS,
    )
    resolved = _run(svc.refresh_now())
    assert resolved.tier == "cache"
    assert [e.key for e in svc.get_catalog("lora")] == ["cached/one"]


def test_corrupt_cache_falls_to_compiled(tmp_path: Path) -> None:
    for content in ("{not json", "[]", '"x"', "null"):
        cache_path = tmp_path / "catalogs.json"
        cache_path.write_text(content, encoding="utf-8")

        async def fetcher(report: ParseReport | None = None) -> list[CatalogEntry]:
            raise ConnectionError("offline")

        svc = CatalogsService(
            cache_path=cache_path,
            app_version="1.3.112",
            fetcher=fetcher,
            compiled=lambda: list(COMPILED_ENTRIES),
            use_registry=False,
        )
        assert svc.resolved.tier == "defaults", f"cache content {content!r}"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_entries_rejects_non_list() -> None:
    with pytest.raises(CatalogsValidationError):
        parse_entries({"kind": "lora"})


def test_parse_entries_rejects_empty_list() -> None:
    # Zero rows = broken query/RLS, never a state to apply.
    with pytest.raises(CatalogsValidationError):
        parse_entries([])


def test_parse_entries_rejects_systemic_corruption() -> None:
    # 1 of 2 rows malformed = 50% > the 20% floor → the payload is refused.
    with pytest.raises(CatalogsValidationError, match="systemic-corruption"):
        parse_entries([_row(), {"kind": "lora"}])  # second row missing key/payload


def test_parse_entries_skips_isolated_bad_row() -> None:
    # 1 of 10 rows malformed = 10% < the 20% floor → skip it, keep the rest.
    rows = [_row(key=f"ok/{i}") for i in range(9)] + [{"kind": "lora"}]
    report = ParseReport()
    entries = parse_entries(rows, report=report)
    assert [e.key for e in entries] == [f"ok/{i}" for i in range(9)]
    assert report.skipped == 1
    assert "failed validation" in report.reasons[0]


def test_parse_entries_rejects_all_rows_malformed() -> None:
    with pytest.raises(CatalogsValidationError, match="NO parseable rows"):
        parse_entries([{"kind": "lora"}, "not-a-dict"])


def test_parse_entries_ignores_unknown_columns() -> None:
    entries = parse_entries([_row(future_column="ignored")])
    assert entries[0].key == "some/repo"
    assert not hasattr(entries[0], "future_column")


# ---------------------------------------------------------------------------
# Kind accessors
# ---------------------------------------------------------------------------


def test_get_catalog_unknown_kind_is_loud(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_error=ConnectionError("offline"))
    with pytest.raises(ValueError, match="unknown catalog kind"):
        svc.get_catalog("not_a_kind")
    assert is_valid_kind("lora") is True
    assert is_valid_kind("not_a_kind") is False
    assert is_valid_kind("lora; drop table") is False


def test_inactive_entries_are_filtered(tmp_path: Path) -> None:
    rows = [_row(key="active/one"), _row(key="inactive/one", is_active=False)]
    svc = _make_service(tmp_path, fetch_rows=rows)
    _run(svc.refresh_now())
    assert [e.key for e in svc.get_catalog("lora")] == ["active/one"]


def test_get_catalog_entry(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_rows=REMOTE_ROWS)
    _run(svc.refresh_now())
    entry = svc.get_catalog_entry("lora", "remote/one")
    assert entry is not None and entry.sort_order == 10
    assert svc.get_catalog_entry("lora", "nope") is None


# ---------------------------------------------------------------------------
# Version gate
# ---------------------------------------------------------------------------


def test_min_app_version_filters_entries(tmp_path: Path) -> None:
    rows = [
        _row(key="old/ok", min_app_version=None),
        _row(key="new/ok", min_app_version="1.3.0"),
        _row(key="too/new", min_app_version="99.0.0"),
    ]
    svc = _make_service(tmp_path, fetch_rows=rows, app_version="1.3.112")
    _run(svc.refresh_now())
    # equal sort_order ties break alphabetically by key
    assert [e.key for e in svc.get_catalog("lora")] == ["new/ok", "old/ok"]


def test_version_probe_fallback_disables_gate(tmp_path: Path) -> None:
    """app_version '0.0.0' = broken version probe → cannot gate, gate open."""
    rows = [_row(key="gated/entry", min_app_version="1.0.0")]
    svc = _make_service(tmp_path, fetch_rows=rows, app_version="0.0.0")
    _run(svc.refresh_now())
    assert [e.key for e in svc.get_catalog("lora")] == ["gated/entry"]


# ---------------------------------------------------------------------------
# Adaptation (payload → legacy dataclass)
# ---------------------------------------------------------------------------


def test_adaptation_skips_malformed_entries_and_drops_extras(tmp_path: Path) -> None:
    from app.services.ner.models import NerModelSpec

    good_payload = {
        "model_id": "m1",
        "repo_id": "org/m1",
        "display_name": "M1",
        "backend": "gliner",
        "tier": "base",
        "description": "d",
        "license": "apache-2.0",
        "estimated_disk_mb": 100,
        "estimated_ram_mb": [512, 1024],
        "default": True,
        "hardware_gate": None,
        "future_payload_key": "dropped",  # unknown → filtered out
    }
    bad_payload = {"model_id": "m2"}  # missing required fields
    entries = parse_entries(
        [
            _row(kind="ner_model", key="m1", payload=good_payload),
            _row(kind="ner_model", key="m2", payload=bad_payload),
        ]
    )

    def _ram_tuple(p: dict) -> dict:
        p = dict(p)
        if isinstance(p.get("estimated_ram_mb"), list):
            p["estimated_ram_mb"] = tuple(p["estimated_ram_mb"])
        return p

    adapted = entries_to_dataclasses(entries, NerModelSpec, transform=_ram_tuple)
    assert len(adapted) == 1
    assert adapted[0].model_id == "m1"
    assert adapted[0].estimated_ram_mb == (512, 1024)


# ---------------------------------------------------------------------------
# Refresh interval knob
# ---------------------------------------------------------------------------


def test_refresh_interval_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATRX_CATALOGS_REFRESH_INTERVAL", "banana")
    assert _resolve_refresh_interval() == 21600
    monkeypatch.setenv("MATRX_CATALOGS_REFRESH_INTERVAL", "0")
    assert _resolve_refresh_interval() == 60
    monkeypatch.setenv("MATRX_CATALOGS_REFRESH_INTERVAL", "300")
    assert _resolve_refresh_interval() == 300


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def test_cache_write_read_round_trip(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_rows=REMOTE_ROWS)
    _run(svc.refresh_now())

    cache_path = tmp_path / "catalogs.json"
    assert cache_path.exists()
    on_disk = json.loads(cache_path.read_text(encoding="utf-8"))
    assert {e["key"] for e in on_disk["entries"]} == {"remote/one", "remote/two"}
    assert on_disk["fetched_at"]
    # No stray temp files left behind by the atomic write.
    assert [p.name for p in tmp_path.iterdir()] == ["catalogs.json"]

    # A fresh service boots straight from that cache (tier=cache).
    async def offline_fetcher(
        report: ParseReport | None = None,
    ) -> list[CatalogEntry]:
        raise ConnectionError("offline")

    svc2 = CatalogsService(
        cache_path=cache_path,
        app_version="1.3.112",
        fetcher=offline_fetcher,
        compiled=lambda: list(COMPILED_ENTRIES),
        use_registry=False,
    )
    assert svc2.resolved.tier == "cache"
    assert {e.key for e in svc2.get_catalog("lora")} == {"remote/one", "remote/two"}
    assert isinstance(svc2.resolved.fetched_at, datetime)
    assert svc2.resolved.fetched_at.tzinfo is not None
    assert svc2.resolved.fetched_at <= datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Status payload provenance
# ---------------------------------------------------------------------------


def test_status_payload(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_rows=REMOTE_ROWS)
    status = svc.status_payload()
    assert status["tier"] == "defaults"
    assert status["kinds"] == {"lora": 1}
    assert status["skipped_entries"] == 0
    assert status["kind_floors_active"] == []
    _run(svc.refresh_now())
    status = svc.status_payload()
    assert status["tier"] == "remote"
    assert status["entry_count"] == 2
    assert status["fetched_at"] is not None


def test_status_payload_surfaces_skipped_rows(tmp_path: Path) -> None:
    # 1 malformed row of 10: applied (isolation) but visible in the status.
    rows = [_row(key=f"ok/{i}") for i in range(9)] + [{"kind": "lora"}]
    svc = _make_service(tmp_path, fetch_rows=rows)
    _run(svc.refresh_now())
    status = svc.status_payload()
    assert status["tier"] == "remote"
    assert status["entry_count"] == 9
    assert status["skipped_entries"] == 1


# ---------------------------------------------------------------------------
# Per-kind emptiness floor (generalized tts_model_file precedent)
# ---------------------------------------------------------------------------


def test_kind_floor_applies_when_remote_empties_a_compiled_kind(
    tmp_path: Path,
) -> None:
    # Remote has ONLY a system_prompt row → the lora kind (non-empty in the
    # compiled tier) resolves to zero from the remote tier → compiled floor.
    rows = [_row(kind="system_prompt", key="sp", payload={"id": "sp"})]
    svc = _make_service(tmp_path, fetch_rows=rows)
    _run(svc.refresh_now())
    assert svc.resolved.tier == "remote"
    assert [e.key for e in svc.get_catalog("lora")] == ["compiled/one"]
    assert svc.status_payload()["kind_floors_active"] == ["lora"]
    # Kinds empty in BOTH tiers stay empty — no phantom floor.
    assert svc.get_catalog("tts_voice") == []
    assert svc.status_payload()["kind_floors_active"] == ["lora"]


def test_kind_floor_lifts_when_kind_recovers(tmp_path: Path) -> None:
    rows = [_row(kind="system_prompt", key="sp", payload={"id": "sp"})]
    svc = _make_service(tmp_path, fetch_rows=rows)
    _run(svc.refresh_now())
    assert [e.key for e in svc.get_catalog("lora")] == ["compiled/one"]
    assert svc.status_payload()["kind_floors_active"] == ["lora"]

    svc2 = _make_service(tmp_path, fetch_rows=REMOTE_ROWS)
    _run(svc2.refresh_now())
    assert [e.key for e in svc2.get_catalog("lora")] == ["remote/two", "remote/one"]
    assert svc2.status_payload()["kind_floors_active"] == []

    # Same-instance recovery: the kind coming back lifts the floor.
    svc._resolved = svc2.resolved
    assert [e.key for e in svc.get_catalog("lora")] == ["remote/two", "remote/one"]
    assert svc.status_payload()["kind_floors_active"] == []


def test_kind_floor_never_fires_on_defaults_tier(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_error=ConnectionError("offline"))
    assert svc.resolved.tier == "defaults"
    assert [e.key for e in svc.get_catalog("lora")] == ["compiled/one"]
    assert svc.status_payload()["kind_floors_active"] == []


# ---------------------------------------------------------------------------
# PostgREST pagination (client.py)
# ---------------------------------------------------------------------------


def _paged_handler(total_rows: int, seen_ranges: list[str]):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert "order=kind%2Ckey" in str(request.url) or "order=kind,key" in str(
            request.url
        ), f"missing explicit order: {request.url}"
        rng = request.headers["Range"]
        seen_ranges.append(rng)
        start, end = (int(x) for x in rng.split("-"))
        rows = [
            _row(key=f"page/{i}", sort_order=i)
            for i in range(start, min(end + 1, total_rows))
        ]
        return httpx.Response(200, json=rows)

    return handler


def test_postgrest_fetch_pages_past_the_1000_row_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from app.services.catalogs import client as client_mod

    seen: list[str] = []
    transport = httpx.MockTransport(_paged_handler(1500, seen))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)
    )

    entries = _run(client_mod._fetch_postgrest())
    assert len(entries) == 1500
    assert seen == ["0-999", "1000-1999"]


def test_postgrest_fetch_screams_past_the_sanity_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from app.services.catalogs import client as client_mod

    seen: list[str] = []
    transport = httpx.MockTransport(_paged_handler(10_000, seen))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)
    )

    with pytest.raises(CatalogsValidationError, match="more than 5000"):
        _run(client_mod._fetch_postgrest())
