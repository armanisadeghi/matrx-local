"""Supabase Broadcast plumb for engine ↔ matrx-extend cross-machine fallback.

Phase 2 (master plan section C2.d) ships SUBSTRATE ONLY — the helpers
exist so future cross-machine flows have a place to land without
rebuilding. Active routing is gated behind the
`extension_broadcast_enabled` user setting (default: ON), surfaced in
the desktop Settings UI under Remote Access. Phase 3's C-bridge
orchestrator will read the same flag.

Channel name: `matrx-local-bridge:<userId>` (per-user-scoped, analogous
to the existing matrx-extension-bridge channel used for the frontend
side).

Envelope shape (engine ↔ extension over Broadcast):

    {
      "direction": "engine->extension" | "extension->engine",
      "type": str,            # "extension.invoke" | "extension.result"
                              # | "ping" | "pong"
      "callId": str | None,
      "payload": dict,
      "timestamp": int,       # ms since epoch
    }

Public API:

    connect_broadcast(user_id)          -> None
    disconnect_broadcast(user_id)       -> None
    publish_to_extension(user_id,
                         type, payload,
                         call_id=None)  -> bool

When the feature flag is OFF, every helper returns immediately with a
debug log line — there is no Supabase client, no realtime subscription,
no network traffic. The flag is read live from settings on every call
so the user can toggle it in the desktop UI without an engine restart.

Lifecycle ownership: this module does NOT auto-connect on engine
startup. The future C-bridge orchestrator decides when to spin a
Broadcast subscription up (typically: when an extension session
registers and the user has a known Supabase identity).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from app.common.system_logger import get_logger
from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL
from app.services.cloud_sync.settings_sync import get_settings_sync

logger = get_logger()


def is_broadcast_enabled() -> bool:
    """Return the live state of the broadcast plumb feature flag.

    Read from the user's settings (default: True). The setting is
    persisted in ~/.matrx/settings.json and synced to Supabase via the
    standard cloud-settings flow, so toggling it in the desktop UI
    takes effect immediately for new helper calls — no engine restart.
    """
    return bool(get_settings_sync().get("extension_broadcast_enabled", True))


# Channel name template — `matrx-local-bridge:<user_id>`.
_CHANNEL_PREFIX = "matrx-local-bridge"


def _channel_name(user_id: str) -> str:
    return f"{_CHANNEL_PREFIX}:{user_id}"


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Internal state — holds the per-user Supabase client + channel handle.
# Indexed by user_id so multi-tenant lifecycles can interleave cleanly.
# ---------------------------------------------------------------------------

_clients: Dict[str, Any] = {}      # user_id -> AsyncClient (Supabase)
_channels: Dict[str, Any] = {}     # user_id -> RealtimeChannel
_lock = asyncio.Lock()


def _log_disabled(action: str) -> None:
    logger.debug(
        "[extension_broadcast] %s skipped — broadcast plumb disabled "
        "(extension_broadcast_enabled=false in settings)",
        action,
    )


async def connect_broadcast(user_id: str) -> None:
    """Subscribe to the user's bridge channel.

    No-op when the feature flag is off. When enabled:
      1. Create an async Supabase client (URL + publishable key from
         `app.config`).
      2. Open a realtime channel `matrx-local-bridge:<user_id>`.
      3. Register a `broadcast` listener that LOGS the received envelope
         only — Phase 2 does not route inbound traffic. Phase 3 will
         replace the log-only handler with a router that dispatches
         based on `direction` + `type`.

    Idempotent: connecting an already-connected user_id is a no-op.
    """
    if not is_broadcast_enabled():
        _log_disabled(f"connect_broadcast(user_id={user_id})")
        return

    async with _lock:
        if user_id in _channels:
            logger.debug(
                "[extension_broadcast] connect_broadcast: user=%s already connected",
                user_id,
            )
            return

        try:
            # Defer the supabase import so module load stays cheap and
            # the import graph remains tsx-friendly (per matrx-extend
            # CLAUDE.md conventions about deferred env reads). The
            # supabase package is declared in pyproject.toml.
            from supabase import create_async_client  # type: ignore[import-not-found]

            client = await create_async_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
            channel = client.channel(_channel_name(user_id))

            def _on_broadcast(payload: Dict[str, Any]) -> None:
                # Phase 2: the log-only stub is replaced by the cross-component
                # router. See app/api/cross_component_router.py for the routing
                # logic; envelope parsing + back-compat lives in
                # app/api/cross_component_envelope.py.
                from app.api.cross_component_router import route_envelope
                # supabase-py wraps the user-supplied event payload in
                # {"type": "broadcast", "event": "<event-name>", "payload": {...}}
                # — unwrap if needed, otherwise pass through.
                inner = payload.get("payload", payload) if isinstance(payload, dict) else payload
                if isinstance(inner, dict):
                    route_envelope(inner)
                else:
                    logger.warning(
                        "[cross-component] non-dict broadcast payload dropped: type=%s",
                        type(inner).__name__,
                    )

            channel.on_broadcast(event="message", callback=_on_broadcast)
            await channel.subscribe()

            _clients[user_id] = client
            _channels[user_id] = channel
            logger.info(
                "[extension_broadcast] connected user=%s channel=%s",
                user_id,
                _channel_name(user_id),
            )
        except Exception as exc:
            logger.warning(
                "[extension_broadcast] connect failed user=%s err=%s",
                user_id,
                exc,
                exc_info=True,
            )


async def disconnect_broadcast(user_id: str) -> None:
    """Tear down the user's bridge channel + client. Idempotent.

    Runs unconditionally so that toggling the feature flag OFF mid-session
    (after a connect) still results in a clean teardown.
    """
    async with _lock:
        channel = _channels.pop(user_id, None)
        client = _clients.pop(user_id, None)

        if channel is not None:
            try:
                await channel.unsubscribe()
            except Exception:
                logger.debug(
                    "[extension_broadcast] unsubscribe failed user=%s",
                    user_id,
                    exc_info=True,
                )

        if client is not None:
            try:
                # supabase-py async clients expose `.realtime.disconnect()`
                # for full teardown; method name has stabilized in 2.x.
                if hasattr(client, "realtime") and hasattr(client.realtime, "disconnect"):
                    await client.realtime.disconnect()
            except Exception:
                logger.debug(
                    "[extension_broadcast] realtime.disconnect failed user=%s",
                    user_id,
                    exc_info=True,
                )

        logger.info("[extension_broadcast] disconnected user=%s", user_id)


async def publish_to_extension(
    user_id: str,
    type: str,
    payload: Dict[str, Any],
    call_id: Optional[str] = None,
) -> bool:
    """Publish an outbound envelope on the user's bridge channel.

    Returns True on successful send, False on missing channel / error.
    No-op (returns False) when the feature flag is off.

    Envelope shape matches the contract documented in this module's
    docstring — `direction` is hard-coded to `engine->extension` since
    that is the only direction this helper is used for. The opposite
    direction is consumed by the broadcast listener registered in
    `connect_broadcast`.
    """
    if not is_broadcast_enabled():
        _log_disabled(f"publish_to_extension(type={type})")
        return False

    channel = _channels.get(user_id)
    if channel is None:
        logger.warning(
            "[extension_broadcast] publish skipped — user=%s not connected",
            user_id,
        )
        return False

    envelope = {
        "direction": "engine->extension",
        "type": type,
        "callId": call_id,
        "payload": payload,
        "timestamp": _now_ms(),
    }

    try:
        # realtime-py signature is send_broadcast(event, data) — the old
        # payload= kwarg raised TypeError on every call, so engine→extension
        # publish silently failed 100% of the time.
        await channel.send_broadcast("message", envelope)
        logger.debug(
            "[extension_broadcast] published user=%s type=%s call_id=%s",
            user_id,
            type,
            call_id,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[extension_broadcast] publish failed user=%s type=%s err=%s",
            user_id,
            type,
            exc,
            exc_info=True,
        )
        return False
