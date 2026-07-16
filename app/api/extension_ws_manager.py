"""Per-connection state for the matrx-extend reverse-push WebSocket channel.

Phase 2 (master plan section C2.b) introduces a dedicated WebSocket route
(`/extension/ws`) used exclusively by the matrx-extend Chrome extension's
offscreen-document client. This is a SEPARATE channel from the existing
`/ws` endpoint (which serves the engine's primary tool-dispatch UI),
deliberately isolated so:

  * Extension lifecycle (offscreen connect / disconnect) does not perturb
    the primary UI session manager (`app.websocket_manager`).
  * Wire format diverges — `/ws` speaks the in-process tool dispatcher's
    language, `/extension/ws` speaks the engine→browser invocation
    envelope contract documented in `docs/MATRX_EXTEND_CONNECTION.md`.

This module owns:

  * `ExtensionSession` — one record per connected extension WebSocket.
  * `ExtensionSessionRegistry` — process-singleton mapping
    `session_id -> ExtensionSession`, plus the per-callId asyncio.Future
    table used by `invoke_extension_tool` to await results.

Public helpers (the engine-side primitives `extension_invoke.py` calls):

  * `register_session(websocket) -> session_id`
  * `unregister_session(session_id) -> None`
  * `send_to_extension_session(session_id, payload) -> bool`
  * `create_pending_future(call_id, timeout_seconds) -> asyncio.Future`
  * `cancel_pending_future(call_id) -> None`
  * `resolve_pending_future(call_id, payload) -> bool`

Loopback-only: the `/extension/ws` route binds to 127.0.0.1 like every
other engine endpoint. Production-grade JWT validation is handled by the
upstream proxy / scraper server — the engine itself only enforces a
Bearer-token presence check (parity with `/extension/rpc`).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from app.common.system_logger import get_logger

logger = get_logger()


@dataclass
class ExtensionSession:
    """One connected extension WebSocket and its bookkeeping.

    `pending_calls` maps the engine-issued `callId` -> the asyncio.Future
    that `invoke_extension_tool` is awaiting. Results arrive as
    `extension.result` envelopes via `_handle_message` and resolve the
    matching Future.
    """

    session_id: str
    websocket: WebSocket
    user_token: Optional[str] = None
    pending_calls: Dict[str, asyncio.Future] = field(default_factory=dict)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Wall-clock timestamps (seconds-since-epoch) used by the desktop UI's
    # Bridge Test panel to render "connected at" + "last ping" columns. Pure
    # bookkeeping — no behaviour depends on these values.
    connected_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    # Supplied by the extension immediately after the engine's ``hello``.
    # The engine cannot enumerate Chrome profiles or dormant installs, so
    # these fields identify only an actually live reverse-channel client.
    extension_id: Optional[str] = None
    extension_version: Optional[str] = None
    extension_name: Optional[str] = None
    identified_at: Optional[float] = None

    async def send(self, payload: Dict[str, Any]) -> bool:
        """Serialize + send a JSON payload. Returns True on success.

        Concurrency: a single asyncio.Lock per session keeps interleaved
        sends from corrupting the WebSocket frame stream. Callers may
        invoke `send` from any task without external coordination.
        """
        async with self._send_lock:
            try:
                await self.websocket.send_text(json.dumps(payload))
                return True
            except Exception as exc:
                logger.warning(
                    "[extension_ws] send failed session=%s type=%s err=%s",
                    self.session_id,
                    payload.get("type"),
                    exc,
                )
                return False

    def cancel_pending(self, reason: str) -> int:
        """Cancel every pending Future on disconnect. Returns count.

        Each cancelled Future will surface as a ConnectionError to the
        awaiting `invoke_extension_tool` caller, preventing the caller
        from hanging forever after the extension disconnects mid-call.
        """
        count = 0
        for call_id, fut in list(self.pending_calls.items()):
            if not fut.done():
                fut.set_exception(ConnectionError(reason))
                count += 1
            self.pending_calls.pop(call_id, None)
        return count


class ExtensionSessionRegistry:
    """Process-singleton tracking every active extension WebSocket.

    Phase 2 ships a single-process registry (sufficient for the
    desktop-engine deployment model). Cross-process correlation via a
    per-user `session_id` lookup is on the C-bridge orchestrator's
    roadmap and out of scope here.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, ExtensionSession] = {}
        # callId -> session_id reverse index, so a result envelope routes
        # back to the right session even when the caller doesn't carry
        # session context (e.g. broadcast result fan-in).
        self._call_to_session: Dict[str, str] = {}

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    @property
    def session_ids(self) -> list[str]:
        return list(self._sessions.keys())

    def register(
        self,
        websocket: WebSocket,
        user_token: Optional[str] = None,
    ) -> ExtensionSession:
        """Allocate a new session_id and bind it to this socket."""
        session_id = str(uuid.uuid4())
        session = ExtensionSession(
            session_id=session_id,
            websocket=websocket,
            user_token=user_token,
        )
        self._sessions[session_id] = session
        logger.info(
            "[extension_ws] session registered id=%s active=%d",
            session_id,
            len(self._sessions),
        )
        return session

    def unregister(self, session_id: str) -> Optional[ExtensionSession]:
        """Remove and cancel a session's pending calls. Idempotent."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return None
        # Drop reverse index entries pointing at this session.
        for call_id, sid in list(self._call_to_session.items()):
            if sid == session_id:
                self._call_to_session.pop(call_id, None)
        cancelled = session.cancel_pending(
            f"extension session {session_id} disconnected"
        )
        logger.info(
            "[extension_ws] session unregistered id=%s cancelled_calls=%d active=%d",
            session_id,
            cancelled,
            len(self._sessions),
        )
        return session

    def get(self, session_id: str) -> Optional[ExtensionSession]:
        return self._sessions.get(session_id)

    def bind_call(self, call_id: str, session_id: str) -> None:
        self._call_to_session[call_id] = session_id

    def session_for_call(self, call_id: str) -> Optional[ExtensionSession]:
        sid = self._call_to_session.get(call_id)
        if sid is None:
            return None
        return self._sessions.get(sid)

    def drop_call(self, call_id: str) -> None:
        self._call_to_session.pop(call_id, None)


# Process-singleton — module-level so every importer shares the same registry.
_REGISTRY = ExtensionSessionRegistry()


def get_registry() -> ExtensionSessionRegistry:
    return _REGISTRY


# ---------------------------------------------------------------------------
# Public helpers consumed by `app/api/extension_invoke.py` and the WS route.
# ---------------------------------------------------------------------------


def register_session(
    websocket: WebSocket,
    user_token: Optional[str] = None,
) -> ExtensionSession:
    return _REGISTRY.register(websocket, user_token=user_token)


def unregister_session(session_id: str) -> None:
    _REGISTRY.unregister(session_id)


async def send_to_extension_session(
    session_id: str,
    payload: Dict[str, Any],
) -> bool:
    """Push a payload to the given extension session.

    Returns False (without raising) when the session is not registered or
    the underlying socket send failed, so callers can distinguish a
    routing miss from a tool-layer failure.
    """
    session = _REGISTRY.get(session_id)
    if session is None:
        logger.warning(
            "[extension_ws] no active session for send: id=%s type=%s",
            session_id,
            payload.get("type"),
        )
        return False
    return await session.send(payload)


def create_pending_future(
    call_id: str,
    timeout_seconds: float,  # noqa: ARG001 — accepted for API symmetry; enforced by caller's wait_for
    *,
    session_id: Optional[str] = None,
) -> asyncio.Future:
    """Allocate a Future the engine will await for `extension.result`.

    The actual timeout is enforced by `asyncio.wait_for` at the call
    site — keeping that here would double-fire and obscure the exception
    type the caller expects (`asyncio.TimeoutError`).

    `session_id` is optional but recommended: when provided, it lets the
    registry route incoming results back through the reverse index even
    if the result envelope arrives on a different socket from the
    invocation (which shouldn't happen in Phase 2 but is cheap to make
    correct now).
    """
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    if session_id is not None:
        session = _REGISTRY.get(session_id)
        if session is not None:
            session.pending_calls[call_id] = fut
            _REGISTRY.bind_call(call_id, session_id)
    return fut


def cancel_pending_future(call_id: str) -> None:
    """Drop the Future for a callId. Idempotent.

    Called on timeout (so a late-arriving result is silently dropped
    rather than resolving a Future no one is awaiting) and on send
    failure (so the registry doesn't leak entries).
    """
    session = _REGISTRY.session_for_call(call_id)
    if session is not None:
        fut = session.pending_calls.pop(call_id, None)
        if fut is not None and not fut.done():
            fut.cancel()
    _REGISTRY.drop_call(call_id)


def resolve_pending_future(call_id: str, payload: Dict[str, Any]) -> bool:
    """Set the result of the Future for `call_id`. Returns True on hit."""
    session = _REGISTRY.session_for_call(call_id)
    if session is None:
        return False
    fut = session.pending_calls.pop(call_id, None)
    _REGISTRY.drop_call(call_id)
    if fut is None or fut.done():
        return False
    fut.set_result(payload)
    return True


# ---------------------------------------------------------------------------
# Introspection / management helpers — used by the desktop frontend's
# Bridge Test page (`POST /extension/sessions`, etc.) to render and act on
# the live registry. The registry methods themselves stay private; these
# helpers form the supported surface.
# ---------------------------------------------------------------------------


def list_active_sessions() -> List[Dict[str, Any]]:
    """Return a JSON-serializable snapshot of every active session.

    Each entry: `{session_id, connected_at, last_seen_at, pending_calls}`.
    Timestamps are seconds-since-epoch so the frontend can render them in
    whatever timezone / format it wants.
    """
    snapshot: List[Dict[str, Any]] = []
    for sid, session in _REGISTRY._sessions.items():  # noqa: SLF001 — module-internal
        snapshot.append(
            {
                "session_id": sid,
                "connected_at": session.connected_at,
                "last_seen_at": session.last_seen_at,
                "pending_calls": len(session.pending_calls),
                "extension_id": session.extension_id,
                "extension_version": session.extension_version,
                "extension_name": session.extension_name,
                "identified_at": session.identified_at,
            }
        )
    return snapshot


def touch_session(session_id: str) -> None:
    """Record a heartbeat on the named session. No-op when missing."""
    session = _REGISTRY.get(session_id)
    if session is not None:
        session.last_seen_at = time.time()


def identify_session(
    session_id: str,
    *,
    extension_id: str,
    extension_version: str,
    extension_name: str,
) -> bool:
    """Attach the extension's self-reported runtime identity to a live session."""
    session = _REGISTRY.get(session_id)
    if session is None:
        return False
    session.extension_id = extension_id
    session.extension_version = extension_version
    session.extension_name = extension_name
    session.identified_at = time.time()
    session.last_seen_at = session.identified_at
    return True


async def disconnect_session(
    session_id: str,
    *,
    code: int = 1000,
    reason: str = "Closed by desktop UI",
) -> bool:
    """Close the named session's underlying WebSocket. Idempotent.

    The session WS-route handler's `finally` clause invokes
    `unregister_session`, so we don't need to do that here — closing the
    socket triggers the same cleanup path as a client-initiated disconnect.

    Returns True when a matching session was found and a close was
    attempted. False when the session_id is unknown.
    """
    session = _REGISTRY.get(session_id)
    if session is None:
        return False
    try:
        await session.websocket.close(code=code, reason=reason)
    except Exception as exc:
        # The socket may already be half-closed — log and fall through.
        logger.warning(
            "[extension_ws] disconnect_session: close failed session=%s err=%s",
            session_id,
            exc,
        )
    return True
