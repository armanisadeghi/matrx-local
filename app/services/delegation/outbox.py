"""Crash-safe local outbox for delegated desktop tool execution."""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.services.local_db.database import get_db


class DelegationOutbox(Protocol):
    async def mark_executing(self, call: dict[str, Any]) -> None: ...

    async def store_result(self, call_id: str, payload: dict[str, Any]) -> None: ...

    async def list_entries(self) -> list[dict[str, Any]]: ...

    async def delete(self, call_id: str) -> None: ...


class SqliteDelegationOutbox:
    async def mark_executing(self, call: dict[str, Any]) -> None:
        db = get_db()
        await db.execute(
            """
            INSERT OR IGNORE INTO delegation_outbox
                (call_id, conversation_id, user_request_id, tool_name, state)
            VALUES (?, ?, ?, ?, 'executing')
            """,
            (
                str(call["call_id"]),
                str(call["conversation_id"]),
                str(call.get("user_request_id") or ""),
                str(call["tool_name"]),
            ),
        )
        await db.commit()

    async def store_result(self, call_id: str, payload: dict[str, Any]) -> None:
        db = get_db()
        await db.execute(
            """
            UPDATE delegation_outbox
            SET state = 'result_pending', result_payload = ?,
                updated_at = datetime('now')
            WHERE call_id = ?
            """,
            (json.dumps(payload, default=str), call_id),
        )
        await db.commit()

    async def list_entries(self) -> list[dict[str, Any]]:
        rows = await get_db().fetchall(
            """
            SELECT call_id, conversation_id, user_request_id, tool_name,
                   state, result_payload
            FROM delegation_outbox
            ORDER BY created_at ASC
            """
        )
        entries: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw = item.pop("result_payload", None)
            if isinstance(raw, str) and raw:
                try:
                    parsed = json.loads(raw)
                    item["result"] = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    item["result"] = None
            else:
                item["result"] = None
            entries.append(item)
        return entries

    async def delete(self, call_id: str) -> None:
        db = get_db()
        await db.execute("DELETE FROM delegation_outbox WHERE call_id = ?", (call_id,))
        await db.commit()


class MemoryDelegationOutbox:
    """Test double with the same persistence semantics."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}

    async def mark_executing(self, call: dict[str, Any]) -> None:
        call_id = str(call["call_id"])
        self.entries.setdefault(
            call_id,
            {
                "call_id": call_id,
                "conversation_id": str(call["conversation_id"]),
                "user_request_id": str(call.get("user_request_id") or ""),
                "tool_name": str(call["tool_name"]),
                "state": "executing",
                "result": None,
            },
        )

    async def store_result(self, call_id: str, payload: dict[str, Any]) -> None:
        entry = self.entries[call_id]
        entry["state"] = "result_pending"
        entry["result"] = payload

    async def list_entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.entries.values()]

    async def delete(self, call_id: str) -> None:
        self.entries.pop(call_id, None)
