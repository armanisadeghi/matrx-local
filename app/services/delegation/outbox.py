"""Crash-safe local outbox for delegated desktop tool execution."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol
from uuid import uuid4

from app.services.local_db.database import get_db


class DelegationOutbox(Protocol):
    @property
    def owner_id(self) -> str: ...

    async def enqueue(self, call: dict[str, Any]) -> bool: ...

    async def mark_executing(self, call_id: str) -> bool: ...

    async def heartbeat_execution(self, call_id: str) -> bool: ...

    async def claim_abandoned_execution(self, call_id: str) -> bool: ...

    async def store_result(self, call_id: str, payload: dict[str, Any]) -> bool: ...

    async def list_entries(self) -> list[dict[str, Any]]: ...

    async def delete(self, call_id: str) -> None: ...

    async def delete_if_queued(self, call_id: str) -> bool: ...


class SqliteDelegationOutbox:
    _LEASE_SECONDS = 120

    def __init__(self, owner_id: str | None = None) -> None:
        self._owner_id = owner_id or str(uuid4())

    @property
    def owner_id(self) -> str:
        return self._owner_id

    async def enqueue(self, call: dict[str, Any]) -> bool:
        db = get_db()
        await db.execute(
            """
            INSERT OR IGNORE INTO delegation_outbox
                (call_id, conversation_id, user_request_id, tool_name, state)
            VALUES (?, ?, ?, ?, 'queued')
            """,
            (
                str(call["call_id"]),
                str(call["conversation_id"]),
                str(call.get("user_request_id") or ""),
                str(call["tool_name"]),
            ),
        )
        await db.commit()
        row = await db.fetchone(
            "SELECT state FROM delegation_outbox WHERE call_id = ?",
            (str(call["call_id"]),),
        )
        return bool(row and row["state"] == "queued")

    async def mark_executing(self, call_id: str) -> bool:
        db = get_db()
        cursor = await db.execute(
            """
            UPDATE delegation_outbox
            SET state = 'executing', owner_id = ?,
                lease_expires_at = datetime('now', '+120 seconds'),
                updated_at = datetime('now')
            WHERE call_id = ? AND state = 'queued'
            """,
            (self._owner_id, call_id),
        )
        await db.commit()
        return cursor.rowcount == 1

    async def heartbeat_execution(self, call_id: str) -> bool:
        db = get_db()
        cursor = await db.execute(
            """
            UPDATE delegation_outbox
            SET lease_expires_at = datetime('now', '+120 seconds'),
                updated_at = datetime('now')
            WHERE call_id = ? AND state = 'executing' AND owner_id = ?
            """,
            (call_id, self._owner_id),
        )
        await db.commit()
        return cursor.rowcount == 1

    async def claim_abandoned_execution(self, call_id: str) -> bool:
        db = get_db()
        cursor = await db.execute(
            """
            UPDATE delegation_outbox
            SET owner_id = ?, lease_expires_at = datetime('now', '+120 seconds'),
                updated_at = datetime('now')
            WHERE call_id = ? AND state = 'executing'
              AND (lease_expires_at IS NULL OR lease_expires_at <= datetime('now'))
            """,
            (self._owner_id, call_id),
        )
        await db.commit()
        return cursor.rowcount == 1

    async def store_result(self, call_id: str, payload: dict[str, Any]) -> bool:
        db = get_db()
        cursor = await db.execute(
            """
            UPDATE delegation_outbox
            SET state = 'result_pending', result_payload = ?,
                lease_expires_at = NULL,
                updated_at = datetime('now')
            WHERE call_id = ? AND state = 'executing' AND owner_id = ?
            """,
            (json.dumps(payload, default=str), call_id, self._owner_id),
        )
        await db.commit()
        return cursor.rowcount == 1

    async def list_entries(self) -> list[dict[str, Any]]:
        rows = await get_db().fetchall(
            """
            SELECT call_id, conversation_id, user_request_id, tool_name,
                   state, owner_id, lease_expires_at, result_payload
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

    async def delete_if_queued(self, call_id: str) -> bool:
        db = get_db()
        cursor = await db.execute(
            "DELETE FROM delegation_outbox WHERE call_id = ? AND state = 'queued'",
            (call_id,),
        )
        await db.commit()
        return cursor.rowcount == 1


class MemoryDelegationOutbox:
    """Test double with the same persistence semantics."""

    def __init__(
        self,
        owner_id: str | None = None,
        entries: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._owner_id = owner_id or str(uuid4())
        self.entries = entries if entries is not None else {}

    @property
    def owner_id(self) -> str:
        return self._owner_id

    async def enqueue(self, call: dict[str, Any]) -> bool:
        call_id = str(call["call_id"])
        self.entries.setdefault(
            call_id,
            {
                "call_id": call_id,
                "conversation_id": str(call["conversation_id"]),
                "user_request_id": str(call.get("user_request_id") or ""),
                "tool_name": str(call["tool_name"]),
                "state": "queued",
                "owner_id": None,
                "lease_expires_at": None,
                "result": None,
            },
        )
        return self.entries[call_id]["state"] == "queued"

    async def mark_executing(self, call_id: str) -> bool:
        entry = self.entries.get(call_id)
        if entry is None or entry["state"] != "queued":
            return False
        entry["state"] = "executing"
        entry["owner_id"] = self._owner_id
        entry["lease_expires_at"] = time.monotonic() + 120.0
        return True

    async def heartbeat_execution(self, call_id: str) -> bool:
        entry = self.entries.get(call_id)
        if (
            entry is None
            or entry["state"] != "executing"
            or entry["owner_id"] != self._owner_id
        ):
            return False
        entry["lease_expires_at"] = time.monotonic() + 120.0
        return True

    async def claim_abandoned_execution(self, call_id: str) -> bool:
        entry = self.entries.get(call_id)
        if entry is None or entry["state"] != "executing":
            return False
        deadline = entry.get("lease_expires_at")
        if isinstance(deadline, (int, float)) and deadline > time.monotonic():
            return False
        entry["owner_id"] = self._owner_id
        entry["lease_expires_at"] = time.monotonic() + 120.0
        return True

    async def store_result(self, call_id: str, payload: dict[str, Any]) -> bool:
        entry = self.entries.get(call_id)
        if (
            entry is None
            or entry["state"] != "executing"
            or entry["owner_id"] != self._owner_id
        ):
            return False
        entry["state"] = "result_pending"
        entry["result"] = payload
        entry["lease_expires_at"] = None
        return True

    async def list_entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.entries.values()]

    async def delete(self, call_id: str) -> None:
        self.entries.pop(call_id, None)

    async def delete_if_queued(self, call_id: str) -> bool:
        entry = self.entries.get(call_id)
        if entry is None or entry["state"] != "queued":
            return False
        self.entries.pop(call_id, None)
        return True
