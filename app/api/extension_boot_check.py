"""Boot-time self-check for the matrx-extend ↔ matrx-local bridge surface.

Goal
----
Every engine startup (and every authenticated re-run via
``POST /extension/boot-check/run``) walks the bridge surface and answers
five orthogonal questions in one shot, in <1 second, with zero external
dependencies:

  1. Are all expected ``/extension/*`` routes registered on the FastAPI
     app? (catches accidental ``include_router`` regressions.)
  2. What's the current JWT-validation posture? (full crypto verification
     vs. degraded permissive Bearer-presence.)
  3. Is the in-memory tunnel-state singleton coherent? (sanity-checks the
     observability path the desktop UI relies on.)
  4. Was the metrics module reset cleanly so this boot starts from zero?
  5. Is the discovery file (``~/.matrx/local.json``) present and parseable
     so external clients can find the engine before authenticating?

The check is **non-fatal**: a failed check sets ``ok=False`` on the
summary and produces a loud log line, but never blocks startup. The
bridge can be partially broken (e.g. tunnel down) and the user still
needs the engine for everything else.

Public API
----------
``CheckResult``         — dataclass for one check.
``BootCheckSummary``    — dataclass for the full sweep.
``run_extension_boot_check(app)`` — async entry point.
``get_cached_summary()`` — last summary as JSON-serializable dict
                           (cheap GET endpoint).

The cache is a module-level reference. ``GET /extension/boot-check``
reads it; ``POST /extension/boot-check/run`` reruns the check and
replaces it. The cache is intentionally process-local — a desktop sidecar
has one process, and persisting the summary across restarts would defeat
the "what's the state right NOW" purpose.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from app.api.extension_auth import _supabase_jwks_url
from app.api.extension_metrics import reset_metrics
from app.api.tunnel_state import get_tunnel_snapshot
from app.common.system_logger import get_logger
from app.config import MATRX_HOME_DIR

logger = get_logger()


# Status values mirror common health-check vocabulary so log readers /
# downstream dashboards can reason about them without a custom legend.
CheckStatus = Literal["ok", "warn", "fail"]


# ---------------------------------------------------------------------------
# Expected route inventory
# ---------------------------------------------------------------------------
#
# These are the canonical /extension/* paths the engine must register at
# boot. Adding a new route to either ``extension_routes.py`` or
# ``extension_bridge_routes.py`` should add an entry here so the
# self-check catches accidental regressions.
#
# The strings match ``Route.path``/``WebSocketRoute.path`` exactly —
# FastAPI normalises path params and prefixes for us.
# ---------------------------------------------------------------------------

_EXPECTED_HTTP_ROUTES: tuple[str, ...] = (
    "/extension/rpc",
    "/extension/pair",
    "/extension/sessions",
    "/extension/sessions/disconnect",
    "/extension/invoke",
    "/extension/broadcast/status",
    "/extension/broadcast/test",
    "/extension/tunnel/status",
    "/extension/metrics",
    "/extension/metrics/reset",
    # The boot-check endpoints themselves register late — included so the
    # check verifies its own routing is wired up. Both are present after
    # ``app.include_router(extension_bridge_router)`` in ``app/main.py``.
    "/extension/boot-check",
    "/extension/boot-check/run",
)

_EXPECTED_WS_ROUTES: tuple[str, ...] = (
    "/extension/ws",
    "/extension/bridge-events",
)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """A single named check outcome.

    Attributes:
        name: short, stable identifier — used as a row label in logs and
            UI. Snake_case so the cached JSON shape is consumer-friendly.
        status: ``"ok"`` | ``"warn"`` | ``"fail"``. Only ``"fail"`` flips
            the summary's overall ``ok`` flag.
        message: human-readable detail. Kept short — one line in the
            startup log.
        duration_ms: wall-clock cost of running this check.
    """

    name: str
    status: CheckStatus
    message: str
    duration_ms: float


@dataclass
class BootCheckSummary:
    """Full self-check sweep result.

    Attributes:
        ok: True when every check has ``status != 'fail'``. Warnings are
            tolerated so a degraded JWT posture doesn't fail the summary.
        checks: ordered list of ``CheckResult``s, in execution order.
        started_at: ``time.time()`` snapshot when the sweep began.
        finished_at: ``time.time()`` snapshot when the sweep ended.
    """

    ok: bool = True
    checks: List[CheckResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0


# ---------------------------------------------------------------------------
# Cache (process-singleton)
# ---------------------------------------------------------------------------

_LATEST_SUMMARY: Optional[BootCheckSummary] = None


def get_cached_summary() -> Optional[Dict[str, Any]]:
    """Return the last summary as a JSON-serializable dict, or ``None``.

    ``None`` means the engine has not yet completed a self-check sweep —
    callers should treat that as "engine still starting up" and either
    wait or trigger a fresh run via ``POST /extension/boot-check/run``.
    """
    if _LATEST_SUMMARY is None:
        return None
    return _summary_to_dict(_LATEST_SUMMARY)


def _summary_to_dict(summary: BootCheckSummary) -> Dict[str, Any]:
    """Materialize a summary into plain dicts (asdict + duration field)."""
    return {
        "ok": summary.ok,
        "checks": [asdict(c) for c in summary.checks],
        "started_at": summary.started_at,
        "finished_at": summary.finished_at,
        "duration_ms": (summary.finished_at - summary.started_at) * 1000.0,
    }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


async def _check_routes_registered(app: Any) -> CheckResult:
    """Walk ``app.routes`` and confirm every expected /extension/* path is bound.

    Reads ``app.router.routes`` (FastAPI inherits Starlette's ``router``).
    Each ``Route`` exposes ``.path``; each ``WebSocketRoute`` does too.
    Missing entries are reported by name so the operator gets actionable
    output instead of "something's wrong".
    """
    t0 = time.perf_counter()

    found_http: set[str] = set()
    found_ws: set[str] = set()

    # ``app.routes`` flattens Starlette + FastAPI route subclasses.
    # Starlette uses ``WebSocketRoute``; FastAPI's ``@router.websocket``
    # decorator produces ``APIWebSocketRoute``. Match either by suffix so
    # this stays robust to future class renames in either lib.
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        cls_name = type(route).__name__
        if cls_name.endswith("WebSocketRoute"):
            found_ws.add(path)
        else:
            found_http.add(path)

    missing_http = [p for p in _EXPECTED_HTTP_ROUTES if p not in found_http]
    missing_ws = [p for p in _EXPECTED_WS_ROUTES if p not in found_ws]

    duration_ms = (time.perf_counter() - t0) * 1000.0
    total_expected = len(_EXPECTED_HTTP_ROUTES) + len(_EXPECTED_WS_ROUTES)

    if missing_http or missing_ws:
        missing = missing_http + [f"WS {p}" for p in missing_ws]
        return CheckResult(
            name="routes_registered",
            status="fail",
            message=(
                f"missing {len(missing)}/{total_expected} expected route(s): "
                f"{', '.join(missing)}"
            ),
            duration_ms=duration_ms,
        )

    return CheckResult(
        name="routes_registered",
        status="ok",
        message=f"{total_expected} routes present "
                f"({len(_EXPECTED_HTTP_ROUTES)} HTTP, {len(_EXPECTED_WS_ROUTES)} WS)",
        duration_ms=duration_ms,
    )


async def _check_jwt_validation_posture() -> CheckResult:
    """Probe the current /extension/* auth posture.

    The engine is a desktop sidecar — it cannot have a server-side JWT
    signing secret (HS256). Two valid postures:

      * ``ok``   — JWKS configured (asymmetric tokens RS256/ES256 are
        cryptographically verified). HS256 tokens still pass through
        with presence-only on loopback.
      * ``ok``   — no JWKS (presence-only on loopback). This is the
        normal mode for desktop installs; the security boundary is the
        loopback socket, not the JWT signature.

    There is no ``warn`` or ``fail`` for missing JWKS — that's the
    expected mode. The check confirms PyJWT is importable so the JWKS
    path can run if it's needed.
    """
    t0 = time.perf_counter()

    jwks_configured = _supabase_jwks_url() is not None

    # Confirm PyJWT is importable so the JWKS verification path will
    # actually run if/when an asymmetric token arrives. We don't hit
    # the network here — the first authenticated /extension/* request
    # exercises that.
    try:
        import jwt as _jwt  # noqa: F401
    except ImportError:
        if jwks_configured:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return CheckResult(
                name="jwt_validation",
                status="fail",
                message=(
                    "PyJWT not installed but SUPABASE_URL is set — "
                    "JWKS verification path cannot run"
                ),
                duration_ms=duration_ms,
            )

    duration_ms = (time.perf_counter() - t0) * 1000.0
    if jwks_configured:
        return CheckResult(
            name="jwt_validation",
            status="ok",
            message="posture=JWKS (asymmetric tokens verified, HS256 pass-through on loopback)",
            duration_ms=duration_ms,
        )
    return CheckResult(
        name="jwt_validation",
        status="ok",
        message="posture=loopback-presence (no JWKS configured; expected for desktop installs)",
        duration_ms=duration_ms,
    )


async def _check_tunnel_state() -> CheckResult:
    """Read the tunnel-state singleton. Always informational.

    The tunnel may legitimately be inactive at boot (most users keep
    ``MATRX_PREFER_TUNNEL=false``); the goal is to confirm the snapshot
    machinery answers without raising. A crash here would point at a
    broken introspection path the desktop UI relies on.
    """
    t0 = time.perf_counter()
    try:
        snapshot = get_tunnel_snapshot()
    except Exception as exc:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return CheckResult(
            name="tunnel_state",
            status="fail",
            message=f"get_tunnel_snapshot() raised: {type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
        )

    duration_ms = (time.perf_counter() - t0) * 1000.0
    if snapshot.get("active"):
        url = snapshot.get("tunnel_url") or "(url missing)"
        mode = snapshot.get("mode") or "?"
        return CheckResult(
            name="tunnel_state",
            status="ok",
            message=f"active ({mode}) → {url}",
            duration_ms=duration_ms,
        )
    return CheckResult(
        name="tunnel_state",
        status="ok",
        message="inactive (local-loopback only)",
        duration_ms=duration_ms,
    )


async def _check_metrics_module() -> CheckResult:
    """Reset the metrics ring so the boot starts from clean counters.

    Boot-time reset is intentional — the in-memory ring is process-local
    and a fresh boot logically starts a new observation window. The
    Bridge Test panel's "Reset" button handles ad-hoc clears mid-session.
    """
    t0 = time.perf_counter()
    try:
        await reset_metrics()
    except Exception as exc:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return CheckResult(
            name="metrics_module",
            status="fail",
            message=f"reset_metrics() raised: {type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
        )

    duration_ms = (time.perf_counter() - t0) * 1000.0
    return CheckResult(
        name="metrics_module",
        status="ok",
        message="reset (counters cleared for this boot)",
        duration_ms=duration_ms,
    )


async def _check_discovery_file() -> CheckResult:
    """Confirm ``~/.matrx/local.json`` exists and parses with a valid port.

    The discovery file is the bootstrap source of truth for any external
    client (extension, mobile companion) before they have a Bearer token.
    Missing-or-corrupt is a ``warn`` rather than ``fail`` because the
    engine itself still works without it — only auto-discovery breaks.
    """
    t0 = time.perf_counter()
    discovery_path = Path(MATRX_HOME_DIR) / "local.json"

    if not discovery_path.exists():
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return CheckResult(
            name="discovery_file",
            status="warn",
            message=(
                f"{discovery_path} missing — external clients won't auto-discover "
                "(engine still serves on its bound port)"
            ),
            duration_ms=duration_ms,
        )

    try:
        # Read in a thread to keep the event loop responsive on slow disks.
        raw = await asyncio.to_thread(discovery_path.read_text, "utf-8")
        data = json.loads(raw)
    except Exception as exc:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return CheckResult(
            name="discovery_file",
            status="warn",
            message=(
                f"{discovery_path} unreadable: {type(exc).__name__}: {exc}"
            ),
            duration_ms=duration_ms,
        )

    if not isinstance(data, dict) or not isinstance(data.get("port"), int):
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return CheckResult(
            name="discovery_file",
            status="warn",
            message=(
                f"{discovery_path} present but missing valid 'port' field"
            ),
            duration_ms=duration_ms,
        )

    duration_ms = (time.perf_counter() - t0) * 1000.0
    return CheckResult(
        name="discovery_file",
        status="ok",
        message=f"{discovery_path}, port {data['port']}",
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_extension_boot_check(app: Any) -> BootCheckSummary:
    """Run every check, log the summary, cache it, return it.

    Designed to be cheap (well under 1s) and side-effect-light. The only
    intentional side effect is the metrics-module reset, which is a
    no-op on counters that are already empty.

    The cached result feeds ``GET /extension/boot-check`` so subsequent
    reads are sub-millisecond.

    Args:
        app: the FastAPI application instance whose routes we're
            verifying. Caller passes ``app`` from inside the lifespan
            handler.
    """
    global _LATEST_SUMMARY

    started = time.time()
    summary = BootCheckSummary(started_at=started)

    # Run sequentially — the checks are intentionally fast and depend on
    # each other only loosely (routes_registered must logically precede
    # JWT/tunnel/metrics, but they don't share state). Sequential order
    # also keeps the log output deterministic.
    summary.checks.append(await _check_routes_registered(app))
    summary.checks.append(await _check_jwt_validation_posture())
    summary.checks.append(await _check_tunnel_state())
    summary.checks.append(await _check_metrics_module())
    summary.checks.append(await _check_discovery_file())

    summary.finished_at = time.time()
    summary.ok = all(c.status != "fail" for c in summary.checks)

    _emit_summary_log(summary)
    _LATEST_SUMMARY = summary
    return summary


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _emit_summary_log(summary: BootCheckSummary) -> None:
    """Emit one multi-line INFO block with the full sweep.

    Format intentionally mirrors a CI report so the user can scan the
    startup log and immediately see which sub-system is degraded::

        [boot] Extension bridge self-check
        [boot]   routes_registered  : ok    (11 routes present ...)
        [boot]   jwt_validation     : ok    (posture=loopback-presence ...)
        ...
        [boot] Self-check completed in 12ms — overall ok=True
    """
    name_width = max((len(c.name) for c in summary.checks), default=0)
    elapsed_ms = (summary.finished_at - summary.started_at) * 1000.0

    logger.info("[boot] Extension bridge self-check")
    for check in summary.checks:
        # Pad names to a consistent column so the status flags line up.
        padded = check.name.ljust(name_width)
        # Status field is 4 chars wide ("ok  " / "warn" / "fail").
        status_str = check.status.ljust(4)
        log_line = f"[boot]   {padded} : {status_str}  ({check.message})"
        if check.status == "fail":
            logger.error(log_line)
        elif check.status == "warn":
            logger.warning(log_line)
        else:
            logger.info(log_line)

    overall = "True" if summary.ok else "False"
    logger.info(
        "[boot] Self-check completed in %.0fms — overall ok=%s",
        elapsed_ms,
        overall,
    )


__all__ = [
    "BootCheckSummary",
    "CheckResult",
    "CheckStatus",
    "get_cached_summary",
    "run_extension_boot_check",
]
