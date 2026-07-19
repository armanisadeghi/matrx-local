"""Permission check API routes.

Exposes endpoints for the frontend to query device/OS permission status
and to trigger live device probes (e.g. list audio devices, scan WiFi).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.config import TEMP_DIR
from app.services.action_needed import ActionNeededKind, os_permission_needed
from app.services.action_needed.registry import get_action_needed_registry
from app.services.permissions.checker import (
    check_all_permissions,
    grant_windows_permissions,
    request_screen_recording,
    request_engine_permission,
    PERMISSION_CHECKERS,
    PermissionStatus,
    WINDOWS_GRANTABLE_PERMISSIONS,
)
from app.tools.session import ToolSession
from app.tools.tools.audio import tool_list_audio_devices, tool_record_audio
from app.tools.tools.calendar_tools import (
    tool_list_events,
    tool_create_event,
    tool_list_reminders,
    tool_create_reminder,
)
from app.tools.tools.contacts import tool_search_contacts, tool_get_contact
from app.tools.tools.location import tool_get_location
from app.tools.tools.mail import tool_list_emails, tool_send_email, tool_get_email_accounts
from app.tools.tools.messages import tool_list_messages, tool_list_conversations, tool_send_message
from app.tools.tools.photos import tool_search_photos, tool_get_photo
from app.tools.tools.speech_recognition_tools import (
    tool_transcribe_with_speech,
    tool_list_speech_locales,
)
from app.tools.tools.wifi_bluetooth import (
    tool_bluetooth_devices,
    tool_connected_devices,
    tool_wifi_networks,
)
from app.tools.tools.network_discovery import tool_network_info
from app.tools.tools.system import tool_list_screens, tool_screenshot
from app.tools.tools.system_monitor import tool_system_resources

logger = logging.getLogger(__name__)

MEDIA_DIR = TEMP_DIR / "devices"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(prefix="/devices", tags=["devices"])


class RecordAudioRequest(BaseModel):
    device_index: int | None = None
    duration_seconds: int = 5


class CapturePhotoRequest(BaseModel):
    device_index: int | None = None


class ContactSearchRequest(BaseModel):
    query: str | None = None
    limit: int = 25


class CreateEventRequest(BaseModel):
    title: str
    start: str
    end: str
    notes: str | None = None
    calendar: str | None = None
    all_day: bool = False


class CreateReminderRequest(BaseModel):
    title: str
    notes: str | None = None
    due: str | None = None
    list_name: str | None = None


class SendMessageRequest(BaseModel):
    recipient: str
    body: str
    service: str = "iMessage"


class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: str | None = None
    bcc: str | None = None


class TranscribeSpeechRequest(BaseModel):
    audio_path: str
    locale: str = "en-US"
    timeout: float = 60.0


class RecordVideoRequest(BaseModel):
    device_index: int | None = None
    duration_seconds: int = 5


class RecordScreenRequest(BaseModel):
    screen_index: int | None = None
    duration_seconds: int = 5


class GrantPermissionsRequest(BaseModel):
    permissions: list[str] = Field(min_length=1)


async def _run(cmd: list[str], timeout: int = 15) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        # wait_for abandons communicate() but does NOT kill the child —
        # system_profiler/PowerShell/ffmpeg kept running (and their pipes
        # leaked) every time a probe timed out.
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise
    return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode or 0


async def _tool_result_payload(
    result: Any,
    *,
    operation_key: str,
    metadata: dict[str, Any] | None = None,
    force_metadata: bool = False,
) -> dict[str, Any]:
    """Serialize a ToolResult without dropping its remediation contract."""
    action_needed = getattr(result, "action_needed", None)
    await get_action_needed_registry().reconcile_operation(
        operation_key, action_needed
    )
    return {
        "output": result.output,
        "metadata": metadata if force_metadata else result.metadata,
        "type": result.type.value if hasattr(result.type, "value") else str(result.type),
        "action_needed": (
            action_needed.model_dump(mode="json", exclude_none=True)
            if action_needed is not None
            else None
        ),
    }


# ---------------------------------------------------------------------------
# /devices/permissions cache
#
# ``check_all_permissions`` enumerates 15 OS surfaces (microphone, camera,
# bluetooth, wifi, location, contacts, calendar, photos, …). On macOS that
# means several ``system_profiler`` invocations, AVFoundation introspection,
# CoreLocation queries, and a TCC.db read — typical first-call wall time is
# 15–25 s on a cold machine because ``system_profiler`` serialises on the
# private ``cfprefsd`` IPC and warm-cache propagation is per-daemon.
#
# The TCC posture itself is stable: a user toggling a permission requires
# them to leave the app and edit System Settings, so a 30-second freshness
# window is invisible to a real workflow but eliminates the 20-second tax
# on every Devices/Permissions page revisit. The Devices page also calls
# us during initial mount, route changes, and a few other UI events — each
# hit was a fresh 20-second blocker without this cache.
#
# ``force_refresh=True`` bypasses the cache for the rare case where the
# user just toggled a permission and wants to verify. The grant action
# itself ALSO clears the cache so the next read shows the new state.
# ---------------------------------------------------------------------------

_PERM_CACHE_TTL_SECONDS = 30.0
_perm_cache_lock = asyncio.Lock()
_perm_cache: dict[str, Any] = {
    "fetched_at": 0.0,
    "payload": None,  # type: dict[str, Any] | None
}


async def _get_permissions_cached(force_refresh: bool) -> dict[str, Any]:
    """Return the /devices/permissions payload, refreshing if stale.

    A single asyncio lock guards the refresh so a thundering herd of UI
    fetches (the Devices page hits this from multiple panels on mount)
    collapses into one underlying probe. Concurrent callers that arrive
    while a refresh is in-flight will see the new value as soon as it
    lands.
    """
    now = time.monotonic()
    cached = _perm_cache["payload"]
    age = now - _perm_cache["fetched_at"]

    if not force_refresh and cached is not None and age < _PERM_CACHE_TTL_SECONDS:
        return {**cached, "cached": True, "age_seconds": round(age, 2)}

    async with _perm_cache_lock:
        # Re-check inside the lock — another coroutine may have refreshed
        # while we waited.
        now = time.monotonic()
        cached = _perm_cache["payload"]
        age = now - _perm_cache["fetched_at"]
        if not force_refresh and cached is not None and age < _PERM_CACHE_TTL_SECONDS:
            return {**cached, "cached": True, "age_seconds": round(age, 2)}

        results = await check_all_permissions()
        await _resolve_permissions_from_probe(results)
        payload = {
            "permissions": results,
            "platform": PLATFORM["system"],
        }
        _perm_cache["fetched_at"] = time.monotonic()
        _perm_cache["payload"] = payload
        return {**payload, "cached": False, "age_seconds": 0.0}


def _invalidate_permissions_cache() -> None:
    """Drop the cached permissions snapshot so the next read goes fresh.

    Called whenever the engine actively changes the OS permission posture
    (e.g. ``grant_windows_permissions``). Without this, the Devices page
    would still show stale ``denied`` rows for up to ``_PERM_CACHE_TTL_SECONDS``
    after the grant succeeds.
    """
    _perm_cache["fetched_at"] = 0.0
    _perm_cache["payload"] = None


async def _resolve_granted_permissions(permission_names: set[str]) -> None:
    """Clear every backend requirement satisfied by a confirmed OS grant."""
    if not permission_names:
        return
    await get_action_needed_registry().resolve_matching(
        lambda item: (
            item.kind == ActionNeededKind.OS_PERMISSION
            and item.action.permission_key in permission_names
        )
    )


async def _resolve_permissions_from_probe(
    results: list[dict[str, Any]],
) -> None:
    """Reconcile affirmative permission probes with authoritative actions."""
    await _resolve_granted_permissions(
        {
            str(result["permission"])
            for result in results
            if result.get("status") == PermissionStatus.GRANTED.value
            and result.get("permission")
        }
    )


@router.get("/permissions")
async def get_permissions(force_refresh: bool = False):
    """Get all device/OS permission statuses.

    Args:
        force_refresh: bypass the in-process cache and re-probe the OS.
            The cache TTL is short (30 s) but the desktop UI exposes a
            "Refresh" affordance that should always show fresh data — set
            this from there.

    Returns:
        ``{permissions, platform, cached, age_seconds}``. ``cached`` /
        ``age_seconds`` let the UI render a subtle "last checked Ns ago"
        hint without a separate timing endpoint.
    """
    return await _get_permissions_cached(force_refresh=force_refresh)


@router.post("/permissions/grant")
async def grant_permissions(request: GrantPermissionsRequest):
    """Grant explicitly requested permissions for the current platform.

    On Windows: expands each public permission name through a fixed allowlist
    and writes only those HKCU ConsentStore registry keys. Unknown or
    non-grantable permissions fail before any registry mutation.
    HKLM keys (system policy) are not touched — they require UAC elevation.

    On macOS/Linux: returns a no-op success (permissions require user action
    in System Settings / polkit — they cannot be force-granted from a sidecar).
    """
    if PLATFORM["is_windows"]:
        requested = list(dict.fromkeys(request.permissions))
        unsupported = [
            name for name in requested if name not in WINDOWS_GRANTABLE_PERMISSIONS
        ]
        if unsupported:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "unsupported_windows_permission",
                    "permissions": unsupported,
                    "available": list(WINDOWS_GRANTABLE_PERMISSIONS),
                },
            )

        results = await grant_windows_permissions(requested)
        granted = sum(1 for ok in results.values() if ok)
        failed = [k for k, ok in results.items() if not ok]
        # Granting flips the actual OS state — drop the cached snapshot so
        # the next ``GET /devices/permissions`` reflects reality instead
        # of replaying the pre-grant ``denied`` rows for ~30s.
        _invalidate_permissions_cache()
        await _resolve_granted_permissions(
            {name for name, succeeded in results.items() if succeeded}
        )
        return {
            "platform": "windows",
            "permissions": requested,
            "granted": granted,
            "total": len(results),
            "failed": failed,
            "details": results,
        }
    return {
        "platform": PLATFORM["system"],
        "granted": 0,
        "total": 0,
        "failed": [],
        "message": "Permissions on this platform require user action in system settings and cannot be force-granted from the engine.",
    }


@router.post("/permissions/request/screen-recording")
async def request_screen_recording_permission():
    """Ask macOS for Screen Recording and return the resulting status.

    Screen capture happens in the ENGINE process (`screencapture`), so the
    engine is the process that needs the grant — and until it calls
    CGRequestScreenCaptureAccess even once, macOS never lists it under Privacy
    & Security → Screen Recording, leaving the user with a warning and no
    switch to flip. This endpoint is what the UI's "Grant" button calls.

    macOS may require an app restart before the new grant reads back as
    granted; that's TCC cache behaviour, not a failure — hence `restart_hint`.
    """
    result = await request_screen_recording()
    # The OS state may have just changed — a stale cached "not granted" here is
    # exactly the kind of lie this whole fix exists to remove.
    _invalidate_permissions_cache()
    granted = result.status == PermissionStatus.GRANTED
    if granted:
        await _resolve_granted_permissions({"screen_recording"})
    return {
        "permission": "screen_recording",
        "status": result.status.value,
        "granted": granted,
        "user_details": result.user_details,
        "user_instructions": result.user_instructions,
        "deep_link": result.deep_link,
        "restart_hint": not granted and PLATFORM["is_mac"],
    }


@router.post("/permissions/request/{name}")
async def request_permission(name: str):
    """Explicit-click request for grants owned by the Python engine process."""
    requestable = {
        "screen_recording",
        "contacts",
        "calendar",
        "reminders",
        "photos",
        "location",
        "speech_recognition",
    }
    if name not in requestable:
        raise HTTPException(
            status_code=400,
            detail={"code": "permission_not_requestable", "permission": name},
        )
    result = await request_engine_permission(name)
    _invalidate_permissions_cache()
    if result.status == PermissionStatus.GRANTED:
        await _resolve_granted_permissions({name})
    return result.to_dict()


@router.get("/permissions/{name}")
async def get_permission(name: str):
    """Get a single permission status by name."""
    checker = PERMISSION_CHECKERS.get(name)
    if not checker:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "unknown_permission",
                "permission": name,
                "available": list(PERMISSION_CHECKERS),
            },
        )
    result = await checker()
    if result.status == PermissionStatus.GRANTED:
        await _resolve_granted_permissions({name})
    return result.to_dict()


@router.get("/audio")
async def get_audio_devices():
    """List audio input/output devices."""
    session = ToolSession()
    try:
        result = await tool_list_audio_devices(session=session)
        return await _tool_result_payload(result, operation_key="devices.audio")
    finally:
        await session.cleanup()


@router.get("/bluetooth")
async def get_bluetooth_devices():
    """List Bluetooth devices."""
    session = ToolSession()
    try:
        result = await tool_bluetooth_devices(session=session)
        return await _tool_result_payload(result, operation_key="devices.bluetooth")
    finally:
        await session.cleanup()


@router.get("/wifi")
async def get_wifi_networks():
    """List WiFi networks."""
    session = ToolSession()
    try:
        result = await tool_wifi_networks(session=session, rescan=False)
        return await _tool_result_payload(result, operation_key="devices.wifi")
    finally:
        await session.cleanup()


@router.get("/network")
async def get_network_info():
    """Get network interface information."""
    session = ToolSession()
    try:
        result = await tool_network_info(session=session)
        return await _tool_result_payload(result, operation_key="devices.network")
    finally:
        await session.cleanup()


@router.get("/connected")
async def get_connected_devices():
    """List all connected peripherals (USB, Bluetooth, etc.)."""
    session = ToolSession()
    try:
        result = await tool_connected_devices(session=session)
        return await _tool_result_payload(result, operation_key="devices.connected")
    finally:
        await session.cleanup()


@router.get("/camera")
async def get_camera_devices():
    """List available cameras."""
    devices: list[dict[str, Any]] = []
    try:
        if PLATFORM["is_mac"]:
            out, _, _ = await _run(["system_profiler", "SPCameraDataType", "-json"])
            data = json.loads(out)
            for item in data.get("SPCameraDataType", []):
                devices.append({
                    "name": item.get("_name", "Unknown Camera"),
                    "model_id": item.get("spcamera_model-id", ""),
                    "unique_id": item.get("spcamera_unique-id", ""),
                    "index": len(devices),
                })
        elif PLATFORM["is_windows"]:
            out, _, _ = await _run([
                CAPABILITIES["powershell_path"], "-NoProfile", "-Command",
                "Get-PnpDevice -Class Camera -PresentOnly | Select-Object FriendlyName, Status | ConvertTo-Json",
            ])
            cams = json.loads(out) if out.strip() else []
            if isinstance(cams, dict):
                cams = [cams]
            for i, cam in enumerate(cams):
                devices.append({
                    "name": cam.get("FriendlyName", f"Camera {i}"),
                    "status": cam.get("Status", ""),
                    "index": i,
                })
        else:
            import glob as _glob
            for i, vd in enumerate(_glob.glob("/dev/video*")):
                devices.append({"name": vd, "index": i})
    except Exception as e:
        logger.warning("Camera probe failed: %s", e)

    return {
        "output": f"{len(devices)} camera(s) found",
        "metadata": {"devices": devices, "count": len(devices)},
        "type": "success",
    }


@router.get("/screens")
async def get_screens():
    """List all connected monitors with geometry."""
    session = ToolSession()
    try:
        result = await tool_list_screens(session=session)
        return await _tool_result_payload(result, operation_key="devices.screens")
    finally:
        await session.cleanup()


@router.get("/screenshot")
async def take_screenshot(monitor: str = "all"):
    """Take a screenshot and return base64-encoded PNG."""
    session = ToolSession()
    try:
        monitor_val: int | str = monitor
        try:
            monitor_val = int(monitor)
        except (ValueError, TypeError):
            pass
        result = await tool_screenshot(session=session, monitor=monitor_val)
        if result.metadata and result.metadata.get("base64"):
            return await _tool_result_payload(
                result, operation_key=f"devices.screenshot:{monitor}"
            )
        # If tool returned a file path, read and base64-encode it
        if result.metadata and result.metadata.get("path"):
            p = Path(str(result.metadata["path"]))
            if p.exists():
                b64 = base64.b64encode(p.read_bytes()).decode()
                return await _tool_result_payload(
                    result,
                    operation_key=f"devices.screenshot:{monitor}",
                    metadata={**result.metadata, "base64": b64, "mime": "image/png"},
                    force_metadata=True,
                )
        return await _tool_result_payload(
            result, operation_key=f"devices.screenshot:{monitor}"
        )
    finally:
        await session.cleanup()


@router.get("/location")
async def get_location():
    """Get current location through the canonical permission-aware tool."""
    session = ToolSession()
    try:
        result = await tool_get_location(session=session, timeout=15.0)
        return await _tool_result_payload(
            result, operation_key="devices.location"
        )
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Recording endpoints
# ---------------------------------------------------------------------------


async def _permission_preflight_payload(
    *,
    permission_key: str,
    feature: str,
    source: str,
    operation_key: str,
) -> dict[str, Any] | None:
    """Return a blocked response without touching protected hardware."""
    permission = await PERMISSION_CHECKERS[permission_key]()
    if permission.status not in {
        PermissionStatus.DENIED,
        PermissionStatus.NOT_DETERMINED,
        PermissionStatus.RESTRICTED,
    }:
        return None
    action = os_permission_needed(
        feature=feature,
        permission_key=permission_key,
        source=source,
    )
    await get_action_needed_registry().reconcile_operation(operation_key, action)
    return {
        "output": permission.user_instructions
        or f"{permission_key.replace('_', ' ').title()} access is required.",
        "metadata": {"permission_status": permission.status.value},
        "type": "error",
        "action_needed": action.model_dump(mode="json", exclude_none=True),
    }


@router.post("/record-audio")
async def record_audio(req: RecordAudioRequest):
    """Record audio from microphone and return base64-encoded WAV."""
    session = ToolSession()
    try:
        result = await tool_record_audio(
            session=session,
            duration_seconds=req.duration_seconds,
            device_index=req.device_index,
        )
        if result.metadata and result.metadata.get("path"):
            p = Path(str(result.metadata["path"]))
            if p.exists():
                b64 = base64.b64encode(p.read_bytes()).decode()
                return await _tool_result_payload(
                    result,
                    operation_key="devices.record-audio",
                    metadata={**result.metadata, "base64": b64, "mime": "audio/wav"},
                    force_metadata=True,
                )
        return await _tool_result_payload(
            result, operation_key="devices.record-audio"
        )
    finally:
        await session.cleanup()


@router.post("/capture-photo")
async def capture_photo(req: CapturePhotoRequest):
    """Capture a photo from webcam and return base64-encoded JPEG."""
    operation_key = "devices.capture-photo"
    blocked = await _permission_preflight_payload(
        permission_key="camera",
        feature="photo capture",
        source="devices.capture-photo",
        operation_key=operation_key,
    )
    if blocked is not None:
        return blocked
    out_path = MEDIA_DIR / f"photo_{uuid.uuid4().hex[:8]}.jpg"
    try:
        if PLATFORM["is_mac"]:
            # Try imagesnap (brew install imagesnap) first
            if CAPABILITIES["has_imagesnap"]:
                args = ["imagesnap"]
                if req.device_index is not None:
                    # imagesnap can list with -l; use index for device selection indirectly
                    pass
                args.append(str(out_path))
                _, _, rc = await _run(args, timeout=10)
                if rc == 0 and out_path.exists():
                    b64 = base64.b64encode(out_path.read_bytes()).decode()
                    await get_action_needed_registry().reconcile_operation(
                        operation_key, None
                    )
                    return {
                        "output": f"Photo captured to {out_path}",
                        "metadata": {"path": str(out_path), "base64": b64, "mime": "image/jpeg"},
                        "type": "success",
                    }

        # Cross-platform: OpenCV
        try:
            import cv2
            import asyncio as _asyncio

            def _capture() -> bytes | None:
                idx = req.device_index if req.device_index is not None else 0
                cap = cv2.VideoCapture(idx)
                if not cap.isOpened():
                    return None
                # Warm up
                for _ in range(5):
                    cap.read()
                ret, frame = cap.read()
                cap.release()
                if not ret:
                    return None
                _, buf = cv2.imencode(".jpg", frame)
                return bytes(buf)

            data = await _asyncio.get_event_loop().run_in_executor(None, _capture)
            if data:
                out_path.write_bytes(data)
                b64 = base64.b64encode(data).decode()
                await get_action_needed_registry().reconcile_operation(
                    operation_key, None
                )
                return {
                    "output": f"Photo captured ({len(data)} bytes)",
                    "metadata": {"path": str(out_path), "base64": b64, "mime": "image/jpeg"},
                    "type": "success",
                }
        except ImportError:
            pass

        await get_action_needed_registry().reconcile_operation(operation_key, None)
        return {
            "output": "Camera capture requires OpenCV (cv2) or imagesnap (macOS). Install: pip install opencv-python",
            "metadata": None,
            "type": "error",
        }
    except Exception as e:
        await get_action_needed_registry().reconcile_operation(operation_key, None)
        return {"output": f"Photo capture failed: {e}", "metadata": None, "type": "error"}


@router.post("/record-video")
async def record_video(req: RecordVideoRequest):
    """Record a short video from webcam and return base64-encoded MP4."""
    operation_key = "devices.record-video"
    blocked = await _permission_preflight_payload(
        permission_key="camera",
        feature="video recording",
        source="devices.record-video",
        operation_key=operation_key,
    )
    if blocked is not None:
        return blocked
    out_path = MEDIA_DIR / f"video_{uuid.uuid4().hex[:8]}.mp4"
    try:
        import cv2
        import asyncio as _asyncio

        def _record() -> bytes | None:
            idx = req.device_index if req.device_index is not None else 0
            cap = cv2.VideoCapture(idx)
            if not cap.isOpened():
                return None
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
            import time
            start = time.monotonic()
            while (time.monotonic() - start) < req.duration_seconds:
                ret, frame = cap.read()
                if not ret:
                    break
                writer.write(frame)
            cap.release()
            writer.release()
            if out_path.exists():
                return out_path.read_bytes()
            return None

        data = await _asyncio.get_event_loop().run_in_executor(None, _record)
        if data:
            b64 = base64.b64encode(data).decode()
            await get_action_needed_registry().reconcile_operation(operation_key, None)
            return {
                "output": f"Video recorded ({len(data)} bytes, {req.duration_seconds}s)",
                "metadata": {
                    "path": str(out_path),
                    "base64": b64,
                    "mime": "video/mp4",
                    "duration_seconds": req.duration_seconds,
                },
                "type": "success",
            }
        await get_action_needed_registry().reconcile_operation(operation_key, None)
        return {"output": "Video recording failed — camera may not be available", "metadata": None, "type": "error"}
    except ImportError:
        await get_action_needed_registry().reconcile_operation(operation_key, None)
        return {
            "output": "Video recording requires OpenCV (cv2). Install: pip install opencv-python",
            "metadata": None,
            "type": "error",
        }
    except Exception as e:
        await get_action_needed_registry().reconcile_operation(operation_key, None)
        return {"output": f"Video recording failed: {e}", "metadata": None, "type": "error"}


@router.post("/record-screen")
async def record_screen(req: RecordScreenRequest):
    """Record a screen capture video and return base64-encoded MP4."""
    out_path = MEDIA_DIR / f"screen_{uuid.uuid4().hex[:8]}.mp4"
    operation_key = f"devices.record-screen:{req.screen_index or 'primary'}"
    action_registry = get_action_needed_registry()

    # CGPreflightScreenCaptureAccess is read-only. Do not enumerate an
    # AVFoundation screen or invoke mss until the user has explicitly granted
    # the engine access; either capture path can itself trigger native UI.
    if PLATFORM["is_mac"]:
        permission = await PERMISSION_CHECKERS["screen_recording"]()
        if permission.status != PermissionStatus.GRANTED:
            action = os_permission_needed(
                feature="screen recording",
                permission_key="screen_recording",
                source="devices.record-screen",
            )
            await action_registry.reconcile_operation(operation_key, action)
            return {
                "output": permission.user_instructions
                or "Screen Recording access is required before recording.",
                "metadata": {"permission_status": permission.status.value},
                "type": "error",
                "action_needed": action.model_dump(mode="json", exclude_none=True),
            }

    try:
        if CAPABILITIES["has_ffmpeg"]:
            if PLATFORM["is_mac"]:
                # avfoundation enumerates CAMERAS before screens — index 0 is
                # the FaceTime camera on most Macs, so the old `-i "0:none"`
                # recorded the webcam as a "screen recording". Resolve the
                # actual "Capture screen N" device index from the device list.
                screen_n = (req.screen_index - 1) if req.screen_index else 0
                _, list_err, _ = await _run(
                    ["ffmpeg", "-f", "avfoundation",
                     "-list_devices", "true", "-i", ""],
                    timeout=15,
                )
                screen_id: str | None = None
                fallback_first_screen: str | None = None
                for line in list_err.splitlines():
                    m = re.search(r"\[(\d+)\]\s+Capture screen (\d+)", line)
                    if m:
                        if fallback_first_screen is None:
                            fallback_first_screen = m.group(1)
                        if int(m.group(2)) == screen_n:
                            screen_id = m.group(1)
                            break
                screen_id = screen_id or fallback_first_screen
                if screen_id is None:
                    await action_registry.reconcile_operation(operation_key, None)
                    return {
                        "output": "No screen capture device found — grant "
                                  "Screen Recording permission and retry",
                        "metadata": None,
                        "type": "error",
                    }
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "avfoundation",
                    "-framerate", "30",
                    "-i", f"{screen_id}:none",
                    "-t", str(req.duration_seconds),
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    str(out_path),
                ]
            elif PLATFORM["is_windows"]:
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "gdigrab",
                    "-framerate", "30",
                    "-i", "desktop",
                    "-t", str(req.duration_seconds),
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    str(out_path),
                ]
            else:
                # x11grab defaults to the full screen size — the hardcoded
                # 1920x1080 errored on smaller displays ("capture area outside
                # screen"); honor $DISPLAY instead of assuming :0.0.
                display = os.environ.get("DISPLAY", ":0.0")
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "x11grab",
                    "-framerate", "30",
                    "-i", display,
                    "-t", str(req.duration_seconds),
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    str(out_path),
                ]
            _, _, rc = await _run(cmd, timeout=req.duration_seconds + 30)
            if rc == 0 and out_path.exists():
                data = out_path.read_bytes()
                b64 = base64.b64encode(data).decode()
                await action_registry.reconcile_operation(operation_key, None)
                return {
                    "output": f"Screen recording saved ({len(data)} bytes, {req.duration_seconds}s)",
                    "metadata": {
                        "path": str(out_path),
                        "base64": b64,
                        "mime": "video/mp4",
                        "duration_seconds": req.duration_seconds,
                    },
                    "type": "success",
                }

        # Fallback: take a sequence of screenshots via PIL/mss
        try:
            import mss
            import asyncio as _asyncio
            import io
            import time

            def _record_mss() -> bytes | None:
                with mss.mss() as sct:
                    monitor_idx = req.screen_index if req.screen_index else 1
                    if monitor_idx >= len(sct.monitors):
                        monitor_idx = 1
                    mon = sct.monitors[monitor_idx]
                    frames: list[Any] = []
                    start = time.monotonic()
                    while (time.monotonic() - start) < req.duration_seconds:
                        img = sct.grab(mon)
                        frames.append(bytes(img.rgb))
                    # Encode to simple GIF as fallback (requires Pillow)
                    from PIL import Image
                    pil_frames: list[Any] = []
                    for rgb in frames:
                        pil_frames.append(Image.frombytes("RGB", (mon["width"], mon["height"]), rgb))
                    buf = io.BytesIO()
                    pil_frames[0].save(
                        buf, format="GIF", save_all=True,
                        append_images=pil_frames[1:], loop=0, duration=33,
                    )
                    return buf.getvalue()

            data = await _asyncio.get_event_loop().run_in_executor(None, _record_mss)
            if data:
                gif_path = out_path.with_suffix(".gif")
                gif_path.write_bytes(data)
                b64 = base64.b64encode(data).decode()
                await action_registry.reconcile_operation(operation_key, None)
                return {
                    "output": f"Screen recording (GIF fallback, {len(data)} bytes)",
                    "metadata": {
                        "path": str(gif_path),
                        "base64": b64,
                        "mime": "image/gif",
                        "duration_seconds": req.duration_seconds,
                    },
                    "type": "success",
                }
        except ImportError:
            pass

        await action_registry.reconcile_operation(operation_key, None)
        return {
            "output": "Screen recording requires ffmpeg. Install: brew install ffmpeg (macOS) or sudo apt install ffmpeg (Linux)",
            "metadata": None,
            "type": "error",
        }
    except Exception as e:
        await action_registry.reconcile_operation(operation_key, None)
        return {"output": f"Screen recording failed: {e}", "metadata": None, "type": "error"}


@router.get("/system")
async def get_system_resources():
    """Get CPU, RAM, disk, battery status."""
    session = ToolSession()
    try:
        result = await tool_system_resources(session=session)
        return await _tool_result_payload(result, operation_key="devices.system")
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@router.get("/contacts")
async def list_contacts(limit: int = 25):
    """List contacts from macOS Contacts (CNContactStore)."""
    session = ToolSession()
    try:
        result = await tool_search_contacts(session=session, query=None, limit=limit)
        return await _tool_result_payload(result, operation_key="devices.contacts.list")
    finally:
        await session.cleanup()


@router.post("/contacts/search")
async def search_contacts(req: ContactSearchRequest):
    """Search contacts by name or identifier."""
    session = ToolSession()
    try:
        result = await tool_search_contacts(session=session, query=req.query, limit=req.limit)
        return await _tool_result_payload(result, operation_key="devices.contacts.search")
    finally:
        await session.cleanup()


@router.get("/contacts/{identifier}")
async def get_contact(identifier: str):
    """Get a single contact by CNContact identifier."""
    session = ToolSession()
    try:
        result = await tool_get_contact(session=session, identifier=identifier)
        return await _tool_result_payload(
            result, operation_key=f"devices.contacts.get:{identifier}"
        )
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


@router.get("/calendar/events")
async def list_calendar_events(days_ahead: int = 7, limit: int = 50):
    """List upcoming calendar events."""
    session = ToolSession()
    try:
        result = await tool_list_events(session=session, days_ahead=days_ahead, limit=limit)
        return await _tool_result_payload(result, operation_key="devices.calendar.events.list")
    finally:
        await session.cleanup()


@router.post("/calendar/events")
async def create_calendar_event(req: CreateEventRequest):
    """Create a new calendar event."""
    session = ToolSession()
    try:
        result = await tool_create_event(
            session=session,
            title=req.title,
            start=req.start,
            end=req.end,
            notes=req.notes,
            calendar=req.calendar,
            all_day=req.all_day,
        )
        return await _tool_result_payload(result, operation_key="devices.calendar.events.create")
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


@router.get("/calendar/reminders")
async def list_reminders(include_completed: bool = False, limit: int = 50):
    """List reminders from macOS Reminders."""
    session = ToolSession()
    try:
        result = await tool_list_reminders(
            session=session, include_completed=include_completed, limit=limit
        )
        return await _tool_result_payload(result, operation_key="devices.reminders.list")
    finally:
        await session.cleanup()


@router.post("/calendar/reminders")
async def create_reminder(req: CreateReminderRequest):
    """Create a new reminder."""
    session = ToolSession()
    try:
        result = await tool_create_reminder(
            session=session,
            title=req.title,
            notes=req.notes,
            due=req.due,
            list_name=req.list_name,
        )
        return await _tool_result_payload(result, operation_key="devices.reminders.create")
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------


@router.get("/photos")
async def list_photos(media_type: str = "image", limit: int = 25, favorites_only: bool = False):
    """List photo/video assets from macOS Photos library."""
    session = ToolSession()
    try:
        result = await tool_search_photos(
            session=session, media_type=media_type, limit=limit, favorites_only=favorites_only
        )
        return await _tool_result_payload(result, operation_key="devices.photos.list")
    finally:
        await session.cleanup()


@router.get("/photos/{identifier}")
async def get_photo(identifier: str, thumbnail_size: int = 512):
    """Get a single photo asset with thumbnail by identifier."""
    session = ToolSession()
    try:
        result = await tool_get_photo(
            session=session, identifier=identifier, thumbnail_size=thumbnail_size
        )
        return await _tool_result_payload(
            result, operation_key=f"devices.photos.get:{identifier}"
        )
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Location (override the existing inline implementation)
# ---------------------------------------------------------------------------


@router.get("/location/current")
async def get_current_location(timeout: float = 15.0):
    """Get current device location via CoreLocation."""
    session = ToolSession()
    try:
        result = await tool_get_location(session=session, timeout=timeout)
        return await _tool_result_payload(result, operation_key="devices.location.current")
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@router.get("/messages")
async def list_messages_endpoint(limit: int = 50, contact: str | None = None, unread_only: bool = False):
    """List recent iMessage/SMS messages (requires Full Disk Access)."""
    session = ToolSession()
    try:
        result = await tool_list_messages(
            session=session, limit=limit, contact=contact, unread_only=unread_only
        )
        return await _tool_result_payload(result, operation_key="devices.messages.list")
    finally:
        await session.cleanup()


@router.get("/messages/conversations")
async def list_conversations_endpoint(limit: int = 25):
    """List recent iMessage/SMS conversations with last message preview."""
    session = ToolSession()
    try:
        result = await tool_list_conversations(session=session, limit=limit)
        return await _tool_result_payload(
            result, operation_key="devices.messages.conversations"
        )
    finally:
        await session.cleanup()


@router.post("/messages/send")
async def send_message_endpoint(req: SendMessageRequest):
    """Send an iMessage or SMS via Messages.app (requires Automation permission)."""
    session = ToolSession()
    try:
        result = await tool_send_message(
            session=session,
            recipient=req.recipient,
            body=req.body,
            service=req.service,
        )
        return await _tool_result_payload(result, operation_key="devices.messages.send")
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------


@router.get("/mail/accounts")
async def get_mail_accounts():
    """List configured Mail.app accounts and their mailboxes."""
    session = ToolSession()
    try:
        result = await tool_get_email_accounts(session=session)
        return await _tool_result_payload(result, operation_key="devices.mail.accounts")
    finally:
        await session.cleanup()


@router.get("/mail")
async def list_mail(mailbox: str = "INBOX", limit: int = 25, unread_only: bool = False):
    """List recent emails from Mail.app (requires Automation permission)."""
    session = ToolSession()
    try:
        result = await tool_list_emails(
            session=session, mailbox=mailbox, limit=limit, unread_only=unread_only
        )
        return await _tool_result_payload(result, operation_key=f"devices.mail.list:{mailbox}")
    finally:
        await session.cleanup()


@router.post("/mail/send")
async def send_mail(req: SendEmailRequest):
    """Send an email via Mail.app (requires Automation permission)."""
    session = ToolSession()
    try:
        result = await tool_send_email(
            session=session,
            to=req.to,
            subject=req.subject,
            body=req.body,
            cc=req.cc,
            bcc=req.bcc,
        )
        return await _tool_result_payload(result, operation_key="devices.mail.send")
    finally:
        await session.cleanup()


# ---------------------------------------------------------------------------
# Speech Recognition
# ---------------------------------------------------------------------------


@router.post("/speech/transcribe")
async def transcribe_with_speech(req: TranscribeSpeechRequest):
    """Transcribe an audio file using Apple's on-device SFSpeechRecognizer."""
    session = ToolSession()
    try:
        result = await tool_transcribe_with_speech(
            session=session,
            audio_path=req.audio_path,
            locale=req.locale,
            timeout=req.timeout,
        )
        return await _tool_result_payload(result, operation_key="devices.speech.transcribe")
    finally:
        await session.cleanup()


@router.get("/speech/locales")
async def list_speech_locales():
    """List all locales supported by SFSpeechRecognizer on this device."""
    session = ToolSession()
    try:
        result = await tool_list_speech_locales(session=session)
        return await _tool_result_payload(result, operation_key="devices.speech.locales")
    finally:
        await session.cleanup()
