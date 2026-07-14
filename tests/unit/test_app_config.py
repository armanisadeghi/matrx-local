"""Unit tests for the remote App Config consumer (app/services/app_config).

Covers the precedence chain (env override > remote > cache > defaults),
tolerant parsing (unknown keys ignored, bad types rejected → next tier),
the semver version gate, and the atomic disk-cache round trip.

No network, no engine — everything is injected via the AppConfigService
constructor seams (cache_path / app_version / fetcher / env_overrides).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.app_config.models import (
    AppConfigRow,
    AppConfigValidationError,
    parse_row,
    semver_tuple,
    version_below,
)
from app.services.app_config.service import AppConfigService

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

REMOTE_ROW = {
    "app": "matrx-local",
    "schema_version": 1,
    "min_supported_app_version": "1.0.0",
    "config": {
        "aidream_server_url": "https://remote.example.com",
        "matrx_files_url": "https://remote-files.example.com",
        "scraper_server_url": "https://remote-scraper.example.com",
        "flags": {"desktop_beta": True},
        "notice": None,
    },
    "updated_at": "2026-07-14T00:00:00+00:00",
}

CACHED_ROW = {
    "app": "matrx-local",
    "schema_version": 1,
    "min_supported_app_version": "1.0.0",
    "config": {
        "aidream_server_url": "https://cached.example.com",
        "matrx_files_url": "https://cached-files.example.com",
        "scraper_server_url": "https://cached-scraper.example.com",
        "flags": {},
        "notice": None,
    },
    "updated_at": "2026-07-13T00:00:00+00:00",
}


def _make_service(
    tmp_path: Path,
    *,
    fetch_result: dict | None = None,
    fetch_error: Exception | None = None,
    env_overrides: dict[str, str] | None = None,
    seed_cache: dict | None = None,
) -> AppConfigService:
    cache_path = tmp_path / "app_config.json"
    if seed_cache is not None:
        cache_path.write_text(
            json.dumps(
                {"row": seed_cache, "fetched_at": "2026-07-13T00:00:00+00:00"}
            ),
            encoding="utf-8",
        )

    async def fetcher() -> AppConfigRow:
        if fetch_error is not None:
            raise fetch_error
        assert fetch_result is not None
        return parse_row(fetch_result)

    return AppConfigService(
        cache_path=cache_path,
        app_version="1.3.112",
        fetcher=fetcher,
        env_overrides=env_overrides or {},
        use_registry=False,
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Precedence chain
# ---------------------------------------------------------------------------


def test_defaults_when_no_cache_and_no_fetch(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_error=ConnectionError("offline"))
    assert svc.resolved.tier == "defaults"
    assert svc.get_aidream_server_url() == "https://server.app.matrxserver.com"
    assert svc.get_matrx_files_url() == "https://files.matrxserver.com"
    assert svc.get_scraper_server_url() == "https://scraper.app.matrxserver.com"


def test_cache_beats_defaults(tmp_path: Path) -> None:
    svc = _make_service(
        tmp_path,
        fetch_error=ConnectionError("offline"),
        seed_cache=CACHED_ROW,
    )
    assert svc.resolved.tier == "cache"
    assert svc.get_aidream_server_url() == "https://cached.example.com"
    # A failed refresh keeps the cache tier applied — and never raises.
    resolved = _run(svc.refresh_now())
    assert resolved.tier == "cache"
    assert svc.last_error is not None and "offline" in svc.last_error


def test_remote_beats_cache(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_result=REMOTE_ROW, seed_cache=CACHED_ROW)
    assert svc.resolved.tier == "cache"  # boot applies cache first
    resolved = _run(svc.refresh_now())
    assert resolved.tier == "remote"
    assert svc.get_aidream_server_url() == "https://remote.example.com"
    assert svc.get_flag("desktop_beta") is True
    assert svc.get_flag("nonexistent") is False
    assert svc.last_error is None


def test_env_override_wins_over_remote(tmp_path: Path) -> None:
    svc = _make_service(
        tmp_path,
        fetch_result=REMOTE_ROW,
        env_overrides={"aidream_server_url": "http://localhost:8000"},
    )
    _run(svc.refresh_now())
    assert svc.resolved.tier == "remote"
    # Overridden key: env wins. Non-overridden keys: remote wins.
    assert svc.get_aidream_server_url() == "http://localhost:8000"
    assert svc.get_matrx_files_url() == "https://remote-files.example.com"


# ---------------------------------------------------------------------------
# Tolerant parsing
# ---------------------------------------------------------------------------


def test_unknown_keys_are_ignored() -> None:
    row = dict(REMOTE_ROW)
    row["config"] = {**REMOTE_ROW["config"], "future_key": {"nested": 1}}
    row["future_top_level"] = "ignored"
    parsed = parse_row(row)
    assert parsed.config.aidream_server_url == "https://remote.example.com"
    assert not hasattr(parsed.config, "future_key")


def test_bad_types_reject_the_payload() -> None:
    row = dict(REMOTE_ROW)
    row["config"] = {**REMOTE_ROW["config"], "flags": {"x": "yes"}}
    with pytest.raises(AppConfigValidationError):
        parse_row(row)


def test_non_https_url_rejected() -> None:
    row = dict(REMOTE_ROW)
    row["config"] = {
        **REMOTE_ROW["config"],
        "aidream_server_url": "http://evil.example.com",
    }
    with pytest.raises(AppConfigValidationError):
        parse_row(row)


def test_loopback_http_allowed_for_dev_rows() -> None:
    row = dict(REMOTE_ROW)
    row["config"] = {
        **REMOTE_ROW["config"],
        "aidream_server_url": "http://localhost:8000",
    }
    assert parse_row(row).config.aidream_server_url == "http://localhost:8000"


def test_non_object_row_rejected() -> None:
    with pytest.raises(AppConfigValidationError):
        parse_row(["not", "a", "row"])


def test_invalid_remote_payload_falls_to_next_tier(tmp_path: Path) -> None:
    svc = _make_service(
        tmp_path,
        fetch_error=AppConfigValidationError("bad payload"),
        seed_cache=CACHED_ROW,
    )
    resolved = _run(svc.refresh_now())
    assert resolved.tier == "cache"
    assert svc.get_aidream_server_url() == "https://cached.example.com"


def test_corrupt_cache_falls_to_defaults(tmp_path: Path) -> None:
    cache_path = tmp_path / "app_config.json"
    cache_path.write_text("{not json", encoding="utf-8")

    async def fetcher() -> AppConfigRow:
        raise ConnectionError("offline")

    svc = AppConfigService(
        cache_path=cache_path,
        app_version="1.3.112",
        fetcher=fetcher,
        env_overrides={},
        use_registry=False,
    )
    assert svc.resolved.tier == "defaults"


# ---------------------------------------------------------------------------
# Version gate
# ---------------------------------------------------------------------------


def test_semver_tuple_parsing() -> None:
    assert semver_tuple("1.3.112") == (1, 3, 112)
    assert semver_tuple("v2.0") == (2, 0)
    assert semver_tuple("1.4.0-rc.1") == (1, 4, 0)
    assert semver_tuple("garbage") is None
    assert semver_tuple("") is None


def test_version_below_comparisons() -> None:
    assert version_below("1.3.112", "1.4.0") is True
    assert version_below("1.4.0", "1.4.0") is False
    assert version_below("1.4.1", "1.4.0") is False
    assert version_below("1.3", "1.3.0") is False  # padded equal
    assert version_below("1.3", "1.3.1") is True
    # Unparseable inputs never lock a user out.
    assert version_below("garbage", "1.0.0") is False
    assert version_below("1.0.0", "garbage") is False


def test_update_required_state(tmp_path: Path) -> None:
    row = dict(REMOTE_ROW)
    row["min_supported_app_version"] = "99.0.0"
    svc = _make_service(tmp_path, fetch_result=row)
    resolved = _run(svc.refresh_now())
    assert resolved.update_required is True
    assert svc.status_payload()["update_required"] is True


def test_update_not_required_when_current(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_result=REMOTE_ROW)  # min 1.0.0 vs 1.3.112
    resolved = _run(svc.refresh_now())
    assert resolved.update_required is False


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def test_cache_write_read_round_trip(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, fetch_result=REMOTE_ROW)
    _run(svc.refresh_now())

    cache_path = tmp_path / "app_config.json"
    assert cache_path.exists()
    on_disk = json.loads(cache_path.read_text(encoding="utf-8"))
    assert on_disk["row"]["config"]["aidream_server_url"] == "https://remote.example.com"
    assert on_disk["fetched_at"]
    # No stray temp files left behind by the atomic write.
    assert [p.name for p in tmp_path.iterdir()] == ["app_config.json"]

    # A fresh service boots straight from that cache (tier=cache).
    async def offline_fetcher() -> AppConfigRow:
        raise ConnectionError("offline")

    svc2 = AppConfigService(
        cache_path=cache_path,
        app_version="1.3.112",
        fetcher=offline_fetcher,
        env_overrides={},
        use_registry=False,
    )
    assert svc2.resolved.tier == "cache"
    assert svc2.get_aidream_server_url() == "https://remote.example.com"
    assert isinstance(svc2.resolved.fetched_at, datetime)
    assert svc2.resolved.fetched_at.tzinfo is not None
    assert svc2.resolved.fetched_at <= datetime.now(timezone.utc)
