"""System hardware inventory API.

Exposes a single source of truth for all hardware on the local machine:
  GET  /hardware          — return cached profile (detects on first call)
  POST /hardware/refresh  — re-run full detection, update cache + cloud

Detection is done once at startup (Phase 0d in main.py) and stored in the
module-level cache. Subsequent GET requests are instant.

Cloud push: after detection the full profile is written to
app_instances.system_hardware in Supabase so the cloud dashboard can
show full hardware info per device. The push is best-effort — a failure
never prevents the local response from succeeding.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.common.background_tasks import fire_and_forget
from app.launcher import get_registry
from app.services.hardware.detector import detect_all

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hardware", tags=["hardware"])

# ── Module-level cache ────────────────────────────────────────────────────────

_cached_profile: Optional[dict[str, Any]] = None
_detection_lock = asyncio.Lock()


# ── Response models ───────────────────────────────────────────────────────────

class HardwareResponse(BaseModel):
    profile: dict[str, Any]
    cached: bool
    detected_at: Optional[str] = None


# ── Cloud push helper ─────────────────────────────────────────────────────────

async def _push_hardware_to_cloud(profile: dict[str, Any]) -> None:
    """PATCH app_instances.system_hardware in Supabase. Best-effort — never raises."""
    try:
        import httpx
        from app.services.cloud_sync.settings_sync import get_settings_sync
        from app.services.cloud_sync.instance_manager import get_instance_manager

        sync = get_settings_sync()
        if not sync.is_configured:
            logger.debug("[hardware] Cloud push skipped — sync not configured yet")
            return

        mgr = get_instance_manager()
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "system_hardware": profile,
            "hardware_detected_at": now,
            "last_seen": now,
        }

        url = (
            f"{sync._supabase_url}/rest/v1/app_instances"
            f"?instance_id=eq.{mgr.instance_id}"
            f"&user_id=eq.{sync._user_id}"
        )
        headers = {
            **sync._headers(),
            "Prefer": "return=minimal",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(url, json=payload, headers=headers)
            if resp.is_success:
                logger.info("[hardware] Hardware profile pushed to cloud ✓")
            else:
                logger.warning(
                    "[hardware] Cloud push failed: HTTP %d — %s",
                    resp.status_code,
                    resp.text[:300],
                )

    except Exception as exc:
        logger.debug("[hardware] Cloud push exception (non-fatal): %s", exc)


# ── Public helper — called from lifespan ─────────────────────────────────────

async def run_initial_detection() -> dict[str, Any]:
    """Run hardware detection once at startup and populate the cache.

    Called from app/main.py lifespan Phase 0d.  Cloud push is scheduled
    as a fire-and-forget background task so it does not block startup.
    """
    global _cached_profile

    registry = get_registry()

    async with _detection_lock:
        if _cached_profile is not None:
            return _cached_profile

        registry.starting("hardware")
        try:
            profile = await detect_all()
            _cached_profile = profile
            logger.info("[hardware] Initial hardware detection complete")
            registry.ready("hardware")

            # Push to cloud in background — don't block startup
            fire_and_forget(_push_hardware_to_cloud(profile), name="hardware-push")

        except Exception as exc:
            # DEGRADED (not a bare warning): detection failed, so hardware-aware
            # features run on an empty/error profile until /hardware/refresh. The
            # registry.degraded call also drops a diagnostic breadcrumb.
            registry.degraded("hardware", reason=f"initial detection failed: {exc}")
            logger.warning("[hardware] Initial hardware detection failed: %s", exc)
            _cached_profile = {
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "cpus": [], "gpus": [], "ram": {},
                "audio_inputs": [], "audio_outputs": [],
                "video_devices": [], "monitors": [],
                "network_adapters": [], "storage": [],
            }

    return _cached_profile


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=HardwareResponse)
async def get_hardware() -> HardwareResponse:
    """Return the cached hardware profile.

    Triggers detection on the first call if the cache is not yet populated
    (e.g. if the lifespan phase hasn't completed yet).
    """
    global _cached_profile

    if _cached_profile is None:
        await run_initial_detection()

    return HardwareResponse(
        profile=_cached_profile or {},
        cached=True,
        detected_at=(_cached_profile or {}).get("detected_at"),
    )


@router.post("/refresh", response_model=HardwareResponse)
async def refresh_hardware() -> HardwareResponse:
    """Re-run full hardware detection, update cache, and push to cloud.

    Use this when the user clicks "Refresh" in the System tab.
    Detection runs in a thread pool to avoid blocking the event loop.
    """
    global _cached_profile

    async with _detection_lock:
        try:
            profile = await detect_all()
            _cached_profile = profile

            # Push to cloud (awaited here since user explicitly requested refresh)
            await _push_hardware_to_cloud(profile)

        except Exception as exc:
            logger.warning("[hardware] Refresh detection failed: %s", exc)
            error_profile: dict[str, Any] = {
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "cpus": [], "gpus": [], "ram": {},
                "audio_inputs": [], "audio_outputs": [],
                "video_devices": [], "monitors": [],
                "network_adapters": [], "storage": [],
            }
            _cached_profile = error_profile

    return HardwareResponse(
        profile=_cached_profile or {},
        cached=False,
        detected_at=(_cached_profile or {}).get("detected_at"),
    )
