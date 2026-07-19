"""Bridge-test HTTP surface for the matrx-extend ↔ matrx-local bridge.

These routes back the desktop frontend's "Bridge Test" page (Settings →
Bridge Test). They are NOT part of the public extension contract — the
extension itself only consumes `POST /extension/rpc` and `WS /extension/ws`.
The endpoints here exist so a human (or an agent driving the desktop UI)
can introspect and exercise the bridge from inside the Tauri app instead
of curl-ing the engine.

Auth: all routes inherit the engine's standard Bearer-token gate via the
upstream `AuthMiddleware` — none are added to `_PUBLIC_PATHS`.

Routes:

  * `GET  /extension/sessions`            — list active extension WS sessions
  * `POST /extension/sessions/disconnect` — close a session by id
  * `POST /extension/invoke`              — engine → browser tool dispatch
  * `GET  /extension/broadcast/status`    — broadcast plumb status
  * `POST /extension/broadcast/test`      — feature-flagged no-op publish
  * `WS   /extension/bridge-events`       — live event log fan-out

The WS route is an additive log fan-out — every bridge event (RPC in,
session open/close, invoke send, invoke result, broadcast publish) is
also pushed to every connected `bridge-events` socket so the Bridge Test
UI's live log panel can render them. Multiple subscribers can attach
without affecting each other or the bridge's primary path.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request, WebSocket
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from app.api.extension_auth import (
    ExtensionPrincipal,
    validate_extension_principal,
    validate_extension_principal_ws,
)
from app.api.extension_boot_check import (
    get_cached_summary as get_cached_boot_check_summary,
    run_extension_boot_check,
)
from app.api.extension_broadcast import (
    is_broadcast_enabled,
    publish_to_extension,
)
from app.api.extension_invoke import invoke_extension_tool
from app.api.extension_metrics import (
    get_snapshot as get_metrics_snapshot,
    record as record_metric,
    reset_metrics as reset_metrics_registry,
)
from app.api.tunnel_state import get_tunnel_snapshot
from app.api.extension_ws_manager import (
    disconnect_session,
    list_active_sessions,
)
from app.common.system_logger import get_logger

logger = get_logger()
router = APIRouter(prefix="/extension", tags=["extension-bridge-test"])


# ---------------------------------------------------------------------------
# Live event log fan-out — process-singleton pub/sub for the Bridge Test
# UI's "Live event log" panel. Every bridge primitive (rpc receive, ws
# session open/close, invoke send/recv, broadcast publish) calls
# `publish_event(...)` which pushes a line to every connected subscriber.
# ---------------------------------------------------------------------------


_EVENT_SUBSCRIBERS: List["asyncio.Queue[Dict[str, Any]]"] = []


def _now_ms() -> int:
    return int(time.time() * 1000)


def publish_event(
    kind: str,
    direction: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Fan out a bridge event to every Bridge Test subscriber.

    Non-blocking: if a subscriber's queue is full (1024 items deep), the
    oldest entry is dropped to make room. Subscribers run independently
    of every other code path — failing to enqueue here never breaks the
    bridge.

    Args:
        kind: short event tag, e.g. "rpc", "ws.open", "ws.close",
            "invoke.send", "invoke.result", "broadcast.publish".
        direction: "in" (frontend/extension → engine) or "out" (engine
            → frontend/extension), or "internal" for engine-only events.
        payload: arbitrary JSON-serializable detail dict.
    """
    if not _EVENT_SUBSCRIBERS:
        return
    event = {
        "timestamp": _now_ms(),
        "kind": kind,
        "direction": direction,
        "payload": payload or {},
    }
    for queue in list(_EVENT_SUBSCRIBERS):
        try:
            if queue.full():
                # Drop oldest to make room — keeps the live log fresh
                # even when a slow client falls behind.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        except Exception:
            # Subscriber may have been removed mid-iteration; ignore.
            pass


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class InvokeRequest(BaseModel):
    session_id: str
    tool_name: str
    args: Dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = 30.0


class InvokeResponse(BaseModel):
    ok: bool
    envelope: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_type: Optional[str] = None


class DisconnectRequest(BaseModel):
    session_id: str
    reason: Optional[str] = None


class BroadcastTestRequest(BaseModel):
    user_id: str
    type: str = "bridge.test"
    payload: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session introspection / management
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def get_sessions(
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, Any]:
    """Return a snapshot of every active extension WebSocket session."""
    t0 = time.perf_counter()
    try:
        sessions = list_active_sessions()
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric("bridge:sessions", latency_ms, ok=True)
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            "bridge:sessions",
            latency_ms,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    publish_event(
        "sessions.list",
        "internal",
        {"count": len(sessions)},
    )
    return {"sessions": sessions, "count": len(sessions)}


@router.post("/sessions/disconnect")
async def post_disconnect(
    req: DisconnectRequest,
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, Any]:
    """Close an extension WS session by id. Idempotent."""
    t0 = time.perf_counter()
    try:
        found = await disconnect_session(
            req.session_id,
            reason=req.reason or "Closed by desktop UI",
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric("bridge:sessions/disconnect", latency_ms, ok=True)
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            "bridge:sessions/disconnect",
            latency_ms,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    publish_event(
        "ws.close",
        "internal",
        {"session_id": req.session_id, "found": found, "by": "desktop-ui"},
    )
    return {"ok": True, "found": found}


# ---------------------------------------------------------------------------
# Engine → browser tool dispatch
# ---------------------------------------------------------------------------


@router.post("/invoke", response_model=InvokeResponse)
async def post_invoke(
    req: InvokeRequest,
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> InvokeResponse:
    """Dispatch a tool call to a connected extension session.

    Thin HTTP wrapper around `invoke_extension_tool`. Returns the FULL
    `extension.result` envelope under `envelope` so callers can branch
    on `envelope.ok` without losing the error / errorType fields.

    Validation: `timeout_seconds` is clamped to [1, 120] — anything
    higher would let a runaway browser hang an HTTP worker indefinitely.
    """
    timeout = max(1.0, min(120.0, float(req.timeout_seconds)))

    publish_event(
        "invoke.send",
        "out",
        {
            "session_id": req.session_id,
            "tool_name": req.tool_name,
            "args_keys": list(req.args.keys()),
            "timeout_seconds": timeout,
        },
    )

    t0 = time.perf_counter()
    try:
        envelope = await invoke_extension_tool(
            req.tool_name,
            req.args,
            req.session_id,
            timeout_seconds=timeout,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            "bridge:invoke",
            latency_ms,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        publish_event(
            "invoke.error",
            "in",
            {
                "session_id": req.session_id,
                "tool_name": req.tool_name,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        return InvokeResponse(
            ok=False,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    latency_ms = (time.perf_counter() - t0) * 1000.0
    envelope_ok = (
        envelope.get("ok") if isinstance(envelope, dict) else None
    )
    if envelope_ok is False:
        # The HTTP call succeeded but the extension reported a tool-level
        # error. Count that as a failed observation so the metrics page
        # can highlight slow / unreliable browser tools.
        envelope_error = (
            envelope.get("error") if isinstance(envelope, dict) else None
        )
        await record_metric(
            "bridge:invoke",
            latency_ms,
            ok=False,
            error=str(envelope_error) if envelope_error else "envelope.ok=false",
        )
    else:
        await record_metric("bridge:invoke", latency_ms, ok=True)

    publish_event(
        "invoke.result",
        "in",
        {
            "session_id": req.session_id,
            "tool_name": req.tool_name,
            "envelope_ok": envelope_ok,
        },
    )
    return InvokeResponse(ok=True, envelope=envelope)


# ---------------------------------------------------------------------------
# Broadcast plumb test
# ---------------------------------------------------------------------------


@router.get("/broadcast/status")
async def get_broadcast_status(
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, Any]:
    """Report the current state of the Supabase Broadcast plumb.

    Always 200 — the panel renders different copy based on whether the
    feature flag is on, never depends on a non-200 to detect the off
    state. Channel name is constructed from a placeholder when no user
    id is supplied, so the UI can show the template.
    """
    t0 = time.perf_counter()
    try:
        enabled = is_broadcast_enabled()
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric("bridge:broadcast/status", latency_ms, ok=True)
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            "bridge:broadcast/status",
            latency_ms,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    return {
        "enabled": enabled,
        "channel_template": "matrx-local-bridge:<user_id>",
        "setting_key": "extension_broadcast_enabled",
    }


@router.post("/broadcast/test")
async def post_broadcast_test(
    req: BroadcastTestRequest,
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, Any]:
    """Publish a no-op test envelope on the user's bridge channel.

    Returns `{ok, sent, enabled}` so the Bridge Test UI can distinguish
    "broadcast plumb is off" from "broadcast plumb is on but the user
    is not connected" from "publish actually fired".
    """
    t0 = time.perf_counter()
    try:
        enabled = is_broadcast_enabled()
        sent = await publish_to_extension(
            req.user_id,
            type=req.type,
            payload=req.payload,
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric("bridge:broadcast/test", latency_ms, ok=True)
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            "bridge:broadcast/test",
            latency_ms,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    publish_event(
        "broadcast.publish",
        "out",
        {
            "user_id": req.user_id,
            "type": req.type,
            "sent": sent,
            "enabled": enabled,
        },
    )
    return {
        "ok": True,
        "sent": sent,
        "enabled": enabled,
    }


# ---------------------------------------------------------------------------
# Tunnel introspection — runtime state of the Cloudflare tunnel.
#
# The discovery file at ``~/.matrx/local.json`` is the *bootstrap* source
# of truth for clients that haven't authenticated yet (they need to know
# the engine port + tunnel URL before they can send a Bearer token). The
# endpoint below is the authenticated runtime equivalent: same data, but
# behind ``validate_extension_principal`` so a tunnel-reached caller still
# proves identity, and includes a ``preferred`` hint telling the
# extension which URL it *should* be using right now.
# ---------------------------------------------------------------------------


@router.get("/tunnel/status")
async def get_tunnel_status_endpoint(
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, Any]:
    """Report tunnel + local URL state with a preferred-mode hint.

    Shape::

        {
          "active": bool,                  # tunnel up?
          "tunnel_url": str | None,        # https://...trycloudflare.com
          "tunnel_ws":  str | None,        # wss equivalent
          "local_url":  str,               # http://127.0.0.1:<port>
          "local_ws":   str,               # ws://127.0.0.1:<port>/ws
          "preferred":  "local" | "tunnel",
          "prefer_tunnel": bool,           # mirror of MATRX_PREFER_TUNNEL
          "mode":       "quick" | "named",
          "uptime_seconds": float,
        }

    The ``preferred`` field is the recommendation the extension should
    follow when it has a choice between local and tunnel: ``"tunnel"``
    iff the tunnel is up *and* the engine was started with
    ``MATRX_PREFER_TUNNEL=true``, else ``"local"``. Most users keep the
    flag off — local loopback is faster and free; the flag is for the
    case where the extension lives on a different device than the engine.
    """
    t0 = time.perf_counter()
    try:
        snapshot = get_tunnel_snapshot()
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric("bridge:tunnel/status", latency_ms, ok=True)
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            "bridge:tunnel/status",
            latency_ms,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    publish_event(
        "tunnel.status",
        "internal",
        {
            "active": snapshot["active"],
            "preferred": snapshot["preferred"],
            "mode": snapshot["mode"],
        },
    )
    return snapshot


# ---------------------------------------------------------------------------
# Observability — in-memory request/error/latency stats per command.
#
# Reads/writes are gated on the same `validate_extension_principal`
# Bearer-JWT path that protects the rest of `/extension/*`. The data is
# in-memory only and resets on engine restart by design — see
# `app/api/extension_metrics.py` for shape and bounds.
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def get_metrics(
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, Dict[str, Any]]:
    """Return the per-command snapshot used by the Bridge Test panel.

    Shape:

        {
          "<command>": {
            "count": int,
            "error_count": int,
            "last_n_latencies_ms": [float, ...],
            "last_called_at": int,    // unix ms; 0 if never
            "last_error": str | null
          },
          ...
        }

    The `_overflow` row is a synthetic entry that only appears when the
    distinct-command cap has been hit; the UI can use it to warn the user.
    """
    return await get_metrics_snapshot()


@router.post("/metrics/reset")
async def post_reset_metrics(
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, bool]:
    """Drop every recorded stat. Idempotent."""
    await reset_metrics_registry()
    publish_event("metrics.reset", "internal", {})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Boot self-check — last-known + on-demand re-run.
#
# The summary is produced once at engine startup (in the lifespan hook in
# ``app/main.py``) and cached in ``extension_boot_check``. Reads of the
# cache are sub-millisecond; the re-run endpoint exists so a desktop user
# can refresh the picture without restarting the engine.
# ---------------------------------------------------------------------------


@router.get("/boot-check")
async def get_boot_check(
    request: Request,
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, Any]:
    """Return the cached boot-time self-check summary.

    Reflects the LAST self-check run — populated at engine startup, and
    refreshed every time ``POST /extension/boot-check/run`` fires. Cheap
    (no engine work per request).

    Shape::

        {
          "ok": bool,
          "checks": [
            {"name": str, "status": "ok"|"warn"|"fail", "message": str, "duration_ms": float},
            ...
          ],
          "started_at": float,    # unix seconds
          "finished_at": float,   # unix seconds
          "duration_ms": float
        }

    Returns ``{"ok": false, "checks": []}`` with an explanatory message
    when the engine has not yet completed a self-check (e.g. extremely
    early in boot). Callers can re-poll or trigger ``run`` to populate.
    """
    cached = get_cached_boot_check_summary()
    if cached is None:
        return {
            "ok": False,
            "checks": [],
            "started_at": 0.0,
            "finished_at": 0.0,
            "duration_ms": 0.0,
            "message": "boot self-check has not yet run",
        }
    return cached


@router.post("/boot-check/run")
async def post_boot_check_run(
    request: Request,
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> Dict[str, Any]:
    """Re-run the boot self-check live and return the fresh summary.

    Replaces the cached summary that ``GET /extension/boot-check`` reads.
    Same shape as ``GET``. The check itself is fast (<1s) and side-effect
    light — the only intentional mutation is resetting the metrics ring.

    The desktop ``Bridge Test`` panel calls this when the user clicks
    "Re-run self-check" so the UI reflects current posture (e.g. after
    starting/stopping a tunnel or flipping ``MATRX_PREFER_TUNNEL``).
    """
    t0 = time.perf_counter()
    try:
        summary = await run_extension_boot_check(request.app)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric("bridge:boot-check/run", latency_ms, ok=True)
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            "bridge:boot-check/run",
            latency_ms,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    publish_event(
        "boot-check.run",
        "internal",
        {"ok": summary.ok, "check_count": len(summary.checks)},
    )
    # Re-read via the cache helper so the wire shape stays in lockstep.
    cached = get_cached_boot_check_summary()
    return cached if cached is not None else {"ok": summary.ok, "checks": []}


# ---------------------------------------------------------------------------
# Live event log WS — additive fan-out for the Bridge Test panel
# ---------------------------------------------------------------------------


async def _wait_for_bridge_events_disconnect(websocket: WebSocket) -> None:
    """Observe a peer disconnect while authentication is still in flight.

    A WebSocket route can spend long enough verifying a JWT for Uvicorn to
    begin shutdown (or for the browser to close the panel) before the route
    reaches ``accept()``.  Consuming the initial ``websocket.connect`` event
    and then watching for ``websocket.disconnect`` prevents a late accept from
    being sent to a transport that already entered its close-only state.
    """
    while True:
        try:
            message = await websocket.receive()
        except WebSocketDisconnect:
            return
        if message.get("type") == "websocket.disconnect":
            return


async def _validate_bridge_events_connection(
    websocket: WebSocket,
) -> ExtensionPrincipal | None:
    """Authenticate only while the peer remains connected."""
    auth_task = asyncio.create_task(validate_extension_principal_ws(websocket))
    disconnect_task = asyncio.create_task(
        _wait_for_bridge_events_disconnect(websocket)
    )
    try:
        done, _pending = await asyncio.wait(
            {auth_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Disconnect wins a same-tick race.  Accepting after Uvicorn has moved
        # the transport into close-only state produces an unhandled ASGI
        # RuntimeError and a packaged-shutdown traceback.
        if disconnect_task in done:
            auth_task.cancel()
            await asyncio.gather(auth_task, return_exceptions=True)
            return None
        return auth_task.result()
    finally:
        if not auth_task.done():
            auth_task.cancel()
        disconnect_task.cancel()
        await asyncio.gather(
            auth_task,
            disconnect_task,
            return_exceptions=True,
        )


@router.websocket("/bridge-events")
async def bridge_events_websocket(websocket: WebSocket) -> None:
    """Stream every bridge primitive event to subscribers.

    Auth: full Supabase JWT signature + expiry verification via
    `validate_extension_principal_ws`. Browsers can't set headers on a
    WS upgrade so the token comes via `?token=`. Engine in degraded
    mode (no JWT secret + no SUPABASE_URL) falls back to
    Bearer-presence; same posture as `/extension/ws`. Every event
    matches the shape produced by `publish_event(...)`.

    The subscriber's queue caps at 1024 entries; on overflow the oldest
    entry is dropped (see `publish_event`). This is a diagnostic
    channel — losing events is preferable to back-pressuring the bridge.
    """
    try:
        principal = await _validate_bridge_events_connection(websocket)
    except asyncio.CancelledError:
        # Cancellation during JWT verification is a normal bounded-shutdown
        # path.  The helper has already drained both child tasks.
        return
    if principal is None:
        # Already closed by the validator with code 1008, or disconnected
        # while authentication was still in flight.
        return

    try:
        await websocket.accept()
    except (RuntimeError, WebSocketDisconnect) as exc:
        # A final transport-close race can occur between cancelling the
        # disconnect watcher and sending the ASGI accept message.  The client
        # is already gone; containing the protocol error is the only useful
        # outcome and prevents Uvicorn from emitting a shutdown traceback.
        logger.debug(
            "[extension_bridge_routes] bridge-events disconnected before accept: %s",
            exc,
        )
        return
    queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=1024)
    _EVENT_SUBSCRIBERS.append(queue)

    publish_event(
        "bridge-events.subscribe",
        "internal",
        {"subscribers": len(_EVENT_SUBSCRIBERS)},
    )

    receive_task: asyncio.Task[dict[str, Any]] | None = None
    event_task: asyncio.Task[Dict[str, Any]] | None = None
    try:
        # Fire a hello so the client can confirm subscription.
        await websocket.send_text(
            json.dumps(
                {
                    "timestamp": _now_ms(),
                    "kind": "bridge-events.hello",
                    "direction": "internal",
                    "payload": {"subscribers": len(_EVENT_SUBSCRIBERS)},
                }
            )
        )

        receive_task = asyncio.create_task(websocket.receive())
        while True:
            event_task = asyncio.create_task(queue.get())
            done, _pending = await asyncio.wait(
                {event_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
                event_task = None
                message = receive_task.result()
                if message.get("type") == "websocket.disconnect":
                    break
                # This diagnostic channel is server-to-client only. Ignore any
                # client frame, then resume listening for the real disconnect.
                receive_task = asyncio.create_task(websocket.receive())
                continue
            event = event_task.result()
            event_task = None
            await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        # Normal — page navigation or panel close.
        pass
    except asyncio.CancelledError:
        # Uvicorn may cancel an in-flight socket at the end of its bounded
        # connection drain. Cancellation is normal shutdown, not an ASGI crash.
        pass
    except Exception as exc:
        logger.warning("[extension_bridge_routes] bridge-events socket crashed: %s", exc)
    finally:
        if event_task is not None:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
        if receive_task is not None:
            receive_task.cancel()
            await asyncio.gather(receive_task, return_exceptions=True)
        try:
            _EVENT_SUBSCRIBERS.remove(queue)
        except ValueError:
            pass
        publish_event(
            "bridge-events.unsubscribe",
            "internal",
            {"subscribers": len(_EVENT_SUBSCRIBERS)},
        )
