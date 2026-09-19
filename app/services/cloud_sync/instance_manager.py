"""Instance registration and system identification.

Collects system info, generates a stable instance ID, and registers
with Supabase so the cloud knows about this device.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.common.platform_ctx import PLATFORM

logger = logging.getLogger(__name__)

from app.config import MATRX_HOME_DIR
INSTANCE_FILE = MATRX_HOME_DIR / "instance.json"


@dataclass(frozen=True)
class RegisteredDeviceIdentity:
    """Current cloud row identity; this is discovery data, not transport authority."""
    app_instance_id: str
    user_id: str
    instance_id: str


def current_app_version() -> str:
    """The running app version, from the single resolver in app.api.routes.

    Imported lazily — app.api.routes imports the service layer, so a
    module-level import here would be circular. Never re-implement version
    resolution; there is exactly one ``_APP_VERSION``.
    """
    from app.api.routes import _APP_VERSION  # noqa: PLC0415

    return str(_APP_VERSION)


def build_config_provenance() -> dict:
    """Remote-config provenance for ``app_instances.metadata``.

    Answers "is this installed client running real remote config, or has it
    silently fallen back?" for the admin fleet view without adding columns.
    Reads the already-resolved state from the app_config and catalogs
    services — it never triggers a fetch.

    Best-effort: a missing/broken service degrades a field to ``None``
    rather than raising into the caller's write path.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    app_config_tier: str | None = None
    catalogs_tier: str | None = None
    catalog_entry_count: int | None = None

    try:
        from app.services.app_config import get_app_config  # noqa: PLC0415

        app_config_tier = get_app_config().tier
    except Exception as exc:
        logger.debug("build_config_provenance: app_config tier unavailable: %s", exc)

    try:
        from app.services.catalogs import get_catalogs_service  # noqa: PLC0415

        status = get_catalogs_service().status_payload()
        catalogs_tier = status.get("tier")
        catalog_entry_count = status.get("entry_count")
    except Exception as exc:
        logger.debug("build_config_provenance: catalogs status unavailable: %s", exc)

    return {
        "app_config_tier": app_config_tier,
        "catalogs_tier": catalogs_tier,
        "catalog_entry_count": catalog_entry_count,
        "provenance_reported_at": datetime.now(timezone.utc).isoformat(),
    }


# Keys owned by build_config_provenance() — compared to decide whether a
# metadata write is worth making. provenance_reported_at is EXCLUDED: it
# changes every call, and including it would turn every heartbeat into a
# write (the write amplification this design exists to avoid).
PROVENANCE_CONTENT_KEYS = (
    "app_config_tier",
    "catalogs_tier",
    "catalog_entry_count",
)


def _stable_machine_id() -> str:
    """Generate a stable machine identifier from hardware characteristics.

    Preference order: hardware_uuid (board-level, survives OS reinstall) →
    serial_number → /etc/machine-id (Linux) → hostname+arch+OS fallback.
    The result is SHA-256 hashed to a fixed 32-char hex string.
    """
    parts = [
        PLATFORM["hostname"],
        PLATFORM["machine"],
        PLATFORM["system"],
    ]
    try:
        system = PLATFORM["system"]
        if system == "Darwin":
            import subprocess
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts.append(line.split('"')[-2])
                    break
        elif system == "Linux":
            machine_id = Path("/etc/machine-id")
            if machine_id.exists():
                parts.append(machine_id.read_text().strip())
            else:
                uuid_path = Path("/sys/class/dmi/id/product_uuid")
                if uuid_path.exists():
                    parts.append(uuid_path.read_text().strip())
        elif system == "Windows":
            import subprocess
            out = subprocess.run(
                ["wmic", "csproduct", "get", "uuid"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            lines = [l.strip() for l in out.splitlines() if l.strip() and l.strip() != "UUID"]
            if lines:
                parts.append(lines[0])
    except Exception:
        pass

    # MATRX_INSTANCE_SALT keeps a dev engine's cloud registration distinct
    # from the installed app's. Without it, both derive the SAME hardware id
    # on one machine and a dev engine overwrites the live app's instance row
    # (tunnel_url, settings) in Supabase. run.py's dev/live isolation guard
    # sets "dev"; the packaged sidecar never sets it.
    salt = os.environ.get("MATRX_INSTANCE_SALT")
    if salt:
        parts.append(f"salt:{salt}")

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _get_or_create_instance_id() -> str:
    """Get existing instance ID or create a new one and persist it."""
    if INSTANCE_FILE.exists():
        try:
            data = json.loads(INSTANCE_FILE.read_text())
            if "instance_id" in data:
                return data["instance_id"]
        except Exception:
            pass

    instance_id = f"inst_{_stable_machine_id()}"

    INSTANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTANCE_FILE.write_text(json.dumps({"instance_id": instance_id}, indent=2))

    return instance_id


def _collect_hardware_ids() -> dict[str, str | None]:
    """Collect truly unique hardware identifiers per OS.

    Returns a dict with keys: hardware_uuid, serial_number, board_id.
    All values are None if unavailable — never raises.
    """
    result: dict[str, str | None] = {
        "hardware_uuid": None,
        "serial_number": None,
        "board_id": None,
    }
    try:
        system = PLATFORM["system"]
        if system == "Darwin":
            import subprocess
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    result["hardware_uuid"] = line.split('"')[-2]
                elif "IOPlatformSerialNumber" in line:
                    result["serial_number"] = line.split('"')[-2]
                elif "board-id" in line.lower():
                    result["board_id"] = line.split('"')[-2]

        elif system == "Linux":
            # DMI info — requires root on some distros but try anyway
            for path, key in [
                ("/sys/class/dmi/id/product_uuid", "hardware_uuid"),
                ("/sys/class/dmi/id/product_serial", "serial_number"),
                ("/sys/class/dmi/id/board_name", "board_id"),
            ]:
                try:
                    val = Path(path).read_text().strip()
                    if val and val not in ("", "None", "To be filled by O.E.M."):
                        result[key] = val
                except Exception:
                    pass

        elif system == "Windows":
            import subprocess
            # BIOS serial
            bios = subprocess.run(
                ["wmic", "bios", "get", "serialnumber"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            lines = [l.strip() for l in bios.splitlines() if l.strip() and l.strip() != "SerialNumber"]
            if lines:
                result["serial_number"] = lines[0]
            # Board product UUID
            csproduct = subprocess.run(
                ["wmic", "csproduct", "get", "uuid"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            lines = [l.strip() for l in csproduct.splitlines() if l.strip() and l.strip() != "UUID"]
            if lines:
                result["hardware_uuid"] = lines[0]
            # Baseboard
            board = subprocess.run(
                ["wmic", "baseboard", "get", "product"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            lines = [l.strip() for l in board.splitlines() if l.strip() and l.strip() != "Product"]
            if lines:
                result["board_id"] = lines[0]
    except Exception:
        pass
    return result


def collect_system_info() -> dict:
    """Collect comprehensive system identification info."""
    info: dict = {
        "platform": PLATFORM["system"].lower(),
        "os_version": PLATFORM["os_version"],
        "architecture": PLATFORM["machine"],
        "hostname": PLATFORM["hostname"],
        "username": os.getenv("USER") or os.getenv("USERNAME") or "",
        "python_version": PLATFORM["python_version"],
        "home_dir": str(Path.home()),
    }

    # CPU info
    try:
        info["cpu_model"] = PLATFORM["processor"] or "unknown"
        info["cpu_cores"] = os.cpu_count() or 0
    except Exception:
        info["cpu_model"] = "unknown"
        info["cpu_cores"] = 0

    # RAM info
    try:
        import psutil
        mem = psutil.virtual_memory()
        info["ram_total_gb"] = round(mem.total / (1024 ** 3), 2)
    except ImportError:
        info["ram_total_gb"] = 0

    # Hardware identifiers (serial number, hardware UUID, board ID)
    info.update(_collect_hardware_ids())

    return info


class InstanceManager:
    """Manages the local app instance identity and registration."""

    def __init__(self) -> None:
        self._instance_id: Optional[str] = None
        self._registered_device_identity_fenced = False
        self._system_info: Optional[dict] = None
        # Load persisted instance_name from settings.json so the name
        # survives engine restarts without requiring re-registration.
        self._instance_name: str = self._load_persisted_name()

    @staticmethod
    def _load_persisted_name() -> str:
        """Read instance_name from ~/.matrx/settings.json if it exists."""
        try:
            data = json.loads(INSTANCE_FILE.parent.joinpath("settings.json").read_text())
            name = data.get("settings", {}).get("instance_name", "")
            return name if name else "My Computer"
        except Exception:
            return "My Computer"

    @property
    def instance_id(self) -> str:
        if self._instance_id is None:
            self._instance_id = _get_or_create_instance_id()
        return self._instance_id

    @property
    def system_info(self) -> dict:
        if self._system_info is None:
            self._system_info = collect_system_info()
        return self._system_info

    @property
    def instance_name(self) -> str:
        return self._instance_name

    @instance_name.setter
    def instance_name(self, value: str) -> None:
        self._instance_name = value

    def get_registration_payload(self) -> dict:
        """Get the full payload for registering this instance with the cloud.

        Includes ``app_version`` — the fleet view (and
        ``app_config.min_supported_app_version`` gating) is blind without a
        record of what is actually RUNNING in the field. Registration is the
        canonical write for this: it happens on every startup and on every
        re-configure, and adds no request of its own.
        """
        info = self.system_info
        return {
            "instance_id": self.instance_id,
            "app_version": current_app_version(),
            "instance_name": self._instance_name,
            "platform": info.get("platform"),
            "os_version": info.get("os_version"),
            "architecture": info.get("architecture"),
            "hostname": info.get("hostname"),
            "username": info.get("username"),
            "python_version": info.get("python_version"),
            "home_dir": info.get("home_dir"),
            "cpu_model": info.get("cpu_model"),
            "cpu_cores": info.get("cpu_cores"),
            "ram_total_gb": info.get("ram_total_gb"),
            "hardware_uuid": info.get("hardware_uuid"),
            "serial_number": info.get("serial_number"),
            "board_id": info.get("board_id"),
        }

    def _instance_record(self) -> dict:
        try:
            value = json.loads(INSTANCE_FILE.read_text())
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _save_instance_record(self, value: dict) -> None:
        INSTANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = INSTANCE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True))
        temporary.replace(INSTANCE_FILE)

    @staticmethod
    def _valid_registered_device(
        value: object,
        *,
        expected_origin: str,
        expected_user_id: str,
        expected_instance_id: str,
    ) -> RegisteredDeviceIdentity | None:
        if not isinstance(value, dict):
            return None
        row_id = value.get("app_instance_id")
        try:
            canonical_id = str(uuid.UUID(str(row_id)))
        except (TypeError, ValueError):
            return None
        if (
            str(row_id) != canonical_id
            or value.get("registration_origin") != expected_origin
            or value.get("user_id") != expected_user_id
            or value.get("instance_id") != expected_instance_id
        ):
            return None
        return RegisteredDeviceIdentity(canonical_id, expected_user_id, expected_instance_id)

    def clear_registered_device_identity(self) -> bool:
        """Fence actor-derived row identity, then best-effort remove it from disk."""
        self._registered_device_identity_fenced = True
        value = self._instance_record()
        if value.pop("registered_device", None) is None:
            return True
        try:
            self._save_instance_record(value)
        except OSError:
            logger.warning("Registered device identity cleanup could not complete")
            return False
        return True

    def retain_registered_device_identity(
        self,
        *,
        expected_origin: str,
        expected_user_id: str,
        expected_instance_id: str,
    ) -> bool:
        """Retain only an exact persisted binding during first configuration."""
        if (
            self._registered_device_identity_fenced
            or expected_instance_id != self.instance_id
        ):
            self.clear_registered_device_identity()
            return False
        identity = self._valid_registered_device(
            self._instance_record().get("registered_device"),
            expected_origin=expected_origin,
            expected_user_id=expected_user_id,
            expected_instance_id=expected_instance_id,
        )
        if identity is None:
            self.clear_registered_device_identity()
            return False
        return True

    def accept_registration_identity(
        self,
        row: object,
        *,
        expected_origin: str,
        expected_user_id: str,
    ) -> bool:
        """Persist only the exact app_instances row created for the daemon owner."""
        if not isinstance(row, dict):
            self.clear_registered_device_identity()
            return False
        row_id, user_id, instance_id = row.get("id"), row.get("user_id"), row.get("instance_id")
        try:
            canonical_id = str(uuid.UUID(str(row_id)))
        except (TypeError, ValueError):
            self.clear_registered_device_identity()
            return False
        if (
            str(row_id) != canonical_id
            or user_id != expected_user_id
            or instance_id != self.instance_id
        ):
            self.clear_registered_device_identity()
            return False
        value = self._instance_record()
        value["instance_id"] = self.instance_id
        value["registered_device"] = {
            "app_instance_id": canonical_id,
            "registration_origin": expected_origin,
            "user_id": expected_user_id,
            "instance_id": self.instance_id,
        }
        try:
            self._save_instance_record(value)
        except OSError:
            self._registered_device_identity_fenced = True
            logger.warning("Registered device identity could not be persisted")
            return False
        self._registered_device_identity_fenced = False
        return True

    async def registered_device_identity(self) -> RegisteredDeviceIdentity | None:
        """Read the binding fresh and re-check current configuration and daemon owner."""
        from app.services.sync_client import get_sync_client
        from app.services.cloud_sync.settings_sync import get_settings_sync

        settings = get_settings_sync()
        grant = await get_sync_client().access_grant()
        expected_origin = settings.registration_origin
        expected_user_id = settings.expected_user_id
        expected_instance_id = settings.expected_instance_id
        if (
            self._registered_device_identity_fenced
            or not settings.is_configured
            or grant is None
            or expected_origin is None
            or expected_user_id is None
            or expected_instance_id is None
            or grant[1] != expected_user_id
        ):
            self.clear_registered_device_identity()
            return None
        identity = self._valid_registered_device(
            self._instance_record().get("registered_device"),
            expected_origin=expected_origin,
            expected_user_id=expected_user_id,
            expected_instance_id=expected_instance_id,
        )
        if identity is None or identity.instance_id != self.instance_id:
            self.clear_registered_device_identity()
            return None
        return identity

    async def update_tunnel_url(
        self,
        tunnel_url: Optional[str],
        active: bool,
        tunnel_ws_url: Optional[str] = None,
    ) -> bool:
        """Push the current tunnel URLs (REST + WS) and active state to Supabase.

        Called when a tunnel starts or stops. Best-effort — never raises.
        Returns True on success, False on failure.

        tunnel_ws_url is derived automatically if not supplied:
          https://xyz.trycloudflare.com → wss://xyz.trycloudflare.com/ws
        """
        try:
            from app.services.cloud_sync.settings_sync import get_settings_sync
            from datetime import datetime, timezone
            import httpx

            sync = get_settings_sync()
            if not sync.is_configured:
                logger.debug("update_tunnel_url: settings sync not configured, skipping")
                return False

            # Derive WS URL from REST URL if not explicitly provided
            if tunnel_url and not tunnel_ws_url:
                tunnel_ws_url = tunnel_url.replace("https://", "wss://") + "/ws"

            now = datetime.now(timezone.utc).isoformat()
            user_id, headers = await sync._request_context()
            payload = {
                "tunnel_url": tunnel_url,
                "tunnel_ws_url": tunnel_ws_url,
                "tunnel_active": active,
                "tunnel_updated_at": now,
                "last_seen": now,
            }
            url = (
                f"{sync._supabase_url}/rest/v1/app_instances"
                f"?instance_id=eq.{self.instance_id}&user_id=eq.{user_id}"
            )
            headers = {
                **headers,
                "Prefer": "return=minimal",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.patch(url, json=payload, headers=headers)
                if resp.is_success:
                    logger.debug(
                        "Tunnel URLs updated in Supabase: active=%s rest=%s ws=%s",
                        active, tunnel_url, tunnel_ws_url,
                    )
                    return True
                else:
                    logger.warning(
                        "update_tunnel_url failed: %d %s",
                        resp.status_code, resp.text[:200],
                    )
                    return False
        except Exception as exc:
            logger.debug("update_tunnel_url exception: %s", exc)
            return False


# Module-level singleton
_instance_manager: Optional[InstanceManager] = None


def get_instance_manager() -> InstanceManager:
    global _instance_manager
    if _instance_manager is None:
        _instance_manager = InstanceManager()
    return _instance_manager
