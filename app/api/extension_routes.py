"""HTTP + WebSocket routes for the matrx-extend Chrome extension.

  * `POST /extension/rpc` — synchronous request/response RPC. Used for
    health, version, capabilities, single tool dispatch.
  * `WS   /extension/ws`  — persistent reverse-push channel. Engine
    sends `extension.invoke` envelopes; extension responds with
    `extension.result`. Heartbeats (`ping`/`pong`) maintain liveness
    and carry the engine's tool-catalog hash so the extension can
    detect catalog drift.

The WS route is dedicated to the extension and SEPARATE from the
existing `/ws` endpoint (which serves the engine's primary tool UI).
This keeps extension session state isolated from the primary UI session
manager — each side can evolve without breaking the other.

Auth: same Bearer-token gate as `/extension/rpc`. Browsers can't set
custom headers on a WS upgrade, so the token comes via `?token=` query
param (consistent with the existing `/ws` endpoint and `AuthMiddleware`
fallback). Loopback-only enforcement is upstream.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request, WebSocket
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from app.api.extension_auth import (
    ExtensionPrincipal,
    validate_extension_principal,
    validate_extension_principal_ws,
)
from app.api.extension_bridge_routes import publish_event
from app.api.extension_handlers import HANDLERS
from app.api.extension_metrics import record as record_metric
from app.api.extension_ws_manager import (
    register_session,
    resolve_pending_future,
    unregister_session,
)
from app.api.routes import _APP_VERSION
from app.common.system_logger import get_logger
from app.tools.dispatcher import tool_catalog_hash

logger = get_logger()
router = APIRouter(prefix="/extension", tags=["extension"])


class DesktopRpcRequest(BaseModel):
    command: str
    args: Optional[Dict[str, Any]] = Field(default_factory=dict)


class DesktopRpcResponse(BaseModel):
    ok: bool
    data: Optional[Any] = None
    error: Optional[str] = None


@router.post("/rpc", response_model=DesktopRpcResponse)
async def handle_rpc(
    request: DesktopRpcRequest,
    req: Request,
    principal: ExtensionPrincipal = Depends(validate_extension_principal),
) -> DesktopRpcResponse:
    """
    Handle RPC requests from the matrx-extend Chrome extension.

    Dispatches to a handler registered in `app.api.extension_handlers.HANDLERS`.
    Handlers return a JSON-serializable dict that becomes the `data` field of
    the outer envelope on success. Unhandled exceptions inside a handler are
    caught here and surfaced as `ok=False`, with the exception class name
    carried in `data.error_type` for the extension's UX.

    Auth: the upstream `AuthMiddleware` checks Bearer-token presence; this
    route layers on cryptographic Supabase JWT validation via
    `validate_extension_principal`. Missing / invalid / expired tokens
    short-circuit with HTTP 401 before the handler runs.
    """
    logger.info(
        "[extension_routes] Received RPC command: %s (user=%s verified=%s)",
        request.command,
        principal.user_id or "<unverified>",
        principal.verified,
    )
    publish_event("rpc.in", "in", {"command": request.command})

    # Stamp the entry time so we can record latency on every exit branch
    # below — unknown command, handler exception, or success.
    t0 = time.perf_counter()

    handler = HANDLERS.get(request.command)
    if handler is None:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            request.command,
            latency_ms,
            ok=False,
            error=f"Unknown command: {request.command}",
        )
        publish_event(
            "rpc.out",
            "out",
            {"command": request.command, "ok": False, "error": "Unknown command"},
        )
        return DesktopRpcResponse(
            ok=False,
            error=f"Unknown command: {request.command}",
        )

    try:
        data = await handler(request.args or {}, req)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(request.command, latency_ms, ok=True)
        publish_event(
            "rpc.out",
            "out",
            {"command": request.command, "ok": True},
        )
        return DesktopRpcResponse(ok=True, data=data)
    except Exception as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        await record_metric(
            request.command,
            latency_ms,
            ok=False,
            error=f"{type(e).__name__}: {e}",
        )
        logger.error(
            "[extension_routes] handler %r failed: %s",
            request.command,
            e,
            exc_info=True,
        )
        publish_event(
            "rpc.out",
            "out",
            {
                "command": request.command,
                "ok": False,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        return DesktopRpcResponse(
            ok=False,
            error=str(e),
            data={"error_type": type(e).__name__},
        )


# ---------------------------------------------------------------------------
# /extension/ws — persistent reverse-push channel
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _build_pong(client_timestamp: Any) -> Dict[str, Any]:
    """Construct the pong envelope.

    Includes the engine version and the current tool-catalog hash so the
    extension can detect catalog drift across reconnects without an
    extra `capabilities` round-trip.
    """
    return {
        "type": "pong",
        "timestamp": _now_ms(),
        "client_timestamp": client_timestamp if isinstance(client_timestamp, (int, float)) else None,
        "engine_version": _APP_VERSION,
        "tool_catalog_hash": tool_catalog_hash(),
    }


@router.websocket("/ws")
async def extension_websocket(websocket: WebSocket) -> None:
    """Engine → extension reverse-push channel.

    Lifecycle:

      1. Validate the Bearer token from `?token=` (browsers cannot set
         Authorization headers on a WS upgrade). The token's Supabase
         JWT signature + expiry are verified inline before `accept()` —
         missing / invalid / expired tokens close with 1008. When the
         engine is running without a JWT secret AND without
         `SUPABASE_URL`, validation gracefully degrades to a Bearer-
         presence check (loud one-time WARNING) so the loopback-only
         happy path keeps working.
      2. Accept upgrade. Register a session in
         `extension_ws_manager`; the registry mints a UUID `session_id`.
      3. Send a `hello` envelope so the client can confirm connection
         and learn its session_id (useful for diagnostics / multi-tab
         scenarios where one user has multiple offscreen documents).
      4. Read messages forever. Dispatch by `type`:
            - "extension.result" => resolve the matching callId Future
            - "ping"              => respond with `pong`
            - other               => log warning, do not disconnect
      5. On any disconnect (clean or abrupt), unregister the session —
         the registry cancels every pending Future for that session
         with a ConnectionError so awaiting `invoke_extension_tool`
         callers don't hang forever.
    """
    principal = await validate_extension_principal_ws(websocket)
    if principal is None:
        # validate_extension_principal_ws already closed the socket
        # with code 1008 on missing / invalid / expired token.
        return
    token = principal.raw_token

    await websocket.accept()
    session = register_session(websocket, user_token=token)
    # Track connection start so disconnect can record total duration as a
    # "latency" — gives the metrics page a useful "how long are sessions
    # staying alive?" signal.
    connection_started = time.perf_counter()
    await record_metric("ws:connect", 0.0, ok=True)
    publish_event(
        "ws.open",
        "in",
        {"session_id": session.session_id},
    )

    # Send the hello envelope. Failure here means the socket died during
    # accept — bail out without entering the read loop.
    hello_sent = await session.send(
        {
            "type": "hello",
            "session_id": session.session_id,
            "engine_version": _APP_VERSION,
            "tool_catalog_hash": tool_catalog_hash(),
            "timestamp": _now_ms(),
        }
    )
    if not hello_sent:
        unregister_session(session.session_id)
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "[extension_ws] invalid JSON session=%s err=%s",
                    session.session_id,
                    exc,
                )
                await record_metric(
                    "ws:message",
                    0.0,
                    ok=False,
                    error=f"invalid JSON: {exc}",
                )
                continue

            msg_t0 = time.perf_counter()
            try:
                await _handle_extension_message(session.session_id, msg)
                msg_latency_ms = (time.perf_counter() - msg_t0) * 1000.0
                await record_metric("ws:message", msg_latency_ms, ok=True)
            except Exception as msg_exc:
                msg_latency_ms = (time.perf_counter() - msg_t0) * 1000.0
                await record_metric(
                    "ws:message",
                    msg_latency_ms,
                    ok=False,
                    error=f"{type(msg_exc).__name__}: {msg_exc}",
                )
                raise
    except WebSocketDisconnect as exc:
        # 1001 (Going Away) and 1012 (Service Restart) are expected
        # close codes during normal extension lifecycle (page unload,
        # engine restart). Log them quietly.
        if exc.code in (1001, 1012):
            logger.debug(
                "[extension_ws] session=%s closed normally code=%s",
                session.session_id,
                exc.code,
            )
        else:
            logger.info(
                "[extension_ws] session=%s disconnected code=%s reason=%s",
                session.session_id,
                exc.code,
                exc.reason,
            )
    except Exception as exc:
        logger.error(
            "[extension_ws] session=%s read loop crashed: %s",
            session.session_id,
            exc,
            exc_info=True,
        )
    finally:
        connection_duration_ms = (
            (time.perf_counter() - connection_started) * 1000.0
        )
        await record_metric(
            "ws:disconnect",
            connection_duration_ms,
            ok=True,
        )
        unregister_session(session.session_id)
        publish_event(
            "ws.close",
            "in",
            {"session_id": session.session_id, "by": "client-or-error"},
        )


async def _handle_extension_message(session_id: str, msg: Dict[str, Any]) -> None:
    """Dispatch a single inbound envelope by `type`."""
    msg_type = msg.get("type")

    if msg_type == "extension.result":
        call_id = msg.get("callId")
        if not isinstance(call_id, str):
            logger.warning(
                "[extension_ws] extension.result missing callId session=%s",
                session_id,
            )
            return
        resolved = resolve_pending_future(call_id, msg)
        publish_event(
            "ws.extension.result",
            "in",
            {
                "session_id": session_id,
                "call_id": call_id,
                "ok": msg.get("ok"),
                "resolved": resolved,
            },
        )
        if not resolved:
            logger.warning(
                "[extension_ws] extension.result for unknown/late callId=%s session=%s",
                call_id,
                session_id,
            )
        return

    if msg_type == "ping":
        # Heartbeat — respond inline. The send goes through the same
        # session lock as engine-initiated invocations, so frame
        # interleaving is impossible.
        from app.api.extension_ws_manager import get_registry, touch_session

        session = get_registry().get(session_id)
        if session is None:
            return
        touch_session(session_id)
        publish_event("ws.ping", "in", {"session_id": session_id})
        await session.send(_build_pong(msg.get("timestamp")))
        return

    if msg_type == "extension.identify":
        from app.api.extension_ws_manager import identify_session

        extension_id = msg.get("extension_id")
        extension_version = msg.get("extension_version")
        extension_name = msg.get("extension_name")
        if not all(
            isinstance(value, str) and 0 < len(value) <= 256
            for value in (extension_id, extension_version, extension_name)
        ):
            logger.warning(
                "[extension_ws] invalid extension.identify session=%s", session_id
            )
            return
        identified = identify_session(
            session_id,
            extension_id=extension_id,
            extension_version=extension_version,
            extension_name=extension_name,
        )
        publish_event(
            "ws.identify",
            "in",
            {
                "session_id": session_id,
                "extension_id": extension_id,
                "extension_version": extension_version,
                "identified": identified,
            },
        )
        return

    logger.warning(
        "[extension_ws] unknown message type=%r session=%s",
        msg_type,
        session_id,
    )
