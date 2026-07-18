from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.delegation.outbox import MemoryDelegationOutbox, SqliteDelegationOutbox
from app.services.local_db import database as database_module
from app.services.local_db.database import LocalDatabase


def test_sqlite_outbox_survives_reopen(tmp_path: Path) -> None:
    async def run() -> None:
        db = LocalDatabase(path=tmp_path / "matrx.db")
        database_module._instance = db
        await db.connect()
        outbox = SqliteDelegationOutbox()
        call = {
            "call_id": "call-1",
            "conversation_id": "conversation-1",
            "user_request_id": "request-1",
            "tool_name": "local_file",
        }
        result = {
            "call_id": "call-1",
            "tool_name": "local_file",
            "output": {"output": "saved"},
            "is_error": False,
            "error_message": None,
            "duration_ms": 1,
        }
        assert await outbox.enqueue(call) is True
        assert await outbox.mark_executing("call-1") is True
        assert await outbox.mark_executing("call-1") is False
        await outbox.store_result("call-1", result)
        await db.close()

        reopened = LocalDatabase(path=tmp_path / "matrx.db")
        database_module._instance = reopened
        await reopened.connect()
        restored = await SqliteDelegationOutbox().list_entries()
        assert len(restored) == 1
        assert restored[0]["state"] == "result_pending"
        assert restored[0]["result"] == result
        await SqliteDelegationOutbox().delete("call-1")
        assert await SqliteDelegationOutbox().list_entries() == []
        await reopened.close()
        database_module._instance = None

    try:
        asyncio.run(run())
    finally:
        database_module._instance = None


def test_execution_claim_is_compare_and_swap() -> None:
    async def run() -> None:
        outbox = MemoryDelegationOutbox()
        call = {
            "call_id": "call-1",
            "conversation_id": "conversation-1",
            "user_request_id": "request-1",
            "tool_name": "local_file",
        }
        assert await outbox.enqueue(call) is True
        first, second = await asyncio.gather(
            outbox.mark_executing("call-1"),
            outbox.mark_executing("call-1"),
        )
        assert sorted((first, second)) == [False, True]

    asyncio.run(run())
