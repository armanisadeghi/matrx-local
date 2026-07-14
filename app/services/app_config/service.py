"""App Config service — resolves and serves the process-wide runtime config.

Owns the precedence chain (env override > fresh remote > disk cache >
compiled defaults), the atomic disk cache under ``MATRX_HOME_DIR``, the 6h
background refresh loop, and the launcher-registry reporting. See
``app/services/app_config/FEATURE.md`` and the cross-repo spec
(/Users/armanisadeghi/code/common-docs/app-config/FEATURE.md).

Startup contract: ``get_app_config_service()`` resolves cache/defaults
SYNCHRONOUSLY (one small file read) — it never touches the network, so the
first accessor call is always instant and offline-safe. The network fetch
runs only in the background task started from the lifespan.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

# app.common MUST be imported before app.config anywhere config might load
# first (pre-existing config↔common cycle: app/config.py imports
# app.common.platform_ctx while app/common/__init__.py imports app.config).
from app.common.system_logger import get_logger

from app.config import (
    AIDREAM_SERVER_URL,
    AIDREAM_SERVER_URL_DEFAULT,
    MATRX_FILES_URL,
    MATRX_FILES_URL_DEFAULT,
    MATRX_HOME_DIR,
    SCRAPER_SERVER_URL,
    SCRAPER_SERVER_URL_DEFAULT,
)
from app.launcher import get_registry
from app.services.app_config.client import APP_KEY, fetch_remote
from app.services.app_config.models import (
    AppConfigError,
    AppConfigNotice,
    AppConfigRow,
    ResolvedAppConfig,
    Tier,
    parse_row,
    version_below,
)

logger = get_logger()

SERVICE_NAME = "app_config"
CACHE_FILENAME = "app_config.json"

# 6h default; env var is a developer-only knob (dev worlds / tests).
_REFRESH_INTERVAL_DEFAULT_S = 21600
_REFRESH_INTERVAL_MIN_S = 60


def _resolve_refresh_interval() -> int:
    """Parse the developer refresh-interval knob defensively.

    A garbage value must not crash module import, and a tiny/zero value must
    not hot-loop against Supabase — clamp to a 60s floor, loudly.
    """
    raw = os.getenv("MATRX_APP_CONFIG_REFRESH_INTERVAL", str(_REFRESH_INTERVAL_DEFAULT_S))
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "[app_config] MATRX_APP_CONFIG_REFRESH_INTERVAL=%r is not an "
            "integer — falling back to default %ss",
            raw,
            _REFRESH_INTERVAL_DEFAULT_S,
        )
        return _REFRESH_INTERVAL_DEFAULT_S
    if value < _REFRESH_INTERVAL_MIN_S:
        logger.warning(
            "[app_config] MATRX_APP_CONFIG_REFRESH_INTERVAL=%s is below the "
            "%ss floor (a 0 would hot-loop against Supabase) — clamping to %ss",
            value,
            _REFRESH_INTERVAL_MIN_S,
            _REFRESH_INTERVAL_MIN_S,
        )
        return _REFRESH_INTERVAL_MIN_S
    return value


DEFAULT_REFRESH_INTERVAL_S = _resolve_refresh_interval()

# The compiled-in last-resort payload — MUST mirror the shipping defaults in
# app/config.py (pinned by tests/parity/test_app_config_live.py).
_COMPILED_DEFAULTS_ROW: dict = {
    "app": APP_KEY,
    "schema_version": 1,
    "min_supported_app_version": "0.0.0",
    "config": {
        "aidream_server_url": AIDREAM_SERVER_URL_DEFAULT,
        "matrx_files_url": MATRX_FILES_URL_DEFAULT,
        "scraper_server_url": SCRAPER_SERVER_URL_DEFAULT,
        "flags": {},
        "notice": None,
    },
    "updated_at": None,
}

# Payload key → (effective config.py constant, compiled default). An env var
# override is ACTIVE when the constant differs from the compiled default —
# that value wins for that key only (precedence tier 1).
_ENV_OVERRIDE_SOURCES: dict[str, tuple[str, str]] = {
    "aidream_server_url": (AIDREAM_SERVER_URL, AIDREAM_SERVER_URL_DEFAULT),
    "matrx_files_url": (MATRX_FILES_URL, MATRX_FILES_URL_DEFAULT),
    "scraper_server_url": (SCRAPER_SERVER_URL, SCRAPER_SERVER_URL_DEFAULT),
}


def _detect_env_overrides() -> dict[str, str]:
    # WHY value-vs-default and not presence-based: packaged builds inject
    # AIDREAM_SERVER_URL_LIVE=<compiled default> into the environment via
    # hooks/runtime_hook.py → bundled_config.apply(), so "is the env var set?"
    # would flag a permanent override in every production install and kill
    # remote config for that key forever. Only a value that actually DIFFERS
    # from the compiled default is a real developer override.
    overrides: dict[str, str] = {}
    for key, (effective, default) in _ENV_OVERRIDE_SOURCES.items():
        if effective and effective.rstrip("/") != default.rstrip("/"):
            overrides[key] = effective.rstrip("/")
    return overrides


def _resolve_app_version() -> str:
    """The running app's version — lazily imported to avoid a routes cycle."""
    from app.api.routes import _APP_VERSION

    return _APP_VERSION


class AppConfigService:
    """Engine for the resolved runtime config. One instance per process.

    Constructor injection (``cache_path`` / ``app_version`` / ``fetcher`` /
    ``env_overrides`` / ``use_registry``) exists for tests only — production
    code always goes through ``get_app_config_service()``.
    """

    def __init__(
        self,
        *,
        cache_path: Path | None = None,
        app_version: str | None = None,
        fetcher: Callable[[], Awaitable[AppConfigRow]] | None = None,
        env_overrides: dict[str, str] | None = None,
        refresh_interval_s: int | None = None,
        use_registry: bool = True,
    ) -> None:
        self._cache_path = cache_path or (MATRX_HOME_DIR / CACHE_FILENAME)
        self._app_version = app_version
        self._fetcher = fetcher or fetch_remote
        self._env_overrides = (
            env_overrides if env_overrides is not None else _detect_env_overrides()
        )
        self._interval = refresh_interval_s or DEFAULT_REFRESH_INTERVAL_S
        self._use_registry = use_registry

        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._refresh_lock: asyncio.Lock | None = None
        self.last_error: str | None = None

        for key, value in self._env_overrides.items():
            logger.warning(
                "[app_config] DEV OVERRIDE ACTIVE — %s=%s (env var differs from "
                "compiled default; remote app config is IGNORED for this key)",
                key,
                value,
            )

        # Boot resolution is synchronous and offline: cache else defaults.
        self._resolved: ResolvedAppConfig = self._load_initial()

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    @property
    def resolved(self) -> ResolvedAppConfig:
        return self._resolved

    def get_aidream_server_url(self) -> str:
        return self._resolved.effective_url("aidream_server_url")

    def get_matrx_files_url(self) -> str:
        return self._resolved.effective_url("matrx_files_url")

    def get_scraper_server_url(self) -> str:
        return self._resolved.effective_url("scraper_server_url")

    def get_flag(self, name: str, default: bool = False) -> bool:
        return self._resolved.row.config.flags.get(name, default)

    def get_notice(self) -> AppConfigNotice | None:
        return self._resolved.row.config.notice

    def status_payload(self) -> dict:
        """Provenance summary for /health and /admin/refresh-config.

        ``notice`` carries the operator broadcast (or None) so the desktop UI
        can render it without a second endpoint — it is part of the applied
        config, so serving it here is free.
        """
        r = self._resolved
        notice = r.row.config.notice
        return {
            # NOTE: tier stays remote/cache/defaults — the "env" Tier literal
            # is reserved. Env overrides are per-key, so they are reported
            # separately below rather than as a whole-config tier.
            "tier": r.tier,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
            "update_required": r.update_required,
            "notice": notice.model_dump(mode="json") if notice else None,
            "env_overrides": sorted(r.env_overrides.keys()),
        }

    # ------------------------------------------------------------------
    # Boot resolution (synchronous, offline)
    # ------------------------------------------------------------------

    def _load_initial(self) -> ResolvedAppConfig:
        cached = self._read_cache()
        if cached is not None:
            row, fetched_at = cached
            logger.info(
                "[app_config] boot: applied last-good cached config "
                "(fetched_at=%s) — background refresh will follow",
                fetched_at.isoformat() if fetched_at else "unknown",
            )
            return self._build_resolved(row, tier="cache", fetched_at=fetched_at)

        logger.warning(
            "[app_config] boot: no disk cache — running on COMPILED DEFAULTS "
            "until the background fetch succeeds"
        )
        row = parse_row(_COMPILED_DEFAULTS_ROW)
        return self._build_resolved(row, tier="defaults", fetched_at=None)

    def _build_resolved(
        self, row: AppConfigRow, *, tier: Tier, fetched_at: datetime | None
    ) -> ResolvedAppConfig:
        app_version = self._app_version or _resolve_app_version()
        if app_version == "0.0.0":
            # "0.0.0" is the version-probe FALLBACK, not a real version — a
            # broken probe must not brand a healthy install with a permanent
            # false "Update required" banner. Cannot gate → gate open, loudly.
            logger.warning(
                "[app_config] app version resolved to the '0.0.0' fallback — "
                "the version probe is broken; version gate DISABLED "
                "(cannot compare an unknown version against "
                "min_supported_app_version=%s)",
                row.min_supported_app_version,
            )
            update_required = False
        else:
            update_required = version_below(app_version, row.min_supported_app_version)
        if update_required:
            # A STATE, not an error — but a loud one: the operator raised the
            # floor above this installed version.
            logger.warning(
                "[app_config] UPDATE REQUIRED — installed version %s is below "
                "min_supported_app_version %s",
                app_version,
                row.min_supported_app_version,
            )
        effective_tier: Tier = tier
        return ResolvedAppConfig(
            row=row,
            tier=effective_tier,
            fetched_at=fetched_at,
            env_overrides=dict(self._env_overrides),
            update_required=update_required,
            app_version=app_version,
        )

    # ------------------------------------------------------------------
    # Disk cache — atomic writes (temp file + os.replace), never partial
    # ------------------------------------------------------------------

    def _read_cache(self) -> tuple[AppConfigRow, datetime | None] | None:
        try:
            if not self._cache_path.exists():
                return None
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                # Valid JSON but not an object ([], "x", null, …) — without
                # this guard the .get() below raises AttributeError inside
                # __init__, the singleton is never assigned, and EVERY
                # accessor call re-raises until the user deletes the file.
                logger.warning(
                    "[app_config] disk cache at %s is valid JSON but not an "
                    "object (got %s) — ignoring it and falling back to "
                    "compiled defaults",
                    self._cache_path,
                    type(data).__name__,
                )
                return None
            row = parse_row(data.get("row"))
            fetched_at: datetime | None = None
            raw_fetched = data.get("fetched_at")
            if isinstance(raw_fetched, str):
                try:
                    fetched_at = datetime.fromisoformat(raw_fetched)
                except ValueError:
                    fetched_at = None
            return row, fetched_at
        except (OSError, ValueError, AppConfigError) as exc:
            # Corrupt cache is a fetch-tier failure, not a crash: scream and
            # fall through to compiled defaults.
            logger.warning(
                "[app_config] disk cache at %s is unreadable/invalid — ignoring "
                "it and falling back to compiled defaults: %s",
                self._cache_path,
                exc,
            )
            return None

    def _write_cache(self, row: AppConfigRow, fetched_at: datetime) -> None:
        payload = {
            "row": row.model_dump(mode="json"),
            "fetched_at": fetched_at.isoformat(),
        }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=f".{CACHE_FILENAME}.", dir=str(self._cache_path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                os.replace(tmp_path, self._cache_path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        except OSError as exc:
            # A failed cache write must not take down the fresh in-memory
            # config — but it must be loud: next boot will silently regress
            # a tier if this keeps failing.
            logger.warning(
                "[app_config] failed to persist config cache to %s: %s",
                self._cache_path,
                exc,
            )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh_now(self) -> ResolvedAppConfig:
        """Fetch, validate, persist, and apply the remote config.

        Never raises: on any failure the current resolved config stays
        applied, the launcher registry is marked degraded with the active
        tier, and ``last_error`` carries the reason.
        """
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        async with self._refresh_lock:
            registry = get_registry() if self._use_registry else None
            try:
                row = await self._fetcher()
            except (AppConfigError, Exception) as exc:  # noqa: BLE001 — total isolation
                self.last_error = str(exc)
                current = self._resolved
                logger.warning(
                    "[app_config] remote refresh FAILED — staying on %s config "
                    "(%s). Reason: %s",
                    current.tier,
                    "last-good cache" if current.tier == "cache" else "compiled defaults"
                    if current.tier == "defaults"
                    else "previously fetched remote",
                    exc,
                )
                if registry is not None:
                    registry.degraded(
                        SERVICE_NAME,
                        reason=f"remote fetch failed: {exc}",
                        tier=current.tier,
                        fetched_at=current.fetched_at.isoformat()
                        if current.fetched_at
                        else None,
                    )
                return self._resolved

            fetched_at = datetime.now(timezone.utc)
            self._write_cache(row, fetched_at)
            self._resolved = self._build_resolved(
                row, tier="remote", fetched_at=fetched_at
            )
            self.last_error = None
            if registry is not None:
                registry.ready(
                    SERVICE_NAME,
                    tier="remote",
                    fetched_at=fetched_at.isoformat(),
                    update_required=self._resolved.update_required,
                    min_supported_app_version=row.min_supported_app_version,
                )
            return self._resolved

    # ------------------------------------------------------------------
    # Background loop (launcher-managed; see app/main.py Phase 00)
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start_background(self) -> None:
        """Kick the initial fetch + periodic refresh. Non-blocking."""
        if self.active:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._refresh_loop(), name="app-config-refresh"
        )
        logger.info(
            "[app_config] background refresh started (interval=%ss)", self._interval
        )

    async def stop_background(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[app_config] background refresh stopped")

    async def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.refresh_now()
            except Exception:  # noqa: BLE001 — the loop must survive anything
                logger.warning(
                    "[app_config] refresh tick crashed (loop continues)",
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue


# ---------------------------------------------------------------------------
# Singleton + process-wide accessors
# ---------------------------------------------------------------------------

_service: AppConfigService | None = None
_service_lock = threading.Lock()


def get_app_config_service() -> AppConfigService:
    """Return the process-wide service, creating it (cache/defaults, offline,
    synchronous) on first call. Safe from any thread."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = AppConfigService()
    return _service


def get_app_config() -> ResolvedAppConfig:
    """The resolved config + provenance the process is running on."""
    return get_app_config_service().resolved


def get_aidream_server_url() -> str:
    """Effective aidream base URL (env override > remote > cache > default)."""
    return get_app_config_service().get_aidream_server_url()


def get_matrx_files_url() -> str:
    """Effective matrx-files base URL (env override > remote > cache > default)."""
    return get_app_config_service().get_matrx_files_url()


def get_scraper_server_url() -> str:
    """Effective scraper base URL (env override > remote > cache > default)."""
    return get_app_config_service().get_scraper_server_url()


def get_flag(name: str, default: bool = False) -> bool:
    """Remote feature flag / kill switch, from the applied config."""
    return get_app_config_service().get_flag(name, default)


def get_notice() -> AppConfigNotice | None:
    """Operator broadcast notice, if the applied config carries one."""
    return get_app_config_service().get_notice()
