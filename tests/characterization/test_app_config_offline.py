"""Characterization: total network failure still yields a working app config.

Pins spec acceptance criterion 1 (common-docs/systems/app-config/FEATURE.md): a fresh
install with NO network and NO disk cache boots on compiled defaults, every
accessor answers, refresh attempts degrade loudly but never raise, and the
version gate stays open.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.app_config.models import AppConfigRow
from app.services.app_config.service import AppConfigService

from app.config import (
    AIDREAM_SERVER_URL_DEFAULT,
    MATRX_FILES_URL_DEFAULT,
    SCRAPER_SERVER_URL_DEFAULT,
    WEB_APP_ORIGIN_DEFAULT,
)


def _offline_service(tmp_path: Path) -> AppConfigService:
    async def fetcher() -> AppConfigRow:
        raise ConnectionError("simulated total network failure")

    return AppConfigService(
        cache_path=tmp_path / "app_config.json",  # does not exist — fresh install
        app_version="1.3.112",
        fetcher=fetcher,
        env_overrides={},
        use_registry=False,
    )


def test_total_network_failure_boots_on_compiled_defaults(tmp_path: Path) -> None:
    svc = _offline_service(tmp_path)

    # Boot resolution is synchronous and offline: compiled defaults applied.
    resolved = svc.resolved
    assert resolved.tier == "defaults"
    assert resolved.fetched_at is None
    assert resolved.update_required is False

    # Every accessor answers with the shipping defaults from app/config.py.
    assert svc.get_aidream_server_url() == AIDREAM_SERVER_URL_DEFAULT
    assert svc.get_matrx_files_url() == MATRX_FILES_URL_DEFAULT
    assert svc.get_scraper_server_url() == SCRAPER_SERVER_URL_DEFAULT
    assert svc.get_web_app_origin() == WEB_APP_ORIGIN_DEFAULT
    assert svc.get_flag("anything") is False
    assert svc.get_notice() is None

    # A refresh attempt fails LOUDLY-but-gracefully: no exception, tier
    # unchanged, reason captured.
    after = asyncio.run(svc.refresh_now())
    assert after.tier == "defaults"
    assert svc.last_error is not None
    assert "simulated total network failure" in svc.last_error

    # No cache file was written for the failed fetch.
    assert not (tmp_path / "app_config.json").exists()

    # The status surface tells the truth.
    status = svc.status_payload()
    assert status == {
        "tier": "defaults",
        "fetched_at": None,
        "update_required": False,
        "notice": None,
        "env_overrides": [],
    }
