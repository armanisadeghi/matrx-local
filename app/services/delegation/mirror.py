"""Settle a locally-answered delegated call in this desktop's own stores.

## The defect this exists to prevent (2026-09-30)

The engine answers a delegated call by POSTing its result to
``/ai/conversations/{id}/tool_results``. That POST is how the SERVER learns
the call is done. This desktop's own copies of the call only ever learned it
from the server: the Cloud Chat card from stream events, the ``chat.tool_call``
mirror row from the next periodic pull. For a delegated call the server sends
no completion event — it hard-suspended and ended the stream — so the card
kept spinning "running on this computer…" after the agent had moved on, and
the mirror row stayed ``delegated`` until a pull minutes later.

## The rule

The process that answers a call is the first to know it is finished, so it
tells ITSELF at the moment it tells the server — inside the one delivery
funnel (``DelegationEngine._deliver``), never per answer path. Executed
calls, refused (disabled) calls and user-review decisions all get it free.

Idempotent and never a downgrade: a row already ``completed``/``error`` is
left alone. Nothing is enqueued for push and ``updated_at``/``version`` are
not touched — the cloud row is authoritative, and the next pull overwrites
this local settle with it (no pending outbox entry, so pull always applies).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

#: chat.tool_call states that are already an answer. Mirrors aidream's
#: ``resolve_client_tool_results`` (status 'completed' / 'error').
TERMINAL_TOOL_CALL_STATES = frozenset({"completed", "error"})


def settled_result(payload: dict[str, Any]) -> dict[str, Any]:
    """The part of a ClientToolResult wire payload a card needs to settle."""
    is_error = bool(payload.get("is_error"))
    return {
        "is_error": is_error,
        "error_message": payload.get("error_message") if is_error else None,
        "output": payload.get("output"),
        "duration_ms": payload.get("duration_ms"),
    }


def _serialize_output(output: Any) -> str | None:
    if output is None:
        return None
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None


class ToolCallMirror(Protocol):
    async def settle(
        self, conversation_id: str, call_id: str, payload: dict[str, Any]
    ) -> bool: ...


class SqliteToolCallMirror:
    """Settles the ``chat.tool_call`` row in the local SQLite mirror."""

    def __init__(self, db: Any | None = None) -> None:
        self._db = db

    async def settle(
        self, conversation_id: str, call_id: str, payload: dict[str, Any]
    ) -> bool:
        from app.services.local_db.database import get_db

        db = self._db or get_db()
        result = settled_result(payload)
        is_error = result["is_error"]
        output = _serialize_output(result["output"])
        duration = result["duration_ms"]
        cursor = await db.execute(
            """
            UPDATE chat.tool_call
            SET status = ?, success = ?, is_error = ?, error_message = ?,
                output = ?, output_chars = ?,
                duration_ms = COALESCE(?, duration_ms),
                completed_at = COALESCE(completed_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                resolved_at = COALESCE(resolved_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                resolution_source = COALESCE(resolution_source, 'client_post')
            WHERE call_id = ? AND conversation_id = ?
              AND (status IS NULL OR status NOT IN ('completed', 'error'))
            """,
            (
                "error" if is_error else "completed",
                0 if is_error else 1,
                1 if is_error else 0,
                result["error_message"],
                output,
                len(output) if output is not None else 0,
                int(duration) if isinstance(duration, (int, float)) else None,
                call_id,
                conversation_id,
            ),
        )
        await db.commit()
        return bool(getattr(cursor, "rowcount", 0))


class MemoryToolCallMirror:
    """Test double: rows keyed by (conversation_id, call_id)."""

    def __init__(self, rows: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else {}

    async def settle(
        self, conversation_id: str, call_id: str, payload: dict[str, Any]
    ) -> bool:
        row = self.rows.get((conversation_id, call_id))
        if row is None or row.get("status") in TERMINAL_TOOL_CALL_STATES:
            return False
        result = settled_result(payload)
        row.update(
            status="error" if result["is_error"] else "completed",
            is_error=result["is_error"],
            error_message=result["error_message"],
            output=_serialize_output(result["output"]),
        )
        return True
