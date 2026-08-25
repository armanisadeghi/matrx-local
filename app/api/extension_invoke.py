"""Engine → matrx-extend reverse-push tool invocation.

Phase 2 (master plan section C2.b) wires the engine-side primitive that
sends an `extension.invoke` envelope to the matrx-extend Chrome extension
over its dedicated `/extension/ws` WebSocket and awaits the matching
`extension.result` envelope.

Wire format (CONTRACTUAL — must match `/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md`):

  Engine → browser:
    { "type": "extension.invoke", "callId": str,
      "toolName": str, "args": dict }

  Browser → engine:
    { "type": "extension.result", "callId": str, "ok": True,
      "result": Any }
    { "type": "extension.result", "callId": str, "ok": False,
      "error": str, "errorType"?: str }

Per-connection state (the callId-keyed Future cache, the session
registry) lives in `app.api.extension_ws_manager` so this module stays a
thin orchestration layer around `asyncio.wait_for`.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict

from app.api.extension_ws_manager import (
    cancel_pending_future,
    create_pending_future,
    send_to_extension_session,
)
from app.common.system_logger import get_logger

logger = get_logger()


async def invoke_extension_tool(
    tool_name: str,
    args: Dict[str, Any],
    session_id: str,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Invoke a browser tool registered by the matrx-extend Chrome extension.

    The extension exposes ~118 browser-side tools (DOM read/write, tab
    control, on-device AI, CDP, etc.) via a WebSocket reverse-push
    channel. This function is the engine-side entry point: it sends an
    `extension.invoke` envelope to the connected extension session and
    awaits the matching `extension.result` envelope.

    Implementation:

      1. Generate a callId (UUID4 string).
      2. Register an asyncio.Future in the callId-keyed cache via
         `create_pending_future(call_id, …, session_id=session_id)` —
         the registry binds the Future to the session so an incoming
         result envelope routes back correctly.
      3. Send `{"type": "extension.invoke", "callId": call_id,
         "toolName": tool_name, "args": args}` via
         `send_to_extension_session(session_id, payload)`.
      4. `await asyncio.wait_for(future, timeout=timeout_seconds)`.
      5. Return the FULL `extension.result` envelope (callers branch on
         the `ok` field — they want to see `error` / `errorType` too,
         not a flattened result).

    On timeout the cache entry is evicted via `cancel_pending_future`
    and `asyncio.TimeoutError` is converted into a RuntimeError carrying
    the callId so the caller's stack trace identifies the dropped call.

    On disconnect of the target session, the WS route handler calls
    `unregister_session`, which cancels every pending Future for that
    session with a `ConnectionError` — the caller sees the error
    immediately rather than hanging until timeout.

    Args:
        tool_name: The extension-side tool to invoke (e.g. "read_page",
            "click_element"). Must match a name registered in the
            extension's tool catalog.
        args: Tool-specific arguments dict. Must be JSON-serializable;
            nested binary blobs should be base64-encoded by the caller.
        session_id: The extension session to target. Each connected
            extension instance has exactly one active session_id; a
            single user may have multiple instances.
        timeout_seconds: How long to wait for the extension to respond
            before raising. Defaults to 30s — long enough for slow page
            renders, short enough to surface real hangs.

    Returns:
        The result payload exactly as produced by the extension's
        dispatcher. Shape is `{"type": "extension.result", "callId":
        str, "ok": bool, "result"?: Any, "error"?: str,
        "errorType"?: str}` — callers should branch on `ok`.

    Raises:
        RuntimeError: Target session is not connected, OR the extension
            did not respond within `timeout_seconds`. Both cases use
            RuntimeError (not TimeoutError) so callers can catch one
            type for "the call did not complete cleanly" without losing
            the distinguishing message.
        ConnectionError: Target session disconnected mid-call (raised
            from inside the awaited Future by the registry's
            disconnect handler).
    """
    call_id = str(uuid.uuid4())

    # Order matters: register the Future BEFORE sending. If the result
    # round-trips faster than `await send_to_extension_session` returns
    # (rare but possible on a hot loopback socket), the result handler
    # must find the Future in the registry to resolve it.
    future = create_pending_future(
        call_id,
        timeout_seconds,
        session_id=session_id,
    )

    payload = {
        "type": "extension.invoke",
        "callId": call_id,
        "toolName": tool_name,
        "args": args,
    }

    sent = await send_to_extension_session(session_id, payload)
    if not sent:
        cancel_pending_future(call_id)
        raise RuntimeError(
            f"No active extension session: {session_id} (call_id={call_id})"
        )

    logger.debug(
        "[extension_invoke] dispatched tool=%s call_id=%s session=%s",
        tool_name,
        call_id,
        session_id,
    )

    try:
        result = await asyncio.wait_for(future, timeout=timeout_seconds)
        logger.debug(
            "[extension_invoke] resolved call_id=%s ok=%s",
            call_id,
            result.get("ok") if isinstance(result, dict) else "?",
        )
        return result
    except asyncio.TimeoutError as exc:
        cancel_pending_future(call_id)
        raise RuntimeError(
            f"Extension call {call_id} (tool={tool_name}) timed out after "
            f"{timeout_seconds}s"
        ) from exc
    except ConnectionError:
        # Already cleaned up by the registry's disconnect handler — just
        # surface to the caller. Re-raise to preserve the original cause.
        raise
