"""Remote Catalogs service — resolves and serves the process-wide catalogs.

Owns the precedence chain (fresh remote > disk cache > compiled fallback),
the atomic disk cache under ``MATRX_HOME_DIR``, the 6h background refresh
loop, and the launcher-registry reporting. Mirrors the app_config service
(``app/services/app_config/service.py``) 1:1 — see
``app/services/catalogs/FEATURE.md`` and the cross-repo spec
(/Users/armanisadeghi/code/common-docs/remote-catalogs/FEATURE.md).

Startup contract: ``get_catalogs_service()`` resolves cache/compiled
SYNCHRONOUSLY (one file read, no network), so the first accessor call is
always instant and offline-safe. The network fetch runs only in the
background task started from the lifespan.
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

from app.common.system_logger import get_logger

from app.config import MATRX_HOME_DIR

# Semver helpers are OWNED by app_config — imported, never duplicated.
from app.services.app_config.models import version_below
from app.launcher import get_registry
from app.services.catalogs.client import fetch_remote
from app.services.catalogs.compiled import compiled_catalog_entries
from app.services.catalogs.models import (
    CatalogEntry,
    CatalogsError,
    ParseReport,
    ResolvedCatalogs,
    Tier,
    is_valid_kind,
    parse_entries,
)

logger = get_logger()

SERVICE_NAME = "catalogs"
CACHE_FILENAME = "catalogs.json"

# 6h default; env var is a developer-only knob (dev worlds / tests).
_REFRESH_INTERVAL_DEFAULT_S = 21600
_REFRESH_INTERVAL_MIN_S = 60


def _resolve_refresh_interval() -> int:
    """Parse the developer refresh-interval knob defensively (60s floor)."""
    raw = os.getenv("MATRX_CATALOGS_REFRESH_INTERVAL", str(_REFRESH_INTERVAL_DEFAULT_S))
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "[catalogs] MATRX_CATALOGS_REFRESH_INTERVAL=%r is not an integer — "
            "falling back to default %ss",
            raw,
            _REFRESH_INTERVAL_DEFAULT_S,
        )
        return _REFRESH_INTERVAL_DEFAULT_S
    if value < _REFRESH_INTERVAL_MIN_S:
        logger.warning(
            "[catalogs] MATRX_CATALOGS_REFRESH_INTERVAL=%s is below the %ss "
            "floor (a 0 would hot-loop against Supabase) — clamping to %ss",
            value,
            _REFRESH_INTERVAL_MIN_S,
            _REFRESH_INTERVAL_MIN_S,
        )
        return _REFRESH_INTERVAL_MIN_S
    return value


DEFAULT_REFRESH_INTERVAL_S = _resolve_refresh_interval()


def _resolve_app_version() -> str:
    """The running app's version — lazily imported to avoid a routes cycle."""
    from app.api.routes import _APP_VERSION  # noqa: PLC0415

    return _APP_VERSION


class CatalogsService:
    """Engine for the resolved catalog set. One instance per process.

    Constructor injection (``cache_path`` / ``app_version`` / ``fetcher`` /
    ``compiled`` / ``use_registry``) exists for tests only — production code
    always goes through ``get_catalogs_service()``.
    """

    def __init__(
        self,
        *,
        cache_path: Path | None = None,
        app_version: str | None = None,
        fetcher: Callable[[ParseReport], Awaitable[list[CatalogEntry]]] | None = None,
        compiled: Callable[[], list[CatalogEntry]] | None = None,
        refresh_interval_s: int | None = None,
        use_registry: bool = True,
    ) -> None:
        self._cache_path = cache_path or (MATRX_HOME_DIR / CACHE_FILENAME)
        self._app_version = app_version
        self._fetcher = fetcher or fetch_remote
        self._compiled = compiled or compiled_catalog_entries
        self._interval = refresh_interval_s or DEFAULT_REFRESH_INTERVAL_S
        self._use_registry = use_registry

        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._refresh_lock: asyncio.Lock | None = None
        self.last_error: str | None = None
        self._version_gate_warned = False

        # Fault-isolation visibility: malformed rows skipped while parsing
        # the currently-applied tier (surfaced in status_payload → /health).
        self._skipped_entries = 0
        # Per-kind emptiness floors currently in force (see get_catalog).
        self._kind_floors_active: set[str] = set()
        # Lazy per-kind index of the compiled tier, built on first floor check.
        self._compiled_by_kind: dict[str, list[CatalogEntry]] | None = None

        # Boot resolution is synchronous and offline: cache else compiled.
        self._resolved: ResolvedCatalogs = self._load_initial()

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    @property
    def resolved(self) -> ResolvedCatalogs:
        return self._resolved

    def get_catalog(self, kind: str) -> list[CatalogEntry]:
        """Active entries of ``kind``, sorted by sort_order, version-gated.

        Entries whose ``min_app_version`` exceeds the running app version are
        filtered OUT (with a debug log) — an entry referencing a pipeline the
        installed binary lacks must never surface. Unknown kinds raise
        ``ValueError`` loudly: consumers pass literal kind slugs, so a miss
        is always a code bug, never a data state.
        """
        if not is_valid_kind(kind):
            raise ValueError(
                f"unknown catalog kind {kind!r} — not in KNOWN_KINDS "
                "(app/services/catalogs/models.py)"
            )
        r = self._resolved
        gate_open = r.app_version == "0.0.0"  # broken version probe → cannot gate
        out: list[CatalogEntry] = []
        for e in r.entries:
            if e.kind != kind or not e.is_active:
                continue
            if e.min_app_version and not gate_open and version_below(
                r.app_version, e.min_app_version
            ):
                logger.debug(
                    "[catalogs] filtering %s/%s: min_app_version=%s exceeds "
                    "running app version %s",
                    e.kind,
                    e.key,
                    e.min_app_version,
                    r.app_version,
                )
                continue
            if e.min_app_version and gate_open and not self._version_gate_warned:
                self._version_gate_warned = True
                logger.warning(
                    "[catalogs] app version resolved to the '0.0.0' probe "
                    "fallback — per-entry min_app_version gate DISABLED "
                    "(cannot compare an unknown version)"
                )
            out.append(e)
        out.sort(key=lambda e: (e.sort_order, e.key))

        # Per-kind emptiness floor (generalized from the tts_model_file
        # precedent): a kind that is non-empty in the compiled tier must
        # never resolve to ZERO entries from a remote/cache tier — a remote
        # mass-deactivation would otherwise brick that capability between
        # releases. Fall back to the compiled entries for THAT KIND, loudly.
        if not out and r.tier != "defaults":
            floor = self._compiled_kind_entries(kind)
            if floor:
                if kind not in self._kind_floors_active:
                    self._kind_floors_active.add(kind)
                    logger.warning(
                        "[catalogs] kind %r resolved to ZERO entries from the "
                        "%s tier but the compiled fallback has %d — applying "
                        "the per-kind compiled floor (a remote mass-"
                        "deactivation must not brick this capability)",
                        kind,
                        r.tier,
                        len(floor),
                    )
                return floor
        if out and kind in self._kind_floors_active:
            self._kind_floors_active.discard(kind)
            logger.info(
                "[catalogs] kind %r recovered (%d entries from the %s tier) — "
                "per-kind compiled floor lifted",
                kind,
                len(out),
                r.tier,
            )
        return out

    def _compiled_kind_entries(self, kind: str) -> list[CatalogEntry]:
        """Active compiled-tier entries for ``kind``, sorted (lazy index)."""
        if self._compiled_by_kind is None:
            index: dict[str, list[CatalogEntry]] = {}
            for e in self._compiled():
                if e.is_active:
                    index.setdefault(e.kind, []).append(e)
            for entries in index.values():
                entries.sort(key=lambda e: (e.sort_order, e.key))
            self._compiled_by_kind = index
        return list(self._compiled_by_kind.get(kind, []))

    def get_catalog_entry(self, kind: str, key: str) -> CatalogEntry | None:
        """The single resolved entry for ``(kind, key)``, or None."""
        for e in self.get_catalog(kind):
            if e.key == key:
                return e
        return None

    def status_payload(self) -> dict:
        """Provenance summary for /health and /admin/refresh-catalogs."""
        r = self._resolved
        kind_counts: dict[str, int] = {}
        for e in r.entries:
            if e.is_active:
                kind_counts[e.kind] = kind_counts.get(e.kind, 0) + 1
        return {
            "tier": r.tier,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
            "entry_count": sum(kind_counts.values()),
            "kinds": dict(sorted(kind_counts.items())),
            # Malformed rows dropped while parsing the applied tier — a
            # non-zero value is a degraded state an admin must be able to
            # see where they look (/health).
            "skipped_entries": self._skipped_entries,
            # Kinds currently running on the per-kind compiled floor.
            "kind_floors_active": sorted(self._kind_floors_active),
        }

    # ------------------------------------------------------------------
    # Boot resolution (synchronous, offline)
    # ------------------------------------------------------------------

    def _load_initial(self) -> ResolvedCatalogs:
        report = ParseReport()
        cached = self._read_cache(report)
        if cached is not None:
            entries, fetched_at = cached
            self._skipped_entries = report.skipped
            logger.info(
                "[catalogs] boot: applied last-good cached catalogs "
                "(%d entries, fetched_at=%s) — background refresh will follow",
                len(entries),
                fetched_at.isoformat() if fetched_at else "unknown",
            )
            return self._build_resolved(entries, tier="cache", fetched_at=fetched_at)

        logger.warning(
            "[catalogs] boot: no disk cache — running on COMPILED FALLBACK "
            "catalogs until the background fetch succeeds"
        )
        return self._build_resolved(self._compiled(), tier="defaults", fetched_at=None)

    def _build_resolved(
        self, entries: list[CatalogEntry], *, tier: Tier, fetched_at: datetime | None
    ) -> ResolvedCatalogs:
        app_version = self._app_version or _resolve_app_version()
        return ResolvedCatalogs(
            entries=entries,
            tier=tier,
            fetched_at=fetched_at,
            app_version=app_version,
        )

    # ------------------------------------------------------------------
    # Disk cache — atomic writes (temp file + os.replace), never partial
    # ------------------------------------------------------------------

    def _read_cache(
        self, report: ParseReport | None = None
    ) -> tuple[list[CatalogEntry], datetime | None] | None:
        try:
            if not self._cache_path.exists():
                return None
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning(
                    "[catalogs] disk cache at %s is valid JSON but not an "
                    "object (got %s) — ignoring it and falling back to the "
                    "compiled catalogs",
                    self._cache_path,
                    type(data).__name__,
                )
                return None
            entries = parse_entries(data.get("entries"), report=report)
            fetched_at: datetime | None = None
            raw_fetched = data.get("fetched_at")
            if isinstance(raw_fetched, str):
                try:
                    fetched_at = datetime.fromisoformat(raw_fetched)
                except ValueError:
                    fetched_at = None
            return entries, fetched_at
        except (OSError, ValueError, CatalogsError) as exc:
            # Corrupt cache is a fetch-tier failure, not a crash: scream and
            # fall through to the compiled catalogs.
            logger.warning(
                "[catalogs] disk cache at %s is unreadable/invalid — ignoring "
                "it and falling back to the compiled catalogs: %s",
                self._cache_path,
                exc,
            )
            return None

    def _write_cache(self, entries: list[CatalogEntry], fetched_at: datetime) -> None:
        payload = {
            "entries": [e.model_dump(mode="json") for e in entries],
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
            # catalogs — but it must be loud: next boot silently regresses a
            # tier if this keeps failing.
            logger.warning(
                "[catalogs] failed to persist catalog cache to %s: %s",
                self._cache_path,
                exc,
            )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh_now(self) -> ResolvedCatalogs:
        """Fetch, validate, persist, and apply the remote catalogs.

        Never raises: on any failure the current resolved set stays applied,
        the launcher registry is marked degraded with the active tier, and
        ``last_error`` carries the reason.
        """
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        async with self._refresh_lock:
            registry = get_registry() if self._use_registry else None
            report = ParseReport()
            try:
                entries = await self._fetcher(report)
            except (CatalogsError, Exception) as exc:  # noqa: BLE001 — total isolation
                self.last_error = str(exc)
                current = self._resolved
                logger.warning(
                    "[catalogs] remote refresh FAILED — staying on %s catalogs "
                    "(%s). Reason: %s",
                    current.tier,
                    "last-good cache" if current.tier == "cache"
                    else "compiled fallback" if current.tier == "defaults"
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
            self._write_cache(entries, fetched_at)
            self._resolved = self._build_resolved(
                entries, tier="remote", fetched_at=fetched_at
            )
            self._skipped_entries = report.skipped
            if report.skipped:
                logger.warning(
                    "[catalogs] applied remote catalogs DEGRADED: %d malformed "
                    "row(s) skipped — %s",
                    report.skipped,
                    "; ".join(report.reasons),
                )
            self.last_error = None
            if registry is not None:
                registry.ready(
                    SERVICE_NAME,
                    tier="remote",
                    fetched_at=fetched_at.isoformat(),
                    entry_count=len(entries),
                    skipped_entries=report.skipped,
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
        self._task = asyncio.create_task(self._refresh_loop(), name="catalogs-refresh")
        logger.info(
            "[catalogs] background refresh started (interval=%ss)", self._interval
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
        logger.info("[catalogs] background refresh stopped")

    async def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.refresh_now()
            except Exception:  # noqa: BLE001 — the loop must survive anything
                logger.warning(
                    "[catalogs] refresh tick crashed (loop continues)",
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue


# ---------------------------------------------------------------------------
# Singleton + process-wide accessors
# ---------------------------------------------------------------------------

_service: CatalogsService | None = None
_service_lock = threading.Lock()


def get_catalogs_service() -> CatalogsService:
    """Return the process-wide service, creating it (cache/compiled, offline,
    synchronous) on first call. Safe from any thread."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = CatalogsService()
    return _service


def get_catalog(kind: str) -> list[CatalogEntry]:
    """Resolved entries for ``kind`` (active, version-gated, sort_order)."""
    return get_catalogs_service().get_catalog(kind)


def get_catalog_entry(kind: str, key: str) -> CatalogEntry | None:
    """The single resolved entry for ``(kind, key)``, or None."""
    return get_catalogs_service().get_catalog_entry(kind, key)
