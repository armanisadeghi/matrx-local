"""Supabase Broadcast plumb for engine ↔ matrx-extend cross-machine fallback.

Live routing (Phase 7): inbound envelopes on the per-user channel are
parsed as v2 CrossComponentEnvelopes and dispatched by
`app/api/cross_component_router.py`. `kind:"rpc"` envelopes route into
the SAME command registry as `POST /extension/rpc`
(`extension_handlers.HANDLERS`) and are answered with a reply envelope
on the same channel. Routing is gated behind the
`extension_broadcast_enabled` user setting (default: ON), surfaced in
the desktop Settings UI under Remote Access.

Channel name: `matrx-local-bridge:<userId>` (per-user-scoped, analogous
to the existing matrx-extension-bridge channel used for the frontend
side).

Envelope shapes on this channel:

  * v2 CrossComponentEnvelope (canonical — see
    `app/api/cross_component_envelope.py`):
    `{v, kind, direction, action, requestId, payload, timestamp,
      fromInstance, toInstance?}`
  * legacy engine→extension push (still emitted by
    `publish_to_extension` for extension.invoke/result over Broadcast):
    `{direction, type, callId, payload, timestamp}`

Public API:

    connect_broadcast(user_id)          -> None
    disconnect_broadcast(user_id)       -> None
    publish_to_extension(user_id,
                         type, payload,
                         call_id=None)  -> bool
    publish_envelope(user_id, envelope) -> bool   # v2 envelope, sent as-is

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
      3. Register a `broadcast` listener that routes every received
         envelope through `cross_component_router.route_envelope` —
         rpc envelopes dispatch into the extension command registry
         and are answered on this channel.

    Idempotent: connecting an already-connected user_id is a no-op.
    """
    # Cloud coordination isolation (dev): a coordination-silent engine must not
    # attach to the per-USER bridge channel at all — every subscriber executes
    # the SAME delegation wakes and RPC-over-broadcast work, so a dev engine on
    # this account would race the installed app and hand back false signals.
    # run.py defaults MATRX_CLOUD_PARTICIPATION=0 for source-run engines.
    from app.config import CLOUD_PARTICIPATION_ENABLED

    if not CLOUD_PARTICIPATION_ENABLED:
        logger.info(
            "[extension_broadcast] connect_broadcast(user=%s) SKIPPED — cloud "
            "coordination disabled (MATRX_CLOUD_PARTICIPATION=0, dev isolation). "
            "This engine stays off the shared bridge channel.",
            user_id,
        )
        return

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
                # Inbound envelopes route through the cross-component
                # dispatcher: kind:"rpc" is invoked against the SAME
                # HANDLERS registry as POST /extension/rpc and answered
                # with a reply envelope on this channel. See
                # app/api/cross_component_router.py for routing and
                # app/api/cross_component_envelope.py for the v2 schema.
                from app.api.cross_component_router import route_envelope
                # supabase-py wraps the user-supplied event payload in
                # {"type": "broadcast", "event": "<event-name>", "payload": {...}}
                # — unwrap if needed, otherwise pass through.
                inner = payload.get("payload", payload) if isinstance(payload, dict) else payload
                if isinstance(inner, dict):
                    # user_id is the channel scope — the router needs it to
                    # publish rpc replies back on the same channel.
                    route_envelope(inner, user_id)
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

    return await _send_on_channel(user_id, channel, envelope, type, call_id)


async def publish_envelope(user_id: str, envelope: Dict[str, Any]) -> bool:
    """Publish a fully-formed v2 CrossComponentEnvelope dict as-is.

    Used by `cross_component_router` for rpc REPLY envelopes, which carry
    the v2 shape (`kind` / `action` / `requestId` / `fromInstance` /
    `toInstance`) rather than the legacy `publish_to_extension` shape.
    Returns True on successful send, False on missing channel / error /
    disabled feature flag.
    """
    if not is_broadcast_enabled():
        _log_disabled(f"publish_envelope(action={envelope.get('action')})")
        return False

    channel = _channels.get(user_id)
    if channel is None:
        logger.warning(
            "[extension_broadcast] publish_envelope skipped — user=%s not connected",
            user_id,
        )
        return False

    return await _send_on_channel(
        user_id,
        channel,
        envelope,
        str(envelope.get("action")),
        envelope.get("requestId"),
    )


async def _send_on_channel(
    user_id: str,
    channel: Any,
    envelope: Dict[str, Any],
    label: str,
    correlation_id: Optional[str],
) -> bool:
    try:
        # realtime-py signature is send_broadcast(event, data) — the old
        # payload= kwarg raised TypeError on every call, so engine→extension
        # publish silently failed 100% of the time.
        await channel.send_broadcast("message", envelope)
        logger.debug(
            "[extension_broadcast] published user=%s label=%s correlation_id=%s",
            user_id,
            label,
            correlation_id,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[extension_broadcast] publish failed user=%s label=%s err=%s",
            user_id,
            label,
            exc,
            exc_info=True,
        )
        return False
