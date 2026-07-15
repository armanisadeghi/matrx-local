"""Cloud settings sync engine.

Handles bidirectional synchronization of all app settings between
the local JSON file and the Supabase cloud database.
See docs/SYNC_CONTRACT.md — the ratified sync contract — for how this
subsystem fits the replica doctrine (local JSON is the working copy;
the cloud row is the durable record).

Settings are stored as a single JSON blob per user+instance. On sync,
the engine compares updated_at timestamps and the newer side wins —
acceptable ONLY because the cloud row is scoped per-instance, so the
only competing writer for a row is this instance itself.

PING-PONG GUARD (do not remove): every pull path persists the CLOUD
timestamp via _save_local(updated_at=...). Stamping now() on pull made
every pull look like a newer local edit — the next sync() pushed, which
bumped the cloud timestamp, which made the following sync() pull; the
in_sync state was unreachable and last-writer-wins windows opened against
other devices (fixed in 9ca565245; pinned by tests/parity).

API keys are NOT part of this blob: they live in the SQLite app_settings
store (ApiKeysRepo) and never sync to the cloud.

Offline mode: if cloud is unreachable, local settings are used and
sync is retried on next startup or manual trigger.

ERROR VISIBILITY POLICY
-----------------------
Cloud operations MUST NOT silently swallow errors. Every HTTP call
captures the full error (status code + response body) into `_last_error`
and logs it at WARNING level so it appears in the activity log. Callers
receive a structured dict with status="error" and a reason string so the
frontend can surface the problem to the user.

ORPHAN INSTANCE POLICY
-----------------------
An "orphan instance" is one whose instance_id is not present in the cloud
`app_instances` table after a successful configure() call. This is treated
as a P0 state. The engine logs at ERROR level, sets `_is_orphan = True`,
and the frontend must surface a prominent warning. The app continues to
work locally — we never block the user — but cloud features are degraded
until registration succeeds.
"""

from __future__ import annotations

import json
import logging
import os
import time
import shutil
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.config import MATRX_HOME_DIR  # noqa: E402
LOCAL_SETTINGS_FILE = MATRX_HOME_DIR / "settings.json"

# Default settings — every possible setting with its default value.
# This MUST stay in sync with DEFAULTS in desktop/src/lib/settings.ts.
DEFAULT_SETTINGS: dict[str, Any] = {
    # Application
    "launch_on_startup": False,
    "minimize_to_tray": True,
    "theme": "dark",
    # Updates
    "auto_check_updates": True,
    "update_check_interval": 240,
    # Scraping
    "headless_scraping": True,
    "scrape_delay": 1.0,
    # Proxy — port-base offset +40: live 22140 → 22180, dev 22240 → 22280
    # (MXL-D-043 dev/live isolation; same formula as proxy/server.py
    # DEFAULT_PROXY_PORT and desktop settings.ts DEFAULTS.proxyPort).
    "proxy_enabled": True,
    "proxy_port": int(os.environ.get("MATRX_PORT_BASE", "22140")) + 40,
    # Remote access
    "tunnel_enabled": False,
    # Instance
    "instance_name": "My Computer",
    # File sync — the desktop replica of the matrx-files cloud tree.
    # off      → engine idles (no watcher, no pulls)
    # pointers → metadata tree + zero-byte placeholders; bytes on demand
    # full     → bytes mirrored locally, bidirectional, offline-capable
    "file_sync_mode": "pointers",
    # Notifications
    "notification_sound": True,
    "notification_sound_style": "chime",
    # Wake word
    "wake_word_enabled": True,
    "wake_word_listen_on_startup": True,
    "wake_word_engine": "whisper",
    "wake_word_oww_model": "hey_jarvis",
    "wake_word_oww_threshold": 0.5,
    "wake_word_custom_keyword": "hey matrix",
    # Chat & AI
    "chat_default_model": "",  # Empty = use first model from DB; never hard-code a model name
    "chat_default_mode": "chat",
    "chat_max_conversations": 100,
    "chat_default_system_prompt_id": "",
    # Local LLM
    "llm_default_model": "",
    "llm_default_gpu_layers": -1,
    "llm_default_context_length": 8192,
    "llm_auto_start_server": False,
    "llm_chat_temperature": 0.7,
    "llm_chat_top_p": 0.8,
    "llm_chat_top_k": 20,
    "llm_chat_max_tokens": 1024,
    "llm_reasoning_temperature": 0.6,
    "llm_reasoning_top_p": 0.95,
    "llm_reasoning_top_k": 20,
    "llm_reasoning_max_tokens": 4096,
    "llm_enable_thinking": False,
    "llm_tool_call_temperature": 0.7,
    "llm_tool_call_top_p": 0.8,
    "llm_tool_call_top_k": 20,
    "llm_structured_output_temperature": 0.1,
    "llm_stream_max_tokens": 1024,
    # Transcription
    "transcription_default_model": "",
    "transcription_auto_init": True,
    "transcription_audio_device": "",
    "transcription_processing_timeout": 15000,
    # Text to Speech
    "tts_default_voice": "af_heart",
    "tts_default_speed": 1.0,
    "tts_auto_download_model": False,
    "tts_favorite_voices": [],
    "tts_chat_voice": "",
    "tts_chat_speed": 0,
    "tts_notification_voice": "",
    "tts_read_aloud_enabled": True,
    "tts_read_aloud_auto_play": False,
    "tts_streaming_threshold": 200,
    "tts_auto_clean_markdown": False,
    # Extension bridge
    "extension_broadcast_enabled": True,
    # Voice assistant (mirror desktop/src/lib/settings.ts defaults)
    "voice_assistant_system_prompt_id": "builtin-voice-assistant",
    "voice_silence_timeout_ms": 1400,
    "voice_restore_prompt_on_exit": True,
    # UI
    "sidebar_collapsed": False,
}

RESET_SCOPES: dict[str, tuple[str, ...]] = {
    "application": ("launch_", "minimize_", "theme", "auto_check_", "update_check_", "sidebar_", "notification_"),
    "network": ("headless_", "scrape_", "proxy_", "tunnel_", "file_sync_", "extension_"),
    "ai": ("chat_", "llm_", "transcription_", "tts_", "wake_word_", "voice_"),
    "identity": ("instance_name",),
}


class SettingsSync:
    """Manages local settings persistence and cloud synchronization."""

    def __init__(self) -> None:
        self._settings: dict[str, Any] = {}
        self._local_updated_at: Optional[str] = None
        self._supabase_url: str = ""
        self._supabase_key: str = ""
        self._jwt: Optional[str] = None
        self._user_id: Optional[str] = None
        self._instance_id: Optional[str] = None
        self._configured = False

        # Visibility state — surfaces to /cloud/debug endpoint
        self._is_orphan: bool = False
        self._last_error: Optional[str] = None
        self._last_registration_at: Optional[str] = None
        # Consecutive heartbeat failures (network or HTTP). Lets the engine
        # distinguish a transient blip from a sustained outage instead of
        # silently logging every failure at DEBUG and reporting healthy.
        self._heartbeat_failures: int = 0
        self._last_registration_result: Optional[str] = None  # "ok" | "error:<msg>"
        self._configure_called_at: Optional[str] = None

        self._load_local()

    # ── configuration ───────────────────────────────────────────────────

    def configure(
        self,
        supabase_url: str,
        supabase_key: str,
        jwt: str,
        user_id: str,
        instance_id: str,
    ) -> None:
        """Configure cloud connection parameters."""
        self._supabase_url = supabase_url.rstrip("/")
        self._supabase_key = supabase_key
        self._jwt = jwt
        self._user_id = user_id
        self._instance_id = instance_id
        self._configured = bool(supabase_url and supabase_key and jwt and user_id)
        self._configure_called_at = datetime.now(timezone.utc).isoformat()
        if self._configured:
            logger.info(
                "Cloud sync configured: user_id=%s instance_id=%s url=%s",
                user_id,
                instance_id,
                supabase_url,
            )
        else:
            logger.warning(
                "Cloud sync configure() called but missing required fields: "
                "url=%r key=%r jwt_present=%s user_id=%r",
                bool(supabase_url),
                bool(supabase_key),
                bool(jwt),
                bool(user_id),
            )

    @property
    def is_configured(self) -> bool:
        return self._configured

    @property
    def is_orphan(self) -> bool:
        return self._is_orphan

    def get_debug_state(self) -> dict[str, Any]:
        """Return full diagnostic state for /cloud/debug endpoint."""
        return {
            "is_configured": self._configured,
            "is_orphan": self._is_orphan,
            "user_id": self._user_id,
            "instance_id": self._instance_id,
            "supabase_url": self._supabase_url or None,
            "configure_called_at": self._configure_called_at,
            "last_registration_at": self._last_registration_at,
            "last_registration_result": self._last_registration_result,
            "last_error": self._last_error,
            "heartbeat_failures": self._heartbeat_failures,
        }

    # ── local settings ──────────────────────────────────────────────────

    def get_all(self) -> dict[str, Any]:
        """Get all current settings (merged with defaults)."""
        merged = {**DEFAULT_SETTINGS, **self._settings}
        return merged

    def get(self, key: str, default: Any = None) -> Any:
        """Get a single setting value."""
        return self._settings.get(key, DEFAULT_SETTINGS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        """Set a single setting and persist locally."""
        self._settings[key] = value
        self._save_local()

    def set_many(self, updates: dict[str, Any]) -> None:
        """Update multiple settings at once."""
        self._settings.update(updates)
        self._save_local()

    def reset_to_defaults(self) -> None:
        """Internal full reset. HTTP callers must use preview/apply_reset."""
        self.apply_reset("all", confirmed=True)

    def preview_reset(self, scope: str) -> dict[str, Any]:
        if scope == "all":
            keys = set(DEFAULT_SETTINGS) | set(self._settings)
        elif scope in RESET_SCOPES:
            prefixes = RESET_SCOPES[scope]
            keys = {key for key in DEFAULT_SETTINGS if any(key == p or key.startswith(p) for p in prefixes)}
        else:
            raise ValueError(f"Unknown reset scope: {scope}")
        changes = {
            key: {"current": self.get(key), "default": DEFAULT_SETTINGS.get(key)}
            for key in sorted(keys)
            if self.get(key) != DEFAULT_SETTINGS.get(key)
        }
        return {"scope": scope, "keys": sorted(keys), "changes": changes, "change_count": len(changes)}

    def apply_reset(self, scope: str, *, confirmed: bool) -> dict[str, Any]:
        preview = self.preview_reset(scope)
        if not confirmed:
            raise ValueError("Reset requires explicit confirmation")
        backup_path: str | None = None
        if LOCAL_SETTINGS_FILE.exists():
            backup = LOCAL_SETTINGS_FILE.with_name(
                f"settings.backup-{time.strftime('%Y%m%d-%H%M%S')}.json"
            )
            shutil.copy2(LOCAL_SETTINGS_FILE, backup)
            backup_path = str(backup)
        for key in preview["keys"]:
            if key in DEFAULT_SETTINGS:
                self._settings[key] = DEFAULT_SETTINGS[key]
            else:
                self._settings.pop(key, None)
        self._save_local()
        return {**preview, "applied": True, "backup_path": backup_path}

    # ── local file I/O ──────────────────────────────────────────────────

    def _load_local(self) -> None:
        """Load settings from local JSON file."""
        if LOCAL_SETTINGS_FILE.exists():
            try:
                data = json.loads(LOCAL_SETTINGS_FILE.read_text())
                self._settings = data.get("settings", {})
                self._local_updated_at = data.get("updated_at")
            except Exception:
                logger.warning("Corrupted local settings file, using defaults")
                self._settings = dict(DEFAULT_SETTINGS)
        else:
            self._settings = dict(DEFAULT_SETTINGS)

    def _save_local(self, updated_at: str | None = None) -> None:
        """Persist settings to local JSON file.

        ``updated_at`` lets pull paths record the CLOUD timestamp. Always
        stamping now() here made every pull look like a newer local edit, so
        the next sync() pushed, bumping the cloud timestamp, which made the
        following sync() pull — the in_sync state was unreachable and the
        ping-pong opened last-writer-wins windows against other devices.
        """
        self._local_updated_at = updated_at or datetime.now(timezone.utc).isoformat()
        payload = {
            "settings": self._settings,
            "updated_at": self._local_updated_at,
        }
        LOCAL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_SETTINGS_FILE.write_text(json.dumps(payload, indent=2))

    # ── cloud sync ──────────────────────────────────────────────────────

    async def sync(self) -> dict:
        """Perform a full sync: compare timestamps, merge, push/pull."""
        if not self._configured:
            return {"status": "skipped", "reason": "not_configured"}

        try:
            cloud = await self._fetch_cloud_settings()

            if cloud is None:
                await self._push_to_cloud()
                await self._update_sync_status("push", "success")
                return {"status": "pushed", "reason": "no_cloud_record"}

            cloud_settings = cloud.get("settings_json", {})
            cloud_updated = cloud.get("updated_at", "")
            local_updated = self._local_updated_at or ""

            if cloud_updated > local_updated:
                self._settings = {**DEFAULT_SETTINGS, **cloud_settings}
                self._save_local(updated_at=cloud_updated)
                await self._update_sync_status("pull", "success")
                return {"status": "pulled", "reason": "cloud_newer"}
            elif local_updated > cloud_updated:
                await self._push_to_cloud()
                await self._update_sync_status("push", "success")
                return {"status": "pushed", "reason": "local_newer"}
            else:
                await self._update_sync_status("full", "success")
                return {"status": "in_sync", "reason": "timestamps_match"}

        except Exception as exc:
            msg = str(exc)
            self._last_error = msg
            logger.warning("Cloud sync failed: %s", msg)
            try:
                await self._update_sync_status("full", "error", msg)
            except Exception:
                pass
            return {"status": "error", "reason": msg}

    async def push_to_cloud(self) -> dict:
        """Force push local settings to cloud."""
        if not self._configured:
            return {"status": "error", "reason": "not_configured"}
        try:
            await self._push_to_cloud()
            await self._update_sync_status("push", "success")
            return {"status": "pushed"}
        except Exception as exc:
            msg = str(exc)
            self._last_error = msg
            return {"status": "error", "reason": msg}

    async def pull_from_cloud(self) -> dict:
        """Force pull cloud settings to local."""
        if not self._configured:
            return {"status": "error", "reason": "not_configured"}
        try:
            cloud = await self._fetch_cloud_settings()
            if cloud is None:
                return {"status": "error", "reason": "no_cloud_record"}
            self._settings = {**DEFAULT_SETTINGS, **cloud.get("settings_json", {})}
            self._save_local(updated_at=cloud.get("updated_at") or None)
            await self._update_sync_status("pull", "success")
            return {"status": "pulled", "settings": self.get_all()}
        except Exception as exc:
            msg = str(exc)
            self._last_error = msg
            return {"status": "error", "reason": msg}

    # ── Supabase REST helpers ───────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._supabase_key,
            "Authorization": f"Bearer {self._jwt}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
            # Explicitly target the public schema — required when the Supabase
            # project exposes multiple schemas (e.g. api + public) and public
            # is not the default exposed schema in PostgREST config.
            "Accept-Profile": "public",
            "Content-Profile": "public",
        }

    def _log_http_error(self, operation: str, resp: Any) -> str:
        """Log and return a descriptive error string from a non-2xx response."""
        try:
            body = resp.text[:500]
        except Exception:
            body = "<unreadable>"
        msg = f"{operation} failed: HTTP {resp.status_code} — {body}"
        self._last_error = msg
        logger.warning(msg)
        return msg

    async def _fetch_cloud_settings(self) -> Optional[dict]:
        """Fetch this instance's settings from Supabase."""
        import httpx

        url = (
            f"{self._supabase_url}/rest/v1/app_settings"
            f"?user_id=eq.{self._user_id}"
            f"&instance_id=eq.{self._instance_id}"
            f"&select=*"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self._headers())
            if not resp.is_success:
                raise RuntimeError(self._log_http_error("fetch_cloud_settings", resp))
            rows = resp.json()
            return rows[0] if rows else None

    async def _push_to_cloud(self) -> None:
        """Upsert local settings to Supabase."""
        import httpx

        payload = {
            "user_id": self._user_id,
            "instance_id": self._instance_id,
            "settings_json": self.get_all(),
        }
        # on_conflict tells PostgREST which unique constraint to use, preventing
        # 409 errors on the second and subsequent pushes from the same instance.
        url = f"{self._supabase_url}/rest/v1/app_settings?on_conflict=user_id,instance_id"
        headers = {
            **self._headers(),
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if not resp.is_success:
                raise RuntimeError(self._log_http_error("push_to_cloud", resp))

    async def _update_sync_status(
        self, direction: str, result: str, error: str = ""
    ) -> None:
        """Update the sync_status record in Supabase."""
        import httpx

        payload = {
            "user_id": self._user_id,
            "instance_id": self._instance_id,
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
            "last_sync_direction": direction,
            "last_sync_result": result,
            "error_message": error or None,
        }
        url = f"{self._supabase_url}/rest/v1/app_sync_status"
        headers = {
            **self._headers(),
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if not resp.is_success:
                    # Non-fatal but we want to see it
                    logger.debug(
                        "update_sync_status returned HTTP %s: %s",
                        resp.status_code,
                        resp.text[:200],
                    )
        except Exception as exc:
            logger.debug("Failed to update sync status: %s", exc)

    async def register_instance(self, registration: dict) -> Optional[dict]:
        """Register or update this instance in the cloud.

        Uses an explicit upsert via POST with on_conflict targeting the
        (user_id, instance_id) unique constraint so re-registrations on
        every startup correctly update the row rather than hitting a 409.

        Sets _is_orphan=True and logs at ERROR level only on genuine failures
        (not on expected conflicts that resolve via upsert).
        """
        if not self._configured:
            logger.warning(
                "register_instance called before configure() — instance will be orphaned"
            )
            self._is_orphan = True
            return None

        import httpx

        payload = {
            "user_id": self._user_id,
            **registration,
            "is_active": True,
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        # on_conflict tells PostgREST exactly which unique constraint to use for
        # the merge, preventing the 409 that occurs when it can't infer it.
        url = f"{self._supabase_url}/rest/v1/app_instances?on_conflict=user_id,instance_id"
        headers = {
            **self._headers(),
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        self._last_registration_at = datetime.now(timezone.utc).isoformat()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if not resp.is_success:
                    err = self._log_http_error("register_instance", resp)
                    self._last_registration_result = f"error:{err}"
                    self._is_orphan = True
                    logger.error(
                        "ORPHAN INSTANCE — instance_id=%s could not be registered with Supabase. "
                        "Cloud features (sync, remote control, multi-device) are unavailable. "
                        "Error: %s",
                        self._instance_id,
                        err,
                    )
                    return None
                rows = resp.json()
                row = rows[0] if rows else None
                if row:
                    self._is_orphan = False
                    self._last_registration_result = "ok"
                    logger.info(
                        "Instance registered successfully: instance_id=%s user_id=%s",
                        self._instance_id,
                        self._user_id,
                    )
                else:
                    # Supabase returned 2xx but empty body — should not happen with upsert
                    self._last_registration_result = "error:empty_response"
                    self._is_orphan = True
                    logger.error(
                        "ORPHAN INSTANCE — register_instance returned 2xx but empty body. "
                        "This usually means an RLS policy is blocking the upsert. "
                        "instance_id=%s user_id=%s",
                        self._instance_id,
                        self._user_id,
                    )
                return row
        except Exception as exc:
            msg = str(exc)
            self._last_error = msg
            self._last_registration_result = f"error:{msg}"
            self._is_orphan = True
            logger.error(
                "ORPHAN INSTANCE — register_instance raised exception: %s. "
                "instance_id=%s user_id=%s",
                msg,
                self._instance_id,
                self._user_id,
            )
            return None

    async def list_instances(self) -> list[dict]:
        """List all instances for the current user."""
        if not self._configured:
            return []

        import httpx

        url = (
            f"{self._supabase_url}/rest/v1/app_instances"
            f"?user_id=eq.{self._user_id}"
            f"&is_active=eq.true"
            f"&select=*"
            f"&order=last_seen.desc"
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=self._headers())
                if not resp.is_success:
                    self._log_http_error("list_instances", resp)
                    return []
                instances = resp.json()
                # Orphan check: if we're configured but this instance isn't in the list
                if self._instance_id and not any(
                    i.get("instance_id") == self._instance_id for i in instances
                ):
                    self._is_orphan = True
                    logger.error(
                        "ORPHAN INSTANCE — instance_id=%s is not present in cloud app_instances "
                        "for user_id=%s. %d other instance(s) found. "
                        "Cloud sync and remote control unavailable until re-registration succeeds.",
                        self._instance_id,
                        self._user_id,
                        len(instances),
                    )
                elif self._instance_id:
                    self._is_orphan = False
                return instances
        except Exception as exc:
            msg = str(exc)
            self._last_error = msg
            logger.warning("list_instances failed: %s", msg)
            return []

    async def heartbeat(self) -> None:
        """Update last_seen and refresh tunnel state for this instance.

        The tunnel URL is included so remote devices always see a fresh value —
        if the engine is running with a tunnel, last_seen proves the URL is still valid.
        If no tunnel is active, tunnel_active is explicitly set to False so stale
        rows don't mislead clients.
        """
        if not self._configured:
            return

        import httpx

        # Include current tunnel state (REST + WS) so remote devices never see stale URLs.
        # Both URLs are written every heartbeat — if the tunnel restarted and got a new
        # trycloudflare.com address the DB is corrected within one heartbeat interval.
        try:
            from app.services.tunnel.manager import get_tunnel_manager
            tm = get_tunnel_manager()
            tunnel_payload: dict = {
                "tunnel_url": tm.url if tm.running else None,
                "tunnel_ws_url": tm.ws_url if tm.running else None,
                "tunnel_active": tm.running,
            }
        except Exception:
            tunnel_payload = {}

        url = (
            f"{self._supabase_url}/rest/v1/app_instances"
            f"?user_id=eq.{self._user_id}"
            f"&instance_id=eq.{self._instance_id}"
        )
        headers = {**self._headers(), "Prefer": "return=minimal"}
        payload = {"last_seen": datetime.now(timezone.utc).isoformat(), **tunnel_payload}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.patch(url, json=payload, headers=headers)
                if resp.is_success:
                    self._heartbeat_failures = 0
                else:
                    # Heartbeat failure may indicate orphan state
                    self._log_http_error("heartbeat", resp)
                    if resp.status_code in (401, 403):
                        self._is_orphan = True
                        logger.error(
                            "ORPHAN INSTANCE — heartbeat returned HTTP %s. "
                            "JWT may be expired or RLS is blocking. instance_id=%s",
                            resp.status_code,
                            self._instance_id,
                        )
                    else:
                        self._note_heartbeat_failure(f"HTTP {resp.status_code}")
        except Exception as exc:
            # Network failure (offline, DNS, timeout). A single blip is
            # expected; a sustained run means the instance is unreachable from
            # the cloud even though it's running — surface that, don't bury it.
            self._note_heartbeat_failure(str(exc))

    # Number of consecutive failures before we escalate from DEBUG to a single
    # WARNING (≈ this many heartbeat intervals of being unreachable).
    _HEARTBEAT_FAIL_THRESHOLD = 3

    def _note_heartbeat_failure(self, reason: str) -> None:
        self._heartbeat_failures += 1
        self._last_error = f"heartbeat failed ({reason})"
        if self._heartbeat_failures == self._HEARTBEAT_FAIL_THRESHOLD:
            logger.warning(
                "Heartbeat has failed %d times in a row — this instance may be "
                "unreachable from the cloud (offline / DNS / server down). "
                "Last reason: %s",
                self._heartbeat_failures,
                reason,
            )
        else:
            logger.debug(
                "Heartbeat failed (#%d, non-critical): %s",
                self._heartbeat_failures,
                reason,
            )


# Module-level singleton
_settings_sync: Optional[SettingsSync] = None


def get_settings_sync() -> SettingsSync:
    global _settings_sync
    if _settings_sync is None:
        _settings_sync = SettingsSync()
    return _settings_sync
