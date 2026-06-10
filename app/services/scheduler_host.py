"""matrx-scheduler host for matrx-local.

The desktop sidecar participates in the cross-component scheduling spine
as ``surface='desktop'``. matrx-scheduler subscribes to ``sch_task`` rows
where ``surfaces[]`` contains ``'desktop'`` or ``'any'``, claims them
atomically (via the ``sch_run_unique_active_per_task`` partial unique
index in Postgres), runs them via the configured ``agent_runner``, and
reports results back to Supabase.

This mirrors aidream's wiring at
``aidream/aidream/package_integration.py::_configure_matrx_scheduler`` +
``aidream/aidream/api/app.py`` (surface='server') — the difference is
that matrx-local is a per-user desktop sidecar, so the scanner runs
against an anon Supabase client carrying the user's JWT. RLS filters
``sch_*`` rows to the signed-in user automatically.

Lifecycle (used by ``app/main.py`` Phase 8 / Phase S8):

  ``configure_scheduler_host(supabase_client)``
      Wire matrx-scheduler with the desktop host's settings. Idempotent.
  ``start_scheduler_host()``
      Spawn the background scanner asyncio task. No-op if not configured
      or if the feature flag is off.
  ``stop_scheduler_host()``
      Graceful shutdown of the scanner. Always safe to call.
  ``scheduler_status()``
      Returns the current ScannerStatus dict, or ``None`` if not running.

Activation is gated by the ``MATRX_LOCAL_SCHEDULER_ENABLED`` env var
(default: off). When disabled, every helper short-circuits with a debug
log so the rest of the engine is untouched. Flip ``MATRX_LOCAL_SCHEDULER_ENABLED=1``
in the env to opt in.

Caveats discovered while wiring (May 2026):
- matrx-scheduler 0.1.0's scanner only queries ``kind = 'agent'`` tasks
  (see ``matrx_scheduler/queries.py::find_due_tasks``). Until the kind
  filter is widened or a new tool runner lands, desktop will receive
  only agent-kind work. Cross-component tasks targeted at the desktop
  surface as kind='tool' will not be picked up by this host yet.
- ``agent_runner`` is left as ``None`` here. matrx-local doesn't run
  the full LLM agent loop in-process (matrx-ai is mounted, but the
  scheduler bridge into it isn't built yet). Calling the scanner with
  a None runner means claimed agent tasks will fail at dispatch — for
  now we set ``agent_runner=None`` and rely on the feature flag being
  off in production until a desktop-side runner adapter is wired.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from app.common.system_logger import get_logger


logger = get_logger()


def _matrx_scheduler():
    """Import matrx_scheduler lazily.

    The package is an OPTIONAL extra (``uv sync --extra scheduler``) and is
    not yet on PyPI, so it may be absent. Importing it lazily lets the rest
    of this module — notably ``is_scheduler_enabled()`` — work without it.
    Callers run only after the feature flag is on, and main.py's Phase 8
    wraps the whole path in try/except, so a missing package degrades to
    "scheduler disabled" instead of crashing startup.
    """
    import matrx_scheduler  # type: ignore[import-not-found]

    return matrx_scheduler


# The string this host advertises as in ``sch_task.surfaces[]``. Pairs with
# the matching value used by the matrx-frontend scheduler UI when targeting
# the desktop sidecar.
DESKTOP_SURFACE = "desktop"

# Scanner cadence + lease length. Aligned with aidream's settings so the
# behaviour is consistent across surfaces; tune later if desktop needs a
# slower / faster poll than the server host.
_SCAN_INTERVAL_SECONDS = 5.0
_LEASE_SECONDS = 600


def is_scheduler_enabled() -> bool:
    """Return True when the host should configure / start the scanner.

    Default OFF. Flip to ON by exporting ``MATRX_LOCAL_SCHEDULER_ENABLED=1``
    (or ``true`` / ``yes``) before launching the sidecar. The flag is
    read lazily on every call so a future runtime toggle could flip it
    without an engine restart.
    """
    return os.environ.get("MATRX_LOCAL_SCHEDULER_ENABLED", "").lower() in (
        "1",
        "true",
        "yes",
    )


async def configure_scheduler_host(supabase_client: Any) -> bool:
    """Wire matrx-scheduler with the desktop host configuration.

    Returns ``True`` if the package was configured (or was already
    configured by a previous call), ``False`` if the feature flag is
    off so the caller can short-circuit cleanly.

    Idempotent: matrx_scheduler.is_configured() short-circuits double-wiring.
    """
    if not is_scheduler_enabled():
        logger.info(
            "[scheduler_host] disabled — set MATRX_LOCAL_SCHEDULER_ENABLED=true to opt in"
        )
        return False

    matrx_scheduler = _matrx_scheduler()

    if matrx_scheduler.is_configured():
        logger.info(
            "[scheduler_host] already configured — skipping re-configure"
        )
        return True

    matrx_scheduler.configure(
        supabase_client=supabase_client,
        surface=DESKTOP_SURFACE,
        # No agent_runner yet — see module docstring. A None runner means
        # claimed agent-kind tasks will fail at dispatch. The feature
        # flag stays default-off precisely so we don't surprise users
        # by claiming-then-failing real tasks; landing a runner adapter
        # is the prerequisite for promoting the flag to default-on.
        agent_runner=None,
        # get_app_context / emitter_factory are also None — they're
        # optional in matrx-scheduler 0.1.0 (see packages/matrx-scheduler/
        # matrx_scheduler/_ext.py). aidream wires them for matrx-ai's
        # AppContext propagation; matrx-local doesn't have an equivalent
        # context-aware emitter yet, so leave them off.
        get_app_context=None,
        emitter_factory=None,
        scan_interval_seconds=_SCAN_INTERVAL_SECONDS,
        lease_seconds=_LEASE_SECONDS,
    )
    logger.info(
        "[scheduler_host] configured surface=%s scan_interval=%.1fs lease=%ds",
        DESKTOP_SURFACE,
        _SCAN_INTERVAL_SECONDS,
        _LEASE_SECONDS,
    )
    return True


async def start_scheduler_host() -> bool:
    """Spawn the matrx-scheduler scanner background task.

    Returns ``True`` if the scanner was started, ``False`` if the flag is
    off or the host hasn't been configured yet.
    """
    if not is_scheduler_enabled():
        return False
    matrx_scheduler = _matrx_scheduler()
    if not matrx_scheduler.is_configured():
        logger.warning(
            "[scheduler_host] start_scheduler_host called but matrx-scheduler "
            "is not configured — call configure_scheduler_host first"
        )
        return False
    await matrx_scheduler.start_scanner()
    logger.info(
        "[scheduler_host] scanner started — surface=%s polling every %.1fs",
        DESKTOP_SURFACE,
        _SCAN_INTERVAL_SECONDS,
    )
    return True


async def stop_scheduler_host() -> None:
    """Graceful shutdown of the scanner. Always safe to call.

    Best-effort: failures during shutdown are logged at warning level
    but never re-raised so a wedged scanner can't block engine
    teardown. Mirrors aidream's pattern.
    """
    try:
        matrx_scheduler = _matrx_scheduler()
    except Exception:
        return
    if not matrx_scheduler.is_configured():
        return
    try:
        await matrx_scheduler.stop_scanner()
        logger.info("[scheduler_host] scanner stopped")
    except Exception as exc:
        logger.warning(
            "[scheduler_host] stop_scanner failed (non-fatal): %s",
            exc,
            exc_info=True,
        )


def scheduler_status() -> Optional[dict[str, Any]]:
    """Return the current ScannerStatus as a dict, or ``None`` if the host
    is disabled / unconfigured.

    Surfaced from ``/admin/status`` and the diagnostic dump so the user
    (and remote agents poking the engine) can see whether the scheduler
    is actually polling.
    """
    if not is_scheduler_enabled():
        return None
    try:
        matrx_scheduler = _matrx_scheduler()
    except Exception:
        return None
    if not matrx_scheduler.is_configured():
        return None
    try:
        st = matrx_scheduler.status()
    except Exception as exc:
        logger.warning(
            "[scheduler_host] status read failed: %s", exc, exc_info=True
        )
        return None
    # ScannerStatus is a dataclass — convert to a plain dict for JSON
    # serialization. Use a defensive vars() so we don't depend on a
    # specific dataclass shape (the package may add fields over time).
    try:
        return {
            k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in vars(st).items()
        }
    except Exception:
        return None
