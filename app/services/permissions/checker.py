"""Cross-platform permission and device capability checker.

Probes the OS for permission status of microphone, camera, accessibility,
bluetooth, location, and network access.  Returns structured results so the
frontend can display real status and guide users to grant missing permissions.

Windows permission model
------------------------
Windows privacy settings live in the registry under:
  HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\<key>
  HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\<key>

Rules:
  - HKCU = per-user setting (writable without elevation → we can force Allow)
  - HKLM = system-wide override (requires UAC to write; we only read it)
  - Effective status = HKLM "Deny" overrides any HKCU value; otherwise HKCU wins

Keys we can force-set (HKCU, no elevation needed):
  microphone, webcam, location, bluetooth, bluetoothSync, contacts,
  appointments, userDataTasks, chat, email, userAccountInformation,
  phoneCall, phoneCallHistory, radios, broadFileSystemAccess,
  picturesLibrary, videosLibrary, documentsLibrary, musicLibrary,
  activity, appDiagnostics, wifiData, wiFiDirect, humanInterfaceDevice,
  usb, serialCommunication, gazeInput, graphicsCaptureWithoutBorder,
  userNotificationListener

Device enumeration is done via Get-PnpDevice / Win32_SoundDevice (PowerShell)
because sounddevice's PortAudio backend is unreliable when running as a
PyInstaller sidecar without a desktop session.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.common.process_shutdown import process_shutdown_requested

logger = logging.getLogger(__name__)


class PermissionStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    NOT_DETERMINED = "not_determined"
    RESTRICTED = "restricted"  # Parental controls / MDM
    UNAVAILABLE = "unavailable"  # Hardware not present
    UNKNOWN = "unknown"


@dataclass
class PermissionResult:
    permission: str
    status: PermissionStatus
    details: str = ""
    grant_instructions: str = ""
    user_details: str = ""
    user_instructions: str = ""
    fixable: bool = False
    fix_capability_id: str | None = None
    devices: list[dict[str, Any]] = field(default_factory=list)

    # macOS deep link for this permission's settings pane
    deep_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "permission": self.permission,
            "status": self.status.value,
            "details": self.details,
            "grant_instructions": self.grant_instructions,
            "user_details": self.user_details,
            "user_instructions": self.user_instructions,
            "fixable": self.fixable,
            "fix_capability_id": self.fix_capability_id,
            "deep_link": self.deep_link,
        }
        if self.devices:
            d["devices"] = self.devices
        return d


# ---------------------------------------------------------------------------
# macOS helpers
# ---------------------------------------------------------------------------


async def _run(cmd: list[str], timeout: int = 10) -> tuple[str, str, int]:
    """Run a subprocess and return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Both an internal timeout and an outer permission-scan deadline cancel
        # communicate() without killing the child. Always reap the OS probe.
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise
    return (
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        proc.returncode or 0,
    )


# ---------------------------------------------------------------------------
# Windows registry helpers
# ---------------------------------------------------------------------------

_WIN_CONSENT_HKCU = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
)
_WIN_CONSENT_HKLM = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
)

# Public permission names mapped to the *specific* Windows ConsentStore keys
# needed by that permission.  Keep this allowlist semantic: callers request a
# Matrx permission, never an arbitrary registry key.  Some entries expand to
# two closely-related capabilities because Windows splits one user-facing
# permission across multiple ConsentStore records.
WINDOWS_GRANTABLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "microphone": ("microphone",),
    "camera": ("webcam",),
    "bluetooth": ("bluetooth", "bluetoothSync"),
    "wifi": ("wifiData", "wiFiDirect"),
    "screen_recording": ("graphicsCaptureWithoutBorder",),
    "location": ("location",),
    "contacts": ("contacts",),
    "calendar": ("appointments",),
    "reminders": ("userDataTasks",),
    "photos": ("picturesLibrary", "videosLibrary"),
    "messages": ("chat",),
    "mail": ("email",),
    "speech_recognition": ("microphone",),
}


def _win_consent_status(key: str) -> PermissionStatus:
    """Read the effective Windows privacy consent status for a capability key.

    Reads both HKCU (user) and HKLM (system) registry values.
    HKLM "Deny" overrides HKCU — returns DENIED.
    Otherwise uses HKCU: "Allow" → GRANTED, "Deny" → DENIED, missing → NOT_DETERMINED.
    """
    try:
        import winreg
        hkcu_val: str | None = None
        hklm_val: str | None = None

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{_WIN_CONSENT_HKCU}\\{key}") as k:
                hkcu_val, _ = winreg.QueryValueEx(k, "Value")
        except OSError:
            pass

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{_WIN_CONSENT_HKLM}\\{key}") as k:
                hklm_val, _ = winreg.QueryValueEx(k, "Value")
        except OSError:
            pass

        # System policy overrides user setting
        if hklm_val and hklm_val.lower() == "deny":
            return PermissionStatus.DENIED
        if hkcu_val is None:
            return PermissionStatus.NOT_DETERMINED
        return PermissionStatus.GRANTED if hkcu_val.lower() == "allow" else PermissionStatus.DENIED
    except Exception:
        return PermissionStatus.UNKNOWN


def _win_force_allow(key: str) -> bool:
    """Force-set a Windows privacy consent key to Allow in HKCU (no elevation needed).

    Returns True on success, False if the key could not be written.
    Creates the registry key if it doesn't exist.
    """
    try:
        import winreg
        full_path = f"{_WIN_CONSENT_HKCU}\\{key}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, full_path,
                                access=winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "Value", 0, winreg.REG_SZ, "Allow")
        return True
    except Exception as exc:
        logger.debug("_win_force_allow(%s) failed: %s", key, exc)
        return False


async def grant_windows_permissions(permission_names: Iterable[str]) -> dict[str, bool]:
    """Grant only the explicitly requested Windows permissions.

    Each public permission name is expanded through
    :data:`WINDOWS_GRANTABLE_PERMISSIONS`, then only those allowlisted HKCU
    ConsentStore keys are written.  Unknown names fail before any mutation.
    Runs registry writes in a thread pool and returns ``{registry_key: success}``.

    Keys that require HKLM (system-level) access are NOT touched here because
    they require UAC elevation which cannot be obtained from a background sidecar.
    On a well-configured personal machine the HKLM values are already "Allow".
    """
    requested = list(dict.fromkeys(permission_names))
    unknown = [name for name in requested if name not in WINDOWS_GRANTABLE_PERMISSIONS]
    if unknown:
        raise ValueError(f"Unsupported Windows permission(s): {', '.join(unknown)}")

    consent_keys = list(
        dict.fromkeys(
            key
            for name in requested
            for key in WINDOWS_GRANTABLE_PERMISSIONS[name]
        )
    )
    loop = asyncio.get_running_loop()

    def _force_requested() -> dict[str, bool]:
        return {key: _win_force_allow(key) for key in consent_keys}

    return await loop.run_in_executor(None, _force_requested)


async def _win_enum_audio_endpoints() -> list[dict[str, Any]]:
    """Enumerate Windows audio endpoints via PowerShell Get-PnpDevice.

    Uses AudioEndpoint class (covers both input and output devices) rather than
    sounddevice/PortAudio, which is unreliable when running as a sidecar without
    a desktop audio session.  Filters to input devices by name heuristics.
    """
    ps = CAPABILITIES.get("powershell_path")
    if not ps:
        return []
    try:
        out, _, rc = await _run([
            ps, "-NoProfile", "-Command",
            "Get-PnpDevice -Class AudioEndpoint -PresentOnly -ErrorAction SilentlyContinue"
            " | Select-Object FriendlyName, Status | ConvertTo-Json -Compress",
        ])
        if rc != 0 or not out.strip():
            return []
        devices = json.loads(out)
        if isinstance(devices, dict):
            devices = [devices]
        return [d for d in devices if isinstance(d, dict)]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Microphone
# ---------------------------------------------------------------------------


async def check_microphone() -> PermissionResult:
    """Check microphone access and list available input devices."""
    devices: list[dict[str, Any]] = []

    # Try to list audio input devices.
    # On macOS we enumerate via system_profiler so the device list is available
    # even before TCC permission is granted (sounddevice may return an empty list
    # or raise when mic access is denied — it is NOT a reliable permission proxy).
    if PLATFORM["is_mac"]:
        try:
            out, _, _ = await _run(["system_profiler", "SPAudioDataType", "-json"])
            data = json.loads(out)
            for item in data.get("SPAudioDataType", []):
                for sub in item.get("_items", [item]):
                    name = sub.get("_name", "")
                    if name:
                        devices.append({"name": name, "type": "system_profiler"})
        except Exception:
            pass
    else:
        # Windows / Linux: sounddevice is reliable for device enumeration
        try:
            import sounddevice as sd

            all_devs = sd.query_devices()
            for i, dev in enumerate(all_devs):
                if dev["max_input_channels"] > 0:
                    devices.append(
                        {
                            "index": i,
                            "name": dev["name"],
                            "channels": dev["max_input_channels"],
                            "sample_rate": dev["default_samplerate"],
                        }
                    )
        except ImportError:
            try:
                if PLATFORM["is_linux"]:
                    out, _, rc = await _run(["arecord", "-l"])
                    if rc == 0:
                        for line in out.split("\n"):
                            if line.startswith("card"):
                                devices.append({"name": line.strip()})
            except Exception:
                pass
        except Exception:
            pass

    _MIC_DEEP_LINK = (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        if PLATFORM["is_mac"] else ""
    )

    # ── Windows: registry-first approach ─────────────────────────────────────
    if PLATFORM["is_windows"]:
        status = _win_consent_status("microphone")

        # Enumerate via PnP AudioEndpoint (reliable from sidecar, unlike sounddevice).
        all_endpoints = await _win_enum_audio_endpoints()
        # Filter to input devices by name heuristics: exclude known output-only patterns.
        _OUTPUT_HINTS = ("speaker", "headphone", "output", "s/pdif", "digital audio",
                         "nvidia high def", "hdmi", "displayport", "dell")
        input_devices = [
            d for d in all_endpoints
            if not any(h in d.get("FriendlyName", "").lower() for h in _OUTPUT_HINTS)
        ]
        if not input_devices and all_endpoints:
            # If heuristic filtered everything out (unusual system), include all
            input_devices = all_endpoints

        if not input_devices:
            # Try sounddevice as fallback
            try:
                import sounddevice as sd
                for i, dev in enumerate(sd.query_devices()):
                    if dev["max_input_channels"] > 0:
                        input_devices.append({
                            "FriendlyName": dev["name"],
                            "Status": "OK",
                            "index": i,
                            "channels": dev["max_input_channels"],
                        })
            except Exception:
                pass

        if not input_devices:
            return PermissionResult(
                permission="microphone",
                status=PermissionStatus.UNAVAILABLE,
                details="No microphone devices detected",
                grant_instructions=_microphone_instructions(),
                user_details="No microphone found — connect one to use voice features",
                user_instructions="Connect a microphone and check Settings > Privacy > Microphone",
            )

        devices = [{"name": d.get("FriendlyName", ""), "status": d.get("Status", "OK")}
                   for d in input_devices]
        return PermissionResult(
            permission="microphone",
            status=status,
            details=f"{len(devices)} microphone(s) found",
            devices=devices,
            grant_instructions=_microphone_instructions(),
            user_details=f"Microphone is active — {len(devices)} device(s) detected"
            if status == PermissionStatus.GRANTED
            else "Microphone access denied by system policy (requires admin)",
            user_instructions=""
            if status == PermissionStatus.GRANTED
            else "A system administrator has blocked microphone access",
        )

    if not devices:
        return PermissionResult(
            permission="microphone",
            status=PermissionStatus.UNAVAILABLE,
            details="No audio input devices detected",
            grant_instructions=_microphone_instructions(),
            user_details="Audio recording is not available yet",
            user_instructions="Click Fix It to enable audio support",
            fixable=True,
            fix_capability_id="audio_recording",
            deep_link=_MIC_DEEP_LINK,
        )

    # On macOS, try a quick non-blocking permission probe
    if PLATFORM["is_mac"]:
        status = await _macos_check_tcc("kTCCServiceMicrophone", "Microphone")
        return PermissionResult(
            permission="microphone",
            status=status,
            details=f"{len(devices)} input device(s) found",
            devices=devices,
            grant_instructions=_microphone_instructions(),
            user_details=f"Microphone ready — {len(devices)} device(s) detected"
            if status == PermissionStatus.GRANTED
            else "Microphone access needs permission",
            user_instructions=""
            if status == PermissionStatus.GRANTED
            else "Open System Settings > Privacy & Security > Microphone",
            deep_link=_MIC_DEEP_LINK,
        )

    # On Linux, if devices are listed the OS generally allows access
    return PermissionResult(
        permission="microphone",
        status=PermissionStatus.GRANTED,
        details=f"{len(devices)} input device(s) found",
        devices=devices,
        grant_instructions=_microphone_instructions(),
        user_details=f"Microphone is active — {len(devices)} device(s) detected",
        user_instructions="",
    )


def _microphone_instructions() -> str:
    if PLATFORM["is_mac"]:
        return "System Settings > Privacy & Security > Microphone > Enable for Matrx Local (or Terminal)"
    elif PLATFORM["is_windows"]:
        return "Settings > Privacy > Microphone > Allow apps to access your microphone"
    return "Ensure your user is in the 'audio' group: sudo usermod -aG audio $USER"


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


async def check_camera() -> PermissionResult:
    """Check camera availability."""
    devices: list[dict[str, Any]] = []

    if PLATFORM["is_mac"]:
        try:
            out, _, _ = await _run(["system_profiler", "SPCameraDataType", "-json"])
            data = json.loads(out)
            for item in data.get("SPCameraDataType", []):
                devices.append(
                    {
                        "name": item.get("_name", "Unknown"),
                        "model_id": item.get("spcamera_model-id", ""),
                        "unique_id": item.get("spcamera_unique-id", ""),
                    }
                )
        except Exception:
            pass

        status = await _macos_check_tcc("kTCCServiceCamera", "Camera")
        _cam_ok = status == PermissionStatus.GRANTED
        return PermissionResult(
            permission="camera",
            status=status,
            details=f"{len(devices)} camera(s) found"
            if devices
            else "No cameras detected",
            devices=devices,
            grant_instructions="System Settings > Privacy & Security > Camera > Enable for Matrx Local",
            user_details=f"Camera is active — {len(devices)} camera(s) detected"
            if _cam_ok
            else "Camera access needs permission",
            user_instructions=""
            if _cam_ok
            else "Open System Settings > Privacy & Security > Camera",
            deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_Camera",
        )

    elif PLATFORM["is_windows"]:
        # Check registry consent status first
        cam_status = _win_consent_status("webcam")

        ps = CAPABILITIES.get("powershell_path")
        if ps:
            try:
                out, _, _ = await _run([
                    ps, "-NoProfile", "-Command",
                    "Get-PnpDevice -Class Camera -PresentOnly -ErrorAction SilentlyContinue"
                    " | Select-Object FriendlyName, Status | ConvertTo-Json -Compress",
                ])
                cams = json.loads(out) if out.strip() else []
                if isinstance(cams, dict):
                    cams = [cams]
                for cam in cams:
                    devices.append({
                        "name": cam.get("FriendlyName", ""),
                        "status": cam.get("Status", ""),
                    })
            except Exception:
                pass

        effective_status = cam_status if devices else PermissionStatus.UNAVAILABLE
        return PermissionResult(
            permission="camera",
            status=effective_status,
            details=f"{len(devices)} camera(s) found" if devices else "No cameras detected",
            devices=devices,
            grant_instructions="Settings > Privacy > Camera > Allow apps to access your camera",
            user_details=f"Camera is active — {len(devices)} camera(s) detected"
            if devices
            else "No camera detected",
            user_instructions="" if devices else "Connect a camera to get started",
        )

    else:  # Linux
        try:
            import glob as _glob

            video_devs = _glob.glob("/dev/video*")
            for vd in video_devs:
                devices.append({"name": vd})
        except Exception:
            pass

        return PermissionResult(
            permission="camera",
            status=PermissionStatus.GRANTED
            if devices
            else PermissionStatus.UNAVAILABLE,
            details=f"{len(devices)} video device(s) found"
            if devices
            else "No cameras detected",
            devices=devices,
            grant_instructions="Ensure your user is in the 'video' group: sudo usermod -aG video $USER",
            user_details=f"Camera is active — {len(devices)} device(s) detected"
            if devices
            else "No camera detected",
            user_instructions="" if devices else "Connect a camera to get started",
        )


# ---------------------------------------------------------------------------
# Screen Recording / Accessibility
# ---------------------------------------------------------------------------


async def check_accessibility() -> PermissionResult:
    """Check accessibility / screen recording permissions (mostly macOS)."""
    if PLATFORM["is_mac"]:
        # Use AXIsProcessTrusted via ctypes — this checks the calling process
        # directly, unlike osascript which spawns a child with a different TCC
        # identity that can give misleading results.
        try:
            import ctypes
            import ctypes.util

            lib_path = ctypes.util.find_library("ApplicationServices")
            if not lib_path:
                lib_path = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            app_services = ctypes.cdll.LoadLibrary(lib_path)
            app_services.AXIsProcessTrusted.restype = ctypes.c_bool
            trusted = app_services.AXIsProcessTrusted()

            _ax_deep_link = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            if trusted:
                return PermissionResult(
                    permission="accessibility",
                    status=PermissionStatus.GRANTED,
                    details="Accessibility access confirmed (AXIsProcessTrusted)",
                    grant_instructions=_accessibility_instructions(),
                    user_details="Automation and accessibility features are active",
                    user_instructions="",
                    deep_link=_ax_deep_link,
                )
            return PermissionResult(
                permission="accessibility",
                status=PermissionStatus.DENIED,
                details="Accessibility access not granted",
                grant_instructions=_accessibility_instructions(),
                user_details="Automation features need permission",
                user_instructions="Open System Settings > Privacy & Security > Accessibility",
                deep_link=_ax_deep_link,
            )
        except Exception:
            pass

        _ax_deep_link = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

        # Fallback: try osascript probe
        try:
            out, err, rc = await _run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first process whose frontmost is true',
                ]
            )
            if rc == 0:
                return PermissionResult(
                    permission="accessibility",
                    status=PermissionStatus.GRANTED,
                    details=f"Accessibility access confirmed (frontmost: {out.strip()})",
                    grant_instructions=_accessibility_instructions(),
                    user_details="Automation and accessibility features are active",
                    user_instructions="",
                    deep_link=_ax_deep_link,
                )
            if (
                "-1743" in err
                or "-25211" in err
                or "not authorized" in err.lower()
                or "assistive" in err.lower()
            ):
                return PermissionResult(
                    permission="accessibility",
                    status=PermissionStatus.DENIED,
                    details="Accessibility access denied by macOS",
                    grant_instructions=_accessibility_instructions(),
                    user_details="Automation features need permission",
                    user_instructions="Open System Settings > Privacy & Security > Accessibility",
                    deep_link=_ax_deep_link,
                )
        except Exception:
            pass

        return PermissionResult(
            permission="accessibility",
            status=PermissionStatus.UNKNOWN,
            details="Could not determine accessibility status (normal when running as background service)",
            grant_instructions=_accessibility_instructions(),
            user_details="Automation features status is unknown",
            user_instructions="Open System Settings to check accessibility permissions",
            deep_link=_ax_deep_link,
        )

    elif PLATFORM["is_windows"]:
        # Windows generally doesn't restrict accessibility for desktop apps
        return PermissionResult(
            permission="accessibility",
            status=PermissionStatus.GRANTED,
            details="Windows does not restrict accessibility for desktop applications",
            grant_instructions="No action needed on Windows",
            user_details="Automation and accessibility features are active",
            user_instructions="",
        )

    else:
        # Linux: check for xdotool or wmctrl availability
        has_xdotool = CAPABILITIES["has_xdotool"]
        has_wmctrl = CAPABILITIES["has_wmctrl"]
        if has_xdotool or has_wmctrl:
            return PermissionResult(
                permission="accessibility",
                status=PermissionStatus.GRANTED,
                details=f"Tools available: {'xdotool' if has_xdotool else ''} {'wmctrl' if has_wmctrl else ''}".strip(),
                grant_instructions="Install xdotool and wmctrl: sudo apt install xdotool wmctrl",
                user_details="Automation and accessibility features are active",
                user_instructions="",
            )
        return PermissionResult(
            permission="accessibility",
            status=PermissionStatus.DENIED,
            details="No accessibility tools found (xdotool / wmctrl)",
            grant_instructions="Install xdotool and wmctrl: sudo apt install xdotool wmctrl",
            user_details="Automation tools are not set up yet",
            user_instructions="Additional system tools are needed — see details for setup steps",
        )


def _accessibility_instructions() -> str:
    if PLATFORM["is_mac"]:
        return (
            "System Settings > Privacy & Security > Accessibility > "
            "Enable for Matrx Local (or Terminal). "
            "Also check: Privacy & Security > Screen Recording for screen capture."
        )
    return ""


# ---------------------------------------------------------------------------
# Bluetooth
# ---------------------------------------------------------------------------


async def check_bluetooth() -> PermissionResult:
    """Check Bluetooth availability and permission."""
    devices: list[dict[str, Any]] = []

    if PLATFORM["is_mac"]:
        try:
            out, _, _ = await _run(["system_profiler", "SPBluetoothDataType", "-json"])
            data = json.loads(out)
            bt_data = data.get("SPBluetoothDataType", [{}])[0]

            # Check if Bluetooth is enabled
            controller = bt_data.get("controller_properties", {})
            bt_power = controller.get("controller_state", "")

            for section_key in ["device_connected", "device_not_connected"]:
                section = bt_data.get(section_key, [])
                if isinstance(section, list):
                    for item in section:
                        if isinstance(item, dict):
                            for name, info in item.items():
                                devices.append(
                                    {
                                        "name": name,
                                        "address": info.get("device_address", ""),
                                        "type": info.get("device_minorType", ""),
                                        "connected": section_key == "device_connected",
                                    }
                                )

            is_on = "attrib_on" in bt_power.lower() if bt_power else len(devices) > 0
            return PermissionResult(
                permission="bluetooth",
                status=PermissionStatus.GRANTED if is_on else PermissionStatus.DENIED,
                details=f"Bluetooth {'on' if is_on else 'off'}, {len(devices)} device(s) paired",
                devices=devices,
                grant_instructions="System Settings > Bluetooth > Turn On. Also: Privacy & Security > Bluetooth > Enable for Matrx Local",
                user_details=f"Bluetooth is active — {len(devices)} device(s) paired"
                if is_on
                else "Bluetooth is turned off",
                user_instructions=""
                if is_on
                else "Turn on Bluetooth in System Settings",
            )
        except Exception as e:
            logger.debug("Bluetooth check failed: %s", e)

    elif PLATFORM["is_windows"]:
        # Consent checks are passive. Registry writes happen only through the
        # explicit grant endpoint after a user action.
        bt_status = _win_consent_status("bluetooth")

        ps = CAPABILITIES.get("powershell_path")
        if ps:
            try:
                # Filter to actual user-facing BT devices: exclude LE service noise,
                # protocol adapters, and Microsoft infrastructure entries.
                out, _, rc = await _run([
                    ps, "-NoProfile", "-Command",
                    "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue"
                    " | Where-Object {"
                    "   $_.FriendlyName -ne $null -and"
                    "   $_.FriendlyName -notmatch 'LE Generic|Protocol|Enumerator|Profile|Service|Device Information'"
                    " }"
                    " | Select-Object FriendlyName, Status | ConvertTo-Json -Compress",
                ])
                if rc == 0 and out.strip():
                    bt_devs = json.loads(out)
                    if isinstance(bt_devs, dict):
                        bt_devs = [bt_devs]
                    for d in bt_devs:
                        name = d.get("FriendlyName", "")
                        if name:
                            devices.append({"name": name, "status": d.get("Status", "")})
            except Exception:
                pass

        # If we have devices, Bluetooth is clearly on regardless of registry
        if devices:
            bt_status = PermissionStatus.GRANTED

        return PermissionResult(
            permission="bluetooth",
            status=bt_status if devices else (
                PermissionStatus.UNAVAILABLE if bt_status == PermissionStatus.NOT_DETERMINED
                else bt_status
            ),
            details=f"{len(devices)} Bluetooth device(s) found" if devices else "No Bluetooth devices found",
            devices=devices,
            grant_instructions="Settings > Bluetooth & devices > Turn on Bluetooth",
            user_details=f"Bluetooth is active — {len(devices)} device(s) found"
            if devices else "No Bluetooth devices found or Bluetooth is off",
            user_instructions="" if devices else "Turn on Bluetooth in Settings",
        )

    else:  # Linux
        if CAPABILITIES["has_bluetoothctl"]:
            try:
                out, _, rc = await _run(["bluetoothctl", "devices"])
                if rc == 0:
                    import re

                    for line in out.strip().split("\n"):
                        match = re.match(r"Device\s+([\dA-Fa-f:]+)\s+(.+)", line)
                        if match:
                            devices.append(
                                {"name": match.group(2), "address": match.group(1)}
                            )
                return PermissionResult(
                    permission="bluetooth",
                    status=PermissionStatus.GRANTED,
                    details=f"{len(devices)} paired device(s)",
                    devices=devices,
                    grant_instructions="Ensure bluetooth service is running: sudo systemctl enable --now bluetooth",
                    user_details=f"Bluetooth is active — {len(devices)} paired device(s)",
                    user_instructions="",
                )
            except Exception:
                pass
        else:
            return PermissionResult(
                permission="bluetooth",
                status=PermissionStatus.UNAVAILABLE,
                details="bluetoothctl not found",
                grant_instructions="Install bluez: sudo apt install bluez",
                user_details="Bluetooth is not available",
                user_instructions="Bluetooth support is not installed on this system",
            )

    return PermissionResult(
        permission="bluetooth",
        status=PermissionStatus.UNKNOWN,
        details="Could not determine Bluetooth status",
        grant_instructions="Check your system's Bluetooth settings",
        user_details="Bluetooth status is unknown",
        user_instructions="Check your system's Bluetooth settings",
    )


# ---------------------------------------------------------------------------
# WiFi / Network
# ---------------------------------------------------------------------------


async def check_network() -> PermissionResult:
    """Check network interfaces and connectivity."""
    import socket as _socket

    details_parts: list[str] = []
    interfaces: list[dict[str, Any]] = []

    try:
        import psutil

        if_addrs = psutil.net_if_addrs()
        if_stats = psutil.net_if_stats()

        for iface_name, addrs in if_addrs.items():
            stat = if_stats.get(iface_name)
            iface_info: dict[str, Any] = {
                "name": iface_name,
                "is_up": stat.isup if stat else False,
                "speed_mbps": stat.speed if stat else 0,
                "type": _classify_iface(iface_name),
            }
            for addr in addrs:
                if addr.family == _socket.AF_INET:
                    iface_info["ipv4"] = addr.address
                elif addr.family == _socket.AF_INET6:
                    iface_info.setdefault("ipv6", addr.address)
                elif hasattr(_socket, "AF_LINK") and addr.family == _socket.AF_LINK:
                    iface_info["mac"] = addr.address
                elif addr.family == -1 or (hasattr(psutil, "AF_LINK") and addr.family == psutil.AF_LINK):
                    iface_info["mac"] = addr.address
            if iface_info["is_up"] or "ipv4" in iface_info:
                interfaces.append(iface_info)

        details_parts.append(f"{len(interfaces)} active interface(s)")
    except ImportError:
        details_parts.append("psutil not available")
    except Exception as e:
        details_parts.append(f"Error: {e}")

    # Check WiFi specifically
    wifi_ok = False
    if PLATFORM["is_mac"]:
        try:
            out, _, rc = await _run(["networksetup", "-getairportpower", "en0"])
            wifi_ok = "on" in out.lower()
            details_parts.append(f"WiFi {'on' if wifi_ok else 'off'}")
        except Exception:
            pass
    elif PLATFORM["is_linux"]:
        if CAPABILITIES["has_nmcli"]:
            try:
                out, _, rc = await _run(["nmcli", "radio", "wifi"])
                wifi_ok = "enabled" in out.lower()
                details_parts.append(f"WiFi {'enabled' if wifi_ok else 'disabled'}")
            except Exception:
                pass
    elif PLATFORM["is_windows"]:
        try:
            out, _, rc = await _run(["netsh", "wlan", "show", "interfaces"])
            wifi_ok = "connected" in out.lower()
            details_parts.append(f"WiFi {'connected' if wifi_ok else 'available'}")
        except Exception:
            pass
    details_parts.append(f"Internet {'reachable' if wifi_ok else 'status unknown'}")

    # Basic internet check
    internet_ok = False
    try:
        sock = _socket.create_connection(("1.1.1.1", 53), timeout=3)
        sock.close()
        internet_ok = True
        details_parts[-1] = "Internet reachable"
    except Exception:
        details_parts[-1] = "Internet unreachable"

    status = PermissionStatus.GRANTED if interfaces else PermissionStatus.DENIED
    _net_fixable = "psutil not available" in ", ".join(details_parts)
    return PermissionResult(
        permission="network",
        status=status,
        details=", ".join(details_parts),
        devices=interfaces,  # list of interface dicts (NOT WiFi networks)
        grant_instructions=_network_instructions(),
        user_details=f"Network is active — {len(interfaces)} interface(s), internet {'reachable' if internet_ok else 'unreachable'}"
        if interfaces
        else "Network monitoring is limited",
        user_instructions=""
        if interfaces
        else (
            "Click Fix It to enable full network monitoring"
            if _net_fixable
            else "Check your network connection"
        ),
        fixable=_net_fixable,
        fix_capability_id="system_monitoring" if _net_fixable else None,
    )


def _classify_iface(name: str) -> str:
    """Return a human-readable interface type from its name."""
    n = name.lower()
    if n.startswith("lo"):
        return "Loopback"
    if n.startswith("en") or n.startswith("eth") or n.startswith("eno") or n.startswith("enp"):
        return "Ethernet/WiFi"
    if n.startswith("wl") or n.startswith("wlan") or n.startswith("wifi"):
        return "WiFi"
    if n.startswith("utun") or n.startswith("tun") or n.startswith("tap"):
        return "VPN/Tunnel"
    if n.startswith("bridge") or n.startswith("br"):
        return "Bridge"
    if n.startswith("vmnet") or n.startswith("veth"):
        return "Virtual"
    if n.startswith("llw"):
        return "Low Latency WiFi"
    return "Network"


def _network_instructions() -> str:
    if PLATFORM["is_mac"]:
        return "System Settings > Privacy & Security > Local Network > Enable for Matrx Local"
    elif PLATFORM["is_windows"]:
        return "Settings > Network & internet. Ensure WiFi or Ethernet is connected."
    return (
        "Ensure NetworkManager is running: sudo systemctl enable --now NetworkManager"
    )


# ---------------------------------------------------------------------------
# WiFi (separate from network interfaces)
# ---------------------------------------------------------------------------


async def check_wifi() -> PermissionResult:
    """Scan and return available WiFi networks."""
    networks: list[dict[str, Any]] = []
    details = ""

    try:
        if PLATFORM["is_mac"]:
            networks, details = await _wifi_scan_macos()
        elif PLATFORM["is_windows"]:
            networks, details = await _wifi_scan_windows()
        else:
            networks, details = await _wifi_scan_linux()
    except Exception as e:
        details = f"WiFi scan failed: {e}"

    status = PermissionStatus.GRANTED if networks else PermissionStatus.NOT_DETERMINED

    return PermissionResult(
        permission="wifi",
        status=status,
        details=details or f"{len(networks)} networks found",
        devices=networks,
        grant_instructions=_wifi_instructions(),
        user_details=f"WiFi active — {len(networks)} network(s) visible"
        if networks
        else "No WiFi networks found",
        user_instructions=""
        if networks
        else "Enable WiFi and click Scan to discover networks",
    )


async def _wifi_scan_macos() -> tuple[list[dict[str, Any]], str]:
    """Scan WiFi networks on macOS via airport or system_profiler."""
    import re as _re

    airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
    try:
        out, _, rc = await _run([airport, "-s"], timeout=15)
        if rc == 0:
            lines = out.strip().split("\n")
            bssid_pat = _re.compile(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", _re.IGNORECASE)
            networks: list[dict[str, Any]] = []
            for line in lines[1:]:
                m = bssid_pat.search(line)
                if m:
                    ssid = line[: m.start()].strip()
                    rest = line[m.end():].strip().split()
                    if len(rest) >= 4:
                        networks.append({
                            "ssid": ssid or "(hidden)",
                            "bssid": m.group(0),
                            "rssi": int(rest[0]) if rest[0].lstrip("-").isdigit() else 0,
                            "channel": rest[1],
                            "security": " ".join(rest[3:]),
                            "connected": False,
                        })
            if networks:
                networks.sort(key=lambda n: n.get("rssi", -100), reverse=True)
                return networks, f"{len(networks)} networks found via airport"
    except (FileNotFoundError, Exception):
        pass

    # Fallback: system_profiler
    try:
        out, _, _ = await _run(["system_profiler", "SPAirPortDataType", "-json"], timeout=20)
        data = json.loads(out)
        networks = []
        sec_labels = {
            "spairport_security_mode_wpa3_transition": "WPA3/WPA2",
            "spairport_security_mode_wpa3_personal": "WPA3",
            "spairport_security_mode_wpa2_personal": "WPA2",
            "spairport_security_mode_wpa_personal": "WPA",
            "spairport_security_mode_none": "Open",
            "spairport_security_mode_wpa2_enterprise": "WPA2-Ent",
        }
        seen_current: str | None = None
        for iface_group in data.get("SPAirPortDataType", []):
            for iface in iface_group.get("spairport_airport_interfaces", []):
                cur = iface.get("spairport_current_network_information")
                if cur:
                    seen_current = cur.get("_name", "")
                    rssi_raw = cur.get("spairport_signal_noise", "")
                    rssi = 0
                    try:
                        rssi = int(str(rssi_raw).split()[0])
                    except Exception:
                        pass
                    raw_sec = cur.get("spairport_security_mode", "")
                    networks.append({
                        "ssid": seen_current or "(hidden)",
                        "rssi": rssi,
                        "channel": str(cur.get("spairport_network_channel", "")),
                        "security": sec_labels.get(raw_sec, raw_sec.replace("spairport_security_mode_", "").replace("_", "-")),
                        "connected": True,
                    })
                for net in iface.get("spairport_other_local_wireless_networks", []):
                    ssid = net.get("_name", "")
                    if ssid == seen_current:
                        continue
                    rssi_raw = net.get("spairport_signal_noise", "")
                    rssi = 0
                    try:
                        rssi = int(str(rssi_raw).split()[0])
                    except Exception:
                        pass
                    raw_sec = net.get("spairport_security_mode", "")
                    networks.append({
                        "ssid": ssid or "(hidden)",
                        "rssi": rssi,
                        "channel": str(net.get("spairport_network_channel", "")),
                        "security": sec_labels.get(raw_sec, raw_sec.replace("spairport_security_mode_", "").replace("_", "-")),
                        "connected": False,
                    })
        networks.sort(key=lambda n: (not n.get("connected", False), -(n.get("rssi") or 0)))
        return networks, f"{len(networks)} networks found via system_profiler"
    except Exception as e:
        return [], f"WiFi scan failed: {e}"


async def _wifi_scan_windows() -> tuple[list[dict[str, Any]], str]:
    out, _, _ = await _run(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=10)
    networks: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("SSID") and "BSSID" not in line:
            if current:
                networks.append(current)
            ssid = line.split(":", 1)[1].strip() if ":" in line else ""
            current = {"ssid": ssid}
        elif "Signal" in line:
            val = line.split(":", 1)[1].strip().replace("%", "")
            current["signal_percent"] = int(val) if val.isdigit() else 0
        elif "Authentication" in line:
            current["security"] = line.split(":", 1)[1].strip()
        elif "Channel" in line:
            current["channel"] = line.split(":", 1)[1].strip()
        elif "BSSID" in line:
            current["bssid"] = line.split(":", 1)[1].strip()
    if current and "ssid" in current:
        networks.append(current)
    networks.sort(key=lambda n: n.get("signal_percent", 0), reverse=True)
    return networks, f"{len(networks)} networks found"


async def _wifi_scan_linux() -> tuple[list[dict[str, Any]], str]:
    out, stderr, rc = await _run(
        ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,SECURITY", "device", "wifi", "list"],
        timeout=10,
    )
    if rc != 0:
        return [], f"nmcli error: {stderr}"
    networks: list[dict[str, Any]] = []
    for line in out.strip().split("\n"):
        parts = line.split(":")
        if len(parts) >= 5:
            networks.append({
                "ssid": parts[0],
                "bssid": parts[1],
                "signal_percent": int(parts[2]) if parts[2].isdigit() else 0,
                "channel": parts[3],
                "security": parts[4],
            })
    networks.sort(key=lambda n: n.get("signal_percent", 0), reverse=True)
    return networks, f"{len(networks)} networks found"


def _wifi_instructions() -> str:
    if PLATFORM["is_mac"]:
        return "Ensure WiFi is enabled in System Settings > Network > WiFi"
    elif PLATFORM["is_windows"]:
        return "Enable WiFi in Settings > Network & internet > WiFi"
    return "Enable WiFi: nmcli radio wifi on"


# ---------------------------------------------------------------------------
# Screen Recording (macOS-specific)
# ---------------------------------------------------------------------------


def _screen_recording_requested_marker() -> Path:
    """Marker file: has this engine ever asked macOS for screen recording?

    macOS gives us exactly one bit — CGPreflightScreenCaptureAccess() is false
    for BOTH "the user has never been asked" and "the user said no". Those are
    completely different situations for the user ("click this button" vs "go
    turn it back on in System Settings"), so we remember whether we've asked.
    """
    from app.config import MATRX_HOME_DIR  # noqa: PLC0415 — avoid an import cycle

    return Path(MATRX_HOME_DIR) / "screen_recording_requested"


def mark_screen_recording_requested() -> None:
    try:
        marker = _screen_recording_requested_marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except Exception as exc:  # noqa: BLE001 — a marker failure must not break the grant
        logger.warning("[permissions] Could not write screen-recording marker: %s", exc)


def _macos_screen_recording_status() -> PermissionStatus:
    """Check screen recording permission status — read-only, no prompt.

    Uses CGPreflightScreenCaptureAccess() from CoreGraphics, which is a
    passive status-only query. It does NOT trigger a permission dialog.

    Returns GRANTED, NOT_DETERMINED, DENIED, or UNKNOWN.

    Preflight-false means "not granted", and NOTHING more. Reporting that as
    DENIED — as this did — is a lie in the common case: the engine had simply
    never asked, so macOS had never even listed it under Screen Recording, and
    the user was told "denied" about a permission they were never offered. We
    now report NOT_DETERMINED until the engine has actually issued the request
    (marker above), and DENIED only after an ask that came back false.

    IMPORTANT: SCShareableContent.getShareableContentWithCompletionHandler_()
    was previously used here but it ACTIVELY TRIGGERS the macOS Sequoia
    recurring 30-day screen recording consent prompt every time it is called.
    Do not use SCShareableContent for status checks.

    CGPreflightScreenCaptureAccess() has a known limitation: it returns False
    until the app is restarted even when the user grants permission in the same
    session. This is expected macOS TCC cache behaviour.
    """
    if not CAPABILITIES["has_quartz"]:
        return PermissionStatus.UNKNOWN
    try:
        from Quartz import CGPreflightScreenCaptureAccess  # pyobjc-framework-Quartz

        if CGPreflightScreenCaptureAccess():
            return PermissionStatus.GRANTED
    except Exception:
        return PermissionStatus.UNKNOWN
    if _screen_recording_requested_marker().exists():
        return PermissionStatus.DENIED
    return PermissionStatus.NOT_DETERMINED


def _macos_request_screen_recording() -> bool:
    """Trigger the macOS Screen Recording permission prompt.

    Calls CGRequestScreenCaptureAccess() which shows the native TCC dialog
    (System Settings → Privacy & Security → Screen Recording) the first time
    it is called. On subsequent calls it is a no-op if the user already made
    a decision.

    Returns True if permission was granted, False otherwise.

    IMPORTANT: This must only be called when the user explicitly requests a
    screenshot or screen-capture operation — never on startup. Calling it
    speculatively triggers the prompt unexpectedly and annoys users.
    """
    if CAPABILITIES["has_quartz"]:
        try:
            from Quartz import CGRequestScreenCaptureAccess  # pyobjc-framework-Quartz

            granted = bool(CGRequestScreenCaptureAccess())
            # We have now ASKED. From here on a preflight-false is a real denial
            # (see _macos_screen_recording_status) — and, just as importantly,
            # this call is what makes the engine appear in System Settings →
            # Screen Recording at all, so the user finally has a switch to flip.
            mark_screen_recording_requested()
            return granted
        except Exception:
            pass
    return False


async def request_screen_recording() -> PermissionResult:
    """Ask macOS for screen recording, then report the resulting status.

    This is the ONLY thing that makes the engine appear in System Settings →
    Privacy & Security → Screen Recording. Without it the user is told the
    permission is missing and sent to a settings pane where the app isn't even
    listed — a dead end, which is exactly what shipped.

    Screen capture runs in THIS process (`screencapture`, see
    app/tools/tools/system.py), so this process is the one that must hold the
    grant; asking on behalf of the Tauri window would register the wrong
    principal. Only ever called from an explicit user action.
    """
    if not PLATFORM["is_mac"]:
        return await check_screen_recording()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _macos_request_screen_recording)
    return await check_screen_recording()


async def request_engine_permission(name: str) -> PermissionResult:
    """Request an engine-owned macOS grant after an explicit user click."""
    checker = PERMISSION_CHECKERS.get(name)
    if checker is None:
        raise ValueError(f"Unknown permission: {name}")
    if not PLATFORM["is_mac"]:
        return await checker()
    if name == "screen_recording":
        return await request_screen_recording()

    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def finish(*_args: Any) -> None:
        if not done.done():
            loop.call_soon_threadsafe(done.set_result, None)

    if name == "contacts":
        from Contacts import CNContactStore

        CNContactStore.alloc().init().requestAccessForEntityType_completionHandler_(0, finish)
    elif name in {"calendar", "reminders"}:
        from EventKit import EKEventStore

        entity_type = 0 if name == "calendar" else 1
        store = EKEventStore.alloc().init()
        if name == "calendar" and hasattr(store, "requestFullAccessToEventsWithCompletion_"):
            store.requestFullAccessToEventsWithCompletion_(finish)
        elif name == "reminders" and hasattr(store, "requestFullAccessToRemindersWithCompletion_"):
            store.requestFullAccessToRemindersWithCompletion_(finish)
        else:
            store.requestAccessToEntityType_completion_(entity_type, finish)
    elif name == "photos":
        from Photos import PHPhotoLibrary

        PHPhotoLibrary.requestAuthorizationForAccessLevel_handler_(2, finish)
    elif name == "speech_recognition":
        from Speech import SFSpeechRecognizer

        SFSpeechRecognizer.requestAuthorization_(finish)
    elif name == "location":
        from CoreLocation import CLLocationManager

        manager = CLLocationManager.alloc().init()
        manager.requestWhenInUseAuthorization()
        # CoreLocation has no completion callback; keep the manager alive and
        # give the TCC callback a short window before rechecking.
        await asyncio.sleep(1.0)
        return await checker()
    else:
        return await checker()

    try:
        await asyncio.wait_for(done, timeout=30.0)
    except asyncio.TimeoutError:
        pass
    return await checker()


async def check_screen_recording() -> PermissionResult:
    """Check screen recording / screenshot permission."""
    _sr_deep_link = (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
        if PLATFORM["is_mac"] else ""
    )

    if PLATFORM["is_mac"]:
        loop = asyncio.get_event_loop()
        status = await loop.run_in_executor(None, _macos_screen_recording_status)

        granted = status == PermissionStatus.GRANTED
        not_determined = status == PermissionStatus.UNKNOWN and not CAPABILITIES["has_quartz"]

        if not_determined:
            detail = "Screen recording status unknown — Quartz framework not available"
            user_detail = "Screen capture status could not be determined"
            user_instruction = "Open System Settings > Privacy & Security > Screen Recording"
        elif granted:
            detail = "Screen recording permission granted"
            user_detail = "Screen capture is active"
            user_instruction = ""
        elif status == PermissionStatus.NOT_DETERMINED:
            # Never asked — so macOS has never listed us, and there is nothing
            # for the user to switch on yet. The fix is a click, not a trip to
            # System Settings.
            detail = "Screen recording has not been requested yet"
            user_detail = "Screen capture hasn’t been set up yet"
            user_instruction = "Click Grant to allow screen capture"
        else:
            detail = "Screen recording permission denied — enable it in System Settings"
            user_detail = "Screen capture is turned off"
            user_instruction = "Open System Settings > Privacy & Security > Screen Recording and enable AI Matrx"

        return PermissionResult(
            permission="screen_recording",
            status=status,
            details=detail,
            grant_instructions="System Settings > Privacy & Security > Screen Recording > Enable for AI Matrx",
            user_details=user_detail,
            user_instructions=user_instruction,
            deep_link=_sr_deep_link,
        )

    elif PLATFORM["is_windows"]:
        return PermissionResult(
            permission="screen_recording",
            status=PermissionStatus.GRANTED,
            details="Screen capture is available on Windows",
            grant_instructions="No action needed",
            user_details="Screen capture is active",
            user_instructions="",
        )

    else:
        return PermissionResult(
            permission="screen_recording",
            status=PermissionStatus.UNKNOWN,
            details="Linux screen capture is verified only by an explicit capture attempt",
            grant_instructions="Use the Screen Capture feature; your desktop portal may ask you to choose a screen",
            user_details="Screen capture has not been functionally verified",
            user_instructions="Start a capture to verify X11/Wayland portal access",
        )


# ---------------------------------------------------------------------------
# Location Services
# ---------------------------------------------------------------------------


async def check_location() -> PermissionResult:
    """Check location services TCC status via CLLocationManager (macOS only).

    CLLocationManager.authorizationStatus() is a static method that can be
    called from any process — it does NOT require a run loop or UI ownership.
    The sidecar can read the status but cannot prompt; if NOT_DETERMINED we
    return UNKNOWN with instructions to open System Settings.
    """
    if PLATFORM["is_mac"]:
        status = PermissionStatus.UNKNOWN
        try:
            from CoreLocation import CLLocationManager  # pyobjc-framework-CoreLocation
            code = CLLocationManager.authorizationStatus()
            # CLAuthorizationStatus: 0=notDetermined, 1=restricted, 2=denied,
            # 3=authorizedAlways, 4=authorizedWhenInUse
            status = {
                0: PermissionStatus.NOT_DETERMINED,
                1: PermissionStatus.RESTRICTED,
                2: PermissionStatus.DENIED,
                3: PermissionStatus.GRANTED,
                4: PermissionStatus.GRANTED,
            }.get(code, PermissionStatus.UNKNOWN)
        except ImportError:
            pass  # pyobjc-framework-CoreLocation not yet installed
        except Exception as exc:
            logger.debug("CoreLocation status check failed: %s", exc)

        instructions = (
            "System Settings → Privacy & Security → Location Services → Enable for AI Matrx"
        )
        return PermissionResult(
            permission="location",
            status=status,
            details="Location permission status via CLLocationManager",
            grant_instructions=instructions,
            user_details="Location services let AI tools access your GPS/network position.",
            user_instructions=instructions if status != PermissionStatus.GRANTED else "",
            deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices",
        )

    if PLATFORM["is_windows"]:
        loc_status = _win_consent_status("location")

        instructions = "Settings > Privacy > Location > Allow apps to access your location"
        return PermissionResult(
            permission="location",
            status=loc_status,
            details="Location services permission via Windows registry",
            grant_instructions=instructions,
            user_details="Location services are enabled" if loc_status == PermissionStatus.GRANTED
            else "Location access denied by system policy (requires admin)",
            user_instructions="" if loc_status == PermissionStatus.GRANTED else instructions,
        )

    return PermissionResult(
        permission="location",
        status=PermissionStatus.UNKNOWN,
        details="Location services check not yet implemented for this platform",
        grant_instructions="Check your system's location settings",
        user_details="Location services status unknown",
        user_instructions="Check your system's location settings",
    )


async def check_contacts() -> PermissionResult:
    """Check Contacts TCC status via CNContactStore (macOS only)."""
    if PLATFORM["is_mac"]:
        status = PermissionStatus.UNKNOWN
        try:
            from Contacts import CNContactStore  # pyobjc-framework-Contacts
            # CNEntityTypeContacts = 0
            code = CNContactStore.authorizationStatusForEntityType_(0)
            # CNAuthorizationStatus: 0=notDetermined, 1=restricted, 2=denied, 3=authorized
            status = {
                0: PermissionStatus.NOT_DETERMINED,
                1: PermissionStatus.RESTRICTED,
                2: PermissionStatus.DENIED,
                3: PermissionStatus.GRANTED,
            }.get(code, PermissionStatus.UNKNOWN)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Contacts status check failed: %s", exc)

        instructions = "System Settings → Privacy & Security → Contacts → Enable for AI Matrx"
        return PermissionResult(
            permission="contacts",
            status=status,
            details="Contacts permission status via CNContactStore",
            grant_instructions=instructions,
            user_details="Contacts access lets AI tools search and read your address book.",
            user_instructions=instructions if status != PermissionStatus.GRANTED else "",
            deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_Contacts",
        )

    return PermissionResult(
        permission="contacts",
        status=PermissionStatus.UNAVAILABLE,
        details="Contacts access is macOS-only",
    )


async def check_calendar() -> PermissionResult:
    """Check Calendar TCC status via EKEventStore (macOS only)."""
    if PLATFORM["is_mac"]:
        status = PermissionStatus.UNKNOWN
        try:
            from EventKit import EKEventStore  # pyobjc-framework-EventKit
            # EKEntityTypeEvent = 0
            code = EKEventStore.authorizationStatusForEntityType_(0)
            # EKAuthorizationStatus: 0=notDetermined, 1=restricted, 2=denied, 3=authorized, 4=writeOnly
            status = {
                0: PermissionStatus.NOT_DETERMINED,
                1: PermissionStatus.RESTRICTED,
                2: PermissionStatus.DENIED,
                3: PermissionStatus.GRANTED,
                4: PermissionStatus.RESTRICTED,
            }.get(code, PermissionStatus.UNKNOWN)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Calendar status check failed: %s", exc)

        instructions = "System Settings → Privacy & Security → Calendars → Enable for AI Matrx"
        return PermissionResult(
            permission="calendar",
            status=status,
            details="Calendar permission status via EKEventStore",
            grant_instructions=instructions,
            user_details="Calendar access lets AI tools list and create calendar events.",
            user_instructions=instructions if status != PermissionStatus.GRANTED else "",
            deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars",
        )

    return PermissionResult(
        permission="calendar",
        status=PermissionStatus.UNAVAILABLE,
        details="Calendar access is macOS-only",
    )


async def check_reminders() -> PermissionResult:
    """Check Reminders TCC status via EKEventStore (macOS only)."""
    if PLATFORM["is_mac"]:
        status = PermissionStatus.UNKNOWN
        try:
            from EventKit import EKEventStore  # pyobjc-framework-EventKit
            # EKEntityTypeReminder = 1
            code = EKEventStore.authorizationStatusForEntityType_(1)
            status = {
                0: PermissionStatus.NOT_DETERMINED,
                1: PermissionStatus.RESTRICTED,
                2: PermissionStatus.DENIED,
                3: PermissionStatus.GRANTED,
                4: PermissionStatus.RESTRICTED,
            }.get(code, PermissionStatus.UNKNOWN)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Reminders status check failed: %s", exc)

        instructions = "System Settings → Privacy & Security → Reminders → Enable for AI Matrx"
        return PermissionResult(
            permission="reminders",
            status=status,
            details="Reminders permission status via EKEventStore",
            grant_instructions=instructions,
            user_details="Reminders access lets AI tools list and create reminders.",
            user_instructions=instructions if status != PermissionStatus.GRANTED else "",
            deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_Reminders",
        )

    return PermissionResult(
        permission="reminders",
        status=PermissionStatus.UNAVAILABLE,
        details="Reminders access is macOS-only",
    )


async def check_photos() -> PermissionResult:
    """Check Photos library TCC status via PHPhotoLibrary (macOS only)."""
    if PLATFORM["is_mac"]:
        status = PermissionStatus.UNKNOWN
        try:
            from Photos import PHPhotoLibrary  # pyobjc-framework-Photos
            # PHAccessLevelReadWrite = 2
            code = PHPhotoLibrary.authorizationStatusForAccessLevel_(2)
            # PHAuthorizationStatus: 0=notDetermined, 1=restricted, 2=denied, 3=authorized, 4=limited
            status = {
                0: PermissionStatus.NOT_DETERMINED,
                1: PermissionStatus.RESTRICTED,
                2: PermissionStatus.DENIED,
                3: PermissionStatus.GRANTED,
                4: PermissionStatus.GRANTED,  # limited access — still usable
            }.get(code, PermissionStatus.UNKNOWN)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Photos status check failed: %s", exc)

        instructions = "System Settings → Privacy & Security → Photos → Enable for AI Matrx"
        return PermissionResult(
            permission="photos",
            status=status,
            details="Photos permission status via PHPhotoLibrary",
            grant_instructions=instructions,
            user_details="Photos access lets AI tools search and view your photo library.",
            user_instructions=instructions if status != PermissionStatus.GRANTED else "",
            deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_Photos",
        )

    return PermissionResult(
        permission="photos",
        status=PermissionStatus.UNAVAILABLE,
        details="Photos access is macOS-only",
    )


async def check_messages() -> PermissionResult:
    """Check Messages access via functional probe on chat.db (macOS only).

    Messages does not have its own TCC service. Access is gated by Full Disk
    Access (kTCCServiceSystemPolicyAllFiles). We probe by attempting to open
    ~/Library/Messages/chat.db in read-only mode.
    """
    if PLATFORM["is_mac"]:
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path

        chat_db = _Path.home() / "Library" / "Messages" / "chat.db"
        if not chat_db.exists():
            return PermissionResult(
                permission="messages",
                status=PermissionStatus.UNKNOWN,
                details="chat.db not found — Messages may not be configured on this device.",
                grant_instructions="Enable Full Disk Access in System Settings → Privacy & Security → Full Disk Access",
                deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
            )

        try:
            conn = _sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True, timeout=3.0)
            conn.execute("SELECT 1 FROM message LIMIT 1")
            conn.close()
            return PermissionResult(
                permission="messages",
                status=PermissionStatus.GRANTED,
                details="chat.db is readable — Full Disk Access granted.",
                user_details="Messages (iMessage/SMS) are accessible.",
            )
        except (_sqlite3.OperationalError, PermissionError) as exc:
            instructions = "System Settings → Privacy & Security → Full Disk Access → Enable for AI Matrx"
            return PermissionResult(
                permission="messages",
                status=PermissionStatus.DENIED,
                details=f"Cannot open chat.db: {exc}",
                grant_instructions=instructions,
                user_details="Full Disk Access is required to read iMessage/SMS history.",
                user_instructions=instructions,
                deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
            )

    return PermissionResult(
        permission="messages",
        status=PermissionStatus.UNAVAILABLE,
        details="Messages (iMessage) is macOS-only",
    )


async def check_mail() -> PermissionResult:
    """Check Mail.app automation access (macOS only).

    Mail has no dedicated TCC service. Access is via Apple Events (Automation).
    We return UNKNOWN with instructions to grant Automation access since we cannot
    query the Automation TCC table from a background sidecar process reliably.
    """
    if PLATFORM["is_mac"]:
        return PermissionResult(
            permission="mail",
            status=PermissionStatus.UNKNOWN,
            details="Mail access is via Automation (Apple Events) — cannot be probed from sidecar.",
            grant_instructions=(
                "System Settings → Privacy & Security → Automation → "
                "AI Matrx → enable 'Mail'"
            ),
            user_details="Mail access lets AI tools read and send emails via Mail.app.",
            user_instructions=(
                "To grant Mail access: System Settings → Privacy & Security → "
                "Automation → AI Matrx → enable Mail"
            ),
            deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
        )

    return PermissionResult(
        permission="mail",
        status=PermissionStatus.UNAVAILABLE,
        details="Mail.app access is macOS-only",
    )


async def check_speech_recognition() -> PermissionResult:
    """Check Speech Recognition TCC status via SFSpeechRecognizer (macOS only)."""
    if PLATFORM["is_mac"]:
        status = PermissionStatus.UNKNOWN
        if CAPABILITIES["has_speech_framework"]:
            try:
                from Speech import SFSpeechRecognizer  # pyobjc-framework-Speech
                # SFSpeechRecognizerAuthorizationStatus: 0=notDetermined, 1=denied, 2=restricted, 3=authorized
                code = SFSpeechRecognizer.authorizationStatus()
                status = {
                    0: PermissionStatus.NOT_DETERMINED,
                    1: PermissionStatus.DENIED,
                    2: PermissionStatus.RESTRICTED,
                    3: PermissionStatus.GRANTED,
                }.get(code, PermissionStatus.UNKNOWN)
            except Exception as exc:
                logger.debug("Speech Recognition status check failed: %s", exc)

        instructions = "System Settings → Privacy & Security → Speech Recognition → Enable for AI Matrx"
        return PermissionResult(
            permission="speech_recognition",
            status=status,
            details="Speech Recognition permission status via SFSpeechRecognizer",
            grant_instructions=instructions,
            user_details="Speech Recognition lets AI tools transcribe audio using Apple's on-device engine.",
            user_instructions=instructions if status != PermissionStatus.GRANTED else "",
            deep_link="x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition",
        )

    return PermissionResult(
        permission="speech_recognition",
        status=PermissionStatus.UNAVAILABLE,
        details="Apple Speech Recognition is macOS-only",
    )


# ---------------------------------------------------------------------------
# macOS TCC helper
# ---------------------------------------------------------------------------


def _avfoundation_auth_status(media_type: str) -> PermissionStatus | None:
    """Check AVCaptureDevice authorization status via pyobjc-framework-AVFoundation.

    media_type: "soun" for audio (microphone), "vide" for video (camera).
    Returns PermissionStatus or None if AVFoundation is not available.

    IMPORTANT: On macOS 14+ (Sonoma) and macOS 15 (Sequoia),
    AVCaptureDevice.authorizationStatusForMediaType_() TRIGGERS the native
    TCC permission dialog when the status is notDetermined (0). This function
    MUST NOT be called from the Python sidecar process. TCC prompts must only
    originate from the main Tauri .app bundle process via tauri-plugin-macos-
    permissions, not from the background sidecar. Calling it from the sidecar
    associates the prompt with "Terminal" or "Matrx Engine" (the helper-app
    bundle name; legacy installs may show "aimatrx-engine") rather than
    "AI Matrx.app", confuses the user, and can cause repeated prompts.

    This function is intentionally disabled — it returns None unconditionally
    so callers fall through to the TCC database probe (_tcc_db_status) which
    is read-only and never triggers any dialog.
    """
    # DO NOT call AVCaptureDevice.authorizationStatusForMediaType_ here.
    # See the docstring above for the full explanation.
    return None


def _tcc_db_status(service: str) -> PermissionStatus:
    """Read TCC permission status from the macOS TCC SQLite database.

    This is a PASSIVE read-only operation — it never triggers any permission
    dialog. The TCC database at ~/Library/Application Support/com.apple.TCC/TCC.db
    stores the grant status for every permission the user has been asked about.

    Schema: SELECT auth_value FROM access WHERE service=? AND client=?
    auth_value: 0=deny, 1=unknown/ask, 2=allow, 3=limited

    Limitation: this only sees rows that exist (i.e. the user was prompted at
    least once). If no row exists → NOT_DETERMINED (never been asked).

    Full Disk Access is needed to read the system TCC DB; we try only the
    per-user DB which is always readable by the owning user.
    """
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    tcc_db = _Path.home() / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
    if not tcc_db.exists():
        return PermissionStatus.UNKNOWN

    try:
        conn = _sqlite3.connect(f"file:{tcc_db}?mode=ro", uri=True, timeout=2.0)
        # The `client` column holds the bundle ID or process name. Filter to
        # OUR clients — matching any row meant e.g. Zoom's microphone grant
        # reported "granted" for Matrx even when Matrx itself was denied.
        our_clients = (
            "com.aimatrx.desktop",     # packaged app bundle id
            "matrx-engine",            # sidecar process name (TCC by path)
            "aimatrx-desktop",
        )
        placeholders = ",".join("?" for _ in our_clients)
        rows = conn.execute(
            f"SELECT auth_value FROM access WHERE service=? AND client IN ({placeholders})",
            (service, *our_clients),
        ).fetchall()
        if not rows:
            conn.close()
            return PermissionStatus.NOT_DETERMINED  # we have never been asked
        conn.close()

        # If ANY of our rows has auth_value=2 (allow), the service is accessible.
        values = [r[0] for r in rows]
        if 2 in values:
            return PermissionStatus.GRANTED
        if 0 in values:
            return PermissionStatus.DENIED
        return PermissionStatus.NOT_DETERMINED

    except Exception:
        return PermissionStatus.UNKNOWN


async def _macos_check_tcc(service: str, label: str) -> PermissionStatus:
    """Read macOS TCC status for a service — read-only, never triggers a dialog.

    Uses _tcc_db_status() which reads the TCC SQLite database directly.
    This avoids the AVFoundation / CNContactStore / etc. APIs that trigger
    the native OS permission dialog when status is notDetermined.
    """
    tcc_service_map = {
        "Microphone": "kTCCServiceMicrophone",
        "Camera": "kTCCServiceCamera",
    }
    svc = tcc_service_map.get(label, service)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _tcc_db_status, svc)


# ---------------------------------------------------------------------------
# Full scan
# ---------------------------------------------------------------------------

PermissionChecker = Callable[[], Awaitable[PermissionResult]]

# One canonical catalog powers both the all-permissions scan and the
# single-permission API.  Insertion order is the UI's stable display order.
PERMISSION_CHECKERS: dict[str, PermissionChecker] = {
    "microphone": check_microphone,
    "camera": check_camera,
    "accessibility": check_accessibility,
    "bluetooth": check_bluetooth,
    "network": check_network,
    "wifi": check_wifi,
    "screen_recording": check_screen_recording,
    "location": check_location,
    "contacts": check_contacts,
    "calendar": check_calendar,
    "reminders": check_reminders,
    "photos": check_photos,
    "messages": check_messages,
    "mail": check_mail,
    "speech_recognition": check_speech_recognition,
}


async def _run_permission_check(name: str, checker: PermissionChecker) -> PermissionResult:
    # WiFi probes can invoke slow platform tooling; retain the all-scan timeout
    # while keeping the public catalog's checker itself reusable.
    if name == "wifi":
        return await asyncio.wait_for(checker(), timeout=10)
    return await checker()


async def check_all_permissions() -> list[dict[str, Any]]:
    """Run all permission checks concurrently and return structured results.

    Logs one line per permission showing exactly what was checked and what
    value came back, so the log tells the full story on every platform.
    """
    names = list(PERMISSION_CHECKERS)

    logger.info(
        "[permissions] check_all_permissions — platform=%s %s — checking %d permissions",
        PLATFORM["system"], PLATFORM["machine"], len(names),
    )

    scan = asyncio.gather(
        *(
            _run_permission_check(name, checker)
            for name, checker in PERMISSION_CHECKERS.items()
        ),
        return_exceptions=True,
    )
    try:
        while True:
            if process_shutdown_requested():
                # Permission probes are advisory and may own platform
                # subprocesses. Cancel the canonical gather during process
                # shutdown; _run() handles CancelledError by killing/reaping
                # its child before the request returns.
                scan.cancel()
                await asyncio.gather(scan, return_exceptions=True)
                return []
            done, _pending = await asyncio.wait((scan,), timeout=0.1)
            if done:
                results = scan.result()
                break
    except asyncio.CancelledError:
        scan.cancel()
        await asyncio.gather(scan, return_exceptions=True)
        raise

    output: list[dict[str, Any]] = []
    for i, result in enumerate(results):
        name = names[i]
        if isinstance(result, Exception):
            logger.warning(
                "[permissions] %-20s → ERROR: %s",
                name, result,
            )
            output.append(
                {
                    "permission": name,
                    "status": "unknown",
                    "details": f"Check failed: {result}",
                    "grant_instructions": "",
                }
            )
        else:
            d = result.to_dict()
            logger.info(
                "[permissions] %-20s → status=%-15s details=%s",
                name, d.get("status", "?"), d.get("details", ""),
            )
            output.append(d)

    granted = sum(1 for d in output if d.get("status") == "granted")
    unavailable = sum(1 for d in output if d.get("status") == "unavailable")
    logger.info(
        "[permissions] Summary: %d/%d granted (%d unavailable on this platform)",
        granted, len(names) - unavailable, unavailable,
    )

    return output
