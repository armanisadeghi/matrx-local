"""Location tool — get device location via CoreLocation (macOS only).

On macOS, CoreLocation can only be queried from the UI-owning process (the Tauri
app bundle). The Python sidecar is a background process and cannot prompt the TCC
dialog. This tool reads the authorization status and, if already granted, returns
the current location. If not yet determined, it returns UNKNOWN with instructions.

Requires:
  - TCC grant: Location Services (System Settings → Privacy & Security → Location Services)
  - NSLocationUsageDescription and NSLocationWhenInUseUsageDescription in Info.plist
  - pyobjc-framework-CoreLocation
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from app.common.platform_ctx import PLATFORM
from app.services.action_needed import os_permission_needed
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_PERMISSION_HINT = (
    "Location Services access is required. "
    "Go to System Settings → Privacy & Security → Location Services and enable it for AI Matrx, "
    "then restart the app."
)

# CLAuthorizationStatus constants
_CL_NOT_DETERMINED = 0
_CL_RESTRICTED = 1
_CL_DENIED = 2
_CL_AUTHORIZED_ALWAYS = 3
_CL_AUTHORIZED_WHEN_IN_USE = 4


class _LocationDelegate:
    """Minimal CLLocationManagerDelegate bridge."""

    def __init__(self) -> None:
        self.location: dict[str, float] | None = None
        self.error: str | None = None
        self._event = threading.Event()

    def locationManager_didUpdateLocations_(self, manager: Any, locations: Any) -> None:
        if locations and len(locations) > 0:
            loc = locations[-1]  # most recent
            coord = loc.coordinate()
            self.location = {
                "latitude": float(coord.latitude),
                "longitude": float(coord.longitude),
                "altitude": float(loc.altitude()),
                "horizontal_accuracy": float(loc.horizontalAccuracy()),
                "vertical_accuracy": float(loc.verticalAccuracy()),
                "speed": float(loc.speed()),
                "course": float(loc.course()),
            }
        manager.stopUpdatingLocation()
        self._event.set()

    def locationManager_didFailWithError_(self, manager: Any, error: Any) -> None:
        self.error = str(error.localizedDescription() if error else "unknown error")
        manager.stopUpdatingLocation()
        self._event.set()

    def locationManagerDidChangeAuthorization_(self, manager: Any) -> None:
        pass  # status changes handled by check before starting updates

    def wait(self, timeout: float = 15.0) -> bool:
        return self._event.wait(timeout=timeout)


def _get_location_sync(timeout: float = 15.0) -> dict[str, Any]:
    """Blocking location fetch. Must run in a thread pool."""
    import CoreLocation  # type: ignore[import]
    import objc  # type: ignore[import]

    status = CoreLocation.CLLocationManager.authorizationStatus()
    if status == _CL_DENIED:
        raise PermissionError(f"Location Services denied. {_PERMISSION_HINT}")
    if status == _CL_RESTRICTED:
        raise PermissionError("Location Services restricted (MDM/parental controls).")
    if status == _CL_NOT_DETERMINED:
        # Background sidecar cannot prompt — return status info only
        return {
            "available": False,
            "status": "not_determined",
            "instructions": _PERMISSION_HINT,
        }

    delegate = _LocationDelegate()
    manager = CoreLocation.CLLocationManager.alloc().init()

    # Attach delegate using PyObjC informal protocol
    manager.setDelegate_(
        objc.pyobjc_new_nonretained_object(delegate)  # type: ignore[attr-defined]
    )
    manager.setDesiredAccuracy_(CoreLocation.kCLLocationAccuracyBest)
    manager.startUpdatingLocation()

    if not delegate.wait(timeout=timeout):
        manager.stopUpdatingLocation()
        return {
            "available": False,
            "status": "timeout",
            "instructions": "Location request timed out. Ensure Location Services is enabled.",
        }

    if delegate.error:
        return {
            "available": False,
            "status": "error",
            "error": delegate.error,
        }

    if delegate.location:
        return {"available": True, "status": "granted", **delegate.location}

    return {"available": False, "status": "no_location"}


async def tool_get_location(
    session: ToolSession,
    timeout: float = 15.0,
) -> ToolResult:
    """Get the device's current GPS/network location via CoreLocation.

    Returns latitude, longitude, altitude, and accuracy in meters.
    Requires Location Services to be granted in System Settings.

    Args:
        timeout: Seconds to wait for a location fix (default 15, max 60).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(
            output="CoreLocation-based location tool is only available on macOS.",
            type=ToolResultType.ERROR,
        )

    timeout = max(3.0, min(timeout, 60.0))

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, _get_location_sync, timeout
        )
    except PermissionError as exc:
        return ToolResult(
            output=str(exc),
            metadata={"available": False, "status": "denied"},
            type=ToolResultType.ERROR,
            action_needed=os_permission_needed(feature="Device location", permission_key="location", source="tool.location"),
        )
    except Exception as exc:
        logger.exception("tool_get_location failed")
        return ToolResult(
            output=f"Failed to get location: {exc}",
            type=ToolResultType.ERROR,
        )

    if result.get("available"):
        lat = result.get("latitude")
        lon = result.get("longitude")
        acc = result.get("horizontal_accuracy")
        output = f"Location: {lat:.6f}, {lon:.6f} (±{acc:.0f}m)" if lat and lon else "Location retrieved."
    else:
        output = result.get("instructions") or result.get("error") or "Location unavailable."

    return ToolResult(
        output=output,
        metadata=result,
        type=ToolResultType.SUCCESS if result.get("available") else ToolResultType.ERROR,
        action_needed=(
            os_permission_needed(
                feature="Device location",
                permission_key="location",
                source="tool.location",
            )
            if result.get("status") == "not_determined"
            else None
        ),
    )
