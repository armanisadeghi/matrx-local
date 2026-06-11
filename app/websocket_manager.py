"""WebSocket connection manager with per-connection tool sessions."""

from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket

from app.tools.dispatcher import dispatch
from app.tools.session import ToolSession
from app.common.system_logger import get_logger

logger = get_logger()


class Connection:
    __slots__ = (
        "websocket", "session", "_running_tasks", "via_tunnel", "_user_cancelled"
    )

    def __init__(
        self, websocket: WebSocket, session: ToolSession, via_tunnel: bool = False
    ) -> None:
        self.websocket = websocket
        self.session = session
        self.via_tunnel = via_tunnel
        self._running_tasks: dict[str, asyncio.Task] = {}
        # Ids cancelled via the explicit "cancel" action — those already got a
        # success ack, so the task's CancelledError handler must not send a
        # second response for the same id.
        self._user_cancelled: set[str] = set()

    def cancel_all(self) -> int:
        count = 0
        for req_id, task in self._running_tasks.items():
            if not task.done():
                self._user_cancelled.add(req_id)
                task.cancel()
                count += 1
        self._running_tasks.clear()
        return count


class WebSocketManager:
    def __init__(self) -> None:
        self.connections: dict[int, Connection] = {}

    async def connect(self, websocket: WebSocket, via_tunnel: bool = False) -> Connection:
        await websocket.accept()
        session = ToolSession()
        conn = Connection(websocket, session, via_tunnel=via_tunnel)
        self.connections[id(websocket)] = conn
        logger.info(
            "WebSocket connected: %s (session cwd: %s, tunnel=%s)",
            id(websocket),
            session.cwd,
            via_tunnel,
        )
        return conn

    async def disconnect(self, websocket: WebSocket) -> None:
        conn = self.connections.pop(id(websocket), None)
        if conn:
            conn.cancel_all()
            await conn.session.cleanup()
            logger.info("WebSocket disconnected: %s", id(websocket))

    async def handle_tool_message(self, conn: Connection, raw: str) -> None:
        """Parse an incoming message and dispatch concurrently.

        Messages:
          Tool call:  {"id": "...", "tool": "Name", "input": {...}}
          Cancel:     {"id": "...", "action": "cancel"}
          Cancel all: {"action": "cancel_all"}
          Ping:       {"action": "ping"}
        """
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send(conn, {"type": "error", "output": "Invalid JSON"})
            return

        action = msg.get("action")
        request_id = msg.get("id")

        # ── Control messages ───────────────────────────────────────────────
        if action == "ping":
            await self._send(conn, {"type": "success", "output": "pong", "id": request_id})
            return

        if action == "cancel_all":
            count = conn.cancel_all()
            await self._send(conn, {
                "type": "success",
                "output": f"Cancelled {count} running task(s)",
                "id": request_id,
            })
            return

        if action == "cancel" and request_id:
            task = conn._running_tasks.pop(request_id, None)
            if task and not task.done():
                conn._user_cancelled.add(request_id)
                task.cancel()
                await self._send(conn, {
                    "type": "success",
                    "output": f"Cancelled task {request_id}",
                    "id": request_id,
                })
            else:
                await self._send(conn, {
                    "type": "error",
                    "output": f"No running task with id {request_id}",
                    "id": request_id,
                })
            return

        # ── Tool call — dispatch concurrently ──────────────────────────────
        tool_name = msg.get("tool")
        tool_input = msg.get("input", {})

        if not tool_name:
            await self._send(conn, {
                "id": request_id,
                "type": "error",
                "output": "Missing 'tool' field in message",
            })
            return

        req_id = request_id or f"auto-{id(msg)}"

        # High-frequency monitoring tools — always DEBUG to avoid terminal flooding.
        # These fire every 10s from the Dashboard/Ports pages and produce repetitive output.
        _QUIET_TOOLS = frozenset({"ListPorts", "SystemResources", "SystemInfo", "ListProcesses"})
        is_quiet = tool_name in _QUIET_TOOLS

        # At INFO level: log tool name + id only (compact, scannable).
        # Full JSON input is logged at DEBUG so it's available when needed without flooding INFO.
        if is_quiet:
            logger.debug("→ WS tool=%s  id=%s", tool_name, req_id)
        else:
            import json as _json
            input_str = _json.dumps(tool_input, indent=2, ensure_ascii=False) if tool_input else "{}"
            logger.info("→ WS tool=%s  id=%s", tool_name, req_id)
            logger.debug("   input: %s", input_str)

        if req_id in conn._running_tasks and not conn._running_tasks[req_id].done():
            await self._send(conn, {
                "id": req_id,
                "type": "error",
                "output": f"A request with id {req_id} is already running",
            })
            return

        task = asyncio.create_task(self._run_tool(conn, req_id, tool_name, tool_input))
        conn._running_tasks[req_id] = task

        def _cleanup(_t: asyncio.Task, _rid: str = req_id, _task=task) -> None:
            # Pop only OUR entry — if a newer task reused the id, leave it.
            if conn._running_tasks.get(_rid) is _task:
                conn._running_tasks.pop(_rid, None)

        task.add_done_callback(_cleanup)

    async def _run_tool(
        self, conn: Connection, request_id: str, tool_name: str, tool_input: dict
    ) -> None:
        import time as _time
        _QUIET_TOOLS = frozenset({"ListPorts", "SystemResources", "SystemInfo", "ListProcesses"})
        is_quiet = tool_name in _QUIET_TOOLS
        t0 = _time.monotonic()
        try:
            result = await dispatch(tool_name, tool_input, conn.session)
            duration_ms = (_time.monotonic() - t0) * 1000

            response: dict = {
                "id": request_id,
                "type": result.type.value,
                "output": result.output,
            }
            if result.image:
                response["image"] = result.image.model_dump()
            if result.metadata:
                response["metadata"] = result.metadata

            # Log result summary at INFO; full preview only at DEBUG.
            if is_quiet:
                logger.debug("← WS tool=%s  type=%s  (%.0fms)", tool_name, result.type.value, duration_ms)
            else:
                out = result.output
                if isinstance(out, str) and len(out) > 300:
                    out_preview = out[:300] + f"… (+{len(result.output) - 300} chars)"
                else:
                    out_preview = out
                logger.info("← WS tool=%s  type=%s  (%.0fms)", tool_name, result.type.value, duration_ms)
                logger.debug("   output: %s", out_preview)

            await self._send(conn, response)

        except asyncio.CancelledError:
            duration_ms = (_time.monotonic() - t0) * 1000
            logger.warning("← WS tool=%s  CANCELLED  (%.0fms)", tool_name, duration_ms)
            if request_id in conn._user_cancelled:
                # The "cancel" action already acked this id — a second response
                # for the same id would confuse the client's pending-request map.
                conn._user_cancelled.discard(request_id)
            else:
                await self._send(conn, {
                    "id": request_id,
                    "type": "error",
                    "output": f"Task {request_id} was cancelled",
                })

        except Exception as e:
            duration_ms = (_time.monotonic() - t0) * 1000
            logger.error("← WS tool=%s  ERROR  (%.0fms): %s: %s", tool_name, duration_ms, type(e).__name__, e, exc_info=True)
            await self._send(conn, {
                "id": request_id,
                "type": "error",
                "output": f"Internal error: {type(e).__name__}: {e}",
            })

    async def _send(self, conn: Connection, data: dict) -> None:
        try:
            await conn.websocket.send_json(data)
        except Exception as exc:
            msg_id = data.get("id", "?")
            msg_type = data.get("type", "?")
            logger.warning(
                "WS send failed (id=%s type=%s): %s: %s — response lost, client will not receive this result",
                msg_id, msg_type, type(exc).__name__, exc,
            )

    async def broadcast(self, message: str) -> None:
        # Snapshot: connects/disconnects during the awaited sends mutate the
        # dict and would raise "dictionary changed size during iteration".
        for conn in list(self.connections.values()):
            await self._send(conn, {"type": "broadcast", "output": message})

    async def broadcast_notification(
        self,
        title: str,
        message: str,
        level: str = "info",
    ) -> None:
        """Push a notification event to every connected UI client."""
        import time as _time
        payload = {
            "type": "notification",
            "title": title,
            "message": message,
            "level": level,
            "timestamp": int(_time.time() * 1000),
        }
        for conn in list(self.connections.values()):
            await self._send(conn, payload)

    @property
    def active_count(self) -> int:
        return len(self.connections)
