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

    async def fetcher() -> list[CatalogEntry]:
        if fetch_error is not None:
            raise fetch_error
        assert fetch_rows is not None
        return parse_entries(fetch_rows)

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

        async def fetcher() -> list[CatalogEntry]:
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


def test_parse_entries_rejects_bad_row() -> None:
    with pytest.raises(CatalogsValidationError):
        parse_entries([_row(), {"kind": "lora"}])  # second row missing key/payload


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
    async def offline_fetcher() -> list[CatalogEntry]:
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
    _run(svc.refresh_now())
    status = svc.status_payload()
    assert status["tier"] == "remote"
    assert status["entry_count"] == 2
    assert status["fetched_at"] is not None
