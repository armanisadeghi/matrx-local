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
        assert await outbox.store_result("call-1", result)
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


def test_live_execution_lease_cannot_be_stolen() -> None:
    async def run() -> None:
        shared: dict[str, dict] = {}
        winner = MemoryDelegationOutbox(owner_id="winner", entries=shared)
        contender = MemoryDelegationOutbox(owner_id="contender", entries=shared)
        call = {
            "call_id": "call-1",
            "conversation_id": "conversation-1",
            "user_request_id": "request-1",
            "tool_name": "local_file",
        }
        assert await winner.enqueue(call)
        assert await winner.mark_executing("call-1")
        assert await contender.claim_abandoned_execution("call-1") is False
        assert shared["call-1"]["owner_id"] == "winner"

        shared["call-1"]["lease_expires_at"] = 0.0
        assert await contender.claim_abandoned_execution("call-1") is True
        assert shared["call-1"]["owner_id"] == "contender"

    asyncio.run(run())


def test_stale_owner_cannot_persist_result_after_takeover() -> None:
    async def run() -> None:
        shared: dict[str, dict] = {}
        stale = MemoryDelegationOutbox(owner_id="stale", entries=shared)
        winner = MemoryDelegationOutbox(owner_id="winner", entries=shared)
        call = {
            "call_id": "call-1",
            "conversation_id": "conversation-1",
            "user_request_id": "request-1",
            "tool_name": "local_file",
        }
        assert await stale.enqueue(call)
        assert await stale.mark_executing("call-1")
        shared["call-1"]["lease_expires_at"] = 0.0
        assert await winner.claim_abandoned_execution("call-1")

        stale_result = {"call_id": "call-1", "output": "late"}
        assert await stale.store_result("call-1", stale_result) is False
        assert shared["call-1"]["state"] == "executing"
        assert shared["call-1"]["result"] is None

    asyncio.run(run())
